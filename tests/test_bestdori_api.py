"""Bestdori 后台 API 测试：编解码与导入合并（全部离线，不联网）。

编解码的正确性标准：与 Bestdori 前端 ``exportProfile`` / ``importProfile``
互逆（前端实现在 app.js / ProfileCards chunk，逆向过程见 2026-09-17 的工作日志）。
"""

from __future__ import annotations

import pytest

from bestdori_helper.bridge.bestdori_api import (
    decode_cards,
    decode_ids,
    encode_cards,
    encode_ids,
    import_inventory,
    new_card_entry,
    rle_decode,
    rle_encode,
)
from bestdori_helper.config import Settings
from bestdori_helper.inventory.store import Inventory
from bestdori_helper.models import Card, Catalog, OwnedCard


# ---------------------------------------------------------------------
# 游程编码
# ---------------------------------------------------------------------


def test_rle_roundtrip():
    vals = [1, 1, 1, 2, 2, 3, 3, 3, 3, 0, 0, 5]
    enc = rle_encode(vals)
    assert enc[:2] == [3, 1], "连续 3 个 1 应压成 [3, 1]"
    assert rle_decode(enc) == vals


def test_rle_decode_pairs():
    assert rle_decode([2, 7, 1, 0]) == [7, 7, 0]
    assert rle_decode([]) == []


def test_rle_encode_single_values():
    assert rle_encode([5, 6, 7]) == [1, 5, 1, 6, 1, 7]


# ---------------------------------------------------------------------
# 卡号数组（base64 / Uint16 小端）
# ---------------------------------------------------------------------


def test_ids_roundtrip():
    # 前端解码带一个 >24464 就 +65536 的"未来卡号"兼容分支：它对 (24464, 65535]
    # 的整数不是恒等映射（65535 会被读成 131071）。现实卡号（~2700 以内）永远
    # 触不到它，往返测试也只用现实范围内的卡号。
    ids = [1, 158, 1903, 2700, 24464]
    assert decode_ids(encode_ids(ids)) == ids


def test_ids_empty():
    assert decode_ids(encode_ids([])) == []


# ---------------------------------------------------------------------
# 档案条目解码 / 编码
# ---------------------------------------------------------------------


def _entry(ids, trains, levels=None):
    n = len(ids)
    return {
        "compression": "2",
        "data": {
            "cards": {
                "ids": encode_ids(ids),
                "levels": rle_encode(levels or [1] * n),
                "masters": rle_encode([0] * n),
                "skills": rle_encode([0] * n),
                "eps": rle_encode([0] * n),
                "trains": rle_encode(trains),
                "arts": rle_encode(list(trains)),
                "excludes": rle_encode([0] * n),
            },
            "items": {},
        },
    }


def test_decode_cards_aligns_columns():
    cards = decode_cards(_entry([158, 1903], [0, 1]))
    assert set(cards) == {158, 1903}
    assert cards[158]["train"] == 0
    assert cards[1903]["train"] == 1
    assert cards[1903]["id"] == 1903


def test_cards_roundtrip():
    entry = _entry([158, 1903], [0, 1], levels=[60, 70])
    cards = decode_cards(entry)
    rebuilt = {"compression": "2", "data": {"cards": encode_cards(cards), "items": {}}}
    assert decode_cards(rebuilt) == cards


def test_decode_cards_rejects_unknown_compression():
    entry = _entry([1], [0])
    entry["compression"] = "9"
    with pytest.raises(ValueError):
        decode_cards(entry)


# ---------------------------------------------------------------------
# 新卡默认值（与 Bestdori 网页手动添加一致）
# ---------------------------------------------------------------------


def test_new_card_entry_trained():
    card = {"levelLimit": 60, "stat": {"training": {"levelLimit": 10}, "episodes": [{}, {}]}}
    e = new_card_entry(101, card, trained=True)
    assert e["level"] == 70
    assert e["train"] == 1 and e["art"] == 1
    assert e["ep"] == 2
    assert e["master"] == 0 and e["skill"] == 0 and e["exclude"] is False


def test_new_card_entry_untrained():
    card = {"levelLimit": 60, "stat": {"training": {"levelLimit": 10}, "episodes": [{}]}}
    e = new_card_entry(101, card, trained=False)
    assert e["level"] == 60 and e["train"] == 0 and e["art"] == 0


def test_new_card_entry_low_rarity_has_no_training():
    """1★/2★ 没有 training 字段，就算误传 trained=True 也不会特训。"""
    e = new_card_entry(102, {"levelLimit": 30, "stat": {}}, trained=True)
    assert e["level"] == 30 and e["train"] == 0


def test_new_card_entry_without_meta_respects_trained():
    """卡池快照缺失时按调用方的 trained 语义来，别把"特训后"悄悄降级。"""
    e = new_card_entry(1, None, trained=True)
    assert e["train"] == 1 and e["art"] == 1
    e0 = new_card_entry(1, None, trained=False)
    assert e0["train"] == 0 and e0["level"] == 1


# ---------------------------------------------------------------------
# 导入合并（FakeAccount 隔离网络）
# ---------------------------------------------------------------------


class FakeAccount:
    """与 BestdoriAccount 同接口的离线替身。"""

    def __init__(self, settings: Settings, profiles: list[dict]):
        self.settings = settings
        self.profiles = profiles
        self.stored: list[dict] | None = None

    def fetch_profiles(self):
        return self.profiles

    def store_profiles(self, profiles):
        self.stored = profiles


@pytest.fixture
def catalog(tmp_path):
    s = Settings(home=tmp_path / "home")
    s.ensure_dirs()
    return Catalog(
        settings=s,
        cards={
            101: Card(101, 1, 3, "cool", ["卡A"] * 5, "res101"),
            102: Card(102, 1, 4, "pure", ["卡B"] * 5, "res102"),
        },
        characters={},
        bands={},
    )


def _make_inventory(tmp_path, owned):
    inv = Inventory(tmp_path / "inv.json")
    for oc in owned:
        inv.add(oc)
    return inv


def test_import_inventory_merges_incrementally(tmp_path, catalog):
    # 远端只有 158（未特训）
    account = FakeAccount(catalog.settings, [_entry([158], [0])])
    inv = _make_inventory(
        tmp_path,
        [
            OwnedCard(card_id=101, trained=False, confidence=0.9, confirmed=True),
            OwnedCard(card_id=158, trained=True, confidence=0.9, confirmed=True),
        ],
    )

    stats = import_inventory(account, inv, catalog, profile_index=0)

    assert (stats.added, stats.upgraded) == (1, 1), stats.summary()
    assert account.stored is not None
    cards = decode_cards(account.stored[0])
    assert 101 in cards and cards[101]["train"] == 0          # 新增（未特训）
    assert cards[158]["train"] == 1                            # 升级为特训


def test_import_inventory_skips_already_present(tmp_path, catalog):
    account = FakeAccount(catalog.settings, [_entry([101], [0])])
    inv = _make_inventory(tmp_path, [OwnedCard(card_id=101, trained=False, confirmed=True)])

    stats = import_inventory(account, inv, catalog, profile_index=0)

    assert stats.added == 0 and stats.upgraded == 0 and stats.already == 1


def test_import_inventory_respects_confirmed_only(tmp_path, catalog):
    account = FakeAccount(catalog.settings, [_entry([], [])])
    inv = _make_inventory(tmp_path, [OwnedCard(card_id=101, trained=False, confirmed=False)])

    stats = import_inventory(account, inv, catalog, profile_index=0, only_confirmed=True)

    assert stats.added == 0, "未确认的条目不该被导入"


def test_import_inventory_keeps_other_profiles(tmp_path, catalog):
    """多档案时只动选中的那份，另一份必须原样保留。"""
    other = _entry([999], [0])
    account = FakeAccount(catalog.settings, [_entry([], []), other])
    inv = _make_inventory(tmp_path, [OwnedCard(card_id=101, trained=False, confirmed=True)])

    import_inventory(account, inv, catalog, profile_index=0)

    assert account.stored is not None
    assert account.stored[1] == other, "另一份档案被改动了"


# ---------------------------------------------------------------------
# 登录态判定（user/me 的响应格式）
# ---------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _account_with_response(tmp_path, monkeypatch, payload):
    from bestdori_helper.bridge.bestdori_api import BestdoriAccount

    s = Settings(home=tmp_path / "home")
    s.ensure_dirs()
    acc = BestdoriAccount(s)
    monkeypatch.setattr(acc, "_request", lambda m, u, **kw: _FakeResponse(payload))
    return acc


def test_me_returns_none_on_login_required(tmp_path, monkeypatch):
    acc = _account_with_response(
        tmp_path, monkeypatch, {"result": False, "code": "LOGIN_REQUIRED"}
    )
    assert acc.me() is None


def test_me_accepts_result_true(tmp_path, monkeypatch):
    acc = _account_with_response(tmp_path, monkeypatch, {"result": True, "username": "foo"})
    assert acc.me() == {"result": True, "username": "foo"}


def test_me_accepts_user_object_without_result(tmp_path, monkeypatch):
    """登录后可能直接返回用户对象，没有 result 字段 —— 必须算已登录。

    只用 result 判定会出现「日志说登录成功、弹窗却说失败」（用户实测）。
    """
    acc = _account_with_response(tmp_path, monkeypatch, {"username": "foo", "server": 0})
    assert acc.me() == {"username": "foo", "server": 0}


def test_me_rejects_empty_and_non_dict(tmp_path, monkeypatch):
    assert _account_with_response(tmp_path, monkeypatch, {}).me() is None
    assert _account_with_response(tmp_path, monkeypatch, ["nope"]).me() is None


def test_login_returns_response_data(tmp_path, monkeypatch):
    """login 成功时把响应数据带回去 —— 登录成功的判定以 login 自己为准。"""
    acc = _account_with_response(tmp_path, monkeypatch, {"result": True, "username": "foo"})
    assert acc.login("u", "p") == {"result": True, "username": "foo"}
    assert acc.session_file.exists(), "登录成功后会话应该落盘"


def test_login_raises_on_failure(tmp_path, monkeypatch):
    import pytest as _pt

    acc = _account_with_response(tmp_path, monkeypatch, {"result": False, "code": "BAD_CREDENTIALS"})
    with _pt.raises(RuntimeError):
        acc.login("u", "p")
