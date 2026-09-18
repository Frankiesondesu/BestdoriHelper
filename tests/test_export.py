"""导出格式测试。"""

from __future__ import annotations

import csv
import io
import json

import pytest

from bestdori_helper.inventory.export import CSV_HEADERS, to_bestdori_ids, to_csv, to_json, to_markdown
from bestdori_helper.inventory.store import Inventory
from bestdori_helper.models import Catalog, OwnedCard


@pytest.fixture
def filled(settings, catalog: Catalog) -> Inventory:
    inv = Inventory(settings.inventory_path)
    ids = sorted(catalog.cards)[:5]
    inv.add(OwnedCard(card_id=ids[0], trained=False, confidence=0.95, matched_by="fingerprint", confirmed=True))
    inv.add(OwnedCard(card_id=ids[1], trained=True, confidence=0.71, matched_by="fingerprint+attribute"))
    inv.add(OwnedCard(card_id=ids[2], trained=False, confidence=0.0))
    return inv


def test_csv_has_expected_headers(filled, catalog: Catalog) -> None:
    rows = list(csv.DictReader(io.StringIO(to_csv(filled, catalog))))
    assert len(rows) == 3
    assert list(rows[0].keys()) == CSV_HEADERS


def test_csv_row_values(filled, catalog: Catalog) -> None:
    rows = list(csv.DictReader(io.StringIO(to_csv(filled, catalog))))
    first = rows[0]
    assert first["特训"] == "否"
    assert first["已确认"] == "是"
    assert first["识别方式"] == "fingerprint"
    assert first["置信度"] == "0.950"


def test_csv_trained_flag(filled, catalog: Catalog) -> None:
    rows = list(csv.DictReader(io.StringIO(to_csv(filled, catalog))))
    assert any(r["特训"] == "是" for r in rows)


def test_csv_uses_lf_line_terminator(filled, catalog: Catalog) -> None:
    """CSV 用 \\n 而不是 \\r\\n，跨平台一致。"""
    assert "\r\n" not in to_csv(filled, catalog)


def test_json_is_valid_and_complete(filled, catalog: Catalog) -> None:
    d = json.loads(to_json(filled, catalog))
    assert d["count"] == 3
    assert len(d["cards"]) == 3
    assert "stats" in d
    first = d["cards"][0]
    for key in ("cardId", "title", "character", "band", "rarityLabel", "attributeLabel", "confirmed"):
        assert key in first


def test_json_confidence_rounded(filled, catalog: Catalog) -> None:
    d = json.loads(to_json(filled, catalog))
    for c in d["cards"]:
        assert isinstance(c["confidence"], float)
        assert len(str(c["confidence"]).split(".")[-1]) <= 4


def test_json_includes_stats(filled, catalog: Catalog) -> None:
    d = json.loads(to_json(filled, catalog))
    assert d["stats"]["total"] == 3
    assert d["stats"]["trained"] == 1


def test_markdown_has_all_sections(filled, catalog: Catalog) -> None:
    md = to_markdown(filled, catalog)
    assert "# 卡面清单" in md
    assert "## 星级分布" in md
    assert "## 属性分布" in md
    assert "## 乐队分布" in md
    assert "## 明细" in md


def test_markdown_detail_row_count(filled, catalog: Catalog) -> None:
    md = to_markdown(filled, catalog)
    detail = md.split("## 明细")[1]
    rows = [ln for ln in detail.splitlines() if ln.startswith("|") and "---" not in ln]
    assert len(rows) == 4  # 表头 + 3 行数据


def test_markdown_totals(filled, catalog: Catalog) -> None:
    md = to_markdown(filled, catalog)
    assert "卡牌总数：**3**" in md


def test_markdown_handles_empty_inventory(settings, catalog: Catalog) -> None:
    md = to_markdown(Inventory(settings.inventory_path), catalog)
    assert "# 卡面清单" in md
    assert "**0**" in md


def test_ids_plain(filled, catalog: Catalog) -> None:
    ids = to_bestdori_ids(filled, include_trained_suffix=False)
    lines = ids.splitlines()
    assert len(lines) == 3
    assert all(ln.isdigit() for ln in lines)


def test_ids_with_trained_suffix(filled, catalog: Catalog) -> None:
    ids = to_bestdori_ids(filled, include_trained_suffix=True)
    assert any(ln.endswith("T") for ln in ids.splitlines())


def test_ids_empty(settings, catalog: Catalog) -> None:
    assert to_bestdori_ids(Inventory(settings.inventory_path)) == ""


def test_export_handles_unknown_card_id(settings, catalog: Catalog) -> None:
    """清单里有卡池不存在的 ID，导出不应崩，要给出占位。"""
    inv = Inventory(settings.inventory_path)
    inv.add(OwnedCard(card_id=999999))
    md = to_markdown(inv, catalog)
    assert "未知卡牌 #999999" in md
    assert json.loads(to_json(inv, catalog))["count"] == 1
