import ipaddress
import re
import socket
from urllib.parse import quote, urljoin, urlsplit

from core.scheduler import AccessDenied, RequestError


DEFAULT_AGENT = "DiscourseEpubArchive"


def normalize_base_url(value):
    value = (value or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise RequestError("站点地址必须是标准 HTTPS 根地址")
    return f"https://{parsed.netloc}"


class SchemaError(RequestError):
    pass


def require(value, expected, location):
    if not isinstance(value, expected):
        raise SchemaError(f"接口结构变化：{location} 应为 {expected.__name__}")
    return value


class RobotRules:
    def __init__(self, text, agent=DEFAULT_AGENT):
        groups = []
        agents, rules = [], []
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if rules:
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
            elif key in ("allow", "disallow", "crawl-delay") and agents:
                rules.append((key, value))
        groups.append((agents, rules))
        wanted = agent.lower()
        specific = [rules for agents, rules in groups if any(
            item != "*" and item in wanted for item in agents)]
        self.rules = [rule for rules in (specific or [rules for agents, rules in groups if "*" in agents])
                      for rule in rules]

    def allowed(self, path):
        matches = []
        for kind, pattern in self.rules:
            if kind not in ("allow", "disallow") or not pattern:
                continue
            anchored = pattern.endswith("$")
            raw = pattern[:-1] if anchored else pattern
            regex = "^" + re.escape(raw).replace(r"\*", ".*") + ("$" if anchored else "")
            if re.search(regex, path):
                matches.append((len(raw.replace("*", "")), kind == "allow"))
        return max(matches)[1] if matches else True

    def crawl_delay(self):
        values = []
        for kind, value in self.rules:
            if kind == "crawl-delay":
                try:
                    values.append(float(value))
                except ValueError:
                    pass
        return max(values, default=0)


class DiscourseClient:
    def __init__(self, scheduler, checkpoint=None, base_url=None):
        self.scheduler = scheduler
        self.checkpoint = checkpoint or (lambda: None)
        if base_url is None:
            base_url = getattr(getattr(scheduler, "settings", None), "base_url", "")
        self.base_url = normalize_base_url(base_url)
        self.base_host = urlsplit(self.base_url).hostname
        self.agent = DEFAULT_AGENT
        self.robots = {}

    def check_url(self, url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None, 443):
            raise RequestError("只支持标准 HTTPS 公开资源")
        if parsed.hostname == self.base_host:
            return
        try:
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise RequestError("拒绝访问非公开网络的图片地址")
        except socket.gaierror as error:
            raise RequestError(f"图片域名无法解析：{parsed.hostname}") from error

    def fetch_url(self, url, priority=0, max_bytes=32 * 1024 * 1024, params=None):
        self.check_url(url)
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            try:
                response = self.scheduler.request(origin + "/robots.txt", priority=priority,
                                                  max_bytes=1024 * 1024, checkpoint=self.checkpoint)
                self.robots[origin] = RobotRules(response.content.decode("utf-8", "replace"), self.agent)
                delay = self.robots[origin].crawl_delay()
                if delay:
                    with self.scheduler.condition:
                        self.scheduler.state["interval"] = max(delay, self.scheduler.state["interval"])
                        self.scheduler._save()
            except RequestError as error:
                if "HTTP 404" in str(error):
                    self.robots[origin] = RobotRules("")
                else:
                    raise
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        if not self.robots[origin].allowed(path):
            raise AccessDenied(f"robots.txt 不允许采集此路径：{url}")
        return self.scheduler.request(url, params=params, priority=priority, max_bytes=max_bytes,
                                      checkpoint=self.checkpoint)

    def get_json(self, path, params=None):
        if not path.startswith("/") or path.startswith("//"):
            raise RequestError("API 路径必须位于原站")
        response = self.fetch_url(self.base_url + path, params=params)
        return require(response.json(), dict, path)

    def categories(self):
        data = self.get_json("/categories.json", {"include_subcategories": "true"})
        listing = require(data.get("category_list"), dict, "category_list")
        items = require(listing.get("categories"), list, "category_list.categories")
        categories = {}

        def collect(entries):
            for item in entries:
                require(item, dict, "category")
                for key, kind in (("id", int), ("name", str), ("slug", str)):
                    require(item.get(key), kind, f"category.{key}")
                if item.get("read_restricted"):
                    continue
                categories[item["id"]] = item
                collect(item.get("subcategory_list", []))

        collect(items)
        return list(categories.values())

    def category_page(self, category, page):
        path = f"/c/{quote(category['slug'], safe='')}/{category['id']}.json"
        data = self.get_json(path, {"page": page, "order": "latest", "ascending": "false",
                                    "no_subcategories": "true"})
        listing = require(data.get("topic_list"), dict, "topic_list")
        topics = require(listing.get("topics"), list, "topic_list.topics")
        result = []
        for topic in topics:
            require(topic, dict, "topic")
            for key, kind in (("id", int), ("title", str), ("category_id", int)):
                require(topic.get(key), kind, f"topic.{key}")
            updated = topic.get("last_posted_at") or topic.get("bumped_at") or topic.get("created_at")
            require(updated, str, "topic.last_posted_at")
            result.append({**topic, "updated_at": updated,
                           "reply_count": max(0, topic.get("posts_count", 1) - 1),
                           "source_url": f"{self.base_url}/t/{topic['id']}"})
        return result, bool(listing.get("more_topics_url"))

    def topic_posts(self, topic_id, all_replies=True):
        data = self.get_json(f"/t/{topic_id}.json")
        stream = require(data.get("post_stream"), dict, "post_stream")
        ids = require(stream.get("stream"), list, "post_stream.stream")
        if any(not isinstance(post_id, int) for post_id in ids):
            raise SchemaError("post_stream.stream 含非法帖子 ID")
        if not ids:
            raise SchemaError("主题没有可见帖子")
        wanted = set(ids if all_replies else ids[:1])
        posts = {}

        def add(items):
            for post in require(items, list, "post_stream.posts"):
                for key, kind in (("id", int), ("username", str), ("created_at", str),
                                  ("cooked", str), ("post_number", int)):
                    require(post.get(key), kind, f"post.{key}")
                if post["id"] in wanted:
                    posts[post["id"]] = post

        add(stream.get("posts"))
        missing = [post_id for post_id in ids if post_id in wanted and post_id not in posts]
        for offset in range(0, len(missing), 20):
            self.checkpoint()
            chunk = missing[offset:offset + 20]
            response = self.get_json(f"/t/{topic_id}/posts.json",
                                     [("post_ids[]", post_id) for post_id in chunk])
            add(require(response.get("post_stream"), dict, "posts.post_stream").get("posts"))
        absent = wanted.difference(posts)
        if absent:
            raise SchemaError(f"主题 {topic_id} 帖子流不完整，缺少 {len(absent)} 篇可见帖子")
        return data, sorted(posts.values(), key=lambda post: post["post_number"])
