"""从口播稿生成 LRC 字幕文件。

给剪辑软件配 TTS 用：一行 = 一句，时间轴按中文语速估算。

断句原则：**按自然停顿断，不按字数硬切**。一句话讲完一个意思就断，
太长（超过约 22 字）才拆成两句。这样 TTS 念出来节奏才对。

时间算法：
  - 中文字算 1 个「音节」，英文字母算 0.55（一个 10 字母的词 ≈ 5.5 音节 ≈ 1.3 秒）
  - 语速取 4.3 音节/秒（比 TTS 默认稍慢，宁可字幕早出也别晚出）
  - 句内间隔 0.35 秒，跨页间隔 1.2 秒（翻页要留气口）
  - 每句最少 1.0 秒，避免短句一闪而过

用法：python scripts/make_lrc.py
产物：BestdoriHelper-使用说明/口播稿.lrc
"""

from __future__ import annotations

import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1] / "BestdoriHelper-使用说明"
OUT = PROJ / "口播稿.lrc"

# ---- 断句（人工断的，不是程序切的）--------------------------------------
# 每页一组，组内每项是一句字幕。
PAGES: list[list[str]] = [
    # 1 封面
    [
        "这个工具叫 BestdoriHelper",
        "帮你把游戏截图，变成 Bestdori 卡册",
    ],
    # 2 目录
    [
        "分两章：怎么用，和怎么同步",
    ],
    # 3 第一章扉页
    [
        "先说怎么用",
    ],
    # 4 三个版本，怎么选
    [
        "三个版本，选一个就行",
        "要同步，选安卓 App",
        "图省事，用网页版",
        "要功能全，装桌面版",
    ],
    # 5 四步走完
    [
        "流程就四步",
        "导入、核对、出清单、再导出",
    ],
    # 6 第一步 导入截图
    [
        "把截图拖进来就行",
        "支持一次选整个文件夹",
        "图片只在本机处理，不上传",
        "切分模式默认自动，不用管",
        "「卡框贴合」记得开着",
    ],
    # 7 第二步 核对与修正
    [
        "左边是截图那一格",
        "右边是匹配到的卡图",
        "两张不一样，就是认错了",
        "点候选卡片，一下就改过来",
        "三张候选都不对？点「都不是？搜卡面」，或者直接输卡号",
        "认不出的，直接忽略这一格",
    ],
    # 8 第三步 卡面清单
    [
        "顶部自动统计",
        "总数、特训后、待确认",
        "重点检查「待确认」就行",
    ],
    # 9 第四步 导出清单
    [
        "四种格式可选",
        "CSV 给表格，Markdown 方便分享",
        "ID 列表给别的工具用",
    ],
    # 10 第二章扉页
    [
        "再说怎么同步",
    ],
    # 11 方式一 安卓 App
    [
        "安卓 App 里四步搞定",
        "登录 Bestdori",
        "选你的云端档案",
        "先生成导入计划，看清再动手",
        "确认后点开始导入",
    ],
    # 12 方式二 导出（服务器选择）
    [
        "网页版走官网导入",
        "先选服务器，默认是国服",
        "点「导出 Bestdori 档案」",
        "复制那段文本",
    ],
    # 13 在官网上导入 —— 官网界面实测过（scripts/annotate_bestdori_web.py 量的坐标）
    [
        "打开 Bestdori 官网的 Profile Manager",
        "第一次会弹设置框，关掉就行",
        "先登录，入口在页面中间的 Cloud Storage 那一段",
        "左边 PROFILE 菜单里点 Import",
        "把文本粘进 Profile Data 框",
        "再点框下方那个 Import 按钮",
        "等级技能填的是满值，不影响卡牌归属",
    ],
    # 14 结尾
    [
        "就这些",
        "网页版打开网址就能用",
        "安卓和 Windows 在 Releases 下载",
    ],
]

CPS = 4.3           # 音节 / 秒
# 间隔调小：原来 0.35/1.2/2.5 句与句之间停顿太明显，观感拖沓。
# 配音本身句末就有自然停顿，这里只补一点点即可。
GAP_IN_PAGE = 0.12  # 同页句间停顿
GAP_BETWEEN = 0.5   # 跨页停顿（翻页气口）
MIN_DUR = 1.0       # 每句最短时长
SECTION_HOLD = 1.0  # 章节扉页额外停留（只有一句，画面要让人看清）


def syllables(text: str) -> float:
    """估算朗读音节数：中文字算 1，英文字母算 0.55，标点算 0.4（当停顿）。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_letters = len(re.findall(r"[A-Za-z0-9]", text))
    punct = len(re.findall(r"[，。、；：？！,.;:?!]", text))
    return cjk + ascii_letters * 0.55 + punct * 0.4


def stamp(sec: float) -> str:
    m, s = divmod(max(0.0, sec), 60)
    return f"[{int(m):02d}:{s:05.2f}]"


def main() -> int:
    lines = [
        "[ti:BestdoriHelper 使用说明]",
        "[ar:口播稿]",
        "[al:BestdoriHelper 使用说明]",
        "[by:scripts/make_lrc.py]",
        "[offset:0]",
        "",
    ]

    t = 0.0
    total = 0
    page_marks: list[tuple[int, float]] = []
    for pi, page in enumerate(PAGES, start=1):
        # 页边界不写进 LRC —— 有些解析器会把无时间戳的行当歌词喂给 TTS。
        # 单独记下来，写到同目录的 .pages.txt 里备查。
        page_marks.append((pi, t))
        for sent in page:
            dur = max(MIN_DUR, syllables(sent) / CPS)
            lines.append(f"{stamp(t)}{sent}")
            t += dur + GAP_IN_PAGE
            total += 1
        # 章节扉页只有一句，念完给画面留点时间，别一闪而过
        if len(page) == 1:
            t += SECTION_HOLD
        t += GAP_BETWEEN - GAP_IN_PAGE   # 换页额外多停一点

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 页 → 起始时间 对照表（供剪辑时对齐幻灯片用）
    marks = PROJ / "口播稿.页时间.txt"
    marks.write_text(
        "每页起始时间（剪辑时用来对齐幻灯片切换）\n"
        + "-" * 34 + "\n"
        + "\n".join(f"第 {p:>2} 页   {stamp(s)}" for p, s in page_marks)
        + "\n",
        encoding="utf-8",
    )

    print(f"已生成 {OUT.name} 和 {marks.name}")
    print(f"  字幕 {total} 行 / {len(PAGES)} 页")
    print(f"  预估总时长 {int(t // 60)} 分 {t % 60:.0f} 秒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
