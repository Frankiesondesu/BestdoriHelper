"""把真实游戏截图切成卡面格，导出原始 RGB —— 供 Node 端做端到端检索验证。

网页版最终要自己用 JS 切分网格，但**切分和检索是两件事**。这里先用 Python
已有的切分（已经验证过 5/5 切对 28 格）把格子导出来，好让 Node 侧专注验证
「特征 + 检索」这一段是否正确。

用法::

    python scripts/export_cells_raw.py --out .bdh-test/site_build
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestdori_helper.vision.detect import crop_box, detect_boxes  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
#: 实测的卡面网格外框（原图 2400x1080 像素，归一化后）
REGION = (704 / 2400, 292 / 1080, 1896 / 2400, 972 / 1080)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".bdh-test/site_build")
    ap.add_argument("--rows", type=int, default=4)
    ap.add_argument("--cols", type=int, default=7)
    args = ap.parse_args()

    out_dir = REPO / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    bin_path = out_dir / "cells_rgb.bin"
    idx_path = out_dir / "cells_index.json"

    shots = sorted((REPO / "Photo").glob("*.jpg"))
    if not shots:
        print("Photo/ 里没有截图")
        return 1

    records: list[dict] = []
    offset = 0
    with bin_path.open("wb") as fh:
        for si, p in enumerate(shots, 1):
            with Image.open(p) as im:
                img = im.convert("RGB")
            boxes = detect_boxes(img, rows=args.rows, cols=args.cols, region=REGION)
            print(f"截图 {si}：{p.name[:32]} -> {len(boxes)} 格")
            for bi, b in enumerate(boxes):
                cell = crop_box(img, b)
                w, h = cell.size
                buf = cell.tobytes()
                fh.write(buf)
                records.append({
                    "shot": si, "cell": bi, "box": [b.x, b.y, b.w, b.h],
                    "width": w, "height": h, "offset": offset, "bytes": len(buf),
                })
                offset += len(buf)

    idx_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    print(f"\n导出 {len(records)} 格")
    print(f"  {bin_path}  {bin_path.stat().st_size/1e6:.1f} MB")
    print(f"  {idx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
