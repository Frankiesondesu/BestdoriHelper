"""GUI 冒烟测试。

用 Qt 的 ``offscreen`` 平台插件在无显示器环境下把主窗口真正构建出来，
并走一遍关键交互路径。目的不是测像素，而是保证：

* 界面构建不抛异常（导入路径、信号连接、控件引用都对）
* 表格填充 / 缩略图占位 / 状态刷新这些真实代码路径能跑通
* 「人工修正识别结果」会正确改写清单

未安装 PySide6 时整体跳过。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea  # noqa: E402

from bestdori_helper.config import Settings  # noqa: E402
from bestdori_helper.gui.app import MainWindow  # noqa: E402
from bestdori_helper.gui.theme import apply_theme  # noqa: E402
from bestdori_helper.gui.widgets import CardPickerDialog  # noqa: E402
from bestdori_helper.inventory.store import Inventory  # noqa: E402
from bestdori_helper.models import Band, Card, Catalog, Character, OwnedCard  # noqa: E402
from bestdori_helper.vision.detect import Box  # noqa: E402
from bestdori_helper.vision.index import Match  # noqa: E402
from bestdori_helper.vision.recognize import RecognitionResult, RecognizedItem  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    yield app


@pytest.fixture
def settings(tmp_path):
    s = Settings(server="cn", home=tmp_path / "home")
    s.ensure_dirs()
    return s


@pytest.fixture
def catalog(settings):
    cat = Catalog(settings=settings)
    cat.bands[1] = Band(id=1, names=["Poppin'Party", "Poppin'Party"])
    cat.bands[2] = Band(id=2, names=["Roselia", "Roselia"])
    cat.characters[1] = Character(id=1, names=["戸山香澄", "Kasumi", "香澄", "户山香澄"], band_id=1)
    cat.characters[2] = Character(id=2, names=["湊友希那", "Yukina", "友希那", "凑友希那"], band_id=2)
    cat.cards[101] = Card(id=101, character_id=1, rarity=4, attribute="powerful",
                          prefix=["初始", "Initial", "初始", "初始卡"], resource_set_name="res00101")
    cat.cards[102] = Card(id=102, character_id=2, rarity=3, attribute="cool",
                          prefix=["", "", "", "夜之诗"], resource_set_name="res00102")
    cat.cards[103] = Card(id=103, character_id=1, rarity=2, attribute="happy",
                          prefix=["", "", "", "日常"], resource_set_name="res00103")
    return cat


def _no_network(window: MainWindow) -> None:
    """屏蔽缩略图的网络下载：冒烟测试不该联网。"""
    window.thumb.request = lambda card, trained: None  # type: ignore[method-assign]


def _make_window(qapp, settings, catalog=None, inventory=None) -> MainWindow:
    win = MainWindow(settings)
    _no_network(win)
    if catalog is not None:
        win.state.catalog = catalog
    if inventory is not None:
        win.state.inventory = inventory
    win._refresh_state()
    return win


# ---------------------------------------------------------------------


def test_window_builds_with_empty_state(qapp, settings):
    win = _make_window(qapp, settings)
    try:
        assert win.stack.count() == 4
        assert win.nav.count() == 4
        # 没有卡池 / 指纹库时，识别按钮必须是禁用的
        assert not win.scan_btn.isEnabled()
        assert "尚未同步" in win.catalog_label.text()
        assert "尚无指纹库" in win.index_label.text()
    finally:
        win.close()


def test_inventory_table_fills_and_filters(qapp, settings, catalog):
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=101, trained=True, confidence=0.93, matched_by="fingerprint", confirmed=True))
    inv.add(OwnedCard(card_id=102, trained=False, confidence=0.71, matched_by="fingerprint", confirmed=False))
    inv.add(OwnedCard(card_id=103, trained=False, confidence=0.55, matched_by="manual", confirmed=True))
    inv.save()

    win = _make_window(qapp, settings, catalog, inv)
    try:
        assert win.inv_table.rowCount() == 3
        assert win.stat_total._value.text() == "3"
        assert win.stat_trained._value.text() == "1"
        assert win.stat_pending._value.text() == "1"   # 只有 102 未确认
        assert win.stat_high._value.text() == "1"      # 只有 101 是 4★

        # 只看待确认
        win.inv_pending_cb.setChecked(True)
        assert win.inv_table.rowCount() == 1
        win.inv_pending_cb.setChecked(False)

        # 搜索过滤（按角色名）
        win.inv_search.setText("友希那")
        assert win.inv_table.rowCount() == 1
        win.inv_search.setText("")
        assert win.inv_table.rowCount() == 3
    finally:
        win.close()


def test_result_table_and_manual_correction(qapp, settings, catalog):
    """人工把错识别改成正确的卡，清单里也要跟着换掉。"""
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=102, trained=False, confidence=0.66, matched_by="fingerprint", confirmed=False))
    inv.save()

    win = _make_window(qapp, settings, catalog, inv)

    result = RecognitionResult(image="shot.png", width=400, height=300)
    result.items.append(
        RecognizedItem(
            box=Box(0, 0, 200, 150),
            card_id=102,
            trained=False,
            confidence=0.66,
            status="ambiguous",
            matched_by="fingerprint",
            candidates=[
                Match(card_id=102, trained=False, score=0.66, hash_similarity=0.7, color_similarity=0.6),
                Match(card_id=101, trained=True, score=0.65, hash_similarity=0.69, color_similarity=0.6),
            ],
        )
    )
    result.items.append(RecognizedItem(box=Box(200, 0, 400, 150), status="unknown"))

    win.state.scan_results = [result]
    try:
        win._reload_result_table()
        assert win.result_table.rowCount() == 2
        assert "确定匹配" in win.result_summary.text()

        # 选中第一行 -> 候选列表应有 2 条
        win.result_table.selectRow(0)
        assert win.cand_list.count() == 2

        # 应用第二个候选（101，特训后）—— 应该替换掉清单里的 102
        win.cand_list.setCurrentRow(1)
        win._apply_candidate()

        inv2 = Inventory.load(settings.inventory_path)
        assert inv2.get(101, True) is not None
        assert inv2.get(102, False) is None
        assert len(inv2) == 1

        # 表格已刷新，且该行显示为已匹配
        assert win.result_table.rowCount() == 2
        assert win.result_table.item(0, 0).text() == "已匹配"

        # 忽略第二行：状态回到未识别
        win.result_table.selectRow(1)
        win._ignore_row()
        assert win.state.scan_results[0].items[1].status == "unknown"
    finally:
        win.close()


def test_pending_paths_and_mode_toggle(qapp, settings, tmp_path):
    win = _make_window(qapp, settings)
    try:
        img = tmp_path / "a.png"
        from PIL import Image

        Image.new("RGB", (60, 40), (10, 20, 30)).save(img)

        win._add_paths([str(img)])
        assert win.file_list.count() == 1
        assert "1 张" in win.file_count_label.text()

        # 重复添加应被去重
        win._add_paths([str(img)])
        assert win.file_list.count() == 1

        # grid 模式才放开行列输入
        assert not win.rows_spin.isEnabled()
        win.mode_combo.setCurrentIndex(1)
        assert win.rows_spin.isEnabled() and win.cols_spin.isEnabled()
        win.mode_combo.setCurrentIndex(0)
        assert not win.rows_spin.isEnabled()

        win._clear_paths()
        assert win.file_list.count() == 0
    finally:
        win.close()


def test_import_plan_table_renders(qapp, settings, catalog):
    from bestdori_helper.bridge.bestdori_import import build_import_plan

    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=101, trained=False, confidence=0.9, matched_by="fingerprint", confirmed=True))
    inv.add(OwnedCard(card_id=102, trained=False, confidence=0.9, matched_by="fingerprint", confirmed=True))
    inv.save()

    win = _make_window(qapp, settings, catalog, inv)
    try:
        plan = build_import_plan(inv, catalog, remote_keys=None)
        win._on_plan_done(plan)
        assert win.plan_table.rowCount() == 2
        assert "待导入 2 张" in win.plan_label.text()

        # 只导入已确认的：这里两条都已确认，仍是 2
        plan2 = build_import_plan(inv, catalog, remote_keys=None, skip_unconfirmed=True)
        assert len(plan2) == 2
    finally:
        win.close()


def test_export_writes_file(qapp, settings, catalog, tmp_path, monkeypatch):
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=101, trained=False, confidence=0.9, matched_by="fingerprint", confirmed=True))
    inv.save()

    win = _make_window(qapp, settings, catalog, inv)
    out = tmp_path / "cards.csv"
    monkeypatch.setattr(
        "bestdori_helper.gui.app.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "")),
    )
    try:
        win._export("csv")
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        assert "101" in text and "角色" in text
    finally:
        win.close()


# ---------------------------------------------------------------------
# 原图框选 + 核对面板
# ---------------------------------------------------------------------


def _shot(path, size=(400, 300)):
    from PIL import Image

    Image.new("RGB", size, (26, 30, 44)).save(path)
    return path


def test_annotated_view_maps_geometry_back_to_index(qapp, tmp_path):
    """控件坐标 -> 格子下标。缩略图坐标换算最容易写错，单独测。"""
    from PySide6.QtCore import QPoint

    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 300)
    assert view.set_screenshot(_shot(tmp_path / "s.png", (400, 300)))
    view.set_boxes(
        [Box(10, 20, 100, 50), Box(200, 150, 80, 60)],
        ["matched", "ambiguous"],
    )
    # 控件与图片同尺寸 -> 1:1，且无偏移，坐标可直接推算
    scale, off = view.image_scale()
    assert scale == pytest.approx(1.0) and off.x() == 0 and off.y() == 0

    assert view.index_at(QPoint(60, 45)) == 0
    assert view.index_at(QPoint(240, 180)) == 1
    assert view.index_at(QPoint(5, 5)) == -1, "空白处不该命中"
    assert view.index_at(QPoint(150, 250)) == -1


def test_annotated_view_keeps_aspect_ratio(qapp, tmp_path):
    """宽图放进方控件：等比缩放 + 居中留边，框不会跑到视野外。"""
    from PySide6.QtCore import QPoint

    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 400)
    view.set_screenshot(_shot(tmp_path / "wide.png", (800, 400)))
    view.set_boxes([Box(0, 0, 800, 400)], ["unknown"])

    scale, off = view.image_scale()
    assert scale == pytest.approx(0.5)          # 被宽度限制
    assert off.y() > 0                          # 上下留边、竖直居中
    assert view.rect_of(0).width() == pytest.approx(400.0)
    assert view.index_at(QPoint(200, 200)) == 0


def test_mouse_click_emits_box_index(qapp, tmp_path):
    """真正的鼠标事件链路：mousePress -> 命中检测 -> indexClicked。"""
    pytest.importorskip("PySide6.QtTest")
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 300)
    view.set_screenshot(_shot(tmp_path / "s.png"))
    view.set_boxes([Box(0, 0, 200, 150), Box(200, 0, 200, 150)], ["matched", "unknown"])

    got: list[int] = []
    view.indexClicked.connect(got.append)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(100, 75))
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(300, 75))
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(5, 290))   # 空白
    assert got == [0, 1, -1]


def test_setting_new_boxes_resets_selection(qapp, tmp_path):
    """换张截图后选中态必须复位，否则会高亮到不存在的格子。"""
    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 300)
    view.set_screenshot(_shot(tmp_path / "s.png"))
    view.set_boxes([Box(0, 0, 200, 150)], ["matched"])
    assert view.current == -1
    view.set_current(0)
    assert view.current == 0

    view.set_boxes([Box(0, 0, 100, 100), Box(120, 0, 100, 100)], ["matched", "ambiguous"])
    assert view.current == -1


def test_result_page_annotation_and_card_url(qapp, settings, catalog, tmp_path):
    """结果页：原图框选数量对得上，选中后核对面板给出卡面详情页网址。"""
    from PIL import Image

    settings.thumb_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (180, 180), (200, 120, 90)).save(
        settings.thumb_dir / "res00101_normal.png"
    )

    win = _make_window(qapp, settings, catalog)
    result = RecognitionResult(image=str(_shot(tmp_path / "shot.png")), width=400, height=300)
    result.items.append(
        RecognizedItem(box=Box(0, 0, 200, 150), card_id=101, trained=False,
                       confidence=0.91, status="matched", matched_by="fingerprint+sift")
    )
    result.items.append(RecognizedItem(box=Box(200, 0, 200, 150), status="unknown"))
    win.state.scan_results = [result]
    try:
        win._reload_result_table()
        assert win.result_table.rowCount() == 2
        assert len(win.anno_view.boxes) == 2, "每个识别格都要在原图上框出来"

        # 点第二个框（未识别）-> 表格跟到第 2 行，但没有可核对的卡面页
        win._on_box_clicked(1)
        assert win.result_table.currentRow() == 1
        assert win.anno_view.current == 1
        assert not win.b_url.isEnabled()
        assert "没有可核对" in win.url_label.text()

        # 点第一个框 -> 出现 Bestdori 卡面详情页网址，两张核对图都有内容
        win._on_box_clicked(0)
        assert win.result_table.currentRow() == 0
        assert "https://bestdori.com/info/cards/101" in win.url_label.text()
        assert win.b_url.isEnabled()
        assert not win.cmp_card.pixmap().isNull(), "右侧应显示 Bestdori 卡图"
        assert not win.cmp_cell.pixmap().isNull(), "左侧应显示截图里裁出的卡面"

        # 点空白处不该改变选中
        win.anno_view.indexClicked.emit(-1)
        assert win.result_table.currentRow() == 0
    finally:
        win.close()


def test_detail_panel_does_not_overlap_on_small_windows(qapp, settings, catalog, tmp_path):
    """回归：核对面板里的图曾经被挤压出区间，压住下面的网址文字（实测 11px 重叠）。

    原因是面板内容的最小高度超过可用高度，嵌套的 QHBoxLayout 把 140x140
    的核对图撑出了自己的区间。现在面板套了 QScrollArea，空间不够就滚动。
    """
    from PIL import Image

    settings.thumb_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (180, 180), (90, 160, 220)).save(settings.thumb_dir / "res00101_normal.png")

    win = _make_window(qapp, settings, catalog)
    result = RecognitionResult(image=str(_shot(tmp_path / "shot.png")), width=400, height=300)
    for k in range(3):
        result.items.append(
            RecognizedItem(box=Box(k * 60, 0, 60, 60), card_id=101, trained=False,
                           confidence=0.7, status="matched", matched_by="sift",
                           candidates=[Match(101, False, 0.7, 0.7, 0.7, good=100, inliers=95)])
        )
    win.state.scan_results = [result]
    try:
        win._reload_result_table()
        for w, h in ((1500, 950), (1080, 720)):
            win.resize(w, h)
            win.show()
            qapp.processEvents()
            win.result_table.selectRow(0)
            qapp.processEvents()
            img_rect = win.cmp_cell.geometry()
            url_rect = win.url_label.geometry()
            assert img_rect.bottom() < url_rect.top(), (
                f"{w}x{h} 下核对图压住了网址文字：图底 {img_rect.bottom()} vs 文字顶 {url_rect.top()}"
            )
            assert win.url_edit.text() == "https://bestdori.com/info/cards/101"
    finally:
        win.close()


def test_detail_panel_does_not_overlap_on_small_windows(qapp, settings, catalog, tmp_path):
    """回归：核对面板里的图曾经被挤压出区间，压住下面的网址文字（实测 11px 重叠）。

    原因是面板内容的最小高度超过可用高度，嵌套的 QHBoxLayout 把 140x140
    的核对图撑出了自己的区间。现在面板套了 QScrollArea，空间不够就滚动。
    """
    from PIL import Image

    settings.thumb_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (180, 180), (90, 160, 220)).save(settings.thumb_dir / "res00101_normal.png")

    win = _make_window(qapp, settings, catalog)
    result = RecognitionResult(image=str(_shot(tmp_path / "shot.png")), width=400, height=300)
    for k in range(3):
        result.items.append(
            RecognizedItem(box=Box(k * 60, 0, 60, 60), card_id=101, trained=False,
                           confidence=0.7, status="matched", matched_by="sift",
                           candidates=[Match(101, False, 0.7, 0.7, 0.7, good=100, inliers=95)])
        )
    win.state.scan_results = [result]
    try:
        win._reload_result_table()
        for w, h in ((1500, 950), (1080, 720)):
            win.resize(w, h)
            win.show()
            qapp.processEvents()
            win.result_table.selectRow(0)
            qapp.processEvents()
            img_rect = win.cmp_cell.geometry()
            url_rect = win.url_label.geometry()
            assert img_rect.bottom() < url_rect.top(), (
                f"{w}x{h} 下核对图压住了网址文字：图底 {img_rect.bottom()} vs 文字顶 {url_rect.top()}"
            )
            assert win.url_edit.text() == "https://bestdori.com/info/cards/101"
    finally:
        win.close()


# ---------------------------------------------------------------------
# 原图框选视图的滚轮缩放 / 拖动平移（人工校对用）
# ---------------------------------------------------------------------


def test_anno_view_wheel_zoom_anchored_at_cursor(qapp, tmp_path):
    """滚轮缩放以鼠标位置为锚点：缩放前后，鼠标下的那个图片点不动。

    反着来的话每滚一下图都会"飘"，没法对着一个卡面慢慢放大核对。
    """
    from PySide6.QtCore import QPointF

    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 300)
    view.set_screenshot(_shot(tmp_path / "s.png", (400, 300)))
    view.set_boxes([Box(100, 100, 100, 100)], ["matched"])

    cursor = QPointF(150.0, 150.0)
    scale, off = view.image_scale()
    anchor = QPointF((cursor.x() - off.x()) / scale, (cursor.y() - off.y()) / scale)

    view._zoom_at(cursor, 2.0)
    assert view._zoom == pytest.approx(2.0)
    scale2, off2 = view.image_scale()
    assert scale2 == pytest.approx(scale * 2.0)
    after = QPointF((cursor.x() - off2.x()) / scale2, (cursor.y() - off2.y()) / scale2)
    assert after.x() == pytest.approx(anchor.x(), abs=1e-6)
    assert after.y() == pytest.approx(anchor.y(), abs=1e-6)
    # 放大后命中检测仍然按新几何来
    assert view.index_at(cursor) == 0


def test_anno_view_zoom_is_clamped(qapp, tmp_path):
    from PySide6.QtCore import QPointF

    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 300)
    view.set_screenshot(_shot(tmp_path / "s.png", (400, 300)))

    view._zoom_at(QPointF(200, 150), 1e6)
    assert view._zoom == AnnotatedScreenshotView.ZOOM_MAX
    view._zoom_at(QPointF(200, 150), 1e-9)
    assert view._zoom == AnnotatedScreenshotView.ZOOM_MIN
    # 回到 1x 时必须强制居中
    assert view._pan == QPointF(0.0, 0.0)


def test_anno_view_drag_pans_and_suppresses_click(qapp, tmp_path):
    """按住拖动 = 平移，不该被当成一次点击。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 300)
    view.set_screenshot(_shot(tmp_path / "s.png", (400, 300)))
    view.set_boxes([Box(0, 0, 200, 150)], ["matched"])

    got: list[int] = []
    view.indexClicked.connect(got.append)

    def ev(kind, pos):
        QApplication.sendEvent(
            view,
            QMouseEvent(kind, QPointF(pos), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier),
        )

    view._zoom_at(QPointF(200, 150), 4.0)     # 1x 时图正好铺满，没有平移余量
    before = QPointF(view._pan)
    ev(QEvent.Type.MouseButtonPress, QPointF(200, 150))
    ev(QEvent.Type.MouseMove, QPointF(240, 150))
    ev(QEvent.Type.MouseMove, QPointF(280, 150))
    ev(QEvent.Type.MouseButtonRelease, QPointF(280, 150))

    assert view._pan != before, "拖动应该平移视图"
    assert got == [], "拖动不该被当成点击"


def test_anno_view_double_click_resets_view(qapp, tmp_path):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 300)
    view.set_screenshot(_shot(tmp_path / "s.png", (400, 300)))
    view._zoom_at(QPointF(200, 150), 3.0)
    assert view._zoom == pytest.approx(3.0)

    QTest.mouseDClick(view, Qt.MouseButton.LeftButton)
    assert view._zoom == 1.0
    assert view._pan == QPointF(0.0, 0.0)


# ---------------------------------------------------------------------
# 原图框选视图的滚轮缩放 / 拖动平移（人工校对用）
# ---------------------------------------------------------------------


def test_anno_view_wheel_zoom_anchored_at_cursor(qapp, tmp_path):
    """滚轮缩放以鼠标位置为锚点：缩放前后，鼠标下的那个图片点不动。

    反着来的话每滚一下图都会"飘"，没法对着一个卡面慢慢放大核对。
    """
    from PySide6.QtCore import QPointF

    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 300)
    view.set_screenshot(_shot(tmp_path / "s.png", (400, 300)))
    view.set_boxes([Box(100, 100, 100, 100)], ["matched"])

    cursor = QPointF(150.0, 150.0)
    scale, off = view.image_scale()
    anchor = QPointF((cursor.x() - off.x()) / scale, (cursor.y() - off.y()) / scale)

    view._zoom_at(cursor, 2.0)
    assert view._zoom == pytest.approx(2.0)
    scale2, off2 = view.image_scale()
    assert scale2 == pytest.approx(scale * 2.0)
    after = QPointF((cursor.x() - off2.x()) / scale2, (cursor.y() - off2.y()) / scale2)
    assert after.x() == pytest.approx(anchor.x(), abs=1e-6)
    assert after.y() == pytest.approx(anchor.y(), abs=1e-6)
    # 放大后命中检测仍然按新几何来
    assert view.index_at(cursor) == 0


def test_anno_view_zoom_is_clamped(qapp, tmp_path):
    from PySide6.QtCore import QPointF

    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 300)
    view.set_screenshot(_shot(tmp_path / "s.png", (400, 300)))

    view._zoom_at(QPointF(200, 150), 1e6)
    assert view._zoom == AnnotatedScreenshotView.ZOOM_MAX
    view._zoom_at(QPointF(200, 150), 1e-9)
    assert view._zoom == AnnotatedScreenshotView.ZOOM_MIN
    # 回到 1x 时必须强制居中
    assert view._pan == QPointF(0.0, 0.0)


def test_anno_view_drag_pans_and_suppresses_click(qapp, tmp_path):
    """按住拖动 = 平移，不该被当成一次点击。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 300)
    view.set_screenshot(_shot(tmp_path / "s.png", (400, 300)))
    view.set_boxes([Box(0, 0, 200, 150)], ["matched"])

    got: list[int] = []
    view.indexClicked.connect(got.append)

    def ev(kind, pos):
        QApplication.sendEvent(
            view,
            QMouseEvent(kind, QPointF(pos), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier),
        )

    view._zoom_at(QPointF(200, 150), 4.0)     # 1x 时图正好铺满，没有平移余量
    before = QPointF(view._pan)
    ev(QEvent.Type.MouseButtonPress, QPointF(200, 150))
    ev(QEvent.Type.MouseMove, QPointF(240, 150))
    ev(QEvent.Type.MouseMove, QPointF(280, 150))
    ev(QEvent.Type.MouseButtonRelease, QPointF(280, 150))

    assert view._pan != before, "拖动应该平移视图"
    assert got == [], "拖动不该被当成点击"


def test_anno_view_double_click_resets_view(qapp, tmp_path):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    from bestdori_helper.gui.widgets import AnnotatedScreenshotView

    view = AnnotatedScreenshotView()
    view.resize(400, 300)
    view.set_screenshot(_shot(tmp_path / "s.png", (400, 300)))
    view._zoom_at(QPointF(200, 150), 3.0)
    assert view._zoom == pytest.approx(3.0)

    QTest.mouseDClick(view, Qt.MouseButton.LeftButton)
    assert view._zoom == 1.0
    assert view._pan == QPointF(0.0, 0.0)


def test_result_page_switches_between_multiple_shots(qapp, settings, catalog, tmp_path):
    """一次识别多张截图时，能切到后面的图 —— 不只是第一张。

    曾经的 bug：点原图里的框时，用的是"当前截图内的格子序号"，却被当成
    结果表的全局行号。结果表是**所有截图**的条目拼起来的，于是点第 2 张图的
    第 2 格会选中第 1 张图的第 2 行，左边又跳回第一张（用户反馈"多张图片时
    只能看第一张"）。
    """
    from bestdori_helper.gui.app import MainWindow

    win = _make_window(qapp, settings, catalog)
    try:
        results = []
        for i in range(2):
            res = RecognitionResult(
                image=str(_shot(tmp_path / f"shot{i}.png")), width=400, height=300
            )
            for j in range(3):
                res.items.append(
                    RecognizedItem(box=Box(j * 130, 0, 120, 150), status="unknown")
                )
            results.append(res)
        win.state.scan_results = results
        win._reload_result_table()

        assert win.result_table.rowCount() == 6
        assert win.shot_combo.count() == 2, "多张截图时切换器要列出每一张"
        assert win._anno_result_index == 0

        # 切到第 2 张
        win.shot_combo.setCurrentIndex(1)
        qapp.processEvents()
        assert win._anno_result_index == 1
        assert len(win.anno_view.boxes) == 3

        # 点第 2 张图里的第 2 个框 -> 必须选中第 2 张图的那一行（row=4），
        # 不能退回第一张
        win._on_box_clicked(1)
        qapp.processEvents()
        row = win.result_table.currentRow()
        assert win._scan_rows[row] == (1, 1), f"选中了错误的截图行 {win._scan_rows[row]}"
        assert win._anno_result_index == 1, "不该跳回第一张图"
    finally:
        win.close()


def test_result_page_switches_between_multiple_shots(qapp, settings, catalog, tmp_path):
    """一次识别多张截图时，能切到后面的图 —— 不只是第一张。

    曾经的 bug：点原图里的框时，用的是"当前截图内的格子序号"，却被当成
    结果表的全局行号。结果表是**所有截图**的条目拼起来的，于是点第 2 张图的
    第 2 格会选中第 1 张图的第 2 行，左边又跳回第一张（用户反馈"多张图片时
    只能看第一张"）。
    """
    from bestdori_helper.gui.app import MainWindow

    win = _make_window(qapp, settings, catalog)
    try:
        results = []
        for i in range(2):
            res = RecognitionResult(
                image=str(_shot(tmp_path / f"shot{i}.png")), width=400, height=300
            )
            for j in range(3):
                res.items.append(
                    RecognizedItem(box=Box(j * 130, 0, 120, 150), status="unknown")
                )
            results.append(res)
        win.state.scan_results = results
        win._reload_result_table()

        assert win.result_table.rowCount() == 6
        assert win.shot_combo.count() == 2, "多张截图时切换器要列出每一张"
        assert win._anno_result_index == 0

        # 切到第 2 张
        win.shot_combo.setCurrentIndex(1)
        qapp.processEvents()
        assert win._anno_result_index == 1
        assert len(win.anno_view.boxes) == 3

        # 点第 2 张图里的第 2 个框 -> 必须选中第 2 张图的那一行（row=4），
        # 不能退回第一张
        win._on_box_clicked(1)
        qapp.processEvents()
        row = win.result_table.currentRow()
        assert win._scan_rows[row] == (1, 1), f"选中了错误的截图行 {win._scan_rows[row]}"
        assert win._anno_result_index == 1, "不该跳回第一张图"
    finally:
        win.close()


def test_nav_rail_matches_qlistwidget_api(qapp):
    """窄导航的接口要对齐 QListWidget 子集，调用方与既有脚本才不用改。

    ——真实踩过：把 self.nav 从 QListWidget 换成自绘控件时，如果接口不一样，
    测试里的 nav.count()、脚本里的 nav.setCurrentRow() 会一起炸。
    """
    from bestdori_helper.gui.widgets import NavRail

    rail = NavRail([("A", "一"), ("B", "二"), ("C", "三")])
    assert rail.count() == 3
    assert rail.currentRow() == -1, "初始不该有选中项"

    got: list[int] = []
    rail.currentRowChanged.connect(got.append)
    rail.setCurrentRow(2)
    assert rail.currentRow() == 2
    assert got == [2]

    rail.setCurrentRow(2)          # 重复设置同一行不该再发信号
    assert got == [2]
    rail.setCurrentRow(99)         # 越界忽略
    assert rail.currentRow() == 2
    rail.setCurrentRow(-1)
    assert rail.currentRow() == 2


def test_main_window_nav_has_four_pages(qapp, settings, catalog):
    """主窗口的窄导航四项，点第 N 项切到第 N 页。"""
    win = _make_window(qapp, settings, catalog)
    try:
        assert win.nav.count() == 4
        for row in range(4):
            win.nav.setCurrentRow(row)
            qapp.processEvents()
            assert win.stack.currentIndex() == row
    finally:
        win.close()


def test_nav_rail_matches_qlistwidget_api(qapp):
    """窄导航的接口要对齐 QListWidget 子集，调用方与既有脚本才不用改。

    ——真实踩过：把 self.nav 从 QListWidget 换成自绘控件时，如果接口不一样，
    测试里的 nav.count()、脚本里的 nav.setCurrentRow() 会一起炸。
    """
    from bestdori_helper.gui.widgets import NavRail

    rail = NavRail([("A", "一"), ("B", "二"), ("C", "三")])
    assert rail.count() == 3
    assert rail.currentRow() == -1, "初始不该有选中项"

    got: list[int] = []
    rail.currentRowChanged.connect(got.append)
    rail.setCurrentRow(2)
    assert rail.currentRow() == 2
    assert got == [2]

    rail.setCurrentRow(2)          # 重复设置同一行不该再发信号
    assert got == [2]
    rail.setCurrentRow(99)         # 越界忽略
    assert rail.currentRow() == 2
    rail.setCurrentRow(-1)
    assert rail.currentRow() == 2


def test_main_window_nav_has_four_pages(qapp, settings, catalog):
    """主窗口的窄导航四项，点第 N 项切到第 N 页。"""
    win = _make_window(qapp, settings, catalog)
    try:
        assert win.nav.count() == 4
        for row in range(4):
            win.nav.setCurrentRow(row)
            qapp.processEvents()
            assert win.stack.currentIndex() == row
    finally:
        win.close()


# ---------------------------------------------------------------------
# 「候选都不对」的兜底路径
#
# 用户报的问题：识别错了只能从候选里挑，候选全不对时就没路可走了。
# 这两条测试盯的就是那条兜底路径 —— 按卡号改判、按卡池搜索改判。
# ---------------------------------------------------------------------


def _settle(ms: int = 100) -> None:
    """跑一小段事件循环，让布局结算完再量几何。

    **只调 processEvents() 不够**：QScrollArea 视口尺寸变化是延迟投递的，
    布局没算完就量会读到初始值 —— 实测同一个按钮量到 x=509（超界），
    而真实事件循环跑完后是 x=284（正常）。布局类断言必须先 qWait。
    """
    from PySide6.QtTest import QTest

    QTest.qWait(ms)


def _one_item_result(card_id: int, cands: list[int]) -> RecognitionResult:
    """造一张只有一个格子、候选指定的识别结果。"""
    result = RecognitionResult(image="shot.png", width=400, height=300)
    result.items.append(
        RecognizedItem(
            box=Box(0, 0, 200, 150),
            card_id=card_id,
            trained=False,
            confidence=0.66,
            status="ambiguous",
            matched_by="fingerprint",
            candidates=[
                Match(card_id=c, trained=False, score=0.66 - i * 0.01,
                      hash_similarity=0.7, color_similarity=0.6)
                for i, c in enumerate(cands)
            ],
        )
    )
    return result


def test_quick_pick_by_card_id_fixes_misrecognition(qapp, settings, catalog):
    """候选里没有正确答案时，直接输卡号改判 —— 并同步改写清单。"""
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=102, trained=False, confidence=0.66,
                      matched_by="fingerprint", confirmed=False))
    inv.save()

    win = _make_window(qapp, settings, catalog, inv)
    try:
        # 候选只有 101 / 102，正确答案 103 压根不在里面
        win.state.scan_results = [_one_item_result(102, [101, 102])]
        win._reload_result_table()
        win.result_table.selectRow(0)
        assert [c.card_id for c in win.state.scan_results[0].items[0].candidates] == [101, 102]

        win.quick_id.setText("103")
        win.quick_id.returnPressed.emit()

        item = win.state.scan_results[0].items[0]
        assert item.card_id == 103
        assert item.matched_by == "manual"
        assert win.quick_id.text() == "", "改判后输入框该清空，方便接着改下一格"

        inv2 = Inventory.load(settings.inventory_path)
        assert inv2.get(103, False) is not None, "清单里要换成 103"
        assert inv2.get(102, False) is None, "旧的错卡要从清单里移除"
        assert len(inv2) == 1
    finally:
        win.close()


def test_quick_pick_accepts_hash_prefix_and_rejects_unknown(qapp, settings, catalog, monkeypatch):
    """``#103`` 这种带井号的写法要认；卡号不存在时只警告、不动清单。"""
    inv = Inventory(settings.inventory_path)
    inv.save()
    win = _make_window(qapp, settings, catalog, inv)
    warned: list[tuple] = []
    # 两个都要挡掉 —— 只挡 warning 的话，走到「先选一格」那支会弹模态框把测试挂死
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: warned.append(a))
    try:
        win.state.scan_results = [_one_item_result(102, [101, 102])]
        win._reload_result_table()
        win.result_table.selectRow(0)

        # 卡池里没有 9999 —— 应该拦下来
        win.quick_id.setText("9999")
        win.quick_id.returnPressed.emit()
        assert warned, "卡号不存在时必须弹警告"
        assert win.state.scan_results[0].items[0].card_id == 102, "不该被改掉"
        assert len(Inventory.load(settings.inventory_path)) == 0

        # 带 # 的卡号要能认
        win.quick_id.setText("#103")
        win.quick_id.returnPressed.emit()
        assert win.state.scan_results[0].items[0].card_id == 103

        # 非数字输入也要拦下来
        warned.clear()
        win.quick_id.setText("香澄")
        win.quick_id.returnPressed.emit()
        assert warned, "非纯数字输入该提示去用搜索"
        assert win.state.scan_results[0].items[0].card_id == 103, "不该被改掉"
    finally:
        win.close()


def test_correction_keeps_row_selected(qapp, settings, catalog):
    """改判后选中不能丢 —— 否则连改好几格要反复点行，第二次回车还会弹「先选一格」。"""
    inv = Inventory(settings.inventory_path)
    inv.save()
    win = _make_window(qapp, settings, catalog, inv)
    try:
        win.state.scan_results = [_one_item_result(102, [101, 102])]
        win._reload_result_table()
        win.result_table.selectRow(0)

        win.quick_id.setText("103")
        win.quick_id.returnPressed.emit()

        assert win.result_table.currentRow() == 0, "改判后应停在原来那一行"
        assert win._current_scan_item() is not None, "选中丢了的话下一次改判会弹「先选一格」"

        # 紧接着再改一次，不该有任何提示
        win.quick_id.setText("101")
        win.quick_id.returnPressed.emit()
        assert win.state.scan_results[0].items[0].card_id == 101
    finally:
        win.close()


def test_card_picker_dialog_puts_exact_id_first(qapp, catalog):
    """手动指定对话框：输纯数字卡号 -> 精确命中排第一并自动选中，回车即用。"""
    dlg = CardPickerDialog(catalog)
    try:
        assert dlg.list.count() == 3, "留空时列出全部卡"

        dlg.search.setText("103")
        assert dlg.list.currentRow() == 0, "精确命中要自动选中"
        assert dlg.list.item(0).data(Qt.ItemDataRole.UserRole) == 103
        assert "匹配" in dlg.count.text()

        dlg._accept()
        assert dlg.chosen == (103, False)

        # 勾上「特训后」再选一次
        dlg2 = CardPickerDialog(catalog)
        try:
            dlg2.search.setText("101")
            dlg2.trained.setChecked(True)
            dlg2._accept()
            assert dlg2.chosen == (101, True)
        finally:
            dlg2.close()
    finally:
        dlg.close()


def test_card_picker_dialog_searches_by_name(qapp, catalog):
    """按角色名搜也要能用（不知道卡号时的另一条路）。"""
    dlg = CardPickerDialog(catalog)
    try:
        dlg.search.setText("友希那")
        ids = [dlg.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dlg.list.count())]
        assert ids == [102], f"只有 102 属于友希那，实际 {ids}"

        dlg.search.setText("不存在的卡")
        assert dlg.list.count() == 0
        assert "没有匹配" in dlg.count.text()
    finally:
        dlg.close()


def test_escape_hatch_stays_inside_visible_panel(qapp, settings, catalog, monkeypatch):
    """候选行右侧的「都不是？搜卡面」必须落在侧栏可视区内。

    这条测的是**布局不变量**，不是逻辑。右侧核对面板是 QScrollArea，
    内容约 890px 而视口只有 ~530px —— 排在候选列表下方的任何按钮都会被滚出
    可视区。用户报的「候选都不对就没辙了」根因之一就在这里：出口存在但看不见。
    顺带盯住水平方向：核对图从 140 放大到 190 后，面板最小宽度没跟着改，
    曾长期有 23px 横向溢出，把靠右的按钮裁掉一截。
    """
    win = _make_window(qapp, settings, catalog)
    # 按钮点下去会开模态对话框（dlg.exec()），测试里不能真开 —— 换替身只验接线
    opened: list[bool] = []
    monkeypatch.setattr(MainWindow, "_pick_card_for_row",
                        lambda self: opened.append(True))
    try:
        win.state.scan_results = [_one_item_result(102, [101, 102, 103])]
        win._reload_result_table()
        win.result_table.selectRow(0)
        # 必须切到「结果」页 —— 侧栏在 QStackedWidget 的隐藏页里，
        # 不切过去布局根本不会计算，量到的全是初始几何（按钮 x 会量成 509）
        win.nav.setCurrentRow(1)
        win.resize(1400, 900)
        win.show()
        _settle()

        side = win.findChild(QScrollArea, "PanelScroll")
        assert side is not None, "找不到右侧核对面板"
        vp = side.viewport()
        pos = win.b_miss.mapTo(vp, QPoint(0, 0))

        assert pos.y() + win.b_miss.height() <= vp.height(), (
            f"「都不是？搜卡面」底边 {pos.y() + win.b_miss.height()} "
            f"超出可视区高 {vp.height()} —— 用户看不到出口")
        assert pos.x() + win.b_miss.width() <= vp.width(), (
            f"按钮右边 {pos.x() + win.b_miss.width()} 超出可视区宽 {vp.width()} "
            f"—— 被裁掉一截")
        assert win.b_miss.isVisible()

        win.b_miss.click()
        qapp.processEvents()
        assert opened, "「都不是？搜卡面」没接到改判入口上"
        assert win._current_scan_item() is not None, "点出口不该丢掉当前选中格"
    finally:
        win.close()


def test_side_panel_has_no_horizontal_overflow(qapp, settings, catalog):
    """侧栏不该出现横向滚动条 —— 出横向滚动条就说明内容比视口宽，右侧控件被裁。"""
    win = _make_window(qapp, settings, catalog)
    try:
        win.state.scan_results = [_one_item_result(102, [101])]
        win._reload_result_table()
        win.result_table.selectRow(0)
        win.nav.setCurrentRow(1)      # 同上：不切到结果页，布局不会计算
        win.resize(1400, 900)
        win.show()
        _settle()

        side = win.findChild(QScrollArea, "PanelScroll")
        inner = side.widget()
        assert inner.width() <= side.viewport().width(), (
            f"内容宽 {inner.width()} > 视口宽 {side.viewport().width()}，"
            f"会出现横向滚动条")
        # 两张核对图也得完整放得下
        for lab in (win.cmp_cell, win.cmp_card):
            assert lab.width() == lab.sizeHint().width(), "核对图被压缩了，看不清属性图标"
    finally:
        win.close()
