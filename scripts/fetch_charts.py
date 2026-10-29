"""把 Bestdori 的谱面预先抓到本地，让组队助手**不需要代理**。

## 为什么值得全量预拉

网页版直连 Bestdori 会被 CORS 拦死，所以"按需拉谱面"必须配一层代理
（Worker 或本地反代）。但实测每首 expert 谱面只有 **24 KB**，824 首合计
**约 19 MB** —— 完全可以一次性抓下来放进 `docs/data/charts/`。

关键差别在于：**"全部抓下来"不等于"全部下载"**。谱面按歌曲存成独立文件，
页面只在用户选歌时取那一个（几 KB），用户侧体感和按需拉完全一样。
于是代理这一层就彻底不需要了。

## 存什么

只存算分要用的东西，不存原始谱面：

    {"d": 117.4, "n": [0.0, 0.317, 0.644, ...]}

- `d`：最后一颗 NOTE 的时间（秒）
- `n`：每颗 NOTE 的时间（秒，已按 BPM 事件换算好，含变速）

原始谱面里的 BPM / System / lane 对算分都没用，去掉后单曲从 24 KB 降到 3~5 KB。

## 采集礼节

- 串行 + 每次间隔 `DELAY` 秒，不并发
- 失败重试 3 次（指数退避）
- **已存在的文件直接跳过** —— 每周出新歌后重跑本脚本即可增量更新

用法::

    python scripts/fetch_charts.py                # 全部 expert
    python scripts/fetch_charts.py --diff hard    # 换个难度
    python scripts/fetch_charts.py --limit 20     # 先试跑 20 首
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / ".bdh-test" / "bdapi"
#: 按难度分目录 —— 同一个 songId 在 easy/hard/expert 下各有自己的谱面，
#: 放同一层会互相覆盖。
OUT_ROOT = REPO / "docs" / "data" / "charts"

SERVER = 5          # 国服
DELAY = 0.4         # 每次请求间隔（秒）—— 礼貌抓取
RETRIES = 3

DIFFS = {"easy": 0, "normal": 1, "hard": 2, "expert": 3, "special": 4}

NOTE_TYPES = {"Single", "Long", "Flick"}


def beat_to_time(chart: list[dict]):
    """把 beat 换算成秒（谱面中途可能变速，要分段累加）。"""
    bpm_events = sorted(
        (e for e in chart if e.get("type") == "BPM" and isinstance(e.get("bpm"), (int, float))),
        key=lambda e: e["beat"],
    ) or [{"beat": 0, "bpm": 120}]

    def conv(beat: float) -> float:
        t = 0.0
        for i, cur in enumerate(bpm_events):
            nxt = bpm_events[i + 1] if i + 1 < len(bpm_events) else None
            start = cur["beat"]
            end = nxt["beat"] if nxt else beat
            if beat <= start:
                return t
            span = min(beat, end) - start
            if span > 0:
                t += span * 60.0 / cur["bpm"]
            if beat <= end:
                return t
        return t

    def start_beat(e):
        """一颗 NOTE 的起始 beat。

        ⚠️ `Long`（长条）**没有 `beat` 字段**，时间在 `connections[0].beat` 里。
        只按 `e["beat"]` 取会把这些全漏掉 —— 实测某首歌少算 59 颗 NOTE（-15%），
        导致每 NOTE 分虚高、总分系统性偏移。
        """
        if e.get("type") == "Long":
            conns = e.get("connections") or []
            return conns[0].get("beat") if conns else None
        return e.get("beat")

    beats = []
    for e in chart:
        if e.get("type") not in NOTE_TYPES:
            continue
        b = start_beat(e)
        if isinstance(b, (int, float)):
            beats.append(conv(b))
    return sorted(beats)


def fetch(url: str) -> bytes | None:
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == RETRIES - 1:
                print(f"    ✗ {e}")
                return None
            time.sleep(1.5 ** attempt)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", default="expert",
                    choices=[*DIFFS, "all"], help="all = 五个难度全跑")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="忽略已有文件，全部重抓")
    args = ap.parse_args()

    songs = json.loads((CACHE / "songs.json").read_text(encoding="utf-8"))
    OUT_DIR = OUT_ROOT / args.diff
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 只抓有这个难度的歌（部分曲子没有 special）
    diffs = list(DIFFS) if args.diff == "all" else [args.diff]
    t_all0 = time.time()
    for diff in diffs:
        ids = [sid for sid, s in songs.items()
               if str(DIFFS[diff]) in (s.get("difficulty") or {})]
        if args.limit:
            ids = ids[: args.limit]
        out_dir = OUT_ROOT / diff
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n=== {diff}  共 {len(ids)} 首 ===")
        run_diff(diff, ids, out_dir, args.force)

    total_files = list(OUT_ROOT.rglob("*.json"))
    total_kb = sum(f.stat().st_size for f in total_files) / 1024
    print(f"\n全部完成：{len(total_files)} 个文件，{total_kb / 1024:.1f} MB，"
          f"总用时 {(time.time() - t_all0) / 60:.1f} 分钟")
    return 0


def run_diff(diff: str, ids: list[str], out_dir: Path, force: bool) -> tuple[int, int, int]:
    """抓一个难度。返回 (成功, 跳过, 失败)。"""
    done = skipped = failed = 0
    t0 = time.time()
    for i, sid in enumerate(ids, 1):
        out = out_dir / f"{sid}.json"
        if out.exists() and not force:
            skipped += 1
            continue
        raw = fetch(f"https://bestdori.com/api/charts/{sid}/{diff}.json")
        if raw is None:
            failed += 1
            continue
        try:
            notes = beat_to_time(json.loads(raw))
        except json.JSONDecodeError:
            failed += 1
            continue
        if not notes:
            failed += 1
            continue
        out.write_text(
            json.dumps({"d": round(notes[-1], 3),
                        "n": [round(x, 3) for x in notes]},
                       separators=(",", ":")),
            encoding="utf-8",
        )
        done += 1
        if done % 50 == 0:
            print(f"  进度 {i}/{len(ids)}  成功 {done} 跳过 {skipped} 失败 {failed}  "
                  f"用时 {time.time() - t0:.0f}s")
        time.sleep(DELAY)

    kb = sum(f.stat().st_size for f in out_dir.glob("*.json")) / 1024
    print(f"{diff}：新增 {done} / 跳过 {skipped} / 失败 {failed}  "
          f"{len(list(out_dir.glob('*.json')))} 个文件 {kb / 1024:.1f} MB  "
          f"用时 {(time.time() - t0) / 60:.1f} 分钟")
    return done, skipped, failed


if __name__ == "__main__":
    raise SystemExit(main())
