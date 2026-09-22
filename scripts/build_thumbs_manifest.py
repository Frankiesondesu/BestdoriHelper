"""扫描 docs/data/thumbs/，生成前端用的缩略图清单 docs/data/thumbs.json。

**为什么需要这个清单**：卡池里有些卡**根本没有缩略图**。实测 2468 张卡里
2172 张有未特训形态（`_n`）、1693 张有特训后形态（`_t`），而 **188 张两张都没有**
（`campaign` / `special` 类的特殊卡，导出时它们不在指纹库里，所以没生成）。

前端原来只能「先请求 `_n`，404 了再 onerror 换 `_t`」—— 对那 188 张就是
两次 404 + 一张坏图。自选卡面按卡号倒序排，开头正好是这批卡，
于是用户看到的是「全部 404」。

有了清单，前端直接选存在的那个变体，没有就渲染占位块 —— 零 404、零坏图。

用法::

    python scripts/build_thumbs_manifest.py            # 写 docs/data/thumbs.json
    python scripts/build_thumbs_manifest.py --check     # 只报告，不写
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
THUMBS = DOCS / "data" / "thumbs"
OUT = DOCS / "data" / "thumbs.json"

NAME_RE = re.compile(r"^(\d+)_([nt])\.webp$")


def scan(thumbs_dir: Path) -> dict[str, list[int]]:
    """扫目录，返回 {"n": [卡号...], "t": [卡号...]}（已排序）。"""
    have: dict[str, list[int]] = {"n": [], "t": []}
    for f in thumbs_dir.glob("*.webp"):
        m = NAME_RE.match(f.name)
        if m:
            have[m.group(2)].append(int(m.group(1)))
    for k in have:
        have[k].sort()
    return have


def build(thumbs_dir: Path = THUMBS, out: Path = OUT, check: bool = False) -> int:
    if not thumbs_dir.is_dir():
        print(f"✗ 找不到缩略图目录：{thumbs_dir}", file=sys.stderr)
        return 1

    have = scan(thumbs_dir)
    payload = {"n": have["n"], "t": have["t"]}
    text = json.dumps(payload, separators=(",", ":"))

    n_n, n_t = len(have["n"]), len(have["t"])
    print(f"  未特训 _n : {n_n} 张")
    print(f"  特训后 _t : {n_t} 张")
    print(f"  两种都有  : {len(set(have['n']) & set(have['t']))} 张")
    print(f"  都没有    : 见下面（相对卡池总数）")

    # 顺带报一下「一张图都没有」的卡有多少 —— 这是前端要渲染占位块的那批
    cards_path = DOCS / "data" / "cards.json"
    if cards_path.is_file():
        cards = json.loads(cards_path.read_text(encoding="utf-8"))["cards"]
        ids = {int(k) for k in cards}
        none = ids - set(have["n"]) - set(have["t"])
        print(f"  卡池总数  : {len(ids)} 张")
        print(f"  一张都没有: {len(none)} 张"
              + (f"（卡号 {min(none)}~{max(none)}）" if none else ""))

    print(f"  清单大小  : {len(text)/1024:.1f} KB")

    if check:
        if out.is_file() and out.read_text(encoding="utf-8") == text:
            print("  ✓ 已是最新，无需写入")
            return 0
        print("  ! 清单需要更新（用不带 --check 的方式运行）")
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"  ✓ 已写入 {out.relative_to(REPO)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查是否需要更新")
    ap.add_argument("--thumbs-dir", default=str(THUMBS))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    return build(Path(args.thumbs_dir), Path(args.out), args.check)


if __name__ == "__main__":
    raise SystemExit(main())
