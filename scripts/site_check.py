"""网页版自检：用真实浏览器打开静态站，跑一遍「上传截图 → 识别 → 出结果」。

比单元测试更接近真实使用：验证 ES module 能正常加载、指纹库能解析、
Worker 之外的整条链路在浏览器里跑得通，并抓取控制台报错。

用法::

    # 先起本地服务
    python -m http.server 8788 --directory docs
    # 再跑自检
    python scripts/site_check.py --url http://127.0.0.1:8788 --shots 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8788")
    ap.add_argument("--shots", type=int, default=1, help="用几张截图测")
    ap.add_argument("--shot-out", default=".bdh-test/site_shot.png")
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    ap.add_argument("--device", default=None,
                    help="模拟设备（Playwright 预设名），如 'iPhone 14' / 'Pixel 7'")
    ap.add_argument("--width", type=int, default=1500)
    ap.add_argument("--height", type=int, default=950)
    ap.add_argument("--tabs", action="store_true", help="额外把四个分页各存一张截图")
    args = ap.parse_args()

    photos = sorted((REPO / "Photo").glob("*.jpg"))[: args.shots]
    if not photos:
        print("Photo/ 里没有截图")
        return 1

    errors: list[str] = []
    logs: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        if args.device:
            if args.device not in pw.devices:
                print(f"未知设备 {args.device!r}；可用的有：")
                for k in sorted(pw.devices):
                    print("   ", k)
                browser.close()
                return 1
            ctx = browser.new_context(**pw.devices[args.device])
            print(f"模拟设备：{args.device}（{pw.devices[args.device]['viewport']}）")
        else:
            ctx = browser.new_context(viewport={"width": args.width, "height": args.height})
        page = ctx.new_page()
        page.on("console", lambda m: (
            errors.append(f"[{m.type}] {m.text}") if m.type == "error" else logs.append(m.text)
        ))
        page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

        print(f"打开 {args.url} …")
        page.goto(args.url, wait_until="load", timeout=60000)

        # 等指纹库加载完（状态变成"就绪"）
        try:
            page.wait_for_function(
                "() => document.getElementById('statusText')?.textContent?.includes('就绪')",
                timeout=120000,
            )
            print("数据加载完成 ✓")
        except Exception:
            status = page.text_content("#statusText")
            print(f"数据加载未完成，当前状态：{status!r}")
            print(f"页面日志：{page.text_content('#log')}")
            browser.close()
            return 1

        print(f"指纹库行数：{page.evaluate('window.__S?.index?.length ?? \"(未暴露)\"')}")
        log_text = page.text_content("#log") or ""
        for line in log_text.strip().split("\n"):
            print("  " + line.strip())

        # 上传截图
        page.set_input_files("#fileInput", [str(p) for p in photos])
        page.wait_for_timeout(500)
        summary = page.text_content("#fileSummary")
        print(f"已选文件：{summary}")

        btn = page.query_selector("#btnRun")
        if btn.is_disabled():
            print("「开始识别」按钮仍禁用 ✗")
            browser.close()
            return 1

        print("开始识别 …")
        page.click("#btnRun")
        try:
            page.wait_for_function(
                "() => document.getElementById('statusText')?.textContent?.includes('就绪')",
                timeout=300000,
            )
        except Exception:
            print("识别超时")
            page.screenshot(path=str(REPO / args.shot_out), full_page=True)
            browser.close()
            return 1

        page.wait_for_timeout(1200)

        # 抓结果
        count_text = page.text_content("#resultCount") or ""
        rows = page.eval_on_selector_all("tr.row", "els => els.length")
        thumbs = page.eval_on_selector_all("#resultBody td.thumb img", "els => els.length")
        loaded_thumbs = page.eval_on_selector_all(
            "#resultBody td.thumb img",
            "els => els.filter(i => i.complete && i.naturalWidth > 0).length",
        )
        # 对照视图：每行应该有「截图格」和「匹配卡图」两张
        pair_cells = page.eval_on_selector_all("#resultBody .pair .cellimg", "els => els.length")
        pair_match = page.eval_on_selector_all("#resultBody .pair .matchimg", "els => els.length")
        stats = page.eval_on_selector_all(
            "#summary .stat",
            "els => els.map(e => e.querySelector('.k').textContent + '=' + e.querySelector('.v').textContent)",
        )
        has_search = page.query_selector("#invSearch") is not None
        has_filter = page.query_selector("#invFilter") is not None
        inv_rows = page.eval_on_selector_all("tr.invrow", "els => els.length")

        print(f"\n结果格数：{rows}")
        print(f"结果统计：{count_text}")
        print(f"对照视图：截图格 {pair_cells} 张 / 匹配卡图 {pair_match} 张")
        print(f"缩略图：{loaded_thumbs}/{thumbs} 张成功加载")
        print(f"清单：{inv_rows} 行，搜索框={'有' if has_search else '无'}，筛选={'有' if has_filter else '无'}")
        print(f"清单统计：{', '.join(stats)}")

        # 分页后清单页默认是隐藏的，Playwright 的 fill() 要求元素可见，
        # 所以先切到清单页再操作
        page.evaluate("() => window.__bdh?.showTab('inventory')")
        page.wait_for_timeout(300)
        inv_rows = page.eval_on_selector_all("tr.invrow", "els => els.length")

        # 搜索框实际过滤一遍
        if has_search and inv_rows:
            page.fill("#invSearch", "香澄")
            page.wait_for_timeout(400)
            filtered = page.eval_on_selector_all("tr.invrow", "els => els.length")
            page.fill("#invSearch", "")
            page.wait_for_timeout(300)
            print(f"搜索「香澄」后：{filtered} 行（原 {inv_rows} 行）")

        # 导航体检：四个 tab 都要能切
        tabs = page.evaluate("""() => {
            const out = [];
            for (const t of ['import','result','inventory','sync']) {
                window.__bdh.showTab(t);
                const shown = [...document.querySelectorAll('.view')]
                    .filter(v => getComputedStyle(v).display !== 'none')
                    .map(v => v.dataset.view);
                const active = document.querySelector('.navitem.active')?.dataset.tab;
                out.push([t, shown.join(','), active]);
            }
            window.__bdh.showTab('result');
            return out;
        }""")
        print("导航切换：")
        nav_ok = True
        for want, shown, active in tabs:
            ok = (shown == want and active == want)
            nav_ok = nav_ok and ok
            print(f"  {'✓' if ok else '✗'} 切到 {want:10} 显示 [{shown}] 高亮 [{active}]")

        # 导航栏形态：窄屏应该是底部栏（横向），宽屏应该是侧边栏（纵向）
        navinfo = page.evaluate("""() => {
            const nav = document.getElementById('appnav');
            const r = nav.getBoundingClientRect();
            const cs = getComputedStyle(nav);
            return { dir: cs.flexDirection, w: Math.round(r.width), h: Math.round(r.height),
                     top: Math.round(r.top), bottom: Math.round(window.innerHeight - r.bottom) };
        }""")
        print(f"导航栏：{navinfo['dir']} {navinfo['w']}×{navinfo['h']} "
              f"(top={navinfo['top']} bottom={navinfo['bottom']})")

        # 每个分页存一张图，方便肉眼过一遍
        if args.tabs:
            base = str(REPO / args.shot_out).replace(".png", "")
            for t in ["import", "result", "inventory", "sync"]:
                page.evaluate("(t) => window.__bdh.showTab(t)", t)
                page.wait_for_timeout(600)
                page.screenshot(path=f"{base}_{t}.png")
            page.evaluate("() => window.__bdh.showTab('result')")
            print(f"已存 4 张分页截图：{base}_{{import,result,inventory,sync}}.png")

        # 布局体检：横向溢出是移动端最常见的毛病
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        print(f"横向溢出：{overflow} px" + ("（有横向滚动条 ✗）" if overflow > 2 else "（无 ✓）"))
        # 哪些元素超出了视口宽度
        wide = page.evaluate("""() => {
            const vw = document.documentElement.clientWidth;
            const out = [];
            document.querySelectorAll('*').forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.width > vw + 2 && r.height > 0) {
                    out.push(`${el.tagName.toLowerCase()}.${el.className || '(无类名)'} → ${Math.round(r.width)}px`);
                }
            });
            return out.slice(0, 8);
        }""")
        if wide:
            print("  超宽元素：")
            for w in wide:
                print("   ", w)

        log_text = page.text_content("#log") or ""
        tail = [l.strip() for l in log_text.strip().split("\n")][-6:]
        print("\n运行日志（末 6 行）：")
        for line in tail:
            print("  " + line)

        page.screenshot(path=str(REPO / args.shot_out), full_page=True)
        vp_path = str(REPO / args.shot_out).replace(".png", "_vp.png")
        page.screenshot(path=vp_path)
        print(f"\n截图已保存：{args.shot_out}（整页）、{vp_path}（首屏）")
        browser.close()

    real_errors = [e for e in errors if "favicon" not in e.lower()]
    if real_errors:
        print(f"\n控制台报错 {len(real_errors)} 条：")
        for e in real_errors[:12]:
            print("  " + e)
        return 1
    print("\n无控制台报错 ✓")

    ok = rows > 0 and loaded_thumbs > 0
    print("自检" + ("通过 ✓" if ok else "未通过 ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
