"""网格切分测试。

重点覆盖 ``auto`` 模式 —— 这是 CLI 的默认模式，也是最容易出问题的地方。
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import synth_sheet

from bestdori_helper.models import Catalog
from bestdori_helper.vision.detect import (
    MIN_CELL,
    REFINE_MIN_RATIO,
    Box,
    _active_extent,
    _content_bands,
    _count_from_period,
    _dominant_period,
    _edge_profiles,
    _grid_from_profile,
    content_mask,
    crop_box,
    detect_boxes,
    detect_card_area,
    refine_box,
    refine_boxes,
    split_by_gaps,
)


# ---------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------


def test_box_as_tuple() -> None:
    b = Box(x=10, y=20, w=30, h=40)
    assert b.as_tuple() == (10, 20, 40, 60)
    assert b.to_dict() == {"x": 10, "y": 20, "w": 30, "h": 40}


def test_content_bands_simple() -> None:
    profile = np.array([0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0], dtype=bool)
    bands = _content_bands(profile, min_gutter=2, min_size=3)
    assert len(bands) == 2
    assert bands[0] == (2, 8)
    assert bands[1] == (12, 22)


def test_content_bands_drops_tiny_runs() -> None:
    profile = np.zeros(60, dtype=bool)
    profile[10:12] = True  # 只有 2 像素，小于 min_size
    assert _content_bands(profile, min_size=5) == []


def test_content_bands_all_true() -> None:
    profile = np.ones(60, dtype=bool)
    assert _content_bands(profile, min_size=5) == [(0, 60)]


def test_content_bands_all_false() -> None:
    assert _content_bands(np.zeros(60, dtype=bool), min_size=5) == []


def test_count_from_period_rejects_too_small() -> None:
    assert _count_from_period(1000, 10) is None      # 周期小于 MIN_CELL
    assert _count_from_period(1000, None) is None
    assert _count_from_period(1000, 0) is None


def test_count_from_period_rejects_non_integer_ratio() -> None:
    """总宽不是周期的近似整数倍 → 那个自相关峰不是真周期，应拒绝。"""
    assert _count_from_period(1000, 400) is None    # 1000/400 = 2.5
    assert _count_from_period(1000, 300) is None    # 1000/300 = 3.33


def test_count_from_period_accepts_clean_multiples() -> None:
    assert _count_from_period(1000, 500) == 2
    assert _count_from_period(1000, 250) == 4
    assert _count_from_period(1000, 200) == 5


def test_count_from_period_rejects_single_cell() -> None:
    assert _count_from_period(100, 90) is None      # 只算得出 1 格


# ---------------------------------------------------------------------
# 显式行列（grid 模式）
# ---------------------------------------------------------------------


def test_explicit_grid_produces_exact_count(catalog: Catalog) -> None:
    ids = sorted(catalog.cards)[:8]
    img, _ = synth_sheet(catalog, ids, cols=4, rows=2)
    boxes = detect_boxes(img, rows=2, cols=4)
    assert len(boxes) == 8


def test_explicit_grid_covers_full_image(catalog: Catalog) -> None:
    """关掉间隙切分时，``grid`` 模式就是纯等分、铺满整幅图。"""
    ids = sorted(catalog.cards)[:6]
    img, _ = synth_sheet(catalog, ids, cols=3, rows=2)
    boxes = detect_boxes(img, rows=2, cols=3, prefer_gaps=False)
    assert boxes[0].x == 0 and boxes[0].y == 0
    assert boxes[-1].x + boxes[-1].w == img.width
    assert boxes[-1].y + boxes[-1].h == img.height


def test_explicit_grid_boxes_do_not_overlap(catalog: Catalog) -> None:
    ids = sorted(catalog.cards)[:6]
    img, _ = synth_sheet(catalog, ids, cols=3, rows=2)
    boxes = detect_boxes(img, rows=2, cols=3)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not (a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h)


# ---------------------------------------------------------------------
# auto 模式
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "cols,rows,gutter,border",
    [
        (4, 2, 6, 5),    # 常规：有背景间隙
        (4, 2, 0, 5),    # 卡框紧贴（早期实现会整块吞掉）
        (3, 3, 8, 5),
        (5, 2, 4, 2),    # 细边框
        (4, 2, 4, 12),   # 粗边框
        (6, 2, 6, 4),
        (2, 2, 10, 6),
    ],
)
def test_auto_grid_detects_correct_cell_count(
    catalog: Catalog, cols: int, rows: int, gutter: int, border: int
) -> None:
    ids = sorted(catalog.cards)[: cols * rows]
    img, _ = synth_sheet(catalog, ids, cols=cols, rows=rows, gutter=gutter, border=border)
    boxes = detect_boxes(img)
    assert len(boxes) == cols * rows, (
        f"{cols}x{rows} gutter={gutter} border={border} 自动切出 {len(boxes)} 格"
    )


def test_auto_grid_boxes_stay_in_bounds(catalog: Catalog) -> None:
    ids = sorted(catalog.cards)[:8]
    img, _ = synth_sheet(catalog, ids, cols=4, rows=2, gutter=6)
    for b in detect_boxes(img):
        assert 0 <= b.x and 0 <= b.y
        assert b.x + b.w <= img.width
        assert b.y + b.h <= img.height


def test_auto_grid_handles_ui_overlay(catalog: Catalog) -> None:
    """卡面上叠加了星级条 / 属性图标这类 UI，仍应正确切分。"""
    from PIL import ImageDraw

    ids = sorted(catalog.cards)[:8]
    img, _ = synth_sheet(catalog, ids, cols=4, rows=2, gutter=6)
    draw = ImageDraw.Draw(img)
    for b in detect_boxes(img, rows=2, cols=4):
        draw.rectangle([b.x, b.y + b.h - 12, b.x + b.w, b.y + b.h], fill=(10, 10, 16))
        draw.ellipse([b.x + 4, b.y + 4, b.x + 18, b.y + 18], fill=(255, 214, 90))
    assert len(detect_boxes(img)) == 8


def test_auto_falls_back_to_single_box_on_blank_image() -> None:
    from PIL import Image

    img = Image.new("RGB", (400, 300), (20, 20, 24))
    boxes = detect_boxes(img)
    assert len(boxes) == 1
    assert boxes[0].w == 400 and boxes[0].h == 300


def test_auto_period_detection_on_synthetic_grid(catalog: Catalog) -> None:
    """周期性检测应该测出与真实格宽接近的周期。"""
    ids = sorted(catalog.cards)[:12]
    img, _ = synth_sheet(catalog, ids, cols=6, rows=2, cell=(100, 75), border=4, gutter=6)
    rgb = np.asarray(img.convert("RGB"))
    col_edge, row_edge = _edge_profiles(rgb)
    p = _dominant_period(col_edge)
    assert p is not None
    assert abs(p - img.width / 6) < 20


def test_margin_expands_boxes(catalog: Catalog) -> None:
    ids = sorted(catalog.cards)[:4]
    img, _ = synth_sheet(catalog, ids, cols=2, rows=2)
    plain = detect_boxes(img, rows=2, cols=2, margin=0)
    grown = detect_boxes(img, rows=2, cols=2, margin=5)
    # 第一个框已经贴到左上角，x/y 会被 clamp 到 0，但尺寸一定变大
    assert grown[0].w > plain[0].w
    assert grown[0].h > plain[0].h
    assert grown[0].x <= plain[0].x and grown[0].y <= plain[0].y


def test_margin_does_not_escape_image(catalog: Catalog) -> None:
    ids = sorted(catalog.cards)[:4]
    img, _ = synth_sheet(catalog, ids, cols=2, rows=2)
    for b in detect_boxes(img, rows=2, cols=2, margin=20):
        assert b.x >= 0 and b.y >= 0
        assert b.x + b.w <= img.width
        assert b.y + b.h <= img.height


# ---------------------------------------------------------------------
# 空白外边距（真实截图才会暴露的问题）
# ---------------------------------------------------------------------


def test_active_extent_strips_blank_margins() -> None:
    profile = np.zeros(300)
    profile[40:260] = 50.0
    assert _active_extent(profile) == (40, 260)


def test_active_extent_none_when_flat() -> None:
    assert _active_extent(np.zeros(300)) is None


def test_active_extent_keeps_full_span_when_content_fills() -> None:
    """内容占满整幅图时不能被误切 —— 所以阈值按**峰值**取，不能按中位数。

    这里中位数是 50、峰值 200。若用中位数当基线，占多数的 50 会被判成
    "空白"，内容段就被切碎了。
    """
    profile = np.full(300, 50.0)
    profile[::7] = 200.0
    assert _active_extent(profile) == (0, 300)


def test_grid_from_profile_ignores_outer_margin() -> None:
    """有外边距时，比例校验要在剥掉边距后的内容段上算。

    这是拿真实卡图跑才暴露的缺陷：3 行网格高 288px、周期 130px，首尾各
    14px 留白，整幅图比例是 288/130 = 2.215，超出 0.18 容差被判成假周期，
    于是退回到较弱的纹理投影路径切错。剥掉边距后 260/130 = 2.0，正常通过。
    """
    period, count, pad = 130, 3, 14
    total = 2 * pad + count * period
    profile = np.zeros(total)
    for k in range(count + 1):
        profile[pad + k * period - 1] = 100.0

    grid = _grid_from_profile(profile, total)
    assert grid is not None
    got_count, got_period, phase = grid
    assert got_count == count
    assert got_period == period
    assert phase == pad  # 相位要回到整幅图的坐标系


def test_grid_from_profile_still_rejects_fake_period() -> None:
    """剥边距只是换了"总长"，比例校验本身仍然严格 —— 别把假周期也放进来。

    经典难例：内容段长 200px，卡面内部重复周期 90px 的比例是 2.22，
    必须被拒绝；只有 100px 才是真网格周期。
    """
    assert _count_from_period(200, 90) is None
    assert _count_from_period(200, 100) == 2


def test_auto_detects_grid_with_large_outer_margin(catalog: Catalog) -> None:
    """四周套一大圈留白（模拟真实截图的安全区）后仍要切对。"""
    from PIL import Image

    ids = sorted(catalog.cards)[:8]
    inner, _ = synth_sheet(catalog, ids, cols=4, rows=2, cell=(120, 90), border=5, gutter=6)
    pad = 40
    sheet = Image.new("RGB", (inner.width + 2 * pad, inner.height + 2 * pad), (18, 18, 26))
    sheet.paste(inner, (pad, pad))

    boxes = detect_boxes(sheet)
    assert len(boxes) == 8
    # 框应该落在内容区里，而不是被外边距带偏
    assert all(b.x >= pad - 8 and b.y >= pad - 8 for b in boxes)


# ---------------------------------------------------------------------
# 裁剪
# ---------------------------------------------------------------------


def test_crop_box_no_inset(catalog: Catalog) -> None:
    ids = sorted(catalog.cards)[:4]
    img, _ = synth_sheet(catalog, ids, cols=2, rows=2)
    b = Box(x=10, y=20, w=100, h=80)
    c = crop_box(img, b, inset=0.0)
    assert c.size == (100, 80)


def test_crop_box_with_inset() -> None:
    from PIL import Image

    img = Image.new("RGB", (200, 200), (0, 0, 0))
    c = crop_box(img, Box(0, 0, 100, 100), inset=0.1)
    assert c.size == (80, 80)


def test_detected_boxes_are_at_least_min_cell(catalog: Catalog) -> None:
    ids = sorted(catalog.cards)[:8]
    img, _ = synth_sheet(catalog, ids, cols=4, rows=2)
    for b in detect_boxes(img):
        assert b.w >= MIN_CELL and b.h >= MIN_CELL


# ---------------------------------------------------------------------
# 卡框贴合（refine）
# ---------------------------------------------------------------------
#
# 等分出来的格子含留白，上下边还可能压在相邻卡面上。refine 的职责是把格子
# 收缩到真正的卡框上。下面用「彩色方块 + 中性底色」直接构造几何关系 ——
# 这里测的是边界定位，不涉及卡面内容，所以不必用 synth_sheet。


def _canvas(w: int, h: int, bg: tuple[int, int, int] = (244, 244, 240)):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), bg)
    return img, ImageDraw.Draw(img)


@pytest.mark.parametrize(
    "bg",
    [(244, 244, 240), (18, 18, 26)],  # 奶白底 / 深色底都该认出来
)
def test_refine_shrinks_to_content(bg: tuple[int, int, int]) -> None:
    # 留白 15px / 格子 200px —— 与真实几何同量级（实测卡框占格子 83%~90%）
    img, d = _canvas(200, 200, bg)
    d.rectangle([15, 15, 184, 184], fill=(230, 90, 60))  # 170x170 的"卡框"

    out = refine_box(np.asarray(img), Box(0, 0, 200, 200))
    assert abs(out.x - 15) <= 3
    assert abs(out.y - 15) <= 3
    assert abs(out.w - 170) <= 4
    assert abs(out.h - 170) <= 4


def test_refine_caps_excessive_shrink() -> None:
    """要收掉超过 25% 时直接放弃。

    这种"内容只占格子中间一小块"的形状在真实卡面上不成立（卡框占 83%~90%），
    更可能是卡面中央的浅色平坦区被误判成了"卡框外的背景"。宁可不动，
    也不要切掉小半张卡。
    """
    img, d = _canvas(200, 200)
    d.rectangle([90, 90, 109, 109], fill=(230, 90, 60))  # 中心一小块内容
    b = Box(0, 0, 200, 200)
    assert refine_box(np.asarray(img), b) == b


def test_refine_excludes_neighbour_strip() -> None:
    """格子上下边压在邻居卡面上时，邻居那条边不能被切进来。

    这是本功能存在的理由：等分框的边界落在邻居卡面上，切出来就混了别的卡。
    这里把"上方邻居的一条边"画成跨过格子上边界的长条，refine 必须把它排除。
    """
    img, d = _canvas(200, 260)
    d.rectangle([20, 20, 139, 55], fill=(60, 90, 230))    # 上方邻居的边缘
    d.rectangle([20, 80, 139, 199], fill=(230, 90, 60))   # 本格真正的卡框

    out = refine_box(np.asarray(img), Box(0, 50, 200, 160))
    assert out.y >= 75, f"把上方邻居切进来了：y={out.y}"
    assert out.y + out.h <= 205
    # 本格内容没被切掉
    assert out.h >= 110


def test_refine_falls_back_when_no_boundary() -> None:
    """整幅图都是内容 —— 没有可见分界，必须原样退回而不是瞎切。"""
    from PIL import Image

    img = Image.new("RGB", (200, 200), (200, 40, 40))
    b = Box(0, 0, 200, 200)
    assert refine_box(np.asarray(img), b) == b


def test_refine_falls_back_on_flat_background() -> None:
    """整幅都是背景 —— 同样没有分界。"""
    from PIL import Image

    img = Image.new("RGB", (200, 200), (250, 250, 248))
    b = Box(0, 0, 200, 200)
    assert refine_box(np.asarray(img), b) == b


def test_refine_ignores_tiny_box() -> None:
    img, d = _canvas(200, 200)
    d.rectangle([20, 20, 179, 179], fill=(230, 90, 60))
    b = Box(10, 10, 30, 30)  # 小于 2*MIN_CELL
    assert refine_box(np.asarray(img), b) == b


def test_refine_box_stays_in_bounds() -> None:
    img, d = _canvas(300, 300)
    d.rectangle([30, 30, 269, 269], fill=(230, 90, 60))
    out = refine_box(np.asarray(img), Box(0, 0, 300, 300))
    assert out.x >= 0 and out.y >= 0
    assert out.x + out.w <= 300 and out.y + out.h <= 300


def test_refine_boxes_preserves_count_and_order() -> None:
    img, d = _canvas(400, 200)
    d.rectangle([10, 10, 179, 179], fill=(230, 90, 60))
    d.rectangle([210, 10, 379, 179], fill=(60, 160, 90))
    boxes = [Box(0, 0, 200, 200), Box(200, 0, 200, 200)]

    out = refine_boxes(img, boxes)
    assert len(out) == 2
    assert out[0].x < out[1].x  # 顺序不变
    assert refine_boxes(img, []) == []


def test_refine_boxes_on_synthetic_sheet(catalog: Catalog) -> None:
    """合成截图上也要收缩，但不能缩过头。"""
    ids = sorted(catalog.cards)[:8]
    img, _ = synth_sheet(catalog, ids, cols=4, rows=2)
    raw = detect_boxes(img, rows=2, cols=4)
    out = refine_boxes(img, raw)

    assert len(out) == len(raw)
    for a, b in zip(raw, out):
        assert b.w <= a.w and b.h <= a.h, "贴合后不该变大"
        assert b.w >= a.w * REFINE_MIN_RATIO, "贴合过头了"
        assert b.h >= a.h * REFINE_MIN_RATIO


@pytest.mark.parametrize(
    "color,is_content",
    [
        ((254, 254, 252), False),  # 奶白 UI 底
        ((255, 255, 255), False),
        ((18, 18, 26), False),     # 深色 UI 底
        ((0, 0, 0), False),
        ((255, 77, 77), True),     # 属性色卡框（纯色但高饱和，别被当背景）
        ((128, 128, 128), True),   # 中性灰 —— 卡面内容
    ],
)
def test_content_mask_classification(
    color: tuple[int, int, int], is_content: bool
) -> None:
    arr = np.array([[color]], dtype=np.uint8)
    assert bool(content_mask(arr)[0, 0]) is is_content


# ---------------------------------------------------------------------
# 按卡面实际位置切（背景间隙法）
# ---------------------------------------------------------------------
#
# 真实游戏截图里卡面网格是**嵌在 UI 中间**的：左侧菜单、顶栏、底部按钮。
# 这时"等分 + 贴合"救不回来 —— 整图等分框里全是 UI 内容，一个背景分界都
# 找不到（实测把 2400x1080 按 4x7 等分得到 343x270 的框，连菜单都算进去）。
#
# 下面用"四周有深色 UI 条、中间一块卡面网格"的合成图覆盖这个场景。


def _ui_screenshot(
    *,
    cols: int = 3,
    rows: int = 2,
    cell: int = 100,
    gap: int = 20,
    pad_top: int = 90,
    pad_left: int = 70,
):
    """造一张「卡面网格嵌在 UI 里」的图。

    返回 ``(图, 卡面网格左上角, cell, gap)``。
    """
    from PIL import Image, ImageDraw

    gw = cols * cell + (cols - 1) * gap
    gh = rows * cell + (rows - 1) * gap
    W, H = pad_left + gw + 60, pad_top + gh + 50
    img = Image.new("RGB", (W, H), (244, 244, 240))
    d = ImageDraw.Draw(img)

    # UI 条：顶栏 / 左侧栏 / 底栏（深色低饱和，同样会被 content_mask 判为背景）
    d.rectangle([0, 0, W, pad_top - 30], fill=(26, 26, 34))
    d.rectangle([0, 0, pad_left - 20, H], fill=(26, 26, 34))
    d.rectangle([0, H - 26, W, H], fill=(26, 26, 34))

    for r in range(rows):
        for c in range(cols):
            x = pad_left + c * (cell + gap)
            y = pad_top + r * (cell + gap)
            # 颜色刻意都取高饱和：饱和度接近背景阈值（40）的填充会被
            # content_mask 判成"背景"，那是判据的边界情况，不该混进这个测试
            d.rectangle([x, y, x + cell - 1, y + cell - 1],
                        fill=(200, 60 + c * 25, 80))
            d.rectangle([x + 10, y + 10, x + cell - 11, y + cell - 11],
                        fill=(70 + c * 40, 140, 210))
    return img, (pad_left, pad_top), cell, gap


def test_detect_card_area_skips_ui() -> None:
    """自动定位出来的区域要罩住卡面网格，而不是选中 UI 条。"""
    img, (x0, y0), cell, gap = _ui_screenshot()
    area = detect_card_area(np.asarray(img))
    assert area is not None
    assert area.x <= x0 and area.y <= y0
    assert area.x + area.w >= x0 + 3 * cell + 2 * gap - 8
    assert area.y + area.h >= y0 + 2 * cell + gap - 8


def test_gap_split_locates_cards_inside_ui() -> None:
    """不给 region、不给行列数，也要切在卡面实际位置上。"""
    img, (x0, y0), cell, gap = _ui_screenshot()
    boxes = detect_boxes(img)
    assert len(boxes) == 6
    for r in range(2):
        for c in range(3):
            b = boxes[r * 3 + c]
            assert abs(b.x - (x0 + c * (cell + gap))) <= 8, f"格({r},{c}) x={b.x}"
            assert abs(b.y - (y0 + r * (cell + gap))) <= 8, f"格({r},{c}) y={b.y}"
            assert abs(b.w - cell) <= 10
            assert abs(b.h - cell) <= 10


def test_gap_split_respects_given_grid_count() -> None:
    """调用方给的行列数与实际不符时，不能拿间隙切分的结果糊弄过去。"""
    img, _, _, _ = _ui_screenshot()
    assert len(detect_boxes(img, rows=2, cols=3)) == 6
    # 报 4x4=16 格而实际只有 6 格 —— 应该退回等分（也是 16 格）
    assert len(detect_boxes(img, rows=4, cols=4)) == 16


def test_prefer_gaps_false_falls_back_to_equal_split(catalog: Catalog) -> None:
    ids = sorted(catalog.cards)[:8]
    img, _ = synth_sheet(catalog, ids, cols=4, rows=2)
    plain = detect_boxes(img, rows=2, cols=4, prefer_gaps=False)
    snapped = detect_boxes(img, rows=2, cols=4, prefer_gaps=True)
    assert len(plain) == len(snapped) == 8
    assert plain[0].x == 0                      # 等分铺满整图
    assert snapped[0].x >= plain[0].x           # 间隙切分收缩到卡框上


def test_split_by_gaps_returns_none_without_gaps() -> None:
    """整幅都是内容、没有背景间隙时不该硬猜。"""
    from PIL import Image

    img = Image.new("RGB", (300, 200), (220, 90, 70))
    assert split_by_gaps(np.asarray(img), Box(0, 0, 300, 200)) is None


def test_split_by_gaps_returns_none_on_blank() -> None:
    from PIL import Image

    img = Image.new("RGB", (300, 200), (244, 244, 240))
    assert split_by_gaps(np.asarray(img), Box(0, 0, 300, 200)) is None


def test_detect_card_area_returns_none_on_blank() -> None:
    from PIL import Image

    img = Image.new("RGB", (400, 300), (244, 244, 240))
    assert detect_card_area(np.asarray(img)) is None


def test_detect_card_area_returns_none_when_single_band() -> None:
    """只有一条长背景带时定不出区域（卡面至少要有两行才谈得上"阵列"）。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 300), (244, 244, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 359, 179], fill=(220, 90, 70))
    assert detect_card_area(np.asarray(img)) is None
