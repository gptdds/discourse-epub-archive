import json
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from config.settings import Settings
from core.database import utc_now
from core.discourse_client import DiscourseClient, SchemaError
from core.image_service import ImageService, extract_images
from core.scheduler import AccessDenied, Cancelled, RateLimited


class TaskControl:
    def __init__(self, on_state=None):
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()
        self.on_state = on_state or (lambda status: None)

    def pause(self):
        self.pause_event.set()
        self.on_state("paused")

    def resume(self):
        self.pause_event.clear()
        self.on_state("running")

    def cancel(self):
        self.cancel_event.set()

    def checkpoint(self):
        while True:
            if self.cancel_event.is_set():
                raise Cancelled("用户停止任务，已保存进度")
            if not self.pause_event.is_set():
                return
            time.sleep(0.1)


class DownloadManager:
    def __init__(self, database, scheduler, session_id, control=None, log=None):
        self.database = database
        self.scheduler = scheduler
        self.session_id = session_id
        self.control = control or TaskControl()
        self.log = log or (lambda message: None)
        session = database.one("SELECT * FROM collection_sessions WHERE id=?", (session_id,))
        self.settings = Settings.from_dict(json.loads(session["settings"]))
        self.client = DiscourseClient(scheduler, self.control.checkpoint, self.settings.base_url)

    def _current(self, message):
        self.database.execute("UPDATE collection_sessions SET current_item=?,updated_at=? WHERE id=?",
                              (message, utc_now(), self.session_id))
        self.log(message)

    def discover(self):
        for cursor in self.database.rows("SELECT * FROM session_categories WHERE session_id=? ORDER BY category_id",
                                         (self.session_id,)):
            if cursor["done"]:
                continue
            category = self.database.one("SELECT * FROM categories WHERE id=?", (cursor["category_id"],))
            page, discovered = cursor["next_page"], cursor["discovered"]
            if not category:
                raise SchemaError(f"分类 {cursor['category_id']} 不在本地列表，请刷新分类")
            while True:
                self.control.checkpoint()
                self._current(f"扫描分类「{category['name']}」第 {page + 1} 页")
                topics, more = self.client.category_page(category, page)
                accepted = []
                new_count = 0
                old_count = 0
                for topic in topics:
                    if topic["category_id"] != category["id"]:
                        continue
                    if self.settings.recent_days:
                        try:
                            stamp = datetime.fromisoformat(topic["updated_at"].replace("Z", "+00:00"))
                            if stamp < datetime.now(timezone.utc) - timedelta(days=self.settings.recent_days):
                                old_count += 1
                                continue
                        except ValueError as error:
                            raise SchemaError("主题更新时间无法解析") from error
                    already = self.database.one("SELECT 1 FROM download_tasks WHERE session_id=? AND topic_id=?",
                                                 (self.session_id, topic["id"]))
                    if already:
                        continue
                    if self.settings.max_topics and discovered >= self.settings.max_topics:
                        break
                    self.database.upsert_topic(topic)
                    accepted.append(topic["id"])
                    discovered += 1
                    new_count += 1
                limit = self.settings.max_topics and discovered >= self.settings.max_topics
                done = (not topics or not more or bool(limit)
                        or (self.settings.recent_days and topics and old_count == len(topics)))
                if topics and more and not new_count and not self.settings.recent_days and page > 0 and not limit:
                    raise SchemaError("分类分页重复或无进展；停止，避免无限请求")
                with self.database.connect() as connection:
                    connection.executemany("INSERT OR IGNORE INTO download_tasks(session_id,topic_id) VALUES(?,?)",
                                           [(self.session_id, topic_id) for topic_id in accepted])
                    connection.execute(
                        "UPDATE session_categories SET next_page=?,discovered=?,done=? WHERE session_id=? AND category_id=?",
                        (page + 1, discovered, int(done), self.session_id, category["id"]))
                if done:
                    break
                page += 1

    def collect_topics(self):
        last_id = 0
        while True:
            rows = self.database.rows(
                "SELECT t.* FROM topics t JOIN download_tasks dt ON t.id=dt.topic_id "
                "WHERE dt.session_id=? AND dt.status='pending' AND t.id>? ORDER BY t.id LIMIT 100",
                (self.session_id, last_id))
            if not rows:
                return
            for topic in rows:
                last_id = topic["id"]
                self.control.checkpoint()
                scope = "all" if self.settings.all_replies else "first"
                unchanged = (topic["collection_status"] == "complete" and
                             topic["collected_version"] == topic["updated_at"] and
                             (topic["scope"] == "all" or topic["scope"] == scope))
                if unchanged and self.settings.skip_unchanged:
                    self.database.execute("UPDATE download_tasks SET status='skipped',last_error=NULL "
                                          "WHERE session_id=? AND topic_id=?", (self.session_id, topic["id"]))
                    continue
                self._current(f"采集主题 #{topic['id']} · {topic['title']}")
                self.database.execute("UPDATE download_tasks SET status='running' WHERE session_id=? AND topic_id=?",
                                      (self.session_id, topic["id"]))
                try:
                    data, posts = self.client.topic_posts(topic["id"], self.settings.all_replies)
                    urls = set()
                    for post in posts:
                        urls.update(extract_images(post["cooked"], self.settings.base_url))
                    self.database.save_posts(topic["id"], posts, topic["updated_at"], scope, urls)
                    self.database.execute("UPDATE download_tasks SET status='complete',last_error=NULL "
                                          "WHERE session_id=? AND topic_id=?", (self.session_id, topic["id"]))
                except (AccessDenied, Cancelled, RateLimited):
                    self.database.execute("UPDATE download_tasks SET status='pending' WHERE session_id=? AND topic_id=?",
                                          (self.session_id, topic["id"]))
                    raise
                except Exception as error:
                    self.database.execute("UPDATE download_tasks SET status='failed',last_error=? "
                                          "WHERE session_id=? AND topic_id=?", (str(error), self.session_id, topic["id"]))
                    self.database.execute("UPDATE topics SET last_error=? WHERE id=?", (str(error), topic["id"]))
                    self.log(f"主题 {topic['id']} 失败：{error}")

    def collect_images(self):
        if not self.settings.download_images:
            return
        service = ImageService(self.database, self.client, self.settings)
        last_url = ""
        while True:
            rows = self.database.rows(
                "SELECT DISTINCT im.* FROM images im JOIN topic_images ti ON ti.original_url=im.original_url "
                "JOIN download_tasks dt ON dt.topic_id=ti.topic_id WHERE dt.session_id=? "
                "AND im.download_status='pending' AND im.original_url>? ORDER BY im.original_url LIMIT 100",
                (self.session_id, last_url))
            if not rows:
                return
            for row in rows:
                last_url = row["original_url"]
                self.control.checkpoint()
                self._current(f"下载图片 · {last_url}")
                if service.download(last_url) is None:
                    self.log(f"图片下载失败，已记录：{last_url}")

    def run(self, retry_failed=False):
        started = time.monotonic()
        self.database.session_status(self.session_id, "running")
        try:
            if retry_failed:
                with self.database.connect() as connection:
                    connection.execute("UPDATE download_tasks SET status='pending' WHERE session_id=? AND status='failed'",
                                       (self.session_id,))
                    connection.execute(
                        "UPDATE images SET download_status='pending' WHERE download_status='failed' AND original_url IN "
                        "(SELECT ti.original_url FROM topic_images ti JOIN download_tasks dt ON dt.topic_id=ti.topic_id "
                        "WHERE dt.session_id=?)", (self.session_id,))
            self.discover()
            self.collect_topics()
            self.collect_images()
            counts = self.database.task_counts(self.session_id)
            failed = counts.get("failed", 0) + (counts["images"]["failed"] if self.settings.download_images else 0)
            self.database.session_status(self.session_id, "partial" if failed else "complete")
            self.log(f"任务结束：主题 {counts.get('complete', 0)}，复用 {counts.get('skipped', 0)}，失败 {failed}")
        except Cancelled as error:
            self.database.session_status(self.session_id, "cancelled", str(error))
            self.log(str(error))
        except AccessDenied as error:
            self.database.session_status(self.session_id, "blocked", str(error))
            self.log(str(error))
        except RateLimited as error:
            self.database.session_status(self.session_id, "paused", str(error))
            self.log(f"HTTP 429：任务已暂停并保存到数据库。{error}")
        except Exception as error:
            self.database.session_status(self.session_id, "failed", str(error))
            self.log(f"任务失败：{error}")
        finally:
            self.database.execute("UPDATE collection_sessions SET elapsed_seconds=elapsed_seconds+? WHERE id=?",
                                  (time.monotonic() - started, self.session_id))
