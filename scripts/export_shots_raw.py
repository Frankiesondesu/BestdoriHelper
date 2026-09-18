"""导出整张截图的原始 RGB —— 供 Node 端验证 JS 版网格切分。

网页版要自己完成切分（不像 `export_cells_raw.py` 那样让 Python 切好），
所以需要把**整张**截图交给 JS，看它能否自己定位卡面区并切出 28 格。

用法::

    python scripts/export_shots_raw.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".bdh-test/site_build")
    args = ap.parse_args()

    out_dir = REPO / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    shots = sorted((REPO / "Photo").glob("*.jpg"))
    if not shots:
        print("Photo/ 里没有截图")
        return 1

    records: list[dict] = []
    offset = 0
    with (out_dir / "shots_rgb.bin").open("wb") as fh:
        for p in shots:
            with Image.open(p) as im:
                rgb = im.convert("RGB")
                w, h = rgb.size
                buf = rgb.tobytes()
            fh.write(buf)
            records.append({
                "name": p.name, "width": w, "height": h,
                "offset": offset, "bytes": len(buf),
            })
            offset += len(buf)

    (out_dir / "shots_index.json").write_text(
        json.dumps(records, ensure_ascii=False), encoding="utf-8")
    size_mb = (out_dir / "shots_rgb.bin").stat().st_size / 1e6
    print(f"导出 {len(records)} 张截图，{size_mb:.1f} MB")
    for r in records:
        print(f"  {r['name'][:40]}  {r['width']}x{r['height']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
