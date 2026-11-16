import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from config.settings import Settings, app_home
from core.database import Database
from core.log_setup import configure_logging
from core.scheduler import Scheduler
from gui.main_window import MainWindow


def main():
    home = app_home()
    for directory in ("data", "output", "logs"):
        (home / directory).mkdir(parents=True, exist_ok=True)
    marker = home / "logs" / "startup.log"
    with marker.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat()} entered main\n")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    application = QApplication(sys.argv)
    application.setApplicationName("Discourse 文集归档器")
    application.setOrganizationName("DiscourseEpubArchive")
    resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    icon_path = resources / "resources" / "icons" / "app.ico"
    if icon_path.exists():
        application.setWindowIcon(QIcon(str(icon_path)))
    try:
        settings = Settings.load(home)
        database = Database(home / "data" / "archive.db")
        scheduler = Scheduler(database, settings)
        logger = configure_logging(home / "logs")
        window = MainWindow(database, settings, scheduler)
        window.show()
        logger.info("应用启动；数据库 %s", database.path)
        with marker.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().isoformat()} window shown\n")
        if "--smoke-test" in sys.argv:
            QTimer.singleShot(1200, application.quit)
    except Exception as error:
        with marker.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().isoformat()} startup error: {error}\n")
        QMessageBox.critical(None, "启动失败", f"无法启动 Discourse 文集归档器：\n{error}")
        return 1
    code = application.exec()
    with marker.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat()} exited {code}\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
