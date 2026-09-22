"""GUI 通用小部件：缩略图异步加载、拖放区、统计卡、选卡对话框、原图框选视图。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

from ..bestdori.client import BestdoriClient
from ..config import ATTRIBUTE_COLOR, Settings
from ..models import Card, Catalog
from ..vision.detect import Box
from .theme import PALETTE

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
THUMB_SIZE = QSize(88, 66)


# ---------------------------------------------------------------------
# 卡图缩略图
# ---------------------------------------------------------------------


class _ThumbTask(QRunnable):
    """后台把一个卡面图取到本地缓存。"""

    def __init__(self, loader: "ThumbnailLoader", key: str, card: Card, trained: bool) -> None:
        super().__init__()
        self.loader = loader
        self.key = key
        self.card = card
        self.trained = trained
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102
        path = ""
        try:
            p = self.loader.fetch(self.card, self.trained)
            if p is not None:
                path = str(p)
        except Exception:  # noqa: BLE001 - 单张图失败不该影响界面
            path = ""
        self.loader.finish(self.key, path)


class ThumbnailLoader(QObject):
    """异步加载卡面缩略图。

    卡图可能还没下载过（首次打开清单时），所以走线程池；命中本地缓存时
    几乎是瞬间返回。``httpx.Client`` 在这里被一把锁串行化 —— 界面上同时
    可见的卡图数量不多，安全性比并发度重要。
    """

    loaded = Signal(str, str)  # key, 本地路径（空串表示没有图）

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._lock = threading.Lock()
        self._client: BestdoriClient | None = None
        self._pending: set[str] = set()
        self._cache: dict[str, str] = {}
        self._pixmaps: dict[str, QPixmap] = {}

    # ---- 内部 --------------------------------------------------------

    def _client_or_new(self) -> BestdoriClient:
        if self._client is None:
            self._client = BestdoriClient(self.settings)
        return self._client

    def fetch(self, card: Card, trained: bool) -> object:
        with self._lock:
            client = self._client_or_new()
            path = client.ensure_card_image(card, trained)
            if path is None:
                # 该形态没有卡图（生日卡等），退回另一形态，界面至少有张图
                path = client.ensure_card_image(card, not trained)
            return path

    def finish(self, key: str, path: str) -> None:
        self._cache[key] = path
        self._pending.discard(key)
        self.loaded.emit(key, path)

    @staticmethod
    def key_of(card_id: int, trained: bool) -> str:
        return f"{card_id}:{int(trained)}"

    # ---- 对外 --------------------------------------------------------

    def reset(self, settings: Settings) -> None:
        """切换服务器后重建（图片缓存目录变了）。"""
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
        self.settings = settings
        self._cache.clear()
        self._pixmaps.clear()
        self._pending.clear()

    def pixmap(self, card_id: int, trained: bool, size: QSize = THUMB_SIZE) -> QPixmap | None:
        """取已缓存的缩略图；没有则返回 None 并触发异步加载。"""
        key = self.key_of(card_id, trained)
        pm = self._pixmaps.get(key)
        if pm is not None:
            return pm
        path = self._cache.get(key)
        if path is None:
            return None
        if not path:
            return QPixmap()  # 空图，调用方据此显示占位符
        src = QPixmap(path)
        if src.isNull():
            return QPixmap()
        pm = src.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._pixmaps[key] = pm
        return pm

    def request(self, card: Card, trained: bool) -> None:
        key = self.key_of(card.id, trained)
        if key in self._cache or key in self._pending:
            return
        self._pending.add(key)
        self._pool.start(_ThumbTask(self, key, card, trained))

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None


# ---------------------------------------------------------------------
# 拖放区
# ---------------------------------------------------------------------


class DropZone(QFrame):
    """接受截图拖入的虚线区域。"""

    filesDropped = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("Card")
        self.setMinimumHeight(96)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        self._title = QLabel("把游戏截图拖到这里")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self._sub = QLabel("支持 PNG / JPG / WEBP，可一次拖入多张或整个文件夹")
        self._sub.setObjectName("Hint")
        self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._title)
        lay.addWidget(self._sub)
        self._set_active(False)

    def _set_active(self, active: bool) -> None:
        border = PALETTE["accent"] if active else PALETTE["border2"]
        bg = "#2a3050" if active else PALETTE["surface"]
        self.setStyleSheet(
            f"QFrame#Card {{ border: 2px dashed {border}; border-radius: 10px; background: {bg}; }}"
        )

    # ---- Qt 事件 -----------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_active(False)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._set_active(False)
        paths: list[str] = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                paths.append(local)
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()


# ---------------------------------------------------------------------
# 统计卡
# ---------------------------------------------------------------------


class StatCard(QFrame):
    """一个「大数字 + 小标题」的统计块。"""

    def __init__(self, label: str, value: str = "—", color: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self._value = QLabel(value)
        self._value.setObjectName("StatValue")
        if color:
            self._value.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {color};")
        self._label = QLabel(label)
        self._label.setObjectName("StatLabel")
        lay.addWidget(self._value)
        lay.addWidget(self._label)

    def set_value(self, value: str) -> None:
        self._value.setText(value)


# ---------------------------------------------------------------------
# 选卡对话框
# ---------------------------------------------------------------------


class CardPickerDialog(QDialog):
    """在卡池里手动挑一张卡（识别失败 / 候选都不对时的兜底）。

    两条快路径：按卡名/角色/乐队搜，或直接输卡号（纯数字会精确命中并自动选中）。
    卡号 = Bestdori 卡面详情页网址 ``bestdori.com/info/cards/1858`` 最后那个数字。
    """

    MAX_SHOWN = 400

    def __init__(self, catalog: Catalog, parent=None, initial: str = "") -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.chosen: tuple[int, bool] | None = None

        self.setWindowTitle("手动指定卡牌")
        self.resize(560, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(9)

        self.search = QLineEdit(initial)
        self.search.setPlaceholderText("卡名 / 角色名 / 乐队 / 卡号，留空显示全部")
        self.search.setClearButtonEnabled(True)
        lay.addWidget(self.search)

        self.count = QLabel("")
        self.count.setObjectName("Hint")
        lay.addWidget(self.count)

        self.trained = QCheckBox("以「特训后」形态登记")
        lay.addWidget(self.trained)

        self.list = QListWidget()
        lay.addWidget(self.list, 1)

        hint = QLabel(
            "不知道卡名？直接输卡号更快 —— 卡号就是 Bestdori 卡面详情页网址 "
            "bestdori.com/info/cards/1858 里最后那个数字，回车即选中。"
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.search.textChanged.connect(self._refill)
        self.search.returnPressed.connect(self._accept_exact)
        self.list.itemDoubleClicked.connect(lambda _i: self._accept())
        self._refill()

    def _refill(self) -> None:
        raw = self.search.text().strip()
        q = raw.lower()
        exact = int(raw) if raw.isdigit() else None

        cards = sorted(self.catalog.cards.values(), key=lambda c: -c.id)
        # 纯数字输入：精确命中的那张提到最前，免得被别的卡的模糊匹配顶掉
        if exact is not None and exact in self.catalog.cards:
            cards = [self.catalog.cards[exact]] + [c for c in cards if c.id != exact]

        self.list.clear()
        shown = 0
        for card in cards:
            info = self.catalog.describe(card.id, False)
            hay = f"{info['title']} {info['character']} {info['band']} {card.id}".lower()
            if q and q not in hay:
                continue
            item = QListWidgetItem(
                f"{info['character']} · {info['title']}   [{info['rarityLabel']} {info['attributeLabel']}]  #{card.id}"
            )
            color = ATTRIBUTE_COLOR.get(card.attribute)
            if color:
                item.setForeground(QColor(color))
            item.setData(Qt.ItemDataRole.UserRole, card.id)
            self.list.addItem(item)
            shown += 1
            if shown >= self.MAX_SHOWN:
                break

        if not shown:
            self.count.setText("没有匹配的卡 —— 换个关键词，或者直接输卡号")
            return

        self.count.setText(
            f"匹配 {shown} 张" + ("（只显示前 400，再输几个字缩小范围）"
                                if shown >= self.MAX_SHOWN else "")
        )
        # 卡号精确命中时直接选中它，回车/双击一步到位
        if exact is not None and exact in self.catalog.cards:
            self.list.setCurrentRow(0)

    def _accept_exact(self) -> None:
        """回车：列表里已选中就用它，否则把输入当卡号试一次。"""
        if self.list.currentItem() is not None:
            self._accept()
            return
        raw = self.search.text().strip().lstrip("#")
        if raw.isdigit() and int(raw) in self.catalog.cards:
            self.chosen = (int(raw), self.trained.isChecked())
            self.accept()

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.chosen = (int(item.data(Qt.ItemDataRole.UserRole)), self.trained.isChecked())
        self.accept()


def _QCheckBoxInline(text: str):  # 延迟导入，保持顶部 import 精简
    from PySide6.QtWidgets import QCheckBox as _CB

    return _CB(text)


# ---------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"color: {PALETTE['border']}; background: {PALETTE['border']}; max-height: 1px;")
    return f


def row(*widgets, spacing: int = 8, stretch_at: int | None = None) -> QHBoxLayout:
    """把若干控件横排。``stretch_at`` 指定在第几个之后插入弹簧。"""
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for i, w in enumerate(widgets):
        lay.addWidget(w)
        if stretch_at is not None and i == stretch_at:
            lay.addStretch(1)
    return lay


def status_color(status: str) -> str:
    return {
        "matched": PALETTE["success"],
        "ambiguous": PALETTE["warning"],
        "unknown": PALETTE["muted"],
    }.get(status, PALETTE["muted"])


def status_label(status: str) -> str:
    return {"matched": "已匹配", "ambiguous": "待确认", "unknown": "未识别"}.get(status, status)


# ---------------------------------------------------------------------
# 原图框选视图
# ---------------------------------------------------------------------


class AnnotatedScreenshotView(QFrame):
    """把整张游戏截图显示出来，并在识别出的卡面格上画框；点某格即选中它。

    交互上的三个要点：

    * 图片按**保持宽高比**缩放到控件内并居中（整张图始终可见，框不会跑到视野外）；
    * **滚轮缩放、按住拖动平移、双击复位** —— 整张 2400×1080 的截图缩到面板里
      每格只剩 35px，属性图标和星级根本看不清，人工校对必须能放大；
    * 点击做**命中检测**，把控件坐标反算回图片坐标后判断落在哪个格里。

    命中检测实现成纯函数 :meth:`index_at`，单测可以直接喂一个坐标进去，
    不用真的构造鼠标事件 —— 缩略图坐标换算很容易写错，值得单独测。
    缩放以**鼠标位置为锚点**（滚轮指向哪里就放大哪里），这是看图软件的惯例，
    反着来每次滚一下图都会"飘"。
    """

    #: 点到了第 index 个格；点空白处发 -1
    indexClicked = Signal(int)
    ZOOM_MIN = 1.0
    ZOOM_MAX = 12.0
    #: 按下后移动超过这个像素数才算"拖动"，否则当一次点击
    DRAG_THRESHOLD = 6.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setMinimumHeight(190)
        self.setMouseTracking(True)
        self._pixmap: QPixmap | None = None
        self._boxes: list[Box] = []
        self._statuses: list[str] = []
        self._current = -1
        self._label = "还没有识别结果。识别后这里会显示原图，并把识别出的卡面框出来。"
        self._hint_font = QFont()
        self._hint_font.setPointSize(10)
        #: 额外缩放因子（1.0 = 适应窗口）
        self._zoom = 1.0
        #: 相对"居中位置"的平移偏移（控件坐标）
        self._pan = QPointF(0.0, 0.0)
        self._press_pos: QPointF | None = None
        self._drag_origin: QPointF | None = None
        self._moved = False

    # ---- 数据 ----------------------------------------------------------

    def set_screenshot(self, path: str | Path | None) -> bool:
        """载入截图；返回是否成功。传 ``None`` 或读不到就退回占位状态。"""
        self._pixmap = None
        if path is not None:
            pm = QPixmap(str(path))
            if not pm.isNull():
                self._pixmap = pm
        self.reset_view()
        self.update()
        return self._pixmap is not None

    def set_boxes(self, boxes: Sequence[Box], statuses: Sequence[str] | None = None) -> None:
        self._boxes = list(boxes)
        self._statuses = list(statuses) if statuses is not None else ["unknown"] * len(self._boxes)
        self._current = -1
        self.update()

    def set_current(self, index: int) -> None:
        if index != self._current:
            self._current = index
            self.update()

    def clear(self) -> None:
        self._pixmap = None
        self._boxes = []
        self._statuses = []
        self._current = -1
        self.reset_view()
        self.update()

    @property
    def pixmap(self) -> QPixmap | None:
        """当前截图（调用方需要整图来裁格子）。"""
        return self._pixmap

    @property
    def boxes(self) -> list[Box]:
        return list(self._boxes)

    @property
    def current(self) -> int:
        return self._current

    # ---- 坐标换算（纯函数，便于单测）-----------------------------------

    def _fit_scale(self) -> float:
        """适应窗口的基础缩放比（不含用户缩放）。"""
        if self._pixmap is None or self._pixmap.isNull():
            return 1.0
        iw, ih = self._pixmap.width(), self._pixmap.height()
        if iw <= 0 or ih <= 0 or self.width() <= 0 or self.height() <= 0:
            return 1.0
        return min(self.width() / iw, self.height() / ih)

    def image_scale(self) -> tuple[float, QPointF]:
        """图片 -> 控件的缩放比与左上角偏移（含用户缩放与平移，居中留边）。"""
        if self._pixmap is None or self._pixmap.isNull():
            return 1.0, QPointF(0.0, 0.0)
        iw, ih = self._pixmap.width(), self._pixmap.height()
        if iw <= 0 or ih <= 0:
            return 1.0, QPointF(0.0, 0.0)
        scale = self._fit_scale() * self._zoom
        off = QPointF(
            (self.width() - iw * scale) / 2.0 + self._pan.x(),
            (self.height() - ih * scale) / 2.0 + self._pan.y(),
        )
        return scale, off

    def rect_of(self, index: int) -> QRectF:
        """第 index 个框在**控件坐标系**里的矩形。"""
        scale, off = self.image_scale()
        b = self._boxes[index]
        return QRectF(off.x() + b.x * scale, off.y() + b.y * scale, b.w * scale, b.h * scale)

    def index_at(self, pos: QPoint | QPointF) -> int:
        """控件坐标 -> 命中的格子下标；没命中返回 -1。"""
        p = QPointF(pos)
        for i in range(len(self._boxes)):
            if self.rect_of(i).contains(p):
                return i
        return -1

    # ---- 缩放 / 平移 ---------------------------------------------------

    def reset_view(self) -> None:
        """回到"适应窗口"的初始视图。"""
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()

    def _clamp_pan(self) -> None:
        """平移量限制在"图不会整个滑出视野"的范围内；图比视口小时强制居中。"""
        if self._pixmap is None or self._pixmap.isNull():
            self._pan = QPointF(0.0, 0.0)
            return
        scale = self._fit_scale() * self._zoom
        iw = self._pixmap.width() * scale
        ih = self._pixmap.height() * scale
        lim_x = max(0.0, (iw - self.width()) / 2.0)
        lim_y = max(0.0, (ih - self.height()) / 2.0)
        self._pan = QPointF(
            max(-lim_x, min(lim_x, self._pan.x())),
            max(-lim_y, min(lim_y, self._pan.y())),
        )

    def _zoom_at(self, pos: QPointF, factor: float) -> None:
        """以 ``pos``（控件坐标）为锚点缩放 ``factor`` 倍。

        锚点必须固定：滚轮指向图片的哪个位置，放大后那个位置还得在鼠标底下，
        否则每滚一下图都会"飘"走。做法是算出鼠标下的图片坐标，缩放后反推
        新的平移量让这个图片坐标仍落在鼠标下。
        """
        if self._pixmap is None or self._pixmap.isNull():
            return
        old_scale, old_off = self.image_scale()
        new_zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-9:
            return
        img_pt = QPointF((pos.x() - old_off.x()) / old_scale,
                         (pos.y() - old_off.y()) / old_scale)
        self._zoom = new_zoom
        new_scale = self._fit_scale() * self._zoom
        want_off = QPointF(pos.x() - img_pt.x() * new_scale,
                           pos.y() - img_pt.y() * new_scale)
        center_off = QPointF((self.width() - self._pixmap.width() * new_scale) / 2.0,
                             (self.height() - self._pixmap.height() * new_scale) / 2.0)
        self._pan = QPointF(want_off.x() - center_off.x(), want_off.y() - center_off.y())
        self._clamp_pan()
        self.update()

    # ---- 事件 ----------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self._pixmap is not None and event.angleDelta().y():
            self._zoom_at(event.position(), 1.25 if event.angleDelta().y() > 0 else 1 / 1.25)
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
            self._drag_origin = event.position()
            self._moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is not None and (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            delta = event.position() - self._drag_origin
            if not self._moved and delta.manhattanLength() > self.DRAG_THRESHOLD:
                self._moved = True
            if self._moved:
                self._pan += event.position() - self._drag_origin
                self._drag_origin = event.position()
                self._clamp_pan()
                self.update()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            if not self._moved:
                # 没拖动 —— 这是一次点击，做命中检测
                self.indexClicked.emit(self.index_at(self._press_pos))
            self._press_pos = None
            self._drag_origin = None
            self._moved = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor(PALETTE["bg"]))

        if self._pixmap is None:
            painter.setPen(QColor(PALETTE["muted"]))
            painter.setFont(self._hint_font)
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap),
                             self._label)
            painter.end()
            return

        scale, off = self.image_scale()
        target = QRectF(off.x(), off.y(),
                        self._pixmap.width() * scale, self._pixmap.height() * scale)
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))

        for i in range(len(self._boxes)):
            r = self.rect_of(i)
            picked = i == self._current
            color = QColor(status_color(self._statuses[i] if i < len(self._statuses) else "unknown"))
            if picked:
                painter.fillRect(r, QColor(color.red(), color.green(), color.blue(), 46))
            pen = QPen(color)
            pen.setWidth(3 if picked else 1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawRect(r)
            if picked:
                painter.setPen(QColor(20, 20, 26))
                painter.setBrush(QColor(PALETTE["accent"]))
                tag = f" {i + 1} "
                painter.drawRect(QRectF(r.x(), r.y(), 26.0, 16.0))
                painter.setPen(QColor(20, 20, 26))
                painter.drawText(QRectF(r.x(), r.y(), 26.0, 16.0),
                                 int(Qt.AlignmentFlag.AlignCenter), str(i + 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.end()


class ClickableLabel(QLabel):
    """点一下就能触发动作的图片标签（用于"点开看大图"）。"""

    clicked = Signal()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ImageZoomDialog(QDialog):
    """把一张图放大看 —— 滚轮缩放，图比窗口大时可以滚动。

    核对面板里那两张图只有 140px 见方，而卡面的属性图标、星级、以及"这张画
    到底是不是同一张"恰恰要靠细节判断 —— 那个尺寸根本看不清，所以必须能点开。
    """

    MIN_SCALE = 1.0
    MAX_SCALE = 10.0

    def __init__(self, pixmap: QPixmap, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._pixmap = pixmap
        self._scale = 1.0

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._label)
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setStyleSheet(
            f"background: {PALETTE['surface2']}; border: none;"
        )

        hint = QLabel(f"{pixmap.width()}×{pixmap.height()} ｜ 滚轮缩放 ｜ Esc 关闭")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color: {PALETTE['muted']};")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(6)
        lay.addWidget(self._scroll, 1)
        lay.addWidget(hint)

        side = max(pixmap.width(), pixmap.height())
        # 小图（卡面是 150px 量级）放大到看得清；本来就大的图就按原尺寸显示
        self._set_scale(max(1.0, min(4.0, 480.0 / max(1, side))))
        self.resize(min(920, int(side * self._scale) + 60),
                    min(780, int(side * self._scale) + 100))

    def _set_scale(self, scale: float) -> None:
        self._scale = max(self.MIN_SCALE, min(self.MAX_SCALE, scale))
        pm = self._pixmap.scaled(
            max(1, int(self._pixmap.width() * self._scale)),
            max(1, int(self._pixmap.height() * self._scale)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(pm)
        self._label.resize(pm.size())

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt 命名
        delta = event.angleDelta().y()
        if delta:
            self._set_scale(self._scale * (1.15 if delta > 0 else 1 / 1.15))
            event.accept()
        else:
            super().wheelEvent(event)


class NavRail(QFrame):
    """左侧窄导航栏 —— 微信 / 现代 App 那种「图标在上、文字在下」的竖排。

    为什么不用 QListWidget：它的 iconMode 做不出"图标上、文字下"的竖排，
    自绘又要自己处理 hover / 选中 / 键盘焦点。用一组 checkable 按钮 + 互斥
    按钮组最直接。

    **API 刻意对齐 QListWidget 的子集**（``count`` / ``setCurrentRow`` /
    ``currentRow`` / ``currentRowChanged``），这样调用方、截图脚本与既有
    测试一行都不用改。
    """

    #: 切换到了第 index 页
    currentRowChanged = Signal(int)

    def __init__(
        self,
        items: Sequence[tuple[str, str]],
        parent: QWidget | None = None,
    ) -> None:
        """:param items: ``[(图标字符, 标签), ...]``，顺序即页面顺序"""
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(84)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(4)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []

        for index, (icon, label) in enumerate(items):
            btn = QPushButton(f"{icon}\n{label}")
            btn.setObjectName("RailItem")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(label)
            btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._group.addButton(btn, index)
            self._buttons.append(btn)
            lay.addWidget(btn)

        lay.addStretch(1)
        self._group.idClicked.connect(self.setCurrentRow)

    # ---- QListWidget 兼容接口 ----------------------------------------

    def count(self) -> int:
        return len(self._buttons)

    def currentRow(self) -> int:
        return self._group.checkedId()

    def setCurrentRow(self, row: int) -> None:  # noqa: N802 - 对齐 Qt 命名
        if not 0 <= row < len(self._buttons):
            return
        if self._group.checkedId() == row:
            return
        self._buttons[row].setChecked(True)
        self.currentRowChanged.emit(row)

    def setItemToolTip(self, index: int, text: str) -> None:  # noqa: N802
        """给第 index 项设悬停提示（对齐 QListWidget 的用法）。"""
        if 0 <= index < len(self._buttons):
            self._buttons[index].setToolTip(text)


__all__ = [
    "AnnotatedScreenshotView",
    "DropZone",
    "StatCard",
    "ThumbnailLoader",
    "CardPickerDialog",
    "ClickableLabel",
    "NavRail",
    "ImageZoomDialog",
    "hline",
    "row",
    "status_color",
    "status_label",
    "THUMB_SIZE",
    "IMAGE_SUFFIXES",
]
