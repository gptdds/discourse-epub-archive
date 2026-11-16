import io
from pathlib import Path

import pytest
from PIL import Image

from config.settings import Settings
from core.database import Database


class FakeResponse:
    def __init__(self, status=200, content=b"", headers=None, error=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
        self.error = error
        self.closed = False

    def iter_content(self, size):
        for offset in range(0, len(self.content), size):
            yield self.content[offset:offset + size]

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            return FakeResponse(500)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeClock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0, seconds)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "archive.db")


@pytest.fixture
def settings(tmp_path):
    return Settings(permission_confirmed=True, permission_note="test authorization",
                    interval=3.0, jitter=0.0, retries=1, timeout=5,
                    base_url="https://forum.example.com",
                    download_dir=str(tmp_path / "images"), output_dir=str(tmp_path / "output"))


@pytest.fixture
def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 32), "#335544").save(buffer, "PNG")
    return buffer.getvalue()
