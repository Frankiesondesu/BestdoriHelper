"""生成 Bestdori 编解码的对照测试用例（Python 侧基准）。

JS 移植（`docs/src/bestdori.js`）必须和 Python 实现输出**逐字节一致** ——
云端档案的格式是从 Bestdori 前端逆向的，差一个字节就会写坏用户的档案。

所以做法是：Python 算一遍当基准，Node 读同一批用例再算一遍，两边比对。
比"读代码确认逻辑一致"可靠得多。

用法::

    python scripts/export_codec_cases.py
    node docs/build/verify_codec.mjs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestdori_helper.bridge import bestdori_api as api  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".bdh-test/site_build/codec_cases.json")
    args = ap.parse_args()

    cases: dict[str, list] = {"rle": [], "ids": [], "cards": [], "entries": []}

    # ---- 游程编解码 ------------------------------------------------
    rle_inputs = [
        [],
        [1],
        [5, 5, 5],
        [1, 2, 3, 4],
        [0, 0, 0, 0, 0, 0, 0, 0],
        [1, 1, 2, 2, 2, 3, 1, 1, 1, 1],
        [0, 1, 0, 1, 0, 1],
        [40, 40, 40, 40, 40, 40, 40, 40, 40, 50],
        [True, True, False, False, False],
    ]
    for arr in rle_inputs:
        cases["rle"].append({
            "input": arr,
            "encoded": api.rle_encode(arr),
            "decoded": api.rle_decode(api.rle_encode(arr)),
        })

    # ---- 卡号编解码 ------------------------------------------------
    # 注意 ID_BIG=24464 那个兼容分支：解码时 >24464 会 +65536，
    # 编码不反向减 —— 所以只保证「小卡号往返一致」，大卡号是单向的。
    id_lists = [
        [],
        [1],
        [158],
        [1, 2, 3, 4, 5],
        [24463, 24464, 24465],
        [65535],
        [1, 158, 2468, 10048, 24464],
        list(range(1, 40)),
    ]
    for ids in id_lists:
        cases["ids"].append({
            "ids": ids,
            "b64": api.encode_ids(ids),
            "decoded": api.decode_ids(api.encode_ids(ids)),
        })

    # ---- 卡牌集合编解码 --------------------------------------------
    card_sets = [
        {},
        {158: api.new_card_entry(158, {"levelLimit": 40, "stat": {"training": {"levelLimit": 10}, "episodes": [1, 2]}}, True)},
        {
            1: api.new_card_entry(1, {"levelLimit": 20, "stat": {"episodes": [1, 2]}}, False),
            2: api.new_card_entry(2, {"levelLimit": 20, "stat": {"episodes": [1, 2]}}, True),
            3: api.new_card_entry(3, {"levelLimit": 30, "stat": {"episodes": [1]}}, False),
        },
        # 有 exclude 的
        {
            100: {**api.new_card_entry(100, None, False), "exclude": True},
            101: api.new_card_entry(101, None, True),
        },
        # 长列表，压一压游程编码
        {i: api.new_card_entry(i, {"levelLimit": 50, "stat": {"episodes": [1, 2, 3]}}, True)
         for i in range(1000, 1060)},
    ]
    for entries in card_sets:
        encoded = api.encode_cards(entries)
        decoded = api.decode_cards({
            "compression": api.SUPPORTED_COMPRESSION,
            "data": {"cards": encoded},
        })
        cases["cards"].append({
            "entries": {str(k): v for k, v in entries.items()},
            "encoded": encoded,
            "decoded": {str(k): v for k, v in decoded.items()},
        })

    # ---- 新卡条目默认值 --------------------------------------------
    # 注意：Python 的 new_card_entry 吃的是**原始卡池 JSON**，而网页版为了体积
    # 只导出紧凑字段（ll / tl / ep）。所以这里两种形式都给出来：
    # JS 侧用 metaCompact 调用（那是它运行时真正拿到的形状）。
    def compact_meta(card: dict | None) -> dict | None:
        if card is None:
            return None
        stat = card.get("stat") if isinstance(card.get("stat"), dict) else {}
        training = stat.get("training") if isinstance(stat.get("training"), dict) else None
        episodes = stat.get("episodes") if isinstance(stat.get("episodes"), list) else []
        return {
            "ll": int(card.get("levelLimit") or 1),
            "tl": int((training or {}).get("levelLimit") or 0),
            # tr 必须单独给：有 153 张 4★ 卡的 training.levelLimit 就是 0，
            # 但它们可特训 —— 靠 tl > 0 判断会判错
            "tr": 1 if training is not None else 0,
            "ep": len(episodes),
        }

    entry_inputs = [
        (158, {"levelLimit": 40, "stat": {"training": {"levelLimit": 10}, "episodes": [1, 2]}}, True),
        (158, {"levelLimit": 40, "stat": {"training": {"levelLimit": 10}, "episodes": [1, 2]}}, False),
        (1, {"levelLimit": 20, "stat": {"episodes": [1, 2]}}, True),   # 无 training -> 不可特训
        (1, {"levelLimit": 20, "stat": {"episodes": [1, 2]}}, False),
        (999, None, True),                                              # 快照缺失
        (999, None, False),
        (7, {"levelLimit": 30}, True),                                  # 无 stat
        (8, {"levelLimit": 30, "stat": {}}, False),
        # 关键边界：training 存在但 levelLimit=0（真实数据里有 153 张这样的 4★）
        (1152, {"levelLimit": 50, "stat": {"training": {"levelLimit": 0}, "episodes": [1]}}, True),
        (1152, {"levelLimit": 50, "stat": {"training": {"levelLimit": 0}, "episodes": [1]}}, False),
    ]
    for cid, meta, trained in entry_inputs:
        cases["entries"].append({
            "cardId": cid,
            "meta": meta,
            "metaCompact": compact_meta(meta),
            "trained": trained,
            "out": api.new_card_entry(cid, meta, trained),
        })

    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"生成用例：rle {len(cases['rle'])} / ids {len(cases['ids'])} / "
          f"cards {len(cases['cards'])} / entries {len(cases['entries'])}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
