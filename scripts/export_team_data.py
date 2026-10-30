"""把 Bestdori 的卡牌 / 技能数据转成网页组队助手用的精简格式。

**为什么需要这一步**：`docs/data/cards.json` 只有卡名/属性/星级/等级上限，
没有**三围**和**技能**，而这两样是出分计算的必需输入。

**为什么不直接前端拉全量**：`cards/all.5.json` 有 1.46 MB，里面塞了
全等级三围、5 种语言的文案、发布日期……组队只用到很小一部分。这里压成
「满级三围 + 1 级三围 + 技能 id + 技能时长/加成」，体积降到十分之一。

产物：`docs/data/team.json`

用法::

    # 先拉到本地缓存（已存在则跳过）
    python scripts/fetch_bestdori_data.py
    python scripts/export_team_data.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / ".bdh-test" / "bdapi"
OUT = REPO / "docs" / "data" / "team.json"

#: simpleDescription 里的语言下标（0 日 / 1 英 / 2 繁 / 3 简 / 4 韩）
LANG_ZH = 3

#: 乐队 id -> 名称。只有 9 个且极少变动，内置比多拉一次 API 划算。
#: ⚠️ 这些编号**不是 1~9 顺排**，是从 characters.json 里实测出来的：
#:   3=HHW、4=Pastel、5=Roselia（3 和 4 极易写反）
#:   RAS=18、Morfonica=21、MyGO=45（不是 6/7/8）
BANDS = {
    1: "Poppin'Party",
    2: "Afterglow",
    3: "Hello, Happy World!",
    4: "Pastel*Palettes",
    5: "Roselia",
    18: "RAISE A SUILEN",
    21: "Morfonica",
    45: "MyGO!!!!!",
}


def skill_up_percent(descs: list[str]) -> int:
    """从「得分提升60%」这类描述里取加成百分比。

    技能等级只影响**持续时间**和发动率，不影响加成值 —— 所以这里取一个数
    就够了。回复/护盾/判定类技能没有百分比，返回 0（在纯分数模型里贡献 0，
    这一点在 UI 上会注明）。
    """
    for text in (descs[LANG_ZH], descs[0] if descs else ""):
        # ⚠️ 游戏文案里全角「％」和半角「%」混用（"得分提升100％"），
        # 只匹配半角会把一半技能读成 0。
        m = re.search(r"(\d+)\s*[%％]", text or "")
        if m:
            return int(m.group(1))
    return 0


def main() -> int:
    cards_raw = json.loads((CACHE / "cards.json").read_text(encoding="utf-8"))
    skills_raw = json.loads((CACHE / "skills.json").read_text(encoding="utf-8"))

    # ---- 技能：id -> {时长[5 级], 加成%} ----
    skills: dict[str, dict] = {}
    for sid, s in skills_raw.items():
        dur = s.get("duration") or [5]
        skills[str(sid)] = {
            "d": [round(float(x), 1) for x in dur],          # 等级 1~5 的秒数
            "up": skill_up_percent(s.get("simpleDescription") or []),
            "desc": (s.get("simpleDescription") or [""])[LANG_ZH],
        }

    # ---- 卡牌：只保留算分需要的字段 ----
    out_cards: dict[str, dict] = {}
    for cid, c in cards_raw.items():
        stat = c.get("stat") or {}
        lv1 = stat.get("1") or {}
        # levelLimit 是**特训前**的上限（如 40），而 stat 的键是
        # "1"（1 级）和特训后的满级（如 "50"）。所以满级取最大的数字键，
        # 不能拿 levelLimit 去查 —— 实测有 1652 张卡因此被跳过。
        num_keys = [int(k) for k in stat if str(k).isdigit()]
        limit = max(num_keys) if num_keys else int(c.get("levelLimit") or 1)
        lvmax = stat.get(str(limit)) or {}
        # 剧情加成：episodes 是 [{performance, technique, visual}, ...]
        ep = {"performance": 0, "technique": 0, "visual": 0}
        for item in stat.get("episodes") or []:
            for k in ep:
                ep[k] += int(item.get(k) or 0)
        if not lvmax:
            continue
        out_cards[str(cid)] = {
            "ch": c.get("characterId"),
            "r": c.get("rarity"),
            "a": c.get("attribute"),
            "ty": c.get("type"),
            "sk": c.get("skillId"),
            "ll": limit,
            "s1": [int(lv1.get("performance") or 0),
                   int(lv1.get("technique") or 0),
                   int(lv1.get("visual") or 0)],
            "sx": [int(lvmax.get("performance") or 0),
                   int(lvmax.get("technique") or 0),
                   int(lvmax.get("visual") or 0)],
            "ep": [ep["performance"], ep["technique"], ep["visual"]],
        }

    # ---- 角色 / 乐队 ----
    site = json.loads((REPO / "docs" / "data" / "cards.json").read_text(encoding="utf-8"))
    chars_raw = json.loads((CACHE / "characters.json").read_text(encoding="utf-8"))
    chars: dict[str, dict] = {}
    for cid, c in chars_raw.items():
        band_id = int(c.get("bandId") or 0)
        names = c.get("characterName") or []
        chars[str(cid)] = {
            "n": (names[LANG_ZH] if len(names) > LANG_ZH else (names[0] if names else "")),
            "b": BANDS.get(band_id, "其他"),
        }
    bands = sorted({v["b"] for v in chars.values()})

    payload = {
        "server": site.get("server", "cn"),
        "skills": skills,
        "cards": out_cards,
        "characters": chars,
        "bands": sorted(bands),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"✓ {OUT.relative_to(REPO)}  卡 {len(out_cards)} / 技能 {len(skills)} "
          f"/ 角色 {len(chars)} / 乐队 {len(bands)}  {kb:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
