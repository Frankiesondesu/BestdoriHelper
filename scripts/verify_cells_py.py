"""Python 版对照检索 —— 与 JS 版（`docs/build/verify_cells.mjs`）跑同一批格子。

两边输入完全相同（同一份 `cells_rgb.bin`），差异只可能来自特征或检索实现。
把两边的 top-1 卡号列出来逐格对比，就能判断 JS 实现是否正确。

用法::

    python scripts/verify_cells_py.py --json .bdh-test/site_build/py_result.json
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
from bestdori_helper.vision.features import compute_features  # noqa: E402
from bestdori_helper.vision.index import FingerprintIndex  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=".bdh-test/site_build")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    src = REPO / args.src
    records = json.loads((src / "cells_index.json").read_text(encoding="utf-8"))

    settings = Settings()
    index = FingerprintIndex.load(settings.index_path)
    if index is None:
        print(f"指纹库不存在：{settings.index_path}")
        return 1
    print(f"指纹库 {len(index)} 条（{settings.index_path.name}）；待识别 {len(records)} 格\n")

    raw = (src / "cells_rgb.bin").read_bytes()
    results = []
    for rec in records:
        w, h = rec["width"], rec["height"]
        buf = raw[rec["offset"]: rec["offset"] + rec["bytes"]]
        img = Image.frombytes("RGB", (w, h), buf)
        feats = compute_features(img)
        cands = index.search(feats, top_k=6)
        top = cands[0]
        second = cands[1] if len(cands) > 1 else None
        results.append({
            "shot": rec["shot"],
            "cell": rec["cell"],
            "cardId": top.card_id,
            "trained": bool(top.trained),
            "score": round(float(top.score), 4),
            "hashSim": round(float(top.hash_similarity), 4),
            "colorSim": round(float(top.color_similarity), 4),
            "gap": round(float(top.score - (second.score if second else 0.0)), 4),
            "top3": [[c.card_id, bool(c.trained), round(float(c.score), 4)] for c in cands[:3]],
        })

    scores = np.array([r["score"] for r in results])
    gaps = np.array([r["gap"] for r in results])
    print("=" * 56)
    print(f"共 {len(results)} 格")
    print(f"置信度：均值 {scores.mean():.3f}  中位 {np.median(scores):.3f}  "
          f"最小 {scores.min():.3f}  最大 {scores.max():.3f}")
    print(f"与次优差距：中位 {np.median(gaps):.4f}  最小 {gaps.min():.4f}")
    for t in (0.9, 0.85, 0.8, 0.75, 0.7, 0.66):
        hit = int((scores >= t).sum())
        print(f"  置信度 >= {t:.2f} : {hit}/{len(scores)} = {hit/len(scores)*100:.1f}%")

    if args.json:
        (REPO / args.json).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
        print(f"\n结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
