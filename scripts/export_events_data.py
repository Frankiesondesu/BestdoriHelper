"""补齐活动加成明细：把「成员加成（指定卡）」写进 docs/data/events.json。

## 为什么需要这个脚本

Bestdori 的活动列表接口 ``/api/events/all.{server}.json`` **只给活动名和时间**，
加成明细在单期接口 ``/api/events/{id}.json`` 里。而国服（``.3``）的列表连活动名
都经常是 null，所以历史数据是「日服 id + 国服时间」拼出来的。

原来的 events.json 导出了 ``attr`` / ``ch`` / ``ab`` / ``lb`` 四项，**漏了
``members``** —— 也就是「本期活动卡额外 +20%」这一项。少了它，算法分不清：

* 角色匹配但**不是**本期活动卡 → 只吃「属性 10% + 角色 20%」= 30%
* 本期活动卡（角色 + 属性 + 成员都中）→ 10 + 20 + 20 = 50%，再加星光

两者差 20 个百分点，直接决定「该上高综合力的通用卡还是低综合力的档期卡」。

## 关键事实（实测确认）

``members[].situationId`` **就是卡牌 ID**。核对过 300 / 320 / 325 / 326 / 327
五期活动共 24 张成员卡，``cards.cn.json`` 全部命中，且角色 / 属性都对得上。

单卡活动加成 = 属性匹配 ? attributes.percent : 0
             + 角色匹配 ? characters.percent : 0
             + 成员匹配 ? members.percent : 0
             + 星光等级（limitBreaks 里按星级和等级取，**单位就是百分数**）

用这套公式反推视频里的实测面板：4★ 满星光卡 = 10+20+20+6 = 56%，
5★ = 10+20+20+15 = 65%，与该期活动界面显示的 +56% / +65% 完全一致。

⚠️ ``eventAttributeAndCharacterBonus.pointPercent`` **不参与逐卡加成**。
它在任务类活动里是 20、竞演类里是 0，只是「这类活动加成作用在 PT 还是综合力」
的标记值；如果当成额外 20% 加进去，单卡会算成 76%，与实测的 56% 不符。

## 用法

    python scripts/export_events_data.py             # 只补缺失的
    python scripts/export_events_data.py --all       # 全部重拉
    python scripts/export_events_data.py --dry-run   # 只报告，不写文件
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 刻意用标准库而不是 requests/httpx：这个脚本要在**没装项目依赖**的解释器里
# 也能跑（换机器、CI、临时 venv 都可能）。只发 300 多个 GET，用不着第三方库。
REPO = Path(__file__).resolve().parents[1]
EVENTS = REPO / "docs" / "data" / "events.json"
API = "https://bestdori.com/api/events/{id}.json"

#: 并发数。Bestdori 没有公开限流，但拉太猛会被 Cloudflare 挡，6 路比较稳。
WORKERS = 6
#: 每个请求之间的最小间隔（秒），按线程各自计
GAP = 0.12

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


_UA = "BestdoriHelper/1.1 (+https://github.com/Frankiesondesu/BestdoriHelper)"
#: urllib 默认走环境变量里的 http_proxy / https_proxy，和项目的其它脚本一致
_opener = urllib.request.build_opener()


def fetch(eid: str, retries: int = 3) -> dict | None:
    for i in range(retries):
        try:
            req = urllib.request.Request(API.format(id=eid), headers={"User-Agent": _UA})
            with _opener.open(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:  # noqa: BLE001 - 网络抖动直接重试
            pass
        time.sleep(0.6 * (i + 1))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="全部重拉（默认只补缺 mb 的）")
    ap.add_argument("--dry-run", action="store_true", help="只报告差异，不写文件")
    args = ap.parse_args()

    data = json.loads(EVENTS.read_text("utf-8"))
    todo = [k for k, v in data.items() if args.all or "mb" not in v]
    log(f"共 {len(data)} 期活动，需要拉取 {len(todo)} 期")

    session = None  # urllib opener 是模块级的，这里保留变量只为可读性

    added = 0
    mismatch = []
    done = 0
    t0 = time.time()

    def work(eid: str) -> tuple[str, dict | None]:
        time.sleep(GAP)
        return eid, fetch(eid)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, eid) for eid in todo]
        for fut in as_completed(futs):
            eid, detail = fut.result()
            done += 1
            if done % 25 == 0 or done == len(todo):
                log(f"  {done}/{len(todo)}  {time.time() - t0:.0f}s")

            if not detail:
                log(f"  ! {eid} 拉取失败")
                continue

            ev = data[eid]
            members = detail.get("members") or []

            # ---- 交叉校验：现有 attr / ch 是否和官方明细一致 --------------
            attrs = {a["attribute"]: a["percent"] for a in (detail.get("attributes") or [])}
            chars = {c["characterId"]: c["percent"] for c in (detail.get("characters") or [])}
            cur_attr = {a["a"]: a["p"] for a in (ev.get("attr") or [])}
            cur_ch = {c["c"]: c["p"] for c in (ev.get("ch") or [])}
            if attrs != cur_attr or (chars and chars != cur_ch):
                mismatch.append((eid, attrs, cur_attr, len(chars), len(cur_ch)))

            # ---- 写入成员加成 --------------------------------------------
            if members:
                ev["mb"] = [m["situationId"] for m in members]
                # percent 通常统一（20），取第一条；万一不一致就逐条存
                pcts = {m.get("percent") for m in members}
                if len(pcts) == 1:
                    ev["mp"] = pcts.pop()
                else:
                    ev["mbp"] = [[m["situationId"], m.get("percent")] for m in members]
                    ev["mp"] = 20
                added += 1
            else:
                ev["mb"] = []
                ev["mp"] = 0

    log(f"完成：{added} 期写入成员加成，耗时 {time.time() - t0:.0f}s")
    if mismatch:
        log(f"⚠️ {len(mismatch)} 期属性/角色加成与现有数据不一致：")
        for eid, a, ca, n1, n2 in mismatch[:10]:
            log(f"   {eid}: attr {a} vs {ca} / 角色 {n1} vs {n2}")

    if args.dry_run:
        log("--dry-run，未写文件")
        return 0

    EVENTS.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), "utf-8")
    log(f"已写入 {EVENTS}（{EVENTS.stat().st_size / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
