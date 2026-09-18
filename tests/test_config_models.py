"""配置与模型测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bestdori_helper.config import (
    ATTRIBUTE_CN,
    ATTRIBUTE_COLOR,
    LANGUAGES,
    RARITY_CN,
    SERVER_DEFAULT_LANG,
    Attribute,
    Server,
    Settings,
)
from bestdori_helper.models import Band, Card, Catalog, Character, OwnedCard


# ---------------------------------------------------------------------
# Server / 语言
# ---------------------------------------------------------------------


def test_server_values() -> None:
    assert [s.value for s in Server] == ["jp", "cn", "tw", "en", "kr"]


def test_server_default_lang_mapping() -> None:
    """语言下标顺序由 Bestdori API 实测确认：[0]日 [1]英 [2]繁中 [3]简中 [4]韩。"""
    assert SERVER_DEFAULT_LANG[Server.JP] == 0
    assert SERVER_DEFAULT_LANG[Server.EN] == 1
    assert SERVER_DEFAULT_LANG[Server.TW] == 2
    assert SERVER_DEFAULT_LANG[Server.CN] == 3
    assert SERVER_DEFAULT_LANG[Server.KR] == 4


def test_languages_cover_all_indices() -> None:
    assert set(LANGUAGES) == {0, 1, 2, 3, 4}


def test_attribute_enum_and_labels() -> None:
    assert {a.value for a in Attribute} == {"powerful", "cool", "pure", "happy"}
    assert set(ATTRIBUTE_CN) == {a.value for a in Attribute}
    assert set(ATTRIBUTE_COLOR) == {a.value for a in Attribute}


def test_rarity_labels() -> None:
    assert RARITY_CN[5] == "5★"


# ---------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------


def test_settings_lang_follows_server() -> None:
    assert Settings(server=Server.CN).lang == 3
    assert Settings(server=Server.JP).lang == 0


def test_settings_explicit_lang_wins() -> None:
    assert Settings(server=Server.CN, lang=1).lang == 1


def test_settings_accepts_server_string() -> None:
    assert Settings(server="jp").server is Server.JP


def test_settings_dirs_are_under_home(tmp_path: Path) -> None:
    s = Settings(home=tmp_path / "h")
    for d in (s.cache_dir, s.data_dir, s.image_dir, s.inventory_path):
        assert str(d).startswith(str(tmp_path / "h"))


def test_settings_image_dir_is_per_server(tmp_path: Path) -> None:
    cn = Settings(server=Server.CN, home=tmp_path)
    jp = Settings(server=Server.JP, home=tmp_path)
    assert cn.image_dir != jp.image_dir
    assert cn.index_path != jp.index_path


def test_settings_ensure_dirs_creates_all(tmp_path: Path) -> None:
    s = Settings(home=tmp_path / "new")
    s.ensure_dirs()
    for d in (s.cache_dir, s.data_dir, s.image_dir, s.thumb_dir, s.home):
        assert d.is_dir()


# ---- 卡图来源（thumb / original）-------------------------------------


def test_settings_default_source_is_thumb() -> None:
    """默认用方形缩略图建库 —— 它的取景和游戏内卡面格一致。"""
    assert Settings().image_source == "thumb"


def test_settings_thumb_and_image_dirs_differ(tmp_path: Path) -> None:
    s = Settings(home=tmp_path)
    assert s.thumb_dir != s.image_dir
    assert s.thumb_dir.is_relative_to(s.cache_dir)
    assert s.image_dir.is_relative_to(s.cache_dir)


def test_settings_source_dir_follows_image_source(tmp_path: Path) -> None:
    s = Settings(home=tmp_path, image_source="thumb")
    assert s.source_dir == s.thumb_dir
    s.image_source = "original"
    assert s.source_dir == s.image_dir


def test_settings_index_path_is_per_source(tmp_path: Path) -> None:
    """两套卡图画幅不同，指纹库必须分开存，否则会互相覆盖。"""
    thumb = Settings(home=tmp_path, image_source="thumb")
    original = Settings(home=tmp_path, image_source="original")
    assert thumb.index_path != original.index_path
    assert thumb.index_path.name == "fingerprints.cn.thumb.npz"
    # original 保持旧文件名，已有的库不用重跑
    assert original.index_path.name == "fingerprints.cn.npz"


def test_settings_pick_uses_lang_index() -> None:
    s = Settings(server=Server.CN)
    values = ["日", "en", "繁", "简", "한"]
    assert s.pick(values) == "简"
    s.lang = 0
    assert s.pick(values) == "日"


def test_settings_pick_falls_back_when_lang_empty() -> None:
    s = Settings(server=Server.CN)
    values = ["日", "", "", "", ""]
    assert s.pick(values) == "日"


def test_settings_pick_out_of_range() -> None:
    s = Settings(server=Server.CN)
    s.lang = 99
    assert s.pick(["a", "b"]) == "a"


def test_settings_pick_none_and_empty() -> None:
    s = Settings()
    assert s.pick(None) == ""
    assert s.pick([]) == ""
    assert s.pick(None, "兜底") == "兜底"


# ---------------------------------------------------------------------
# Card / Character / Band
# ---------------------------------------------------------------------


def test_card_from_api() -> None:
    raw = {
        "characterId": 1,
        "rarity": 4,
        "attribute": "pure",
        "prefix": ["猪突猛進っ！", "Reckless!", "莽撞冒進！", "奋不顾身向前冲！", "저돌맹진!"],
        "resourceSetName": "res001001",
        "skillId": 5,
        "type": "permanent",
        "releasedAt": ["1"] * 5,
    }
    c = Card.from_api(100, raw)
    assert c.id == 100
    assert c.character_id == 1
    assert c.rarity == 4
    assert c.attribute == "pure"
    assert c.resource_set_name == "res001001"


def test_card_title_follows_language() -> None:
    c = Card(id=1, character_id=1, rarity=4, attribute="pure", prefix=["日", "en", "繁", "简", "한"])
    assert c.title(Settings(server=Server.CN)) == "简"
    assert c.title(Settings(server=Server.JP)) == "日"


def test_card_labels() -> None:
    c = Card(id=1, character_id=1, rarity=5, attribute="happy")
    assert c.rarity_cn == "5★"
    assert c.attribute_cn == "快乐"


def test_card_image_url_normal_and_trained() -> None:
    c = Card(id=1, character_id=1, rarity=4, attribute="pure", resource_set_name="res001001")
    normal = c.image_url("cn", trained=False)
    trained = c.image_url("cn", trained=True)
    assert normal.endswith("/res001001_rip/card_normal.png")
    assert trained.endswith("/res001001_rip/card_after_training.png")
    assert "/assets/cn/" in normal


def test_card_from_api_tolerates_missing_fields() -> None:
    c = Card.from_api(1, {})
    assert c.character_id == 0 and c.rarity == 0 and c.prefix == []


# ---- 缩略图 URL（thumb/chara/）---------------------------------------

#: (卡牌 ID, 期望的分桶目录)。分桶 = id // 50 补零 5 位，实测确认。
THUMB_BUCKET_CASES = [
    (0, "card00000"),
    (1, "card00000"),
    (49, "card00000"),
    (50, "card00001"),
    (158, "card00003"),
    (437, "card00008"),
    (1200, "card00024"),
    (2000, "card00040"),
    (2400, "card00048"),
    (2468, "card00049"),
]


@pytest.mark.parametrize("card_id,bucket", THUMB_BUCKET_CASES)
def test_card_thumb_url_bucket(card_id: int, bucket: str) -> None:
    c = Card(id=card_id, character_id=1, rarity=4, attribute="cool",
             resource_set_name="res001001")
    url = c.thumb_url("cn")
    assert url == (
        f"https://bestdori.com/assets/cn/thumb/chara/"
        f"{bucket}_rip/res001001_normal.png"
    )


def test_card_thumb_url_trained_suffix() -> None:
    c = Card(id=158, character_id=1, rarity=4, attribute="cool",
             resource_set_name="res013006")
    assert c.thumb_url("cn", trained=True).endswith(
        "/card00003_rip/res013006_after_training.png"
    )


def test_card_thumb_url_differs_from_image_url() -> None:
    """两套资源不在同一棵目录树下，别写成同一个地址。"""
    c = Card(id=158, character_id=1, rarity=4, attribute="cool",
             resource_set_name="res013006")
    assert c.thumb_url("cn") != c.image_url("cn")
    assert "/thumb/chara/" in c.thumb_url("cn")
    assert "/characters/resourceset/" in c.image_url("cn")


def test_character_from_api() -> None:
    ch = Character.from_api(1, {"characterName": ["戸山 香澄", "Kasumi"], "bandId": 1, "colorCode": "#FF5522"})
    assert ch.id == 1 and ch.band_id == 1 and ch.color_code == "#FF5522"
    assert ch.display_name(Settings(server=Server.JP)) == "戸山 香澄"


def test_band_from_api() -> None:
    b = Band.from_api(1, {"bandName": ["Poppin'Party"] * 5})
    assert b.display_name(Settings()) == "Poppin'Party"


# ---------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------


def test_catalog_describe(catalog: Catalog) -> None:
    cid = sorted(catalog.cards)[0]
    d = catalog.describe(cid)
    assert d["cardId"] == cid
    assert d["character"] == "户山 香澄"
    assert d["band"] == "Poppin'Party"
    assert d["rarityLabel"].endswith("★")
    assert d["attributeLabel"] in ATTRIBUTE_CN.values()


def test_catalog_describe_unknown_card(catalog: Catalog) -> None:
    d = catalog.describe(999999)
    assert "未知卡牌" in d["title"]


def test_catalog_character_and_band_name(catalog: Catalog) -> None:
    card = catalog.cards[sorted(catalog.cards)[1]]
    assert catalog.character_name(card) == "凑 友希那"
    assert catalog.band_name(card) == "Roselia"


def test_catalog_filter_cards(catalog: Catalog) -> None:
    assert all(c.rarity == 4 for c in catalog.filter_cards(rarity=4))
    assert all(c.attribute == "cool" for c in catalog.filter_cards(attribute="cool"))
    assert all(c.character_id == 1 for c in catalog.filter_cards(character_id=1))
    assert catalog.filter_cards(rarity=99) == []


def test_catalog_to_json(catalog: Catalog) -> None:
    import json

    d = json.loads(catalog.to_json())
    assert d["count"] == len(catalog.cards)
    assert d["server"] == "cn"


# ---------------------------------------------------------------------
# OwnedCard
# ---------------------------------------------------------------------


def test_owned_card_roundtrip() -> None:
    oc = OwnedCard(card_id=1, trained=True, confidence=0.9, matched_by="x", confirmed=True)
    assert OwnedCard.from_dict(oc.to_dict()) == oc


def test_owned_card_defaults() -> None:
    oc = OwnedCard(card_id=5)
    assert oc.trained is False
    assert oc.confidence == 0.0
    assert oc.confirmed is False


# ---- 卡面详情页 URL（人工核对识别结果用）-----------------------------


def test_card_page_url(catalog: Catalog) -> None:
    assert catalog.cards[101].page_url() == "https://bestdori.com/info/cards/101"


def test_describe_carries_card_page_url(catalog: Catalog) -> None:
    """describe() 必须带 url —— GUI/CLI/Web 都靠它提供核对入口。"""
    known = catalog.describe(101)
    assert known["url"] == "https://bestdori.com/info/cards/101"


def test_describe_unknown_card_also_has_url() -> None:
    """未知卡也要给全键（含 url）—— 这里曾经因为缺键把导出搞崩过。"""
    d = Catalog(settings=Settings()).describe(999999)
    assert d["url"] == "https://bestdori.com/info/cards/999999"
    assert "未知卡牌" in d["title"]
