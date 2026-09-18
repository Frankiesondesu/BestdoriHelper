"""Bestdori 用户数据后台 API —— 纯 HTTP + cookie，**不弹浏览器窗口**。

端点（从 Bestdori 前端 bundle 逆向确认，dump 见 ``.bdh-test/js/``；每个端点
都在前端代码里找到了对应的调用点，不是猜的）::

    POST /api/user/login       {username, password}   成功即下发会话 cookie
    GET  /api/user/me                                 未登录 = {"result":false,"code":"LOGIN_REQUIRED"}
    GET  /api/user/profiles    （注意：无 .json 后缀）  {"result":true,"profiles":[...]}
    POST /api/user/profiles    {"profiles":[...]}      全量写回

profiles 数组的元素是 ProfileManager「导出档案」的格式::

    {"compression": "2", "data": {"cards": {...}, "items": {...}}}

其中 ``cards.ids`` 是 **base64(Uint16 小端)** 的卡号数组，其余字段是
**游程编码**（成对 [重复次数, 值] 展平成数组）—— 与前端
``exportProfile`` / ``importProfile``（app.js）互逆，编解码在本文件实现。
卡牌条目字段 ``{id, level, master, skill, ep, train, art, exclude}`` 与前端
添加卡牌时的默认值（ProfileCards 的 ``onStartAction``）一致。
"""

from __future__ import annotations

import base64
import json
import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..inventory.store import Inventory
from ..models import Catalog

log = logging.getLogger(__name__)

API = "https://bestdori.com/api"
LOGIN_URL = f"{API}/user/login"
ME_URL = f"{API}/user/me"
PROFILES_URL = f"{API}/user/profiles"

#: 卡号超出此值时解码要 +65536（前端 importProfile 的兼容分支；照抄保持互逆）
_ID_BIG = 24464
#: 支持的云存储压缩版本
SUPPORTED_COMPRESSION = "2"


# ---------------------------------------------------------------------
# 编解码（与前端 exportProfile / importProfile 互逆）
# ---------------------------------------------------------------------


def rle_decode(arr: list[Any]) -> list[Any]:
    """游程解码：``[次数, 值, 次数, 值, …]`` 展开成值序列（前端 ``w``）。"""
    out: list[Any] = []
    if not arr:
        return out
    i = 0
    while i < len(arr):
        count, value = arr[i], arr[i + 1]
        out.extend([value] * int(count))
        i += 2
    return out


def rle_encode(values: list[Any]) -> list[Any]:
    """游程编码：连续相同值压成 ``[次数, 值]`` 对再展平（前端 ``S``）。"""
    out: list[Any] = []
    for v in values:
        if out and out[-1][1] == v:
            out[-1][0] += 1
        else:
            out.append([1, v])
    return [x for pair in out for x in pair]


def decode_ids(b64: str) -> list[int]:
    """base64(Uint16 小端) -> 卡号列表（前端 ``x`` 里 ids 的逆）。"""
    raw = base64.b64decode(b64)
    n = len(raw) // 2
    vals = struct.unpack(f"<{n}H", raw[: n * 2])
    return [v + 65536 if v > _ID_BIG else v for v in vals]


def encode_ids(ids: list[int]) -> str:
    """卡号列表 -> base64(Uint16 小端)（前端 ``exportProfile`` 的逆）。"""
    return base64.b64encode(struct.pack(f"<{len(ids)}H", *ids)).decode("ascii")


def decode_cards(entry: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """云存储条目 -> ``{cardId: {id, level, master, skill, ep, train, art, exclude}}``。

    与前端 ``x`` 函数一致：ids 解码后与其余字段按下标对齐。
    """
    compression = entry.get("compression")
    if compression != SUPPORTED_COMPRESSION:
        raise ValueError(
            f"不支持的档案版本 compression={compression!r}（当前支持 {SUPPORTED_COMPRESSION!r}）"
        )
    cards = entry["data"]["cards"]
    ids = decode_ids(cards["ids"])
    levels = rle_decode(cards.get("levels") or [])
    masters = rle_decode(cards.get("masters") or [])
    skills = rle_decode(cards.get("skills") or [])
    eps = rle_decode(cards.get("eps") or [])
    trains = rle_decode(cards.get("trains") or [])
    arts = rle_decode(cards.get("arts") or [])
    excludes = rle_decode(cards.get("excludes") or [])

    def pick(arr: list[Any], i: int, default: Any) -> Any:
        return arr[i] if i < len(arr) else default

    out: dict[int, dict[str, Any]] = {}
    for i, cid in enumerate(ids):
        out[cid] = {
            "id": cid,
            "level": pick(levels, i, 1),
            "master": pick(masters, i, 0),
            "skill": pick(skills, i, 0),
            "ep": pick(eps, i, 0),
            "train": pick(trains, i, 0),
            "art": pick(arts, i, 0),
            "exclude": bool(pick(excludes, i, False)),
        }
    return out


def encode_cards(entries: dict[int, dict[str, Any]], ids_order: list[int] | None = None) -> dict[str, Any]:
    """卡牌列表 -> 云存储的 ``data.cards``（exportProfile 的逆）。

    :param entries: ``{cardId: 条目}``
    :param ids_order: 输出顺序；不传就按卡号排序（稳定、可复现）
    """
    order = list(ids_order) if ids_order else sorted(entries)
    order = [i for i in order if i in entries]
    return {
        "ids": encode_ids(order),
        "levels": rle_encode([entries[i]["level"] for i in order]),
        "masters": rle_encode([entries[i]["master"] for i in order]),
        "skills": rle_encode([entries[i]["skill"] for i in order]),
        "eps": rle_encode([entries[i]["ep"] for i in order]),
        "trains": rle_encode([entries[i]["train"] for i in order]),
        "arts": rle_encode([entries[i]["art"] for i in order]),
        "excludes": rle_encode([int(bool(entries[i]["exclude"])) for i in order]),
    }


# ---------------------------------------------------------------------
# 登录会话（cookie 持久化 —— 登录一次，之后全部后台调用）
# ---------------------------------------------------------------------


@dataclass
class ImportStats:
    """一次后台导入的统计。"""

    profiles_total: int = 0
    added: int = 0
    upgraded: int = 0          # 远端已有该卡（未特训），本地是特训后 -> 升级
    already: int = 0
    profiles_touched: int = 0

    def summary(self) -> str:
        return (
            f"档案 {self.profiles_total} 份：新增 {self.added}，"
            f"升级为特训 {self.upgraded}，已在远端 {self.already}"
        )


def new_card_entry(card_id: int, card: dict[str, Any] | None, trained: bool) -> dict[str, Any]:
    """构造一张新卡的条目 —— 默认值与 Bestdori 网页上手动添加一致。

    前端 ``onStartAction``（mode=1）添加时：
    ``level = levelLimit + (training ? training.levelLimit : 0)``、
    ``train/art = training ? 1 : 0``、``ep = len(stat.episodes)``、其余 0。

    ``card`` 为 ``None``（卡池快照缺失）时无法查稀有度，按调用方的 ``trained``
    语义设置特训标记 —— 宁可多给一个特训标记（Bestdori 页面上可改），
    也不把用户确认过的"特训后"悄悄降成"未特训"。
    """
    if card is None:
        return {
            "id": card_id,
            "level": 1,
            "master": 0,
            "skill": 0,
            "ep": 0,
            "train": 1 if trained else 0,
            "art": 1 if trained else 0,
            "exclude": False,
        }
    level_limit = int(card.get("levelLimit") or 1)
    stat = card.get("stat") if isinstance(card.get("stat"), dict) else {}
    training = stat.get("training") if isinstance(stat.get("training"), dict) else None
    episodes = stat.get("episodes") if isinstance(stat.get("episodes"), list) else None
    if trained and training is not None:
        level = level_limit + int(training.get("levelLimit") or 0)
        train, art = 1, 1
    else:
        level = level_limit
        train, art = 0, 0
    return {
        "id": card_id,
        "level": level,
        "master": 0,
        "skill": 0,
        "ep": len(episodes) if episodes else 0,
        "train": train,
        "art": art,
        "exclude": False,
    }


class BestdoriAccount:
    """已登录的 Bestdori 会话（cookie 持久化到本地）。

    登录一次之后，清单同步全部走后台 HTTP，不再弹出任何浏览器窗口。
    会话文件里是 Bestdori 下发的会话 cookie，等价于浏览器里的登录态 ——
    **不要外传**。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session_file: Path = settings.home / "session.json"
        self.cookies = httpx.Cookies()

    # ---- 会话持久化 ---------------------------------------------------

    def restore(self) -> bool:
        """从本地恢复会话；返回是否恢复出了可用的登录态。"""
        if not self.session_file.exists():
            return False
        try:
            raw = json.loads(self.session_file.read_text(encoding="utf-8"))
            for c in raw.get("cookies", []):
                self.cookies.set(
                    c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/")
                )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log.warning("会话文件损坏，忽略：%s", e)
            return False
        return self.me() is not None

    def save(self) -> None:
        raw = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
            for c in self.cookies.jar
        ]
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(
            json.dumps({"cookies": raw}, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # ---- 基础请求 -----------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url="https://bestdori.com",
            cookies=self.cookies,
            headers={"User-Agent": "BestdoriHelper/1.0"},
            timeout=30.0,
            follow_redirects=True,
        )

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        with self._client() as c:
            r = c.request(method, url, **kw)
            # 把服务端新下发的会话 cookie（login 的 Set-Cookie）取回。
            # httpx 的 Client(cookies=jar) 不保证把 Set-Cookie 写回传入的 jar，
            # 不取回的话下一次请求（me / profiles）就是匿名请求。
            self.cookies = c.cookies
            return r

    def me(self) -> dict[str, Any] | None:
        """登录态探针：已登录返回用户数据，未登录返回 ``None``。

        注意判定不能只认 ``result`` —— 那是**未登录**响应里才有的字段
        （``{"result": false, "code": "LOGIN_REQUIRED"}``），登录后大概率直接
        返回用户对象。所以以"是不是未登录"为准，而不是"有没有 result:true"。
        """
        try:
            r = self._request("GET", ME_URL)
            data = r.json()
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, dict) or not data:
            return None
        if data.get("code") == "LOGIN_REQUIRED" or data.get("result") is False:
            return None
        return data

    def login(self, username: str, password: str) -> dict[str, Any]:
        """账号密码登录；成功后会话 cookie 存盘，返回响应数据。"""
        r = self._request("POST", LOGIN_URL, json={"username": username, "password": password})
        data = r.json()
        if not (isinstance(data, dict) and data.get("result")):
            code = data.get("code") if isinstance(data, dict) else "UNKNOWN"
            raise RuntimeError(f"登录失败（{code}）。请检查用户名/密码。")
        self.save()
        return data

    # ---- 档案读写 -----------------------------------------------------

    def fetch_profiles(self) -> list[dict[str, Any]]:
        """拉取云端的全部档案（原始条目，未解码）。"""
        r = self._request("GET", PROFILES_URL)
        data = r.json()
        if not (isinstance(data, dict) and data.get("result")):
            code = data.get("code") if isinstance(data, dict) else "UNKNOWN"
            raise RuntimeError(f"读取档案失败（{code}）")
        profiles = data.get("profiles")
        if not isinstance(profiles, list):
            raise RuntimeError("读取档案失败：响应里没有 profiles 数组")
        return profiles

    def store_profiles(self, profiles: list[dict[str, Any]]) -> None:
        r = self._request("POST", PROFILES_URL, json={"profiles": profiles})
        data = r.json()
        if not (isinstance(data, dict) and data.get("result")):
            code = data.get("code") if isinstance(data, dict) else "UNKNOWN"
            raise RuntimeError(f"写入档案失败（{code}）")


# ---------------------------------------------------------------------
# 后台导入
# ---------------------------------------------------------------------


def import_inventory(
    account: BestdoriAccount,
    inv: Inventory,
    catalog: Catalog,
    *,
    profile_index: int = 0,
    only_confirmed: bool = False,
) -> ImportStats:
    """把清单增量合并进云端档案。

    读 → 合并 → **全量写回**（Bestdori 的写接口没有逐卡端点）。
    合并规则：

    * 远端没有这张卡 → **新增**（默认值与网页手动添加一致）
    * 远端有、但远端是未特训而本地是特训后 → **升级**（train/art 置 1，等级按
      特训后的上限拉满）；远端已特训而本地未特训则不动（本地数据更弱，不覆盖）
    * 两者一致 → 跳过
    """
    cards_data = inv.all()
    by_key = {(c.card_id, c.trained): c for c in cards_data}
    stats = ImportStats()

    # 等级上限 / 特训加成在**原始卡池 JSON** 里（Card 模型只留了元数据），
    # 快照缺失时降级为 level=1 —— 只影响显示的等级，不影响"拥有"语义
    raw_cards: dict[int, dict[str, Any]] = {}
    try:
        raw = json.loads(account.settings.cards_json.read_text(encoding="utf-8"))
        raw_cards = {int(k): v for k, v in raw.items() if isinstance(v, dict)}
    except (OSError, json.JSONDecodeError, ValueError):
        log.warning("卡池快照缺失，新卡的等级将按 1 处理")

    profiles = account.fetch_profiles()
    stats.profiles_total = len(profiles)
    if not profiles:
        raise RuntimeError("云端没有档案。请先到 Bestdori 的 Profile Manager 创建一个。")
    if not 0 <= profile_index < len(profiles):
        raise RuntimeError(f"档案序号 {profile_index} 不存在（共 {len(profiles)} 份）")

    touched = 0
    for pi, entry in enumerate(profiles):
        if pi != profile_index:
            continue
        cards_map = decode_cards(entry)
        data = entry["data"]
        for (cid, trained), oc in by_key.items():
            if only_confirmed and not oc.confirmed:
                continue
            meta = raw_cards.get(cid)
            existing = cards_map.get(cid)
            if existing is None:
                cards_map[cid] = new_card_entry(cid, meta, trained)
                stats.added += 1
            elif trained and not existing.get("train"):
                # 远端只有未特训，本地确认了特训后 -> 升级
                upgraded = new_card_entry(cid, meta, True)
                existing.update({k: upgraded[k] for k in ("train", "art", "level")})
                stats.upgraded += 1
            else:
                stats.already += 1

        data["cards"] = encode_cards(cards_map)
        touched += 1

    if touched:
        account.store_profiles(profiles)
    stats.profiles_touched = touched
    return stats
