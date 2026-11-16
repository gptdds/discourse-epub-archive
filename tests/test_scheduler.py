from datetime import datetime, timedelta, timezone
import threading
import time

import pytest
import requests

from core.scheduler import AccessDenied, RateLimited, RequestError, Scheduler
from tests.conftest import FakeClock, FakeResponse, FakeTransport


def scheduler(db, settings, clock, transport):
    return Scheduler(db, settings, transport=transport, clock=clock, sleeper=clock.sleep,
                     random_uniform=lambda low, high: 0)


def test_permission_is_enforced_before_network(db, settings):
    settings.permission_confirmed = False
    transport = FakeTransport([FakeResponse(200, b"{}")])
    with pytest.raises(AccessDenied):
        scheduler(db, settings, FakeClock(), transport).request("https://forum.example.com/categories.json")
    assert transport.calls == []


def test_success_keeps_conservative_interval_and_records_metrics(db, settings):
    clock, transport = FakeClock(), FakeTransport([FakeResponse(200, b'{"ok":true}'),
                                                    FakeResponse(200, b'{"ok":true}')])
    service = scheduler(db, settings, clock, transport)
    response = service.request("https://forum.example.com/categories.json")
    assert response.json() == {"ok": True}
    assert transport.calls[0][1]["stream"] is True
    assert service.metrics()["count_429"] == 0
    assert service.metrics()["recent_requests"] == 1
    original = service.metrics()["interval"]
    clock.now += 100
    service.request("https://forum.example.com/categories.json")
    assert service.metrics()["interval"] <= original


def test_429_obeys_retry_after_and_cooldown_survives_restart(db, settings):
    settings.retries = 0
    clock = FakeClock()
    transport = FakeTransport([FakeResponse(429, b"", {"Retry-After": "120"})])
    service = scheduler(db, settings, clock, transport)
    with pytest.raises(RateLimited):
        service.request("https://forum.example.com/t/1.json")
    metrics = service.metrics()
    assert metrics["count_429"] == 1
    assert 119 <= metrics["cooldown_remaining"] <= 121
    assert metrics["interval"] >= 4.5
    restarted = scheduler(db, settings, clock, FakeTransport([FakeResponse(200, b"{}")]))
    assert restarted.metrics()["cooldown_remaining"] >= 119
    assert restarted.request("https://forum.example.com/t/1.json").status == 200
    assert clock.now >= 1_800_000_000.0 + 119


def test_429_http_date_retry_after(db, settings):
    settings.retries = 0
    clock = FakeClock()
    stamp = datetime.fromtimestamp(clock.now + 90, timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    service = scheduler(db, settings, clock, FakeTransport([FakeResponse(429, b"", {"Retry-After": stamp})]))
    with pytest.raises(RateLimited):
        service.request("https://forum.example.com/categories.json")
    assert 85 <= service.metrics()["cooldown_remaining"] <= 90


def test_403_stops_all_modules_and_requires_manual_unlock(db, settings):
    clock = FakeClock()
    service = scheduler(db, settings, clock, FakeTransport([FakeResponse(403, b"forbidden")]))
    with pytest.raises(AccessDenied):
        service.request("https://forum.example.com/t/40.json")
    assert service.metrics()["forbidden"] is True
    assert service.metrics()["paused"] is True
    with pytest.raises(AccessDenied):
        service.resume()
    replacement = FakeTransport([FakeResponse(200, b"{}")])
    restarted = scheduler(db, settings, clock, replacement)
    with pytest.raises(AccessDenied):
        restarted.request("https://forum.example.com/categories.json")
    assert replacement.calls == []
    restarted.resume(reviewed_403=True)
    restarted.request("https://forum.example.com/categories.json")
    assert len(replacement.calls) == 1


def test_503_backoff_and_bounded_5xx_retry(db, settings):
    settings.retries = 2
    clock = FakeClock()
    service = scheduler(db, settings, clock, FakeTransport([
        FakeResponse(503), FakeResponse(502), FakeResponse(200, b"{}")]))
    assert service.request("https://forum.example.com/t/2.json").status == 200
    metrics = service.metrics()
    assert metrics["interval"] >= 4.5
    assert metrics["failures"] == 2
    assert metrics["count_429"] == 0
    limited = scheduler(db, settings, clock, FakeTransport([FakeResponse(500), FakeResponse(500), FakeResponse(500)]))
    with pytest.raises(RequestError):
        limited.request("https://forum.example.com/t/3.json")
    assert limited.metrics()["failures"] == 5


def test_timeouts_are_retried_then_fail(db, settings):
    settings.retries = 1
    clock = FakeClock()
    service = scheduler(db, settings, clock, FakeTransport([requests.Timeout("slow"), requests.Timeout("slow")]))
    with pytest.raises(RequestError):
        service.request("https://forum.example.com/t/5.json")
    assert service.metrics()["failures"] == 2
    assert service.metrics()["timeouts"] == 2
    assert service.metrics()["interval"] > 3


def test_unsafe_urls_and_redirects_are_rejected(db, settings):
    clock = FakeClock()
    service = scheduler(db, settings, clock, FakeTransport([FakeResponse(302, headers={"Location": "http://x"})]))
    with pytest.raises(RequestError):
        service.request("http://forum.example.com/categories.json")
    with pytest.raises(RequestError):
        service.request("https://forum.example.com/categories.json")


def test_response_size_limit(db, settings):
    transport = FakeTransport([FakeResponse(200, b"x" * 100)])
    service = scheduler(db, settings, FakeClock(), transport)
    with pytest.raises(RequestError):
        service.request("https://forum.example.com/categories.json", max_bytes=50)


def test_api_priority_precedes_queued_images(db, settings):
    service = Scheduler(db, settings, transport=FakeTransport([]), clock=FakeClock(),
                        sleeper=lambda seconds: None)
    service.busy = True
    order = []
    image_ready = threading.Event()
    api_ready = threading.Event()
    finished = [threading.Event(), threading.Event()]

    def acquire(priority, mark, out_index):
        if priority == 10:
            mark.set()
        service._acquire(priority, lambda: None)
        order.append(priority)
        service._release()
        finished[out_index].set()

    image_thread = threading.Thread(target=acquire, args=(10, image_ready, 0))
    api_thread = threading.Thread(target=acquire, args=(0, api_ready, 1))
    image_thread.start()
    image_ready.wait(1)
    time.sleep(0.02)
    api_thread.start()
    api_ready.wait(1)
    time.sleep(0.02)
    service.busy = False
    image_thread.join(2)
    api_thread.join(2)
    assert order == [0, 10]


def test_interval_is_measured_after_previous_request(db, settings):
    clock = FakeClock()

    class SlowTransport(FakeTransport):
        def get(self, url, **kwargs):
            response = super().get(url, **kwargs)
            clock.now += 20
            return response

    transport = SlowTransport([FakeResponse(200, b"{}"), FakeResponse(200, b"{}")])
    service = scheduler(db, settings, clock, transport)
    service.request("https://forum.example.com/categories.json")
    first_finished = clock.now
    service.request("https://forum.example.com/t/1.json")
    assert clock.now >= first_finished + 3
