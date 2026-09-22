"""GUI 深色主题。

Qt 的 QSS 里 ``{}`` 是选择器语法，用 f-string 会满屏转义。这里改用
``@name@`` 占位符，最后统一替换，写起来干净也不容易出错。
"""

from __future__ import annotations

PALETTE: dict[str, str] = {
    "bg": "#1b1c1f",
    "surface": "#232529",
    "surface2": "#2b2e33",
    "surface3": "#34383e",
    "border": "#3c4046",
    "border2": "#4a4f56",
    "text": "#e8e9eb",
    "muted": "#9ba1a8",
    "accent": "#6f8dff",
    "accentHover": "#86a0ff",
    "accentDim": "#39436b",
    "success": "#4dd98a",
    "warning": "#ffb84d",
    "danger": "#ff6b6b",
    "powerful": "#FF4D4D",
    "cool": "#4D9BFF",
    "pure": "#4DD98A",
    "happy": "#FFB84D",
}

_TEMPLATE = """
QWidget {
    background: @bg@;
    color: @text@;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}

QMainWindow, QDialog { background: @bg@; }

/* ---- 通用容器 ---- */
QScrollArea#PanelScroll { background: @surface@; border: none; }
QScrollArea#PanelScroll > QWidget > QWidget { background: transparent; }
QFrame#Card, QFrame#Panel {
    background: @surface@;
    border: 1px solid @border@;
    border-radius: 10px;
}
/* 内容卡片：现代 App 里"把一组相关控件装进一张卡"是基本手法 */
QFrame#Card {
    background: @surface@;
    border: 1px solid @border@;
    border-radius: 12px;
}
QToolBar QToolButton {
    padding: 5px 10px;
    border-radius: 6px;
    color: @muted@;
}
QToolBar QToolButton:hover { background: @surface2@; color: @text@; }
QToolBar QToolButton:pressed { background: @surface3@; }

QLabel#Title { font-size: 19px; font-weight: 600; }
QLabel#Subtitle { color: @muted@; font-size: 12px; }
QLabel#SectionTitle { font-size: 14px; font-weight: 600; color: @text@; }
QLabel#Hint { color: @muted@; font-size: 12px; }
QLineEdit#CardUrl { color: @muted@; font-size: 12px; padding: 3px 6px; background: @surface2@;
                    border: 1px solid @border@; border-radius: 4px; }
QLabel#StatValue { font-size: 24px; font-weight: 700; }
QLabel#StatLabel { color: @muted@; font-size: 12px; }

/* ---- 左侧导航 ---- */
/* ===== 左侧窄导航（图标在上、文字在下）===== */
QFrame#NavRail {
    background: @surface@;
    border: none;
    border-right: 1px solid @border@;
}
QPushButton#RailItem {
    border: none;
    border-radius: 10px;
    padding: 4px 2px 3px 2px;
    color: @muted@;
    font-size: 11px;
    text-align: center;
}
QPushButton#RailItem:hover { background: @surface2@; color: @text@; }
QPushButton#RailItem:checked {
    background: @accentDim@;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#RailItem:focus { outline: none; }

/* ---- 按钮 ---- */
QPushButton {
    background: @surface2@;
    border: 1px solid @border@;
    border-radius: 7px;
    padding: 7px 14px;
    color: @text@;
}
QPushButton:hover:enabled { background: @surface3@; border-color: @border2@; }
QPushButton:pressed:enabled { background: @border@; }
QPushButton:disabled { color: #66696e; background: @surface@; border-color: @border@; }
QPushButton#Primary {
    background: @accent@;
    border: 1px solid @accent@;
    color: #10131f;
    font-weight: 600;
}
QPushButton#Primary:hover:enabled { background: @accentHover@; border-color: @accentHover@; }
QPushButton#Primary:disabled { background: @accentDim@; border-color: @accentDim@; color: #8f97b5; }
QPushButton#Danger { color: @danger@; border-color: #5a3336; }
QPushButton#Danger:hover:enabled { background: #3a2427; }
QPushButton#Ghost { background: transparent; border: 1px solid @border@; }
/* 「候选都不对」的出口：虚线边框，一眼看出是兜底路径而不是主操作 */
QPushButton#Dashed {
    background: transparent;
    border: 1px dashed @border2@;
    color: @muted@;
}
QPushButton#Dashed:hover:enabled { border-color: @accent@; color: @accent@; }
/* 行内文字按钮：用来把「出口」塞进标题行，不额外占高度。
   Qt 的 QSS 对 text-decoration 支持不可靠，用颜色变化表达可点。 */
QPushButton#LinkBtn {
    background: transparent;
    border: none;
    padding: 0;
    color: @accent@;
}
QPushButton#LinkBtn:hover:enabled { color: @accentHover@; }

/* ---- 输入控件 ---- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: @surface2@;
    border: 1px solid @border@;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: @accent@;
    selection-color: #10131f;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {
    border-color: @accent@;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: @surface2@;
    border: 1px solid @border2@;
    selection-background-color: @accentDim@;
    outline: none;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background: @surface3@; border: none; width: 15px;
}
QCheckBox { spacing: 7px; }
QCheckBox::indicator {
    width: 15px; height: 15px;
    border: 1px solid @border2@; border-radius: 4px; background: @surface2@;
}
QCheckBox::indicator:checked { background: @accent@; border-color: @accent@; }
QCheckBox::indicator:hover { border-color: @accent@; }

/* ---- 表格 ---- */
QTableWidget, QTableView {
    background: @surface@;
    alternate-background-color: #262a2f;
    border: 1px solid @border@;
    border-radius: 8px;
    gridline-color: #31353b;
    outline: none;
    selection-background-color: @accentDim@;
    selection-color: #ffffff;
}
QTableWidget::item { padding: 4px 6px; border: none; }
QHeaderView::section {
    background: @surface2@;
    color: @muted@;
    border: none;
    border-bottom: 1px solid @border@;
    border-right: 1px solid @border@;
    padding: 7px 6px;
    font-weight: 600;
}
QTableCornerButton::section { background: @surface2@; border: none; }

/* ---- 列表 ---- */
QListWidget, QListView {
    background: @surface@;
    border: 1px solid @border@;
    border-radius: 8px;
    outline: none;
}
QListWidget::item { padding: 5px 7px; border-radius: 6px; }
QListWidget::item:hover { background: @surface2@; }
QListWidget::item:selected { background: @accentDim@; color: #ffffff; }

/* ---- 滚动条 ---- */
QScrollBar:vertical { background: transparent; width: 11px; margin: 2px; }
QScrollBar::handle:vertical { background: @border2@; border-radius: 5px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #5c626a; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 2px; }
QScrollBar::handle:horizontal { background: @border2@; border-radius: 5px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---- 进度条 ---- */
QProgressBar {
    background: @surface2@; border: none; border-radius: 5px;
    height: 8px; text-align: center; color: transparent;
}
QProgressBar::chunk { background: @accent@; border-radius: 5px; }

/* ---- 分隔条 / 状态栏 / 分组 ---- */
QSplitter::handle { background: @border@; }
QSplitter::handle:horizontal { width: 3px; }
QSplitter::handle:vertical { height: 3px; }
QStatusBar { background: @surface@; border-top: 1px solid @border@; color: @muted@; }
QStatusBar::item { border: none; }
QGroupBox {
    border: 1px solid @border@; border-radius: 9px;
    margin-top: 14px; padding-top: 10px; background: @surface@;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 12px; padding: 0 5px;
    color: @muted@; font-weight: 600;
}
QToolBar { background: @surface@; border-bottom: 1px solid @border@; spacing: 6px; padding: 5px; }
QToolBar QLabel { color: @muted@; }
QMenu { background: @surface2@; border: 1px solid @border2@; }
QMenu::item:selected { background: @accentDim@; }
QTabWidget::pane { border: 1px solid @border@; border-radius: 8px; }
QTabBar::tab {
    background: @surface@; color: @muted@;
    padding: 7px 15px; border: 1px solid @border@; border-bottom: none;
    border-top-left-radius: 7px; border-top-right-radius: 7px;
}
QTabBar::tab:selected { background: @surface2@; color: @text@; }
QToolTip {
    background: @surface3@; color: @text@;
    border: 1px solid @border2@; border-radius: 5px; padding: 5px;
}
"""


def qss() -> str:
    """返回替换好颜色变量的样式表。"""
    out = _TEMPLATE
    for key, value in PALETTE.items():
        out = out.replace(f"@{key}@", value)
    return out


def apply_theme(app) -> None:
    """给 QApplication 套上深色主题。

    强制 Fusion 风格：Windows 原生风格会忽略大部分 QSS，导致深浅混杂。
    """
    from PySide6.QtGui import QColor, QPalette

    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(PALETTE["bg"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(PALETTE["text"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(PALETTE["surface"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(PALETTE["surface2"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(PALETTE["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(PALETTE["surface2"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(PALETTE["text"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(PALETTE["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#10131f"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(PALETTE["surface3"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(PALETTE["text"]))
    app.setPalette(pal)
    app.setStyleSheet(qss())
