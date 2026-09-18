"""用 **Bestdori 真实卡图** 做端到端验证。

和 ``e2e_check.py`` 的区别
--------------------------
``e2e_check.py`` 用的是程序合成的"假卡面"（渐变 + 色块），只能证明算法逻辑自洽。
这个脚本下载**真实卡图**，按游戏卡牌列表的样式（属性色边框 + 星级条 + 缩放 + JPEG 压缩）
拼成截图，测真实数据下的表现。同时顺手量一下全量卡图库到底占多大。

::

    python scripts/real_data_check.py                  # 默认 48 张卡，独立数据目录
    python scripts/real_data_check.py --cards 120
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from bestdori_helper.bestdori.client import BestdoriClient  # noqa: E402
from bestdori_helper.config import ATTRIBUTE_COLOR, Server, Settings  # noqa: E402
from bestdori_helper.models import Card  # noqa: E402
from bestdori_helper.vision.index import build_index  # noqa: E402
from bestdori_helper.vision.recognize import recognize_image  # noqa: E402

BG = (26, 28, 32)
STAR = (255, 214, 92)


# ---------------------------------------------------------------------
# 把真实卡图拼成"游戏截图"
# ---------------------------------------------------------------------


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def make_cell(
    art: Image.Image,
    card: Card,
    *,
    size: tuple[int, int] = (160, 120),
    border: int = 5,
    star_h: int = 14,
    desaturate: float = 0.0,
) -> Image.Image:
    """模拟游戏里的一格卡面：属性色边框 + 缩略卡图 + 底部星级条。

    :param desaturate: 边框颜色向灰色靠拢的比例（0=饱和的属性色，1=纯灰），
        用来模拟"边框颜色不明显"的难例。
    """
    rgb = _hex(ATTRIBUTE_COLOR.get(card.attribute, "#888888"))
    if desaturate > 0:
        gray = sum(rgb) / 3
        rgb = tuple(int(v + (gray - v) * desaturate) for v in rgb)  # type: ignore[assignment]

    cell = Image.new("RGB", (size[0] + 2 * border, size[1] + border + star_h), rgb)
    cell.paste(art.resize(size, Image.Resampling.LANCZOS), (border, border))

    d = ImageDraw.Draw(cell)
    d.rectangle(
        [border, border + size[1], border + size[0] - 1, border + size[1] + star_h - 1],
        fill=(18, 19, 22),
    )
    for i in range(min(card.rarity, 5)):
        x = border + 4 + i * 9
        d.rectangle([x, border + size[1] + 3, x + 5, border + size[1] + 8], fill=STAR)
    return cell


def compose(
    cells: list[Image.Image],
    cols: int,
    *,
    gap: int = 6,
    pad: int = 14,
) -> Image.Image:
    """按 cols 列排布成一张截图。"""
    rows = (len(cells) + cols - 1) // cols
    cw = max(c.width for c in cells)
    ch = max(c.height for c in cells)
    w = pad * 2 + cols * cw + (cols - 1) * gap
    h = pad * 2 + rows * ch + (rows - 1) * gap
    sheet = Image.new("RGB", (w, h), BG)
    for i, cell in enumerate(cells):
        r, c = divmod(i, cols)
        sheet.paste(cell, (pad + c * (cw + gap), pad + r * (ch + gap)))
    return sheet


# ---------------------------------------------------------------------
# 采样与建库
# ---------------------------------------------------------------------


def sample_variants(catalog, client: BestdoriClient, want: int, say) -> list[tuple[Card, bool, Path]]:
    """在全卡池里等距采样，凑够 ``want`` 个"卡 + 卡面变体"。"""
    ids = sorted(catalog.cards)
    step = max(1, len(ids) // (want * 2))
    out: list[tuple[Card, bool, Path]] = []
    for n, cid in enumerate(ids[::step], 1):
        card = catalog.cards[cid]
        variants = client.resolve_variants(card, include_trained=True)
        if n % 10 == 0:
            say(f"  采样中… {n} 张卡，已取到 {len(out)} 个卡面")
        for path, trained in variants:
            out.append((card, trained, path))
            if len(out) >= want:
                return out
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="用真实 Bestdori 卡图做端到端验证")
    ap.add_argument("--cards", type=int, default=48, help="采样多少个卡面变体（默认 48）")
    ap.add_argument("--server", default="cn", choices=[s.value for s in Server])
    ap.add_argument("--home", default=str(REPO / ".bdh-test" / "real-data"),
                    help="独立数据目录，避免污染正式指纹库")
    args = ap.parse_args()

    settings = Settings(server=Server(args.server), home=Path(args.home))
    settings.ensure_dirs()

    def say(msg: str) -> None:
        print(msg, flush=True)

    say("=" * 68)
    say(f"真实卡图端到端验证 · 服务器 {args.server} · 数据目录 {settings.home}")
    say("=" * 68)

    client = BestdoriClient(settings)
    try:
        say("\n[1/4] 拉取卡池资料")
        catalog = client.fetch_catalog(progress=say)

        say(f"\n[2/4] 采样并下载真实卡图（目标 {args.cards} 个卡面变体）")
        variants = sample_variants(catalog, client, args.cards, say)
        if not variants:
            say("❌ 一个卡面都没取到，检查网络。")
            return 1

        sizes = [p.stat().st_size for _, _, p in variants]
        avg_kb = sum(sizes) / len(sizes) / 1024
        total_mb = sum(sizes) / 1024 / 1024
        est_variants = sum(2 if c.rarity >= 3 else 1 for c in catalog.cards.values())
        say(f"  取到 {len(variants)} 个卡面，共 {total_mb:.1f} MB，平均 {avg_kb:.0f} KB/张")
        say(f"  全库估算：约 {est_variants} 个变体 × {avg_kb:.0f} KB ≈ "
            f"{est_variants * avg_kb / 1024 / 1024:.2f} GB")

        say("\n[3/4] 构建指纹库（仅采样卡）")
        only = {c.id for c, _, _ in variants}
        index, stats = build_index(
            catalog, client, include_trained=True, only_card_ids=only, progress=say
        )
        say("  " + stats.summary())
    finally:
        client.close()

    # ---- 组装截图 ----
    say("\n[4/4] 合成真实卡图截图并识别")

    layouts = {
        "A 标准 4×3（属性色边框 + 星级条）": dict(
            cols=4, rows=3, size=(160, 120), border=5, gap=6, quality=95
        ),
        "B 小图 6×2（110×83 + JPEG q70）": dict(
            cols=6, rows=2, size=(110, 83), border=4, gap=4, quality=70
        ),
        "C 紧贴网格 4×3（无间隙）": dict(
            cols=4, rows=3, size=(150, 113), border=3, gap=0, quality=90
        ),
        "D 边框褪色 4×3（属性提示难例）": dict(
            cols=4, rows=3, size=(160, 120), border=5, gap=6, quality=95, desaturate=0.85
        ),
        "E 末行不满（10 格 / 12 格位）": dict(
            cols=4, rows=3, cells=10, size=(160, 120), border=5, gap=6, quality=95
        ),
    }

    report: list[str] = []
    all_top1 = all_total = 0
    grid_ok_n = grid_total_n = 0

    for name, cfg in layouts.items():
        cols = cfg["cols"]
        n_cells = cfg.get("cells", cols * cfg["rows"])
        rows = (n_cells + cols - 1) // cols
        slots = rows * cols  # 网格总格位数（末行不满时会多于实际卡面数）

        cells, expect = [], []
        for card, trained, path in variants[:n_cells]:
            with Image.open(path) as im:
                art = im.convert("RGB")
            cells.append(
                make_cell(
                    art,
                    card,
                    size=cfg["size"],
                    border=cfg["border"],
                    desaturate=cfg.get("desaturate", 0.0),
                )
            )
            expect.append((card.id, trained))

        sheet = compose(cells, cols, gap=cfg["gap"])
        shot = settings.home / f"shot_{name[0]}.png"
        if cfg["quality"] < 95:
            buf = io.BytesIO()
            sheet.save(buf, "JPEG", quality=cfg["quality"])
            shot = shot.with_suffix(".jpg")
            shot.write_bytes(buf.getvalue())
        else:
            sheet.save(shot)

        # --- auto 模式：网格要靠自己推断 ---
        res_auto = recognize_image(shot, index, catalog, mode="auto", use_attribute_hint=True)
        got = [(i.card_id, i.trained) for i in res_auto.items]
        hit = sum(1 for a, b in zip(got, expect) if a == b)
        ok = len(res_auto.items) == slots
        grid_ok_n += int(ok)
        grid_total_n += 1
        grid_txt = f"✓（{slots} 格位）" if ok else f"✗ 切出 {len(res_auto.items)} 格，期望 {slots}"

        # --- grid 模式：显式行列，作为"识别质量"的基准 ---
        res_grid = recognize_image(
            shot, index, catalog, mode="grid", rows=rows, cols=cols, use_attribute_hint=True
        )
        got_g = [(i.card_id, i.trained) for i in res_grid.items]
        hit_g = sum(1 for a, b in zip(got_g, expect) if a == b)

        # --- 属性提示 A/B ---
        res_no = recognize_image(
            shot, index, catalog, mode="grid", rows=rows, cols=cols, use_attribute_hint=False
        )
        got_n = [(i.card_id, i.trained) for i in res_no.items]
        hit_n = sum(1 for a, b in zip(got_n, expect) if a == b)

        all_top1 += hit_g
        all_total += len(expect)

        line = (
            f"\n{name}\n"
            f"  截图 {sheet.width}×{sheet.height}，实际 {len(cells)} 格 / 网格 {slots} 格位"
            f"（{rows}×{cols}）\n"
            f"  auto 切分：{grid_txt}\n"
            f"  Top-1  grid 模式 {hit_g}/{len(expect)}    auto 模式 {hit}/{len(expect)}\n"
            f"  属性提示：开 {hit_g}/{len(expect)}    关 {hit_n}/{len(expect)}"
        )
        report.append(line)
        say(line)
        for g, e in zip(got_g, expect):
            if g != e:
                got_txt = f"#{g[0]}{'T' if g[1] else ''}" if g[0] is not None else "未识别"
                say(f"      期望 #{e[0]}{'T' if e[1] else ''} → 得到 {got_txt}")

    say("\n" + "=" * 68)
    say(
        f"汇总：grid 模式 Top-1 {all_top1}/{all_total}"
        f"（{all_top1 / max(1, all_total) * 100:.1f}%），"
        f"auto 切分正确 {grid_ok_n}/{grid_total_n}"
    )
    say("=" * 68)

    (REPO / ".bdh-test" / "real-data-report.txt").write_text("\n".join(report), encoding="utf-8")
    say(f"\n截图与报告留在：{settings.home}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
