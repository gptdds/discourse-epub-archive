import hashlib
import io
import warnings
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup
from PIL import Image, ImageOps

from core.scheduler import AccessDenied, Cancelled, RateLimited, RequestError


Image.MAX_IMAGE_PIXELS = 40_000_000


def image_url(tag, base):
    source = tag.get("data-orig-src") or tag.get("data-src")
    srcset = tag.get("srcset") or tag.get("data-srcset")
    if not source and srcset:
        choices = []
        for candidate in srcset.split(","):
            parts = candidate.strip().split()
            if parts:
                try:
                    weight = float(parts[-1].rstrip("wx")) if len(parts) > 1 else 1
                except ValueError:
                    weight = 1
                choices.append((weight, parts[0]))
        if choices:
            source = max(choices)[1]
    source = source or tag.get("src", "")
    if not source or source.startswith(("data:", "blob:", "upload:")):
        return None
    url = urldefrag(urljoin(base, source))[0]
    return url if urlsplit(url).scheme == "https" else None


def extract_images(html, base):
    soup = BeautifulSoup(html, "html.parser")
    return set(filter(None, (image_url(tag, base) for tag in soup.find_all("img"))))


class ImageService:
    def __init__(self, database, client, settings):
        self.database = database
        self.client = client
        self.settings = settings
        self.directory = Path(settings.download_dir)
        self.directory.mkdir(parents=True, exist_ok=True)

    def download(self, url):
        row = self.database.one("SELECT * FROM images WHERE original_url=?", (url,))
        if row and row["download_status"] == "complete" and Path(row["local_path"]).is_file():
            return Path(row["local_path"])
        self.database.execute("INSERT OR IGNORE INTO images(original_url) VALUES(?)", (url,))
        self.database.execute("UPDATE images SET download_status='running',attempts=attempts+1 WHERE original_url=?",
                              (url,))
        try:
            response = self.client.fetch_url(url, priority=10, max_bytes=self.settings.image_max_mb * 1024 * 1024)
            mime = response.headers.get("Content-Type", response.headers.get("content-type", "")).split(";")[0]
            if not mime.startswith("image/"):
                raise RequestError(f"图片 MIME 类型错误：{mime}")
            raw = response.content
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
                    fmt = image.format
                    animated = getattr(image, "is_animated", False)
                    if animated and fmt not in ("GIF", "PNG"):
                        raise RequestError("动画格式不受 EPUB 普遍支持，保留来源链接，未静默丢弃动画")
                    if animated and max(image.size) > self.settings.image_max_dimension:
                        raise RequestError("动画尺寸超过上限，未强行缩放或丢弃动画")
                    if fmt not in ("JPEG", "PNG", "GIF") or (
                            not animated and (self.settings.compress_images or
                                              max(image.size) > self.settings.image_max_dimension)):
                        transformed = ImageOps.exif_transpose(image)
                        transformed.thumbnail((self.settings.image_max_dimension, self.settings.image_max_dimension))
                        fmt = "PNG" if "A" in transformed.getbands() else "JPEG"
                        buffer = io.BytesIO()
                        if fmt == "JPEG":
                            transformed.convert("RGB").save(buffer, fmt, quality=self.settings.image_quality,
                                                             optimize=True)
                        else:
                            transformed.save(buffer, fmt)
                        raw = buffer.getvalue()
            extension, mime = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"),
                               "GIF": ("gif", "image/gif")}[fmt]
            filename = hashlib.sha256(url.encode()).hexdigest() + "." + extension
            destination = self.directory / filename
            temporary = destination.with_suffix(destination.suffix + ".part")
            temporary.write_bytes(raw)
            temporary.replace(destination)
            self.database.execute(
                "UPDATE images SET local_path=?,download_status='complete',file_hash=?,file_size=?,mime=?,"
                "last_error=NULL WHERE original_url=?",
                (str(destination), hashlib.sha256(raw).hexdigest(), len(raw), mime, url))
            return destination
        except (AccessDenied, Cancelled, RateLimited):
            self.database.execute("UPDATE images SET download_status='pending' WHERE original_url=?", (url,))
            raise
        except Exception as error:
            self.database.execute("UPDATE images SET download_status='failed',last_error=? WHERE original_url=?",
                                  (str(error), url))
            return None
