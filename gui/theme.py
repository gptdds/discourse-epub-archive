STYLE = """
* { font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; }
QMainWindow, QWidget#Root { background: #0f1512; color: #e8ede9; }
QWidget { color: #e8ede9; }
QToolTip { background: #24332c; color: #eef1e9; border: 1px solid #3c5045; padding: 5px; }
QFrame#Sidebar { background: #0b110e; border-right: 1px solid #223027; }
QLabel#Brand { color: #f3eee0; font-family: "Georgia", "Microsoft YaHei UI"; font-size: 23px;
  font-weight: 700; letter-spacing: 1px; }
QLabel#Eyebrow { color: #a89161; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
QPushButton#Nav { background: transparent; border: none; border-radius: 7px; color: #91a097;
  padding: 11px 14px; text-align: left; font-size: 14px; }
QPushButton#Nav:hover { background: #16201b; color: #eef2eb; }
QPushButton#Nav:checked { background: #1c2a22; color: #f3eee0; border-left: 2px solid #b49a67;
  padding-left: 12px; }
QLabel#PageTitle { font-size: 26px; font-weight: 700; color: #f2f0e7; }
QLabel#PageSub { color: #89988f; font-size: 12px; }
QLabel#SectionTitle { font-size: 16px; font-weight: 700; color: #e8e4d8; }
QLabel#Muted { color: #89988f; }
QLabel#Metric { font-size: 27px; font-weight: 700; color: #f1ede2; }
QLabel#MetricSmall { font-size: 15px; font-weight: 600; color: #dde4dc; }
QFrame#Card, QFrame#Panel { background: #151e19; border: 1px solid #26352c; border-radius: 10px; }
QFrame#Hero { background: #16231c; border: 1px solid #334436; border-radius: 12px; }
QFrame#Divider { background: #26352c; max-height: 1px; }
QLabel#Badge { background: #242d24; color: #c1aa78; border: 1px solid #4b543f;
  border-radius: 9px; padding: 3px 9px; font-size: 11px; }
QLabel#BadgeOk { background: #1c3227; color: #94c5a1; border: 1px solid #355b43;
  border-radius: 9px; padding: 3px 9px; font-size: 11px; }
QLabel#BadgeWarn { background: #342b1c; color: #d6b474; border: 1px solid #66502e;
  border-radius: 9px; padding: 3px 9px; font-size: 11px; }
QPushButton { background: #24332a; color: #e9eee8; border: 1px solid #394d40;
  border-radius: 7px; padding: 8px 15px; }
QPushButton:hover { background: #2c3f34; border-color: #536b5a; }
QPushButton:pressed { background: #1b271f; }
QPushButton:disabled { color: #59665e; background: #18211c; border-color: #25322a; }
QPushButton#Primary { background: #c2aa78; color: #182017; border: 1px solid #d4be8e;
  font-weight: 700; }
QPushButton#Primary:hover { background: #d2bc8a; }
QPushButton#Danger { background: #2d211f; color: #d6a297; border: 1px solid #5a3732; }
QPushButton#Quiet { background: transparent; border: 1px solid #304137; color: #a9b6ac; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #101713; color: #e7ece5;
  border: 1px solid #314138; border-radius: 6px; padding: 7px 9px; selection-background-color: #4e6244; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #93805b; }
QComboBox QAbstractItemView { background: #18221c; border: 1px solid #3b4c40; selection-background-color: #2e4034; }
QCheckBox { spacing: 8px; color: #cbd5cd; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #536359;
  border-radius: 4px; background: #101713; }
QCheckBox::indicator:checked { background: #b8a06f; border-color: #d0b984; }
QTableWidget, QListWidget { background: #111814; alternate-background-color: #141d18;
  border: 1px solid #26352c; border-radius: 8px; gridline-color: transparent;
  selection-background-color: #26392d; selection-color: #f5f2e9; }
QHeaderView::section { background: #18221c; color: #8f9e94; border: none;
  border-bottom: 1px solid #2d3d32; padding: 9px 8px; font-size: 11px; font-weight: 700; }
QTableWidget::item { padding: 8px; border-bottom: 1px solid #1c2821; }
QProgressBar { background: #101713; border: 1px solid #2c3c32; border-radius: 5px;
  text-align: center; color: #dedbd0; height: 13px; font-size: 10px; }
QProgressBar::chunk { background: #a89466; border-radius: 4px; }
QTextEdit { background: #0e1511; color: #c7d0c7; border: 1px solid #26352c;
  border-radius: 8px; padding: 10px; font-family: "Cascadia Mono", "Consolas"; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #34453a; border-radius: 5px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal { background: #34453a; border-radius: 5px; min-width: 28px; }
QFrame#SettingGroup { background: #151e19; border: 1px solid #26352c; border-radius: 9px; }
QSplitter::handle { background: #26352c; }
"""
