"""把 13 页幻灯片渲染成 PNG（2560×1440）。

为什么不用 pptx 直接转图：本机没装 LibreOffice，PowerPoint 的 COM 导出在沙箱里
也跑不出文件。所以这里按 DESIGN.md 的规格用 HTML 复刻一遍，再用 Playwright 截图 ——
配色、字号、布局都是我自己定的，复刻准确度有保证，而且以后改版式也方便。

产物：BestdoriHelper-使用说明/render/01.png ... 13.png
用法：python scripts/render_slides.py
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
PROJ = REPO / "BestdoriHelper-使用说明"
ASSETS = PROJ / "assets"
OUT = PROJ / "render"

W, H = 1280, 720
SCALE = 2                      # 2x -> 2560×1440
N_PAGES = 14                   # 总页数 —— 改页数时这里和 make_lrc.PAGES 一起改

# ---- 配色（同 DESIGN.md）-------------------------------------------------
INK = "#1F2430"
BG = "#F7F8FA"
ACCENT = "#E8478B"
DIM = "#5A6376"
FAINT = "#8A93A6"
LINE = "#E4E7EE"
BORDER = "#D5DAE4"

CSS = f"""
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#000; font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif; }}
.slide {{
  width:{W}px; height:{H}px; position:relative; overflow:hidden;
  background:{BG}; color:{INK}; display:flex; flex-direction:column;
  padding:20px 72px; font-size:14px; line-height:1.55;
}}
.slide.dark {{ background:{INK}; padding:0; }}

/* 母版 */
.a {{ height:100px; display:flex; align-items:center; gap:16px; flex:none; }}
.a .bar {{ width:6px; height:38px; background:{ACCENT}; border-radius:3px; }}
.a h1 {{ font-size:36px; font-weight:700; letter-spacing:.2px; }}
.a .sub {{ font-size:19px; color:{FAINT}; font-weight:400; }}
.b {{ flex:1; display:flex; min-height:0; }}
.c {{ height:60px; display:flex; align-items:center; justify-content:space-between;
      font-size:15px; color:{FAINT}; flex:none; }}

/* 卡片 */
.card {{ background:#fff; border-radius:12px; padding:16px 22px;
         box-shadow:0 2px 12px rgba(31,36,48,.06); border-left:4px solid {BORDER}; }}
.card.hi {{ border-left-color:{ACCENT}; }}
.card.tint {{ background:rgba(232,71,139,.07); border-left:4px solid {ACCENT}; box-shadow:none; }}
.card h3 {{ font-size:21px; font-weight:700; }}
.card p {{ font-size:17px; color:{DIM}; line-height:1.55; }}
.darkbar {{ background:linear-gradient(135deg,#1F2430 0%,#2C3444 100%);
            border-radius:12px 0 0 12px; padding:36px 32px;
            display:flex; flex-direction:column; justify-content:space-between; color:#fff; }}
.panel {{ background:#fff; border-radius:0 12px 12px 0; padding:26px 30px;
          display:flex; flex-direction:column; gap:16px;
          box-shadow:0 2px 12px rgba(31,36,48,.06); }}

.shot {{ background:{INK}; border:1px solid #2E3442; border-radius:12px;
         overflow:hidden; box-shadow:0 6px 24px rgba(31,36,48,.18); flex:none; }}
.shot img {{ width:100%; height:100%; object-fit:contain; display:block; }}

.insight {{ background:{INK}; border-radius:12px; padding:16px 26px;
            display:flex; align-items:center; gap:14px; color:#fff; }}
.insight .bar {{ width:5px; height:30px; background:{ACCENT}; border-radius:3px; flex:none; }}
.insight p {{ font-size:19px; line-height:1.5; }}
"""


def shot(name: str, w: int, h: int, style: str = "") -> str:
    # HTML 落在 render/ 下，图片在 ../assets/ —— 路径要相对 HTML 文件
    return (f'<div class="shot" style="width:{w}px;height:{h}px;{style}">'
            f'<img src="../assets/{name}"></div>')


def slide(n: int, inner: str, dark: bool = False) -> str:
    cls = "slide dark" if dark else "slide"
    return f'<section class="{cls}" data-n="{n:02d}">{inner}</section>'


def footer(n: int) -> str:
    return (f'<div class="c"><span>BestdoriHelper 使用说明</span>'
            f'<span>{n:02d} / {N_PAGES:02d}</span></div>')


# ---- 深色页的背景 SVG ------------------------------------------------------
def svg_cover() -> str:
    return """<svg width="1280" height="720" viewBox="0 0 1280 720"
        style="position:absolute;inset:0">
      <defs>
        <linearGradient id="cbg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#1F2430"/><stop offset="55%" stop-color="#252B39"/>
          <stop offset="100%" stop-color="#2C3444"/></linearGradient>
        <radialGradient id="cglo" cx="0.78" cy="0.28" r="0.62">
          <stop offset="0%" stop-color="#E8478B" stop-opacity="0.42"/>
          <stop offset="55%" stop-color="#E8478B" stop-opacity="0.10"/>
          <stop offset="100%" stop-color="#E8478B" stop-opacity="0"/></radialGradient>
      </defs>
      <rect width="1280" height="720" fill="url(#cbg)"/>
      <rect width="1280" height="720" fill="url(#cglo)"/>
      <g stroke="#fff" stroke-opacity="0.05" stroke-width="1">
        <line x1="0" y1="180" x2="1280" y2="180"/><line x1="0" y1="360" x2="1280" y2="360"/>
        <line x1="0" y1="540" x2="1280" y2="540"/><line x1="320" y1="0" x2="320" y2="720"/>
        <line x1="640" y1="0" x2="640" y2="720"/><line x1="960" y1="0" x2="960" y2="720"/>
      </g>
      <g>
        <rect x="842" y="196" width="112" height="150" rx="10" fill="#E8478B" fill-opacity="0.55"/>
        <rect x="972" y="150" width="112" height="150" rx="10" fill="#fff" fill-opacity="0.10"/>
        <rect x="842" y="366" width="112" height="150" rx="10" fill="#fff" fill-opacity="0.06"/>
        <rect x="972" y="320" width="112" height="150" rx="10" fill="#E8478B" fill-opacity="0.22"/>
      </g>
      <circle cx="120" cy="690" r="210" fill="none" stroke="#E8478B" stroke-opacity="0.16" stroke-width="1.5"/>
      <circle cx="120" cy="690" r="290" fill="none" stroke="#fff" stroke-opacity="0.05" stroke-width="1"/>
    </svg>"""


def svg_section1() -> str:
    return """<svg width="1280" height="720" viewBox="0 0 1280 720"
        style="position:absolute;inset:0">
      <defs>
        <linearGradient id="s1b" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#1B1F2A"/><stop offset="100%" stop-color="#2A3242"/></linearGradient>
        <radialGradient id="s1g" cx="0.22" cy="0.72" r="0.66">
          <stop offset="0%" stop-color="#E8478B" stop-opacity="0.30"/>
          <stop offset="100%" stop-color="#E8478B" stop-opacity="0"/></radialGradient>
      </defs>
      <rect width="1280" height="720" fill="url(#s1b)"/>
      <rect width="1280" height="720" fill="url(#s1g)"/>
      <g stroke="#fff" stroke-opacity="0.045" stroke-width="1">
        <line x1="900" y1="0" x2="900" y2="720"/><line x1="996" y1="0" x2="996" y2="720"/>
        <line x1="1092" y1="0" x2="1092" y2="720"/><line x1="1188" y1="0" x2="1188" y2="720"/>
        <line x1="900" y1="144" x2="1280" y2="144"/><line x1="900" y1="288" x2="1280" y2="288"/>
        <line x1="900" y1="432" x2="1280" y2="432"/><line x1="900" y1="576" x2="1280" y2="576"/>
      </g>
      <rect x="900" y="144" width="96" height="144" fill="#E8478B" fill-opacity="0.20"/>
      <rect x="1092" y="432" width="96" height="144" fill="#E8478B" fill-opacity="0.12"/>
    </svg>"""


def svg_section2() -> str:
    return """<svg width="1280" height="720" viewBox="0 0 1280 720"
        style="position:absolute;inset:0">
      <defs>
        <linearGradient id="s2b" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#1B1F2A"/><stop offset="100%" stop-color="#2A3242"/></linearGradient>
        <radialGradient id="s2g" cx="0.78" cy="0.62" r="0.68">
          <stop offset="0%" stop-color="#E8478B" stop-opacity="0.32"/>
          <stop offset="100%" stop-color="#E8478B" stop-opacity="0"/></radialGradient>
      </defs>
      <rect width="1280" height="720" fill="url(#s2b)"/>
      <rect width="1280" height="720" fill="url(#s2g)"/>
      <rect x="905" y="196" width="86" height="112" rx="8" fill="#fff" fill-opacity="0.07"/>
      <rect x="1011" y="196" width="86" height="112" rx="8" fill="#fff" fill-opacity="0.05"/>
      <rect x="905" y="328" width="86" height="112" rx="8" fill="#fff" fill-opacity="0.04"/>
      <rect x="1011" y="328" width="86" height="112" rx="8" fill="#E8478B" fill-opacity="0.22"/>
      <path d="M1054 384 L1120 384" stroke="#E8478B" stroke-opacity="0.55" stroke-width="3" stroke-linecap="round"/>
      <path d="M1108 372 L1122 384 L1108 396" fill="none" stroke="#E8478B"
            stroke-opacity="0.55" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M1148 402 a34 34 0 0 1 8 -67 a44 44 0 0 1 82 -4 a30 30 0 0 1 4 71 z"
            fill="#fff" fill-opacity="0.10" stroke="#E8478B" stroke-opacity="0.4" stroke-width="2"/>
      <circle cx="1160" cy="690" r="180" fill="none" stroke="#fff" stroke-opacity="0.04" stroke-width="1"/>
    </svg>"""


def svg_ending() -> str:
    return """<svg width="1280" height="720" viewBox="0 0 1280 720"
        style="position:absolute;inset:0">
      <defs>
        <linearGradient id="eb" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#1B1F2A"/><stop offset="52%" stop-color="#272E3D"/>
          <stop offset="100%" stop-color="#2F3849"/></linearGradient>
        <radialGradient id="eg" cx="0.74" cy="0.42" r="0.70">
          <stop offset="0%" stop-color="#E8478B" stop-opacity="0.40"/>
          <stop offset="58%" stop-color="#E8478B" stop-opacity="0.10"/>
          <stop offset="100%" stop-color="#E8478B" stop-opacity="0"/></radialGradient>
      </defs>
      <rect width="1280" height="720" fill="url(#eb)"/>
      <rect width="1280" height="720" fill="url(#eg)"/>
      <rect x="856" y="250" width="100" height="136" rx="10" fill="#fff" fill-opacity="0.06"/>
      <rect x="972" y="250" width="100" height="136" rx="10" fill="#fff" fill-opacity="0.05"/>
      <rect x="1088" y="250" width="100" height="136" rx="10" fill="#E8478B" fill-opacity="0.55"/>
      <path d="M1118 316 l20 20 l40 -42" fill="none" stroke="#fff" stroke-opacity="0.9"
            stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="1240" cy="60" r="240" fill="none" stroke="#fff" stroke-opacity="0.04" stroke-width="1"/>
      <circle cx="60" cy="660" r="170" fill="none" stroke="#E8478B" stroke-opacity="0.12" stroke-width="1.5"/>
    </svg>"""


# =========================================================================
def build() -> str:
    S: list[str] = []

    # ---- 01 封面 ----
    S.append(slide(1, svg_cover() + """
      <div style="position:relative;height:100%;display:flex;flex-direction:column;
                  justify-content:center;padding:0 96px;gap:26px">
        <div style="display:flex;align-items:center;gap:12px">
          <div style="width:44px;height:4px;background:#E8478B;border-radius:2px"></div>
          <span style="font-size:20px;color:#E8478B;font-weight:700;letter-spacing:3px">使用说明</span>
        </div>
        <div style="font-size:76px;font-weight:700;color:#fff;line-height:1.15;letter-spacing:1px">
          Bestdori<span style="color:#E8478B">Helper</span></div>
        <div style="font-size:30px;color:#C9CEDA;line-height:1.5">把游戏截图变成 Bestdori 卡册</div>
        <div style="display:flex;align-items:center;gap:14px;margin-top:14px">
          <span style="font-size:22px;color:#8A93A6">导入截图</span>
          <span style="font-size:22px;color:#E8478B">→</span>
          <span style="font-size:22px;color:#8A93A6">自动识别卡面</span>
          <span style="font-size:22px;color:#E8478B">→</span>
          <span style="font-size:22px;color:#8A93A6">同步到「我的卡牌」</span>
        </div>
      </div>
      <div style="position:absolute;left:96px;bottom:46px;font-size:15px;color:#6B7280">
        《BanG Dream! 少女乐团派对》卡面管理工具 · 网页 / 安卓 / Windows 三端通用</div>
    """, dark=True))

    # ---- 02 目录 ----
    S.append(slide(2, f"""
      <div class="a"><h1>这份说明讲什么</h1></div>
      <div class="b" style="gap:0">
        <div class="darkbar" style="width:366px">
          <div style="display:flex;flex-direction:column;gap:16px">
            <div style="width:44px;height:4px;background:{ACCENT};border-radius:2px"></div>
            <div style="font-size:40px;font-weight:700;line-height:1.25">目录</div>
            <div style="font-size:19px;color:{FAINT};line-height:1.6">两个章节，从「怎么用」讲到「怎么同步」</div>
          </div>
          <div style="font-size:16px;color:#5A6376">全程 13 页 · 约 6 分钟</div>
        </div>
        <div class="panel" style="flex:1;justify-content:center;gap:30px;padding:40px 44px">
          <div style="display:flex;gap:24px;align-items:flex-start">
            <div style="font-size:46px;font-weight:700;color:{ACCENT};line-height:1.05;width:82px">01</div>
            <div style="flex:1;display:flex;flex-direction:column;gap:8px">
              <div style="font-size:28px;font-weight:700">开始使用</div>
              <div style="font-size:21px;color:{DIM};line-height:1.6">
                三个版本怎么选 · 四步走完整个流程<br>导入截图 · 核对修正 · 卡面清单 · 导出</div>
              <div style="font-size:16px;color:{FAINT}">第 03 – 09 页</div>
            </div>
          </div>
          <div style="height:1px;background:{LINE}"></div>
          <div style="display:flex;gap:24px;align-items:flex-start">
            <div style="font-size:46px;font-weight:700;color:{ACCENT};line-height:1.05;width:82px">02</div>
            <div style="flex:1;display:flex;flex-direction:column;gap:8px">
              <div style="font-size:28px;font-weight:700">同步到 Bestdori</div>
              <div style="font-size:21px;color:{DIM};line-height:1.6">
                把清单写进「我的卡牌」<br>安卓 App 一键同步 · 网页版导出档案导入</div>
              <div style="font-size:16px;color:{FAINT}">第 10 – 12 页</div>
            </div>
          </div>
        </div>
      </div>
    """ + footer(2)))

    # ---- 03 第一章扉页 ----
    S.append(slide(3, svg_section1() + """
      <div style="position:relative;height:100%;display:flex;flex-direction:column;
                  justify-content:center;padding:0 96px;gap:20px">
        <div style="font-size:150px;font-weight:700;color:#E8478B;line-height:1;letter-spacing:2px">01</div>
        <div style="font-size:64px;font-weight:700;color:#fff;line-height:1.15">开始使用</div>
        <div style="width:68px;height:4px;background:#E8478B;border-radius:2px;margin-top:6px"></div>
        <div style="font-size:22px;color:#8A93A6;margin-top:6px">从选版本到导出清单，四步走完</div>
      </div>
    """, dark=True))

    # ---- 04 三个版本怎么选 ----
    S.append(slide(4, f"""
      <div class="a"><div class="bar"></div><h1>三个版本，怎么选</h1></div>
      <div class="b" style="gap:34px">
        <div style="width:715px;display:flex;flex-direction:column;gap:14px">
          <div class="card">
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:5px">
              <span style="font-size:25px;font-weight:700">网页版</span>
              <span style="font-size:15px;color:{FAINT}">免安装</span></div>
            <p>打开网址就能用，适合快速识别完导出清单。</p>
          </div>
          <div class="card tint">
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:5px">
              <span style="font-size:25px;font-weight:700">安卓 App</span>
              <span style="font-size:15px;color:{ACCENT};font-weight:700">想同步就选它</span></div>
            <p>手机上直接把清单写进 Bestdori 的「我的卡牌」，一键完成。</p>
          </div>
          <div class="card">
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:5px">
              <span style="font-size:25px;font-weight:700">Windows 桌面版</span>
              <span style="font-size:15px;color:{FAINT}">功能最全</span></div>
            <p>在电脑上用，界面最完整，适合批量整理。</p>
          </div>
          <div class="insight" style="margin-top:auto">
            <div class="bar"></div>
            <p>先想清楚要不要同步：要同步选安卓，图省事选网页，要功能全选桌面</p>
          </div>
        </div>
        <div class="panel" style="flex:1;align-items:center;justify-content:center;gap:16px;padding:22px 18px">
          <svg width="330" height="330" viewBox="0 0 330 330">
            <rect x="26" y="34" width="150" height="104" rx="8" fill="#F2F4F8" stroke="#C8CEDA" stroke-width="2"/>
            <rect x="26" y="34" width="150" height="20" rx="8" fill="#C8CEDA"/>
            <circle cx="41" cy="44" r="3" fill="#8A93A6"/><circle cx="52" cy="44" r="3" fill="#8A93A6"/>
            <circle cx="63" cy="44" r="3" fill="#8A93A6"/>
            <rect x="40" y="68" width="52" height="46" rx="4" fill="#D5DAE4"/>
            <rect x="102" y="68" width="52" height="46" rx="4" fill="#D5DAE4"/>
            <text x="101" y="164" font-size="15" fill="#5A6376" text-anchor="middle">网页</text>
            <rect x="200" y="26" width="98" height="170" rx="14" fill="#fff" stroke="#E8478B" stroke-width="3"/>
            <rect x="212" y="44" width="74" height="126" rx="6" fill="#1F2430"/>
            <rect x="222" y="56" width="54" height="30" rx="3" fill="#E8478B" fill-opacity="0.55"/>
            <rect x="222" y="94" width="54" height="30" rx="3" fill="#3A4354"/>
            <rect x="222" y="132" width="54" height="26" rx="3" fill="#3A4354"/>
            <rect x="236" y="182" width="26" height="7" rx="3.5" fill="#C8CEDA"/>
            <text x="249" y="222" font-size="15" fill="#E8478B" font-weight="bold" text-anchor="middle">安卓</text>
            <rect x="52" y="240" width="196" height="62" rx="8" fill="#F2F4F8" stroke="#C8CEDA" stroke-width="2"/>
            <rect x="66" y="256" width="74" height="30" rx="4" fill="#D5DAE4"/>
            <rect x="150" y="256" width="84" height="30" rx="4" fill="#D5DAE4"/>
            <text x="150" y="322" font-size="15" fill="#5A6376" text-anchor="middle">Windows</text>
          </svg>
          <div style="font-size:15px;color:{FAINT};text-align:center;line-height:1.5">
            三个平台共用同一套识别<br>结果和清单格式，界面不同而已</div>
        </div>
      </div>
    """ + footer(4)))

    # ---- 05 四步走完 ----
    steps = [("1", "导入截图", "把游戏里截的卡面列表图丢进来"),
             ("2", "核对修正", "扫一眼对照图，认错的手动改过来"),
             ("3", "卡面清单", "自动汇总统计，确认后就是你的卡册"),
             ("4", "导出 / 同步", "存成文件，或者直接写进 Bestdori")]
    rows = "".join(f"""
      <div class="card hi" style="padding:17px 26px;display:flex;align-items:center;gap:20px">
        <span style="font-size:30px;font-weight:700;color:{ACCENT};width:46px">{n}</span>
        <span style="font-size:25px;font-weight:700;width:168px">{t}</span>
        <span style="font-size:19px;color:{DIM}">{d}</span>
      </div>""" for n, t, d in steps)
    S.append(slide(5, f"""
      <div class="a"><div class="bar"></div><h1>四步走完</h1></div>
      <div class="b" style="gap:40px;align-items:center">
        <div style="width:330px;display:flex;flex-direction:column;gap:10px">
          <div style="font-size:240px;font-weight:700;color:{ACCENT};line-height:.92;letter-spacing:-6px">4</div>
          <div style="font-size:40px;font-weight:700;line-height:1.2">步走完全部流程</div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;gap:18px">
          <div style="display:flex;flex-direction:column;gap:12px">{rows}</div>
          <div class="insight">
            <div class="bar" style="height:32px"></div>
            <p style="font-size:20px">后面四页一步一步讲 —— 每一步你真正要做的，通常不超过两次点击</p>
          </div>
        </div>
      </div>
    """ + footer(5)))

    # ---- 06 第一步 ----
    S.append(slide(6, f"""
      <div class="a"><div class="bar"></div><h1>第一步 · 导入截图</h1></div>
      <div class="b" style="gap:32px;align-items:center">
        <div style="display:flex;flex-direction:column;gap:14px">
          {shot("step1_import.png", 660, 274)}
          <div class="card hi" style="padding:15px 22px;display:flex;flex-direction:column;gap:6px">
            <div style="font-size:18px;font-weight:700">截什么样的图</div>
            <div style="font-size:15px;color:{DIM};line-height:1.6">
              就是游戏里的「成员一览」列表页。整屏截下来即可，
              横竖都行，一次可以放很多张。</div>
          </div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;gap:16px">
          <div class="card hi">
            <h3>拖进来，或者点一下选</h3>
            <p>可以一次选多张，也能直接选整个文件夹。<br>图片只在你本机处理，不会上传。</p>
          </div>
          <div class="card">
            <h3>切分模式用「自动」</h3>
            <p>它会自己数出行数和列数。切得不准时再改成<br>「指定行列」，手动填行、列。</p>
          </div>
          <div class="card tint">
            <h3>别忘了「卡框贴合」</h3>
            <p>默认开着。它让每一格收紧到真正的卡框上，<br>不会把旁边卡面的边切进来 —— 直接决定准不准。</p>
          </div>
        </div>
      </div>
    """ + footer(6)))

    # ---- 07 第二步 ----
    # 两张图并排（对照 + 原图切分框），横向占满，不再是一张小图孤零零居中
    S.append(slide(7, f"""
      <div class="a"><div class="bar"></div><h1>第二步 · 核对与修正</h1>
        <span class="sub">这步决定最终准不准</span></div>
      <div class="b" style="flex-direction:column;gap:14px">
        <div style="display:flex;gap:20px;justify-content:center">
          {shot("step2_compare.png", 400, 400)}
          {shot("result_preview.png", 584, 400)}
        </div>
        <div style="flex:1;display:flex;gap:14px">
          <div class="card hi" style="flex:1">
            <h3 style="font-size:20px">左右并排，一眼看对不对</h3>
            <p style="font-size:16px">每行左边是截图里那一格，右边是匹配到的卡图。两张长得不一样就是认错了。</p>
          </div>
          <div class="card" style="flex:1">
            <h3 style="font-size:20px">都不是？搜卡面或输卡号</h3>
            <p style="font-size:16px">点候选卡片直接换；三张都不是，搜全卡池或直接输卡号。</p>
          </div>
          <div class="card" style="flex:1">
            <h3 style="font-size:20px">拿不准就忽略这一格</h3>
            <p style="font-size:16px">点缩略图能看大图。确实认不出来的格子直接忽略，不会污染清单。</p>
          </div>
        </div>
      </div>
    """ + footer(7)))

    # ---- 08 第三步 ----
    S.append(slide(8, f"""
      <div class="a"><div class="bar"></div><h1>第三步 · 卡面清单</h1></div>
      <div class="b" style="gap:0">
        <div class="darkbar" style="width:360px">
          <div style="display:flex;flex-direction:column;gap:14px">
            <div style="font-size:72px;font-weight:700;color:{ACCENT};line-height:1">3</div>
            <div style="font-size:30px;font-weight:700;line-height:1.25">清单就是<br>你的卡册</div>
            <div style="width:44px;height:4px;background:{ACCENT};border-radius:2px"></div>
            <div style="font-size:18px;color:{FAINT};line-height:1.6">
              确认过的卡会自动汇总，<br>下一步就能导出或同步</div>
          </div>
          <div style="font-size:15px;color:#5A6376">网页版 / 手机 / 桌面 都有</div>
        </div>
        <div class="panel" style="flex:1;gap:16px">
          {shot("step3_inventory.png", 700, 141)}
          {shot("inventory_table.png", 700, 210)}
          <div style="display:flex;flex-direction:column;gap:9px">
            <div style="display:flex;gap:12px"><span style="color:{ACCENT};font-weight:700;width:20px">·</span>
              <span style="font-size:18px;color:{DIM}">顶部自动统计：总数、特训后、4★5★、待确认</span></div>
            <div style="display:flex;gap:12px"><span style="color:{ACCENT};font-weight:700;width:20px">·</span>
              <span style="font-size:18px;color:{DIM}">按卡名 / 角色搜索，也能一键筛出「待确认」</span></div>
            <div style="display:flex;gap:12px"><span style="color:{ACCENT};font-weight:700;width:20px">·</span>
              <span style="font-size:18px;color:{DIM}">
                <b style="color:{INK}">拿不准的会被标成「待确认」</b> —— 重点检查这些就行</span></div>
          </div>
        </div>
      </div>
    """ + footer(8)))

    # ---- 09 第四步 ----
    fmts = [("CSV", "给 Excel / 表格软件看，想再加工就用它", True),
            ("ID 列表", "一串卡号，方便扔给别的工具", False),
            ("Markdown", "排版好的表格，直接贴到群里分享", False),
            ("JSON", "完整数据，给会写脚本的人用", False)]
    # 2×2 排（不是单列），上面卡片、下面把导出区横幅铺满整宽 —— 避免右栏一大片空白
    frows = "".join(f"""
      <div class="card{(' hi' if hi else '')}" style="padding:14px 22px;display:flex;align-items:center;
                  gap:18px;width:calc(50% - 7px)">
        <span style="font-size:22px;font-weight:700;width:112px;flex:none">{n}</span>
        <span style="font-size:16px;color:{DIM}">{d}</span>
      </div>""" for n, d, hi in fmts)
    S.append(slide(9, f"""
      <div class="a"><div class="bar"></div><h1>第四步 · 导出清单</h1></div>
      <div class="b" style="flex-direction:column;gap:16px">
        <div style="display:flex;flex-wrap:wrap;gap:14px">{frows}</div>
        {shot("step4_export.png", 1136, 218)}
        <div class="insight" style="margin-top:auto">
          <div class="bar"></div>
          <p>导出只是中途站 —— 想写进 Bestdori 的，看下一章</p>
        </div>
      </div>
    """ + footer(9)))

    # ---- 10 第二章扉页 ----
    S.append(slide(10, svg_section2() + """
      <div style="position:relative;height:100%;display:flex;flex-direction:column;
                  justify-content:center;padding:0 96px;gap:20px">
        <div style="font-size:150px;font-weight:700;color:#E8478B;line-height:1;letter-spacing:2px">02</div>
        <div style="font-size:64px;font-weight:700;color:#fff;line-height:1.15">同步到 Bestdori</div>
        <div style="width:68px;height:4px;background:#E8478B;border-radius:2px;margin-top:6px"></div>
        <div style="font-size:22px;color:#8A93A6;margin-top:6px">两种方式，按你用的版本选</div>
      </div>
    """, dark=True))

    # ---- 11 方式一 安卓 ----
    asteps = [("1", "登录 Bestdori", "填账号密码，会话存在手机上，下次不用再登"),
              ("2", "选一份云端档案", "就是你 Bestdori 上那份「我的卡牌」"),
              ("3", "先「生成导入计划」", "告诉你新增多少、升级多少、跳过多少 —— 先看清再动手"),
              ("4", "点「开始导入」", "会弹确认框，不会不打招呼就改你的卡册")]
    arows = "".join(f"""
      <div class="card{(' tint' if i == 3 else ' hi' if i < 3 else '')}"
           style="padding:14px 22px;display:flex;align-items:center;gap:18px;width:calc(50% - 7px)">
        <span style="font-size:26px;font-weight:700;color:{ACCENT};width:38px;flex:none">{n}</span>
        <div style="flex:1"><div style="font-size:19px;font-weight:700">{t}</div>
          <div style="font-size:15px;color:{DIM}">{d}</div></div>
      </div>""" for i, (n, t, d) in enumerate(asteps))
    S.append(slide(11, f"""
      <div class="a"><div class="bar"></div><h1>方式一 · 安卓 App 一键同步</h1>
        <span style="background:rgba(232,71,139,.12);border-radius:20px;padding:5px 14px;
                     font-size:16px;color:{ACCENT};font-weight:700">最省事</span></div>
      <div class="b" style="flex-direction:column;gap:16px">
        <div style="display:flex;flex-wrap:wrap;gap:14px">{arows}</div>
        <div style="display:flex;justify-content:center">
          {shot("sync_app.png", 900, 308)}
        </div>
        <div class="insight" style="margin-top:auto;padding:13px 20px">
          <div class="bar" style="height:26px"></div>
          <p style="font-size:16px">只有 App 能直连 Bestdori，网页做不到</p>
        </div>
      </div>
    """ + footer(11)))

    # ---- 12 方式二 网页版 ----
    wsteps = [("①", "选服务器", "国服默认，日服等要改"),
              ("②", "导出", "清单页点「导出 Bestdori 档案」"),
              ("③", "复制", "点弹层里的「复制」按钮")]
    wchips = "".join(f"""
      <div style="flex:1;background:{BG};border-radius:10px;padding:13px 14px;
                  border-top:3px solid {ACCENT}">
        <div style="font-size:15px;color:{ACCENT};font-weight:700">{n}</div>
        <div style="font-size:17px;font-weight:700;margin:4px 0">{t}</div>
        <div style="font-size:14px;color:{DIM};line-height:1.45">{d}</div>
      </div>""" for n, t, d in wsteps)
    S.append(slide(12, f"""
      <div class="a"><div class="bar"></div><h1>方式二 · 导出档案</h1>
        <span class="sub">下一步拿到官网导入</span></div>
      <div class="b" style="gap:0">
        <div class="darkbar" style="width:360px">
          <div style="display:flex;flex-direction:column;gap:14px">
            <div style="font-size:72px;font-weight:700;color:{ACCENT};line-height:1">2</div>
            <div style="font-size:29px;font-weight:700;line-height:1.25">借用官网<br>自己的导入入口</div>
            <div style="width:44px;height:4px;background:{ACCENT};border-radius:2px"></div>
            <div style="font-size:18px;color:{FAINT};line-height:1.6">
              网页版没法直连 Bestdori，<br>但官网本来就支持粘贴导入</div>
          </div>
          <div style="background:rgba(255,255,255,.06);border-radius:10px;padding:13px 16px;
                      font-size:16px;color:#C9CEDA;line-height:1.55">
            账号密码不经过任何第三方</div>
        </div>
        <div class="panel" style="flex:1;gap:16px;padding:24px 30px">
          <div style="display:flex;gap:10px">{wchips}</div>
          {shot("profile_dialog.png", 660, 330)}
          <div style="display:flex;align-items:center;gap:10px;padding:10px 16px;
                      background:rgba(232,71,139,.07);border-radius:10px">
            <div style="width:4px;height:26px;background:{ACCENT};border-radius:2px;flex:none"></div>
            <div style="font-size:15px;color:{DIM};line-height:1.5">
              等级 / 技能 / 剧情数没法从截图判断，填的是满值 ——
              <b style="color:{INK}">只影响算分，不影响卡牌归属</b>，要精确在官网改一下</div>
          </div>
        </div>
      </div>
    """ + footer(12)))

    # ---- 13 在官网上导入（真实官网界面 + 实测坐标标注）----
    # 坐标不是目测的：scripts/annotate_bestdori_web.py 读 DOM 量出来的。
    # ⚠️ 官网右上角**没有**登录按钮 —— 登录链接在页面中部 Cloud Storage 段，
    #    这点实测推翻了我最初写错的「右上角 Log In」。
    wweb = [
        ("①", "先登录", "页面中部 Cloud Storage 那段文字里的 Log In"),
        ("②", "点侧栏 Import", "左栏 PROFILE 组里，Cloud Storage 下一行"),
        ("③", "粘贴档案", "粘进 Profile Data 文本框"),
        ("④", "点 Import", "文本框左下角正下方那个按钮"),
    ]
    wwebrows = "".join(f"""
      <div style="display:flex;gap:12px;align-items:flex-start">
        <div style="width:30px;height:30px;border-radius:8px;background:{ACCENT};color:#fff;
                    font-size:16px;font-weight:700;display:flex;align-items:center;
                    justify-content:center;flex:none">{n}</div>
        <div style="min-width:0">
          <div style="font-size:18px;font-weight:700;line-height:1.25">{t}</div>
          <div style="font-size:14px;color:{DIM};line-height:1.4;margin-top:2px">{d}</div>
        </div>
      </div>""" for n, t, d in wweb)
    S.append(slide(13, f"""
      <div class="a"><div class="bar"></div><h1>在官网上导入</h1>
        <span class="sub">bestdori.com/profile/manager</span></div>
      <div class="b" style="gap:26px;align-items:center">
        {shot("web_import_annotated.png", 700, 438)}
        <div style="flex:1;display:flex;flex-direction:column;gap:15px">
          {wwebrows}
          <div style="display:flex;align-items:flex-start;gap:10px;padding:11px 15px;
                      background:rgba(232,71,139,.07);border-radius:10px;margin-top:2px">
            <div style="width:4px;height:44px;background:{ACCENT};border-radius:2px;flex:none"></div>
            <div style="font-size:14px;color:{DIM};line-height:1.5">
              <b style="color:{INK}">登录入口不在右上角</b>，在页面中部。
              第一次进会弹「Welcome」设置框，关掉就行。
              等级 / 技能填的是满值，<b style="color:{INK}">只影响算分，不影响卡牌归属</b>。</div>
          </div>
        </div>
      </div>
    """ + footer(14)))

    # ---- 14 结尾 ----
    S.append(slide(14, svg_ending() + """
      <div style="position:relative;height:100%;display:flex;flex-direction:column;
                  justify-content:center;padding:0 96px;gap:24px">
        <div style="display:flex;align-items:center;gap:12px">
          <div style="width:44px;height:4px;background:#E8478B;border-radius:2px"></div>
          <span style="font-size:20px;color:#E8478B;font-weight:700;letter-spacing:3px">就这些</span>
        </div>
        <div style="font-size:72px;font-weight:700;color:#fff;line-height:1.15">现在就去试</div>
        <div style="font-size:22px;color:#C9CEDA;line-height:1.6">截一张卡面列表图丢进去，剩下的交给它</div>
        <div style="width:68px;height:4px;background:#E8478B;border-radius:2px;margin-top:4px"></div>
        <div style="display:flex;gap:40px;margin-top:10px">
          <div><div style="font-size:15px;color:#8A93A6">网页版（免安装）</div>
            <div style="font-size:19px;color:#fff;font-weight:700">frankiesondesu.github.io/BestdoriHelper</div></div>
          <div><div style="font-size:15px;color:#8A93A6">安卓 / Windows</div>
            <div style="font-size:19px;color:#fff;font-weight:700">仓库 Releases 里下载</div></div>
        </div>
      </div>
      <div style="position:absolute;left:96px;bottom:44px;font-size:15px;color:#6B7280">
        图片只在本机处理，不上传 · 账号密码只发往 bestdori.com</div>
    """, dark=True))

    return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<style>{CSS}</style></head><body>' + "".join(S) + "</body></html>")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    html = OUT / "slides.html"
    html.write_text(build(), encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=SCALE)
        pg = ctx.new_page()
        pg.goto(html.as_uri(), wait_until="load")
        # 等图片全部就绪
        pg.wait_for_function(
            "() => [...document.images].every(i => i.complete)", timeout=60000)
        pg.wait_for_timeout(600)

        n = pg.eval_on_selector_all("section.slide", "els => els.length")
        if n != N_PAGES:
            print(f"  ! 实际 {n} 页，N_PAGES={N_PAGES} —— 页脚会写错，请对齐",
                  flush=True)
        for i in range(n):
            el = pg.query_selector_all("section.slide")[i]
            out = OUT / f"{i + 1:02d}.png"
            el.screenshot(path=str(out))
            print(f"  ✓ {out.name}", flush=True)
        browser.close()

    print(f"\n渲染完成 {n} 页 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
