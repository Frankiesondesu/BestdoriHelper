"""持有卡牌清单：存储、去重、统计。"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..models import Catalog, OwnedCard
from ..vision.recognize import RecognitionResult

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class InventoryStats:
    """清单统计。"""

    total: int = 0
    trained: int = 0
    by_rarity: dict[int, int] = None  # type: ignore[assignment]
    by_attribute: dict[str, int] = None  # type: ignore[assignment]
    by_band: dict[str, int] = None  # type: ignore[assignment]
    by_character: dict[str, int] = None  # type: ignore[assignment]
    pending_review: int = 0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "trained": self.trained,
            "byRarity": {str(k): v for k, v in sorted((self.by_rarity or {}).items())},
            "byAttribute": self.by_attribute or {},
            "byBand": self.by_band or {},
            "byCharacter": self.by_character or {},
            "pendingReview": self.pending_review,
        }


class Inventory:
    """一份用户卡牌清单。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.owned: dict[tuple[int, bool], OwnedCard] = {}
        self.updated_at: str = ""

    # ---- 持久化 ------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Inventory:
        """从磁盘加载清单。

        容错策略：**单条坏数据不该毁掉整份清单**。JSON 解析失败、顶层类型
        不对、或某条记录缺字段时，跳过并记日志，其余条目照常加载。
        """
        inv = cls(path)
        if not inv.path.exists():
            return inv
        try:
            raw = json.loads(inv.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log.warning("清单文件损坏，按空清单处理：%s（%s）", inv.path, e)
            return inv
        if not isinstance(raw, dict):
            log.warning("清单文件格式不对（顶层不是对象），按空清单处理：%s", inv.path)
            return inv

        skipped = 0
        for item in raw.get("cards", []) or []:
            if not isinstance(item, dict):
                skipped += 1
                continue
            try:
                oc = OwnedCard.from_dict(item)
            except (ValueError, TypeError) as e:
                skipped += 1
                log.debug("跳过损坏的清单条目：%s", e)
                continue
            inv.owned[(oc.card_id, oc.trained)] = oc
        if skipped:
            log.warning("清单中有 %d 条损坏记录被跳过", skipped)

        inv.updated_at = raw.get("updatedAt", "")
        return inv

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "updatedAt": self.updated_at,
            "count": len(self.owned),
            "cards": [c.to_dict() for c in self._sorted()],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 增删改查 ----------------------------------------------------

    def _sorted(self) -> list[OwnedCard]:
        return sorted(self.owned.values(), key=lambda c: (c.card_id, c.trained))

    def add(self, card: OwnedCard, *, overwrite: bool = False) -> bool:
        """加入清单。已存在且 ``overwrite=False`` 时返回 False。"""
        key = (card.card_id, card.trained)
        if key in self.owned and not overwrite:
            return False
        self.owned[key] = card
        return True

    def remove(self, card_id: int, trained: bool | None = None) -> int:
        keys = [
            k for k in self.owned if k[0] == card_id and (trained is None or k[1] == trained)
        ]
        for k in keys:
            del self.owned[k]
        return len(keys)

    def get(self, card_id: int, trained: bool = False) -> OwnedCard | None:
        return self.owned.get((card_id, trained))

    def confirm(self, card_id: int, trained: bool) -> None:
        oc = self.get(card_id, trained)
        if oc:
            oc.confirmed = True

    def clear(self) -> int:
        n = len(self.owned)
        self.owned.clear()
        return n

    def __len__(self) -> int:
        return len(self.owned)

    def all(self) -> list[OwnedCard]:
        return self._sorted()

    # ---- 与识别结果对接 ----------------------------------------------

    def absorb_recognition(
        self,
        result: RecognitionResult,
        *,
        min_confidence: float = 0.0,
        include_ambiguous: bool = True,
        overwrite: bool = False,
    ) -> dict[str, int]:
        """把一次识别的结果并入清单。

        返回 ``{"added": n, "skipped": n, "pending": n}``。
        ``pending`` 是需要人工确认的条数（低置信 / 有歧义）。
        """
        added = skipped = pending = 0
        for item in result.items:
            if item.card_id is None:
                pending += 1
                continue
            if item.status == "unknown":
                pending += 1
                continue
            if item.status == "ambiguous" and not include_ambiguous:
                pending += 1
                continue
            if item.confidence < min_confidence:
                pending += 1
                continue

            oc = OwnedCard(
                card_id=item.card_id,
                trained=item.trained,
                source_image=result.image,
                confidence=item.confidence,
                matched_by=item.matched_by,
                confirmed=item.status == "matched",
            )
            if self.add(oc, overwrite=overwrite):
                added += 1
            else:
                skipped += 1
        return {"added": added, "skipped": skipped, "pending": pending}

    # ---- 统计 --------------------------------------------------------

    def stats(self, catalog: Catalog) -> InventoryStats:
        st = InventoryStats(
            total=len(self.owned),
            trained=sum(1 for c in self.owned.values() if c.trained),
            by_rarity=Counter(),
            by_attribute=Counter(),
            by_band=Counter(),
            by_character=Counter(),
        )
        for oc in self.owned.values():
            card = catalog.card(oc.card_id)
            if card is None:
                continue
            st.by_rarity[card.rarity] += 1
            st.by_attribute[card.attribute_cn] += 1
            st.by_character[catalog.character_name(card)] += 1
            band = catalog.band_name(card)
            if band:
                st.by_band[band] += 1
            if not oc.confirmed:
                st.pending_review += 1
        return st

    def missing_from(self, catalog: Catalog, *, rarity_min: int = 0) -> list[int]:
        """清单中不存在、但卡池里有的卡（可选只看某星级以上）。"""
        have = {cid for cid, _ in self.owned}
        return [
            cid
            for cid, card in catalog.cards.items()
            if cid not in have and card.rarity >= rarity_min
        ]

    def merge(self, others: Iterable[OwnedCard]) -> int:
        n = 0
        for oc in others:
            if self.add(oc):
                n += 1
        return n
