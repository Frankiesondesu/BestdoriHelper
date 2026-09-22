"""给使用说明 PPT 抓真实界面截图 —— **按需裁剪**版本。

第一版是整屏截图 + `objectFit: cover`，结果 1280×720 的整屏塞进又扁又宽的容器里，
关键内容被裁掉。这一版改为：**先在浏览器里定位要展示的 DOM 元素，
拿它的 bounding rect 当裁剪框，输出的图就是这块内容**。比例和内容都对得上。

抓这些（都改成只截相关区块）：

  step1_import     导入区（拖放卡 + 切分参数 + 卡框贴合）
  step2_compare    结果页（右栏整张对照视图，前 4 行）
  step3_inventory  顶部统计 + 搜索筛选
  step4_export     导出区卡片（含导出 Bestdori 档案）
  sync_app         同步页的登录表单卡片
  sync_web         导出区（同 step4_export）

用法：
  python -m http.server 8788 --directory docs     # 先起服务
  python scripts/shoot_for_ppt.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "BestdoriHelper-使用说明" / "assets"
SHOT_W, SHOT_H = 1280, 720
N_SHOTS = 2


def clip_of_import(pg):
    """导入页：拖放卡 + 切分模式/卡框贴合，不含文件摘要区"""
    return pg.evaluate("""() => {
        const cards = document.querySelectorAll('#view-import > .card');
        const c1 = cards[0].getBoundingClientRect();   // 拖放
        const c2 = cards[1].getBoundingClientRect();   // 参数
        const x = Math.round(c1.x) - 18;
        const y = Math.round(c1.y) - 18;
        const right = Math.round(c2.right) + 18;
        const bottom = Math.round(c2.bottom) + 18;
        return { x, y, width: right - x, height: bottom - y };
    }""")


def clip_of_compare(pg):
    """结果页：右栏对照视图（前 4 行）"""
    return pg.evaluate("""() => {
        const pane = document.getElementById('resultBody').closest('.pane');
        const r = pane.getBoundingClientRect();
        return {
            x: Math.round(r.x),
            y: Math.round(r.y),
            width: Math.round(r.width),
            height: Math.min(Math.round(r.height), 460)
        };
    }""")


def clip_of_result_preview(pg):
    """结果页左栏：原图 + 切分框（最能说明「自动切分」这一步）"""
    return pg.evaluate("""() => {
        const pane = document.getElementById('previewBody').closest('.pane');
        const r = pane.getBoundingClientRect();
        return {
            x: Math.round(r.x),
            y: Math.round(r.y),
            width: Math.round(r.width),
            height: Math.min(Math.round(r.height), 460)
        };
    }""")


def clip_of_inventory_table(pg):
    """清单页的表格本体（带卡图和卡名）"""
    return pg.evaluate("""() => {
        const b = document.getElementById('invBody');
        const r = b.getBoundingClientRect();
        return {
            x: Math.round(r.x),
            y: Math.round(r.y),
            width: Math.round(r.width),
            height: Math.min(Math.round(r.height), 340)
        };
    }""")


def clip_of_inventory(pg):
    """清单页：顶部统计 + 搜索筛选"""
    return pg.evaluate("""() => {
        const sum = document.getElementById('summary');
        const filter = document.getElementById('invSearch').closest('.card');
        const sr = sum.getBoundingClientRect();
        const fr = filter.getBoundingClientRect();
        const x = Math.round(sr.x) - 14;
        const y = Math.round(sr.y) - 14;
        const right = Math.round(fr.right) + 14;
        const bottom = Math.round(fr.bottom) + 14;
        return { x, y, width: right - x, height: bottom - y };
    }""")


def clip_of_export_card(pg):
    """导出区卡片（含 Bestdori 档案按钮）"""
    return pg.evaluate("""() => {
        const cards = [...document.querySelectorAll('#view-inventory .card')];
        const t = cards.find(e => e.textContent.includes('导出 Bestdori 档案'));
        const r = t.getBoundingClientRect();
        return {
            x: Math.max(0, Math.round(r.x) - 14),
            y: Math.max(0, Math.round(r.y) - 14),
            width: Math.min(1280, Math.round(r.width) + 28),
            height: Math.round(r.height) + 28
        };
    }""")


def clip_of_profile_dialog(pg):
    """「导出 Bestdori 档案」弹层 —— 含服务器选择 + 复制按钮 + 满值说明。

    需要先点开弹层（按钮在清单页，识别完后才会启用）。
    """
    pg.evaluate("""() => {
        const b = document.getElementById('btnProfile');
        if (b && !b.disabled) b.click();
    }""")
    pg.wait_for_timeout(800)
    return pg.evaluate("""() => {
        const lb = document.getElementById('lightbox');
        if (!lb || lb.hidden) return null;
        const r = lb.getBoundingClientRect();
        const x = Math.max(0, Math.round(r.x));
        const y = Math.max(0, Math.round(r.y));
        return { x, y, width: Math.round(r.width), height: Math.round(r.height) };
    }""")


def close_dialog(pg):
    pg.evaluate("""() => {
        const b = document.getElementById('lbClose');
        if (b) b.click();
    }""")
    pg.wait_for_timeout(400)


def clip_of_sync_card(pg):
    """同步页的整张卡片（含登录表单）"""
    return pg.evaluate("""() => {
        const c = document.getElementById('syncCard');
        const r = c.getBoundingClientRect();
        return {
            x: Math.max(0, Math.round(r.x) - 14),
            y: Math.max(0, Math.round(r.y) - 14),
            width: Math.min(1280, Math.round(r.width) + 28),
            height: Math.round(r.height) + 28
        };
    }""")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    shots = sorted((REPO / "Photo").glob("*.jpg"))[:N_SHOTS]
    if not shots:
        print("✗ Photo/ 下没有截图")
        return 1

    with sync_playwright() as pw:
        # --no-proxy-server 必填：环境里有 http_proxy，浏览器会把 127.0.0.1 也代理出去。
        browser = pw.chromium.launch(headless=True, args=["--no-proxy-server"])

        def new_page(native=False):
            ctx = browser.new_context(
                viewport={"width": SHOT_W, "height": SHOT_H},
                device_scale_factor=2,                  # 2x → 嵌进 PPT 不糊
            )
            pg = ctx.new_page()
            if native:
                pg.add_init_script(
                    "window.Capacitor = { isNativePlatform: () => true, Plugins: {} };")
            return pg

        def prep(pg):
            pg.goto("http://127.0.0.1:8788/", wait_until="load", timeout=60000)
            print("    页面已加载，等初始化…", flush=True)
            pg.wait_for_function(
                "() => document.getElementById('statusText')?.textContent?.includes('就绪')",
                timeout=180000)
            print("    初始化完成", flush=True)

        def shoot(pg, name, clip):
            # 卡图懒加载：只等视口内的，否则超时不返回
            pg.wait_for_function("""() => {
                const imgs = [...document.querySelectorAll('img')].filter(i => {
                    const r = i.getBoundingClientRect();
                    return r.width > 0 && r.height > 0
                        && r.bottom > 0 && r.top < window.innerHeight;
                });
                return imgs.length === 0 || imgs.every(i => i.complete);
            }""", timeout=30000)
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(OUT / f"{name}.png"), clip=clip)
            print(f"  ✓ {name}.png  clip={clip['width']}×{clip['height']}", flush=True)

        # ===== 网页版形态 =====
        pg = new_page(native=False)
        prep(pg)

        # 跑识别
        print("    开始识别…", flush=True)
        pg.set_input_files("#fileInput", [str(p) for p in shots])
        pg.click("#btnRun")
        pg.wait_for_function(
            "() => document.getElementById('statusText')?.textContent?.includes('就绪')",
            timeout=300000)
        pg.wait_for_timeout(900)
        print("    识别完成", flush=True)

        # ① 导入区
        pg.evaluate("() => window.__bdh.showTab('import')")
        pg.wait_for_timeout(600)
        shoot(pg, "step1_import", clip_of_import(pg))

        # ② 结果对照（右栏整张 = 整个对照视图，前 4 行够看）
        pg.evaluate("() => window.__bdh.showTab('result')")
        pg.wait_for_timeout(700)
        shoot(pg, "step2_compare", clip_of_compare(pg))
        # 左栏原图 + 切分框，用来填充「核对」页的画面
        shoot(pg, "result_preview", clip_of_result_preview(pg))

        # ③ 清单顶部
        pg.evaluate("() => window.__bdh.showTab('inventory')")
        pg.wait_for_timeout(700)
        shoot(pg, "step3_inventory", clip_of_inventory(pg))
        # 清单表格本体
        shoot(pg, "inventory_table", clip_of_inventory_table(pg))

        # ④ 导出区（= sync_web 的同款）
        export_clip = clip_of_export_card(pg)
        shoot(pg, "step4_export", export_clip)
        shoot(pg, "sync_web", export_clip)

        # ⑤ 导出档案弹层（含服务器选择 —— 网页版同步的关键一步）
        dlg = clip_of_profile_dialog(pg)
        if dlg and dlg["width"] > 100:
            shoot(pg, "profile_dialog", dlg)
        else:
            print("  ! profile_dialog 没截到（弹层没打开）")
        close_dialog(pg)
        pg.context.close()

        # ===== 原生壳形态（同步登录）====
        pg2 = new_page(native=True)
        prep(pg2)
        pg2.evaluate("() => window.__bdh.showTab('sync')")
        pg2.wait_for_timeout(600)
        shoot(pg2, "sync_app", clip_of_sync_card(pg2))
        pg2.context.close()

        browser.close()

    print(f"\n截图已存到 {OUT}")
    for p in sorted(OUT.glob("*.png")):
        print(f"  {p.name:26} {p.stat().st_size / 1024:6.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())