import json
import logging
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QHeaderView)

from config.settings import Settings, app_home
from core.database import Database
from core.discourse_client import DiscourseClient
from core.download_manager import DownloadManager, TaskControl
from core.epub_builder import EpubBuilder, EpubValidator
from core.image_service import ImageService
from core.scheduler import AccessDenied, Scheduler
from gui.components import Card, Section, button, labeled, page_header, progress_row
from gui.theme import STYLE


class JobThread(QThread):
    log = Signal(str)
    completed = Signal(bool, str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            message = self.function(self.log.emit)
            self.completed.emit(True, str(message or "完成"))
        except Exception as error:
            self.log.emit(f"错误：{error}")
            self.completed.emit(False, str(error))


class NavButton(QPushButton):
    def __init__(self, label, parent=None):
        super().__init__(label, parent)
        self.setObjectName("Nav")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)


class MainWindow(QMainWindow):
    def __init__(self, database, settings, scheduler):
        super().__init__()
        self.database = database
        self.settings = settings
        self.scheduler = scheduler
        self.home = app_home()
        self.active_thread = None
        self.active_control = None
        self.active_session = None
        self.job_session = None
        self.current_page = 0
        self.session_started = {}
        self.setWindowTitle("Discourse 文集归档器 · EPUB 工作台")
        self.resize(1420, 900)
        self.setMinimumSize(1120, 720)
        self.setStyleSheet(STYLE)
        self.database.recover()
        self._build()
        self._load_categories()
        self._load_sessions()
        self._load_books()
        self._sync_permission_badges()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._tick()

    def _build(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._sidebar())
        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        self.pages.addWidget(self._overview())
        self.pages.addWidget(self._categories())
        self.pages.addWidget(self._tasks())
        self.pages.addWidget(self._books())
        self.pages.addWidget(self._settings())
        self.pages.addWidget(self._logs())

    def _sidebar(self):
        panel = QFrame()
        panel.setObjectName("Sidebar")
        panel.setFixedWidth(218)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 26, 18, 20)
        layout.setSpacing(6)
        eyebrow = QLabel("ARCHIVE WORKBENCH")
        eyebrow.setObjectName("Eyebrow")
        brand = QLabel("文集归档器")
        brand.setObjectName("Brand")
        edition = QLabel("EPUB 3 / 本地优先")
        edition.setObjectName("Muted")
        layout.addWidget(eyebrow)
        layout.addWidget(brand)
        layout.addWidget(edition)
        layout.addSpacing(34)
        self.nav_buttons = []
        for index, text in enumerate(("概览", "分类管理", "采集任务", "电子书管理", "下载设置", "日志")):
            nav = NavButton(text)
            nav.clicked.connect(lambda checked=False, i=index: self._show_page(i))
            layout.addWidget(nav)
            self.nav_buttons.append(nav)
        layout.addStretch()
        line = QLabel("PUBLIC ARCHIVE\nSINGLE REQUEST · RESPECT LIMITS")
        line.setObjectName("Eyebrow")
        line.setWordWrap(True)
        layout.addWidget(line)
        layout.addSpacing(8)
        note = QLabel("仅限获授权的公开内容采集。\n文本优先，图片排队。")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.nav_buttons[0].setChecked(True)
        return panel

    def _page_shell(self, header):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 24, 30, 22)
        layout.setSpacing(14)
        layout.addWidget(header)
        return page, layout

    def _overview(self):
        page, layout = self._page_shell(page_header(
            "工作台概览", "公开内容采集、断点续传与 EPUB 制作的状态总览"))
        hero = QFrame()
        hero.setObjectName("Hero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(24, 19, 22, 19)
        copy = QVBoxLayout()
        copy.setSpacing(5)
        eyebrow = QLabel("DISCOURSE / OFFLINE READING")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("把公开主题整理成一本可长期保存的书")
        title.setObjectName("SectionTitle")
        desc = QLabel("采集与排版分离。任何一次中断，都会从数据库中的真实进度继续。")
        desc.setObjectName("Muted")
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(desc)
        hero_layout.addLayout(copy, 1)
        self.site_badge = QLabel("未确认许可 · 不联网")
        self.site_badge.setObjectName("BadgeWarn")
        hero_layout.addWidget(self.site_badge)
        self.connection_badge = QLabel("等待站点配置")
        self.connection_badge.setObjectName("Badge")
        hero_layout.addWidget(self.connection_badge)
        layout.addWidget(hero)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_categories = Card("分类", "0", "本地已发现")
        self.card_topics = Card("主题", "0", "已完整采集")
        self.card_images = Card("图片", "0", "离线可用")
        self.card_books = Card("EPUB", "0", "通过校验")
        for card in (self.card_categories, self.card_topics, self.card_images, self.card_books):
            cards.addWidget(card, 1)
        layout.addLayout(cards)

        split = QHBoxLayout()
        split.setSpacing(12)
        monitor = Section("服务器保护", "所有 API 与图片请求共用一个串行调度器；限流状态会持久化。")
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(13)
        self.metric_labels = {}
        metrics = [("当前速度", "0.00 次/分"), ("最近 5 分钟", "0 次"), ("HTTP 429", "0"),
                   ("HTTP 403", "0"), ("请求失败", "0"), ("冷却剩余", "0 秒"),
                   ("全局间隔", "4.0 秒"), ("待处理主题", "0")]
        for index, (name, initial) in enumerate(metrics):
            label = QLabel(name)
            label.setObjectName("Muted")
            value = QLabel(initial)
            value.setObjectName("MetricSmall")
            grid.addWidget(label, index // 2 * 2, index % 2)
            grid.addWidget(value, index // 2 * 2 + 1, index % 2)
            self.metric_labels[name] = value
        monitor.layout.addLayout(grid)
        self.cooldown_note = QLabel("服务器拒绝时在这里显示冷却与人工检查提示。")
        self.cooldown_note.setObjectName("Muted")
        self.cooldown_note.setWordWrap(True)
        monitor.add(self.cooldown_note)
        monitor.layout.addStretch()
        split.addWidget(monitor, 3)

        work = Section("当前任务", "进度直接读取数据库，不使用估算值。")
        self.ov_current = QLabel("没有运行中的采集任务")
        self.ov_current.setWordWrap(True)
        self.ov_current.setObjectName("MetricSmall")
        work.add(self.ov_current)
        self.ov_progress = QProgressBar()
        self.ov_progress.setRange(0, 100)
        self.ov_progress.setValue(0)
        work.add(self.ov_progress)
        self.ov_hint = QLabel("从「分类管理」选择分类后建立任务。")
        self.ov_hint.setObjectName("Muted")
        self.ov_hint.setWordWrap(True)
        work.add(self.ov_hint)
        open_tasks = button("查看采集任务", "Quiet")
        open_tasks.clicked.connect(lambda: self._show_page(2))
        work.add(open_tasks)
        work.layout.addStretch()
        split.addWidget(work, 2)
        layout.addLayout(split, 1)
        return page

    def _categories(self):
        actions = [button("刷新分类", "Quiet"), button("全选", "Quiet"), button("反选", "Quiet")]
        actions[0].clicked.connect(self._refresh_categories)
        actions[1].clicked.connect(lambda: self._set_checks(True))
        actions[2].clicked.connect(self._invert_checks)
        page, layout = self._page_shell(page_header(
            "分类管理", "网站主题数仅供参考；已采集数来自本地数据库", actions))
        bar = QFrame()
        bar.setObjectName("Panel")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(14, 11, 14, 11)
        self.category_search = QLineEdit()
        self.category_search.setPlaceholderText("搜索分类名称或 slug")
        self.category_search.setClearButtonEnabled(True)
        self.category_search.textChanged.connect(self._filter_categories)
        bar_layout.addWidget(self.category_search, 1)
        self.category_count = QLabel("0 个分类")
        self.category_count.setObjectName("Muted")
        bar_layout.addWidget(self.category_count)
        layout.addWidget(bar)
        self.category_table = QTableWidget(0, 6)
        self.category_table.setHorizontalHeaderLabels(["", "分类名称", "网站主题", "已采集", "未采集", "分类 ID"])
        self.category_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.category_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.category_table.setAlternatingRowColors(True)
        self.category_table.verticalHeader().setVisible(False)
        header = self.category_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.category_table.setColumnWidth(0, 44)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in (2, 3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.category_table.itemChanged.connect(self._update_selected)
        layout.addWidget(self.category_table, 1)
        footer = QHBoxLayout()
        self.task_name = QLineEdit(f"文集归档 {datetime.now():%Y-%m-%d %H%M}")
        self.task_name.setMinimumWidth(270)
        footer.addWidget(QLabel("任务名称"))
        footer.addWidget(self.task_name)
        footer.addStretch()
        self.selected_hint = QLabel("已选 0 个分类")
        self.selected_hint.setObjectName("Muted")
        footer.addWidget(self.selected_hint)
        start = button("建立采集任务", "Primary")
        start.clicked.connect(self._start_collection)
        footer.addWidget(start)
        layout.addLayout(footer)
        return page

    def _tasks(self):
        page, layout = self._page_shell(page_header(
            "采集任务", "暂停、续传和重试均由 SQLite 状态驱动"))
        detail = QFrame()
        detail.setObjectName("Panel")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(18, 16, 18, 16)
        top = QHBoxLayout()
        heading = QLabel("任务进度")
        heading.setObjectName("SectionTitle")
        top.addWidget(heading)
        top.addStretch()
        for text, kind, callback in (("暂停", "Quiet", self._pause_task),
                                     ("继续", "Quiet", self._resume_task),
                                     ("取消", "Danger", self._cancel_task),
                                     ("重试失败项", "", self._retry_failed)):
            control = button(text, kind)
            control.clicked.connect(callback)
            top.addWidget(control)
        detail_layout.addLayout(top)
        self.task_current = QLabel("选择一个任务查看真实进度")
        self.task_current.setObjectName("MetricSmall")
        self.task_current.setWordWrap(True)
        detail_layout.addWidget(self.task_current)
        self.task_bar = QProgressBar()
        self.task_bar.setRange(0, 100)
        detail_layout.addWidget(progress_row("主题", self.task_bar, QLabel("—")))
        self.image_bar = QProgressBar()
        self.image_bar.setRange(0, 100)
        detail_layout.addWidget(progress_row("图片", self.image_bar, QLabel("—")))
        self.task_stats = QLabel("主题 —　图片 —　失败 —　耗时 —")
        self.task_stats.setObjectName("Muted")
        detail_layout.addWidget(self.task_stats)
        layout.addWidget(detail)

        self.session_table = QTableWidget(0, 7)
        self.session_table.setHorizontalHeaderLabels(
            ["任务", "状态", "当前内容", "主题进度", "图片", "失败", "已用时间"])
        self.session_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.session_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.session_table.setAlternatingRowColors(True)
        self.session_table.verticalHeader().setVisible(False)
        header = self.session_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for column in range(3, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.session_table.itemSelectionChanged.connect(self._session_selected)
        layout.addWidget(self.session_table, 1)
        return page

    def _books(self):
        actions = [button("刷新", "Quiet"), button("从已采集分类生成", "Primary")]
        actions[0].clicked.connect(self._load_books)
        actions[1].clicked.connect(self._new_book)
        page, layout = self._page_shell(page_header(
            "电子书管理", "已生成文件、校验状态与来源记录", actions))
        self.book_table = QTableWidget(0, 7)
        self.book_table.setHorizontalHeaderLabels(["书名", "主题", "分类", "文件大小", "生成时间", "校验", "文件"])
        self.book_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.book_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.book_table.setAlternatingRowColors(True)
        self.book_table.verticalHeader().setVisible(False)
        header = self.book_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.book_table.setColumnWidth(6, 210)
        layout.addWidget(self.book_table, 1)
        footer = QHBoxLayout()
        note = QLabel("EPUB 只有在 EPUBCheck 返回 0 时才会写入生成记录。")
        note.setObjectName("Muted")
        footer.addWidget(note)
        footer.addStretch()
        delete = button("删除生成记录", "Danger")
        delete.clicked.connect(self._delete_book_record)
        footer.addWidget(delete)
        layout.addLayout(footer)
        return page

    def _settings(self):
        page, layout = self._page_shell(page_header(
            "下载与保护设置", "默认值偏保守；任何修改都不会绕过服务器的明确拒绝"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        form = QVBoxLayout(content)
        form.setContentsMargins(2, 2, 10, 4)
        form.setSpacing(12)

        permission = Section("站点与联网许可", "填写目标 Discourse 站点的 HTTPS 根地址。"
                                      "请确认服务条款、robots.txt 和站方许可允许自动访问；"
                                      "未完成配置时所有网络请求都会被本地阻止。")
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://forum.example.com")
        self.base_url.setClearButtonEnabled(True)
        permission.add(labeled("Discourse 站点", self.base_url))
        self.permission_check = QCheckBox("我已取得授权，并会控制请求频率")
        self.permission_note = QLineEdit()
        self.permission_note.setPlaceholderText("授权依据，例如邮件日期 / 书面许可编号")
        permission.add(self.permission_check)
        permission.add(self.permission_note)
        test = button("测试公开 API（发送 1 次 robots + 1 次分类请求）", "Quiet")
        test.clicked.connect(self._test_api)
        permission.layout.addWidget(test, alignment=Qt.AlignLeft)
        form.addWidget(permission)

        group = Section("采集范围")
        grid = QGridLayout()
        self.max_topics = QSpinBox()
        self.max_topics.setRange(0, 100000)
        self.max_topics.setSpecialValueText("不限制")
        self.replies = QComboBox()
        self.replies.addItems(["主帖 + 全部公开回复", "仅主帖"])
        self.recent_days = QSpinBox()
        self.recent_days.setRange(0, 3650)
        self.recent_days.setSpecialValueText("不按时间筛选")
        self.skip_unchanged = QCheckBox("跳过已采集且未更新的主题")
        for index, (label, widget) in enumerate((
                ("每个分类最大主题数", self.max_topics), ("帖子范围", self.replies),
                ("只采集最近 N 天更新", self.recent_days), ("增量采集", self.skip_unchanged))):
            grid.addWidget(labeled(label, widget), index // 2, index % 2)
        group.layout.addLayout(grid)
        form.addWidget(group)

        images = Section("图片")
        grid = QGridLayout()
        self.download_images = QCheckBox("下载正文图片到本地")
        self.compress_images = QCheckBox("主动压缩（可能损失质量）")
        self.image_dimension = QSpinBox()
        self.image_dimension.setRange(256, 10000)
        self.image_dimension.setSuffix(" px")
        self.image_quality = QSpinBox()
        self.image_quality.setRange(1, 100)
        self.image_quality.setSuffix(" %")
        self.image_max_mb = QSpinBox()
        self.image_max_mb.setRange(1, 100)
        self.image_max_mb.setSuffix(" MB")
        for index, (label, widget) in enumerate((
                ("离线图片", self.download_images), ("无损优先", self.compress_images),
                ("长边上限", self.image_dimension), ("压缩质量", self.image_quality),
                ("单图大小上限", self.image_max_mb))):
            grid.addWidget(labeled(label, widget), index // 2, index % 2)
        images.layout.addLayout(grid)
        form.addWidget(images)

        network = Section("请求节奏", "最短间隔 3 秒。收到 429 会全局暂停并增加后续间隔；恢复不会瞬间提速。")
        grid = QGridLayout()
        self.interval = QDoubleSpinBox()
        self.interval.setRange(3, 300)
        self.interval.setDecimals(1)
        self.interval.setSuffix(" 秒")
        self.jitter = QDoubleSpinBox()
        self.jitter.setRange(0, 1)
        self.jitter.setDecimals(1)
        self.jitter.setSuffix(" 秒")
        self.retries = QSpinBox()
        self.retries.setRange(0, 5)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(5, 120)
        self.timeout.setSuffix(" 秒")
        self.epubcheck = QLineEdit()
        self.epubcheck.setPlaceholderText("留空使用 tools/epubcheck/epubcheck.jar")
        choose = button("选择", "Quiet")
        choose.clicked.connect(self._choose_epubcheck)
        jar_row = QWidget()
        jar_layout = QHBoxLayout(jar_row)
        jar_layout.setContentsMargins(0, 0, 0, 0)
        jar_layout.addWidget(self.epubcheck, 1)
        jar_layout.addWidget(choose)
        for index, (label, widget) in enumerate((
                ("全局请求间隔", self.interval), ("随机抖动", self.jitter),
                ("失败重试次数", self.retries), ("网络超时", self.timeout))):
            grid.addWidget(labeled(label, widget), index // 2, index % 2)
        grid.addWidget(labeled("EPUBCheck", jar_row), 2, 0, 1, 2)
        network.layout.addLayout(grid)
        form.addWidget(network)

        paths = Section("目录")
        self.download_dir = QLineEdit()
        self.output_dir = QLineEdit()
        for label, field, action in (("图片缓存", self.download_dir, self._choose_download_dir),
                                     ("EPUB 输出", self.output_dir, self._choose_output_dir)):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(field, 1)
            choose = button("浏览", "Quiet")
            choose.clicked.connect(action)
            row_layout.addWidget(choose)
            paths.add(labeled(label, row))
        form.addWidget(paths)
        form.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        footer = QHBoxLayout()
        unlock = button("解除 403 锁定…", "Danger")
        unlock.clicked.connect(self._unlock_403)
        footer.addWidget(unlock)
        footer.addStretch()
        save = button("保存设置", "Primary")
        save.clicked.connect(self._save_settings)
        footer.addWidget(save)
        layout.addLayout(footer)
        self._fill_settings()
        return page

    def _logs(self):
        page, layout = self._page_shell(page_header("日志", "运行事件、接口错误与写入磁盘的日志文件"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("采集和生成事件将显示在这里。")
        layout.addWidget(self.log_view, 1)
        footer = QHBoxLayout()
        clear = button("清空显示", "Quiet")
        clear.clicked.connect(self.log_view.clear)
        folder = button("打开日志目录", "Quiet")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.home / "logs"))))
        footer.addWidget(clear)
        footer.addWidget(folder)
        footer.addStretch()
        layout.addLayout(footer)
        return page

    def _show_page(self, index):
        self.pages.setCurrentIndex(index)
        for position, nav in enumerate(self.nav_buttons):
            nav.setChecked(position == index)
        self.current_page = index
        if index == 2:
            self._load_sessions()
        if index == 3:
            self._load_books()

    def log(self, message):
        logging.getLogger("discourse_epub_archive").info(message)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f'<span style="color:#6e8074">{stamp}</span>&nbsp;&nbsp;'
                             f'{message.replace("<", "&lt;").replace(">", "&gt;")}')
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _load_categories(self):
        rows = self.database.category_counts()
        self.category_table.setRowCount(0)
        for row in rows:
            index = self.category_table.rowCount()
            self.category_table.insertRow(index)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Unchecked)
            check.setData(Qt.UserRole, row["id"])
            check.setData(Qt.UserRole + 1, row["name"])
            self.category_table.setItem(index, 0, check)
            values = (row["name"], row["topic_count"], row["collected"], max(0, row["discovered"] - row["collected"]),
                      row["id"])
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(str(value))
                if column >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.category_table.setItem(index, column, item)
        self._update_selected()

    def _filter_categories(self, text):
        text = text.lower().strip()
        for row in range(self.category_table.rowCount()):
            haystack = " ".join(self.category_table.item(row, column).text() for column in (1, 5)).lower()
            self.category_table.setRowHidden(row, bool(text) and text not in haystack)
        self._update_selected()

    def _checked_categories(self):
        result = []
        for row in range(self.category_table.rowCount()):
            item = self.category_table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                result.append((item.data(Qt.UserRole), item.data(Qt.UserRole + 1)))
        return result

    def _update_selected(self):
        checked = self._checked_categories()
        total = sum(not self.category_table.isRowHidden(row) for row in range(self.category_table.rowCount()))
        self.selected_hint.setText(f"已选 {len(checked)} 个分类")
        self.category_count.setText(f"{total} 个分类 · 网站主题数为动态值")

    def _set_checks(self, state):
        for row in range(self.category_table.rowCount()):
            if not self.category_table.isRowHidden(row):
                self.category_table.item(row, 0).setCheckState(Qt.Checked if state else Qt.Unchecked)
        self._update_selected()

    def _invert_checks(self):
        for row in range(self.category_table.rowCount()):
            if not self.category_table.isRowHidden(row):
                item = self.category_table.item(row, 0)
                item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        self._update_selected()

    def _require_permission(self):
        if (self.settings.permission_confirmed and self.settings.permission_note.strip()
                and self.settings.base_url.strip()):
            return True
        QMessageBox.warning(self, "需要站点配置与授权", "请先在「下载设置」中填写目标站点地址，"
                                                     "确认已取得授权并记录依据。软件不会绕过访问限制。")
        self._show_page(4)
        return False

    def _refresh_categories(self):
        if not self._require_permission() or not self._idle():
            return

        def work(log):
            client = DiscourseClient(self.scheduler)
            items = client.categories()
            self.database.save_categories(items)
            return f"已更新 {len(items)} 个分类"

        self._run_job(work, "刷新分类", then=self._load_categories)

    def _test_api(self):
        if not self._save_settings(silent=True) or not self._require_permission() or not self._idle():
            return
        self.connection_badge.setText("正在测试公开 API")

        def work(log):
            items = DiscourseClient(self.scheduler).categories()
            self.database.save_categories(items)
            return f"公开 API 可用，读取到 {len(items)} 个分类"

        def completed(ok, message):
            self.connection_badge.setText("公开 API 可用" if ok else "公开 API 测试失败")
            if ok:
                self._load_categories()

        self._run_job(work, "验证连接", on_result=completed)

    def _start_collection(self):
        if not self._require_permission() or not self._idle():
            return
        categories = self._checked_categories()
        if not categories:
            QMessageBox.information(self, "尚未选择", "请勾选至少一个分类。")
            return
        if not self._save_settings(silent=True):
            return
        name = self.task_name.text().strip() or f"文集归档 {datetime.now():%Y-%m-%d %H%M}"
        session_id = self.database.create_session(name, [item[0] for item in categories], asdict(self.settings))
        self.active_session = session_id
        self.job_session = session_id
        self.session_started[session_id] = time.monotonic()
        control = TaskControl()
        self.active_control = control
        try:
            self.scheduler.resume()
        except AccessDenied as error:
            self.active_control = None
            QMessageBox.warning(self, "服务器已锁定", str(error))
            return

        def work(log):
            manager = DownloadManager(self.database, self.scheduler, session_id, control, log)
            manager.run()
            return "采集任务已结束"

        self.log(f"建立任务《{name}》：{len(categories)} 个分类")
        self._run_job(work, "采集", then=self._after_collection)
        self._load_sessions()
        self._show_page(2)

    def _after_collection(self):
        job_session = self.job_session or self.active_session
        self.active_control = None
        self.job_session = None
        self._load_sessions()
        self._load_categories()
        self._load_books()
        if job_session:
            session = self.database.one("SELECT status,last_error FROM collection_sessions WHERE id=?",
                                        (job_session,))
            if session and session["status"] in ("blocked", "failed"):
                QMessageBox.warning(self, "任务未完成", session["last_error"] or "请查看日志")

    def _load_sessions(self):
        rows = self.database.rows(
            "SELECT s.*,"
            "(SELECT COUNT(*) FROM download_tasks d WHERE d.session_id=s.id) AS total,"
            "(SELECT COUNT(*) FROM download_tasks d WHERE d.session_id=s.id AND d.status='complete') AS complete,"
            "(SELECT COUNT(*) FROM download_tasks d WHERE d.session_id=s.id AND d.status='skipped') AS skipped,"
            "(SELECT COUNT(*) FROM download_tasks d WHERE d.session_id=s.id AND d.status='failed') AS failed,"
            "(SELECT COUNT(*) FROM images i JOIN topic_images ti ON ti.original_url=i.original_url "
            " JOIN download_tasks d ON d.topic_id=ti.topic_id WHERE d.session_id=s.id AND i.download_status='complete') "
            " AS image_complete,"
            "(SELECT COUNT(*) FROM images i JOIN topic_images ti ON ti.original_url=i.original_url "
            " JOIN download_tasks d ON d.topic_id=ti.topic_id WHERE d.session_id=s.id) AS image_total "
            "FROM collection_sessions s ORDER BY s.id DESC")
        self.session_table.setRowCount(0)
        for row in rows:
            index = self.session_table.rowCount()
            self.session_table.insertRow(index)
            total = row["total"] or 0
            complete = row["complete"] + row["skipped"]
            progress = f"{complete}/{total}" if total else "发现中"
            image_progress = f'{row["image_complete"]}/{row["image_total"]}'
            elapsed = row["elapsed_seconds"]
            if row["status"] == "running" and row["id"] in self.session_started:
                elapsed += time.monotonic() - self.session_started[row["id"]]
            status = {"pending": "待开始", "running": "运行中", "paused": "已暂停", "complete": "已完成",
                      "partial": "部分完成", "blocked": "已阻止", "failed": "失败",
                      "cancelled": "已取消"}.get(row["status"], row["status"])
            values = (row["name"], status, row["current_item"] or "—", progress, image_progress,
                      str(row["failed"]), self._duration(elapsed))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row["id"])
                if column == 1:
                    item.setForeground(QColor("#94c5a1" if row["status"] == "running" else
                                              "#d6a297" if row["status"] in ("blocked", "failed") else
                                              "#c7c2b3"))
                self.session_table.setItem(index, column, item)
        if not self.active_session and rows:
            self.active_session = rows[0]["id"]
        if self.active_session:
            for index in range(self.session_table.rowCount()):
                if self.session_table.item(index, 0).data(Qt.UserRole) == self.active_session:
                    self.session_table.selectRow(index)
                    break
        self._session_selected()

    def _session_selected(self):
        row = self.session_table.currentRow()
        if row < 0:
            return
        self.active_session = self.session_table.item(row, 0).data(Qt.UserRole)
        session = self.database.one("SELECT * FROM collection_sessions WHERE id=?", (self.active_session,))
        if not session:
            return
        counts = self.database.task_counts(self.active_session)
        total = counts.get("total", 0)
        done = counts.get("complete", 0) + counts.get("skipped", 0)
        failed = counts.get("failed", 0)
        self.task_current.setText(session["current_item"] or f'任务「{session["name"]}」尚未开始')
        self.task_bar.setValue(round(done * 100 / total) if total else 0)
        images = counts["images"]
        self.image_bar.setValue(round(images["complete"] * 100 / images["total"]) if images["total"] else 0)
        self.task_stats.setText(
            f'主题 {done}/{total}　图片 {images["complete"]}/{images["total"]}　失败 '
            f'{failed + images["failed"]}　耗时 {self._duration(session["elapsed_seconds"])}')
        self.task_bar.setFormat(f"{done}/{total}" if total else "发现中")
        self.image_bar.setFormat(f'{images["complete"]}/{images["total"]}')

    def _pause_task(self):
        if not self.active_control:
            QMessageBox.information(self, "没有运行中的任务", "请先建立或恢复一个采集任务。")
            return
        self.active_control.pause()
        self.scheduler.pause()
        if self.job_session or self.active_session:
            self.database.session_status(self.job_session or self.active_session, "paused")
        self.log("任务已暂停；网络队列会停在当前请求之后。")
        self._load_sessions()

    def _resume_task(self):
        if self.scheduler.metrics()["forbidden"]:
            QMessageBox.warning(self, "403 锁定", "必须人工检查访问权限，并在「下载设置」中明确解除锁定。")
            return
        if self.active_control:
            self.scheduler.resume()
            self.active_control.resume()
            if self.job_session or self.active_session:
                self.database.session_status(self.job_session or self.active_session, "running")
            self.log("任务已继续。")
            self._load_sessions()
            return
        if not self.active_session:
            return
        session = self.database.one("SELECT * FROM collection_sessions WHERE id=?", (self.active_session,))
        if session["status"] not in ("paused", "cancelled", "blocked", "failed", "partial", "pending"):
            return
        if not self._require_permission() or not self._idle():
            return
        control = TaskControl()
        self.active_control = control
        self.job_session = self.active_session
        self.session_started[self.active_session] = time.monotonic()
        try:
            self.scheduler.resume(reviewed_403=session["status"] == "blocked")
        except AccessDenied as error:
            self.active_control = None
            QMessageBox.warning(self, "服务器已锁定", str(error))
            return

        def work(log):
            DownloadManager(self.database, self.scheduler, self.active_session, control, log).run(
                retry_failed=session["status"] in ("partial", "failed"))
            return "采集任务已结束"

        self._run_job(work, "继续采集", then=self._after_collection)

    def _cancel_task(self):
        if not self.active_control:
            return
        self.active_control.cancel()
        self.log("正在停止；当前请求结束后保存进度。")

    def _retry_failed(self):
        if not self.active_session or not self._idle():
            return
        if not self._require_permission():
            return
        control = TaskControl()
        self.active_control = control
        self.job_session = self.active_session
        self.session_started[self.active_session] = time.monotonic()
        try:
            self.scheduler.resume()
        except AccessDenied as error:
            self.active_control = None
            QMessageBox.warning(self, "服务器已锁定", str(error))
            return

        def work(log):
            DownloadManager(self.database, self.scheduler, self.active_session, control, log).run(retry_failed=True)
            return "失败项重试结束"

        self._run_job(work, "重试", then=self._after_collection)

    def _book_categories(self):
        rows = self.database.rows(
            "SELECT category_id,COUNT(*) AS count FROM topics WHERE collection_status='complete' "
            "GROUP BY category_id HAVING count>0 ORDER BY count DESC")
        if not rows:
            QMessageBox.information(self, "没有内容", "还没有完整采集的主题。")
            return []
        names = {row["id"]: row["name"] for row in self.database.rows("SELECT id,name FROM categories")}
        choices = {f'{names.get(row["category_id"], row["category_id"])} · {row["count"]} 篇': row["category_id"]
                   for row in rows}
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QListWidgetItem
        dialog = QDialog(self)
        dialog.setWindowTitle("选择要合并的分类")
        dialog.resize(460, 480)
        layout = QVBoxLayout(dialog)
        note = QLabel("可多选。分类将作为 EPUB 的一级目录。")
        note.setObjectName("Muted")
        layout.addWidget(note)
        listing = QListWidget()
        listing.setAlternatingRowColors(True)
        for label, category_id in choices.items():
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, category_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            listing.addItem(item)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return []
        return [listing.item(index).data(Qt.UserRole) for index in range(listing.count())
                if listing.item(index).checkState() == Qt.Checked]

    def _new_book(self):
        categories = self._book_categories()
        if not categories:
            return
        self._generate_book(f"文集归档 {datetime.now():%Y-%m-%d}", categories)

    def _generate_book(self, title, category_ids):
        if not self._idle():
            return
        validator = EpubValidator(self.settings.epubcheck_jar)

        def work(log):
            builder = EpubBuilder(self.database, self.settings.output_dir, validator, log,
                                  base_url=self.settings.base_url)
            return f"已生成：{builder.build(title, category_ids)}"

        self._run_job(work, "制作 EPUB", then=self._load_books)
        self._show_page(3)

    def _load_books(self):
        rows = self.database.rows("SELECT * FROM books ORDER BY id DESC")
        self.book_table.setRowCount(0)
        for row in rows:
            index = self.book_table.rowCount()
            self.book_table.insertRow(index)
            stamp = row["created_at"].replace("T", " ")[:19]
            values = (row["title"], str(row["topic_count"]), str(row["category_count"]),
                      self._size(row["file_size"]), stamp, "通过", "")
            for column, value in enumerate(values[:-1]):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row["id"])
                self.book_table.setItem(index, column, item)
            tools = QWidget()
            tools_layout = QHBoxLayout(tools)
            tools_layout.setContentsMargins(4, 2, 4, 2)
            tools_layout.setSpacing(4)
            for label, callback in (("打开", lambda checked=False, path=row["path"]:
                                    QDesktopServices.openUrl(QUrl.fromLocalFile(path))),
                                    ("重新生成", lambda checked=False, title=row["title"],
                                     ids=json.loads(row["category_ids"]): self._generate_book(title, ids))):
                action = button(label, "Quiet")
                action.clicked.connect(callback)
                tools_layout.addWidget(action)
            self.book_table.setCellWidget(index, 6, tools)

    def _delete_book_record(self):
        row = self.book_table.currentRow()
        if row < 0:
            return
        book_id = self.book_table.item(row, 0).data(Qt.UserRole)
        if QMessageBox.question(self, "删除记录", "只删除数据库中的生成记录，保留磁盘文件。继续？",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.database.execute("DELETE FROM books WHERE id=?", (book_id,))
            self._load_books()

    def _fill_settings(self):
        settings = self.settings
        self.base_url.setText(settings.base_url)
        self.permission_check.setChecked(settings.permission_confirmed)
        self.permission_note.setText(settings.permission_note)
        self.max_topics.setValue(settings.max_topics)
        self.replies.setCurrentIndex(0 if settings.all_replies else 1)
        self.recent_days.setValue(settings.recent_days)
        self.skip_unchanged.setChecked(settings.skip_unchanged)
        self.download_images.setChecked(settings.download_images)
        self.compress_images.setChecked(settings.compress_images)
        self.image_dimension.setValue(settings.image_max_dimension)
        self.image_quality.setValue(settings.image_quality)
        self.image_max_mb.setValue(settings.image_max_mb)
        self.interval.setValue(settings.interval)
        self.jitter.setValue(settings.jitter)
        self.retries.setValue(settings.retries)
        self.timeout.setValue(settings.timeout)
        self.epubcheck.setText(settings.epubcheck_jar)
        self.download_dir.setText(settings.download_dir)
        self.output_dir.setText(settings.output_dir)

    def _settings_from_form(self):
        return replace(
            self.settings,
            base_url=self.base_url.text().strip(),
            permission_confirmed=self.permission_check.isChecked(),
            permission_note=self.permission_note.text().strip(),
            max_topics=self.max_topics.value(), all_replies=self.replies.currentIndex() == 0,
            recent_days=self.recent_days.value(), skip_unchanged=self.skip_unchanged.isChecked(),
            download_images=self.download_images.isChecked(), compress_images=self.compress_images.isChecked(),
            image_max_dimension=self.image_dimension.value(), image_quality=self.image_quality.value(),
            image_max_mb=self.image_max_mb.value(), interval=self.interval.value(), jitter=self.jitter.value(),
            retries=self.retries.value(), timeout=self.timeout.value(), epubcheck_jar=self.epubcheck.text().strip(),
            download_dir=self.download_dir.text().strip(), output_dir=self.output_dir.text().strip())

    def _save_settings(self, silent=False):
        try:
            settings = self._settings_from_form()
            settings.validate()
            settings.save(self.home)
            if settings.base_url.rstrip("/") != self.settings.base_url.rstrip("/"):
                self.connection_badge.setText("站点已更改，尚未测试")
            self.settings = settings
            self.scheduler.configure(settings)
            self._sync_permission_badges()
            if not silent:
                self.log("设置已保存；新的请求节奏从下一次请求开始生效。")
            return True
        except Exception as error:
            QMessageBox.warning(self, "设置无法保存", str(error))
            return False

    def _sync_permission_badges(self):
        confirmed = (self.settings.permission_confirmed and bool(self.settings.permission_note.strip())
                     and bool(self.settings.base_url.strip()))
        self.site_badge.setObjectName("BadgeOk" if confirmed else "BadgeWarn")
        if confirmed:
            self.site_badge.setText("站点与授权已配置")
        elif not self.settings.base_url.strip():
            self.site_badge.setText("未配置站点 · 不联网")
        else:
            self.site_badge.setText("未确认授权 · 不联网")
        self.site_badge.style().unpolish(self.site_badge)
        self.site_badge.style().polish(self.site_badge)

    def _choose_download_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择图片缓存目录", self.download_dir.text())
        if path:
            self.download_dir.setText(path)

    def _choose_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择 EPUB 输出目录", self.output_dir.text())
        if path:
            self.output_dir.setText(path)

    def _choose_epubcheck(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 epubcheck.jar", str(self.home),
                                              "EPUBCheck (epubcheck.jar)")
        if path:
            self.epubcheck.setText(path)

    def _unlock_403(self):
        if QMessageBox.question(
                self, "人工检查确认",
                "只有在已确认站方许可、访问限制和请求频率都合规时才解除。\n\n"
                "软件不会更换 IP 或账号绕过限制。是否已完成人工检查？",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.scheduler.resume(reviewed_403=True)
            self.log("403 锁定已由用户人工解除。")
        except AccessDenied as error:
            QMessageBox.warning(self, "无法恢复", str(error))

    def _run_job(self, function, label, then=None, on_result=None):
        if not self._idle():
            QMessageBox.information(self, "已有任务", "请等待当前后台任务结束，或先取消采集。")
            return
        thread = JobThread(function, self)
        self.active_thread = thread
        thread.log.connect(self.log)
        thread.completed.connect(lambda ok, message: self._job_done(ok, message, label, then, on_result))
        thread.start()
        self.log(f"开始：{label}")

    def _job_done(self, ok, message, label, then, on_result=None):
        if ok:
            self.log(f"{label}完成：{message}")
        else:
            self.log(f"{label}失败：{message}")
        if on_result:
            on_result(ok, message)
        if then:
            try:
                then()
            except Exception as error:
                self.log(f"界面刷新失败：{error}")
        if self.active_thread:
            self.active_thread.deleteLater()
            self.active_thread = None
        self._tick()

    def _idle(self):
        return self.active_thread is None or not self.active_thread.isRunning()

    def _tick(self):
        metrics = self.scheduler.metrics()
        self.metric_labels["当前速度"].setText(f'{metrics["requests_per_minute"]:.2f} 次/分')
        self.metric_labels["最近 5 分钟"].setText(f'{metrics["recent_requests"]} 次')
        self.metric_labels["HTTP 429"].setText(str(metrics["count_429"]))
        self.metric_labels["HTTP 403"].setText(str(metrics["count_403"]))
        self.metric_labels["请求失败"].setText(str(metrics["failures"]))
        remaining = int(metrics["cooldown_remaining"] + 0.999)
        self.metric_labels["冷却剩余"].setText(f"{remaining} 秒")
        self.metric_labels["全局间隔"].setText(f'{metrics["interval"]:.1f} 秒')
        pending = 0
        if self.active_session:
            pending = self.database.task_counts(self.active_session).get("remaining", 0)
        self.metric_labels["待处理主题"].setText(str(pending))
        if metrics["forbidden"]:
            self.cooldown_note.setText("HTTP 403 全局锁定。采集已停止，必须人工检查后明确解除。")
            self.cooldown_note.setStyleSheet("color:#d6a297")
        elif remaining:
            self.cooldown_note.setText(f"服务器保护冷却中：剩余 {remaining} 秒。所有模块同步等待。")
            self.cooldown_note.setStyleSheet("color:#d6b474")
        elif metrics["paused"]:
            self.cooldown_note.setText("请求队列已由用户暂停。已采集数据保存在 SQLite 中。")
            self.cooldown_note.setStyleSheet("color:#d6b474")
        else:
            self.cooldown_note.setText("调度器就绪。正文优先，图片排队；同一图片 URL 只请求一次。")
            self.cooldown_note.setStyleSheet("")
        if self.active_session:
            session = self.database.one("SELECT status,current_item,elapsed_seconds FROM collection_sessions WHERE id=?",
                                        (self.active_session,))
            if session:
                counts = self.database.task_counts(self.active_session)
                total = counts.get("total", 0)
                done = counts.get("complete", 0) + counts.get("skipped", 0)
                self.ov_current.setText(session["current_item"] or f"任务状态：{session['status']}")
                self.ov_progress.setValue(round(done * 100 / total) if total else 0)
                self.ov_hint.setText(f"主题 {done}/{total} · 失败 {counts.get('failed', 0)} · "
                                     f"图片 {counts['images']['complete']}/{counts['images']['total']} · "
                                     f"耗时 {self._duration(session['elapsed_seconds'])}")
                self._session_selected()
        if self.current_page == 0:
            self.card_categories.set(str(self.database.one("SELECT COUNT(*) AS n FROM categories")["n"]),
                                     "本地已发现")
            self.card_topics.set(str(self.database.one(
                "SELECT COUNT(*) AS n FROM topics WHERE collection_status='complete'")["n"]), "已完整采集")
            self.card_images.set(str(self.database.one(
                "SELECT COUNT(*) AS n FROM images WHERE download_status='complete'")["n"]), "离线可用")
            self.card_books.set(str(self.database.one("SELECT COUNT(*) AS n FROM books")["n"]), "通过校验")

    @staticmethod
    def _duration(seconds):
        seconds = int(seconds or 0)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _size(value):
        value = float(value or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}"
            value /= 1024

    def closeEvent(self, event):
        if self.active_thread and self.active_thread.isRunning():
            if QMessageBox.question(self, "任务运行中", "后台任务仍在运行。取消任务并等待保存进度后退出？",
                                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                event.ignore()
                return
            if self.active_control:
                self.active_control.cancel()
            if not self.active_thread.wait(5000):
                QMessageBox.warning(self, "仍在保存", "当前网络请求尚未结束。请稍后再次关闭，避免丢失进度。")
                event.ignore()
                return
        event.accept()
