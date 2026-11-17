import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QScrollArea

from core.download_manager import TaskControl
from core.scheduler import Scheduler
from gui.main_window import MainWindow
from tests.conftest import FakeTransport
from core.scheduler import RequestError


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


@pytest.mark.parametrize("succeeds", [False, True])
def test_api_badge_matches_actual_job_result(qtbot, db, settings, tmp_path, monkeypatch, succeeds):
    monkeypatch.setenv("EPUB_ARCHIVE_HOME", str(tmp_path))

    def categories(client):
        if not succeeds:
            raise RequestError("connection failed")
        return [{"id": 1, "name": "公开分类", "slug": "", "topic_count": 1}]

    monkeypatch.setattr("gui.main_window.DiscourseClient.categories", categories)
    window = MainWindow(db, settings, Scheduler(db, settings, transport=FakeTransport([])))
    qtbot.addWidget(window)
    window.connection_badge.setText("公开 API 可用")
    window._test_api()
    assert window.connection_badge.text() == "正在测试公开 API"
    qtbot.waitUntil(lambda: window.active_thread is None, timeout=5000)
    assert window.connection_badge.text() == ("公开 API 可用" if succeeds else "公开 API 测试失败")
    assert window.category_table.rowCount() == (1 if succeeds else 0)
    assert bool(db.one("SELECT * FROM categories WHERE id=1")) == succeeds
    window.close()


def test_checked_categories_survive_search_filter(qtbot, db, settings, tmp_path, monkeypatch):
    monkeypatch.setenv("EPUB_ARCHIVE_HOME", str(tmp_path))
    db.save_categories([
        {"id": 1, "name": "示例分类甲", "slug": "sample-a", "topic_count": 1},
        {"id": 2, "name": "示例分类乙", "slug": "sample-b", "topic_count": 1},
    ])
    window = MainWindow(db, settings, Scheduler(db, settings, transport=FakeTransport([])))
    qtbot.addWidget(window)
    window._load_categories()
    for row in range(window.category_table.rowCount()):
        window.category_table.item(row, 0).setCheckState(Qt.Checked)

    window.category_search.setText("示例分类乙")

    assert window.selected_hint.text() == "已选 2 个分类"
    assert {item[0] for item in window._checked_categories()} == {1, 2}
    window.close()


@pytest.mark.parametrize("field_name", [
    "max_topics", "replies", "recent_days", "image_dimension", "image_quality",
    "image_max_mb", "interval", "jitter", "retries", "timeout",
])
@pytest.mark.parametrize("focused", [False, True])
def test_settings_wheel_scrolls_page_without_changing_values(
        qtbot, db, settings, tmp_path, monkeypatch, field_name, focused):
    monkeypatch.setenv("EPUB_ARCHIVE_HOME", str(tmp_path))
    window = MainWindow(db, settings, Scheduler(db, settings, transport=FakeTransport([])))
    qtbot.addWidget(window)
    window.resize(1100, 760)
    window._show_page(4)
    window.show()
    field = getattr(window, field_name)
    scroll = window.pages.widget(4).findChild(QScrollArea)
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() > 0)
    scroll.ensureWidgetVisible(field)
    field.setFocus() if focused else window.base_url.setFocus()
    QApplication.processEvents()
    before_value = field.currentIndex() if isinstance(field, QComboBox) else field.value()
    bar = scroll.verticalScrollBar()
    before_scroll = bar.value()
    delta = -120 if before_scroll < bar.maximum() else 120
    position = field.rect().center()
    event = QWheelEvent(QPointF(position), QPointF(field.mapToGlobal(position)),
                        QPoint(), QPoint(0, delta), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)

    QApplication.sendEvent(field, event)

    after_value = field.currentIndex() if isinstance(field, QComboBox) else field.value()
    assert after_value == before_value
    assert bar.value() != before_scroll
    qtbot.keyClick(field, Qt.Key_Down if isinstance(field, QComboBox) else Qt.Key_Up)
    edited_value = field.currentIndex() if isinstance(field, QComboBox) else field.value()
    assert edited_value != before_value
    window.close()
