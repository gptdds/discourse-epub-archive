import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


def app_home():
    override = os.environ.get("EPUB_ARCHIVE_HOME")
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DiscourseEpubArchive"
    return Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    max_topics: int = 100
    all_replies: bool = True
    download_images: bool = True
    image_max_dimension: int = 2400
    image_quality: int = 90
    compress_images: bool = False
    image_max_mb: int = 20
    interval: float = 4.0
    jitter: float = 1.0
    retries: int = 3
    timeout: float = 30.0
    skip_unchanged: bool = True
    recent_days: int = 0
    base_url: str = ""
    download_dir: str = ""
    output_dir: str = ""
    permission_confirmed: bool = False
    permission_note: str = ""
    epubcheck_jar: str = ""

    def validate(self):
        if not 3 <= self.interval <= 300:
            raise ValueError("请求间隔必须在 3～300 秒之间")
        if not 0 <= self.jitter <= min(1, self.interval - 3):
            raise ValueError("抖动必须在 0～1 秒之间，且最短间隔不得低于 3 秒")
        if not 0 <= self.retries <= 5:
            raise ValueError("重试次数必须在 0～5 之间")
        if not 0 <= self.max_topics <= 100000:
            raise ValueError("每分类主题上限必须在 0～100000 之间（0 为不限制）")
        if not 256 <= self.image_max_dimension <= 10000 or not 1 <= self.image_quality <= 100:
            raise ValueError("图片尺寸或质量不合法")
        if not 1 <= self.image_max_mb <= 100 or not 5 <= self.timeout <= 120:
            raise ValueError("图片大小或超时设置不合法")
        if self.recent_days < 0:
            raise ValueError("最近更新天数不能为负")
        base_url = self.base_url.strip().rstrip("/")
        if base_url:
            parsed = urlsplit(base_url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.port not in (None, 443) or parsed.path not in ("", "/")
                    or parsed.query or parsed.fragment):
                raise ValueError("站点地址必须是标准 HTTPS 根地址，例如 https://forum.example.com")
        if self.permission_confirmed:
            if not self.permission_note.strip():
                raise ValueError("请填写授权说明后再开启联网")
            if not base_url:
                raise ValueError("请填写要归档的 Discourse 站点地址")

    @classmethod
    def load(cls, home=None):
        home = Path(home or app_home())
        path = home / "data" / "settings.json"
        values = json.loads(path.read_text("utf-8")) if path.exists() else {}
        settings = cls(**{key: value for key, value in values.items() if key in cls.__dataclass_fields__})
        settings.download_dir = settings.download_dir or str(home / "data" / "images")
        settings.output_dir = settings.output_dir or str(home / "output")
        settings.base_url = settings.base_url.strip().rstrip("/")
        settings.validate()
        return settings

    @classmethod
    def from_dict(cls, values):
        instance = cls(**{key: value for key, value in values.items() if key in cls.__dataclass_fields__})
        home = app_home()
        instance.download_dir = instance.download_dir or str(home / "data" / "images")
        instance.output_dir = instance.output_dir or str(home / "output")
        return instance

    def save(self, home=None):
        self.validate()
        path = Path(home or app_home()) / "data" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(path)
