"""把 `docs/data/songs.json`（站点用）和 Bestdori 的歌曲缓存对齐。

站点这个文件是**精简格式**，只保留组队页要用的字段：

```json
{"1": {"t": "Yes! BanG_Dream!", "d": ["easy", ...], "lv": {"easy": 5, ...},
       "j": "yes_bang_dream", "b": 1}}
```

字段映射：

| 站点 | 来源 | 说明 |
| --- | --- | --- |
| `t` | `musicTitle[0]` | 优先日文原名，空则退回 `musicTitle[1]` |
| `d` | `difficulty` 的键 | 难度名列表 |
| `lv` | `difficulty[i].playLevel` | |
| `j` | `jacketImage[0]` | 封面文件名 |
| `b` | `bandId` | |

**这个文件原来是手工生成的，仓库里没有生成脚本** —— 所以新歌上线后不会自动更新。
本脚本只做「补齐」：新增歌曲、给已有歌曲补上新难度，**不改动已有字段**，
避免因为 Bestdori 改了某个译名就把全站标题刷掉。

用法::

    python scripts/sync_site_songs.py            # 只报告差异
    python scripts/sync_site_songs.py --write    # 真的写入
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / ".bdh-test" / "bdapi" / "songs.json"
SITE = REPO / "docs" / "data" / "songs.json"
CHARTS = REPO / "docs" / "data" / "charts"

DIFF_NAMES = ["easy", "normal", "hard", "expert", "special"]


def build_entry(b: dict) -> dict:
    titles = b.get("musicTitle") or []
    title = next((x for x in titles if x), "")
    diff = b.get("difficulty") or {}
    names, lv = [], {}
    for idx, name in enumerate(DIFF_NAMES):
        d = diff.get(str(idx))
        if not d:
            continue
        names.append(name)
        lv[name] = d.get("playLevel")
    jackets = b.get("jacketImage") or []
    return {
        "t": title,
        "d": names,
        "lv": lv,
        "j": jackets[0] if jackets else "",
        "b": b.get("bandId"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写入 docs/data/songs.json")
    args = ap.parse_args()

    bulk = json.loads(CACHE.read_text(encoding="utf-8"))
    site = json.loads(SITE.read_text(encoding="utf-8"))

    added, patched = [], []
    for sid, b in bulk.items():
        if sid not in site:
            site[sid] = build_entry(b)
            added.append((sid, site[sid]["t"], site[sid]["d"]))
            continue
        # 已有歌曲：只补新难度，不动其它字段
        cur = site[sid]
        want = build_entry(b)
        new_names = [n for n in want["d"] if n not in cur["d"]]
        if new_names:
            cur["d"] = [n for n in DIFF_NAMES if n in set(cur["d"]) | set(new_names)]
            for n in new_names:
                cur["lv"][n] = want["lv"][n]
            patched.append((sid, cur["t"], new_names))

    print(f"缓存 {len(bulk)} 首，站点原有 {len(site) - len(added)} 首")
    print(f"\n新增歌曲 {len(added)} 首：")
    for sid, title, ds in added:
        print(f"  + {sid:>4} {title[:24]:24} {','.join(ds)}")
    print(f"\n补难度 {len(patched)} 首：")
    for sid, title, ds in patched:
        print(f"  ~ {sid:>4} {title[:24]:24} +{','.join(ds)}")

    # 新歌的谱面文件在不在
    missing = []
    for sid in [a[0] for a in added] + [p[0] for p in patched]:
        for name in site[sid]["d"]:
            if not (CHARTS / name / f"{sid}.json").exists():
                missing.append(f"{name}/{sid}")
    if missing:
        print(f"\n⚠️ 缺谱面文件 {len(missing)} 个，先跑 refetch_charts_parallel.py：")
        print("  " + ", ".join(missing[:20]))

    if not args.write:
        print("\n（只报告，未写入。加 --write 生效）")
        return 0

    ordered = {k: site[k] for k in sorted(site, key=lambda x: int(x))}
    SITE.write_text(json.dumps(ordered, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    print(f"\n已写入 {SITE.relative_to(REPO)}，共 {len(ordered)} 首")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
