import heapq
import json
import random
import threading
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests


class RequestError(RuntimeError):
    pass


class AccessDenied(RequestError):
    pass


class RateLimited(RequestError):
    pass


class Cancelled(RuntimeError):
    pass


@dataclass
class ResponseData:
    status: int
    headers: dict
    content: bytes
    url: str

    def json(self):
        try:
            return json.loads(self.content)
        except (ValueError, UnicodeError) as error:
            raise RequestError(f"接口没有返回合法 JSON：{self.url}") from error


class Scheduler:
    def __init__(self, database, settings, transport=None, clock=None, sleeper=None, random_uniform=None):
        self.database = database
        self.settings = settings
        self.clock = clock or time.time
        self.sleeper = sleeper or time.sleep
        self.random_uniform = random_uniform or random.uniform
        self.condition = threading.Condition()
        self.pending = []
        self.sequence = 0
        self.busy = False
        self.transport = transport or requests.Session()
        if transport is None:
            self.transport.trust_env = False
            self.transport.headers.update({"User-Agent": "DiscourseEpubArchive/1.0",
                                           "Accept": "application/json,image/*;q=0.9,*/*;q=0.5"})
        self.state = database.get_meta("scheduler", {})
        self.state.setdefault("cooldown_until", 0.0)
        self.state.setdefault("next_request_at", 0.0)
        self.state.setdefault("interval", settings.interval)
        self.state.setdefault("paused", False)
        self.state.setdefault("forbidden", False)
        self.state.setdefault("success_streak", 0)
        self.state.setdefault("timeouts", 0)
        self.state.setdefault("count_429", 0)
        self.state.setdefault("count_403", 0)
        self.state.setdefault("failures", 0)
        self.state["interval"] = max(settings.interval, self.state["interval"])
        self._save()

    def _save(self):
        self.database.set_meta("scheduler", self.state)

    def pause(self):
        with self.condition:
            self.state["paused"] = True
            self._save()
            self.condition.notify_all()

    def resume(self, reviewed_403=False):
        with self.condition:
            if self.state["forbidden"] and not reviewed_403:
                raise AccessDenied("HTTP 403 已锁定联网；人工检查并明确确认后才能恢复")
            self.state["forbidden"] = False
            self.state["paused"] = False
            self._save()
            self.condition.notify_all()

    def configure(self, settings):
        with self.condition:
            self.settings = settings
            self.state["interval"] = max(self.state["interval"], settings.interval)
            self._save()

    def _wait(self, checkpoint):
        checkpoint()
        self.sleeper(0.1)

    def _acquire(self, priority, checkpoint):
        with self.condition:
            self.sequence += 1
            ticket = (priority, self.sequence)
            heapq.heappush(self.pending, ticket)
        try:
            while True:
                checkpoint()
                with self.condition:
                    if self.state["forbidden"]:
                        raise AccessDenied("HTTP 403 全局锁定：请人工检查访问权限")
                    if not self.state["paused"] and not self.busy and self.pending[0] == ticket:
                        self.busy = True
                        heapq.heappop(self.pending)
                        return
                self._wait(checkpoint)
        except BaseException:
            with self.condition:
                if ticket in self.pending:
                    self.pending.remove(ticket)
                    heapq.heapify(self.pending)
            raise

    def _release(self):
        with self.condition:
            self.busy = False
            self.condition.notify_all()

    def _wait_ready(self, checkpoint):
        while True:
            checkpoint()
            with self.condition:
                if self.state["forbidden"]:
                    raise AccessDenied("HTTP 403 全局锁定")
                remaining = max(self.state["cooldown_until"], self.state["next_request_at"]) - self.clock()
                if not self.state["paused"] and remaining <= 0:
                    return
            self._wait(checkpoint)

    def _retry_after(self, headers, fallback):
        value = headers.get("Retry-After", headers.get("retry-after", ""))
        try:
            return max(0.0, float(value))
        except (ValueError, TypeError):
            try:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                return max(0.0, stamp.timestamp() - self.clock())
            except (ValueError, TypeError, OverflowError):
                return fallback

    def _record(self, status, failed):
        now = self.clock()
        self.database.execute("INSERT INTO request_events(at,status,failed) VALUES(?,?,?)",
                              (now, status, int(failed)))
        self.database.execute("DELETE FROM request_events WHERE at<?", (now - 86400,))
        if failed:
            self.state["failures"] += 1
            self.state["success_streak"] = 0
        if status == 429:
            self.state["count_429"] += 1
        if status == 403:
            self.state["count_403"] += 1

    def _cool(self, seconds, adaptive=False):
        self.state["cooldown_until"] = max(self.state["cooldown_until"], self.clock() + seconds)
        if adaptive:
            self.state["interval"] = min(300.0, max(self.settings.interval, self.state["interval"] * 1.5))
        self._save()

    def request(self, url, params=None, priority=0, max_bytes=32 * 1024 * 1024, checkpoint=None):
        if not self.settings.permission_confirmed:
            raise AccessDenied("尚未确认站方自动采集许可；请先在下载设置中记录许可")
        if not self.settings.base_url.strip():
            raise AccessDenied("尚未配置目标站点；不会发送网络请求")
        checkpoint = checkpoint or (lambda: None)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RequestError("仅允许无凭据的 HTTPS 地址")
        last_error = None
        last_status = None
        for attempt in range(self.settings.retries + 1):
            self._acquire(priority, checkpoint)
            try:
                self._wait_ready(checkpoint)
                now = self.clock()
                delay = max(3.0, self.state["interval"] + self.random_uniform(-self.settings.jitter,
                                                                            self.settings.jitter))
                response = None
                try:
                    response = self.transport.get(url, params=params, timeout=(10, self.settings.timeout),
                                                  stream=True, allow_redirects=False)
                    status = response.status_code
                    last_status = status
                    headers = dict(response.headers)
                    self._record(status, status >= 300)
                    if status == 403:
                        self.state["forbidden"] = True
                        self.state["paused"] = True
                        self._save()
                        raise AccessDenied("HTTP 403：任务已停止；请人工检查站方许可和访问限制")
                    if status == 429 or status >= 500:
                        fallback = max(30, 5 * 2 ** attempt) if status == 429 else 5 * 2 ** attempt
                        delay = max(fallback, self._retry_after(headers, fallback))
                        self._cool(delay, adaptive=status in (429, 503))
                        last_error = RequestError(f"HTTP {status}；全局冷却 {delay:.0f} 秒，尝试 {attempt + 1}")
                        continue
                    if status >= 300:
                        raise RequestError(f"HTTP {status}：{url}（不自动跟随重定向或绕过限制）")
                    content_length = headers.get("Content-Length")
                    if content_length:
                        try:
                            declared_size = int(content_length)
                        except (TypeError, ValueError):
                            raise RequestError("接口返回了非法 Content-Length")
                        if declared_size > max_bytes:
                            raise RequestError(f"响应超过大小限制 {max_bytes} 字节")
                    content = bytearray()
                    for chunk in response.iter_content(64 * 1024):
                        checkpoint()
                        if chunk:
                            content.extend(chunk)
                            if len(content) > max_bytes:
                                raise RequestError(f"响应超过大小限制 {max_bytes} 字节")
                    self.state["timeouts"] = 0
                    self.state["success_streak"] += 1
                    self._save()
                    return ResponseData(status, headers, bytes(content), url)
                except (requests.Timeout, requests.ConnectionError) as error:
                    self._record(None, True)
                    self.state["timeouts"] += 1
                    self._cool(5 * 2 ** attempt, adaptive=self.state["timeouts"] >= 2)
                    last_error = RequestError(f"网络超时或连接失败：{error}")
                finally:
                    if response is not None:
                        response.close()
                    self.state["next_request_at"] = self.clock() + delay
                    self._save()
            finally:
                self._release()
        if last_status == 429:
            raise RateLimited(str(last_error or "HTTP 429"))
        raise last_error or RequestError("请求重试次数已耗尽")

    def metrics(self):
        with self.condition:
            state = dict(self.state)
            queued = len(self.pending)
        recent = self.database.one("SELECT COUNT(*) AS count FROM request_events WHERE at>=?",
                                   (self.clock() - 300,))["count"]
        state.update(recent_requests=recent, requests_per_minute=recent / 5,
                     cooldown_remaining=max(0, state["cooldown_until"] - self.clock()), queued=queued)
        return state
