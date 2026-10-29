"""从游戏「商店 / 道具」截图里读区域道具等级。

游戏界面每张道具卡的固定布局：

    等级 7  8/8        <- 等级徽章 + 进度
    椰子树             <- 道具名
    ¥1200000

所以识别策略是：**先用 OCR 找出所有「等级X」，再到它右下方找最近的道具名**。
这比"按坐标硬切"稳 —— 分辨率和排版稍有变化也不会错位。

道具名到槽位的对应靠关键词表（游戏里道具名是固定文案）。

产物：`docs/data/my-items.json`（供网页端「导入道具截图」使用）

用法::

    python scripts/read_item_shots.py              # 读 Photo/item/ 下全部
    python scripts/read_item_shots.py --dir <目录>
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "data" / "my-items.json"

#: 道具名怎么归到 Bestdori 的 items 槽位。
#:
#: 游戏里的命名规律是「**{乐队}的{乐器/海报/宣传单}**」：
#:   爱音的吉他 / 大家的贝斯 / 沙绫的爵士鼓 / 大家的键盘   -> 该乐队的道具
#:   Popipa的海报 / Afterglow的宣传单                      -> 该乐队的道具
#:   CiRCLE的海报 / CiRCLE的宣传单                          -> Everyone（全队）
#:   椰子树 / 足浴池 / 喷泉 / 盆栽套装                       -> Plaza（广场）
#:   肉酱意大利面 / 紫薯冰霜 / 极上咖啡                      -> Menu（咖啡厅）
#:   电影杂志 / 音乐杂志 / 时尚杂志                          -> Magazine
#:
#: 所以**先认乐队名，再认道具类型** —— 只按"贝斯"这种类型去匹配会把
#: 所有乐队的贝斯混成一个（第一版就踩了这个坑）。
BAND_ALIASES: list[tuple[str, str]] = [
    ("popipa", "PoppinParty"), ("poppin", "PoppinParty"),
    ("afterglow", "Afterglow"),
    ("paspare", "PastelPalettes"), ("pastel", "PastelPalettes"),
    ("roselia", "Roselia"),
    ("hello happy", "HelloHappyWorld"), ("hhw", "HelloHappyWorld"),
    ("monica", "Morfonica"), ("morfonica", "Morfonica"),
    ("ras", "RaiseASuilen"), ("raise", "RaiseASuilen"),
    ("mygo", "MyGO"),
]

#: 不属于任何乐队的道具类型
ITEM_KINDS = ("吉他", "贝斯", "爵士鼓", "鼓", "键盘", "麦克风", "海报", "宣传单", "传单")

MENU_KEYWORDS = (
    "意大利面", "冰霜", "马卡龙", "水果挞", "海螺包", "咖啡", "蛋糕", "芭菲",
    "圣代", "松饼", "布丁", "曲奇", "意面", "沙拉", "寿司", "饭团",
)
PLAZA_KEYWORDS = ("椰子树", "足浴池", "铜像", "喷泉", "盆栽", "长椅", "路灯", "花坛", "秋千", "钟楼")
MAGAZINE_KEYWORDS = ("电影杂志", "音乐杂志", "时尚杂志", "杂志")


def match_slot(name: str) -> str | None:
    n = name.lower()

    # 1) 杂志优先（"杂志"是更长的专有名词，避免被别的规则抢走）
    if any(k in name for k in MAGAZINE_KEYWORDS):
        return "Magazine"

    # 2) 带乐队名的道具：{乐队}的{乐器/海报/宣传单}
    if any(k in name for k in ITEM_KINDS):
        for alias, slot in BAND_ALIASES:
            if alias in n:
                return slot
        # CiRCLE 的是全队通用
        if "circle" in n or "サークル" in n:
            return "Everyone"

    # 3) 咖啡厅（属性道具）
    if any(k in name for k in MENU_KEYWORDS):
        return "Menu"

    # 4) 广场
    if any(k in name for k in PLAZA_KEYWORDS):
        return "Plaza"

    return None


LEVEL_RE = re.compile(r"等级\s*(\d+)")
PROGRESS_RE = re.compile(r"(\d+)\s*/\s*(\d+)")




def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="Photo/item")
    args = ap.parse_args()

    src = REPO / args.dir
    files = sorted(p for p in src.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    if not files:
        print(f"✗ {src} 下没有图片")
        return 1

    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()
    found: dict[str, dict] = {}
    unmatched: list[str] = []

    for f in files:
        res, _ = ocr(str(f))
        if not res:
            continue
        # 只保留有内容的块，按 y 再按 x 排序
        blocks = [(int(b[0][0]), int(b[0][1]), t) for b, t, _ in res if t.strip()]
        blocks.sort(key=lambda x: (x[1], x[0]))

        print(f"\n{f.name}  {len(blocks)} 段")
        for i, (x, y, text) in enumerate(blocks):
            m = LEVEL_RE.search(text)
            if not m:
                continue
            level = int(m.group(1))
            # 进度 x/y：可能和「等级」在同一块，也可能是紧邻的下一块
            prog = None
            if PROGRESS_RE.search(text):
                prog = PROGRESS_RE.search(text).groups()
            elif i + 1 < len(blocks) and PROGRESS_RE.search(blocks[i + 1][2]):
                prog = PROGRESS_RE.search(blocks[i + 1][2]).groups()

            # 道具名：等级块右下方、同一列范围内最近的纯文本块
            name = None
            for x2, y2, t2 in blocks:
                if not (y < y2 < y + 90):
                    continue
                if abs(x2 - x) > 220:
                    continue
                if LEVEL_RE.search(t2) or PROGRESS_RE.fullmatch(t2.strip()):
                    continue
                if re.search(r"[价¥￥]", t2):
                    continue
                # 纯数字、"8/8"、"418" 这类是进度或残留，不是道具名
                if re.fullmatch(r"[\d\s/.,]+", t2):
                    continue
                if len(re.sub(r"\W", "", t2)) < 2:
                    continue
                name = t2.strip()
                break

            slot = match_slot(name) if name else None
            tag = f"Lv{level}" + (f" ({prog[0]}/{prog[1]})" if prog else "")
            if slot:
                # 同一槽位可能有多件（乐器/海报/传单），取最高等级
                prev = found.get(slot)
                if not prev or level > prev["level"]:
                    found[slot] = {"level": level, "name": name, "file": f.name}
                print(f"   ✓ {tag:<12} {name:<14} -> {slot}")
            else:
                unmatched.append(f"{f.name}: {tag} {name}")
                print(f"   ? {tag:<12} {name}")

    payload = {
        "source": "screenshot",
        "items": {k: [0, v["level"]] for k, v in found.items()},
        "detail": found,
        "unmatched": unmatched,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 汇总 ===")
    print(f"识别到 {len(found)} 个槽位，未匹配 {len(unmatched)} 条")
    for slot, v in sorted(found.items()):
        print(f"  {slot:<16} Lv{v['level']:<3} ({v['name']})")
    print(f"\n✓ 写入 {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
