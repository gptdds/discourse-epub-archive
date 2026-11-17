from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)


class SettingsWheelFilter(QObject):
    def __init__(self, scroll):
        super().__init__(scroll)
        self.scroll = scroll

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel:
            viewport = self.scroll.viewport()
            forwarded = QWheelEvent(
                QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                event.source(), event.pointingDevice())
            QApplication.sendEvent(viewport, forwarded)
            event.accept()
            return True
        return super().eventFilter(watched, event)


class Card(QFrame):
    def __init__(self, label, value="—", hint="", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(3)
        self.label = QLabel(label.upper())
        self.label.setObjectName("Eyebrow")
        self.value = QLabel(value)
        self.value.setObjectName("Metric")
        self.hint = QLabel(hint)
        self.hint.setObjectName("Muted")
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def set(self, value, hint=None):
        self.value.setText(str(value))
        if hint is not None:
            self.hint.setText(hint)


def page_header(title, subtitle, actions=()):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 8)
    text = QVBoxLayout()
    text.setSpacing(2)
    heading = QLabel(title)
    heading.setObjectName("PageTitle")
    description = QLabel(subtitle)
    description.setObjectName("PageSub")
    text.addWidget(heading)
    text.addWidget(description)
    layout.addLayout(text)
    layout.addStretch()
    for action in actions:
        layout.addWidget(action)
    return widget


def button(text, kind="", tooltip=""):
    widget = QPushButton(text)
    if kind:
        widget.setObjectName(kind)
    if tooltip:
        widget.setToolTip(tooltip)
    widget.setCursor(Qt.PointingHandCursor)
    return widget


def divider():
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.HLine)
    return line


def labeled(label, field):
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    caption = QLabel(label)
    caption.setObjectName("Muted")
    layout.addWidget(caption)
    layout.addWidget(field)
    return widget


def metric_row(items):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    for label, value in items:
        card = Card(label, value)
        layout.addWidget(card, 1)
    return widget


def progress_row(label, bar, value_label):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    caption = QLabel(label)
    caption.setObjectName("Muted")
    caption.setFixedWidth(105)
    layout.addWidget(caption)
    layout.addWidget(bar, 1)
    value_label.setFixedWidth(90)
    value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    layout.addWidget(value_label)
    return widget


class Section(QFrame):
    def __init__(self, title, description="", parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        self.layout.addWidget(heading)
        if description:
            note = QLabel(description)
            note.setObjectName("Muted")
            note.setWordWrap(True)
            self.layout.addWidget(note)

    def add(self, widget, stretch=0):
        self.layout.addWidget(widget, stretch)
