"""网页版自检：识别错了能不能改回来。

专盯用户报的那个问题 —— **候选只给三张，三张都不对时没路可走**。
这个脚本在真实浏览器里跑完整链路，然后验证两条兜底路径：

1. 候选行末尾有「都不对？搜卡面」按钮，每一行都有「改」按钮
2. 手动指定里输卡号能精确定位（精确命中排第一并高亮），点一下就能改判
3. 改判后清单跟着变（错的卡被移除，对的卡进来）

用法::

    python scripts/correction_check.py            # 自己起服务
    python scripts/correction_check.py --url http://127.0.0.1:8788
"""

from __future__ import annotations

import argparse
import collections
import functools
import http.server
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # 别刷屏
        pass


def serve(port: int) -> socketserver.TCPServer:
    """起一个**多线程**静态服务。

    单线程的 TCPServer 顶不住卡图缩略图的并发请求 —— 浏览器会同时开十几条连接，
    排不上的直接被拒，控制台刷满 ERR_CONNECTION_REFUSED，把真正的报错淹掉。
    """
    handler = functools.partial(_Quiet, directory=str(DOCS))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def js_click(page, selector: str) -> None:
    """直接派发 click，绕过 Playwright 的视口检查。

    结果表在可滚动面板里，排在下面的行 Playwright 判定为「outside of the
    viewport」而拒绝点击 —— 但元素本身是可见可点的。这里要验的是**点击行为**，
    不是滚动行为，所以直接调 DOM 的 click()。
    """
    page.eval_on_selector(selector, "el => el.click()")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None, help="已有服务地址；不给就自己起一个")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--shot-out", default=".bdh-test/correction_dialog.png")
    args = ap.parse_args()

    photos = sorted((REPO / "Photo").glob("*.jpg"))[:1]
    if not photos:
        print("Photo/ 里没有截图")
        return 1

    httpd = None
    url = args.url
    if url is None:
        httpd = serve(args.port)
        url = f"http://127.0.0.1:{args.port}"
    print(f"打开 {url} …")

    errors: list[str] = []
    failed: list[str] = []
    not_found: collections.Counter = collections.Counter()
    real: list[str] = []        # 排除已知 404 之后剩下的实质报错

    def check(ok: bool, msg: str) -> None:
        print(f"  {'✓' if ok else '✗'} {msg}")
        if not ok:
            failed.append(msg)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
            ctx = browser.new_context(viewport={"width": 1500, "height": 950})
            page = ctx.new_page()
            page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}")
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
            # 单独收集 404 的真实 URL —— 卡池里有些卡没有缩略图，
            # 前端靠 thumbs.json 清单挑变体，不该再发任何必然 404 的请求。
            page.on("response", lambda r: not_found.update([r.url])
                    if r.status == 404 else None)

            page.goto(url, wait_until="load", timeout=60000)
            page.wait_for_function(
                "() => document.getElementById('statusText')?.textContent?.includes('就绪')",
                timeout=120000)
            print("数据加载完成 ✓")

            page.set_input_files("#fileInput", [str(photos[0])])
            page.wait_for_timeout(400)
            page.click("#btnRun")
            page.wait_for_function(
                "() => document.getElementById('statusText')?.textContent?.includes('就绪')",
                timeout=300000)
            page.wait_for_timeout(1200)
            page.evaluate("() => window.__bdh.showTab('result')")
            page.wait_for_timeout(400)

            rows = page.eval_on_selector_all("tr.row", "els => els.length")
            print(f"\n识别出 {rows} 格")

            # ---- 1. 兜底入口是否真的在界面上 ----
            print("\n[1] 兜底入口")
            fix_btns = page.eval_on_selector_all("[data-pick-manual]", "els => els.length")
            check(fix_btns >= rows,
                  f"每行都有可点的改判入口（{fix_btns} 个 / {rows} 行）")

            cand_more = page.eval_on_selector_all(".cand-more", "els => els.length")
            multi = page.eval_on_selector_all(
                "#resultBody .candidates", "els => els.length")
            check(cand_more == multi,
                  f"候选行末尾都有「都不对？搜卡面」（{cand_more} 个 / {multi} 个候选行）")

            # 候选只有 3 张时按钮更该在 —— 这正是用户卡住的情形
            if multi == 0:
                print("  ! 这张图没有多候选的格子，候选行那条没法验")

            # ---- 2. 选一格，找一个「不在候选里」的卡号 ----
            print("\n[2] 按卡号改判")
            target = page.evaluate("""() => {
                const rows = [...document.querySelectorAll('tr.row')];
                for (const tr of rows) {
                    const si = Number(tr.dataset.shot), ci = Number(tr.dataset.cell);
                    const cell = window.__bdh.S.shots[si].cells[ci];
                    const cands = new Set((cell.top3 || []).map(c => c.cardId));
                    const cards = Object.keys(window.__bdh.S.meta.cards).map(Number);
                    // 挑一张真实存在、但不在候选里的卡 —— 模拟「三张都不对」
                    const outside = cards.find(id => !cands.has(id) && id !== cell.cardId);
                    if (outside != null) {
                        return {si, ci, before: cell.cardId, pick: outside,
                                cands: [...cands]};
                    }
                }
                return null;
            }""")
            if target is None:
                print("  ! 找不到可测的格子")
                return 1
            print(f"  目标格 {target['si']}:{target['ci']}  "
                  f"改判前=#{target['before']}  候选={target['cands']}  "
                  f"要改成=#{target['pick']}")

            # 点这一行的「改」
            js_click(page, f'tr.row[data-shot="{target["si"]}"]'
                           f'[data-cell="{target["ci"]}"] [data-pick-manual]')
            page.wait_for_selector("#cardSearch", timeout=8000)
            check(True, "点「改」能打开手动指定面板")

            # 输入框提示里应该教了卡号怎么来
            hint = page.text_content(".lightbox, #lbBody") or ""
            check("info/cards" in hint, "面板里说明了卡号从哪来（卡面页网址末位数字）")

            # 输一个不存在的卡号 -> 应该提示没有匹配
            page.fill("#cardSearch", "9999999")
            page.wait_for_timeout(300)
            empty = page.text_content("#cardPickList") or ""
            check("没有匹配" in empty, "卡号不存在时给出「没有匹配」提示")

            # 输真实卡号 -> 精确命中排第一且高亮
            page.fill("#cardSearch", str(target["pick"]))
            page.wait_for_timeout(400)
            first = page.eval_on_selector(
                "#cardPickList .pick-item",
                "el => ({id: Number(el.dataset.pickCard), exact: el.classList.contains('exact')})")
            check(first["id"] == target["pick"],
                  f"精确命中排第一（第一项 #{first['id']}）")
            check(first["exact"], "精确命中带高亮标记")

            count_text = page.text_content("#cardPickCount") or ""
            check("匹配" in count_text, f"显示匹配数量：{count_text.strip()[:40]}")

            Path(REPO / args.shot_out).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(REPO / args.shot_out))
            print(f"  截图：{args.shot_out}")

            # ---- 3. 点一下改判，清单要跟着变 ----
            print("\n[3] 改判结果与清单同步")
            # S.inv 是个 Map（key = cardId:trained），不是数组 —— 别按数组读
            js_click(page, "#cardPickList .pick-item")
            page.wait_for_timeout(600)

            after = page.evaluate(
                f"() => window.__bdh.S.shots[{target['si']}].cells[{target['ci']}].cardId")
            check(after == target["pick"],
                  f"格子里改成了 #{after}（期望 #{target['pick']}）")

            inv_ids = page.evaluate(
                "() => [...window.__bdh.S.inv.values()].map(x => x.cardId)")
            check(inv_ids, f"清单非空（{len(inv_ids)} 张）")
            check(target["pick"] in inv_ids, f"清单里出现了 #{target['pick']}")
            if target["before"] is not None:
                check(target["before"] not in inv_ids,
                      f"清单里不再有错的 #{target['before']}")

            # 界面上的清单表也要跟着刷新
            page.evaluate("() => window.__bdh.showTab('inventory')")
            page.wait_for_timeout(400)
            inv_rows = page.eval_on_selector_all("tr.invrow", "els => els.length")
            check(inv_rows == len(inv_ids),
                  f"清单页行数与数据一致（表 {inv_rows} 行 / 数据 {len(inv_ids)} 张）")

            # ---- 4. 改判后还能接着改（选中不丢） ----
            print("\n[4] 连续改判")
            page.evaluate("() => window.__bdh.showTab('result')")
            page.wait_for_timeout(300)
            js_click(page, f'tr.row[data-shot="{target["si"]}"]'
                           f'[data-cell="{target["ci"]}"] [data-pick-manual]')
            page.wait_for_selector("#cardSearch", timeout=8000)
            page.fill("#cardSearch", str(target["before"] or target["pick"]))
            page.wait_for_timeout(400)
            second = page.eval_on_selector(
                "#cardPickList .pick-item", "el => Number(el.dataset.pickCard)")
            js_click(page, "#cardPickList .pick-item")
            page.wait_for_timeout(500)
            again = page.evaluate(
                f"() => window.__bdh.S.shots[{target['si']}].cells[{target['ci']}].cardId")
            check(again == second, f"改第二次也生效（#{again}）")

            # 卡图 404 应该是 0 —— 卡池里 188 张特殊卡两种形态都没有图，
            # 前端改查 thumbs.json 清单来挑变体，缺图渲染占位块。
            # 这里断言「一条 404 都不该有」，防止以后又退回「先请求再 onerror」。
            print(f"\n[5] 404 检查")
            if not_found:
                check(False, f"出现 {sum(not_found.values())} 条 404（应当为 0）")
                for u, n in not_found.most_common(5):
                    print(f"      {n} × {u}")
            else:
                check(True, "全程零 404")

            real = [e for e in errors if "404" not in e]
            if real:
                print("\n控制台实质报错：")
                for e in real[:10]:
                    print("  " + e)
            else:
                print("\n控制台无实质报错 ✓")

            browser.close()
    finally:
        if httpd is not None:
            httpd.shutdown()

    print("\n" + "=" * 56)
    if failed or real:
        print(f"未通过 {len(failed)} 项：")
        for f in failed:
            print("  - " + f)
        if real:
            print(f"另有 {len(real)} 条控制台实质报错")
        return 1
    print("全部通过 ✓  「候选都不对」有路可走了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
