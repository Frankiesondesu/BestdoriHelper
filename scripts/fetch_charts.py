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

NOTE_TYPES = {"Single", "Long", "Flick", "Slide", "Directional"}


def note_beats(e: dict) -> list[float]:
    """一颗谱面事件对应的**连击数**（可能不止 1）。

    官方 `songs.json` 的 `notes` 字段是「连击数」，不是「事件数」。
    全库 3457 个谱面比对下来，规则是：

    | 类型 | 连击数 | 说明 |
    |------|--------|------|
    | Single / Flick / **Directional** | 1 | 三者在游戏里都是一次点击 |
    | Long | `len(connections)` | 头 + 尾各算一次（实测都是 2） |
    | Slide | **非 `hidden` 的连接点数** | 见下 |

    ## 两个踩过的坑

    **坑一：`Directional` 整个漏掉。** 它只在 SPECIAL 谱面里出现
    （歌 9 有 28 个、歌 3 有 127 个）。早期 `NOTE_TYPES` 里没有它，
    歌 3 的 SPECIAL 直接少算 127 颗。

    **坑二：Slide 的 `hidden: true` 连接点是渲染插值，不是连击点。**
    SPECIAL 谱面里的弧形滑条会被 Bestdori 采样成几十个点，标上
    `"hidden": true` 只是为了画曲线。歌 9 的 SPECIAL 有 **608 个 hidden 点**
    （占全部连接点的 75%），全算进去的话 690 → 1270，虚高 84%。

    ```
    歌 9  ETERNAL BLAZE   SPECIAL   官方 690
          Single 406 + Directional 28 + Long 52 + Slide 可见点 204 = 690  ✓
    歌 8  空色デイズ        SPECIAL   官方 760
          Single 474 + Directional 58 + Long 26 + Slide 可见点 202 = 760  ✓
    歌 3  ぽっぴん'しゃっふる SPECIAL   官方 979
          Single 576 + Directional 127 + Long 58 + Slide 可见点 218 = 979  ✓
    ```

    全库校验：162 首 SPECIAL **全部命中**（`scripts/diag_special_notes.py`），
    其余四个难度 3331 例也全一致。
    """
    t = e.get("type")
    if t in ("Single", "Flick", "Directional"):
        b = e.get("beat")
        return [b] if isinstance(b, (int, float)) else []
    conns = e.get("connections") or []
    if t == "Long":
        # 头尾两个连接点；只有 1 个点时退回该点（容错）
        pts = [c.get("beat") for c in conns if isinstance(c.get("beat"), (int, float))]
        if not pts:
            return []
        return [pts[0], pts[-1]] if len(pts) > 1 else [pts[0]]
    if t == "Slide":
        # ⚠️ 必须跳过 hidden 的插值点，否则 SPECIAL 谱面会虚高几十个百分点
        return [
            c.get("beat")
            for c in conns
            if not c.get("hidden") and isinstance(c.get("beat"), (int, float))
        ]
    return []



def chart_payload(chart: list[dict]) -> dict:
    """把原始谱面压成算分要用的最小结构。

    ```json
    {"d": 117.4, "n": [0.0, 0.317, ...], "s": [12, 168, ...], "f": [61.2, 78.4]}
    ```

    - `d`：最后一颗 NOTE 的时间（秒）
    - `n`：每颗 NOTE 的时间（秒，已按 BPM 事件换算，含变速）
    - `s`：**技能触发点在 `n` 里的下标**
    - `f`：**Fever 区间**（秒，闭区间），没有就是缺省

    ## 为什么要存 `s` 和 `f`

    原来只存 `n`，前端只能拿「把谱面按 6 等分」当触发点、并且**完全不知道
    Fever 存在**。拿 Bestdori 自己的 `ct()` 当基准实测（同一综合力）：

    | 歌曲 | 网格/无火 vs 真实/无火 | 加上 Fever |
    | --- | --- | --- |
    | 7 expert | +0.41% | **+29.3%** |
    | 540 hard | +2.19% | **+29.2%** |
    | 9 special | −1.52% | **+29.4%** |

    Fever 区间内每颗音符**分数翻倍**，漏掉就是系统性低估近三成。
    触发点用网格只差 ±4%，但既然要重抓，就一并存真的。

    规则来自 Bestdori 的 `ToolTeamBuilder`（`apiChart` → `notes` 那段）：
    `cmd_fever_start.wav` / `cmd_fever_end.wav` 两个 System 事件的 beat 之间
    算 Fever；技能触发是 note（含 Slide/Long 的连接点）上的 `skill` 属性。
    """
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

    fever_start = fever_end = None
    for e in chart:
        if e.get("type") != "System":
            continue
        if e.get("data") == "cmd_fever_start.wav":
            fever_start = e.get("beat")
        elif e.get("data") == "cmd_fever_end.wav":
            fever_end = e.get("beat")

    pts: list[tuple[float, bool]] = []   # (秒, 是否技能触发)
    for e in chart:
        t = e.get("type")
        if t in ("Single", "Flick", "Directional"):
            b = e.get("beat")
            if isinstance(b, (int, float)):
                pts.append((conv(b), bool(e.get("skill"))))
        elif t in ("Long", "Slide"):
            for c in e.get("connections") or []:
                if c.get("hidden"):
                    continue
                b = c.get("beat")
                if isinstance(b, (int, float)):
                    pts.append((conv(b), bool(c.get("skill"))))

    # Bestdori 的排序：先按时间，同刻**触发音符在前**
    pts.sort(key=lambda x: (x[0], not x[1]))
    times = [round(p[0], 3) for p in pts]

    # 触发点按**时刻**去重，不是按音符。
    #
    # 同一时刻可能有多个音符都带 `skill: true`（Slide 的每个连接点都会标），
    # 实测 `special/786` 有 17 个带标记的音符，但只有 6 个不同时刻：
    #   17.333×2, 30.667×2, 41.0×2, 63.167×7, 74.5×2, 90.667×2
    # 不去重的话下游会以为有 17 次技能发动，而游戏里永远是 6 次。
    skills: list[int] = []
    seen_t: set[float] = set()
    for i, (t, is_skill) in enumerate(pts):
        if not is_skill:
            continue
        key = times[i]
        if key in seen_t:
            continue
        seen_t.add(key)
        skills.append(i)

    out = {"d": times[-1] if times else 0, "n": times}
    if skills:
        out["s"] = skills
    if isinstance(fever_start, (int, float)) and isinstance(fever_end, (int, float)):
        if fever_end > fever_start:
            out["f"] = [round(conv(fever_start), 3), round(conv(fever_end), 3)]
    return out


def beat_to_time(chart: list[dict]):
    """把 beat 换算成秒（谱面中途可能变速，要分段累加）。"""
    return chart_payload(chart)["n"]


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
