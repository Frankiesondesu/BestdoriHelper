"""并发重抓谱面（`fetch_charts.py` 的加速版，只做一次性修复用）。

背景：`fetch_charts.py` 是串行 + 0.4s 间隔，单次请求实测 1.4~2.5 秒，
3457 个文件要 **2 小时以上**。修 NOTE 计数那次要全量重抓，串行不现实。

这个脚本只做一件事：把 `fetch_charts.py` 的解析逻辑（`beat_to_time`）套到
并发抓取上。**解析规则复用 `fetch_charts`，不重复实现**，避免两边漂移。

用法::

    python scripts/refetch_charts_parallel.py --workers 6
    python scripts/refetch_charts_parallel.py --diff hard --workers 6
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from fetch_charts import DIFFS, OUT_ROOT, chart_payload  # noqa: E402

CACHE = REPO / ".bdh-test" / "bdapi"


def grab(job: tuple[str, str, Path]) -> tuple[str, str, str]:
    """返回 (状态, 标识, 说明)。

    ⚠️ 必须 catch 宽泛异常：实测服务器会直接断连，抛的是
    `http.client.RemoteDisconnected`（继承 `ConnectionResetError`），
    它**不是** `URLError` 的子类 —— 只 catch URLError 会让整个任务崩掉。
    """
    diff, sid, out = job
    url = f"https://bestdori.com/api/charts/{sid}/{diff}.json"
    last = ""
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=45) as r:
                raw = r.read()
            payload = chart_payload(json.loads(raw))
            if not payload["n"]:
                return ("skip", f"{diff}/{sid}", "空谱面")
            out.write_text(
                json.dumps(payload, separators=(",", ":")),
                encoding="utf-8",
            )
            return ("ok", f"{diff}/{sid}", str(len(payload["n"])))
        except Exception as e:  # noqa: BLE001 —— 断连/超时/JSON 都要重试
            last = f"{type(e).__name__}: {e}"
            time.sleep(1.5 ** attempt)
    return ("fail", f"{diff}/{sid}", last)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--diff", default="all", choices=[*DIFFS, "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume-minutes", type=int, default=0,
                    help="跳过最近 N 分钟内已更新过的文件（断点续跑用）")
    ap.add_argument("--only-stale", action="store_true",
                    help="只重抓仍是旧格式（缺 s/f 键）的文件，幂等续跑用")
    args = ap.parse_args()

    songs = json.loads((CACHE / "songs.json").read_text(encoding="utf-8"))
    diffs = list(DIFFS) if args.diff == "all" else [args.diff]

    jobs: list[tuple[str, str, Path]] = []
    cutoff = time.time() - args.resume_minutes * 60 if args.resume_minutes else 0
    skipped_recent = 0

    def is_stale(p: Path) -> bool:
        """旧格式只有 {d, n}；新格式会带 s（技能点）或 f（Fever）。

        注意：确实存在既无技能点也无 Fever 的谱面，所以不能拿
        「有没有 s/f」当新旧判据 —— 只能看有没有多出来的键。
        `chart_payload` 在无技能点/无 Fever 时确实会省掉这两个键，
        所以判据取「键集合是否 ⊆ {d, n}」。

        ⚠️ 还要校验 **`s` 的长度必须是 6**。踩过：`special/786` 有 17 个带
        `skill` 标记的音符（Slide 每个连接点都标），早期版本按音符去重，
        写出 17 个触发点；而 `is_stale` 只看「有没有 s 键」，于是这个错文件
        永远被当成最新，`--only-stale` 反复跳过它。
        现在全库 3466 个谱面 `s` 长度都是 6，唯一例外就是它。
        """
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return True
        if set(o) <= {"d", "n"}:
            return True
        s = o.get("s")
        return s is not None and len(s) != 6

    for diff in diffs:
        ids = [sid for sid, s in songs.items()
               if str(DIFFS[diff]) in (s.get("difficulty") or {})]
        if args.limit:
            ids = ids[: args.limit]
        out_dir = OUT_ROOT / diff
        out_dir.mkdir(parents=True, exist_ok=True)
        for sid in ids:
            p = out_dir / f"{sid}.json"
            if cutoff and p.exists() and p.stat().st_mtime >= cutoff:
                skipped_recent += 1
                continue
            if args.only_stale and p.exists() and not is_stale(p):
                skipped_recent += 1
                continue
            jobs.append((diff, sid, p))

    print(f"共 {len(jobs)} 个谱面，并发 {args.workers}"
          + (f"，跳过近期已更新 {skipped_recent} 个" if skipped_recent else ""))
    t0 = time.time()
    ok = fail = skip = 0
    fails: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(grab, j) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            st, tag, note = f.result()
            if st == "ok":
                ok += 1
            elif st == "skip":
                skip += 1
            else:
                fail += 1
                fails.append((tag, note))
            if i % 200 == 0:
                el = time.time() - t0
                print(f"  {i}/{len(jobs)}  ok={ok} fail={fail}  "
                      f"{el:.0f}s  预计剩余 {el / i * (len(jobs) - i):.0f}s")

    print(f"\n完成：ok={ok} skip={skip} fail={fail}  用时 {(time.time() - t0) / 60:.1f} 分钟")
    for tag, note in fails[:20]:
        print(f"  ✗ {tag}: {note[:80]}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
