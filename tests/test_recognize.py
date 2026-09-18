"""识别流水线测试（端到端，全部离线）。

用合成卡面建库，再把同样的合成卡面拼成模拟截图跑识别。
"""

from __future__ import annotations

import pytest
from conftest import synth_art, synth_sheet

from bestdori_helper.config import Settings
from bestdori_helper.models import Catalog
from bestdori_helper.vision.detect import Box
from bestdori_helper.vision.index import FingerprintIndex
from bestdori_helper.vision.recognize import (
    MATCH_THRESHOLD,
    UNKNOWN_FLOOR,
    RecognitionResult,
    recognize_crop,
    recognize_image,
)


# ---------------------------------------------------------------------
# 单张识别
# ---------------------------------------------------------------------


def test_recognize_crop_exact_match(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    target = card_ids[4]
    # 带上属性图标，让属性 hint 选对路
    attr = catalog.cards[target].attribute
    item = recognize_crop(synth_art(target, attribute=attr), synth_index, catalog)
    assert item.card_id == target
    assert item.status == "matched"
    assert item.confidence > MATCH_THRESHOLD
    assert item.matched_by


def test_recognize_crop_all_cards(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    wrong = []
    for cid in card_ids:
        attr = catalog.cards[cid].attribute
        item = recognize_crop(synth_art(cid, attribute=attr), synth_index, catalog)
        if item.card_id != cid:
            wrong.append((cid, item.card_id, item.status))
    assert not wrong, f"这些卡识别错了：{wrong}"


def test_recognize_crop_returns_candidates(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    item = recognize_crop(synth_art(card_ids[0]), synth_index, catalog)
    assert item.candidates
    assert item.candidates[0].card_id == item.card_id
    scores = [c.score for c in item.candidates]
    assert scores == sorted(scores, reverse=True), "候选应按分数降序"


def test_recognize_crop_unknown_image(synth_index: FingerprintIndex, catalog: Catalog) -> None:
    """库里没有的图（用完全不同的 seed 生成）应该报 unknown 而不是硬猜。"""
    item = recognize_crop(synth_art(99999), synth_index, catalog)
    # 要么 unknown，要么至少不是 matched
    assert item.status in {"unknown", "ambiguous"}


def test_recognize_crop_uses_explicit_box(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    box = Box(3, 7, 100, 80)
    item = recognize_crop(synth_art(card_ids[0]), synth_index, catalog, box=box)
    assert item.box == box


def test_recognize_crop_attribute_hint_toggle(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    """开/关属性提示都不应报错，且结果都应该是有效卡牌。"""
    for use in (True, False):
        item = recognize_crop(synth_art(card_ids[2]), synth_index, catalog, use_attribute_hint=use)
        assert item.card_id in catalog.cards


def test_recognize_crop_with_ocr_disabled(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    from bestdori_helper.vision.ocr import NullOcr

    cid = card_ids[0]
    attr = catalog.cards[cid].attribute
    item = recognize_crop(synth_art(cid, attribute=attr), synth_index, catalog, ocr=NullOcr(), use_ocr=False)
    assert item.card_id == cid
    assert item.ocr_lines == []


def test_recognize_crop_ocr_enabled_but_unavailable(
    synth_index: FingerprintIndex, catalog: Catalog, card_ids
) -> None:
    """装了 OCR 但引擎不可用时，应该静默降级，不影响指纹结果。"""
    from bestdori_helper.vision.ocr import NullOcr

    cid = card_ids[1]
    attr = catalog.cards[cid].attribute
    item = recognize_crop(synth_art(cid, attribute=attr), synth_index, catalog, ocr=NullOcr(), use_ocr=True)
    assert item.card_id == cid


# ---------------------------------------------------------------------
# 整图识别
# ---------------------------------------------------------------------


def test_recognize_image_grid_mode(synth_index: FingerprintIndex, catalog: Catalog, card_ids, tmp_path) -> None:
    ids = card_ids[:8]
    img, slots = synth_sheet(catalog, ids, cols=4, rows=2)
    p = tmp_path / "sheet.png"
    img.save(p)

    res = recognize_image(p, synth_index, catalog, mode="grid", rows=2, cols=4)
    assert len(res.items) == 8
    assert [i.card_id for i in res.items] == slots


def test_recognize_image_auto_mode(synth_index: FingerprintIndex, catalog: Catalog, card_ids, tmp_path) -> None:
    ids = card_ids[:6]
    img, slots = synth_sheet(catalog, ids, cols=3, rows=2)
    p = tmp_path / "sheet.png"
    img.save(p)

    res = recognize_image(p, synth_index, catalog, mode="auto")
    assert len(res.items) == 6
    assert [i.card_id for i in res.items] == slots


def test_recognize_image_auto_no_gutter(synth_index: FingerprintIndex, catalog: Catalog, card_ids, tmp_path) -> None:
    """卡框紧贴的布局 —— 早期版本在这里会整块吞掉。"""
    ids = card_ids[:8]
    img, slots = synth_sheet(catalog, ids, cols=4, rows=2, gutter=0)
    p = tmp_path / "sheet.png"
    img.save(p)

    res = recognize_image(p, synth_index, catalog, mode="auto")
    assert len(res.items) == 8
    assert [i.card_id for i in res.items] == slots


def test_recognize_image_single_mode(synth_index: FingerprintIndex, catalog: Catalog, card_ids, tmp_path) -> None:
    p = tmp_path / "one.png"
    synth_art(card_ids[3]).save(p)
    res = recognize_image(p, synth_index, catalog, mode="single")
    assert len(res.items) == 1
    assert res.items[0].card_id == card_ids[3]


def test_recognize_image_inset(synth_index: FingerprintIndex, catalog: Catalog, card_ids, tmp_path) -> None:
    ids = card_ids[:4]
    img, _ = synth_sheet(catalog, ids, cols=2, rows=2)
    p = tmp_path / "sheet.png"
    img.save(p)
    res = recognize_image(p, synth_index, catalog, mode="grid", rows=2, cols=2, inset=0.05)
    assert len(res.items) == 4
    assert all(i.card_id in catalog.cards for i in res.items)


def test_recognize_image_records_dimensions(synth_index: FingerprintIndex, catalog: Catalog, card_ids, tmp_path) -> None:
    img, _ = synth_sheet(catalog, card_ids[:4], cols=2, rows=2)
    p = tmp_path / "sheet.png"
    img.save(p)
    res = recognize_image(p, synth_index, catalog, mode="grid", rows=2, cols=2)
    assert res.width == img.width
    assert res.height == img.height


def test_recognize_image_missing_file(synth_index: FingerprintIndex, catalog: Catalog, tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        recognize_image(tmp_path / "nope.png", synth_index, catalog)


# ---------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------


def test_result_matched_property(synth_index: FingerprintIndex, catalog: Catalog, card_ids, tmp_path) -> None:
    img, _ = synth_sheet(catalog, card_ids[:4], cols=2, rows=2)
    p = tmp_path / "sheet.png"
    img.save(p)
    res = recognize_image(p, synth_index, catalog, mode="grid", rows=2, cols=2)
    assert len(res.matched) == len(res.items)


def test_result_to_dict(synth_index: FingerprintIndex, catalog: Catalog, card_ids, tmp_path) -> None:
    img, _ = synth_sheet(catalog, card_ids[:4], cols=2, rows=2)
    p = tmp_path / "sheet.png"
    img.save(p)
    res = recognize_image(p, synth_index, catalog, mode="grid", rows=2, cols=2)
    d = res.to_dict(catalog)
    assert d["count"] == 4
    assert d["matched"] == 4
    assert "card" in d["items"][0]
    assert d["items"][0]["card"]["character"]


def test_empty_result_to_dict() -> None:
    d = RecognitionResult(image="x.png").to_dict()
    assert d["count"] == 0 and d["matched"] == 0


# ---------------------------------------------------------------------
# 判定阈值：match / ambiguous / unknown 是三件事，别用一个阈值混起来
# ---------------------------------------------------------------------


def test_unknown_floor_is_below_match_threshold() -> None:
    """两个阈值回答不同问题：敢不敢自动采信 vs 有没有候选。

    合成一个阈值会出事 —— 实测真实截图上有 16 个格子分数落在
    [UNKNOWN_FLOOR, MATCH_THRESHOLD)，正确卡其实就是 top-1，只是分数偏低；
    用一个阈值会把它们全判成 unknown，用户连候选列表都看不到。
    """
    assert UNKNOWN_FLOOR < MATCH_THRESHOLD


def test_low_score_still_gets_candidates() -> None:
    """分数低于采信线但高于下限时，必须是 ambiguous（带候选），不是 unknown。"""
    assert UNKNOWN_FLOOR <= 0.62 < MATCH_THRESHOLD

    class _FakeIndex:
        def search(self, *_a, **_kw):
            from bestdori_helper.vision.index import Match

            return [Match(1, False, 0.62, 0.62, 0.62), Match(2, False, 0.60, 0.60, 0.60)]

    item = recognize_crop(synth_art(3), _FakeIndex(), _fake_catalog())  # type: ignore[arg-type]
    assert item.status == "ambiguous"
    assert item.candidates, "ambiguous 必须带候选列表"


def test_very_low_score_is_unknown() -> None:
    """低于下限才算"库里没有这张卡"。"""

    class _FakeIndex:
        def search(self, *_a, **_kw):
            from bestdori_helper.vision.index import Match

            return [Match(1, False, 0.40, 0.40, 0.40), Match(2, False, 0.39, 0.39, 0.39)]

    item = recognize_crop(synth_art(4), _FakeIndex(), _fake_catalog())  # type: ignore[arg-type]
    assert item.status == "unknown"


def _fake_catalog():
    from bestdori_helper.models import Card

    cards = {i: Card(id=i, character_id=1, rarity=4, attribute="cool",
                     prefix=["", "", "", f"卡{i}"], resource_set_name=f"res{i:06d}")
             for i in range(1, 6)}
    return Catalog(settings=Settings(), cards=cards, characters={}, bands={})


# ---------------------------------------------------------------------
# 判定阈值：match / ambiguous / unknown 是三件事，别用一个阈值混起来
# ---------------------------------------------------------------------


def test_unknown_floor_is_below_match_threshold() -> None:
    """两个阈值回答不同问题：敢不敢自动采信 vs 有没有候选。

    合成一个阈值会出事 —— 实测真实截图上有 16 个格子分数落在
    [UNKNOWN_FLOOR, MATCH_THRESHOLD)，正确卡其实就是 top-1，只是分数偏低；
    用一个阈值会把它们全判成 unknown，用户连候选列表都看不到。
    """
    assert UNKNOWN_FLOOR < MATCH_THRESHOLD


def test_low_score_still_gets_candidates() -> None:
    """分数低于采信线但高于下限时，必须是 ambiguous（带候选），不是 unknown。"""
    assert UNKNOWN_FLOOR <= 0.62 < MATCH_THRESHOLD

    class _FakeIndex:
        def search(self, *_a, **_kw):
            from bestdori_helper.vision.index import Match

            return [Match(1, False, 0.62, 0.62, 0.62), Match(2, False, 0.60, 0.60, 0.60)]

    item = recognize_crop(synth_art(3), _FakeIndex(), _fake_catalog())  # type: ignore[arg-type]
    assert item.status == "ambiguous"
    assert item.candidates, "ambiguous 必须带候选列表"


def test_very_low_score_is_unknown() -> None:
    """低于下限才算"库里没有这张卡"。"""

    class _FakeIndex:
        def search(self, *_a, **_kw):
            from bestdori_helper.vision.index import Match

            return [Match(1, False, 0.40, 0.40, 0.40), Match(2, False, 0.39, 0.39, 0.39)]

    item = recognize_crop(synth_art(4), _FakeIndex(), _fake_catalog())  # type: ignore[arg-type]
    assert item.status == "unknown"


def _fake_catalog():
    from bestdori_helper.models import Card

    cards = {i: Card(id=i, character_id=1, rarity=4, attribute="cool",
                     prefix=["", "", "", f"卡{i}"], resource_set_name=f"res{i:06d}")
             for i in range(1, 6)}
    return Catalog(settings=Settings(), cards=cards, characters={}, bands={})
