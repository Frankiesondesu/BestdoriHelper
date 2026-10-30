"""截取 Bestdori 官网的界面，用于「在官网上导入」教程。

很多人卡在「官网怎么点」，光说「点导入资料」太抽象。这里把真实界面拍下来。

**语言必须切成简体中文。** 官网对首次访问者是英文界面，而这份说明是中文的 ——
截图是英文的话，用户对着自己的屏幕根本对不上。所以脚本用
`localStorage['settings/profile/preferences/uiLang'] = '2'` 直接设成简体中文
（实测 2 = 简），并在页面脚本执行前注入，保证首屏就是中文。

顺带拍一张欢迎弹窗（里面有「用户界面语言」选项），教程里用来教怎么切中文。

**不登录** —— 导入框在未登录时也能看到，足够演示操作位置；
但要真正存到云端必须先登录，这点在旁白里说清楚。

产物：BestdoriHelper-使用说明/assets/web_*.png
用法：python scripts/shoot_bestdori_web.py
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

PROJ = Path(__file__).resolve().parents[1] / "BestdoriHelper-使用说明"
OUT = PROJ / "assets"
URL = "https://bestdori.com/profile/manager"

#: Bestdori 的界面语言存在 localStorage 的这个键里。实测值：0=En 1=日 2=简 3=繁 …
#: 必须在页面自己的脚本跑之前注入，否则首屏会先渲染成英文再切过来。
UI_LANG_KEY = "settings/profile/preferences/uiLang"
UI_LANG_CN = "2"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  device_scale_factor=2)
        ctx.add_init_script(
            f"try {{ localStorage.setItem({UI_LANG_KEY!r}, {UI_LANG_CN!r}); }} catch (e) {{}}"
        )
        pg = ctx.new_page()
        pg.goto(URL, wait_until="load", timeout=90000)
        # Bestdori 的界面文案是**异步**替换的：语言包加载完之前，页面上会直接
        # 渲染成 "cloud.txtLogin[4]" 这类占位符。等固定秒数不稳（实测 6 秒仍有
        # 残留，截图里很难看），所以改成轮询等它消失再往下走。
        # 关键：注入 localStorage 后再 **reload 一次**。
        # 第一次打开时语言包是运行时替换的，会被漏掉个别键（实测
        # "cloud.txtLogin[4]" 一直不消失）；刷新后首屏就按中文渲染，不再有残留。
        pg.reload(wait_until="load", timeout=90000)
        pg.wait_for_timeout(2500)
        try:
            pg.wait_for_function(
                "() => !/[A-Za-z_]+\\.[A-Za-z_]+\\[\\d+\\]/.test(document.body.innerText)",
                timeout=20000,
            )
            print("  \u2713 i18n 就绪（无占位符残留）")
        except Exception:
            print("  \u26a0 20 秒内仍有占位符残留，照常截图")

        # ① 欢迎弹窗 —— 教程里用来说明「在哪把界面切成中文」。
        #    首次访问才有；已有设置的话不会出现，那也没关系（跳过即可）。
        got_dialog = False
        for sel in [".modal", "[class*=modal]", "[class*=Modal]", "[class*=dialog]"]:
            try:
                el = pg.query_selector(sel)
                if el and el.is_visible():
                    el.screenshot(path=str(OUT / "web_lang_dialog.png"))
                    print(f"  ✓ web_lang_dialog.png（{sel}）")
                    got_dialog = True
                    break
            except Exception:
                continue
        if not got_dialog:
            # 兜底：把「用户界面语言」那行连同选项一起裁出来
            box = pg.evaluate("""() => {
                const el = [...document.querySelectorAll('*')]
                    .find(e => e.children.length === 0 && /界面语言|UI Language/.test(e.textContent));
                if (!el) return null;
                let p = el;
                for (let i = 0; i < 4 && p.parentElement; i++) p = p.parentElement;
                const r = p.getBoundingClientRect();
                return {x: Math.max(0, r.x - 20), y: Math.max(0, r.y - 20),
                        width: Math.min(1400, r.width + 40), height: Math.min(800, r.height + 40)};
            }""")
            if box:
                pg.screenshot(path=str(OUT / "web_lang_dialog.png"), clip=box)
                print(f"  ✓ web_lang_dialog.png（裁切 {box['width']:.0f}×{box['height']:.0f}）")
            else:
                print("  ! 找不到语言选项区域（已有设置时正常）")

        # 关掉弹窗（中文界面下按钮是「关闭」）
        for label in ["关闭", "Close"]:
            try:
                pg.get_by_text(label, exact=True).first.click(timeout=6000)
                print(f"  已关闭欢迎弹窗（{label}）")
                break
            except Exception:
                continue
        pg.wait_for_timeout(2000)

        # ② 整页（左侧栏「用户档案」菜单 + 右侧导入区）
        pg.screenshot(path=str(OUT / "web_import_full.png"))
        print("  ✓ web_import_full.png")

        # ③ 只截「用户档案数据 文本框 + 导入 按钮」这一块 —— 放进视频里最清楚。
        #    用 textarea 定位（页面只有一个），不依赖 placeholder 文案。
        box = pg.evaluate("""() => {
            const ta = [...document.querySelectorAll('textarea')]
                .filter(t => t.getBoundingClientRect().width > 0)[0];
            if (!ta) return null;
            const r = ta.getBoundingClientRect();
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
            print("  ! 没找到用户档案数据文本框")

        # ④ 左侧栏（「用户档案」菜单，「导入资料」高亮）
        side = pg.evaluate("""() => {
            const a = [...document.querySelectorAll('a')]
                .filter(e => e.getBoundingClientRect().width > 0)
                .find(e => /^(导入资料|Import)$/.test(e.innerText.trim()));
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
