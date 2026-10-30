"""校验谱面连击数口径：把本地 `docs/data/charts/` 的 note 数
和 Bestdori 单曲接口的官方 `notes` 字段逐难度比对。

批量接口 `/api/songs/all.*.json` **没有** `notes` 字段，只有单曲接口
`/api/songs/<id>.json` 有，所以要把 824 首逐个拉一遍（并发，约 2 分钟）。
结果缓存在 `.bdh-test/bdapi/songs_detail.json`，重复跑不重抓。

用法::

    python scripts/verify_chart_notes.py
    python scripts/verify_chart_notes.py --refresh   # 重新拉取
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / ".bdh-test" / "bdapi"
DETAIL = CACHE / "songs_detail.json"
CHARTS = REPO / "docs" / "data" / "charts"

DIFFS = {"easy": 0, "normal": 1, "hard": 2, "expert": 3, "special": 4}


def grab(sid: str) -> tuple[str, dict | None]:
    url = f"https://bestdori.com/api/songs/{sid}.json"
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=45) as r:
                return sid, json.loads(r.read())
        except Exception:  # noqa: BLE001 —— 断连/超时/JSON 都重试
            time.sleep(1.5 ** attempt)
    return sid, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    songs = json.loads((CACHE / "songs.json").read_text(encoding="utf-8"))
    if DETAIL.exists() and not args.refresh:
        detail = json.loads(DETAIL.read_text(encoding="utf-8"))
        print(f"用缓存 {DETAIL.name}（{len(detail)} 首）")
    else:
        print(f"拉取 {len(songs)} 首单曲详情，并发 {args.workers}")
        detail: dict[str, dict] = {}
        fails = []
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = [pool.submit(grab, sid) for sid in songs]
            for i, f in enumerate(as_completed(futs), 1):
                sid, d = f.result()
                if d is None:
                    fails.append(sid)
                else:
                    detail[sid] = d
                if i % 200 == 0:
                    print(f"  {i}/{len(songs)}  {time.time() - t0:.0f}s")
        DETAIL.write_text(json.dumps(detail, separators=(",", ":")), encoding="utf-8")
        print(f"完成 {len(detail)} 首，失败 {len(fails)}，用时 {(time.time() - t0) / 60:.1f} 分钟")
        if fails:
            print("  失败:", fails[:20])

    ok = bad = skip = 0
    diffs: list[tuple] = []
    for sid, d in detail.items():
        notes = d.get("notes") or {}
        diff = d.get("difficulty") or {}
        for name, idx in DIFFS.items():
            if str(idx) not in diff:
                continue
            want = notes.get(str(idx))
            p = CHARTS / name / f"{sid}.json"
            if want is None or not p.exists():
                skip += 1
                continue
            got = len(json.loads(p.read_text(encoding="utf-8")).get("n") or [])
            if got == want:
                ok += 1
            else:
                bad += 1
                diffs.append((name, sid, d.get("musicTitle", [""])[0], got, want))

    print(f"\n一致 {ok}   不一致 {bad}   跳过 {skip}")
    for name, sid, title, got, want in diffs[:20]:
        print(f"  ✗ {name:7} {sid:>4} {title[:18]:18} 本地 {got} / 官方 {want}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
