"""清单导出：CSV / JSON / Markdown / Bestdori ID 列表。"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from ..models import Catalog
from .store import Inventory

CSV_HEADERS = [
    "cardId",
    "角色",
    "乐队",
    "卡名",
    "星级",
    "属性",
    "特训",
    "置信度",
    "识别方式",
    "已确认",
    "来源截图",
]


def _rows(inv: Inventory, catalog: Catalog) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for oc in inv.all():
        info = catalog.describe(oc.card_id, oc.trained)
        rows.append(
            {
                "cardId": oc.card_id,
                "角色": info["character"],
                "乐队": info["band"],
                "卡名": info["title"],
                "星级": info["rarityLabel"],
                "属性": info["attributeLabel"],
                "特训": "是" if oc.trained else "否",
                "置信度": f"{oc.confidence:.3f}" if oc.confidence else "",
                "识别方式": oc.matched_by,
                "已确认": "是" if oc.confirmed else "否",
                "来源截图": oc.source_image,
            }
        )
    return rows


def to_csv(inv: Inventory, catalog: Catalog) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_HEADERS, lineterminator="\n")
    w.writeheader()
    for r in _rows(inv, catalog):
        w.writerow(r)
    return buf.getvalue()


def to_json(inv: Inventory, catalog: Catalog) -> str:
    payload = {
        "updatedAt": inv.updated_at,
        "count": len(inv),
        "stats": inv.stats(catalog).to_dict(),
        "cards": [
            {
                **catalog.describe(oc.card_id, oc.trained),
                "confidence": round(oc.confidence, 4),
                "matchedBy": oc.matched_by,
                "confirmed": oc.confirmed,
                "sourceImage": oc.source_image,
            }
            for oc in inv.all()
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def to_markdown(inv: Inventory, catalog: Catalog) -> str:
    rows = _rows(inv, catalog)
    st = inv.stats(catalog)

    lines: list[str] = []
    lines.append("# 卡面清单")
    lines.append("")
    lines.append(f"- 更新时间：{inv.updated_at or '（未保存）'}")
    lines.append(f"- 卡牌总数：**{st.total}**（其中特训后卡面 {st.trained} 张）")
    lines.append(f"- 待人工确认：{st.pending_review}")
    lines.append("")

    if st.by_rarity:
        lines.append("## 星级分布")
        lines.append("")
        lines.append("| 星级 | 数量 |")
        lines.append("| --- | ---: |")
        for r, n in sorted(st.by_rarity.items()):
            lines.append(f"| {r}★ | {n} |")
        lines.append("")

    if st.by_attribute:
        lines.append("## 属性分布")
        lines.append("")
        lines.append("| 属性 | 数量 |")
        lines.append("| --- | ---: |")
        for a, n in sorted(st.by_attribute.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {a} | {n} |")
        lines.append("")

    if st.by_band:
        lines.append("## 乐队分布")
        lines.append("")
        lines.append("| 乐队 | 数量 |")
        lines.append("| --- | ---: |")
        for b, n in sorted(st.by_band.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {b} | {n} |")
        lines.append("")

    lines.append("## 明细")
    lines.append("")
    lines.append("| # | 角色 | 卡名 | 星级 | 属性 | 特训 | 置信度 |")
    lines.append("| ---: | --- | --- | :-: | :-: | :-: | ---: |")
    for i, r in enumerate(rows, 1):
        conf = r["置信度"] or "—"
        lines.append(
            f"| {i} | {r['角色']} | {r['卡名']} | {r['星级']} | {r['属性']} | {r['特训']} | {conf} |"
        )
    lines.append("")
    return "\n".join(lines)


def to_bestdori_ids(inv: Inventory, *, include_trained_suffix: bool = True) -> str:
    """输出纯卡牌 ID 列表，每行一个。

    ``include_trained_suffix=True`` 时特训卡写成 ``1234T``，否则只写数字。
    """
    out: list[str] = []
    for oc in inv.all():
        if include_trained_suffix and oc.trained:
            out.append(f"{oc.card_id}T")
        else:
            out.append(str(oc.card_id))
    return "\n".join(out)
