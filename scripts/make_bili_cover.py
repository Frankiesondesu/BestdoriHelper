"""生成 B 站投稿封面（1146×717）。

B 站封面规格：1146×717（16:10），JPG/PNG。设计上注意三点：

1. **字要大** —— 手机信息流里封面只有邮票大小，小字完全看不清
2. **右下角留白** —— B 站的时长角标压在那里
3. **一眼看懂是什么** —— 所以主视觉用「原图 vs 识别卡面」的对照截图，
   比纯文字或抽象图形直观得多

渲染方式与 render_slides.py 一致：HTML + Playwright，2 倍图再缩到目标尺寸
（缩图带来的抗锯齿让字边缘更干净）。

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


def data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def crop_pair(src: Path, box: tuple[int, int, int, int], dst: Path) -> Path:
    """从对照表里裁一块「截图格 vs Bestdori 卡图」的特写。

    整屏截图在封面尺寸下只有邮票大，卡面根本看不清；而单独裁一行对照
    （两张脸并排 + 卡名 + 置信度）一眼就能看懂工具在干什么。
    """
    Image.open(src).crop(box).save(dst)
    return dst


def build() -> str:
    # 对照表里两行的对照区：今井莉莎、青叶摩卡（各含左右两张卡面）
    a = crop_pair(ASSETS / "step2_compare.png", (30, 308, 372, 462),
                  ASSETS / "_cover_pair_a.png")
    b = crop_pair(ASSETS / "step2_compare.png", (30, 643, 372, 797),
                  ASSETS / "_cover_pair_b.png")
    pair_a, pair_b = data_uri(a), data_uri(b)

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ width: {W}px; height: {H}px; overflow: hidden;
            font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif; }}
    .stage {{ position: relative; width: {W}px; height: {H}px;
              background: linear-gradient(135deg, #1B1F2A 0%, #262C3C 48%, #33243C 100%); }}
    /* 右上角粉色光斑，把视线往主视觉引 */
    .glow {{ position: absolute; right: -140px; top: -180px; width: 720px; height: 720px;
             border-radius: 50%; background: radial-gradient(circle,
               rgba(232,71,139,.42) 0%, rgba(232,71,139,.12) 52%, rgba(232,71,139,0) 72%); }}
    .grid {{ position: absolute; inset: 0; opacity: .07;
             background-image: linear-gradient(rgba(255,255,255,.6) 1px, transparent 1px),
                               linear-gradient(90deg, rgba(255,255,255,.6) 1px, transparent 1px);
             background-size: 56px 56px; }}

    .left {{ position: absolute; left: 54px; top: 58px; width: 520px; }}
    .brand {{ display: flex; align-items: center; gap: 10px; }}
    .mark {{ width: 34px; height: 34px; border-radius: 9px;
             background: linear-gradient(135deg, {ACCENT} 0%, {ACCENT_2} 100%); }}
    .brand b {{ font-size: 21px; font-weight: 700; color: #fff; letter-spacing: .3px; }}
    .brand b i {{ font-style: normal; color: {ACCENT}; }}

    h1 {{ margin-top: 26px; font-size: 74px; line-height: 1.06; font-weight: 800;
          color: #fff; letter-spacing: -1px; }}
    h1 em {{ font-style: normal; color: {ACCENT};
             text-shadow: 0 0 34px rgba(232,71,139,.55); }}

    .tags {{ display: flex; gap: 10px; margin-top: 28px; }}
    .tag {{ padding: 8px 17px; border-radius: 999px; font-size: 19px; font-weight: 700; }}
    .tag.a {{ background: {ACCENT}; color: #fff; }}
    .tag.b {{ background: rgba(255,255,255,.12); color: #EDEFF7;
              border: 1px solid rgba(255,255,255,.22); }}
    .tag.c {{ background: rgba(124,92,255,.20); color: #C9BCFF;
              border: 1px solid rgba(124,92,255,.45); }}

    .foot {{ position: absolute; left: 54px; bottom: 34px; font-size: 16px;
             color: rgba(255,255,255,.52); letter-spacing: .2px; }}

    /* 主视觉：两行「对照」特写竖叠 —— 左是截图里那一格，右是匹配到的卡图。
       比整屏截图清楚得多，封面尺寸下也能看清卡面。 */
    .pair {{ position: absolute; width: 448px; border-radius: 15px; overflow: hidden;
             background: #2A3040; border: 1px solid rgba(255,255,255,.20);
             box-shadow: 0 22px 48px rgba(0,0,0,.55); }}
    .pair img {{ width: 100%; display: block; }}
    .pair.p1 {{ right: 40px; top: 152px; transform: rotate(-2.5deg);
                box-shadow: 0 22px 48px rgba(0,0,0,.55), 0 0 0 5px rgba(232,71,139,.22); }}
    .pair.p2 {{ right: 92px; top: 392px; transform: rotate(1.8deg); opacity: .97; }}
    .cap {{ position: absolute; right: 44px; top: 112px; font-size: 16px; font-weight: 700;
            color: #FFD2E6; letter-spacing: .5px; }}
    /* 右下角（时长角标位）留白 —— 所以统计徽章放左下 */
    .stat {{ position: absolute; left: 54px; bottom: 96px; display: flex; gap: 9px; }}
    .stat span {{ padding: 7px 15px; border-radius: 999px; font-size: 17px; font-weight: 700;
                  background: rgba(255,255,255,.10); color: #E7EAF3;
                  border: 1px solid rgba(255,255,255,.20); }}
    .stat span b {{ color: #FF7DB4; }}

    /* B 站时长角标压右下角，那里不放内容 */
    </style></head><body>
    <div class="stage">
      <div class="glow"></div><div class="grid"></div>

      <div class="left">
        <div class="brand"><div class="mark"></div>
          <b>Bestdori<i>Helper</i></b></div>
        <h1>把游戏截图<br><em>变成卡册</em></h1>
        <div class="tags">
          <div class="tag a">自动识别</div>
          <div class="tag b">一键同步</div>
          <div class="tag c">免安装</div>
        </div>
      </div>

      <div class="cap">左 = 你的截图 · 右 = 匹配到的卡面</div>
      <div class="pair p1"><img src="{pair_a}" alt=""></div>
      <div class="pair p2"><img src="{pair_b}" alt=""></div>

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

    # 2 倍图缩到目标尺寸：抗锯齿让文字边缘更干净
    im = Image.open(png).convert("RGB")
    im = im.resize((W, H), Image.LANCZOS)
    im.save(OUT, quality=95)
    png.unlink(missing_ok=True)
    for tmp in ASSETS.glob("_cover_pair_*.png"):
        tmp.unlink(missing_ok=True)
    print(f"✓ {OUT.name}  {im.size[0]}x{im.size[1]}  {OUT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
