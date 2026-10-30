"""诊断 SPECIAL 难度的连击数口径。

现象：easy/normal/hard/expert 的本地连击数与官方 `notes` 字段**全一致**
（3331 例），但 SPECIAL 有 114/161 不一致，且两个方向都有（本地可高可低）。
所以不是简单的「漏算 Directional」。

这个脚本拉一批 SPECIAL 原始谱面，把几个候选口径都算一遍，看哪个匹配率最高。

用法::

    python scripts/diag_special_notes.py               # 抽样 30 首
    python scripts/diag_special_notes.py --limit 0     # 全部 161 首
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / ".bdh-test" / "bdapi"
RAW = REPO / ".bdh-test" / "charts_special"


def fetch(sid: str) -> tuple[str, list | None]:
    RAW.mkdir(parents=True, exist_ok=True)
    p = RAW / f"{sid}.json"
    if p.exists():
        try:
            return sid, json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    url = f"https://bestdori.com/api/charts/{sid}/special.json"
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=45) as r:
                data = json.loads(r.read())
            p.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            return sid, data
        except Exception:  # noqa: BLE001
            time.sleep(1.5 ** attempt)
    return sid, None


def candidates(chart: list[dict]) -> dict[str, int]:
    c = Counter(e.get("type") for e in chart)
    sl_pts = sum(len(e.get("connections") or []) for e in chart if e.get("type") == "Slide")
    sl_vis = sum(
        1
        for e in chart
        if e.get("type") == "Slide"
        for p in (e.get("connections") or [])
        if not p.get("hidden")
    )
    sl_cnt = c.get("Slide", 0)
    lg_pts = sum(len(e.get("connections") or []) for e in chart if e.get("type") == "Long")
    lg_cnt = c.get("Long", 0)
    base = c.get("Single", 0) + c.get("Flick", 0)
    direc = c.get("Directional", 0)
    return {
        "A 旧 S+L2+SlidePts": base + lg_pts + sl_pts,
        "B +Directional": base + direc + lg_pts + sl_pts,
        "C Slide记1": base + lg_pts + sl_cnt,
        "D +Dir,Slide记1": base + direc + lg_pts + sl_cnt,
        "E Long记1": base + lg_cnt + sl_pts,
        "F 纯事件数": base + lg_cnt + sl_cnt + direc,
        "H +Dir,跳过hidden": base + direc + lg_pts + sl_vis,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args()

    detail = json.loads((CACHE / "songs_detail.json").read_text(encoding="utf-8"))
    ids = [sid for sid, d in detail.items() if (d.get("notes") or {}).get("4") is not None]
    if args.limit:
        ids = ids[: args.limit]
    print(f"诊断 {len(ids)} 首 SPECIAL 谱面")

    rows = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for f in as_completed([pool.submit(fetch, s) for s in ids]):
            sid, chart = f.result()
            if not chart:
                continue
            want = detail[sid]["notes"]["4"]
            rows.append((sid, detail[sid].get("musicTitle", [""])[0], want, candidates(chart)))

    hit = Counter()
    for _, _, want, cand in rows:
        for k, v in cand.items():
            if v == want:
                hit[k] += 1
    n = len(rows)
    print(f"\n匹配率（共 {n} 首）：")
    for k, v in sorted(hit.items(), key=lambda x: -x[1]):
        print(f"  {k:24} {v:>3}/{n}  {v / n * 100:5.1f}%")

    print("\n前 10 首明细（官方 / 各候选）：")
    print(f"  {'sid':>4} {'标题':16} {'官方':>5} " + " ".join(f"{k.split()[0]:>6}" for k in hit))
    keys = list(hit)
    for sid, title, want, cand in rows[:10]:
        vals = " ".join(f"{cand[k]:>6}" for k in keys)
        print(f"  {sid:>4} {title[:16]:16} {want:>5} {vals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
