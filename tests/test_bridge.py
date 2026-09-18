"""Bestdori 导入计划测试（不联网、不开浏览器）。"""

from __future__ import annotations

import pytest

from bestdori_helper.bridge.bestdori_import import (
    ImportPlan,
    PlanEntry,
    SelectorConfig,
    build_import_plan,
)
from bestdori_helper.inventory.store import Inventory
from bestdori_helper.models import Catalog, OwnedCard


def _fill(inv: Inventory, catalog: Catalog, n: int, *, trained=False, confirmed=True) -> list[int]:
    ids = sorted(catalog.cards)[:n]
    for cid in ids:
        inv.add(OwnedCard(card_id=cid, trained=trained, confirmed=confirmed))
    return ids


# ---------------------------------------------------------------------
# 计划生成
# ---------------------------------------------------------------------


def test_plan_all_new_when_remote_empty(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    _fill(inv, catalog, 4)
    plan = build_import_plan(inv, catalog)
    assert len(plan) == 4
    assert plan.local_count == 4
    assert plan.remote_count == 0
    assert plan.already_present == []


def test_plan_excludes_remote_present(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    ids = _fill(inv, catalog, 4)
    plan = build_import_plan(inv, catalog, remote_keys={(ids[0], False), (ids[1], False)})
    assert len(plan) == 2
    assert len(plan.already_present) == 2
    assert {e.card_id for e in plan.entries} == set(ids[2:])


def test_plan_distinguishes_trained_form(settings, catalog: Catalog) -> None:
    """同一张卡的普通形态和特训后形态是两条独立记录。"""
    inv = Inventory(settings.inventory_path)
    cid = sorted(catalog.cards)[0]
    inv.add(OwnedCard(card_id=cid, trained=False))
    inv.add(OwnedCard(card_id=cid, trained=True))

    plan = build_import_plan(inv, catalog, remote_keys={(cid, False)})
    assert len(plan) == 1
    assert plan.entries[0].card_id == cid
    assert plan.entries[0].trained is True


def test_plan_reports_unknown_card_ids(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=999999))
    plan = build_import_plan(inv, catalog)
    assert plan.unknown_card_ids == [999999]
    assert len(plan) == 0


def test_plan_skip_unconfirmed(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    ids = sorted(catalog.cards)[:3]
    inv.add(OwnedCard(card_id=ids[0], confirmed=True))
    inv.add(OwnedCard(card_id=ids[1], confirmed=False))
    inv.add(OwnedCard(card_id=ids[2], confirmed=False))

    plan = build_import_plan(inv, catalog, skip_unconfirmed=True)
    assert len(plan) == 1
    assert plan.entries[0].card_id == ids[0]


def test_plan_keeps_unconfirmed_by_default(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    _fill(inv, catalog, 3, confirmed=False)
    assert len(build_import_plan(inv, catalog)) == 3


def test_plan_empty_inventory(settings, catalog: Catalog) -> None:
    plan = build_import_plan(Inventory(settings.inventory_path), catalog)
    assert len(plan) == 0
    assert plan.summary()


def test_plan_counts(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    ids = _fill(inv, catalog, 5)
    plan = build_import_plan(inv, catalog, remote_keys={(ids[0], False)})
    assert plan.local_count == 5
    assert plan.remote_count == 1
    assert len(plan) == 4


def test_plan_summary_mentions_counts(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    _fill(inv, catalog, 2)
    s = build_import_plan(inv, catalog).summary()
    assert "2" in s


def test_plan_to_dict_is_serializable(settings, catalog: Catalog) -> None:
    import json

    inv = Inventory(settings.inventory_path)
    _fill(inv, catalog, 3)
    d = build_import_plan(inv, catalog).to_dict()
    assert json.loads(json.dumps(d, ensure_ascii=False))["toImport"] == 3
    assert len(d["entries"]) == 3


def test_plan_entry_fields(settings, catalog: Catalog) -> None:
    inv = Inventory(settings.inventory_path)
    _fill(inv, catalog, 1)
    plan = build_import_plan(inv, catalog)
    e = plan.entries[0]
    assert isinstance(e, PlanEntry)
    assert e.title and e.character
    assert 1 <= e.rarity <= 5
    assert e.attribute


def test_plan_entry_search_text_falls_back_to_character(settings, catalog: Catalog) -> None:
    """卡名为空时用角色名搜索。"""
    e = PlanEntry(card_id=1, trained=False, title="", character="户山 香澄", rarity=4, attribute="pure")
    assert e.search_text == "户山 香澄"


def test_plan_entry_search_text_prefers_title(settings, catalog: Catalog) -> None:
    e = PlanEntry(card_id=1, trained=False, title="卡名", character="角色", rarity=4, attribute="pure")
    assert e.search_text == "卡名"


def test_plan_entry_to_dict_roundtrip() -> None:
    e = PlanEntry(card_id=7, trained=True, title="t", character="c", rarity=5, attribute="cool")
    d = e.to_dict()
    assert PlanEntry(**d) == e


def test_import_plan_len_and_dict() -> None:
    p = ImportPlan(entries=[PlanEntry(1, False, "a", "b", 4, "pure")])
    assert len(p) == 1
    assert p.to_dict()["toImport"] == 1


# ---------------------------------------------------------------------
# 选择器配置
# ---------------------------------------------------------------------


def test_selector_config_has_candidates_for_every_step() -> None:
    """每个操作都必须有多个候选选择器，否则 Bestdori 改版就没救了。"""
    s = SelectorConfig()
    for field in ("welcome_close", "search_input", "result_item", "save_button"):
        candidates = getattr(s, field)
        assert len(candidates) >= 2, f"{field} 只有 {len(candidates)} 个候选选择器"
        assert all(isinstance(c, str) and c for c in candidates)


def test_selector_config_is_overridable() -> None:
    s = SelectorConfig(search_input=["#my-search"])
    assert s.search_input == ["#my-search"]
    assert s.welcome_close  # 其他字段保持默认


def test_extract_card_keys_finds_nested_entries() -> None:
    """从任意形状的 JSON 里找 {"cardId": int} 对象。

    Bestdori 用户卡牌数据的 API 结构没有官方文档，导入器用通用递归兜底：
    凡是有 cardId 整型字段的对象都算远端已有卡牌。bool 要排除——
    isinstance(True, int) 在 Python 里是 True，会把别的布尔字段误当卡号。
    """
    from bestdori_helper.bridge.bestdori_import import PlaywrightImporter

    data = {
        "result": True,
        "cards": [
            {"cardId": 158, "trained": [0, 1]},
            {"cardId": 1903},
            {"foo": {"cardId": 2232, "trained": True}},
            {"cardId": True},        # 布尔"卡号"必须被忽略
            {"cardId": "158"},       # 字符串也不算
        ],
    }
    out: set = set()
    PlaywrightImporter._extract_card_keys(data, out)
    assert out == {(158, True), (1903, False), (2232, True)}


def test_extract_card_keys_finds_nested_entries() -> None:
    """从任意形状的 JSON 里找 {"cardId": int} 对象。

    Bestdori 用户卡牌数据的 API 结构没有官方文档，导入器用通用递归兜底：
    凡是有 cardId 整型字段的对象都算远端已有卡牌。bool 要排除——
    isinstance(True, int) 在 Python 里是 True，会把别的布尔字段误当卡号。
    """
    from bestdori_helper.bridge.bestdori_import import PlaywrightImporter

    data = {
        "result": True,
        "cards": [
            {"cardId": 158, "trained": [0, 1]},
            {"cardId": 1903},
            {"foo": {"cardId": 2232, "trained": True}},
            {"cardId": True},        # 布尔"卡号"必须被忽略
            {"cardId": "158"},       # 字符串也不算
        ],
    }
    out: set = set()
    PlaywrightImporter._extract_card_keys(data, out)
    assert out == {(158, True), (1903, False), (2232, True)}
