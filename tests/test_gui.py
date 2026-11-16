import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.download_manager import TaskControl
from core.scheduler import Scheduler
from gui.main_window import MainWindow
from tests.conftest import FakeTransport


def test_gui_pause_resume_and_cancel_state(qtbot, db, settings, tmp_path, monkeypatch):
    monkeypatch.setenv("EPUB_ARCHIVE_HOME", str(tmp_path))
    settings.download_dir = str(tmp_path / "images")
    settings.output_dir = str(tmp_path / "output")
    db.save_categories([{"id": 1, "name": "公开分类", "slug": "public", "topic_count": 1}])
    session = db.create_session("界面测试", [1], settings.__dict__)
    scheduler = Scheduler(db, settings, transport=FakeTransport([]))
    window = MainWindow(db, settings, scheduler)
    qtbot.addWidget(window)
    window.active_session = session
    window.active_control = TaskControl()
    window.database.session_status(session, "running")
    window._pause_task()
    assert window.active_control.pause_event.is_set()
    assert scheduler.metrics()["paused"] is True
    assert db.one("SELECT status FROM collection_sessions WHERE id=?", (session,))["status"] == "paused"
    window._resume_task()
    assert not window.active_control.pause_event.is_set()
    assert scheduler.metrics()["paused"] is False
    assert db.one("SELECT status FROM collection_sessions WHERE id=?", (session,))["status"] == "running"
    window._cancel_task()
    assert window.active_control.cancel_event.is_set()
    window.active_control = None
    window.close()
