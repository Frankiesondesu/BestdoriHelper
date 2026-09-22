"""导出网页版静态数据：卡牌元数据 JSON + WebP 缩略图。

产出（都在 docs/data/ 下）::

    cards.json          卡牌 / 角色 / 乐队元数据（只留简体中文，短键名压体积）
    thumbs/{id}_{n|t}.webp   卡面缩略图，前端按需加载（只取候选那几十张）

缩略图只用于**显示给人核对**，不参与识别计算（识别用的是 fingerprints.bin），
所以可以放心用有损压缩。

用法::

    python scripts/export_site_data.py
    python scripts/export_site_data.py --quality 78 --no-thumbs   # 只导元数据
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# 同目录的 build_thumbs_manifest 也要能 import（直接跑脚本时 Python 会自动加
# 脚本目录，但被当模块导入时不会 —— 显式加上更稳）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bestdori_helper.config import Settings  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
#: 多语言数组里简体中文的下标（实测顺序：日 / 英 / 繁中 / 简中 / 韩）
LANG_CN = 3


def pick(arr, idx: int = LANG_CN) -> str:
    """从多语言数组里取简体中文，缺失则回退到日文/英文/第一项。"""
    if not isinstance(arr, list) or not arr:
        return ""
    for i in (idx, 0, 1):
        if i < len(arr) and arr[i]:
            return str(arr[i])
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quality", type=int, default=80, help="WebP 质量")
    ap.add_argument("--no-thumbs", action="store_true", help="跳过缩略图转换")
    args = ap.parse_args()

    settings = Settings()
    home = settings.home / "data"
    out_dir = REPO / "docs" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 元数据 ----------------------------------------------------
    cards_raw = json.loads((home / f"cards.{settings.server.value}.json").read_text("utf-8"))
    chars_raw = json.loads((home / f"characters.{settings.server.value}.json").read_text("utf-8"))
    bands_raw = json.loads((home / f"bands.{settings.server.value}.json").read_text("utf-8"))

    cards = {}
    for k, v in cards_raw.items():
        cid = int(v.get("characterId") or 0)
        stat = v.get("stat") if isinstance(v.get("stat"), dict) else {}
        # 注意：training 必须区分「不是 dict」和「是 dict 但 levelLimit=0」——
        # 实测有 153 张 4★ 卡的 training.levelLimit 就是 0，但它们是**可特训**的。
        # 所以下面要单独导出一个 tr 标志，不能靠 tl > 0 推断。
        training = stat.get("training") if isinstance(stat.get("training"), dict) else None
        episodes = stat.get("episodes") if isinstance(stat.get("episodes"), list) else []
        cards[k] = {
            "ch": cid,                                   # 角色 ID
            "r": int(v.get("rarity") or 0),              # 星级
            "a": v.get("attribute") or "",               # 属性
            "t": pick(v.get("prefix")),                  # 卡名
            "ty": v.get("type") or "",                   # 卡类型
            "rs": v.get("resourceSetName") or "",        # 资源集名
            # ↓ 这几个只有「同步到 Bestdori」用得到：构造新卡条目时要按
            #   level = levelLimit + (trained ? training.levelLimit : 0) 算等级，
            #   ep 取剧情数，tr 表示这张卡有没有特训形态。
            "ll": int(v.get("levelLimit") or 1),         # 等级上限
            "tl": int((training or {}).get("levelLimit") or 0),  # 特训后的等级加成
            "tr": 1 if training is not None else 0,      # 是否有特训形态
            "ep": len(episodes),                         # 剧情数
        }
    characters = {}
    for k, v in chars_raw.items():
        characters[k] = {
            "n": pick(v.get("characterName")),           # 角色名
            "b": int(v.get("bandId") or 0),              # 乐队 ID
            "c": v.get("colorCode") or "",               # 应援色
        }
    bands = {k: {"n": pick(v.get("bandName"))} for k, v in bands_raw.items()}

    meta = {
        "server": settings.server.value,
        "source": settings.image_source,
        "cards": cards,
        "characters": characters,
        "bands": bands,
    }
    meta_path = out_dir / "cards.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                         encoding="utf-8")
    print(f"cards.json：{len(cards)} 张卡 / {len(characters)} 角色 / {len(bands)} 乐队"
          f"  {meta_path.stat().st_size/1024:.0f} KB")

    if args.no_thumbs:
        return 0

    # ---- 缩略图 ----------------------------------------------------
    # 用指纹库的顺序（= 实际存在的变体），避免导出不存在的组合
    import numpy as np

    z = np.load(settings.index_path)
    thumbs_dir = out_dir / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    written = skipped = 0
    total_bytes = 0
    for cid, tr in zip(z["card_ids"], z["trained"]):
        cid = int(cid)
        tr = bool(tr)
        rs = cards.get(str(cid), {}).get("rs", "")
        if not rs:
            skipped += 1
            continue
        suffix = "after_training" if tr else "normal"
        src = settings.source_dir / f"{rs}_{suffix}.png"
        if not src.exists():
            alt = settings.source_dir / f"{rs}_after_training.png"
            src = alt if alt.exists() else None
        if src is None or not src.exists():
            skipped += 1
            continue
        dst = thumbs_dir / f"{cid}_{'t' if tr else 'n'}.webp"
        if dst.exists():
            written += 1
            total_bytes += dst.stat().st_size
            continue
        with Image.open(src) as im:
            im.convert("RGBA").save(dst, "WEBP", quality=args.quality, method=5)
        written += 1
        total_bytes += dst.stat().st_size
        if written % 500 == 0:
            print(f"  缩略图 {written} 张，累计 {total_bytes/1e6:.1f} MB", flush=True)

    print(f"\n缩略图：{written} 张，跳过 {skipped}，合计 {total_bytes/1e6:.1f} MB")
    print(f"  {thumbs_dir}")
    avg = total_bytes / max(1, written) / 1024
    print(f"  平均 {avg:.1f} KB/张")

    # ---- 缩略图清单 -------------------------------------------------
    # 前端必须知道**哪些卡真的有图**，否则只能「先请求 _n、404 了再换 _t」，
    # 而两种形态都没有图的卡（实测 188 张，campaign / special 类）换了也没用，
    # 只会留下坏图 + 两条 404。自选卡面按卡号倒序排，开头正好是这批，
    # 用户看到的就是「全部 404」。
    print()
    from build_thumbs_manifest import build as build_manifest
    rc = build_manifest(thumbs_dir, out_dir / "thumbs.json")
    if rc != 0:
        print("! 缩略图清单生成失败 —— 前端会退回按需请求（可能有 404）", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
