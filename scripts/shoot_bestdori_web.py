"""截取 Bestdori 官网的导入界面。

很多人卡在「官网怎么点」，光说「点 Import」太抽象。这里把真实的官网界面拍下来：
左侧栏 PROFILE → Import，右侧 Profile Data 文本框 + Import 按钮。

**不登录** —— 导入框在未登录时也能看到，足够演示操作位置；
但要真正存到云端必须先登录，这点在旁白里说清楚。

产物：BestdoriHelper-使用说明/assets/web_import_*.png
用法：python scripts/shoot_bestdori_web.py
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

PROJ = Path(__file__).resolve().parents[1] / "BestdoriHelper-使用说明"
OUT = PROJ / "assets"
URL = "https://bestdori.com/profile/manager"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto(URL, wait_until="load", timeout=90000)
        pg.wait_for_timeout(5000)

        # 首次访问会弹「Welcome to Bestdori!」偏好设置框，先关掉
        try:
            pg.get_by_text("Close", exact=True).first.click(timeout=8000)
            print("  已关闭欢迎弹窗")
        except Exception:
            print("  没有欢迎弹窗（或已关过）")
        pg.wait_for_timeout(1500)

        # 确保左侧栏的 Import 被选中
        try:
            pg.get_by_role("link", name="Import").first.click(timeout=8000)
            print("  已点击侧栏 Import")
        except Exception as e:
            print(f"  点 Import 失败: {e}")
        pg.wait_for_timeout(2500)

        # ① 整页（能看到左侧栏 PROFILE 菜单 + 右侧导入区）
        pg.screenshot(path=str(OUT / "web_import_full.png"))
        print("  ✓ web_import_full.png")

        # ② 只截「Profile Data 文本框 + Import 按钮」这一块 —— 放进视频里最清楚
        box = pg.evaluate("""() => {
            // 找 placeholder 为 Enter profile data 的 textarea
            const ta = [...document.querySelectorAll('textarea')]
                .find(t => (t.placeholder || '').toLowerCase().includes('profile data'));
            if (!ta) return null;
            const r = ta.getBoundingClientRect();
            // 往上带一点标题，往下带按钮
            return {
                x: Math.max(0, Math.round(r.x) - 40),
                y: Math.max(0, Math.round(r.y) - 90),
                width: Math.round(r.width) + 80,
                height: Math.round(r.height) + 200
            };
        }""")
        if box:
            pg.screenshot(path=str(OUT / "web_import_box.png"), clip=box)
            print(f"  ✓ web_import_box.png  {box['width']}×{box['height']}")
        else:
            print("  ! 没找到 Profile Data 文本框")

        # ③ 左侧栏（PROFILE 菜单，Import 高亮）
        side = pg.evaluate("""() => {
            const a = [...document.querySelectorAll('a')]
                .find(e => e.innerText.trim() === 'Import');
            if (!a) return null;
            const r = a.getBoundingClientRect();
            return {
                x: 0,
                y: Math.max(0, Math.round(r.y) - 260),
                width: Math.max(200, Math.round(r.x) + Math.round(r.width) + 30),
                height: 520
            };
        }""")
        if side:
            pg.screenshot(path=str(OUT / "web_import_sidebar.png"), clip=side)
            print(f"  ✓ web_import_sidebar.png  {side['width']}×{side['height']}")

        browser.close()

    print(f"\n截图已存到 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
