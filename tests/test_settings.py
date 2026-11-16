from types import SimpleNamespace

import pytest

from config.settings import Settings
from core.discourse_client import DiscourseClient, normalize_base_url
from core.scheduler import AccessDenied, RequestError, Scheduler
from tests.conftest import FakeClock, FakeResponse, FakeTransport


@pytest.mark.parametrize("url", [
    "http://forum.example.com",
    "https://forum.example.com/path",
    "https://user:pass" + "word@forum.example.com",
    "https://forum.example.com:8443",
    "https://forum.example.com?query=1",
    "not-a-url",
])
def test_site_url_rejects_non_root_or_unsafe_values(url):
    with pytest.raises(ValueError, match="标准 HTTPS 根地址"):
        Settings(base_url=url).validate()
    with pytest.raises(RequestError):
        normalize_base_url(url)


def test_site_url_is_normalized_without_trailing_slash():
    settings = Settings(base_url=" https://forum.example.com/ ")
    settings.validate()
    assert normalize_base_url(settings.base_url) == "https://forum.example.com"


def test_enabled_network_requires_site_url():
    with pytest.raises(ValueError, match="站点地址"):
        Settings(permission_confirmed=True, permission_note="authorized").validate()


def test_scheduler_rejects_network_without_site_url(db, tmp_path):
    settings = Settings(permission_confirmed=True, permission_note="authorized",
                        interval=3.0, jitter=0.0, base_url="",
                        download_dir=str(tmp_path / "images"),
                        output_dir=str(tmp_path / "output"))
    transport = FakeTransport([FakeResponse(200, b"{}")])
    clock = FakeClock()
    service = Scheduler(db, settings, transport=transport, clock=clock, sleeper=clock.sleep,
                        random_uniform=lambda low, high: 0)
    with pytest.raises(AccessDenied, match="站点"):
        service.request("https://forum.example.com/categories.json")
    assert transport.calls == []


def test_discourse_client_uses_configured_site():
    scheduler = SimpleNamespace(settings=SimpleNamespace(base_url="https://community.example.org"))
    client = DiscourseClient(scheduler)
    assert client.base_url == "https://community.example.org"
    assert client.base_host == "community.example.org"
