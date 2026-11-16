import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS categories (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL,
 topic_count INTEGER NOT NULL DEFAULT 0, parent_id INTEGER, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS topics (
 id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL, title TEXT NOT NULL, slug TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '',
 reply_count INTEGER NOT NULL DEFAULT 0, collection_status TEXT NOT NULL DEFAULT 'pending',
 last_error TEXT, last_collected_at TEXT, collected_version TEXT, scope TEXT, source_url TEXT);
CREATE INDEX IF NOT EXISTS topics_category_status ON topics(category_id, collection_status, id);
CREATE TABLE IF NOT EXISTS posts (
 id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
 username TEXT NOT NULL, created_at TEXT NOT NULL, cooked_html TEXT NOT NULL,
 post_number INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS posts_topic_number ON posts(topic_id, post_number);
CREATE TABLE IF NOT EXISTS images (
 original_url TEXT PRIMARY KEY, local_path TEXT, download_status TEXT NOT NULL DEFAULT 'pending',
 file_hash TEXT, file_size INTEGER, mime TEXT, last_error TEXT, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS topic_images (
 topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
 original_url TEXT NOT NULL REFERENCES images(original_url), PRIMARY KEY(topic_id, original_url));
CREATE TABLE IF NOT EXISTS collection_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category_ids TEXT NOT NULL,
 settings TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, last_error TEXT, current_item TEXT NOT NULL DEFAULT '',
 elapsed_seconds REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS session_categories (
 session_id INTEGER NOT NULL REFERENCES collection_sessions(id), category_id INTEGER NOT NULL,
 next_page INTEGER NOT NULL DEFAULT 0, discovered INTEGER NOT NULL DEFAULT 0,
 done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session_id, category_id));
CREATE TABLE IF NOT EXISTS download_tasks (
 session_id INTEGER NOT NULL REFERENCES collection_sessions(id), topic_id INTEGER NOT NULL REFERENCES topics(id),
 status TEXT NOT NULL DEFAULT 'pending', last_error TEXT,
 PRIMARY KEY(session_id, topic_id));
CREATE INDEX IF NOT EXISTS tasks_status ON download_tasks(session_id, status, topic_id);
CREATE TABLE IF NOT EXISTS request_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, status INTEGER, failed INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS requests_at ON request_events(at);
CREATE TABLE IF NOT EXISTS books (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, path TEXT NOT NULL, category_ids TEXT NOT NULL,
 topic_count INTEGER NOT NULL, category_count INTEGER NOT NULL, file_size INTEGER NOT NULL,
 created_at TEXT NOT NULL, validation TEXT NOT NULL);
"""


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("数据库由较新版本创建，请升级软件")
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def execute(self, sql, args=()):
        with self.connect() as connection:
            cursor = connection.execute(sql, args)
            return cursor.lastrowid

    def rows(self, sql, args=()):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def iter_rows(self, sql, args=()):
        with self.connect() as connection:
            for row in connection.execute(sql, args):
                yield dict(row)

    def get_meta(self, key, default=None):
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_meta(self, key, value):
        self.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))

    def save_categories(self, categories):
        with self.connect() as connection:
            connection.executemany(
                "INSERT INTO categories VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,slug=excluded.slug,topic_count=excluded.topic_count,"
                "parent_id=excluded.parent_id,updated_at=excluded.updated_at",
                [(item["id"], item["name"], item["slug"], item.get("topic_count", 0),
                  item.get("parent_category_id"), utc_now()) for item in categories])

    def category_counts(self):
        return self.rows("SELECT c.*,COUNT(t.id) AS discovered,"
                         "COALESCE(SUM(t.collection_status='complete'),0) AS collected "
                         "FROM categories c LEFT JOIN topics t ON t.category_id=c.id GROUP BY c.id ORDER BY c.id")

    def upsert_topic(self, topic):
        self.execute(
            "INSERT INTO topics(id,category_id,title,slug,created_at,updated_at,reply_count,source_url) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET category_id=excluded.category_id,"
            "title=excluded.title,slug=excluded.slug,updated_at=excluded.updated_at,"
            "reply_count=excluded.reply_count,source_url=excluded.source_url",
            (topic["id"], topic["category_id"], topic["title"], topic.get("slug", ""),
             topic.get("created_at", ""), topic["updated_at"], topic.get("reply_count", 0),
             topic.get("source_url") or None))

    def save_posts(self, topic_id, posts, version, scope, image_urls):
        with self.connect() as connection:
            connection.execute("DELETE FROM posts WHERE topic_id=?", (topic_id,))
            connection.executemany("INSERT INTO posts VALUES(?,?,?,?,?,?)",
                                   [(post["id"], topic_id, post["username"], post["created_at"],
                                     post["cooked"], post["post_number"]) for post in posts])
            connection.execute("DELETE FROM topic_images WHERE topic_id=?", (topic_id,))
            for url in set(image_urls):
                connection.execute("INSERT OR IGNORE INTO images(original_url) VALUES(?)", (url,))
                connection.execute("INSERT INTO topic_images VALUES(?,?)", (topic_id, url))
            connection.execute(
                "UPDATE topics SET collection_status='complete',collected_version=?,scope=?,"
                "last_collected_at=?,last_error=NULL WHERE id=?", (version, scope, utc_now(), topic_id))

    def create_session(self, name, category_ids, settings):
        stamp = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO collection_sessions(name,category_ids,settings,created_at,updated_at) VALUES(?,?,?,?,?)",
                (name, json.dumps(category_ids), json.dumps(settings), stamp, stamp))
            session_id = cursor.lastrowid
            connection.executemany("INSERT INTO session_categories(session_id,category_id) VALUES(?,?)",
                                   [(session_id, category_id) for category_id in category_ids])
            return session_id

    def recover(self):
        with self.connect() as connection:
            connection.execute("UPDATE collection_sessions SET status='paused',updated_at=? WHERE status='running'",
                               (utc_now(),))
            connection.execute("UPDATE download_tasks SET status='pending' WHERE status='running'")
            connection.execute("UPDATE images SET download_status='pending' WHERE download_status='running'")

    def session_status(self, session_id, status, error=None):
        self.execute("UPDATE collection_sessions SET status=?,last_error=?,updated_at=? WHERE id=?",
                     (status, error, utc_now(), session_id))

    def task_counts(self, session_id):
        counts = {row["status"]: row["count"] for row in self.rows(
            "SELECT status,COUNT(*) AS count FROM download_tasks WHERE session_id=? GROUP BY status", (session_id,))}
        counts["total"] = sum(counts.values())
        counts["remaining"] = counts.get("pending", 0) + counts.get("running", 0)
        images = self.one(
            "SELECT COUNT(*) AS total,COALESCE(SUM(download_status='complete'),0) AS complete,"
            "COALESCE(SUM(download_status='failed'),0) AS failed FROM images WHERE original_url IN "
            "(SELECT ti.original_url FROM topic_images ti JOIN download_tasks dt ON dt.topic_id=ti.topic_id "
            "WHERE dt.session_id=?)", (session_id,))
        counts["images"] = images
        counts["discovery_done"] = not self.one(
            "SELECT 1 FROM session_categories WHERE session_id=? AND done=0 LIMIT 1", (session_id,))
        return counts
