"""端到端验证脚本（开发用）。

流程：
1. 取一部分卡牌构建指纹库
2. 合成多张"游戏截图"（网格布局 + 边框 + 缩放 + JPEG 压缩 + 轻微色偏）
3. 跑识别，统计 Top-1 准确率与歧义率

用法::

    python scripts/e2e_check.py
    python scripts/e2e_check.py --home /tmp/foo

⚠️ 默认写在仓库内的 ``.bdh-test/e2e``，**不会碰你的正式数据目录**。
早期版本直接写 ``~/.bestdori-helper/data/fingerprints.cn.npz``，跑一次基准测试
就把真实指纹库覆盖成 80 张卡的合成库了 —— 别再用那种写法。
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from PIL import Image, ImageDraw, ImageEnhance  # noqa: E402

from bestdori_helper.bestdori.client import BestdoriClient  # noqa: E402
from bestdori_helper.config import ATTRIBUTE_COLOR, Server, Settings  # noqa: E402
from bestdori_helper.vision.index import FingerprintIndex, build_index  # noqa: E402
from bestdori_helper.vision.recognize import recognize_image  # noqa: E402

OUT = REPO / "build" / "e2e"
OUT.mkdir(parents=True, exist_ok=True)


def _hex_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


#: 四属性对应的卡框色。**必须按卡的真实属性上色** —— 游戏里卡框颜色就是
#: 属性指示器，识别流水线也会读它当"属性提示"。早期版本按位置轮流取色，
#: 等于给流水线喂了假信号，会把正确答案筛掉，测出来的准确率完全不可信。
ATTR_COLORS = [_hex_rgb(ATTRIBUTE_COLOR[a]) for a in ("powerful", "cool", "pure", "happy")]
_ATTR_INDEX = {"powerful": 0, "cool": 1, "pure": 2, "happy": 3}


#: 游戏格取景 = 缩略图中心多大比例。
#: 实测（``.bdh-test/measure_zoom.py``，用换库后高置信度命中的真实格子反向标定）
#: 中位数是 **1.00** —— 游戏内卡面格和 Bestdori 方形缩略图**取景本来就一致**，
#: 这正是换用缩略图建库后识别率大涨的原因。留成参数是为了合成"取景偏移"的
#: 鲁棒性用例（见 F/G 两个 case）。
DEFAULT_ZOOM = 1.0


def make_screenshot(
    cards,
    client: BestdoriClient,
    dest: Path,
    *,
    cols: int,
    rows: int,
    cell: tuple[int, int],
    border: int = 3,
    quality: int = 82,
    brightness: float = 1.0,
    color: float = 1.0,
    zoom: float = DEFAULT_ZOOM,
    attr_icon: bool = True,
):
    """把若干卡面拼成一张模拟游戏列表截图。

    返回与网格槽位一一对应的列表：``slots[i] = (card_id, trained) | None``。
    ``None`` 表示该槽位是空的（该卡在 Bestdori 上没有卡图）。

    几何上有一条硬要求：**画布尺寸必须正好是 ``cols*cell × rows*cell``**。
    因为识别时 ``grid`` 模式是**等分切分**的，画布一旦多留了外边距，
    切出来的框就和实际粘贴位置错位（框比格子宽、还偏移几个像素），
    测出来的准确率全是几何误差，跟识别能力无关。
    卡框改成**画在格子内部**（``border`` 是内缩像素），和游戏里一样。

    ``zoom`` 模拟"游戏格取景比缩略图更紧"：先从卡图中心裁掉 ``zoom`` 比例再
    缩放进格子。默认 1.0 = 用满整张缩略图（实测游戏就是这个取景）。

    格子必须是**正方形**：真实游戏格实测 170x170，卡图也是方形；
    用非正方形格子会把卡图拉伸变形，那种失真连人眼都认不出，测了没意义。
    """
    if cell[0] != cell[1]:
        raise ValueError(f"格子必须是正方形（真实游戏格是方的），收到 {cell}")

    W = cols * cell[0]
    H = rows * cell[1]
    canvas = Image.new("RGB", (W, H), (18, 18, 26))
    draw = ImageDraw.Draw(canvas)

    inner = max(8, cell[0] - border * 2)
    slots: list[tuple[int, bool] | None] = [None] * (cols * rows)
    for i, card in enumerate(cards[: cols * rows]):
        variants = client.resolve_variants(card, include_trained=False)
        if not variants:
            continue  # 该槽位留空
        path, trained = variants[0]
        with Image.open(path) as im:
            art = im.convert("RGB")
            if zoom < 1.0:
                w, h = art.size
                bw, bh = max(8, int(w * zoom)), max(8, int(h * zoom))
                x0, y0 = (w - bw) // 2, (h - bh) // 2
                art = art.crop((x0, y0, x0 + bw, y0 + bh))
            art = art.resize((inner, inner), Image.Resampling.LANCZOS)

        c, r = i % cols, i // cols
        x, y = c * cell[0], r * cell[1]
        col = ATTR_COLORS[_ATTR_INDEX.get(card.attribute, i % 4)]
        # 卡框画在格子内部 —— 这样等分切分切出的框正好等于整个格子
        draw.rectangle([x, y, x + cell[0] - 1, y + cell[1] - 1], fill=col)
        canvas.paste(art, (x + border, y + border))
        if attr_icon:
            # 右上角属性图标 —— 真实游戏格里有这个（约格宽的 20%），
            # 属性提示（``estimate_attribute_icon``）读的就是右上角 25%x20% 那块。
            # 不画的话提示只能读到卡面画色，会"自信地给出错误属性"并把正确答案筛掉，
            # 那是基准的失真，不是识别能力的问题。
            s = max(12, int(cell[0] * 0.20))
            m = max(2, int(cell[0] * 0.03))
            x1 = x + cell[0] - m
            y1 = y + m
            draw.ellipse([x1 - s, y1, x1, y1 + s], fill=col)
            draw.ellipse(
                [x1 - s * 0.6, y1 + s * 0.4, x1 - s * 0.4 + s * 0.2, y1 + s * 0.6 + s * 0.2],
                fill=(255, 255, 255),
            )
        slots[i] = (card.id, trained)

    if brightness != 1.0:
        canvas = ImageEnhance.Brightness(canvas).enhance(brightness)
    if color != 1.0:
        canvas = ImageEnhance.Color(canvas).enhance(color)
    canvas.save(dest, quality=quality)
    return slots


def run_case(name: str, cards, client, catalog, index, **kw):
    """合成一张截图并识别，返回统计。"""
    shot = OUT / f"{name}.png"
    grid = {k: kw.pop(k) for k in ("cols", "rows", "cell") if k in kw}
    slots = make_screenshot(cards, client, shot, **grid, **kw)
    if not any(slots):
        return None

    res = recognize_image(
        shot, index, catalog,
        mode="grid", rows=grid["rows"], cols=grid["cols"], inset=0.0,
    )

    ok = amb = unk = wrong = blank = 0
    details = []
    for i, item in enumerate(res.items):
        expect = slots[i] if i < len(slots) else None

        if expect is None:
            # 空槽位：不应该报 matched
            if item.status == "matched":
                wrong += 1
                mark = "✗空位误报"
            else:
                blank += 1
                mark = "·空位"
        elif item.card_id == expect[0]:
            ok += 1
            mark = "✓"
        elif item.status == "ambiguous":
            amb += 1
            mark = "~"
        elif item.status == "unknown":
            unk += 1
            mark = "?"
        else:
            wrong += 1
            mark = "✗"

        info = catalog.describe(item.card_id, item.trained) if item.card_id else None
        exp = catalog.describe(expect[0], expect[1]) if expect else None
        fmt = lambda d: (d["character"] + "·" + d["title"]) if d else "—"  # noqa: E731
        details.append(
            f"    {mark:<10} {item.status:<9} {item.confidence:.3f} "
            f"期望={fmt(exp):<26} 得到={fmt(info)}"
        )

    n = len(res.items)
    real = n - blank
    acc = ok / real if real else 0
    print(f"\n[{name}] {n} 格（空位 {blank}）：正确 {ok}，歧义 {amb}，未识别 {unk}，"
          f"错误 {wrong}  → 有效格 Top-1 {acc:.1%}")
    for d in details:
        print(d)
    return {"n": real, "ok": ok, "amb": amb, "unk": unk, "wrong": wrong}


def main() -> int:
    ap = argparse.ArgumentParser(description="端到端识别准确率验证")
    ap.add_argument(
        "--home",
        default=str(REPO / ".bdh-test" / "e2e"),
        help="数据目录，默认 .bdh-test/e2e（刻意与正式数据目录隔离）",
    )
    ap.add_argument(
        "--no-refine",
        action="store_true",
        help="关掉卡框贴合，直接按等分格子切 —— 用于 A/B 对照",
    )
    args = ap.parse_args()

    settings = Settings(server=Server.CN, home=Path(args.home))
    settings.refine_cells = not args.no_refine
    settings.ensure_dirs()
    print(f"数据目录：{settings.home}")
    print(f"卡框贴合：{'关闭（按等分格子切）' if args.no_refine else '开启'}")

    with BestdoriClient(settings) as client:
        catalog = client.fetch_catalog()

        rng = random.Random(20260916)
        by_char: dict[int, list[int]] = {}
        for cid, card in catalog.cards.items():
            by_char.setdefault(card.character_id, []).append(cid)

        # 挑同一角色的多张卡，刻意制造"难例"
        picked: list[int] = []
        for ch in [1, 2, 3, 5, 10, 18, 21, 25, 31, 34, 36, 40]:
            ids = sorted(by_char.get(ch, []))
            if ids:
                picked.extend(rng.sample(ids, min(7, len(ids))))
        picked = picked[:80]

        print(f"[1/3] 构建指纹库（{len(picked)} 张卡）…")
        idx, stats = build_index(
            catalog, client, include_trained=False, only_card_ids=set(picked),
            progress=lambda m: print("   ", m),
        )
        idx.save(settings.index_path)
        print("   ", stats.summary())

        index = FingerprintIndex.load(settings.index_path)
        assert index is not None
        usable = [catalog.cards[c] for c in picked if c in catalog.cards]
        rng.shuffle(usable)

        print("[2/3] 合成模拟截图并识别…")
        cases = []
        cases.append(run_case("A_4x2_标准", usable[0:8], client, catalog, index,
                              cols=4, rows=2, cell=(170, 170)))
        cases.append(run_case("B_3x3_小图", usable[8:17], client, catalog, index,
                              cols=3, rows=3, cell=(140, 140)))
        cases.append(run_case("C_5x2_JPEG压低", usable[17:27], client, catalog, index,
                              cols=5, rows=2, cell=(160, 160), quality=55))
        cases.append(run_case("D_4x2_偏暗偏灰", usable[27:35], client, catalog, index,
                              cols=4, rows=2, cell=(170, 170), brightness=0.82, color=0.7))
        cases.append(run_case("E_6x2_极小图", usable[35:47], client, catalog, index,
                              cols=6, rows=2, cell=(110, 110), quality=70))
        # F 专测"取景比缩略图更紧/更松"的鲁棒性：zoom 偏离默认值
        cases.append(run_case("F_4x2_取景偏松", usable[47:55], client, catalog, index,
                              cols=4, rows=2, cell=(170, 170), zoom=0.80))
        cases.append(run_case("G_4x2_取景偏紧", usable[55:63], client, catalog, index,
                              cols=4, rows=2, cell=(170, 170), zoom=0.42))

        print("\n" + "=" * 62)
        tot = {k: sum(c[k] for c in cases if c) for k in ("n", "ok", "amb", "unk", "wrong")}
        if tot["n"]:
            print(f"合计 {tot['n']} 格：Top-1 准确率 {tot['ok'] / tot['n']:.1%}，"
                  f"歧义 {tot['amb']}，未识别 {tot['unk']}，错误 {tot['wrong']}")
            print(f"可用率（正确+歧义可由人工点选）{(tot['ok'] + tot['amb']) / tot['n']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
