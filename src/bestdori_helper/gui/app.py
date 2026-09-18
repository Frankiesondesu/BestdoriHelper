"""桌面 GUI 主窗口。

四个功能区，对应「截图 → 识别 → 清单 → 同步」的实际使用顺序：

    ① 导入截图   拖入截图、选切分模式、跑批量识别
    ② 识别结果   逐格核对、人工修正错识别、再并入清单
    ③ 卡面清单   统计、筛选、确认、导出
    ④ 同步       同步卡池资料 / 构建指纹库 / 生成导入计划 / 浏览器自动化写入

界面本身不实现任何业务逻辑，全部调用 ``vision`` / ``inventory`` / ``bridge``。
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..bestdori.client import BestdoriClient
from ..config import ATTRIBUTE_COLOR, LANGUAGES, SOURCE_LABEL, Server, Settings
from ..inventory.export import to_bestdori_ids, to_csv, to_json, to_markdown
from ..inventory.store import Inventory
from ..models import Catalog, OwnedCard
from ..vision.index import FingerprintIndex
from ..vision.recognize import RecognitionResult, RecognizedItem
from .theme import PALETTE, apply_theme
from .widgets import (
    THUMB_SIZE,
    AnnotatedScreenshotView,
    CardPickerDialog,
    ClickableLabel,
    DropZone,
    ImageZoomDialog,
    NavRail,
    StatCard,
    ThumbnailLoader,
    hline,
    status_color,
    status_label,
)
from .workers import (
    Worker,
    iter_images,
    task_api_import,
    task_api_login,
    task_build_index,
    task_import_plan,
    task_scan,
    task_sync,
)

log = logging.getLogger(__name__)

SERVER_CN = {"jp": "日服", "cn": "国服", "tw": "台服", "en": "国际服", "kr": "韩服"}

#: 左侧窄导航：(图标, 短标签, 悬停提示)。短标签要能塞进 84px 宽的栏里，
#: 所以不用"① 导入截图"这种带序号的写法 —— 序号在窄栏里既占地方又没意义。
NAV_ITEMS = [
    ("📥", "导入", "把游戏截图拖进来，批量识别"),
    ("🔍", "结果", "核对每一格，修正错识别"),
    ("🗂", "清单", "统计、筛选、导出"),
    ("☁", "同步", "指纹库与卡册写入"),
]


# ---------------------------------------------------------------------
# 进程内状态
# ---------------------------------------------------------------------


@dataclass
class GuiState:
    """GUI 持有的共享状态（对应 Web 版的 AppState）。"""

    settings: Settings
    client: BestdoriClient | None = None
    catalog: Catalog | None = None
    index: FingerprintIndex | None = None
    inventory: Inventory | None = None
    scan_results: list[RecognitionResult] = field(default_factory=list)
    plan: object | None = None

    def client_or_new(self) -> BestdoriClient:
        if self.client is None:
            self.client = BestdoriClient(self.settings)
        return self.client

    def catalog_or_load(self, *, allow_network: bool = True) -> Catalog | None:
        """加载卡池资料。

        ``allow_network=False`` 时只读本地缓存 —— 启动阶段绝不能卡在网络上，
        否则窗口会白屏几秒。
        """
        if self.catalog is not None:
            return self.catalog
        if self.settings.cards_json.exists():
            self.catalog = self.client_or_new().fetch_catalog(refresh=False)
        elif allow_network:
            self.catalog = self.client_or_new().fetch_catalog(refresh=True)
        return self.catalog

    def index_or_load(self) -> FingerprintIndex | None:
        if self.index is None:
            self.index = FingerprintIndex.load(self.settings.index_path)
        return self.index

    def inventory_or_load(self) -> Inventory:
        if self.inventory is None:
            self.inventory = Inventory.load(self.settings.inventory_path)
        return self.inventory

    def reset(self, settings: Settings) -> None:
        """切换服务器 / 数据目录后清空缓存。"""
        if self.client is not None:
            self.client.close()
        self.settings = settings
        self.client = None
        self.catalog = None
        self.index = None
        self.inventory = None
        self.scan_results = []
        self.plan = None


# ---------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.state = GuiState(settings=settings)
        self.thumb = ThumbnailLoader(settings, self)
        self.thumb.loaded.connect(self._on_thumb_loaded)

        self._worker: Worker | None = None
        self._action_buttons: list[QPushButton] = []
        self._thumb_watchers: dict[str, dict[str, list[QTableWidgetItem]]] = {}
        self._placeholder: QPixmap | None = None

        # ① 页
        self.pending_paths: list[str] = []
        # ② 页：表格行 -> (结果下标, 条目下标)
        self._scan_rows: list[tuple[int, int]] = []
        # ② 页：原图框选视图当前显示的是第几张截图（-1 = 无）
        self._anno_result_index = -1
        #: 当前核对面板对应的截图整图（用来裁出游戏格）
        self._cmp_source: QPixmap | None = None
        #: 核对两图的**原始分辨率**版本（点击放大时显示的是它，不是缩略图）
        self._cmp_cell_raw: QPixmap | None = None
        self._cmp_card_raw: QPixmap | None = None
        #: Bestdori 后台 API 会话（登录一次，之后纯 HTTP 同步，不弹浏览器）
        self._account = None
        #: 正在等哪张卡图下载完（下载完要回来刷新核对面板）
        self._detail_waiting = ""

        self.setWindowTitle("BestdoriHelper — 卡面识别与卡册同步")
        self.resize(1400, 900)
        self.setMinimumSize(1080, 720)

        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self._refresh_state()
        self._log("就绪。数据目录：" + str(self.state.settings.home))
        self._log("卡图来源：" + SOURCE_LABEL.get(self.state.settings.image_source,
                                              self.state.settings.image_source))

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("主工具栏")
        tb.setMovable(False)

        tb.addWidget(QLabel("  服务器  "))
        self.server_combo = QComboBox()
        for s in Server:
            self.server_combo.addItem(SERVER_CN.get(s.value, s.value), s.value)
        self.server_combo.setCurrentIndex(
            max(0, [s.value for s in Server].index(self.state.settings.server.value))
        )
        self.server_combo.setFixedWidth(96)
        self.server_combo.currentIndexChanged.connect(self._on_server_changed)
        tb.addWidget(self.server_combo)

        tb.addWidget(QLabel("  语言  "))
        self.lang_combo = QComboBox()
        for idx, name in LANGUAGES.items():
            self.lang_combo.addItem(name, idx)
        self.lang_combo.setCurrentIndex(max(0, self.state.settings.lang or 0))
        self.lang_combo.setFixedWidth(112)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        tb.addWidget(self.lang_combo)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        tb.addSeparator()

        act_home = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon),
            "打开数据目录", self)
        act_home.triggered.connect(self._open_home)
        tb.addAction(act_home)

        act_refresh = QAction("刷新状态", self)
        act_refresh.triggered.connect(self._refresh_state)
        tb.addAction(act_refresh)

        act_about = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation),
            "关于", self)
        act_about.triggered.connect(self._show_about)
        tb.addAction(act_about)

    def _build_body(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)

        # 窄导航栏（图标竖排）—— 现代 App 的常见形态。
        # NavRail 的接口对齐 QListWidget 子集，所以下面的调用与旧代码一致。
        self.nav = NavRail([(icon, label) for icon, label, _tip in NAV_ITEMS])
        for i, (_icon, _label, tip) in enumerate(NAV_ITEMS):
            self.nav.setItemToolTip(i, tip)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(lambda i: self.stack.setCurrentIndex(i))
        top.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_import_page())
        self.stack.addWidget(self._build_results_page())
        self.stack.addWidget(self._build_inventory_page())
        self.stack.addWidget(self._build_sync_page())
        top.addWidget(self.stack, 1)

        outer.addLayout(top, 1)
        outer.addWidget(self._build_log_panel())

        self.setCentralWidget(central)

    def _build_log_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 4, 10, 6)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("运行日志")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.log_toggle = QPushButton("收起")
        self.log_toggle.setObjectName("Ghost")
        self.log_toggle.setFixedWidth(64)
        self.log_toggle.clicked.connect(self._toggle_log)
        head.addWidget(self.log_toggle)
        clear = QPushButton("清空")
        clear.setObjectName("Ghost")
        clear.setFixedWidth(64)
        clear.clicked.connect(lambda: self.log_view.clear())
        head.addWidget(clear)
        lay.addLayout(head)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setFixedHeight(132)
        self.log_view.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 12px;")
        lay.addWidget(self.log_view)
        return panel

    def _build_statusbar(self) -> None:
        bar = self.statusBar()
        self.status_label = QLabel("就绪")
        bar.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(170)
        self.progress.hide()
        bar.addPermanentWidget(self.progress)

    # ---- ① 导入截图 ---------------------------------------------------

    def _build_import_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 10)
        lay.setSpacing(10)

        head = QLabel("导入截图")
        head.setObjectName("Title")
        lay.addWidget(head)
        sub = QLabel(
            "在游戏里把卡册/编队界面截成图（一张图里有多少张卡都行），拖进来批量识别。"
        )
        sub.setObjectName("Subtitle")
        lay.addWidget(sub)

        self.drop = DropZone()
        self.drop.filesDropped.connect(self._add_paths)
        lay.addWidget(self.drop)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        b_files = QPushButton("选择图片…")
        b_files.clicked.connect(self._pick_files)
        b_dir = QPushButton("选择文件夹…")
        b_dir.clicked.connect(self._pick_dir)
        b_del = QPushButton("移除选中")
        b_del.clicked.connect(self._remove_selected_paths)
        b_clear = QPushButton("清空列表")
        b_clear.clicked.connect(self._clear_paths)
        self.scan_btn = QPushButton("开始识别  ▶")
        self.scan_btn.setObjectName("Primary")
        self.scan_btn.setMinimumWidth(150)
        self.scan_btn.clicked.connect(self._do_scan)
        for b in (b_files, b_dir, b_del, b_clear):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.scan_btn)
        lay.addLayout(btns)
        self._action_buttons.append(self.scan_btn)

        self.file_list = QListWidget()
        self.file_list.setIconSize(QSize(64, 48))
        self.file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        lay.addWidget(self.file_list, 1)

        self.file_count_label = QLabel("待处理：0 张")
        self.file_count_label.setObjectName("Hint")
        lay.addWidget(self.file_count_label)

        lay.addWidget(hline())
        # 选项区装进一张卡片 —— 裸放在空白上会显得散、也不像成品软件
        opt_card = QFrame()
        opt_card.setObjectName("Card")
        opts = QGridLayout(opt_card)
        opts.setContentsMargins(14, 12, 14, 12)
        opts.setHorizontalSpacing(12)
        opts.setVerticalSpacing(9)

        opts.addWidget(QLabel("切分模式"), 0, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("自动识别网格（推荐）", "auto")
        self.mode_combo.addItem("按行列等分", "grid")
        self.mode_combo.addItem("整图当一张", "single")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        opts.addWidget(self.mode_combo, 0, 1, 1, 2)

        opts.addWidget(QLabel("行"), 0, 3)
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(0, 30)
        self.rows_spin.setSpecialValueText("自动")
        self.rows_spin.setEnabled(False)
        opts.addWidget(self.rows_spin, 0, 4)

        opts.addWidget(QLabel("列"), 0, 5)
        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(0, 30)
        self.cols_spin.setSpecialValueText("自动")
        self.cols_spin.setEnabled(False)
        opts.addWidget(self.cols_spin, 0, 6)

        opts.addWidget(QLabel("内缩"), 0, 7)
        self.inset_spin = QDoubleSpinBox()
        self.inset_spin.setRange(0.0, 0.30)
        self.inset_spin.setSingleStep(0.01)
        self.inset_spin.setDecimals(2)
        self.inset_spin.setValue(0.0)
        self.inset_spin.setToolTip("每个格子向内收缩的比例，用来切掉卡面边框")
        opts.addWidget(self.inset_spin, 0, 8)

        self.attr_hint_cb = QCheckBox("属性边框提示")
        self.attr_hint_cb.setChecked(True)
        self.attr_hint_cb.setToolTip("用卡面边框颜色（红/橙/绿/蓝）先缩小候选范围，能提高准确率")
        opts.addWidget(self.attr_hint_cb, 1, 0, 1, 2)

        self.ocr_cb = QCheckBox("OCR 文字辅助")
        self.ocr_cb.setToolTip("需要额外安装 rapidocr-onnxruntime；不装也能用")
        opts.addWidget(self.ocr_cb, 1, 2, 1, 2)

        self.absorb_cb = QCheckBox("识别后自动写入清单")
        self.absorb_cb.setChecked(True)
        opts.addWidget(self.absorb_cb, 1, 4, 1, 2)

        self.amb_cb = QCheckBox("允许「待确认」条目入清单")
        self.amb_cb.setChecked(True)
        self.amb_cb.setToolTip("不勾选则只把确定匹配的写入清单，有歧义的留在识别结果里等你手动点选")
        opts.addWidget(self.amb_cb, 1, 6, 1, 3)

        opts.addWidget(QLabel("最低置信度"), 2, 0)
        self.min_conf_spin = QDoubleSpinBox()
        self.min_conf_spin.setRange(0.0, 1.0)
        self.min_conf_spin.setSingleStep(0.01)
        self.min_conf_spin.setDecimals(2)
        opts.addWidget(self.min_conf_spin, 2, 1)
        hint = QLabel("低于该分数的结果不会写进清单（0 表示不限制）")
        hint.setObjectName("Hint")
        opts.addWidget(hint, 2, 2, 1, 7)

        lay.addWidget(opt_card)
        lay.addStretch(0)
        return page

    # ---- ② 识别结果 ---------------------------------------------------

    def _build_results_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 10)
        lay.setSpacing(10)

        head = QLabel("识别结果")
        head.setObjectName("Title")
        lay.addWidget(head)
        sub = QLabel(
            "先在原图上逐格核对：点框或点表格行都会选中同一格，右侧把「游戏里的卡面」"
            "和「Bestdori 卡图」并排显示，并给出卡面详情页网址。识别错了双击候选即可改过来。"
        )
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- 左：原图框选（上）+ 结果表格（下）----
        left = QSplitter(Qt.Orientation.Vertical)

        anno_box = QFrame()
        anno_box.setObjectName("Panel")
        abl = QVBoxLayout(anno_box)
        abl.setContentsMargins(10, 10, 10, 10)
        abl.setSpacing(6)
        anno_head = QHBoxLayout()
        anno_head.setSpacing(8)
        anno_cap = QLabel(
            "原图框选 —— 点框选中该格（绿=已匹配/橙=待确认/灰=未识别）；"
            "滚轮放大、按住拖动、双击复位"
        )
        anno_cap.setObjectName("Hint")
        anno_cap.setWordWrap(True)
        anno_head.addWidget(anno_cap, 1)
        # 一次识别多张截图时，结果表是所有截图的条目拼起来的 ——
        # 没有这个切换器，用户根本不知道左边显示的是哪一张、怎么换
        anno_head.addWidget(QLabel("当前截图"))
        self.shot_combo = QComboBox()
        self.shot_combo.setMinimumWidth(220)
        self.shot_combo.setToolTip("切换左边显示的截图（多张截图时才有意义）")
        self.shot_combo.currentIndexChanged.connect(self._on_shot_combo)
        anno_head.addWidget(self.shot_combo)
        abl.addLayout(anno_head)
        self.anno_view = AnnotatedScreenshotView()
        self.anno_view.indexClicked.connect(self._on_box_clicked)
        abl.addWidget(self.anno_view, 1)
        left.addWidget(anno_box)

        self.result_table = self._make_table(
            ["状态", "卡图", "卡牌", "角色", "星级", "属性", "置信度", "识别方式", "来源截图"]
        )
        self.result_table.itemSelectionChanged.connect(self._on_result_selected)
        left.addWidget(self.result_table)
        left.setStretchFactor(0, 1)
        left.setStretchFactor(1, 1)
        left.setSizes([320, 380])
        splitter.addWidget(left)

        # ---- 右：核对面板 ----
        # 面板控件不少（核对图 + 网址 + 候选 + 一堆按钮）。实测把这些塞进
        # 固定高度的 QFrame 时，窗口一矮嵌套布局就会把 140x140 的核对图撑出
        # 自己的区间，压到下面的文字上（量到过 11px 重叠）。套一层滚动容器，
        # 空间不够时改为滚动，布局就不会被挤压。
        side = QScrollArea()
        side.setObjectName("PanelScroll")
        side.setWidgetResizable(True)
        side.setFrameShape(QFrame.Shape.NoFrame)
        # 最小宽度必须容得下"两张 140px 核对图 + 间距 + 内边距 + 垂直滚动条"：
        # 140*2 + 8 + 24 + 11 = 323。早先写 322 —— 视口只剩 311px，而内容要
        # 320px，右边那张图被裁掉一条，且水平滚动条被禁用，用户看到的就是
        # "右边的图显示不全"。
        side.setMinimumWidth(380)
        side.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        side_inner = QWidget()
        sl = QVBoxLayout(side_inner)
        sl.setContentsMargins(12, 12, 12, 12)
        sl.setSpacing(8)

        self.sel_label = QLabel("未选中")
        self.sel_label.setObjectName("SectionTitle")
        self.sel_label.setWordWrap(True)
        sl.addWidget(self.sel_label)

        cmp_cap = QLabel(
            "核对：左 = 截图里的卡面，右 = Bestdori 卡图（点图片可放大，滚轮缩放）"
        )
        cmp_cap.setObjectName("Hint")
        cmp_cap.setWordWrap(True)
        sl.addWidget(cmp_cap)

        cmp_row = QHBoxLayout()
        cmp_row.setSpacing(8)
        self.cmp_cell = self._make_cmp_label()
        self.cmp_card = self._make_cmp_label()
        # 140px 的缩略图看不清属性图标和星级，点开看原图是核对的基本需求
        self.cmp_cell.clicked.connect(lambda: self._zoom_cmp("cell"))
        self.cmp_card.clicked.connect(lambda: self._zoom_cmp("card"))
        for lab in (self.cmp_cell, self.cmp_card):
            lab.setToolTip("点击放大查看")
        cmp_row.addWidget(self.cmp_cell)
        cmp_row.addWidget(self.cmp_card)
        cmp_row.addStretch(1)
        sl.addLayout(cmp_row)

        self.url_label = QLabel("卡面详情页（点链接或按钮打开核对）")
        self.url_label.setObjectName("Hint")
        self.url_label.setWordWrap(True)
        self.url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.url_label.setOpenExternalLinks(True)
        sl.addWidget(self.url_label)

        # 完整网址单独用只读输入框显示：QLabel 不会在斜杠处折行，
        # 窄面板里会被截断成 "https://bestdori.com/info/…"，那样就没法核对了。
        # QLineEdit 可以横向滚动 + 全选复制，保证网址一个字符都不少。
        self.url_edit = QLineEdit()
        self.url_edit.setReadOnly(True)
        self.url_edit.setObjectName("CardUrl")
        self.url_edit.setToolTip("选中后 Ctrl+C 可以复制，或点下面的按钮直接在浏览器打开")
        sl.addWidget(self.url_edit)

        self.b_url = QPushButton("打开 Bestdori 卡面页核对")
        self.b_url.clicked.connect(self._open_card_url)
        sl.addWidget(self.b_url)

        sl.addWidget(hline())

        cand_hint = QLabel("候选（按「是不是同一张画」排序）")
        cand_hint.setObjectName("Hint")
        sl.addWidget(cand_hint)

        self.cand_list = QListWidget()
        self.cand_list.setMinimumHeight(96)
        self.cand_list.itemDoubleClicked.connect(lambda _i: self._apply_candidate())
        sl.addWidget(self.cand_list, 1)

        b_apply = QPushButton("应用选中候选")
        b_apply.setObjectName("Primary")
        b_apply.clicked.connect(self._apply_candidate)
        sl.addWidget(b_apply)

        b_pick = QPushButton("手动指定卡牌…")
        b_pick.clicked.connect(self._pick_card_for_row)
        sl.addWidget(b_pick)

        b_ignore = QPushButton("忽略此项（从清单移除）")
        b_ignore.clicked.connect(self._ignore_row)
        sl.addWidget(b_ignore)

        sl.addWidget(hline())

        b_all = QPushButton("全部加入清单")
        b_all.clicked.connect(self._absorb_all)
        sl.addWidget(b_all)
        self._action_buttons.append(b_all)

        b_clear = QPushButton("清空识别结果")
        b_clear.clicked.connect(self._clear_results)
        sl.addWidget(b_clear)

        side.setWidget(side_inner)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([850, 400])
        lay.addWidget(splitter, 1)

        self.result_summary = QLabel("还没有识别结果。")
        self.result_summary.setObjectName("Hint")
        lay.addWidget(self.result_summary)
        return page

    @staticmethod
    def _make_cmp_label() -> ClickableLabel:
        """核对面板里并排的两张图（点击可放大）。"""
        lab = ClickableLabel()
        lab.setFixedSize(140, 140)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setCursor(Qt.CursorShape.PointingHandCursor)
        lab.setStyleSheet(
            f"background: {PALETTE['surface2']}; border: 1px solid {PALETTE['border']};"
            " border-radius: 4px;"
        )
        return lab

    def _zoom_cmp(self, which: str) -> None:
        """点核对图 → 弹出大图窗口（原始分辨率，滚轮缩放）。"""
        raw = self._cmp_cell_raw if which == "cell" else self._cmp_card_raw
        if raw is None or raw.isNull():
            return
        title = "截图里的卡面（放大核对）" if which == "cell" else "Bestdori 卡图（放大核对）"
        ImageZoomDialog(raw, title, self).exec()

    # ---- ③ 卡面清单 ---------------------------------------------------

    def _build_inventory_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 10)
        lay.setSpacing(10)

        head = QLabel("卡面清单")
        head.setObjectName("Title")
        lay.addWidget(head)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.stat_total = StatCard("清单总数")
        self.stat_trained = StatCard("特训后卡面")
        self.stat_pending = StatCard("待人工确认", color=PALETTE["warning"])
        self.stat_high = StatCard("4★ / 5★ 合计", color=PALETTE["accent"])
        for s in (self.stat_total, self.stat_trained, self.stat_pending, self.stat_high):
            stats.addWidget(s)
        stats.addStretch(1)
        lay.addLayout(stats)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.inv_search = QLineEdit()
        self.inv_search.setPlaceholderText("搜索卡名 / 角色 / 乐队 / ID…")
        self.inv_search.textChanged.connect(self._reload_inventory_table)
        self.inv_search.setMinimumWidth(240)
        tools.addWidget(self.inv_search, 1)
        self.inv_pending_cb = QCheckBox("只看待确认")
        self.inv_pending_cb.stateChanged.connect(self._reload_inventory_table)
        tools.addWidget(self.inv_pending_cb)
        b_confirm = QPushButton("确认选中")
        b_confirm.clicked.connect(self._confirm_selected)
        tools.addWidget(b_confirm)
        b_remove = QPushButton("删除选中")
        b_remove.setObjectName("Danger")
        b_remove.clicked.connect(self._remove_selected_cards)
        tools.addWidget(b_remove)
        b_clear_inv = QPushButton("清空清单")
        b_clear_inv.setObjectName("Danger")
        b_clear_inv.clicked.connect(self._clear_inventory)
        tools.addWidget(b_clear_inv)
        lay.addLayout(tools)

        self.inv_table = self._make_table(
            ["卡图", "卡牌", "角色", "乐队", "星级", "属性", "特训", "置信度", "已确认", "ID"]
        )
        self.inv_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        lay.addWidget(self.inv_table, 1)

        exp = QHBoxLayout()
        exp.setSpacing(8)
        for label, fmt in (("导出 CSV", "csv"), ("导出 Markdown", "md"), ("导出 JSON", "json"), ("导出 ID 列表", "ids")):
            b = QPushButton(label)
            b.clicked.connect(lambda _c=False, f=fmt: self._export(f))
            exp.addWidget(b)
        exp.addStretch(1)
        b_open = QPushButton("打开清单文件位置")
        b_open.clicked.connect(lambda: self._open_path(self.state.settings.inventory_path.parent))
        exp.addWidget(b_open)
        lay.addLayout(exp)
        return page

    # ---- ④ 同步 -------------------------------------------------------

    def _build_sync_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 10)
        lay.setSpacing(10)

        head = QLabel("同步 Bestdori")
        head.setObjectName("Title")
        lay.addWidget(head)

        # --- 卡池资料 ---
        g1 = QGroupBox("第一步 · 卡池资料")
        l1 = QHBoxLayout(g1)
        l1.setContentsMargins(12, 8, 12, 10)
        l1.setSpacing(10)
        self.sync_btn = QPushButton("同步卡牌资料")
        self.sync_btn.setObjectName("Primary")
        self.sync_btn.clicked.connect(self._do_sync)
        l1.addWidget(self.sync_btn)
        self._action_buttons.append(self.sync_btn)
        self.catalog_label = QLabel("尚未同步")
        self.catalog_label.setObjectName("Hint")
        l1.addWidget(self.catalog_label, 1)
        lay.addWidget(g1)

        # --- 指纹库 ---
        g2 = QGroupBox("第二步 · 卡面指纹库")
        l2 = QGridLayout(g2)
        l2.setContentsMargins(12, 8, 12, 10)
        l2.setHorizontalSpacing(10)
        self.trained_cb = QCheckBox("包含特训后卡面")
        self.trained_cb.setChecked(True)
        l2.addWidget(self.trained_cb, 0, 0)
        l2.addWidget(QLabel("只处理前"), 0, 1)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(0, 99999)
        self.limit_spin.setSpecialValueText("全部")
        self.limit_spin.setToolTip("只想先试跑一下时填个小数字，例如 100。注意局部重建会覆盖整个指纹库。")
        l2.addWidget(self.limit_spin, 0, 2)
        l2.addWidget(QLabel("张卡"), 0, 3)
        self.index_btn = QPushButton("构建 / 更新指纹库")
        self.index_btn.setObjectName("Primary")
        self.index_btn.clicked.connect(self._do_build_index)
        l2.addWidget(self.index_btn, 0, 4)
        self._action_buttons.append(self.index_btn)
        self.index_label = QLabel("尚无指纹库")
        self.index_label.setObjectName("Hint")
        l2.addWidget(self.index_label, 1, 0, 1, 5)
        warn = QLabel(
            "首次构建需要下载全部卡面原图（约 1~2 GB），耗时较长；之后重建会自动复用未变的图片。"
        )
        warn.setObjectName("Hint")
        l2.addWidget(warn, 2, 0, 1, 5)
        lay.addWidget(g2)

        # --- 导入 ---
        g3 = QGroupBox("第三步 · 写入 Bestdori「我的卡牌」")
        l3 = QVBoxLayout(g3)
        l3.setContentsMargins(12, 8, 12, 10)
        l3.setSpacing(8)

        # --- 账号（后台导入用：登录一次，会话存本机，之后全部纯 HTTP 不弹窗）---
        acc_row = QHBoxLayout()
        acc_row.setSpacing(8)
        acc_row.addWidget(QLabel("Bestdori 账号"))
        self.acc_user = QLineEdit()
        self.acc_user.setPlaceholderText("用户名")
        self.acc_user.setMaximumWidth(170)
        acc_row.addWidget(self.acc_user)
        self.acc_pass = QLineEdit()
        self.acc_pass.setPlaceholderText("密码")
        self.acc_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.acc_pass.setMaximumWidth(170)
        acc_row.addWidget(self.acc_pass)
        self.login_btn = QPushButton("登录")
        self.login_btn.clicked.connect(self._do_api_login)
        acc_row.addWidget(self.login_btn)
        self._action_buttons.append(self.login_btn)
        self.acc_status = QLabel("未登录 —— 后台导入需要；登录一次即可，会话只保存在本机")
        self.acc_status.setObjectName("Hint")
        acc_row.addWidget(self.acc_status, 1)
        l3.addLayout(acc_row)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.plan_btn = QPushButton("生成导入计划")
        self.plan_btn.clicked.connect(self._do_plan)
        row1.addWidget(self.plan_btn)
        self._action_buttons.append(self.plan_btn)
        self.confirmed_only_cb = QCheckBox("只导入已人工确认的条目")
        row1.addWidget(self.confirmed_only_cb)
        row1.addStretch(1)
        b_page = QPushButton("打开 Bestdori 卡册页面")
        b_page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://bestdori.com/profile/cards")))
        row1.addWidget(b_page)
        l3.addLayout(row1)

        self.plan_label = QLabel("尚未生成导入计划")
        self.plan_label.setObjectName("Hint")
        l3.addWidget(self.plan_label)

        self.plan_table = self._make_table(["卡牌 ID", "卡名", "角色", "星级", "属性", "特训"])
        self.plan_table.setMaximumHeight(190)
        l3.addWidget(self.plan_table)

        row_prof = QHBoxLayout()
        row_prof.setSpacing(8)
        row_prof.addWidget(QLabel("同步到云端第"))
        self.profile_spin = QSpinBox()
        # 对用户显示 1-based（"第 1 份"），内部仍是 0-based 的列表下标
        self.profile_spin.setRange(1, 99)
        self.profile_spin.setValue(1)
        self.profile_spin.setToolTip(
            "Bestdori 支持多份档案（在 Profile Manager 里创建）。\n"
            "1 = 第一份，通常只有这一份。登录成功后运行日志会显示云端档案数。"
        )
        row_prof.addWidget(self.profile_spin)
        row_prof.addWidget(QLabel("份档案"))
        row_prof.addStretch(1)
        l3.addLayout(row_prof)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.api_btn = QPushButton("后台导入到 Bestdori")
        self.api_btn.setObjectName("Primary")
        self.api_btn.setToolTip(
            "通过 Bestdori 接口写入「我的卡牌」：远端没有的卡新增，\n"
            "远端只有未特训而本地已特训的卡升级。全程后台，不弹浏览器。"
        )
        self.api_btn.clicked.connect(self._do_api_import)
        row2.addWidget(self.api_btn)
        self._action_buttons.append(self.api_btn)
        row2.addStretch(1)
        b_page2 = QPushButton("打开 Bestdori 卡册页面")
        b_page2.setToolTip("导入后到这里核对：https://bestdori.com/profile/cards")
        b_page2.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://bestdori.com/profile/cards"))
        )
        row2.addWidget(b_page2)
        l3.addLayout(row2)

        note = QLabel(
            "导入走 Bestdori 后台接口，不启动浏览器：先登录（会话保存在本机），"
            "再按清单增量写入。写入前可以先「生成导入计划」看一眼待导入清单。"
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        l3.addWidget(note)
        lay.addWidget(g3, 1)
        return page

    # ------------------------------------------------------------------
    # 通用小工具
    # ------------------------------------------------------------------

    def _make_table(self, headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(56)
        t.setAlternatingRowColors(True)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setShowGrid(False)
        t.setIconSize(THUMB_SIZE)
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        return t

    @staticmethod
    def _cell(text: str, color: str | None = None, center: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if color:
            item.setForeground(QColor(color))
        if center:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def _placeholder_pixmap(self) -> QPixmap:
        if self._placeholder is None:
            pm = QPixmap(THUMB_SIZE)
            pm.fill(QColor(PALETTE["surface3"]))
            self._placeholder = pm
        return self._placeholder

    def _icon_from(self, pm: QPixmap | None) -> QIcon:
        if pm is None or pm.isNull():
            return QIcon(self._placeholder_pixmap())
        return QIcon(pm)

    def _register_thumb(self, bucket: str, item: QTableWidgetItem, card_id: int, trained: bool) -> None:
        """给表格单元挂上缩略图；图还没下载完就先占位，加载完自动补上。"""
        catalog = self.state.catalog
        card = catalog.card(card_id) if catalog else None
        if card is None:
            item.setIcon(self._icon_from(None))
            return
        pm = self.thumb.pixmap(card_id, trained)
        if pm is not None:
            item.setIcon(self._icon_from(pm))
            return
        item.setIcon(self._icon_from(None))
        key = self.thumb.key_of(card_id, trained)
        self._thumb_watchers.setdefault(bucket, {}).setdefault(key, []).append(item)
        self.thumb.request(card, trained)

    def _on_thumb_loaded(self, key: str, path: str) -> None:
        try:
            cid_s, tr_s = key.split(":")
            cid, tr = int(cid_s), bool(int(tr_s))
        except ValueError:
            return
        pm = self.thumb.pixmap(cid, tr)
        icon = self._icon_from(pm)
        # 核对面板正等这张图 —— 补上它
        if key == self._detail_waiting:
            self._detail_waiting = ""
            cur = self._current_scan_item()
            if cur is not None:
                self._refresh_detail_panel(cur[0], cur[2])
        for bucket in self._thumb_watchers.values():
            items = bucket.pop(key, [])
            for it in items:
                try:
                    it.setIcon(icon)
                except RuntimeError:
                    # 底层 C++ 对象已被表格清空销毁
                    pass

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _set_busy(self, busy: bool, text: str = "") -> None:
        for b in self._action_buttons:
            b.setEnabled(not busy)
        if busy:
            self.progress.show()
            self.status_label.setText(text or "处理中…")
        else:
            self.progress.hide()
            self.status_label.setText(text or "就绪")
            self._update_scan_button()

    def _run_task(self, worker: Worker, on_done, *, busy: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍等", "已有任务正在执行，等它跑完再试。")
            return
        self._worker = worker
        self._set_busy(True, busy)
        worker.progress.connect(self._log)
        worker.done.connect(lambda result: self._task_finished(result, on_done))
        worker.failed.connect(self._task_failed)
        worker.start()

    def _task_finished(self, result, on_done) -> None:
        self._set_busy(False)
        try:
            on_done(result)
        except Exception as e:  # noqa: BLE001 - 回调里的错误也要显示出来
            log.exception("结果处理失败")
            self._log(f"❌ 结果处理失败：{e}")
            QMessageBox.critical(self, "出错了", str(e))

    def _task_failed(self, message: str) -> None:
        self._set_busy(False)
        self._log(f"❌ 失败：{message}")
        QMessageBox.critical(self, "任务失败", message)

    def _toggle_log(self) -> None:
        if self.log_view.isVisible():
            self.log_view.hide()
            self.log_toggle.setText("展开")
        else:
            self.log_view.show()
            self.log_toggle.setText("收起")

    def _open_path(self, path: Path) -> None:
        p = Path(path)
        if not p.exists():
            QMessageBox.information(self, "路径不存在", f"{p}\n\n（还没有生成过文件）")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _open_home(self) -> None:
        self._open_path(self.state.settings.home)

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "关于 BestdoriHelper",
            "BestdoriHelper\n\n"
            "把游戏卡面截图批量识别成本地清单，再同步到 Bestdori「我的卡牌」。\n\n"
            f"数据目录：{self.state.settings.home}\n"
            f"当前服务器：{SERVER_CN.get(self.state.settings.server.value, '')}\n"
            f"语言：{LANGUAGES.get(self.state.settings.lang or 0, '')}",
        )

    # ------------------------------------------------------------------
    # 状态刷新
    # ------------------------------------------------------------------

    def _refresh_state(self) -> None:
        s = self.state
        catalog = s.catalog_or_load(allow_network=False)
        index = s.index_or_load()
        inv = s.inventory_or_load()

        if catalog is None:
            self.catalog_label.setText("尚未同步 —— 点左边的按钮拉取卡池资料")
        else:
            stamp = ""
            if s.settings.cards_json.exists():
                import datetime as _dt

                stamp = _dt.datetime.fromtimestamp(
                    s.settings.cards_json.stat().st_mtime
                ).strftime("%Y-%m-%d %H:%M")
            self.catalog_label.setText(
                f"卡池 {len(catalog.cards)} 张 · 角色 {len(catalog.characters)} 名"
                + (f" · 本地缓存于 {stamp}" if stamp else "")
            )

        if index is None or len(index) == 0:
            self.index_label.setText("尚无指纹库 —— 构建后才能识别截图")
        else:
            st = index.stats()
            self.index_label.setText(
                f"卡面变体 {st['variants']} 个 · 覆盖 {st['cards']} 张卡"
                f"（普通 {st['normal']} / 特训后 {st['trained']}）"
            )

        self._reload_inventory_table()
        self._update_scan_button()

    def _update_scan_button(self) -> None:
        catalog = self.state.catalog
        index = self.state.index_or_load()
        ready = catalog is not None and index is not None and len(index) > 0
        self.scan_btn.setEnabled(ready and not self._is_busy())
        if not ready:
            self.scan_btn.setToolTip("需要先同步卡池资料并构建指纹库（见第 ④ 页）")
        else:
            self.scan_btn.setToolTip("")

    def _is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _on_server_changed(self, row: int) -> None:
        value = self.server_combo.itemData(row)
        if not value or value == self.state.settings.server.value:
            return
        ok = QMessageBox.question(
            self,
            "切换服务器",
            f"切到「{SERVER_CN.get(value, value)}」？\n\n"
            "卡池资料与指纹库是按服务器分开存的，切换后需要重新同步 / 构建。\n"
            "（清单文件是共用的，不会丢）",
        )
        if ok != QMessageBox.StandardButton.Yes:
            self.server_combo.blockSignals(True)
            self.server_combo.setCurrentIndex(
                [s.value for s in Server].index(self.state.settings.server.value)
            )
            self.server_combo.blockSignals(False)
            return
        new_settings = Settings(
            server=Server(value),
            home=self.state.settings.home,
            # 保住用户已有的卡图来源选择，否则切一次服务器就被打回默认值
            image_source=self.state.settings.image_source,
        )
        new_settings.ensure_dirs()
        self.state.reset(new_settings)
        self.thumb.reset(new_settings)
        self._thumb_watchers.clear()
        self.lang_combo.blockSignals(True)
        self.lang_combo.setCurrentIndex(max(0, new_settings.lang or 0))
        self.lang_combo.blockSignals(False)
        self._clear_results()
        self._log(f"已切换到 {SERVER_CN.get(value, value)}，数据目录 {new_settings.data_dir}")
        self._refresh_state()

    def _on_lang_changed(self, row: int) -> None:
        value = self.lang_combo.itemData(row)
        if value is None:
            return
        self.state.settings.lang = int(value)
        if self.state.catalog is not None:
            # 只影响显示文本，重建一次 Catalog 视图即可
            self.state.catalog.settings = self.state.settings
            self._reload_inventory_table()
            self._reload_result_table()
        self._log(f"语言切换为 {LANGUAGES.get(int(value), '')}")

    def _on_mode_changed(self) -> None:
        grid = self.mode_combo.currentData() == "grid"
        self.rows_spin.setEnabled(grid)
        self.cols_spin.setEnabled(grid)

    # ------------------------------------------------------------------
    # ① 截图列表
    # ------------------------------------------------------------------

    def _add_paths(self, paths: list[str]) -> None:
        added = 0
        existing = {str(Path(p).resolve()) for p in self.pending_paths}
        for raw in paths:
            for p in iter_images([raw]):
                key = str(p.resolve())
                if key in existing:
                    continue
                existing.add(key)
                self.pending_paths.append(str(p))
                added += 1
        self._reload_file_list()
        if added:
            self._log(f"加入 {added} 张截图，共 {len(self.pending_paths)} 张待处理")
            self.nav.setCurrentRow(0)

    def _pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择卡面截图", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)"
        )
        if files:
            self._add_paths(files)

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择截图文件夹", "")
        if d:
            self._add_paths([d])

    def _remove_selected_paths(self) -> None:
        rows = sorted({i.row() for i in self.file_list.selectedIndexes()}, reverse=True)
        for r in rows:
            if 0 <= r < len(self.pending_paths):
                del self.pending_paths[r]
        self._reload_file_list()

    def _clear_paths(self) -> None:
        self.pending_paths.clear()
        self._reload_file_list()

    def _reload_file_list(self) -> None:
        self.file_list.clear()
        for p in self.pending_paths:
            item = QListWidgetItem(Path(p).name)
            item.setToolTip(p)
            item.setData(Qt.ItemDataRole.UserRole, p)
            pm = QPixmap(p)
            if not pm.isNull():
                item.setIcon(
                    QIcon(
                        pm.scaled(
                            QSize(64, 48),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                )
            self.file_list.addItem(item)
        self.file_count_label.setText(f"待处理：{len(self.pending_paths)} 张")

    # ------------------------------------------------------------------
    # 识别
    # ------------------------------------------------------------------

    def _do_scan(self) -> None:
        if not self.pending_paths:
            QMessageBox.information(self, "还没有截图", "先把截图拖进上面的框里，或点「选择图片…」。")
            return
        index = self.state.index_or_load()
        if index is None or len(index) == 0:
            QMessageBox.warning(self, "指纹库为空", "请先到「④ 同步 Bestdori」页构建指纹库。")
            self.nav.setCurrentRow(3)
            return
        catalog = self.state.catalog_or_load(allow_network=False)
        if catalog is None:
            QMessageBox.warning(self, "卡池资料缺失", "请先到「④ 同步 Bestdori」页同步卡牌资料。")
            self.nav.setCurrentRow(3)
            return

        mode = self.mode_combo.currentData() or "auto"
        rows = self.rows_spin.value() or None
        cols = self.cols_spin.value() or None
        worker = Worker(
            task_scan(
                self.state.settings,
                index,
                catalog,
                list(self.pending_paths),
                mode=mode,
                rows=rows,
                cols=cols,
                inset=self.inset_spin.value(),
                use_ocr=self.ocr_cb.isChecked(),
                use_attribute_hint=self.attr_hint_cb.isChecked(),
                absorb=self.absorb_cb.isChecked(),
                min_confidence=self.min_conf_spin.value(),
                include_ambiguous=self.amb_cb.isChecked(),
            ),
            self,
        )
        self._run_task(worker, self._on_scan_done, busy="正在识别截图…")

    def _on_scan_done(self, payload: dict) -> None:
        self.state.scan_results = payload["results"]
        if payload.get("inventory") is not None:
            self.state.inventory = payload["inventory"]

        total_items = sum(len(r.items) for r in self.state.scan_results)
        matched = sum(len(r.matched) for r in self.state.scan_results)
        self._log(f"识别完成：{len(self.state.scan_results)} 张截图，共切出 {total_items} 格，确定匹配 {matched} 格")
        absorbed = payload.get("absorbed")
        if absorbed:
            self._log(
                f"清单更新：新增 {absorbed['added']}，已存在跳过 {absorbed['skipped']}，"
                f"待人工确认 {absorbed['pending']}"
            )

        self._reload_result_table()
        self._refresh_state()
        self.nav.setCurrentRow(1)
        self._set_busy(False, "识别完成")

    def _reload_result_table(self) -> None:
        self._thumb_watchers.pop("scan", None)
        self.result_table.setRowCount(0)
        self._scan_rows = []
        catalog = self.state.catalog

        for ri, res in enumerate(self.state.scan_results):
            for ii, item in enumerate(res.items):
                row = self.result_table.rowCount()
                self.result_table.insertRow(row)
                self._scan_rows.append((ri, ii))

                self.result_table.setItem(
                    row, 0, self._cell(status_label(item.status), status_color(item.status), True)
                )

                thumb_item = QTableWidgetItem()
                if item.card_id is not None and catalog is not None:
                    self._register_thumb("scan", thumb_item, item.card_id, item.trained)
                else:
                    thumb_item.setIcon(self._icon_from(None))
                self.result_table.setItem(row, 1, thumb_item)

                if item.card_id is not None and catalog is not None:
                    info = catalog.describe(item.card_id, item.trained)
                else:
                    info = None

                self.result_table.setItem(row, 2, self._cell(info["title"] if info else "（未识别）"))
                self.result_table.setItem(row, 3, self._cell(info["character"] if info else ""))
                self.result_table.setItem(row, 4, self._cell(info["rarityLabel"] if info else "", center=True))
                attr_item = self._cell(info["attributeLabel"] if info else "", center=True)
                if info and info["attribute"] in ATTRIBUTE_COLOR:
                    attr_item.setForeground(QColor(ATTRIBUTE_COLOR[info["attribute"]]))
                self.result_table.setItem(row, 5, attr_item)
                self.result_table.setItem(row, 6, self._cell(f"{item.confidence:.3f}", center=True))
                self.result_table.setItem(row, 7, self._cell(item.matched_by))
                src_item = self._cell(Path(res.image).name)
                src_item.setToolTip(str(res.image))
                self.result_table.setItem(row, 8, src_item)

        self.result_table.resizeColumnsToContents()
        self.result_table.setColumnWidth(1, 96)

        # 截图切换器：一次识别多张截图时要让用户看得见、点得着
        self.shot_combo.blockSignals(True)
        self.shot_combo.clear()
        for _i, _res in enumerate(self.state.scan_results):
            self.shot_combo.addItem(f"第 {_i + 1} 张 · {Path(_res.image).name}")
        self.shot_combo.setEnabled(len(self.state.scan_results) > 1)
        self.shot_combo.blockSignals(False)

        # 原图框选：结果换了就回到第一张截图
        self._load_anno_shot(0 if self.state.scan_results else -1)

        self.result_summary.setText(
            f"共 {self.result_table.rowCount()} 格 · 确定匹配 {sum(1 for r in self.state.scan_results for i in r.items if i.status == 'matched')}"
            f" · 待确认 {sum(1 for r in self.state.scan_results for i in r.items if i.status == 'ambiguous')}"
            f" · 未识别 {sum(1 for r in self.state.scan_results for i in r.items if i.status == 'unknown')}"
        )

    def _current_scan_item(self) -> tuple[int, int, RecognizedItem] | None:
        row = self.result_table.currentRow()
        if row < 0 or row >= len(self._scan_rows):
            return None
        ri, ii = self._scan_rows[row]
        return ri, ii, self.state.scan_results[ri].items[ii]

    def _load_anno_shot(self, index: int) -> None:
        """把第 index 张结果的原图与框送进框选视图。"""
        self._anno_result_index = index
        # 同步切换器（blockSignals 避免回头再触发一次切换）
        if index >= 0 and self.shot_combo.count() > index:
            self.shot_combo.blockSignals(True)
            self.shot_combo.setCurrentIndex(index)
            self.shot_combo.blockSignals(False)
        if index < 0 or index >= len(self.state.scan_results):
            self.anno_view.clear()
            self._cmp_source = None
            return
        res = self.state.scan_results[index]
        ok = self.anno_view.set_screenshot(res.image)
        self._cmp_source = self.anno_view.pixmap if ok else None
        self.anno_view.set_boxes(
            [i.box for i in res.items], [i.status for i in res.items]
        )

    def _on_shot_combo(self, index: int) -> None:
        """切换器选了另一张截图 —— 换掉左边的原图与框。"""
        if index < 0 or index == self._anno_result_index:
            return
        self._load_anno_shot(index)
        # 顺手选中这张截图的第一格，右侧核对面板才有内容
        for row, (ri, _ii) in enumerate(self._scan_rows):
            if ri == index:
                self.result_table.selectRow(row)
                break

    def _on_box_clicked(self, index: int) -> None:
        """点原图里的框 -> 选中结果表里对应的行。

        ``index`` 是**当前那张截图内**的格子序号，而表格是**所有截图**的条目
        拼起来的 —— 多张截图时两者根本不是一回事。原来直接拿 index 当行号，
        于是点第 2 张图的第 3 格会选中表格第 3 行（那是第 1 张图的格子），
        面板又把左边的原图切回第一张 —— 表现就是"多张图片时只能看第一张"。
        """
        target = (self._anno_result_index, index)
        for row, key in enumerate(self._scan_rows):
            if key == target:
                self.result_table.selectRow(row)
                it = self.result_table.item(row, 0)
                if it is not None:
                    self.result_table.scrollToItem(it)
                return

    def _reference_pixmap(self, card_id: int, trained: bool) -> QPixmap | None:
        """Bestdori 卡图。

        优先直接读本地卡图缓存（就是建指纹库用的那一套，几乎总是已经在盘上）；
        没有就触发异步下载，下完由 :meth:`_on_thumb_loaded` 回来刷新面板。
        """
        catalog = self.state.catalog
        card = catalog.card(card_id) if catalog is not None else None
        if card is None:
            return None
        base = self.state.settings.source_dir
        # 和 verify._art_path 同样的回退：约 6.5% 的卡没有 *_normal.png
        for tr in ((trained, not trained) if trained else (False, True)):
            suffix = "after_training" if tr else "normal"
            path = base / f"{card.resource_set_name}_{suffix}.png"
            if path.exists():
                pm = QPixmap(str(path))
                if not pm.isNull():
                    return pm
        self.thumb.request(card, trained)
        self._detail_waiting = self.thumb.key_of(card_id, trained)
        return None

    @staticmethod
    def _fit_label(label: QLabel, pm: QPixmap | None) -> None:
        if pm is None or pm.isNull():
            label.clear()
            return
        label.setPixmap(
            pm.scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
        )

    def _refresh_detail_panel(self, ri: int, item: RecognizedItem | None) -> None:
        """右侧核对面板：游戏格 / Bestdori 卡图 / 卡面详情页网址。"""
        if item is None:
            self._fit_label(self.cmp_cell, None)
            self._fit_label(self.cmp_card, None)
            self._cmp_cell_raw = None
            self._cmp_card_raw = None
            self.url_label.setText("卡面详情页（点链接或按钮打开核对）")
            self.url_edit.clear()
            self.b_url.setEnabled(False)
            return
        if ri != self._anno_result_index:
            self._load_anno_shot(ri)

        # 左：从截图里按识别出的框裁出来，这就是"识别到的图"
        cell: QPixmap | None = None
        if self._cmp_source is not None and not self._cmp_source.isNull():
            cell = self._cmp_source.copy(
                int(item.box.x), int(item.box.y), int(item.box.w), int(item.box.h)
            )
        self._cmp_cell_raw = cell
        self._fit_label(self.cmp_cell, cell)

        catalog = self.state.catalog
        info = None
        if item.card_id is not None and catalog is not None:
            info = catalog.describe(item.card_id, item.trained)

        # 右：Bestdori 的同一套卡图（就是比对用的那张）
        ref: QPixmap | None = None
        if info is not None:
            self._detail_waiting = ""
            ref = self._reference_pixmap(item.card_id, item.trained)
        self._cmp_card_raw = ref
        self._fit_label(self.cmp_card, ref)

        if info is not None:
            url = info["url"]
            self.url_label.setText(
                f'卡面详情页：<a href="{url}" style="color:{PALETTE["accent"]}">'
                f"在浏览器中打开 ↗</a>"
            )
            self.url_label.setToolTip(url)
            self.url_edit.setText(url)
            self.url_edit.setCursorPosition(0)
            self.url_edit.setToolTip(f"Bestdori 卡面页：{url}（可直接复制）")
            self.b_url.setEnabled(True)
        else:
            self.url_label.setText("未识别出卡牌，没有可核对的卡面页。")
            self.url_edit.clear()
            self.b_url.setEnabled(False)

    def _open_card_url(self) -> None:
        cur = self._current_scan_item()
        if cur is None:
            return
        info = self.state.catalog.describe(cur[2].card_id, cur[2].trained) \
            if self.state.catalog is not None and cur[2].card_id is not None else None
        if info is not None:
            QDesktopServices.openUrl(QUrl(info["url"]))

    def _on_result_selected(self) -> None:
        cur = self._current_scan_item()
        self.cand_list.clear()
        if cur is None:
            self.sel_label.setText("未选中")
            self.anno_view.set_current(-1)
            self._refresh_detail_panel(-1, None)
            return
        ri, ii, item = cur
        self.anno_view.set_current(ii)
        self._refresh_detail_panel(ri, item)
        catalog = self.state.catalog
        if item.card_id is not None and catalog is not None:
            info = catalog.describe(item.card_id, item.trained)
            self.sel_label.setText(
                f"{info['character']} · {info['title']}\n"
                f"{info['rarityLabel']} {info['attributeLabel']}　置信度 {item.confidence:.3f}\n"
                f"状态：{status_label(item.status)}"
            )
        else:
            self.sel_label.setText("未识别出卡牌")

        for m in item.candidates:
            if catalog is not None:
                info = catalog.describe(m.card_id, m.trained)
                text = (
                    f"{m.score:.3f}　{info['character']} · {info['title']}"
                    f"　[{info['rarityLabel']}]"
                    + ("　特训后" if m.trained else "")
                )
            else:
                text = f"{m.score:.3f}　#{m.card_id}"
            li = QListWidgetItem(text)
            li.setData(Qt.ItemDataRole.UserRole, (m.card_id, m.trained))
            if catalog is not None:
                card = catalog.card(m.card_id)
                if card and card.attribute in ATTRIBUTE_COLOR:
                    li.setForeground(QColor(ATTRIBUTE_COLOR[card.attribute]))
            self.cand_list.addItem(li)

    def _apply_candidate(self) -> None:
        cur = self._current_scan_item()
        if cur is None:
            return
        li = self.cand_list.currentItem()
        if li is None:
            QMessageBox.information(self, "先选一个候选", "在右侧列表里点一下要用的那张卡。")
            return
        card_id, trained = li.data(Qt.ItemDataRole.UserRole)
        self._set_scan_item(cur[0], cur[1], card_id, trained, source="manual")

    def _pick_card_for_row(self) -> None:
        cur = self._current_scan_item()
        if cur is None:
            return
        catalog = self.state.catalog_or_load(allow_network=False)
        if catalog is None:
            QMessageBox.warning(self, "卡池资料缺失", "请先同步卡牌资料。")
            return
        dlg = CardPickerDialog(catalog, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen:
            self._set_scan_item(cur[0], cur[1], dlg.chosen[0], dlg.chosen[1], source="manual")

    def _set_scan_item(self, ri: int, ii: int, card_id: int, trained: bool, *, source: str) -> None:
        """人工修正一格识别结果，并同步更新清单。"""
        res = self.state.scan_results[ri]
        item = res.items[ii]
        old = (item.card_id, item.trained)

        item.card_id = card_id
        item.trained = trained
        item.status = "matched"
        item.matched_by = source
        item.confidence = 1.0

        inv = self.state.inventory_or_load()
        if old[0] is not None:
            inv.remove(old[0], old[1])
        inv.add(
            OwnedCard(
                card_id=card_id,
                trained=trained,
                source_image=res.image,
                confidence=1.0,
                matched_by=source,
                confirmed=True,
            ),
            overwrite=True,
        )
        inv.save()

        catalog = self.state.catalog
        label = ""
        if catalog is not None:
            info = catalog.describe(card_id, trained)
            label = f"{info['character']} · {info['title']}"
        self._log(f"手动修正：{label} (#{card_id}{' 特训后' if trained else ''})")

        self._reload_result_table()
        self._reload_inventory_table()

    def _ignore_row(self) -> None:
        cur = self._current_scan_item()
        if cur is None:
            return
        ri, ii, item = cur
        if item.card_id is not None:
            inv = self.state.inventory_or_load()
            inv.remove(item.card_id, item.trained)
            inv.save()
        item.card_id = None
        item.trained = False
        item.status = "unknown"
        item.matched_by = "ignored"
        item.confidence = 0.0
        self._log("已忽略该格，并从清单移除")
        self._reload_result_table()
        self._reload_inventory_table()

    def _absorb_all(self) -> None:
        if not self.state.scan_results:
            return
        inv = self.state.inventory_or_load()
        total = {"added": 0, "skipped": 0, "pending": 0}
        for res in self.state.scan_results:
            delta = inv.absorb_recognition(
                res, min_confidence=self.min_conf_spin.value(), include_ambiguous=self.amb_cb.isChecked()
            )
            for k in total:
                total[k] += delta[k]
        inv.save()
        self._log(
            f"全部加入清单：新增 {total['added']}，已存在跳过 {total['skipped']}，待确认 {total['pending']}"
        )
        self._refresh_state()

    def _clear_results(self) -> None:
        self.state.scan_results = []
        self._thumb_watchers.pop("scan", None)
        self.result_table.setRowCount(0)
        self._scan_rows = []
        self.cand_list.clear()
        self.sel_label.setText("未选中")
        self.result_summary.setText("还没有识别结果。")

    # ------------------------------------------------------------------
    # ③ 清单
    # ------------------------------------------------------------------

    def _reload_inventory_table(self) -> None:
        self._thumb_watchers.pop("inventory", None)
        self.inv_table.setRowCount(0)
        inv = self.state.inventory_or_load()
        catalog = self.state.catalog

        if catalog is None:
            self.stat_total.set_value("—")
            self.stat_trained.set_value("—")
            self.stat_pending.set_value("—")
            self.stat_high.set_value("—")
            return

        st = inv.stats(catalog)
        self.stat_total.set_value(str(st.total))
        self.stat_trained.set_value(str(st.trained))
        self.stat_pending.set_value(str(st.pending_review))
        high = sum(n for r, n in (st.by_rarity or {}).items() if r >= 4)
        self.stat_high.set_value(str(high))

        query = self.inv_search.text().strip().lower()
        only_pending = self.inv_pending_cb.isChecked()

        for oc in inv.all():
            if only_pending and oc.confirmed:
                continue
            info = catalog.describe(oc.card_id, oc.trained)
            hay = f"{info['title']} {info['character']} {info['band']} {oc.card_id}".lower()
            if query and query not in hay:
                continue

            row = self.inv_table.rowCount()
            self.inv_table.insertRow(row)

            thumb_item = QTableWidgetItem()
            self._register_thumb("inventory", thumb_item, oc.card_id, oc.trained)
            self.inv_table.setItem(row, 0, thumb_item)

            self.inv_table.setItem(row, 1, self._cell(info["title"]))
            self.inv_table.setItem(row, 2, self._cell(info["character"]))
            self.inv_table.setItem(row, 3, self._cell(info["band"]))
            self.inv_table.setItem(row, 4, self._cell(info["rarityLabel"], center=True))
            attr_item = self._cell(info["attributeLabel"], center=True)
            if info["attribute"] in ATTRIBUTE_COLOR:
                attr_item.setForeground(QColor(ATTRIBUTE_COLOR[info["attribute"]]))
            self.inv_table.setItem(row, 5, attr_item)
            self.inv_table.setItem(row, 6, self._cell("是" if oc.trained else "", center=True))
            self.inv_table.setItem(
                row, 7, self._cell(f"{oc.confidence:.3f}" if oc.confidence else "", center=True)
            )
            confirmed_item = self._cell(
                "是" if oc.confirmed else "待确认",
                None if oc.confirmed else PALETTE["warning"],
                center=True,
            )
            self.inv_table.setItem(row, 8, confirmed_item)

            id_item = self._cell(str(oc.card_id), center=True)
            id_item.setData(Qt.ItemDataRole.UserRole, (oc.card_id, oc.trained))
            self.inv_table.setItem(row, 9, id_item)

        self.inv_table.resizeColumnsToContents()
        self.inv_table.setColumnWidth(0, 96)
        self.inv_table.setColumnWidth(1, 220)

    def _selected_inventory_keys(self) -> list[tuple[int, bool]]:
        keys: list[tuple[int, bool]] = []
        for idx in self.inv_table.selectionModel().selectedRows() if self.inv_table.selectionModel() else []:
            item = self.inv_table.item(idx.row(), 9)
            if item is None:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                keys.append((int(data[0]), bool(data[1])))
        return keys

    def _confirm_selected(self) -> None:
        keys = self._selected_inventory_keys()
        if not keys:
            QMessageBox.information(self, "没有选中行", "先在表格里选几行。")
            return
        inv = self.state.inventory_or_load()
        for cid, trained in keys:
            inv.confirm(cid, trained)
        inv.save()
        self._log(f"已确认 {len(keys)} 条")
        self._reload_inventory_table()

    def _remove_selected_cards(self) -> None:
        keys = self._selected_inventory_keys()
        if not keys:
            QMessageBox.information(self, "没有选中行", "先在表格里选几行。")
            return
        ok = QMessageBox.question(self, "确认删除", f"从清单里删除选中的 {len(keys)} 条记录？")
        if ok != QMessageBox.StandardButton.Yes:
            return
        inv = self.state.inventory_or_load()
        for cid, trained in keys:
            inv.remove(cid, trained)
        inv.save()
        self._log(f"已删除 {len(keys)} 条")
        self._reload_inventory_table()

    def _clear_inventory(self) -> None:
        inv = self.state.inventory_or_load()
        if not len(inv):
            return
        ok = QMessageBox.question(self, "确认清空", f"清单里有 {len(inv)} 条记录，全部清空？")
        if ok != QMessageBox.StandardButton.Yes:
            return
        n = inv.clear()
        inv.save()
        self._log(f"清单已清空（移除 {n} 条）")
        self._reload_inventory_table()

    def _export(self, fmt: str) -> None:
        inv = self.state.inventory_or_load()
        if not len(inv):
            QMessageBox.information(self, "清单是空的", "先识别几张截图吧。")
            return
        catalog = self.state.catalog_or_load(allow_network=False)
        if catalog is None:
            QMessageBox.warning(self, "卡池资料缺失", "请先同步卡牌资料。")
            return

        suffix = {"csv": "csv", "md": "md", "json": "json", "ids": "txt"}[fmt]
        out_dir = self.state.settings.home / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        default = out_dir / f"cards.{suffix}"
        path, _ = QFileDialog.getSaveFileName(self, "导出清单", str(default), f"*.{suffix}")
        if not path:
            return

        if fmt == "csv":
            content = to_csv(inv, catalog)
        elif fmt == "json":
            content = to_json(inv, catalog)
        elif fmt == "ids":
            content = to_bestdori_ids(inv)
        else:
            content = to_markdown(inv, catalog)

        Path(path).write_text(content, encoding="utf-8")
        self._log(f"已导出：{path}")

    # ------------------------------------------------------------------
    # ④ 同步
    # ------------------------------------------------------------------

    def _do_sync(self) -> None:
        worker = Worker(task_sync(self.state.settings, refresh=True), self)
        self._run_task(worker, self._on_sync_done, busy="正在同步卡池资料…")

    def _on_sync_done(self, catalog: Catalog) -> None:
        self.state.catalog = catalog
        self._log(f"卡池资料已同步：卡牌 {len(catalog.cards)} 张，角色 {len(catalog.characters)} 名")
        self._refresh_state()

    def _do_build_index(self) -> None:
        catalog = self.state.catalog_or_load(allow_network=False)
        if catalog is None:
            QMessageBox.warning(self, "卡池资料缺失", "请先点「同步卡牌资料」。")
            return
        limit = self.limit_spin.value()
        if limit:
            ok = QMessageBox.question(
                self,
                "局部重建",
                f"只会处理前 {limit} 张卡，新建的指纹库会**覆盖**整个旧库。\n继续？",
            )
            if ok != QMessageBox.StandardButton.Yes:
                return
        worker = Worker(
            task_build_index(
                self.state.settings,
                catalog,
                include_trained=self.trained_cb.isChecked(),
                limit=limit,
            ),
            self,
        )
        self._run_task(worker, self._on_index_done, busy="正在构建指纹库…")

    def _on_index_done(self, payload) -> None:
        index, stats = payload
        self.state.index = index
        self._log("指纹库构建完成：" + stats.summary())
        self._refresh_state()

    def _do_plan(self) -> None:
        inv = self.state.inventory_or_load()
        if not len(inv):
            QMessageBox.information(self, "清单是空的", "先识别截图并加入清单。")
            return
        catalog = self.state.catalog_or_load(allow_network=False)
        if catalog is None:
            QMessageBox.warning(self, "卡池资料缺失", "请先同步卡牌资料。")
            return
        worker = Worker(
            task_import_plan(catalog, inv, confirmed_only=self.confirmed_only_cb.isChecked()), self
        )
        self._run_task(worker, self._on_plan_done, busy="正在生成导入计划…")

    def _on_plan_done(self, plan) -> None:
        self.state.plan = plan
        self.plan_label.setText(plan.summary())
        self.plan_table.setRowCount(0)
        for e in plan.entries:
            row = self.plan_table.rowCount()
            self.plan_table.insertRow(row)
            self.plan_table.setItem(row, 0, self._cell(str(e.card_id), center=True))
            self.plan_table.setItem(row, 1, self._cell(e.title))
            self.plan_table.setItem(row, 2, self._cell(e.character))
            self.plan_table.setItem(row, 3, self._cell(f"{e.rarity}★", center=True))
            attr_item = self._cell(e.attribute, center=True)
            if e.attribute in ATTRIBUTE_COLOR:
                attr_item.setForeground(QColor(ATTRIBUTE_COLOR[e.attribute]))
            self.plan_table.setItem(row, 4, attr_item)
            self.plan_table.setItem(row, 5, self._cell("是" if e.trained else "", center=True))
        self.plan_table.resizeColumnsToContents()
        self._log("导入计划：" + plan.summary())

    def _api_account(self):
        from .bridge.bestdori_api import BestdoriAccount

        if self._account is None:
            self._account = BestdoriAccount(self.state.settings)
        return self._account

    def _do_api_login(self) -> None:
        user = self.acc_user.text().strip()
        pwd = self.acc_pass.text()
        if not user or not pwd:
            QMessageBox.information(self, "缺少账号信息", "请填写 Bestdori 的用户名和密码。")
            return
        worker = Worker(task_api_login(self.state.settings, user, pwd), self)
        self._run_task(worker, self._on_api_login_done, busy="正在登录 Bestdori…")

    def _on_api_login_done(self, user: dict | None) -> None:
        if user:
            me = user.get("_me") or {}
            name = user.get("username") or me.get("username") or me.get("name") or "Bestdori 账号"
            self.acc_status.setText(f"已登录 ✓（{name}）—— 会话保存在本机，之后同步不再需要登录")
            self.acc_pass.clear()
            self._log(f"Bestdori 后台登录成功：{name}")
        else:
            QMessageBox.warning(self, "登录失败", "用户名或密码不对，或网络异常。详见运行日志。")

    def _do_api_import(self) -> None:
        inv = self.state.inventory_or_load()
        if not len(inv):
            QMessageBox.information(self, "清单是空的", "先识别截图并加入清单。")
            return
        catalog = self.state.catalog_or_load(allow_network=False)
        if catalog is None:
            QMessageBox.warning(self, "卡池资料缺失", "请先同步卡牌资料。")
            return
        ok = QMessageBox.question(
            self,
            "确认写入",
            "将通过 Bestdori 接口把清单增量写入「我的卡牌」：\n"
            "· 远端没有的卡会添加\n· 远端只有未特训而本地已特训的卡会升级\n\n继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        worker = Worker(
            task_api_import(
                self.state.settings,
                inv,
                catalog,
                # 界面上 1 = 第一份，内部是 0-based 下标
                profile_index=self.profile_spin.value() - 1,
                only_confirmed=self.confirmed_only_cb.isChecked(),
            ),
            self,
        )
        self._run_task(worker, self._on_api_import_done, busy="正在后台导入…")

    def _on_api_import_done(self, stats) -> None:
        self._log("后台导入完成：" + stats.summary())
        QMessageBox.information(self, "后台导入完成", stats.summary() + "\n\n可到 Bestdori 卡册页面核对。")

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            ok = QMessageBox.question(self, "任务进行中", "还有任务在跑，确定要退出吗？")
            if ok != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._worker.cancel()
            self._worker.wait(3000)
        self.thumb.shutdown()
        if self.state.client is not None:
            self.state.client.close()
        event.accept()


# ---------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------


def run(settings: Settings | None = None, *, argv: list[str] | None = None) -> int:
    """启动 GUI，返回进程退出码。"""
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("BestdoriHelper")
    apply_theme(app)

    settings = settings or Settings()
    settings.ensure_dirs()

    window = MainWindow(settings)
    window.show()
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
