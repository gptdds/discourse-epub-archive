import json
from types import SimpleNamespace

import pytest

from core.discourse_client import DiscourseClient, RobotRules, SchemaError
from core.scheduler import AccessDenied, ResponseData


BASE_URL = "https://forum.example.com"


class RoutingScheduler:
    def __init__(self, routes, robots=""):
        self.routes = routes
        self.robots = robots
        self.calls = []
        self.settings = SimpleNamespace(base_url=BASE_URL)

    def request(self, url, params=None, priority=0, max_bytes=0, checkpoint=None):
        self.calls.append((url, params, priority))
        if url.endswith("/robots.txt"):
            return ResponseData(200, {"Content-Type": "text/plain"}, self.robots.encode(), url)
        if url not in self.routes:
            raise AssertionError(f"unexpected url {url}")
        data = self.routes[url]
        payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        return ResponseData(200, {"Content-Type": "application/json"}, payload, url)


def test_category_tree_validation_and_restricted_filtering():
    routes = {BASE_URL + "/categories.json": {"category_list": {"categories": [
        {"id": 1, "name": "甲", "slug": "a", "topic_count": 3,
         "subcategory_list": [{"id": 2, "name": "乙", "slug": "b", "topic_count": 1}]},
        {"id": 3, "name": "隐藏", "slug": "hidden", "topic_count": 0, "read_restricted": True}]}}}
    client = DiscourseClient(RoutingScheduler(routes))
    items = client.categories()
    assert [item["id"] for item in items] == [1, 2]
    assert client.robots[BASE_URL].allowed("/categories.json")


def test_category_pagination_parameters_and_topic_validation():
    url = BASE_URL + "/c/a/1.json"
    rows = [{"id": 9, "title": "主题", "category_id": 1, "posts_count": 3,
             "last_posted_at": "2026-11-15T00:00:00Z", "slug": "t"}]
    scheduler = RoutingScheduler({url: {"topic_list": {"topics": rows, "more_topics_url": "next"}}})
    topics, more = DiscourseClient(scheduler).category_page({"id": 1, "slug": "a"}, 2)
    assert topics[0]["reply_count"] == 2
    assert more is True
    assert scheduler.calls[-1][1]["page"] == 2
    assert scheduler.calls[-1][1]["order"] == "latest"


def test_malformed_categories_raise_schema_error():
    client = DiscourseClient(RoutingScheduler(
        {BASE_URL + "/categories.json": {"category_list": {"categories": "not-a-list"}}}))
    with pytest.raises(SchemaError, match="category_list.categories"):
        client.categories()


def test_empty_category_slug_uses_numeric_fallback():
    url = BASE_URL + "/c/86-category/86.json"
    scheduler = RoutingScheduler({url: {"topic_list": {"topics": [], "more_topics_url": None}}})
    assert DiscourseClient(scheduler).category_page({"id": 86, "slug": ""}, 0) == ([], False)
    assert scheduler.calls[-1][0] == url


def test_subcategory_path_includes_parent_fallbacks():
    url = BASE_URL + "/c/14-category/7-category/7.json"
    scheduler = RoutingScheduler({url: {"topic_list": {"topics": [], "more_topics_url": None}}})
    categories = {
        14: {"id": 14, "slug": "", "parent_id": None},
        7: {"id": 7, "slug": "", "parent_id": 14},
    }
    assert DiscourseClient(scheduler).category_page(categories[7], 0, categories) == ([], False)
    assert scheduler.calls[-1][0] == url


def test_subcategory_path_uses_parent_slug_when_available():
    url = BASE_URL + "/c/parent/7-category/7.json"
    scheduler = RoutingScheduler({url: {"topic_list": {"topics": [], "more_topics_url": None}}})
    categories = {
        14: {"id": 14, "slug": "parent", "parent_id": None},
        7: {"id": 7, "slug": "", "parent_id": 14},
    }
    assert DiscourseClient(scheduler).category_page(categories[7], 0, categories) == ([], False)
    assert scheduler.calls[-1][0] == url


def test_topic_stream_fetches_missing_posts_in_batches():
    topic = {
        "post_stream": {
            "stream": [101, 102, 103],
            "posts": [{"id": 101, "username": "楼主", "created_at": "2026-11-01",
                       "cooked": "<p>一</p>", "post_number": 1}]}}
    batch = {"post_stream": {"posts": [
        {"id": 102, "username": "甲", "created_at": "2026-11-02", "cooked": "<p>二</p>", "post_number": 2},
        {"id": 103, "username": "乙", "created_at": "2026-11-03", "cooked": "<p>三</p>", "post_number": 3}]}}
    scheduler = RoutingScheduler({BASE_URL + "/t/1.json": topic, BASE_URL + "/t/1/posts.json": batch})
    data, posts = DiscourseClient(scheduler).topic_posts(1, all_replies=True)
    assert [post["post_number"] for post in posts] == [1, 2, 3]
    assert scheduler.calls[-1][1] == [("post_ids[]", 102), ("post_ids[]", 103)]


def test_first_post_only_avoids_extra_requests():
    topic = {"post_stream": {"stream": [1, 2], "posts": [
        {"id": 1, "username": "楼主", "created_at": "2026-11-01", "cooked": "<p>一</p>", "post_number": 1}]}}
    scheduler = RoutingScheduler({BASE_URL + "/t/1.json": topic})
    _, posts = DiscourseClient(scheduler).topic_posts(1, all_replies=False)
    assert len(posts) == 1
    assert not any(url.endswith("/posts.json") for url, _, _ in scheduler.calls)


def test_incomplete_topic_stream_is_an_error():
    topic = {"post_stream": {"stream": [1, 2], "posts": [
        {"id": 1, "username": "楼主", "created_at": "2026-11-01", "cooked": "<p>一</p>", "post_number": 1}]}}
    scheduler = RoutingScheduler({BASE_URL + "/t/1.json": topic, BASE_URL + "/t/1/posts.json":
                                  {"post_stream": {"posts": []}}})
    with pytest.raises(SchemaError, match="帖子流不完整"):
        DiscourseClient(scheduler).topic_posts(1, all_replies=True)


def test_robots_disallow_blocks_path():
    scheduler = RoutingScheduler({}, robots="User-agent: *\nDisallow: /categories.json\n")
    client = DiscourseClient(scheduler)
    with pytest.raises(AccessDenied):
        client.get_json("/categories.json")
    assert all(not url.endswith("/categories.json") for url, _, _ in scheduler.calls)


def test_robot_rules_prefer_specific_agent():
    rules = RobotRules("User-agent: *\nDisallow: /private\n"
                       "User-agent: DiscourseEpubArchive\nDisallow: /c/\nAllow: /c/public\n")
    assert rules.allowed("/private/x")
    assert not rules.allowed("/c/1.json")
    assert rules.allowed("/c/public/1.json")
