"""生成 B 站投稿封面（16:10 与 4:3 两种比例）。

**B 站有两种封面位，比例不同，各出一张**：
  - 个人空间 / 投稿封面框  -> 16:10，`--ratio 16x10`（1146×717）
  - **首页推荐流是 4:3**   -> `--ratio 4x3`（1146×860）

**手机小窗口是硬约束，也是这份设计的出发点。**
首页推荐里封面只有拇指大 —— 手机约 360px 宽，是原图的 **0.314 倍**。
按这个比例折算，正图里画 15px 的小字缩过去只剩 5px，等于没写。
所以本脚本定了一条量化下限：**正图里不放小于 38px 的文字**
（38 × 0.314 ≈ 12px，刚好是手机上能看清的最小字号）。
结果就是：只留主标题、品牌、三个标签和主视觉，标注、统计、页脚全部砍掉。

**封面要传达的核心动作：从一张游戏截图里，自动切出每一张卡面。**
主视觉不是「已识别的结果」，而是「截图 ——提取——> 卡面」这条链路：
上半是游戏里那张 4×7 的卡面网格截图，中间一个箭头，下半是从同一张截图里
对应位置真正切出来的三张卡面。上下是真实对应关系，不是拼凑的。

渲染方式沿用 render_slides.py：HTML + Playwright 出 2 倍图，
再用 LANCZOS 缩到目标尺寸（缩图抗锯齿让字边缘更干净）。

产物：BestdoriHelper-使用说明/封面-b站-{16x10,4x3}.png
用法：python scripts/make_bili_cover.py            # 两种都出
"""

from __future__ import annotations

import argparse
import base64
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
PROJ = REPO / "BestdoriHelper-使用说明"
ASSETS = PROJ / "assets"

ACCENT = "#E8478B"
ACCENT_2 = "#7C5CFF"

#: 比例 -> (宽, 高)。宽固定 1146，高按比例算
RATIOS: dict[str, tuple[int, int]] = {
    "16x10": (1146, 717),   # 个人空间 / 投稿封面框
    "4x3": (1146, 860),     # 首页推荐流
}
SCALE = 2

#: 素材来源：那张真实的游戏卡面网格截图
SHOT = "Photo/1789790736148.jpg"
#: 截图里卡面网格的实测坐标（与 README 里 --region 那组一致）
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


def build(w: int, h: int) -> str:
    # 4:3 比 16:10 高 20%，主视觉可以放得更大
    tall = h / w > 0.7
    grid = crop(GRID_BOX, ASSETS / "_cover_grid.png", (640 if tall else 600, 382 if tall else 358))
    cells = [
        crop((CELL_X0 + i * CELL_S, CELL_Y0,
              CELL_X0 + i * CELL_S + CELL_W, CELL_Y0 + CELL_W),
             ASSETS / f"_cover_cell{i}.png")
        for i in range(3)
    ]
    grid_uri = data_uri(grid)
    cell_html = "".join(
        f'<div class="cell"><img src="{data_uri(c)}" alt=""></div>' for c in cells)

    # ---- 版面参数（全部按"手机上看得清"来定）----
    # 4:3 比 16:10 高 20%，东西放大、整体下压，别让下面空一大块
    title = 98 if tall else 86          # 主标题；手机上缩到 30px 左右，很清楚
    tag = 34 if tall else 30            # 标签；缩到 11px，可读下限
    shot_w = 516 if tall else 442       # 主视觉宽度
    cell_w = 158 if tall else 132
    top = 108 if tall else 56
    grid_top = 158 if tall else 96
    arrow_top = grid_top + int(shot_w * (382 / 640 if tall else 358 / 600)) - int(arrow_size(tall) * 0.55)
    cells_top = arrow_top + int(arrow_size(tall) * 0.92)

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ width: {w}px; height: {h}px; overflow: hidden;
            font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif; }}
    .stage {{ position: relative; width: {w}px; height: {h}px;
              background: linear-gradient(135deg, #1B1F2A 0%, #262C3C 48%, #33243C 100%); }}
    .glow {{ position: absolute; right: -160px; top: -200px; width: 780px; height: 780px;
             border-radius: 50%; background: radial-gradient(circle,
               rgba(232,71,139,.42) 0%, rgba(232,71,139,.12) 52%, rgba(232,71,139,0) 72%); }}
    .tiles {{ position: absolute; inset: 0; opacity: .07;
             background-image: linear-gradient(rgba(255,255,255,.6) 1px, transparent 1px),
                               linear-gradient(90deg, rgba(255,255,255,.6) 1px, transparent 1px);
             background-size: 56px 56px; }}

    /* ---- 左：品牌 + 大标题 + 标签 ---- */
    .brand {{ position: absolute; left: 56px; top: {top}px;
              display: flex; align-items: center; gap: 12px; }}
    .mark {{ width: 42px; height: 42px; border-radius: 11px;
             background: linear-gradient(135deg, {ACCENT} 0%, {ACCENT_2} 100%); }}
    .brand b {{ font-size: 26px; font-weight: 700; color: #fff; letter-spacing: .3px; }}
    .brand b i {{ font-style: normal; color: {ACCENT}; }}

    h1 {{ position: absolute; left: 56px; top: {top + 78}px;
          font-size: {title}px; line-height: 1.05; font-weight: 800;
          color: #fff; letter-spacing: -2px; }}
    h1 em {{ font-style: normal; color: {ACCENT};
             text-shadow: 0 0 40px rgba(232,71,139,.6); }}

    .tags {{ position: absolute; left: 56px; top: {top + 78 + title * 2 + 46}px;
             display: flex; flex-direction: column; gap: 12px; align-items: flex-start; }}
    .tag {{ padding: 10px 22px; border-radius: 999px; font-size: {tag}px; font-weight: 700; }}
    .tag.a {{ background: {ACCENT}; color: #fff; }}
    .tag.b {{ background: rgba(255,255,255,.12); color: #EDEFF7;
              border: 1px solid rgba(255,255,255,.22); }}
    .tag.c {{ background: rgba(124,92,255,.20); color: #C9BCFF;
              border: 1px solid rgba(124,92,255,.45); }}

    /* ---- 右：截图 ——提取——> 卡面 ---- */
    .shot {{ position: absolute; right: 52px; top: {grid_top}px; width: {shot_w}px;
             border-radius: 14px; overflow: hidden; transform: rotate(-1.4deg);
             border: 1px solid rgba(255,255,255,.20);
             box-shadow: 0 22px 48px rgba(0,0,0,.55); }}
    .shot img {{ width: 100%; display: block; }}

    .arrow {{ position: absolute; right: {int(w * 0.5 - 40)}px; top: {arrow_top}px;
              width: 78px; height: 78px; border-radius: 50%;
              background: linear-gradient(135deg, {ACCENT} 0%, {ACCENT_2} 100%);
              display: flex; align-items: center; justify-content: center;
              font-size: 40px; font-weight: 800; color: #fff;
              box-shadow: 0 0 0 9px rgba(232,71,139,.20), 0 12px 30px rgba(232,71,139,.45); }}

    .cells {{ position: absolute; right: 46px; top: {cells_top}px;
              display: flex; gap: 14px; }}
    .cell {{ width: {cell_w}px; border-radius: 12px; overflow: hidden;
             border: 3px solid rgba(232,71,139,.70); background: #2A3040;
             box-shadow: 0 16px 34px rgba(0,0,0,.5); }}
    .cell img {{ width: 100%; display: block; }}
    .cell:nth-child(1) {{ transform: rotate(-2deg) translateY(8px); }}
    .cell:nth-child(3) {{ transform: rotate(2deg) translateY(8px); }}
    </style></head><body>
    <div class="stage">
      <div class="glow"></div><div class="tiles"></div>

      <div class="brand"><div class="mark"></div>
        <b>Bestdori<i>Helper</i></b></div>
      <h1>从截图里<br><em>提取卡面</em></h1>
      <div class="tags">
        <div class="tag a">自动切分</div>
        <div class="tag b">识别卡名</div>
        <div class="tag c">免安装</div>
      </div>

      <div class="shot"><img src="{grid_uri}" alt=""></div>
      <div class="arrow">↓</div>
      <div class="cells">{cell_html}</div>
    </div>
    </body></html>"""


def arrow_size(tall: bool) -> int:
    return 78


def render(name: str, w: int, h: int) -> Path:
    out = PROJ / f"封面-b站-{name}.png"
    html = PROJ / f"封面-b站-{name}.html"
    html.write_text(build(w, h), encoding="utf-8")

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
        page = b.new_context(viewport={"width": w, "height": h},
                             device_scale_factor=SCALE).new_page()
        page.goto(html.as_uri(), wait_until="load")
        page.wait_for_timeout(700)
        png = out.with_name(out.stem + "-2x.png")
        page.screenshot(path=str(png))
        b.close()

    im = Image.open(png).convert("RGB").resize((w, h), Image.LANCZOS)
    im.save(out, quality=95)
    png.unlink(missing_ok=True)
    print(f"✓ {out.name}  {im.size[0]}x{im.size[1]}  "
          f"{im.size[0] / im.size[1]:.3f}  {out.stat().st_size // 1024} KB")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ratio", choices=[*RATIOS, "all"], default="all")
    args = ap.parse_args()

    names = list(RATIOS) if args.ratio == "all" else [args.ratio]
    try:
        for name in names:
            w, h = RATIOS[name]
            render(name, w, h)
    finally:
        (ASSETS / "_cover_grid.png").unlink(missing_ok=True)
        for i in range(3):
            (ASSETS / f"_cover_cell{i}.png").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
