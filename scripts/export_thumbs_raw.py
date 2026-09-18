"""把卡面缩略图导出成「原始 RGB 大文件 + 索引」，供 Node 端算指纹库。

## 为什么要这么绕

网页版的指纹库必须和浏览器端用**同一份特征代码**算出来（`docs/src/features.js`），
否则 JS 的重采样和 PIL 的 LANCZOS 不一致，库端和查询端的特征对不上，检索直接崩。

所以构建流程是：

    缩略图 PNG（调色板模式）
        │  ← 这一步用 Python：PIL 处理调色板 PNG 很稳，Node 没有零依赖的解法
        ▼
    thumbs_rgb.bin + thumbs_index.json（原始 RGB + 偏移索引）
        │  ← 这一步用 Node：跑 features.js，与浏览器端完全同一份代码
        ▼
    docs/data/fingerprints.bin

## 为什么不用 pngjs

Bestdori 的缩略图是 **PNG 调色板模式（colorType=3）**，而 pngjs 只支持
0/2/4/6，读不了。用 sharp 又要原生依赖。既然构建是一次性的，
让 Python 解码、Node 算特征是最省事且零运行时依赖的组合。

用法::

    python scripts/export_thumbs_raw.py --out .bdh-test/site_build
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestdori_helper.config import Settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".bdh-test/site_build", help="输出目录")
    ap.add_argument("--home", default=None, help="数据目录，默认 ~/.bestdori-helper")
    args = ap.parse_args()

    settings = Settings() if args.home is None else Settings(home=Path(args.home))
    idx_path = settings.index_path
    if not idx_path.exists():
        print(f"指纹库不存在：{idx_path}")
        return 1

    # 指纹库里的 (card_id, trained) 顺序就是导出顺序 —— 保证两边一一对应
    z = np.load(idx_path)
    card_ids = z["card_ids"]
    trained = z["trained"]
    print(f"指纹库 {len(card_ids)} 条记录，来源 {settings.image_source}")
    print(f"缩略图目录：{settings.source_dir}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    bin_path = out_dir / "thumbs_rgb.bin"
    idx_out = out_dir / "thumbs_index.json"

    # 读 catalog 快照拿 resourceSetName（指纹库里只存 card_id）
    cat_file = settings.home / "data" / f"cards.{settings.server.value}.json"
    if not cat_file.exists():
        print(f"卡牌快照不存在：{cat_file}（先跑 bdh sync）")
        return 1
    raw_cards = json.loads(cat_file.read_text(encoding="utf-8"))
    rs_of = {int(k): (v.get("resourceSetName") or "").strip() for k, v in raw_cards.items()}

    records: list[dict] = []
    offset = 0
    missing = 0

    with bin_path.open("wb") as fh:
        for cid, tr in zip(card_ids, trained):
            cid = int(cid)
            tr = bool(tr)
            rs = rs_of.get(cid, "")
            suffix = "after_training" if tr else "normal"
            path: Path | None = settings.source_dir / f"{rs}_{suffix}.png"
            if not path.exists():
                # 回退：约 6.5% 的卡没有 _normal，卡面只在 _after_training
                alt = settings.source_dir / f"{rs}_after_training.png"
                path = alt if alt.exists() else None
            if path is None or not path.exists():
                missing += 1
                records.append({
                    "cardId": cid, "trained": tr, "width": 0, "height": 0,
                    "offset": offset, "bytes": 0,
                })
                continue
            with Image.open(path) as im:
                rgb = im.convert("RGB")
                w, h = rgb.size
                buf = rgb.tobytes()
            fh.write(buf)
            records.append({
                "cardId": cid, "trained": tr, "width": w, "height": h,
                "offset": offset, "bytes": len(buf),
            })
            offset += len(buf)

    idx_out.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    size_mb = bin_path.stat().st_size / 1e6
    print(f"\n导出完成：{len(records)} 条，缺图 {missing} 条")
    print(f"  {bin_path}  {size_mb:.1f} MB")
    print(f"  {idx_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
