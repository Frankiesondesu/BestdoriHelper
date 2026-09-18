"""pytest 共享 fixture。

原则：**全部离线**。不联网、不依赖 Bestdori、不依赖预先存在的指纹库，
用程序合成的"假卡面"构造索引与目录，保证 CI 上也能跑。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestdori_helper.config import Server, Settings  # noqa: E402
from bestdori_helper.models import Band, Card, Catalog, Character  # noqa: E402
from bestdori_helper.vision.features import compute_features  # noqa: E402
from bestdori_helper.vision.index import FingerprintIndex  # noqa: E402


# ---------------------------------------------------------------------
# 合成卡面
# ---------------------------------------------------------------------


def synth_art(seed: int, size: tuple[int, int] = (240, 180), attribute: str | None = None) -> Image.Image:
    """生成一张"结构化的假卡面"。

    刻意用**平滑渐变 + 大色块**而不是纯噪声：纯噪声的 DCT 高频占优，
    pHash 会退化成几乎相同的值，测不出区分度。渐变 + 色块能同时拉开
    pHash 和颜色直方图的差异。

    ``attribute`` 为 ``powerful`` / ``happy`` / ``pure`` / ``cool`` 时，
    会在右上角画一个属性色的小图标——贴近真实游戏截图，让
    :func:`bestdori_helper.vision.ui_hints.estimate_attribute_icon` 能
    读到正确属性。测试里给 ``None``（默认）就不画图标。
    """
    rng = np.random.default_rng(seed)
    w, h = size

    base = rng.integers(40, 220, 3).astype(np.float64)
    alt = rng.integers(40, 220, 3).astype(np.float64)

    # 对角渐变
    yy, xx = np.mgrid[0:h, 0:w]
    t = ((xx / max(1, w - 1)) + (yy / max(1, h - 1))) / 2.0
    arr = base[None, None, :] * (1 - t[:, :, None]) + alt[None, None, :] * t[:, :, None]

    img = Image.fromarray(arr.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img)

    # 3~5 个随机的实心色块，位置/颜色由 seed 决定
    for _ in range(int(rng.integers(3, 6))):
        cx, cy = int(rng.integers(0, w)), int(rng.integers(0, h))
        rw, rh = int(rng.integers(w // 6, w // 2)), int(rng.integers(h // 6, h // 2))
        color = tuple(int(c) for c in rng.integers(0, 256, 3))
        if rng.random() < 0.5:
            draw.ellipse([cx, cy, cx + rw, cy + rh], fill=color)
        else:
            draw.rectangle([cx, cy, cx + rw, cy + rh], fill=color)

    # 属性色图标（贴近真实游戏截图）
    if attribute is not None:
        attr_colors = {
            "powerful": (220, 50, 50),
            "happy": (240, 180, 50),
            "pure": (60, 180, 90),
            "cool": (60, 110, 220),
        }
        c = attr_colors.get(attribute)
        if c is not None:
            # 图标放在右上 ROI (70-95%, 5-25%) 的中心附近，
            # 尺寸大到占 ROI 30%+，让 estimate_attribute_icon 能压过卡面主色
            cx = int(w * 0.82)
            cy = int(h * 0.15)
            r = 22
            draw.ellipse(
                [cx - r, cy - r, cx + r, cy + r], fill=c, outline=(255, 255, 255), width=4
            )

    return img


# ---------------------------------------------------------------------
# 目录与配置
# ---------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(server=Server.CN, home=tmp_path / "home")
    s.ensure_dirs()
    return s


@pytest.fixture
def catalog(settings: Settings) -> Catalog:
    """构造一个小型假卡池：3 个角色、2 支乐队、12 张卡。

    刻意让**同一角色有多张卡**且属性不同，方便测属性/星级过滤。
    """
    bands = {1: Band(id=1, names=["Poppin'Party"] * 5), 2: Band(id=2, names=["Roselia"] * 5)}
    chars = {
        1: Character(id=1, names=["户山 香澄"] * 5, band_id=1, color_code="#FF5522"),
        2: Character(id=2, names=["凑 友希那"] * 5, band_id=2, color_code="#0077DD"),
        3: Character(id=3, names=["市谷 有咲"] * 5, band_id=1, color_code="#FFDD44"),
    }
    cards: dict[int, Card] = {}
    attrs = ["powerful", "cool", "pure", "happy"]
    for i in range(12):
        cid = 100 + i
        cards[cid] = Card(
            id=cid,
            character_id=[1, 2, 3][i % 3],
            rarity=1 + (i % 5),
            attribute=attrs[i % 4],
            prefix=[f"卡名{i}"] * 5,
            resource_set_name=f"res{i:06d}",
            skill_id=i,
            card_type="permanent",
        )
    return Catalog(settings=settings, cards=cards, characters=chars, bands=bands)


@pytest.fixture
def synth_index(catalog: Catalog) -> FingerprintIndex:
    """用合成卡面构建一个指纹库，每个 card_id 一行（普通形态）。"""
    ids: list[int] = []
    ph, dh, hi, av = [], [], [], []
    for cid in sorted(catalog.cards):
        feats = compute_features(synth_art(cid))
        ids.append(cid)
        ph.append(feats.phash)
        dh.append(feats.dhash)
        hi.append(feats.hist)
        av.append(feats.avg_rgb)
    return FingerprintIndex(
        card_ids=np.asarray(ids, dtype=np.int32),
        trained=np.zeros(len(ids), dtype=bool),
        phash=np.stack(ph),
        dhash=np.stack(dh),
        hist=np.stack(hi),
        avg_rgb=np.stack(av),
        manifest={},
    )


@pytest.fixture
def card_ids(catalog: Catalog) -> list[int]:
    return sorted(catalog.cards)


# ---------------------------------------------------------------------
# 合成"游戏截图"
# ---------------------------------------------------------------------


def synth_sheet(
    catalog: Catalog,
    ids: list[int],
    *,
    cols: int,
    rows: int,
    cell: tuple[int, int] = (120, 90),
    border: int = 5,
    gutter: int = 6,
    bg: tuple[int, int, int] = (18, 18, 26),
    frame_colors: list[tuple[int, int, int]] | None = None,
) -> tuple[Image.Image, list[int | None]]:
    """把若干合成卡面拼成一张模拟游戏列表截图。

    返回 ``(图像, 槽位)``，槽位与网格一一对应，``None`` 表示空槽。
    """
    frame_colors = frame_colors or [(255, 77, 77), (77, 155, 255), (77, 217, 138), (255, 184, 77)]
    step_x = cell[0] + border * 2 + gutter
    step_y = cell[1] + border * 2 + gutter
    W = cols * step_x + gutter
    H = rows * step_y + gutter
    canvas = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(canvas)

    slots: list[int | None] = [None] * (cols * rows)
    for i, cid in enumerate(ids[: cols * rows]):
        # 把卡牌实际属性透传给 synth_art，让它画出属性色图标
        attr = catalog.cards[cid].attribute if cid in catalog.cards else None
        thumb = synth_art(cid, attribute=attr).resize(cell, Image.Resampling.LANCZOS)
        c, r = i % cols, i // cols
        x = gutter + c * step_x + border
        y = gutter + r * step_y + border
        col = frame_colors[i % len(frame_colors)]
        draw.rectangle([x - border, y - border, x + cell[0] + border - 1, y + cell[1] + border - 1], fill=col)
        canvas.paste(thumb, (x, y))
        slots[i] = cid
    return canvas, slots
