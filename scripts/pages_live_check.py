"""对**已部署的 GitHub Pages 站点**做一次真实识别，确认线上能跑通。

本地跑通不等于线上跑通 —— 线上要额外确认：指纹库（3.28 MB）能完整下载、
ES module 在真实 HTTP 下能加载、缩略图可取。

用法::

    python .bdh-test/pages_live_check.py
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
#: GitHub Pages 站点地址；改仓库名时要同步改这里
URL = "https://frankiesondesu.github.io/BestdoriHelper/"

def main() -> int:
    photos = sorted((REPO / "Photo").glob("*.jpg"))[:1]
    if not photos:
        print("Photo/ 里没有截图")
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        errs: list[str] = []
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errs.append(str(e)))

        print(f"打开 {URL} …")
        page.goto(URL, wait_until="load", timeout=120000)
        page.wait_for_function(
            "() => document.getElementById('statusText')?.textContent?.includes('就绪')",
            timeout=180000)
        print("✓ 页面就绪（指纹库已加载）")

        page.set_input_files("#fileInput", [str(p) for p in photos])
        page.wait_for_timeout(600)
        page.click("#btnRun")
        page.wait_for_function(
            "() => /格/.test(document.getElementById('resultCount')?.textContent || '')",
            timeout=300000)
        print("识别结果：", page.text_content("#resultCount"))

        page.click(".navitem[data-tab='inventory']")
        page.wait_for_timeout(600)
        n = page.evaluate(
            "() => document.querySelectorAll('.invrow, #invBody tr').length")
        print(f"清单行数：{n}")
        print("导出按钮可用：", not page.locator("#btnProfile").is_disabled())

        # 顺带确认档案导出能生成合法 JSON
        page.click("#btnProfile")
        page.wait_for_timeout(1000)
        raw = page.input_value("#profileText")
        data = json.loads(raw)
        print(f"档案导出：{len(data['cards'])} 张卡，server={data['server']}")

        print("\n控制台错误：", errs or "无 ✓")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
