import hashlib
from pathlib import Path

from PIL import Image

from core.image_service import ImageService, extract_images
from core.scheduler import RequestError


def test_extracts_all_discourse_lazy_and_srcset_forms():
    html = """
    <p><img src="/uploads/a.png"><img data-src="/uploads/b.png">
    <img data-orig-src="/uploads/c.png"><img srcset="/uploads/small.jpg 1x, /uploads/large.jpg 2x">
    <img data-srcset="/uploads/d-srcset.png 480w">
    <img src="data:image/png;base64,xxx"></p>"""
    urls = extract_images(html, "https://forum.example.com")
    assert "https://forum.example.com/uploads/a.png" in urls
    assert "https://forum.example.com/uploads/b.png" in urls
    assert "https://forum.example.com/uploads/c.png" in urls
    assert "https://forum.example.com/uploads/large.jpg" in urls
    assert "https://forum.example.com/uploads/d-srcset.png" in urls
    assert len(urls) == 5


class ImageClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def fetch_url(self, url, priority=10, max_bytes=0):
        self.calls.append((url, priority))
        return type("Response", (), {"headers": {"Content-Type": "image/png"}, "content": self.payload})()


def test_image_download_is_deduplicated_by_url_and_hash(db, settings, png_bytes):
    client = ImageClient(png_bytes)
    service = ImageService(db, client, settings)
    url = "https://forum.example.com/uploads/a.png"
    first = service.download(url)
    second = service.download(url)
    assert first == second and first.is_file()
    assert client.calls == [(url, 10)]
    row = db.one("SELECT * FROM images WHERE original_url=?", (url,))
    assert row["file_hash"] == hashlib.sha256(png_bytes).hexdigest()
    assert row["mime"] == "image/png"
    assert row["file_size"] == len(png_bytes)


def test_invalid_image_records_failure(db, settings):
    service = ImageService(db, ImageClient(b"not an image"), settings)
    assert service.download("https://forum.example.com/uploads/fake.png") is None
    row = db.one("SELECT * FROM images WHERE original_url=?", ("https://forum.example.com/uploads/fake.png",))
    assert row["download_status"] == "failed"
    assert row["last_error"]


def test_image_is_resized_when_larger_than_limit(db, settings):
    settings.image_max_dimension = 32
    import io
    buffer = io.BytesIO()
    Image.new("RGB", (300, 100), "#223344").save(buffer, "PNG")
    service = ImageService(db, ImageClient(buffer.getvalue()), settings)
    path = service.download("https://forum.example.com/uploads/wide.png")
    with Image.open(path) as image:
        assert max(image.size) <= 32
