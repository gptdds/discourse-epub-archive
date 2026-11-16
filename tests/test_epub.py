import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from lxml import etree
from PIL import Image

from core.epub_builder import EpubBuilder, EpubValidator


class AcceptingValidator:
    def __init__(self):
        self.paths = []

    def validate(self, path):
        self.paths.append(path)
        return "No errors."


def metadata():
    return {"permission_confirmed": True, "permission_note": "test authorization",
            "download_dir": "", "output_dir": "", "interval": 3, "jitter": 0, "retries": 1}


def seed(db, tmp_path, long=False, missing_image=False):
    db.save_categories([
        {"id": 1, "name": "甲分类", "slug": "one", "topic_count": 2},
        {"id": 2, "name": "乙分类", "slug": "two", "topic_count": 1}])
    entries = [(10, 1, "甲文章"), (11, 1, "甲篇长文" if long else "甲文章二"), (20, 2, "乙文章")]
    image_path = tmp_path / "local.png"
    Image.new("RGB", (40, 20), "#884422").save(image_path)
    raw = image_path.read_bytes()
    db.execute("INSERT INTO images VALUES(?,?,?,?,?,?,?,?)",
               ("https://forum.example.com/uploads/pic.png", str(image_path), "complete",
                hashlib.sha256(raw).hexdigest(), len(raw), "image/png", None, 1))
    for topic_id, category_id, title in entries:
        db.upsert_topic({"id": topic_id, "category_id": category_id, "title": title, "slug": "t",
                         "created_at": f"2026-11-0{topic_id % 9 + 1}T00:00:00Z",
                         "updated_at": "2026-11-15T00:00:00Z", "reply_count": 1,
                         "source_url": f"https://forum.example.com/t/{topic_id}"})
        markup = ('<div class="post"><p>第一段，中文标点：测试。</p>'
                  '<blockquote><p>引用内容</p></blockquote>'
                  '<pre><code>print("代码")</code></pre>'
                  '<ul><li>列表一</li><li>列表二</li></ul>'
                  '<table><tr><th>列</th></tr><tr><td>值</td></tr></table>'
                  '<img src="https://forum.example.com/uploads/pic.png" alt="插图"/>'
                  '<script>alert(1)</script><iframe src="https://ads.example"></iframe></div>')
        if long and topic_id == 11:
            markup += "<p>" + ("超长段落。" * 50000) + "</p>"
        if missing_image and topic_id == 10:
            markup += '<p><img src="https://forum.example.com/uploads/missing.png"></p>'
        db.save_posts(topic_id, [
            {"id": topic_id * 10, "username": "作者甲", "created_at": "2026-11-01T08:00:00Z",
             "cooked": markup, "post_number": 1},
            {"id": topic_id * 10 + 1, "username": "回复者", "created_at": "2026-11-02T08:00:00Z",
             "cooked": "<p>这是一条回复。</p>", "post_number": 2}],
            "2026-11-15T00:00:00Z", "all", ["https://forum.example.com/uploads/pic.png"])


def test_epub_merges_categories_and_contains_valid_structure(db, tmp_path):
    seed(db, tmp_path)
    builder = EpubBuilder(db, tmp_path / "output", AcceptingValidator())
    path = builder.build("测试文集", [1, 2])
    assert path.is_file() and path.suffix == ".epub"
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert names[0] == "mimetype"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert "META-INF/container.xml" in names and "EPUB/package.opf" in names
        nav = archive.read("EPUB/nav.xhtml").decode()
        assert "甲分类" in nav and "乙分类" in nav
        assert "甲文章" in nav and "乙文章" in nav
        assert nav.count("topic_") == 3
        for name in names:
            if name.endswith((".xhtml", ".opf", ".xml")):
                etree.fromstring(archive.read(name))
        chapters = "\n".join(archive.read(name).decode() for name in names
                             if name.startswith("EPUB/chapters/topic_"))
        assert "第一段，中文标点" in chapters
        assert "这是一条回复" in chapters
        assert "<script" not in chapters and "<iframe" not in chapters
        assert "../images/article_images/" in chapters
        assert 'src="https://forum.example.com' not in chapters
        assert "https://forum.example.com/t/10" in chapters
        images = [name for name in names if name.startswith("EPUB/images/article_images/")]
        assert len(images) == 1
    assert db.one("SELECT COUNT(*) AS n FROM books")["n"] == 1


def test_long_article_is_split_into_multiple_chapters(db, tmp_path):
    seed(db, tmp_path, long=True)
    path = EpubBuilder(db, tmp_path / "out", AcceptingValidator()).build("长文集", [1])
    with zipfile.ZipFile(path) as archive:
        parts = sorted(name for name in archive.namelist()
                       if name.startswith("EPUB/chapters/topic_11_"))
        assert len(parts) > 1
        for name in parts:
            assert len(archive.read(name)) < 400_000


def test_missing_image_becomes_visible_source_link(db, tmp_path):
    seed(db, tmp_path, missing_image=True)
    path = EpubBuilder(db, tmp_path / "out", AcceptingValidator()).build("缺图文集", [1])
    with zipfile.ZipFile(path) as archive:
        chapters = "\n".join(archive.read(name).decode() for name in archive.namelist()
                             if name.startswith("EPUB/chapters/topic_"))
        assert "图片未离线保存" in chapters
        assert "https://forum.example.com/uploads/missing.png" in chapters


def test_duplicate_image_bytes_are_packaged_once(db, tmp_path):
    seed(db, tmp_path)
    path = EpubBuilder(db, tmp_path / "out", AcceptingValidator()).build("去重文集", [1, 2])
    with zipfile.ZipFile(path) as archive:
        images = [name for name in archive.namelist() if "/article_images/" in name]
        assert len(images) == 1


@pytest.mark.skipif(not (Path("tools/epubcheck/epubcheck.jar").exists()),
                    reason="EPUBCheck JAR 尚未下载")
def test_epub_passes_real_epubcheck(db, tmp_path):
    seed(db, tmp_path)
    builder = EpubBuilder(db, tmp_path / "out", EpubValidator("tools/epubcheck/epubcheck.jar"))
    path = builder.build("EPUBCheck 验证", [1, 2])
    report = Path(str(path) + ".epubcheck.txt").read_text("utf-8")
    assert "No errors" in report or "0 errors" in report
