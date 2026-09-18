"""领域模型：卡牌、角色、乐队、持有记录。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any

from .config import ATTRIBUTE_CN, RARITY_CN, Settings


@dataclass(slots=True)
class Character:
    """角色。"""

    id: int
    names: list[str] = field(default_factory=list)
    band_id: int = 0
    color_code: str = "#888888"

    @classmethod
    def from_api(cls, cid: int, raw: dict[str, Any]) -> Character:
        return cls(
            id=cid,
            names=list(raw.get("characterName") or []),
            band_id=int(raw.get("bandId") or 0),
            color_code=raw.get("colorCode") or "#888888",
        )

    def display_name(self, settings: Settings) -> str:
        return settings.pick(self.names, f"角色#{self.id}")


@dataclass(slots=True)
class Band:
    """乐队。"""

    id: int
    names: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, bid: int, raw: dict[str, Any]) -> Band:
        return cls(id=bid, names=list(raw.get("bandName") or []))

    def display_name(self, settings: Settings) -> str:
        return settings.pick(self.names, f"乐队#{self.id}")


@dataclass(slots=True)
class Card:
    """一张卡牌（不含图片）。"""

    id: int
    character_id: int
    rarity: int
    attribute: str
    prefix: list[str] = field(default_factory=list)
    resource_set_name: str = ""
    skill_id: int = 0
    card_type: str = ""
    released_at: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, cid: int, raw: dict[str, Any]) -> Card:
        return cls(
            id=cid,
            character_id=int(raw.get("characterId") or 0),
            rarity=int(raw.get("rarity") or 0),
            attribute=str(raw.get("attribute") or ""),
            prefix=list(raw.get("prefix") or []),
            resource_set_name=str(raw.get("resourceSetName") or ""),
            skill_id=int(raw.get("skillId") or 0),
            card_type=str(raw.get("type") or ""),
            released_at=list(raw.get("releasedAt") or []),
        )

    def title(self, settings: Settings) -> str:
        return settings.pick(self.prefix, "")

    @property
    def attribute_cn(self) -> str:
        return ATTRIBUTE_CN.get(self.attribute, self.attribute)

    @property
    def rarity_cn(self) -> str:
        return RARITY_CN.get(self.rarity, f"{self.rarity}★")

    def page_url(self) -> str:
        """Bestdori 卡面**详情页** URL —— 人工核对识别结果用。

        实测确认：``https://bestdori.com/info/cards/158`` 服务端渲染出的标题是
        "Hagumi Kitazawa - Source Of Happiness"，与本地卡片 158
        （``res013006``＝「精神的源泉」）一致。

        注意**语言只能跟随站点自身的设置** —— 试过 ``/cn/...``、``?lang=cn``、
        ``/zh-cn/...`` 都不能在 URL 上强制语言，所以这里只给基础地址。
        """
        return f"https://bestdori.com/info/cards/{self.id}"

    def image_url(self, server: str, trained: bool = False) -> str:
        """Bestdori 卡面原图 URL（1334x1002 横向）。"""
        suffix = "card_after_training" if trained else "card_normal"
        return (
            f"https://bestdori.com/assets/{server}/characters/"
            f"resourceset/{self.resource_set_name}_rip/{suffix}.png"
        )

    def thumb_url(self, server: str, trained: bool = False) -> str:
        """Bestdori 卡面**缩略图** URL（180x180 方形、脸部特写取景）。

        这套资源和原图不在同一棵目录树下，是 Bestdori 前端卡片列表页用的：

            /assets/{server}/thumb/chara/card{id // 50 补零 5 位}_rip/{资源名}_{形态}.png

        目录名里的数字是**卡牌 ID 的 50 张分桶**（实测 id=1 -> card00000、
        id=158 -> card00003、id=2468 -> card00049）。

        为什么值得单独支持：游戏内的卡面格是方形脸部特写，取景和这套缩略图
        一致，而和横向原图差了一倍多的画幅 —— 用原图建库时 pHash 只能"猜角色"。
        """
        bucket = f"{self.id // 50:05d}"
        suffix = "after_training" if trained else "normal"
        return (
            f"https://bestdori.com/assets/{server}/thumb/chara/"
            f"card{bucket}_rip/{self.resource_set_name}_{suffix}.png"
        )


@dataclass(slots=True)
class OwnedCard:
    """清单中的一条记录：一张卡 + 持有状态 + 识别溯源信息。"""

    card_id: int
    trained: bool = False          # 是否已特训
    mastery: int = 0               # 特训等级 / 大师等级（Bestdori 里为 masterRank）
    skill_level: int = 1
    # 识别溯源
    source_image: str = ""
    confidence: float = 0.0
    matched_by: str = ""           # fingerprint / ocr / manual
    # 用户确认状态
    confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OwnedCard:
        """从字典构造。未知字段被忽略，缺少 card_id 则报明确错误。"""
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        if "card_id" not in raw:
            raise ValueError(f"清单条目缺少 card_id 字段：{raw!r}")
        return cls(**{k: v for k, v in raw.items() if k in allowed})


@dataclass(slots=True)
class RecognitionCandidate:
    """识别出的候选结果。"""

    card_id: int
    score: float          # 0~1，越大越像
    trained: bool = False
    distance: float = 0.0
    color_similarity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Catalog:
    """一次加载进内存的完整卡牌数据库。"""

    settings: Settings
    cards: dict[int, Card] = field(default_factory=dict)
    characters: dict[int, Character] = field(default_factory=dict)
    bands: dict[int, Band] = field(default_factory=dict)

    def card(self, cid: int) -> Card | None:
        return self.cards.get(cid)

    def character_name(self, card: Card) -> str:
        ch = self.characters.get(card.character_id)
        return ch.display_name(self.settings) if ch else f"角色#{card.character_id}"

    def band_name(self, card: Card) -> str:
        ch = self.characters.get(card.character_id)
        if not ch:
            return ""
        b = self.bands.get(ch.band_id)
        return b.display_name(self.settings) if b else ""

    def describe(self, cid: int, trained: bool = False) -> dict[str, Any]:
        """生成一行人类可读的卡牌描述（用于清单/导出）。

        卡池里找不到该 ID 时（例如换了服务器、或 Bestdori 下架了旧卡），
        **返回同样的一组键**，值用占位符填充。调用方可以放心地
        ``info["rarityLabel"]`` 而不用先判空 —— 这是踩过的坑：早期版本
        在这里返回了缺键的字典，导致导出清单时 KeyError 崩掉。
        """
        c = self.cards.get(cid)
        if not c:
            return {
                "cardId": cid,
                "title": f"未知卡牌 #{cid}",
                "character": "",
                "band": "",
                "rarity": 0,
                "rarityLabel": "?",
                "attribute": "",
                "attributeLabel": "?",
                "trained": trained,
                "resourceSetName": "",
                # 未知卡也给完整键（含 url）—— 见上面 docstring 里说的那个坑
                "url": self.card_page_url(cid),
            }
        return {
            "cardId": cid,
            "title": c.title(self.settings),
            "character": self.character_name(c),
            "band": self.band_name(c),
            "rarity": c.rarity,
            "rarityLabel": c.rarity_cn,
            "attribute": c.attribute,
            "attributeLabel": c.attribute_cn,
            "trained": trained,
            "resourceSetName": c.resource_set_name,
            # 卡面详情页，人工核对识别结果用（见 Card.page_url）
            "url": c.page_url(),
        }

    def card_page_url(self, cid: int) -> str:
        """卡面详情页 URL；卡池里没有该 ID 时也给一个可用的地址。"""
        c = self.cards.get(cid)
        return c.page_url() if c else f"https://bestdori.com/info/cards/{cid}"

    def filter_cards(
        self,
        *,
        character_id: int | None = None,
        rarity: int | None = None,
        attribute: str | None = None,
    ) -> list[Card]:
        out = []
        for c in self.cards.values():
            if character_id is not None and c.character_id != character_id:
                continue
            if rarity is not None and c.rarity != rarity:
                continue
            if attribute is not None and c.attribute != attribute:
                continue
            out.append(c)
        return out

    def to_json(self) -> str:
        return json.dumps(
            {
                "server": self.settings.server.value,
                "lang": self.settings.lang,
                "count": len(self.cards),
            },
            ensure_ascii=False,
        )
