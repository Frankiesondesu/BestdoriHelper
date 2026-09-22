"""生成 B 站投稿封面（1146×717）。

**封面要传达的核心动作：从一张游戏截图里，自动切出每一张卡面。**
所以主视觉不是「已识别的结果」，而是「截图 ——提取——> 卡面」这条链路：
上半是游戏里那张 4×7 的卡面网格截图，中间一个箭头，下半是从里面对应位置
真正切出来的三张卡面。一眼就能看懂软件在干什么。

B 站封面的三条经验：
1. **字要大** —— 手机信息流里封面只有邮票大小，小字完全看不清
2. **右下角留白** —— B 站的时长角标压在那里，所以统计徽章放左下
3. **不要用整屏截图** —— 缩到封面尺寸后细节全糊，必须裁特写

渲染方式沿用 render_slides.py：HTML + Playwright 出 2 倍图，
再用 LANCZOS 缩到目标尺寸（缩图抗锯齿让字边缘更干净）。

产物：BestdoriHelper-使用说明/封面-b站.png
用法：python scripts/make_bili_cover.py
"""

from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
PROJ = REPO / "BestdoriHelper-使用说明"
ASSETS = PROJ / "assets"
OUT = PROJ / "封面-b站.png"

W, H = 1146, 717
SCALE = 2

INK = "#20242F"
ACCENT = "#E8478B"
ACCENT_2 = "#7C5CFF"

#: 素材来源：那张真实的游戏卡面网格截图
SHOT = "Photo/1789790736148.jpg"
#: 截图里卡面网格的实测坐标
GRID_BOX = (660, 260, 1900, 1000)
#: 网格里三格的起点与步长（每格约 170px，间距约 185px）
CELL_X0, CELL_Y0, CELL_S, CELL_W = 704, 292, 185, 168


def data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def crop(box: tuple[int, int, int, int], dst: Path,
         size: tuple[int, int] | None = None) -> Path:
    im = Image.open(REPO / SHOT).crop(box)
    if size:
        im = im.resize(size, Image.LANCZOS)
    im.save(dst)
    return dst


def build() -> str:
    grid = crop(GRID_BOX, ASSETS / "_cover_grid.png", (600, 358))
    cells = [
        crop((CELL_X0 + i * CELL_S, CELL_Y0,
              CELL_X0 + i * CELL_S + CELL_W, CELL_Y0 + CELL_W),
             ASSETS / f"_cover_cell{i}.png")
        for i in range(3)
    ]
    grid_uri = data_uri(grid)
    cell_html = "".join(
        f'<div class="cell"><img src="{data_uri(c)}" alt=""></div>' for c in cells)

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ width: {W}px; height: {H}px; overflow: hidden;
            font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif; }}
    .stage {{ position: relative; width: {W}px; height: {H}px;
              background: linear-gradient(135deg, #1B1F2A 0%, #262C3C 48%, #33243C 100%); }}
    .glow {{ position: absolute; right: -140px; top: -180px; width: 720px; height: 720px;
             border-radius: 50%; background: radial-gradient(circle,
               rgba(232,71,139,.42) 0%, rgba(232,71,139,.12) 52%, rgba(232,71,139,0) 72%); }}
    .tiles {{ position: absolute; inset: 0; opacity: .07;
             background-image: linear-gradient(rgba(255,255,255,.6) 1px, transparent 1px),
                               linear-gradient(90deg, rgba(255,255,255,.6) 1px, transparent 1px);
             background-size: 56px 56px; }}

    /* ---- 左：品牌 + 大标题 + 标签 ---- */
    .left {{ position: absolute; left: 54px; top: 56px; width: 520px; }}
    .brand {{ display: flex; align-items: center; gap: 10px; }}
    .mark {{ width: 34px; height: 34px; border-radius: 9px;
             background: linear-gradient(135deg, {ACCENT} 0%, {ACCENT_2} 100%); }}
    .brand b {{ font-size: 21px; font-weight: 700; color: #fff; letter-spacing: .3px; }}
    .brand b i {{ font-style: normal; color: {ACCENT}; }}
    h1 {{ margin-top: 24px; font-size: 74px; line-height: 1.06; font-weight: 800;
          color: #fff; letter-spacing: -1px; }}
    h1 em {{ font-style: normal; color: {ACCENT};
             text-shadow: 0 0 34px rgba(232,71,139,.55); }}
    .tags {{ display: flex; gap: 10px; margin-top: 26px; }}
    .tag {{ padding: 8px 17px; border-radius: 999px; font-size: 19px; font-weight: 700; }}
    .tag.a {{ background: {ACCENT}; color: #fff; }}
    .tag.b {{ background: rgba(255,255,255,.12); color: #EDEFF7;
              border: 1px solid rgba(255,255,255,.22); }}
    .tag.c {{ background: rgba(124,92,255,.20); color: #C9BCFF;
              border: 1px solid rgba(124,92,255,.45); }}

    /* ---- 右：截图 ——提取——> 卡面 这条链路 ---- */
    .cap1 {{ position: absolute; right: 50px; top: 60px; font-size: 15px;
             font-weight: 700; color: #C9D2E4; letter-spacing: .4px; }}
    .shot {{ position: absolute; right: 46px; top: 90px; width: 442px;
             border-radius: 13px; overflow: hidden; transform: rotate(-1.4deg);
             border: 1px solid rgba(255,255,255,.20);
             box-shadow: 0 20px 44px rgba(0,0,0,.55); }}
    .shot img {{ width: 100%; display: block; }}

    .arrow {{ position: absolute; right: 236px; top: 304px; width: 64px; height: 64px;
              border-radius: 50%;
              background: linear-gradient(135deg, {ACCENT} 0%, {ACCENT_2} 100%);
              display: flex; align-items: center; justify-content: center;
              font-size: 32px; font-weight: 800; color: #fff;
              box-shadow: 0 0 0 7px rgba(232,71,139,.20), 0 10px 26px rgba(232,71,139,.45); }}

    .cells {{ position: absolute; right: 42px; top: 386px; display: flex; gap: 13px; }}
    .cell {{ width: 130px; border-radius: 11px; overflow: hidden;
             border: 2px solid rgba(232,71,139,.65); background: #2A3040;
             box-shadow: 0 14px 30px rgba(0,0,0,.5); }}
    .cell img {{ width: 100%; display: block; }}
    .cell:nth-child(1) {{ transform: rotate(-2deg) translateY(7px); }}
    .cell:nth-child(3) {{ transform: rotate(2deg) translateY(7px); }}
    .cap2 {{ position: absolute; right: 46px; top: 552px; font-size: 15px;
             font-weight: 700; color: #FF9CC6; letter-spacing: .4px; }}

    /* ---- 左下：统计（右下留给 B 站时长角标）---- */
    .stat {{ position: absolute; left: 54px; bottom: 92px; display: flex; gap: 9px; }}
    .stat span {{ padding: 7px 15px; border-radius: 999px; font-size: 17px; font-weight: 700;
                  background: rgba(255,255,255,.10); color: #E7EAF3;
                  border: 1px solid rgba(255,255,255,.20); }}
    .stat span b {{ color: #FF7DB4; }}
    .foot {{ position: absolute; left: 54px; bottom: 34px; font-size: 16px;
             color: rgba(255,255,255,.52); letter-spacing: .2px; }}
    </style></head><body>
    <div class="stage">
      <div class="glow"></div><div class="tiles"></div>

      <div class="left">
        <div class="brand"><div class="mark"></div>
          <b>Bestdori<i>Helper</i></b></div>
        <h1>从截图里<br><em>提取卡面</em></h1>
        <div class="tags">
          <div class="tag a">自动切分</div>
          <div class="tag b">识别卡名</div>
          <div class="tag c">免安装</div>
        </div>
      </div>

      <div class="cap1">① 游戏里的卡面列表截图</div>
      <div class="shot"><img src="{grid_uri}" alt=""></div>
      <div class="arrow">↓</div>
      <div class="cells">{cell_html}</div>
      <div class="cap2">② 自动切出每一张卡面</div>

      <div class="stat"><span><b>56</b> 格一次认完</span><span>置信度 <b>0.94</b></span></div>
      <div class="foot">《BanG Dream! 少女乐团派对》卡面管理工具 · 网页 / 安卓 / Windows</div>
    </div>
    </body></html>"""


def main() -> int:
    html = OUT.with_suffix(".html")
    html.write_text(build(), encoding="utf-8")

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
        page = b.new_context(viewport={"width": W, "height": H},
                             device_scale_factor=SCALE).new_page()
        page.goto(html.as_uri(), wait_until="load")
        page.wait_for_timeout(700)
        png = OUT.with_name(OUT.stem + "-2x.png")
        page.screenshot(path=str(png))
        b.close()

    im = Image.open(png).convert("RGB").resize((W, H), Image.LANCZOS)
    im.save(OUT, quality=95)
    png.unlink(missing_ok=True)
    (ASSETS / "_cover_grid.png").unlink(missing_ok=True)
    for i in range(3):
        (ASSETS / f"_cover_cell{i}.png").unlink(missing_ok=True)
    print(f"✓ {OUT.name}  {im.size[0]}x{im.size[1]}  {OUT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
