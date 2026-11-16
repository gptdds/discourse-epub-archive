import json
from types import SimpleNamespace

import pytest

from core.database import utc_now
from core.download_manager import DownloadManager, TaskControl
from core.scheduler import Scheduler
from core.scheduler import RateLimited
from tests.conftest import FakeClock, FakeTransport


CATEGORY = {"id": 7, "name": "测试分类", "slug": "test", "topic_count": 3}


def make_topic(topic_id, updated="2026-11-15T10:00:00Z"):
    return {"id": topic_id, "category_id": 7, "title": f"主题 {topic_id}", "slug": "topic",
            "created_at": "2026-11-01T00:00:00Z", "updated_at": updated, "reply_count": 1,
            "source_url": f"https://forum.example.com/t/{topic_id}"}


def setup_session(db, settings, category=CATEGORY, topics=()):
    db.save_categories([category])
    for topic in topics:
        db.upsert_topic(topic)
    session = db.create_session("测试任务", [category["id"]], settings.__dict__)
    with db.connect() as connection:
        connection.executemany("INSERT INTO download_tasks(session_id,topic_id) VALUES(?,?)",
                               [(session, topic["id"]) for topic in topics])
    return session


def test_database_persists_each_topic_and_posts(db, settings):
    session = setup_session(db, settings, topics=[make_topic(1)])
    db.save_posts(1, [{"id": 11, "username": "甲", "created_at": "2026-11-01",
                       "cooked": "<p>正文</p><img src='https://forum.example.com/a.png'>", "post_number": 1}],
                  "2026-11-15T10:00:00Z", "all", ["https://forum.example.com/a.png"])
    row = db.one("SELECT * FROM topics WHERE id=1")
    assert row["collection_status"] == "complete"
    assert db.one("SELECT COUNT(*) AS n FROM posts WHERE topic_id=1")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM images")["n"] == 1
    db.execute("UPDATE download_tasks SET status='complete' WHERE session_id=?", (session,))
    assert db.task_counts(session)["remaining"] == 0


class FakeClient:
    def __init__(self, topics, pages=None):
        self.topic_rows = list(topics)
        self.pages = pages
        self.calls = []

    def category_page(self, category, page):
        self.calls.append(("page", category["id"], page))
        if self.pages is not None:
            rows = self.pages.get(page, [])
            return rows, page + 1 in self.pages
        return self.topic_rows if page == 0 else [], False

    def topic_posts(self, topic_id, all_replies=True):
        self.calls.append(("topic", topic_id, all_replies))
        return {}, [{"id": topic_id * 10, "username": "作者", "created_at": "2026-11-01T00:00:00Z",
                     "cooked": "<p>内容</p>", "post_number": 1}]


def manager(db, settings, session, client):
    manager = DownloadManager(db, Scheduler(db, settings, transport=FakeTransport([])), session,
                              TaskControl())
    manager.client = client
    return manager


def test_category_pagination_and_topic_discovery(db, settings):
    session = setup_session(db, settings)
    client = FakeClient([], pages={0: [make_topic(1), make_topic(2)], 1: [make_topic(3)]})
    manager(db, settings, session, client).discover()
    assert [call[2] for call in client.calls] == [0, 1]
    assert db.one("SELECT COUNT(*) AS n FROM download_tasks WHERE session_id=?", (session,))["n"] == 3


def test_incremental_collection_skips_unchanged_and_updates_changed(db, settings):
    session = setup_session(db, settings, topics=[make_topic(1)])
    client = FakeClient([])
    first = manager(db, settings, session, client)
    first.collect_topics()
    assert ("topic", 1, True) in client.calls
    db.execute("UPDATE topics SET updated_at='2026-11-16T10:00:00Z' WHERE id=1")
    db.execute("UPDATE download_tasks SET status='pending' WHERE session_id=?", (session,))
    client.calls.clear()
    manager(db, settings, session, client).collect_topics()
    assert ("topic", 1, True) in client.calls
    db.execute("UPDATE download_tasks SET status='pending' WHERE session_id=?", (session,))
    client.calls.clear()
    manager(db, settings, session, client).collect_topics()
    assert client.calls == []
    assert db.one("SELECT status FROM download_tasks WHERE session_id=?", (session,))["status"] == "skipped"


def test_failed_topic_can_be_retried(db, settings):
    session = setup_session(db, settings, topics=[make_topic(2)])
    client = FakeClient([])

    def fail(*args, **kwargs):
        raise RuntimeError("接口结构变化")

    client.topic_posts = fail
    manager(db, settings, session, client).collect_topics()
    assert db.one("SELECT status FROM download_tasks WHERE session_id=?", (session,))["status"] == "failed"
    fixed = FakeClient([])
    manager(db, settings, session, fixed).run(retry_failed=True)
    assert db.one("SELECT status FROM download_tasks WHERE session_id=?", (session,))["status"] == "complete"


def test_pause_and_resume_control():
    control = TaskControl()
    control.pause()
    assert control.pause_event.is_set()
    control.resume()
    assert not control.pause_event.is_set()
    control.cancel()
    with pytest.raises(Exception):
        control.checkpoint()


def test_429_pauses_session_without_losing_pending_topic(db, settings):
    session = setup_session(db, settings, topics=[make_topic(31)])
    client = FakeClient([])

    def limited(*args, **kwargs):
        raise RateLimited("HTTP 429; retry after 60")

    client.topic_posts = limited
    manager(db, settings, session, client).run()
    assert db.one("SELECT status FROM collection_sessions WHERE id=?", (session,))["status"] == "paused"
    assert db.one("SELECT status FROM download_tasks WHERE session_id=?", (session,))["status"] == "pending"
