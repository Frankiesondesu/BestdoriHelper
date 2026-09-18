"""边界场景验证（开发用）。

覆盖三件此前没测过的事：

1. ``auto`` 自动网格切分 —— 这是 CLI 的默认模式，但之前的测试都传了显式行列
   - 有背景间隙（真实游戏列表常见）
   - 无背景间隙、卡框紧贴（旧实现会整块吞掉）
2. 属性提示 A/B —— ``use_attribute_hint`` 开/关对准确率的影响
3. 不同边框宽度、带模拟 UI 叠加（星级条 + 属性图标）的鲁棒性

用法::

    python scripts/e2e_check.py     # 先建指纹库
    python scripts/edge_cases.py

⚠️ 默认数据目录是仓库内的 ``.bdh-test/e2e``（与 ``e2e_check.py`` 共用），
**不会碰你的正式数据目录**。
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from bestdori_helper.bestdori.client import BestdoriClient  # noqa: E402
from bestdori_helper.config import ATTRIBUTE_COLOR, Server, Settings  # noqa: E402
from bestdori_helper.vision.detect import detect_boxes  # noqa: E402
from bestdori_helper.vision.index import FingerprintIndex  # noqa: E402
from bestdori_helper.vision.recognize import recognize_image  # noqa: E402

OUT = REPO / "build" / "edge"
OUT.mkdir(parents=True, exist_ok=True)

def _hex_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


#: 四属性对应的卡框色。**必须按卡的真实属性上色** —— 游戏里卡框颜色就是属性
#: 指示器，识别流水线会读它当"属性提示"。早期版本按槽位轮流取色，等于给流水线
#: 喂假信号，会把正确答案筛掉，据此得出的"属性提示有害"结论是假的
#: （关掉提示就 100%，说明问题全在基准的假信号上）。
ATTR_COLORS = [_hex_rgb(ATTRIBUTE_COLOR[a]) for a in ("powerful", "cool", "pure", "happy")]
_ATTR_INDEX = {"powerful": 0, "cool": 1, "pure": 2, "happy": 3}
BG = (18, 18, 26)


def make_sheet(
    cards,
    client: BestdoriClient,
    dest: Path,
    *,
    cols: int,
    rows: int,
    cell: tuple[int, int],
    border: int = 6,
    gutter: int = 0,
    overlay: bool = False,
    quality: int = 85,
):
    """合成模拟截图。

    :param gutter: 卡框之间的背景间隙像素数。0 表示卡框紧贴。
    :param overlay: 是否叠加模拟游戏 UI（底部星级条 + 左上属性图标）
    """
    step_x = cell[0] + border * 2 + gutter
    step_y = cell[1] + border * 2 + gutter
    W = cols * step_x + gutter
    H = rows * step_y + gutter
    canvas = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(canvas)

    slots: list[tuple[int, bool] | None] = [None] * (cols * rows)
    for i, card in enumerate(cards[: cols * rows]):
        variants = client.resolve_variants(card, include_trained=False)
        if not variants:
            continue
        path, trained = variants[0]
        with Image.open(path) as im:
            thumb = im.convert("RGB").resize(cell, Image.Resampling.LANCZOS)

        c, r = i % cols, i // cols
        x = gutter + c * step_x + border
        y = gutter + r * step_y + border
        col = ATTR_COLORS[_ATTR_INDEX.get(card.attribute, i % 4)]
        draw.rectangle(
            [x - border, y - border, x + cell[0] + border - 1, y + cell[1] + border - 1], fill=col
        )
        canvas.paste(thumb, (x, y))

        # 右上角属性图标：真实游戏格里有，也是属性提示读取的位置
        # （``estimate_attribute_icon`` 看的是右上角 25%x20%）。不画这个，
        # 提示就只能读到卡面画色、给出错误属性。
        ic = max(12, int(cell[0] * 0.20))
        m = max(2, int(cell[0] * 0.04))
        draw.ellipse([x + cell[0] - m - ic, y + m, x + cell[0] - m, y + m + ic], fill=col)

        if overlay:
            # 底部星级条（模拟游戏 UI 叠加）
            bar_h = max(8, cell[1] // 9)
            draw.rectangle([x, y + cell[1] - bar_h, x + cell[0] - 1, y + cell[1] - 1], fill=(12, 12, 18))
            for s in range(5):
                sx = x + 6 + s * (bar_h - 2)
                draw.ellipse([sx, y + cell[1] - bar_h + 2, sx + bar_h - 5, y + cell[1] - 3],
                             fill=(255, 214, 90))

        slots[i] = (card.id, trained)

    canvas.save(dest, quality=quality)
    return slots


def score(res, slots, catalog):
    """把识别结果与真值比对，返回统计。"""
    ok = amb = unk = wrong = blank = 0
    for i, item in enumerate(res.items):
        expect = slots[i] if i < len(slots) else None
        if expect is None:
            if item.status == "matched":
                wrong += 1
            else:
                blank += 1
        elif item.card_id == expect[0]:
            ok += 1
        elif item.status == "ambiguous":
            amb += 1
        elif item.status == "unknown":
            unk += 1
        else:
            wrong += 1
    real = len(res.items) - blank
    return {"n": real, "ok": ok, "amb": amb, "unk": unk, "wrong": wrong,
            "acc": ok / real if real else 0.0}


def main() -> int:
    ap = argparse.ArgumentParser(description="auto 切分与属性提示的边界场景验证")
    ap.add_argument(
        "--home",
        default=str(REPO / ".bdh-test" / "e2e"),
        help="数据目录，默认 .bdh-test/e2e（与 e2e_check.py 共用）",
    )
    args = ap.parse_args()

    settings = Settings(server=Server.CN, home=Path(args.home))
    settings.ensure_dirs()
    print(f"数据目录：{settings.home}")

    with BestdoriClient(settings) as client:
        catalog = client.fetch_catalog()
        index = FingerprintIndex.load(settings.index_path)
        if index is None or len(index) == 0:
            print("指纹库为空，请先运行 scripts/e2e_check.py 建库")
            return 2

        rng = random.Random(7777)
        # 关键：卡池必须取自**指纹库实际覆盖的卡**，否则测的是"库里没有这张卡"，
        # 识别率低是必然的，会掩盖真正的切分问题。
        known = sorted({int(c) for c in index.card_ids})
        cards = [catalog.cards[c] for c in known if c in catalog.cards]
        rng.shuffle(cards)
        print(f"指纹库覆盖 {len(known)} 张卡，本测试全部取自该集合\n")

        # ---------------- 1. auto 自动切分 ----------------
        print("=" * 64)
        print("1. auto 自动网格切分（CLI 默认模式）")
        print("=" * 64)

        layouts = [
            # 格子一律正方形：真实游戏格是方的，卡图也是方的 180x180 缩略图。
            # 用长方形格子会把卡图拉伸变形，识别准确率就没法看了
            # （auto 切分本身与长宽比无关，所以这些用例的切分覆盖度不受影响）。
            ("4x2 有间隙", 4, 2, (190, 190), 6, 10, False),
            ("4x2 无间隙紧贴", 4, 2, (190, 190), 6, 0, False),
            ("3x3 有间隙", 3, 3, (170, 170), 5, 8, False),
            ("5x2 细边框", 5, 2, (180, 180), 2, 6, False),
            ("4x2 粗边框", 4, 2, (190, 190), 12, 4, False),
            ("4x2 带UI叠加", 4, 2, (190, 190), 6, 8, True),
            ("6x2 有间隙", 6, 2, (140, 140), 4, 6, False),
        ]

        auto_total = auto_ok = 0
        for name, cols, rows, cell, border, gutter, overlay in layouts:
            shot = OUT / f"auto_{name.replace(' ', '_')}.png"
            slots = make_sheet(cards, client, shot, cols=cols, rows=rows, cell=cell,
                               border=border, gutter=gutter, overlay=overlay)

            with Image.open(shot) as im:
                boxes = detect_boxes(im)
            expect_n = cols * rows

            res = recognize_image(shot, index, catalog, mode="auto")
            st = score(res, slots, catalog)
            detected = len(boxes)
            flag = "✓" if detected == expect_n else "✗"
            print(f"\n  [{name}] 期望 {expect_n} 格 → 自动切出 {detected} 格 {flag}"
                  f"；识别 Top-1 {st['acc']:.0%}（{st['ok']}/{st['n']}）"
                  f" 歧义{st['amb']} 未识别{st['unk']} 错误{st['wrong']}")
            auto_total += expect_n
            auto_ok += detected if detected == expect_n else 0

        print(f"\n  → 切分正确率 {auto_ok}/{auto_total} 格")

        # ---------------- 2. 属性提示 A/B ----------------
        print("\n" + "=" * 64)
        print("2. 属性提示 A/B 对照")
        print("=" * 64)

        ab_cases = [
            ("4x2 标准", 4, 2, (190, 190), 6, 8, False),
            ("4x2 无间隙", 4, 2, (190, 190), 6, 0, False),
            ("3x3 小图", 3, 3, (160, 160), 5, 6, False),
        ]

        agg = {"on": {"n": 0, "ok": 0, "wrong": 0}, "off": {"n": 0, "ok": 0, "wrong": 0}}
        for name, cols, rows, cell, border, gutter, overlay in ab_cases:
            shot = OUT / f"ab_{name.replace(' ', '_')}.png"
            slots = make_sheet(cards, client, shot, cols=cols, rows=rows, cell=cell,
                               border=border, gutter=gutter, overlay=overlay)
            line = f"  [{name}]"
            for tag, use in (("on", True), ("off", False)):
                res = recognize_image(shot, index, catalog, mode="grid",
                                      rows=rows, cols=cols, use_attribute_hint=use)
                st = score(res, slots, catalog)
                for k in ("n", "ok", "wrong"):
                    agg[tag][k] += st[k]
                line += f"  属性提示{'开' if use else '关'}: {st['acc']:.0%}({st['ok']}/{st['n']})"
            print(line)

        print()
        for tag, label in (("on", "属性提示 开"), ("off", "属性提示 关")):
            a = agg[tag]
            acc = a["ok"] / a["n"] if a["n"] else 0
            print(f"  {label}：合计 Top-1 {acc:.1%}（{a['ok']}/{a['n']}），误判 {a['wrong']}")
        on_a = agg["on"]["ok"] / max(1, agg["on"]["n"])
        off_a = agg["off"]["ok"] / max(1, agg["off"]["n"])
        verdict = "有帮助" if on_a > off_a else ("无影响" if on_a == off_a else "有损害")
        print(f"  → 结论：属性提示{verdict}（差 {abs(on_a - off_a):.1%}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
