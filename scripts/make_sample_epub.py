"""Build a small synthetic EPUB used only for layout and EPUBCheck verification."""

import hashlib
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.database import Database
from core.epub_builder import EpubBuilder, EpubValidator


def main():
    workspace = ROOT / "build" / "sample"
    image_dir = workspace / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "sample.db"
    if db_path.exists():
        db_path.unlink()
    db = Database(db_path)
    db.save_categories([
        {"id": 901, "name": "排版示例", "slug": "layout", "topic_count": 2},
        {"id": 902, "name": "长文示例", "slug": "longform", "topic_count": 1},
    ])
    illustration = image_dir / "illustration.png"
    canvas = Image.new("RGB", (1200, 500), "#1b2c23")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((60, 60, 1140, 440), outline="#c4ab78", width=5)
    for index in range(5):
        draw.line((130, 145 + index * 55, 960 - index * 70, 145 + index * 55), fill="#8da092", width=8)
    canvas.save(illustration)
    raw = illustration.read_bytes()
    image_url = "https://forum.example.com/uploads/synthetic-sample.png"
    db.execute("INSERT INTO images VALUES(?,?,?,?,?,?,?,?)",
               (image_url, str(illustration), "complete", hashlib.sha256(raw).hexdigest(),
                len(raw), "image/png", None, 1))

    samples = [
        (9101, 901, "中文标点、引用与表格", "这是合成排版样本，不对应真实站点文章。正文使用全角标点，"
                                           "包括“引号”、顿号、破折号与省略号……用于检查阅读器中的段落节奏。"),
        (9102, 901, "代码块、列表与图片", "下图是程序生成的示意图，用来验证图片本地化、去重与内部资源路径。"),
        (9103, 902, "超长文章分章检查", "这一节故意重复较长的段落，用于验证单个 XHTML 不会无限增大。"),
    ]
    for topic_id, category_id, title, lead in samples:
        db.upsert_topic({"id": topic_id, "category_id": category_id, "title": title,
                         "slug": "sample", "created_at": "2026-11-16T00:00:00Z",
                         "updated_at": "2026-11-16T01:00:00Z", "reply_count": 1})
        markup = (f"<p>{lead}</p><blockquote><p>引用段落用于检查左边线与缩进。</p></blockquote>"
                  "<ul><li>列表第一项</li><li>列表第二项</li></ul>"
                  "<pre><code>print('EPUB 3')</code></pre>"
                  "<table><thead><tr><th>字段</th><th>值</th></tr></thead>"
                  "<tbody><tr><td>语言</td><td>zh-CN</td></tr></tbody></table>")
        if topic_id == 9102:
            markup += f'<p><img src="{image_url}" alt="本地化测试插图"/></p>'
        if topic_id == 9103:
            markup += "<p>" + ("长段落排版检验。" * 18000
                              + "合成文本到此结束。</p>")
        db.save_posts(topic_id, [
            {"id": topic_id * 10, "username": "合成样本作者", "created_at": "2026-11-16T08:00:00Z",
             "cooked": markup, "post_number": 1},
            {"id": topic_id * 10 + 1, "username": "回复示例", "created_at": "2026-11-16T09:00:00Z",
             "cooked": "<p>这是回复区域的样式示例。</p>", "post_number": 2},
        ], "2026-11-16T01:00:00Z", "all", [image_url] if topic_id == 9102 else [])
    output = ROOT / "output"
    builder = EpubBuilder(
        db, output, EpubValidator(ROOT / "tools" / "epubcheck" / "epubcheck.jar"),
        log=print, base_url="https://forum.example.com")
    path = builder.build(
        "通用文集排版测试样本", [901, 902],
        source_note="本文件是完全由程序生成的排版测试样本，不对应任何真实站点文章，"
                    "也不包含真实采集内容。仅用于验证 EPUB 3 结构和中文排版。")
    print(f"\nSAMPLE={path}")
    stable = output / "通用文集排版测试样本.epub"
    stable.write_bytes(path.read_bytes())
    source_report = Path(str(path) + ".epubcheck.txt")
    stable_report = Path(str(stable) + ".epubcheck.txt")
    stable_report.write_bytes(source_report.read_bytes())
    path.unlink()
    source_report.unlink()
    print(f"STABLE={stable}")
    print(f"REPORT={stable_report}")


if __name__ == "__main__":
    main()
