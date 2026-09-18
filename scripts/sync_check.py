"""验证 Bestdori 同步链路：用 mock 网络跑通「登录 → 读档案 → 生成计划 → 写入」。

为什么要这个：真机验证需要 APK + 真账号，成本高。但同步的**业务逻辑**
（cookie 管理、档案编解码、增量合并、写回）完全可以离线验证 ——
把 bestdori.com 的响应 mock 掉，注入一个假的 `window.Capacitor`
（让网页以为自己在原生壳里），整条链路就能跑。

这样能在装到手机之前就发现逻辑错误，而不是等到用户真的写入档案才发现写坏了。

用法::

    python scripts/sync_check.py
"""

from __future__ import annotations

import functools
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bestdori_helper.bridge import bestdori_api as api  # noqa: E402

# ---- 构造受控数据 -----------------------------------------------------

#: 云端已有的卡：1（未特训）、158（未特训）
REMOTE_CARDS = {
    1: api.new_card_entry(1, {"levelLimit": 20, "stat": {"episodes": [1, 2]}}, False),
    158: api.new_card_entry(158, {"levelLimit": 40, "stat": {"training": {"levelLimit": 10}, "episodes": [1, 2]}}, False),
}
PROFILE = {
    "compression": api.SUPPORTED_COMPRESSION,
    "name": "测试档案",
    "data": {"cards": api.encode_cards(REMOTE_CARDS)},
}

#: 本地清单（注入进网页）—— 覆盖三条分支：
#:   158 远端未特训、本地特训后 -> **升级**
#:   1   远端已有、状态一致     -> **跳过**
#:   4   远端没有              -> **新增**
LOCAL = [
    {"cardId": 158, "trained": True, "score": 0.95, "gap": 0.2, "status": "matched"},
    {"cardId": 1, "trained": False, "score": 0.95, "gap": 0.2, "status": "matched"},
    {"cardId": 4, "trained": False, "score": 0.95, "gap": 0.2, "status": "matched"},
]

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Expose-Headers": "set-cookie",
}

captured: dict[str, object] = {"requests": [], "written": None}


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: ANN002 - 静音，别刷屏
        pass


def _serve_docs() -> tuple[socketserver.TCPServer, int]:
    """临时起一个服务 docs/ 的静态服务器，返回 (服务器, 端口)。

    以前这里是硬编码的 ``http://127.0.0.1:8788/``，**假设外部已经有服务器**，
    所以脚本换台机器就跑不起来（原地重跑还会撞上残留的老进程）。
    改成自己起、端口交给系统分配，脚本才真正能独立复现。
    """
    handler = functools.partial(_QuietHandler, directory=str(REPO / "docs"))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, int(httpd.server_address[1])


def main() -> int:
    httpd, port = _serve_docs()
    print(f"本地服务器：http://127.0.0.1:{port}/（服务 docs/）")
    try:
        return _run(port)
    finally:
        httpd.shutdown()


def _run(port: int) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 950})
        page = ctx.new_page()

        # 让网页以为自己在原生壳里（app.js 只认 window.Capacitor）
        page.add_init_script(
            "window.Capacitor = { isNativePlatform: () => true, Plugins: {} };")

        def handler(route):
            req = route.request
            url = req.url
            method = req.method
            captured["requests"].append(f"{method} {url}")

            if method == "OPTIONS":
                route.fulfill(status=204, headers=CORS, body="")
                return
            if url.endswith("/api/user/login"):
                route.fulfill(
                    status=200,
                    headers={**CORS, "Content-Type": "application/json",
                             "set-cookie": "sessionid=MOCK_SESSION; Path=/"},
                    body=json.dumps({"result": True, "token": "mock"}),
                )
            elif url.endswith("/api/user/me"):
                route.fulfill(
                    status=200, headers={**CORS, "Content-Type": "application/json"},
                    body=json.dumps({"username": "tester", "id": 12345}),
                )
            elif url.endswith("/api/user/profiles"):
                if method == "GET":
                    route.fulfill(
                        status=200, headers={**CORS, "Content-Type": "application/json"},
                        body=json.dumps({"result": True, "profiles": [PROFILE]}),
                    )
                else:
                    captured["written"] = req.post_data
                    route.fulfill(
                        status=200, headers={**CORS, "Content-Type": "application/json"},
                        body=json.dumps({"result": True}),
                    )
            else:
                route.fulfill(status=404, headers={**CORS, "Content-Type": "application/json"},
                              body=json.dumps({"result": False, "code": "NOT_MOCKED"}))

        page.route("https://bestdori.com/**", handler)

        print("打开页面…")
        page.goto(f"http://127.0.0.1:{port}/", wait_until="load", timeout=60000)
        page.wait_for_function(
            "() => document.getElementById('statusText')?.textContent?.includes('就绪')",
            timeout=120000)
        # 默认停在「导入」页，而 .view 非 activated 时是 display:none ——
        # 同步页里的登录框在 Playwright 眼里就是不可见，fill 会直接超时。
        page.click(".navitem[data-tab='sync']")
        page.wait_for_timeout(300)

        is_native = page.evaluate("() => window.__bdh.isNative")
        sync_visible = page.evaluate(
            "() => { const el = document.getElementById('syncCard'); return el && !el.hidden; }")
        print(f"平台判定为原生：{is_native}    同步区块可见：{sync_visible}")
        if not is_native or not sync_visible:
            print("✗ 原生检测或同步区块没生效")
            browser.close()
            return 1

        # 注入受控清单
        page.evaluate("""(items) => {
            const S = window.__bdh.S;
            S.inv.clear();
            for (const x of items) {
                S.inv.set(`${x.cardId}_${x.trained ? 't' : 'n'}`, {...x, source: '测试', shot: 0, cell: 0});
            }
            window.__bdh.renderInventory();
        }""", LOCAL)
        inv_rows = page.eval_on_selector_all("tr.invrow", "els => els.length")
        print(f"注入清单：{inv_rows} 行")

        # 登录
        print("\n登录…")
        page.fill("#bdUser", "tester")
        page.fill("#bdPass", "hunter2")
        page.click("#btnLogin")
        page.wait_for_function(
            "() => document.getElementById('syncState')?.textContent?.includes('已登录')",
            timeout=30000)
        state = page.text_content("#syncState")
        print(f"  状态：{state}")
        opts = page.eval_on_selector_all("#profileSel option", "els => els.map(e => e.textContent)")
        print(f"  云端档案：{opts}")

        # 生成计划
        print("\n生成导入计划…")
        page.click("#btnPlan")
        page.wait_for_timeout(900)
        plan_text = page.text_content("#syncResult")
        print(f"  {plan_text}")

        # 执行导入（自动接受 confirm）
        print("\n执行导入…")
        page.on("dialog", lambda d: d.accept())
        page.click("#btnImport")
        page.wait_for_timeout(1500)
        result = page.text_content("#syncResult")
        print(f"  {result}")

        # 校验写回的 payload
        written = captured["written"]
        ok = True
        if not written:
            print("\n✗ 没有捕获到写入请求")
            ok = False
        else:
            body = json.loads(written)
            entry = body["profiles"][0]
            after = api.decode_cards(entry)
            print(f"\n写入的档案含 {len(after)} 张卡：")
            for cid in sorted(after):
                c = after[cid]
                print(f"  卡{cid}: level={c['level']} train={c['train']} art={c['art']} ep={c['ep']}")

            # 期望：1 保持未特训、158 升级为特训后、4 新增
            checks = [
                ("卡 158 应被升级为特训后", after.get(158, {}).get("train") == 1),
                ("卡 158 等级应为 40+10=50", after.get(158, {}).get("level") == 50),
                ("卡 1 应保持未特训", after.get(1, {}).get("train") == 0),
                ("卡 4 应被新增", 4 in after),
                ("总数应为 3", len(after) == 3),
            ]
            print()
            for name, passed in checks:
                print(f"  {'✓' if passed else '✗'} {name}")
                ok = ok and passed

        print(f"\n捕获到的请求：{captured['requests']}")
        page.screenshot(path=str(REPO / ".bdh-test/sync_check.png"), full_page=True)
        browser.close()

    print("\n" + ("同步链路验证通过 ✓" if ok else "同步链路验证失败 ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
