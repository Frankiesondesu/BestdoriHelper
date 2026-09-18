"""属性提示（``vision/ui_hints.py``）测试。

回归动机
--------
``estimate_attribute_icon`` 原来用 ``(sat >= 60) & (val <= 245)`` 筛像素 ——
那个 ``val <= 245`` 本意是"排掉白色面板"，但**四属性标准色里三个的 V 就是
255**（``#FF4D4D`` / ``#4D9BFF`` / ``#FFB84D``），图标本体被当成白色滤掉，
剩下的全是卡面画色 → 提示会**自信地报出错误属性**，把正确答案从候选集里筛走。

白色本身 ``sat ≈ 0``，靠饱和度已经排除了。这里的用例刻意用 ``ATTRIBUTE_COLOR``
里的**原始十六进制色**（含 V=255 的那三个），把这个坑钉死。

全部离线：图标是程序画上去的，不依赖任何真实卡图。
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from bestdori_helper.config import ATTRIBUTE_COLOR, Attribute
from bestdori_helper.vision.ui_hints import (
    ATTRIBUTE_HINT_MIN_CONF,
    ICON_ROI,
    estimate_attribute_icon,
)


def _hex_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def synth_card(attribute: str | None, *, size: tuple[int, int] = (180, 180)) -> Image.Image:
    """画一张假卡面：带干扰色块的底图 + 右上角属性图标。"""
    w, h = size
    rng = np.random.default_rng(7)
    arr = rng.integers(30, 210, (h, w, 3)).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(img)

    # 干扰：几个高饱和色块，模拟卡面本身的鲜艳配色
    for _ in range(4):
        cx, cy = int(rng.integers(0, w)), int(rng.integers(0, h))
        rw, rh = int(rng.integers(w // 5, w // 2)), int(rng.integers(h // 5, h // 2))
        draw.rectangle([cx, cy, cx + rw, cy + rh],
                       fill=tuple(int(c) for c in rng.integers(0, 256, 3)))

    if attribute is not None:
        # 图标放在 ICON_ROI 的中心，尺寸占 ROI 一半以上
        cx = int(w * (ICON_ROI[0] + ICON_ROI[2]) / 2)
        cy = int(h * (ICON_ROI[1] + ICON_ROI[3]) / 2)
        r = max(8, int(w * (ICON_ROI[2] - ICON_ROI[0]) * 0.45))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     fill=_hex_rgb(ATTRIBUTE_COLOR[attribute]))
    return img


#: 用 ATTRIBUTE_COLOR 里的原始色 —— 其中三个的 V 就是 255
ATTRS = [Attribute.POWERFUL.value, Attribute.COOL.value,
         Attribute.PURE.value, Attribute.HAPPY.value]


@pytest.mark.parametrize("attr", ATTRS)
def test_icon_hint_reads_pure_hue_icon(attr: str) -> None:
    """V=255 的纯色图标必须能被读到（旧实现在这里翻车）。"""
    hint = estimate_attribute_icon(synth_card(attr))
    assert hint.attribute == attr, f"{attr}: 读到 {hint.attribute}"
    assert hint.confidence >= ATTRIBUTE_HINT_MIN_CONF


@pytest.mark.parametrize("attr", ATTRS)
def test_icon_hint_confidence_is_meaningful(attr: str) -> None:
    """图标占 ROI 一半以上时，置信度不该贴着阈值线。"""
    hint = estimate_attribute_icon(synth_card(attr))
    assert hint.confidence > 0.5, f"{attr}: 置信度只有 {hint.confidence:.2f}"


def test_no_icon_yields_no_attribute() -> None:
    """没有图标就不该硬报一个属性出来。"""
    hint = estimate_attribute_icon(synth_card(None))
    # 随机色块可能偶尔凑出个属性，但置信度必须不高
    assert hint.confidence < ATTRIBUTE_HINT_MIN_CONF or hint.attribute is None


def test_white_background_is_not_mistaken_for_icon() -> None:
    """纯白面板不该被认成属性图标（这才是 ``val`` 过滤想防的情况）。"""
    img = Image.new("RGB", (180, 180), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 160, 160], fill=(250, 250, 252))  # 近白，带一点噪
    hint = estimate_attribute_icon(img)
    assert hint.attribute is None
    assert hint.confidence == 0.0


def test_icon_hint_scales_with_cell_size() -> None:
    """不同格子尺寸下都要能读到 —— 真实截图分辨率会变。"""
    for side in (110, 140, 170, 190, 240):
        hint = estimate_attribute_icon(synth_card(Attribute.COOL.value, size=(side, side)))
        assert hint.attribute == Attribute.COOL.value, f"边长 {side} 读错了"
