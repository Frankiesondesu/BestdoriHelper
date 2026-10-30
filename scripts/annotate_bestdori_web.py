"""探测 Bestdori 官网导入界面的元素坐标，并生成带编号标注的截图。

为什么要这么干：光说「点 Import」太抽象，很多人打开官网一脸茫然。
这个脚本把真实页面上每个按钮的**像素坐标**测出来，再画上 ① ② ③ 编号，
视频 / PPT 里直接指哪打哪。

**不登录** —— 导入框和登录入口在未登录时都能看到，足够演示位置。

**界面切成简体中文** —— 官网对首次访问者是英文，而这份说明是中文的，
截图是英文的话用户对着自己的屏幕根本对不上。

产物：
  BestdoriHelper-使用说明/assets/web_import_annotated.png   （带编号标注的整页）
  BestdoriHelper-使用说明/官网导入坐标.json                  （坐标表，便于复用）
用法：python scripts/annotate_bestdori_web.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

PROJ = Path(__file__).resolve().parents[1] / "BestdoriHelper-使用说明"
ASSETS = PROJ / "assets"
URL = "https://bestdori.com/profile/manager"

VIEW_W, VIEW_H = 1440, 900
SCALE = 2

#: Bestdori 的界面语言存在 localStorage 的这个键里。实测：0=En 1=日 2=简 3=繁 …
#: **必须切成简体中文** —— 这份说明是中文的，截图是英文的话用户对不上自己的屏幕。
UI_LANG_KEY = "settings/profile/preferences/uiLang"
UI_LANG_CN = "2"

ACCENT = (232, 71, 139)      # #E8478B 项目主色
INK = (31, 36, 48)

# 要探测的元素：编号 → (说明, 返回候选 Element 数组的 JS 表达式)
# 数组里第一个「可见」的会被采用 —— 页面上常有隐藏副本（面包屑里的同名链接、
# 弹窗里的同名按钮），不筛可见就会量到 0×0 的幽灵元素。
#
# ⚠️ 选择器**不要依赖界面文案**：官网语言一切换，「Import」就变「导入资料」，
#    按英文文案写的探测会全部落空。这里尽量用 href / 结构定位，
#    实在只能靠文案的就同时接受中英文。
#
# ⚠️ 实测修正（2026-09-22）：官网**右上角没有 Log In**。
#    顶部栏只有 logo / 面包屑 / 两个图标 / 返回上一页，登录入口在页面中部
#    云存取那段文字里（"要使用云存取, 请先 登录 或 注册"）。
#    之前 PPT 写「右上角 Log In」是错的。
PROBES: list[tuple[str, str, str]] = [
    ("1", "登录链接（页面中部 云存取 那段）",
     "[...document.querySelectorAll('a[href*=\"profile/account?t=login\"]')]"),
    ("2", "左侧栏「用户档案」下的 导入资料",
     "[...document.querySelectorAll('a')].filter(a => /^(导入资料|Import)$/"
     ".test(a.innerText.trim()) && a.getBoundingClientRect().x < 260)"),
    ("3", "「用户档案数据」文本框",
     "[...document.querySelectorAll('textarea')]"),
    ("4", "「导入」按钮（文本框正下方）",
     "[...document.querySelectorAll('button')].filter(b => /^(导入|Import)$/"
     ".test(b.innerText.trim()))"),
]

# 编号 → 图上标签（视频里图会缩到 ~860px 宽，字得够大才看得清）
LABELS = {
    "1": "① 先登录",
    "2": "② 点 导入资料",
    "3": "③ 粘贴档案",
    "4": "④ 确认导入",
}

# 标签摆放：默认在编号右边，挡到内容就换边/换到下方
LABEL_POS = {"1": "below", "2": "after", "3": "right", "4": "right"}


def font(size: int) -> ImageFont.FreeTypeFont:
    for p in ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc",
              "C:/Windows/Fonts/simhei.ttf"):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    boxes: dict[str, dict] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": VIEW_W, "height": VIEW_H},
                                  device_scale_factor=SCALE)
        # 切成简体中文（在页面脚本之前注入，首屏就是中文）
        ctx.add_init_script(
            f"try {{ localStorage.setItem({UI_LANG_KEY!r}, {UI_LANG_CN!r}); }} catch (e) {{}}"
        )
        pg = ctx.new_page()
        pg.goto(URL, wait_until="load", timeout=90000)
        pg.wait_for_timeout(5000)

        # 首次访问会弹偏好设置框（中文界面下按钮是「关闭」）
        for label in ("关闭", "Close"):
            try:
                pg.get_by_text(label, exact=True).first.click(timeout=8000)
                print(f"  已关闭欢迎弹窗（{label}）")
                break
            except Exception:
                continue
        else:
            print("  没有欢迎弹窗（或已关过）")
        pg.wait_for_timeout(1500)

        # 滚到顶部，保证整页坐标一致
        pg.evaluate("window.scrollTo(0, 0)")
        pg.wait_for_timeout(800)

        for num, desc, js in PROBES:
            expr = f"""() => {{
                const vis = els => els.filter(e => {{
                    const r = e.getBoundingClientRect();
                    return r.width > 1 && r.height > 1 &&
                           getComputedStyle(e).visibility !== 'hidden' &&
                           getComputedStyle(e).display !== 'none';
                }});
                const el = vis({js.strip()})[0];
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {{x: Math.round(r.x), y: Math.round(r.y),
                         w: Math.round(r.width), h: Math.round(r.height),
                         text: (el.innerText || el.placeholder || '').trim().slice(0, 40)}};
            }}"""
            try:
                b = pg.evaluate(expr)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {num} 探测失败: {e}")
                b = None
            if b:
                boxes[num] = {"desc": desc, **b}
                print(f"  {num}  {b['x']:>4},{b['y']:>4}  {b['w']:>4}×{b['h']:<4} "
                      f"{b['text']!r}")
            else:
                print(f"  ! {num} 没找到 —— {desc}")

        pg.screenshot(path=str(ASSETS / "web_import_full.png"))
        browser.close()

    # ---- 画编号标注 ----
    img = Image.open(ASSETS / "web_import_full.png").convert("RGB")
    W_img, H_img = img.size
    d = ImageDraw.Draw(img)
    f_num = font(64)
    f_lab = font(52)

    R = 56          # 编号圆半径
    PAD = 22        # 标签内边距

    for num, b in boxes.items():
        # 元素框（CSS 坐标 → 图像坐标）
        x0, y0 = b["x"] * SCALE, b["y"] * SCALE
        x1, y1 = (b["x"] + b["w"]) * SCALE, (b["y"] + b["h"]) * SCALE
        d.rectangle([x0 - 4, y0 - 4, x1 + 4, y1 + 4], outline=ACCENT, width=6)

        # 编号圆点压在元素左上角
        cx, cy = x0 + R - 10, y0 + R - 10

        # 标签：默认放圆的右边；pos=below 放到元素框正下方
        lab = LABELS.get(num, "")
        tb = (0, 0, 0, 0)
        tw = th = 0
        if lab:
            tb = d.textbbox((0, 0), lab, font=f_lab)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]

        pos = LABEL_POS.get(num, "right")
        if pos == "below":
            lx = x0
            ly = y1 + 16
        elif pos == "after":
            # 贴在被标注元素的**右边缘之外**。左侧栏很窄（200px），
            # 用默认的「圆点右边」会把标签压在菜单文字上 —— 恰好盖住要指的那一项。
            lx = x1 + 14
            ly = cy - th / 2 - PAD
            if lx + tw + PAD * 2 > W_img:
                lx = max(0, x0 - 14 - tw - PAD * 2)
        else:
            lx = cx + R + 14
            if lx + tw + PAD * 2 > W_img:
                lx = cx - R - 14 - tw - PAD * 2
            ly = cy - th / 2 - PAD

        if lab:
            d.rounded_rectangle([lx, ly, lx + tw + PAD * 2, ly + th + PAD * 2],
                                radius=14, fill=(255, 255, 255),
                                outline=ACCENT, width=5)
            d.text((lx + PAD - tb[0], ly + PAD - tb[1]), lab, font=f_lab, fill=ACCENT)

        d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=ACCENT,
                  outline=(255, 255, 255), width=6)
        nb = d.textbbox((0, 0), num, font=f_num)
        d.text((cx - (nb[2] - nb[0]) / 2 - nb[0], cy - (nb[3] - nb[1]) / 2 - nb[1]),
               num, font=f_num, fill=(255, 255, 255))

    img.save(ASSETS / "web_import_annotated.png")
    print(f"\n  ✓ web_import_annotated.png  {img.size[0]}×{img.size[1]}")

    out = PROJ / "官网导入坐标.json"
    out.write_text(json.dumps(boxes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ {out.name}")

    # ---- 打印给视频用的相对位置描述 ----
    print("\n相对位置（CSS 像素，视口 1440×900）：")
    for num, b in boxes.items():
        cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
        hx = "左侧" if cx < VIEW_W / 3 else ("中间" if cx < VIEW_W * 2 / 3 else "右侧")
        hy = "上方" if cy < VIEW_H / 3 else ("中部" if cy < VIEW_H * 2 / 3 else "下方")
        print(f"  {num}  {hx}{hy}  中心 ({cx:.0f}, {cy:.0f})  {b['desc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
