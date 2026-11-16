import copy
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from lxml import etree
from PIL import Image, ImageDraw, ImageFont

from core.database import utc_now
from core.image_service import image_url


XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
CSS = """
body { font-family: serif; line-height: 1.8; margin: 5%; color: #242424; overflow-wrap: anywhere; }
h1 { font-size: 1.7em; line-height: 1.4; margin: 1.6em 0 .8em; }
h2 { font-size: 1.15em; margin-top: 2em; border-top: 1px solid #bbb; padding-top: 1em; }
p { margin: .7em 0; } img { max-width: 100%; height: auto; }
blockquote { border-left: .2em solid #a6aca9; margin: 1em 0; padding: .1em 1em; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #eee; padding: .8em; }
code { font-family: monospace; } table { border-collapse: collapse; max-width: 100%; font-size: .9em; }
td, th { border: 1px solid #bbb; padding: .35em; overflow-wrap: anywhere; }
.meta, .notice { font-size: .85em; color: #5a6561; }
.cover { text-align: center; } .cover img { max-height: 95vh; }
a { color: #286452; } figcaption { font-size: .85em; }
"""


def xhtml(title, body):
    content = (f'<?xml version="1.0" encoding="UTF-8"?>'
               f'<html xmlns="{XHTML}" xmlns:epub="{EPUB}" xml:lang="zh-CN" lang="zh-CN">'
               f'<head><title>{html.escape(title)}</title><link rel="stylesheet" type="text/css" '
               f'href="../styles/main.css"/></head><body>{body}</body></html>')
    etree.fromstring(content.encode())
    return content.encode("utf-8")


def safe_name(title):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(". ")[:100] or "文集归档"


def resource_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


class EpubValidator:
    def __init__(self, jar=""):
        self.jar = Path(jar) if jar else resource_root() / "tools" / "epubcheck" / "epubcheck.jar"

    def validate(self, path):
        java = shutil.which("java")
        bundled = resource_root() / "tools" / "jre" / "bin" / "java.exe"
        if bundled.exists():
            java = str(bundled)
        if not java or not self.jar.is_file():
            raise RuntimeError("EPUBCheck 或 Java 不可用，请在设置中选择 epubcheck.jar 并安装 Java 17+")
        path = Path(path).resolve()
        java_temp = path.parent / f".epubcheck-tmp-{uuid.uuid4().hex[:8]}"
        java_temp.mkdir()
        try:
            result = subprocess.run(
                [java, f"-Djava.io.tmpdir={java_temp}", "-jar", str(self.jar.resolve()), str(path)],
                capture_output=True, text=True, cwd=path.parent, encoding="utf-8", errors="replace", timeout=900,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        finally:
            shutil.rmtree(java_temp, ignore_errors=True)
        report = result.stdout + result.stderr
        Path(str(path) + ".epubcheck.txt").write_text(report, "utf-8")
        if result.returncode:
            raise RuntimeError(f"EPUBCheck 未通过（退出码 {result.returncode}），报告：{path}.epubcheck.txt\n{report[-3000:]}")
        return report


class EpubBuilder:
    def __init__(self, database, output_dir, validator=None, log=None, checkpoint=None, base_url=""):
        self.database = database
        self.output_dir = Path(output_dir)
        self.validator = validator or EpubValidator()
        self.log = log or (lambda text: None)
        self.checkpoint = checkpoint or (lambda: None)
        self.base_url = (base_url or "").strip().rstrip("/")
        self.assets = {}
        self.manifest = []
        self.missing_images = 0

    def _write(self, archive, path, data, mime, properties=""):
        archive.writestr("EPUB/" + path, data)
        identifier = "item" + str(len(self.manifest))
        self.manifest.append((identifier, path, mime, properties))
        return identifier

    def _cover(self, title, category_count, topic_count):
        image = Image.new("RGB", (1200, 1600), "#182822")
        drawing = ImageDraw.Draw(image)
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
        try:
            title_font = ImageFont.truetype(str(font_path), 76)
            small_font = ImageFont.truetype(str(font_path), 30)
        except OSError:
            title_font = ImageFont.load_default(size=76)
            small_font = ImageFont.load_default(size=30)
        drawing.rectangle((100, 120, 1100, 132), fill="#c4b797")
        drawing.text((100, 190), "ARCHIVE / 离线文集", font=small_font, fill="#c4b797")
        for index, offset in enumerate(range(0, min(len(title), 60), 12)):
            drawing.text((100, 400 + index * 115), title[offset:offset + 12], font=title_font, fill="#f4f0e5")
        drawing.text((100, 1320), f"{category_count} 个分类 · {topic_count} 篇主题", font=small_font, fill="#c4b797")
        drawing.text((100, 1390), datetime.now().strftime("%Y.%m.%d"), font=small_font, fill="#c4b797")
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90)
        return buffer.getvalue()

    def _image(self, archive, url):
        row = self.database.one("SELECT * FROM images WHERE original_url=? AND download_status='complete'", (url,))
        if not row or not row["local_path"] or not Path(row["local_path"]).is_file():
            return None
        digest = row["file_hash"]
        if digest in self.assets:
            return "../" + self.assets[digest]
        source = Path(row["local_path"])
        actual = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                actual.update(chunk)
        if actual.hexdigest() != digest:
            self.log(f"图片缓存校验失败：{url}")
            return None
        destination = f"images/article_images/{digest}{source.suffix.lower()}"
        archive.write(source, "EPUB/" + destination)
        self.manifest.append(("item" + str(len(self.manifest)), destination, row["mime"], ""))
        self.assets[digest] = destination
        return "../" + destination

    def _sanitize(self, archive, cooked, source):
        cooked = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cooked)
        soup = BeautifulSoup(cooked, "html.parser")
        for node in list(soup.find_all(["script", "style", "iframe", "form", "input", "button",
                                       "svg", "canvas", "noscript", "nav"])):
            node.decompose()
        for node in list(soup.find_all(["video", "audio", "object", "embed"])):
            link = node.get("src") or node.get("data")
            source_node = node.find("source")
            link = link or (source_node.get("src") if source_node else None)
            parent_link = node.find_parent("a")
            replacement = soup.new_string("媒体内容（访问原文）") if parent_link else soup.new_tag(
                "a", href=urljoin(source, link or source))
            if not parent_link:
                replacement.string = "媒体内容（访问原文）"
            node.replace_with(replacement)
        for node in list(soup.find_all("img")):
            url = image_url(node, source)
            internal = self._image(archive, url) if url else None
            alt = node.get("alt", "文章图片")
            if internal:
                node.attrs = {"src": internal, "alt": alt}
            else:
                self.missing_images += 1
                parent_link = node.find_parent("a")
                replacement = soup.new_string(f"[图片未离线保存：{alt}]") if parent_link else soup.new_tag(
                    "a", href=url or source)
                if not parent_link:
                    replacement.string = f"[图片未离线保存：{alt}]"
                node.replace_with(replacement)
        allowed = set("p div span a img h1 h2 h3 h4 h5 h6 blockquote pre code ul ol li table "
                      "thead tbody tfoot tr th td caption colgroup col br hr strong b em i u s del ins "
                      "sub sup figure figcaption dl dt dd q abbr time mark small ruby rt rp details summary".split())
        for node in list(soup.find_all(True)):
            if node.name not in allowed:
                node.unwrap()
                continue
            attrs = {}
            if node.name == "a":
                href = urljoin(source, node.get("href", source))
                if urlsplit(href).scheme in ("https", "http", "mailto"):
                    attrs["href"] = href
            elif node.name == "img":
                attrs = {"src": node["src"], "alt": node.get("alt", "")}
            elif node.name in ("td", "th"):
                for name in ("colspan", "rowspan"):
                    value = str(node.get(name, ""))
                    if value.isdigit() and 0 < int(value) <= 1000:
                        attrs[name] = value
            if node.name == "ol" and str(node.get("start", "")).lstrip("-").isdigit():
                attrs["start"] = str(node["start"])
            if node.get("title"):
                attrs["title"] = node["title"]
            node.attrs = attrs
        serialized = soup.decode(formatter="minimal")
        parser = etree.HTMLParser(recover=True, encoding="utf-8")
        document = etree.fromstring(("<html><body>" + serialized + "</body></html>").encode(), parser)
        body = document.find("body")
        if body is None:
            return ""
        return html.escape(body.text or "") + "".join(
            etree.tostring(node, encoding="unicode", method="xml") for node in body)

    def _pieces(self, fragment, limit=60000):
        soup = BeautifulSoup(fragment, "html.parser")

        def split(node):
            serialized = str(node)
            if len(serialized) <= limit:
                yield serialized
            elif isinstance(node, NavigableString):
                for offset in range(0, len(str(node)), limit // 2):
                    yield html.escape(str(node)[offset:offset + limit // 2])
            elif isinstance(node, Tag):
                opening = "<" + node.name + "".join(
                    f' {name}="{html.escape(str(value), quote=True)}"' for name, value in node.attrs.items()) + ">"
                closing = f"</{node.name}>"
                for child in node.contents:
                    for part in split(child):
                        yield opening + part + closing

        buffer = ""
        for child in soup.contents:
            for piece in split(child):
                if buffer and len(buffer) + len(piece) > limit:
                    yield buffer
                    buffer = ""
                buffer += piece
        if buffer:
            yield buffer

    def build(self, title, category_ids, source_note=None):
        self.assets = {}
        self.manifest = []
        self.missing_images = 0
        category_ids = list(dict.fromkeys(category_ids))
        if not category_ids:
            raise ValueError("请至少选择一个分类")
        placeholders = ",".join("?" for _ in category_ids)
        count = self.database.one(
            f"SELECT COUNT(*) AS count FROM topics WHERE category_id IN ({placeholders}) AND collection_status='complete'",
            category_ids)["count"]
        if not count:
            raise ValueError("选中分类没有已完整采集的主题")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        identifier = str(uuid.uuid4())
        destination = self.output_dir / f"{safe_name(title)}-{identifier[:8]}.epub"
        temporary = destination.with_suffix(".building.epub")
        spine = []
        toc = []
        source_origins = []
        included_categories = 0
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
                archive.writestr("META-INF/container.xml",
                                '<?xml version="1.0"?><container version="1.0" '
                                'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                                '<rootfiles><rootfile full-path="EPUB/package.opf" '
                                'media-type="application/oebps-package+xml"/></rootfiles></container>')
                self._write(archive, "styles/main.css", CSS, "text/css")
                self._write(archive, "images/cover.jpg", self._cover(title, len(category_ids), count),
                            "image/jpeg", "cover-image")
                spine.append(self._write(archive, "chapters/cover.xhtml", xhtml(title,
                    '<div class="cover"><img src="../images/cover.jpg" alt="文集封面"/></div>'), "application/xhtml+xml"))
                for category_id in category_ids:
                    self.checkpoint()
                    category = self.database.one("SELECT * FROM categories WHERE id=?", (category_id,))
                    category_name = category["name"] if category else f"分类 {category_id}"
                    children = []
                    for topic in self.database.iter_rows(
                            "SELECT * FROM topics WHERE category_id=? AND collection_status='complete' ORDER BY created_at,id",
                            (category_id,)):
                        self.checkpoint()
                        self.log(f"编排 · {topic['title']}")
                        source = topic["source_url"] or (
                            f"{self.base_url}/t/{topic['id']}" if self.base_url else "")
                        if source:
                            parsed_source = urlsplit(source)
                            origin = f"{parsed_source.scheme}://{parsed_source.netloc}"
                            if origin not in source_origins:
                                source_origins.append(origin)
                        part_index, blocks, size = 1, [], 0
                        first_path = f"chapters/topic_{topic['id']}_1.xhtml"

                        def flush():
                            suffix = f"（续 {part_index}）" if part_index > 1 else ""
                            chapter_title = topic["title"] + suffix
                            body = f"<h1>{html.escape(chapter_title)}</h1>"
                            scope = "全部公开可见帖子" if topic["scope"] == "all" else "仅主帖"
                            source_link = (
                                f'<a href="{html.escape(source, quote=True)}">原始主题 #{topic["id"]}</a>'
                                if source else f'原始主题 #{topic["id"]}')
                            body += (f'<p class="meta">{html.escape(scope)} · {source_link}</p>'
                                     + "".join(blocks))
                            return self._write(archive, f"chapters/topic_{topic['id']}_{part_index}.xhtml",
                                               xhtml(chapter_title, body), "application/xhtml+xml")

                        for post in self.database.iter_rows("SELECT * FROM posts WHERE topic_id=? ORDER BY post_number",
                                                           (topic["id"],)):
                            self.checkpoint()
                            post_base = source or self.base_url
                            cooked = self._sanitize(
                                archive, post["cooked_html"],
                                f"{post_base}/{post['post_number']}" if post_base else "")
                            metadata = (f'<h2>{post["post_number"]} 楼 · {html.escape(post["username"])}</h2>'
                                        f'<p class="meta">{html.escape(post["created_at"])}</p>')
                            for fragment in self._pieces(cooked):
                                block = metadata + fragment
                                if blocks and size + len(block) > 90000:
                                    spine.append(flush())
                                    part_index += 1
                                    blocks, size = [], 0
                                blocks.append(block)
                                size += len(block)
                        if blocks:
                            spine.append(flush())
                            children.append((topic["title"], first_path))
                    if children:
                        included_categories += 1
                        toc.append((category_name, children))
                provenance = source_note or (
                    "本书整理自用户配置站点中的公开主题，原作者保留其作品权利。"
                    "采集、使用和再分发应取得相应授权。本工具本身不授予任何内容许可。")
                note = (f"<h1>版权与来源说明</h1><p>{html.escape(provenance)}</p>"
                        f"<p>主题来源链接、作者和原始发布时间保留在各章节中。生成时间：{html.escape(utc_now())}。</p>"
                        f"<p>共 {count} 篇主题，{included_categories} 个分类。"
                        f"未能离线保存的图片出现次数：{self.missing_images}。这些位置已替换为原始来源链接。</p>")
                notice_id = self._write(archive, "chapters/notice.xhtml", xhtml("版权与来源说明", note),
                                        "application/xhtml+xml")
                spine.insert(1, notice_id)
                nav_items = ['<li><a href="chapters/cover.xhtml">封面</a></li>',
                             '<li><a href="chapters/notice.xhtml">版权与来源说明</a></li>']
                for name, children in toc:
                    links = "".join(f'<li><a href="{path}">{html.escape(label)}</a></li>' for label, path in children)
                    nav_items.append(f'<li><span>{html.escape(name)}</span><ol>{links}</ol></li>')
                nav = xhtml("目录", f'<nav epub:type="toc" id="toc"><h1>目录</h1><ol>{"".join(nav_items)}</ol></nav>')
                nav = nav.replace(b'../styles/main.css', b'styles/main.css')
                nav_id = self._write(archive, "nav.xhtml", nav, "application/xhtml+xml", "nav")
                spine.insert(2, nav_id)
                items = "".join(f'<item id="{item_id}" href="{path}" media-type="{mime}"'
                                f'{(" properties=" + chr(34) + properties + chr(34)) if properties else ""}/>'
                                for item_id, path, mime, properties in self.manifest)
                refs = "".join(f'<itemref idref="{item_id}"/>' for item_id in spine)
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                source_value = self.base_url or (source_origins[0] if source_origins else "")
                source_meta = (f"<dc:source>{html.escape(source_value)}</dc:source>"
                               if source_value else "")
                opf = (f'<?xml version="1.0" encoding="UTF-8"?>'
                       f'<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0">'
                       f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                       f'<dc:identifier id="book-id">urn:uuid:{identifier}</dc:identifier>'
                       f'<dc:title>{html.escape(title)}</dc:title><dc:language>zh-CN</dc:language>'
                       f'<dc:creator>各主题原作者</dc:creator>{source_meta}'
                       f'<meta property="dcterms:modified">{stamp}</meta></metadata><manifest>{items}</manifest>'
                       f'<spine>{refs}</spine></package>')
                archive.writestr("EPUB/package.opf", opf.encode())
            self.checkpoint()
            self.log("正在运行 EPUBCheck…")
            report = self.validator.validate(temporary)
            temporary.replace(destination)
            validation_path = Path(str(temporary) + ".epubcheck.txt")
            if validation_path.exists():
                validation_path.replace(Path(str(destination) + ".epubcheck.txt"))
            self.database.execute(
                "INSERT INTO books(title,path,category_ids,topic_count,category_count,file_size,created_at,validation) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (title, str(destination), json.dumps(category_ids), count, included_categories,
                 destination.stat().st_size, utc_now(), report))
            self.log(f"EPUB 已生成并通过校验：{destination}")
            return destination
        except BaseException:
            if temporary.exists():
                temporary.unlink()
            raise
