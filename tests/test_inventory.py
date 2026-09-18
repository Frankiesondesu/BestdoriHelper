"""清单存储测试：去重、增删、统计、与识别结果对接。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bestdori_helper.inventory.store import Inventory
from bestdori_helper.models import Catalog, OwnedCard
from bestdori_helper.vision.detect import Box
from bestdori_helper.vision.recognize import RecognizedItem, RecognitionResult


def _item(card_id, *, status="matched", conf=0.9, trained=False, matched_by="fingerprint") -> RecognizedItem:
    return RecognizedItem(
        box=Box(0, 0, 10, 10),
        card_id=card_id,
        trained=trained,
        confidence=conf,
        status=status,
        matched_by=matched_by,
    )


def _result(items, image="shot.png") -> RecognitionResult:
    return RecognitionResult(image=image, items=items, width=100, height=100)


# ---------------------------------------------------------------------
# 增删改查
# ---------------------------------------------------------------------


def test_add_and_len(settings) -> None:
    inv = Inventory(settings.inventory_path)
    assert inv.add(OwnedCard(card_id=100))
    assert len(inv) == 1


def test_add_dedupes_same_card_and_form(settings) -> None:
    inv = Inventory(settings.inventory_path)
    assert inv.add(OwnedCard(card_id=100, trained=False))
    assert not inv.add(OwnedCard(card_id=100, trained=False))
    assert len(inv) == 1


def test_same_card_different_form_are_separate(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=100, trained=False))
    inv.add(OwnedCard(card_id=100, trained=True))
    assert len(inv) == 2


def test_add_overwrite(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=100, confidence=0.5))
    assert inv.add(OwnedCard(card_id=100, confidence=0.99), overwrite=True)
    assert inv.get(100).confidence == pytest.approx(0.99)


def test_remove_by_id(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=100, trained=False))
    inv.add(OwnedCard(card_id=100, trained=True))
    assert inv.remove(100, trained=False) == 1
    assert inv.get(100, trained=True) is not None
    assert inv.get(100, trained=False) is None


def test_remove_all_forms(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=100, trained=False))
    inv.add(OwnedCard(card_id=100, trained=True))
    assert inv.remove(100) == 2
    assert len(inv) == 0


def test_remove_missing_returns_zero(settings) -> None:
    inv = Inventory(settings.inventory_path)
    assert inv.remove(999) == 0


def test_confirm(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=100, confirmed=False))
    inv.confirm(100, False)
    assert inv.get(100).confirmed is True


def test_confirm_missing_is_noop(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.confirm(999, False)  # 不应抛异常


def test_clear(settings) -> None:
    inv = Inventory(settings.inventory_path)
    for i in range(5):
        inv.add(OwnedCard(card_id=100 + i))
    assert inv.clear() == 5
    assert len(inv) == 0


def test_all_is_sorted(settings) -> None:
    inv = Inventory(settings.inventory_path)
    for cid in (300, 100, 200):
        inv.add(OwnedCard(card_id=cid))
    assert [c.card_id for c in inv.all()] == [100, 200, 300]


def test_merge(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=100))
    added = inv.merge([OwnedCard(card_id=100), OwnedCard(card_id=200)])
    assert added == 1
    assert len(inv) == 2


# ---------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------


def test_save_load_roundtrip(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=100, trained=True, confidence=0.88, matched_by="fingerprint+ocr", confirmed=True))
    inv.save()
    assert settings.inventory_path.exists()

    back = Inventory.load(settings.inventory_path)
    assert len(back) == 1
    oc = back.get(100, trained=True)
    assert oc is not None
    assert oc.confidence == pytest.approx(0.88)
    assert oc.matched_by == "fingerprint+ocr"
    assert oc.confirmed is True
    assert back.updated_at


def test_load_missing_returns_empty(settings) -> None:
    assert len(Inventory.load(settings.inventory_path)) == 0


def test_load_tolerates_corrupt_file(settings) -> None:
    settings.inventory_path.parent.mkdir(parents=True, exist_ok=True)
    settings.inventory_path.write_text("{ broken", encoding="utf-8")
    assert len(Inventory.load(settings.inventory_path)) == 0


def test_load_ignores_unknown_fields(settings) -> None:
    """旧版本写的清单多了字段，新版本读的时候不应炸。"""
    settings.inventory_path.parent.mkdir(parents=True, exist_ok=True)
    settings.inventory_path.write_text(
        '{"cards":[{"card_id":100,"trained":false,"未来字段":"x"}]}', encoding="utf-8"
    )
    inv = Inventory.load(settings.inventory_path)
    assert inv.get(100) is not None


def test_load_skips_malformed_entries(settings) -> None:
    """单条坏数据不该毁掉整份清单 —— 好的条目照常加载。"""
    settings.inventory_path.parent.mkdir(parents=True, exist_ok=True)
    settings.inventory_path.write_text(
        '{"cards":[{"card_id":100},{"noCardId":1},"字符串",{"card_id":200}]}',
        encoding="utf-8",
    )
    inv = Inventory.load(settings.inventory_path)
    assert len(inv) == 2
    assert inv.get(100) is not None and inv.get(200) is not None


def test_load_rejects_non_object_top_level(settings) -> None:
    settings.inventory_path.parent.mkdir(parents=True, exist_ok=True)
    settings.inventory_path.write_text("[1,2,3]", encoding="utf-8")
    assert len(Inventory.load(settings.inventory_path)) == 0


def test_load_handles_missing_cards_key(settings) -> None:
    settings.inventory_path.parent.mkdir(parents=True, exist_ok=True)
    settings.inventory_path.write_text('{"updatedAt":"x"}', encoding="utf-8")
    inv = Inventory.load(settings.inventory_path)
    assert len(inv) == 0
    assert inv.updated_at == "x"


def test_from_dict_missing_card_id_raises() -> None:
    with pytest.raises(ValueError, match="card_id"):
        OwnedCard.from_dict({"trained": True})


def test_save_is_utf8_readable(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=100, source_image="截图/卡面.png"))
    inv.save()
    text = settings.inventory_path.read_text(encoding="utf-8")
    assert "截图/卡面.png" in text


# ---------------------------------------------------------------------
# 与识别结果对接
# ---------------------------------------------------------------------


def test_absorb_matched(settings) -> None:
    inv = Inventory(settings.inventory_path)
    delta = inv.absorb_recognition(_result([_item(100), _item(200)]))
    assert delta == {"added": 2, "skipped": 0, "pending": 0}
    assert len(inv) == 2


def test_absorb_skips_unknown(settings) -> None:
    inv = Inventory(settings.inventory_path)
    delta = inv.absorb_recognition(_result([_item(None, status="unknown")]))
    assert delta["added"] == 0 and delta["pending"] == 1


def test_absorb_skips_none_card_id(settings) -> None:
    inv = Inventory(settings.inventory_path)
    delta = inv.absorb_recognition(_result([_item(None, status="matched")]))
    assert delta["pending"] == 1


def test_absorb_ambiguous_included_by_default(settings) -> None:
    inv = Inventory(settings.inventory_path)
    delta = inv.absorb_recognition(_result([_item(100, status="ambiguous")]))
    assert delta["added"] == 1
    assert inv.get(100).confirmed is False


def test_absorb_ambiguous_excluded_when_strict(settings) -> None:
    inv = Inventory(settings.inventory_path)
    delta = inv.absorb_recognition(_result([_item(100, status="ambiguous")]), include_ambiguous=False)
    assert delta["added"] == 0 and delta["pending"] == 1


def test_absorb_respects_min_confidence(settings) -> None:
    inv = Inventory(settings.inventory_path)
    delta = inv.absorb_recognition(_result([_item(100, conf=0.3)]), min_confidence=0.7)
    assert delta["added"] == 0 and delta["pending"] == 1


def test_absorb_counts_duplicates_as_skipped(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.absorb_recognition(_result([_item(100)]))
    delta = inv.absorb_recognition(_result([_item(100)]))
    assert delta == {"added": 0, "skipped": 1, "pending": 0}


def test_absorb_records_provenance(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.absorb_recognition(_result([_item(100, conf=0.85, matched_by="fingerprint+attribute")], image="A.png"))
    oc = inv.get(100)
    assert oc.source_image == "A.png"
    assert oc.matched_by == "fingerprint+attribute"
    assert oc.confidence == pytest.approx(0.85)


def test_absorb_matched_marks_confirmed(settings) -> None:
    inv = Inventory(settings.inventory_path)
    inv.absorb_recognition(_result([_item(100, status="matched")]))
    assert inv.get(100).confirmed is True


# ---------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------


def test_stats_breakdown(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    for cid in sorted(catalog.cards)[:6]:
        inv.add(OwnedCard(card_id=cid))
    st = inv.stats(catalog)
    assert st.total == 6
    assert sum(st.by_rarity.values()) == 6
    assert sum(st.by_attribute.values()) == 6
    assert sum(st.by_character.values()) == 6
    assert st.pending_review == 6


def test_stats_counts_trained(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    cid = sorted(catalog.cards)[0]
    inv.add(OwnedCard(card_id=cid, trained=False))
    inv.add(OwnedCard(card_id=cid, trained=True))
    st = inv.stats(catalog)
    assert st.total == 2 and st.trained == 1


def test_stats_handles_unknown_card_id(settings, catalog: Catalog) -> None:
    """清单里有卡池中不存在的 ID（比如换了服务器）不应崩。"""
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=999999))
    st = inv.stats(catalog)
    assert st.total == 1


def test_stats_to_dict_has_string_keys(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=sorted(catalog.cards)[0]))
    d = inv.stats(catalog).to_dict()
    assert all(isinstance(k, str) for k in d["byRarity"])


def test_missing_from(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    have = sorted(catalog.cards)[:3]
    for cid in have:
        inv.add(OwnedCard(card_id=cid))
    missing = inv.missing_from(catalog)
    assert set(missing) == set(catalog.cards) - set(have)


def test_missing_from_with_rarity_filter(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    missing = inv.missing_from(catalog, rarity_min=5)
    assert all(catalog.cards[c].rarity >= 5 for c in missing)
