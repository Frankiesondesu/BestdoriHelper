"""命令行入口。

典型流程::

    bdh sync                      # 1. 同步 Bestdori 卡牌数据
    bdh index build               # 2. 构建卡面指纹库（会下载卡图，约 1~2 GB）
    bdh scan ~/shots/*.png        # 3. 批量识别截图，写入清单
    bdh review                    # 4. 查看待确认项
    bdh export -f markdown        # 5. 导出清单
    bdh import-bestdori           # 6. 同步到 Bestdori（默认试运行）
    bdh gui                       # 桌面界面（推荐，图形化操作）
    bdh serve                     # 或启动 Web UI
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .bestdori.client import BestdoriClient
from .config import LANGUAGES, SOURCE_LABEL, Server, Settings
from .inventory.export import to_csv, to_json, to_markdown, to_bestdori_ids
from .inventory.store import Inventory
from .vision.index import FingerprintIndex, build_index
from .vision.ocr import get_ocr_engine
from .vision.recognize import recognize_image
from .vision.verify import build_verifier

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _say(msg: str) -> None:
    print(msg, flush=True)


#: 卡图来源的可读说明
_SOURCE_LABEL = {
    "thumb": "thumb —— " + SOURCE_LABEL["thumb"],
    "original": "original —— " + SOURCE_LABEL["original"],
}


def _source_label(src: str) -> str:
    return _SOURCE_LABEL.get(src, src)


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings(server=Server(args.server))
    if getattr(args, "lang", None) is not None:
        s.lang = int(args.lang)
    if getattr(args, "home", None):
        s.home = Path(args.home)
    src = getattr(args, "source", None)
    if src:
        s.image_source = src
    if getattr(args, "no_refine", False):
        s.refine_cells = False
    s.ensure_dirs()
    return s


def _load_everything(settings: Settings, *, need_index: bool = True):
    client = BestdoriClient(settings)
    catalog = client.fetch_catalog()
    index = FingerprintIndex.load(settings.index_path) if need_index else None
    if need_index and (index is None or len(index) == 0):
        _say("指纹库为空，请先运行：bdh index build")
        raise SystemExit(2)
    return client, catalog, index


def _iter_images(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        path = Path(p).expanduser()
        if path.is_dir():
            out.extend(sorted(f for f in path.rglob("*") if f.suffix.lower() in IMAGE_SUFFIXES))
        elif path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            out.append(path)
    return out


def _parse_region(text: str | None) -> tuple[float, float, float, float] | None:
    """解析 ``--region x0,y0,x1,y1``（0~1 归一化）。"""
    if not text:
        return None
    parts = [p.strip() for p in text.replace("，", ",").split(",") if p.strip()]
    if len(parts) != 4:
        raise SystemExit("--region 需要 4 个 0~1 之间的数：x0,y0,x1,y1")
    try:
        vals = tuple(float(p) for p in parts)
    except ValueError:
        raise SystemExit("--region 里有无法解析的数字") from None
    if any(v < 0 or v > 1 for v in vals):
        raise SystemExit("--region 的坐标必须落在 0~1 之间（相对图片宽高的比例）")
    return vals  # type: ignore[return-value]


# ---------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------


def cmd_sync(args: argparse.Namespace) -> int:
    s = _settings(args)
    with BestdoriClient(s) as client:
        catalog = client.fetch_catalog(refresh=True, progress=_say)
    _say(f"完成。数据缓存目录：{s.data_dir}")
    _say(f"语言：{LANGUAGES.get(s.lang or 0, '?')}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    s = _settings(args)
    if getattr(args, "source", None):
        s.image_source = args.source
    if args.index_action == "stats":
        idx = FingerprintIndex.load(s.index_path)
        if idx is None:
            _say(f"还没有指纹库（来源 = {s.image_source}）。运行：bdh index build")
            return 1
        st = idx.stats()
        _say(f"指纹库：{s.index_path}")
        _say(f"  卡图来源：{_source_label(s.image_source)}")
        _say(f"  卡面变体 {st['variants']} 个，覆盖 {st['cards']} 张卡"
             f"（普通 {st['normal']}，特训后 {st['trained']}）")
        return 0

    with BestdoriClient(s) as client:
        catalog = client.fetch_catalog(progress=_say)
        old = FingerprintIndex.load(s.index_path)
        only = None
        if args.cards:
            only = {int(x) for x in args.cards.split(",") if x.strip()}
            _say(f"⚠️  --cards 是**局部重建**：新指纹库只会包含这 {len(only)} 张卡，"
                 f"原有 {len(old) if old else 0} 条指纹会被覆盖。")
            _say("    想做完整库请不带 --cards 重新运行。")
            old = None  # 局部重建时不做增量复用，避免留下无关行
        elif old is not None:
            _say(f"发现已有指纹库（{len(old)} 条），将做增量更新。")
        if args.workers:
            s.max_concurrency = max(1, args.workers)
        _say(f"卡图来源：{_source_label(s.image_source)}")
        _say(f"卡图下载并发度：{s.max_concurrency}")
        idx, stats = build_index(
            catalog,
            client,
            include_trained=not args.no_trained,
            only_card_ids=only,
            reuse=old,
            progress=_say,
        )
    idx.save(s.index_path)
    _say(stats.summary())
    _say(f"已保存：{s.index_path}（{s.index_path.stat().st_size / 1e6:.1f} MB）")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    s = _settings(args)
    _, catalog, index = _load_everything(s)

    images = _iter_images(args.paths)
    if not images:
        _say("没有找到可处理的图片。")
        return 1

    ocr = get_ocr_engine(prefer=args.ocr)
    inv = Inventory.load(s.inventory_path)
    region = _parse_region(args.region)

    verifier = None if args.no_verify else build_verifier(catalog)
    if verifier is not None:
        _say("几何校验：开启（粗筛 top-30 + SIFT/RANSAC 二次排序）")
    elif not args.no_verify:
        _say("几何校验：不可用（未装 opencv-python），退回纯全局特征识别")

    refine = s.refine_cells and args.mode != "single"
    _say("卡框贴合：" + ("开启（每格收缩到卡框边界，去掉相邻卡面的边缘）" if refine
                        else "关闭（按等分格子切）"))

    _say(f"共 {len(images)} 张截图，模式={args.mode}"
         f"{f'，网格={args.rows}x{args.cols}' if args.mode == 'grid' else ''}"
         f"{'，区域=' + args.region if region else ''}"
         f"{'，OCR=开启' if ocr.available else ''}")

    total = {"added": 0, "skipped": 0, "pending": 0}
    for i, img_path in enumerate(images, 1):
        _say(f"[{i}/{len(images)}] {img_path.name}")
        res = recognize_image(
            img_path,
            index,
            catalog,
            mode=args.mode,
            rows=args.rows,
            cols=args.cols,
            inset=args.inset,
            ocr=ocr,
            use_ocr=ocr.available and not args.no_ocr,
            region=region,
            verifier=verifier,
        )
        for item in res.items:
            info = catalog.describe(item.card_id, item.trained) if item.card_id else None
            label = (
                f"{info['character']} · {info['title']} [{info['rarityLabel']} {info['attributeLabel']}]"
                if info
                else "（未识别）"
            )
            _say(f"    {item.status:<9} {item.confidence:.3f}  {label}")
            if args.urls and info is not None:
                _say(f"        {info['url']}")

        delta = inv.absorb_recognition(
            res,
            min_confidence=args.min_confidence,
            include_ambiguous=not args.strict,
        )
        for k in total:
            total[k] += delta[k]

    inv.save()
    _say("")
    _say(f"清单已更新：新增 {total['added']}，已存在跳过 {total['skipped']}，"
         f"待人工确认 {total['pending']}")
    _say(f"清单文件：{s.inventory_path}（共 {len(inv)} 条）")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    s = _settings(args)
    inv = Inventory.load(s.inventory_path)
    if not len(inv):
        _say("清单是空的。先运行：bdh scan <截图目录>")
        return 0

    with BestdoriClient(s) as client:
        catalog = client.fetch_catalog()

    st = inv.stats(catalog)
    _say(f"清单共 {st.total} 张（特训后 {st.trained} 张，待确认 {st.pending_review} 张）")
    if st.by_rarity:
        _say("星级：" + "  ".join(f"{r}★={n}" for r, n in sorted(st.by_rarity.items())))
    if st.by_attribute:
        _say("属性：" + "  ".join(f"{a}={n}" for a, n in sorted(st.by_attribute.items(), key=lambda kv: -kv[1])))
    _say("")

    rows = inv.all()
    if args.pending:
        rows = [r for r in rows if not r.confirmed]
    for i, oc in enumerate(rows[: args.limit], 1):
        info = catalog.describe(oc.card_id, oc.trained)
        flag = "" if oc.confirmed else "  ⚠待确认"
        _say(
            f"{i:>4}. {info['character']:<8} {info['title']:<18} "
            f"{info['rarityLabel']:<3} {info['attributeLabel']:<3} "
            f"{'特训' if oc.trained else '  '} {oc.confidence:.3f}{flag}"
        )
    if len(rows) > args.limit:
        _say(f"... 还有 {len(rows) - args.limit} 条（用 --limit 调整）")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    s = _settings(args)
    inv = Inventory.load(s.inventory_path)
    if not len(inv):
        _say("清单是空的，没什么可导出。")
        return 1
    with BestdoriClient(s) as client:
        catalog = client.fetch_catalog()

    fmt = args.format
    if fmt == "csv":
        content = to_csv(inv, catalog)
    elif fmt == "json":
        content = to_json(inv, catalog)
    elif fmt == "ids":
        content = to_bestdori_ids(inv)
    else:
        content = to_markdown(inv, catalog)

    if args.output:
        out = Path(args.output).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        _say(f"已导出：{out}")
    else:
        print(content)
    return 0


def cmd_import_bestdori(args: argparse.Namespace) -> int:
    from .bridge.bestdori_import import PlaywrightImporter, build_import_plan

    s = _settings(args)
    inv = Inventory.load(s.inventory_path)
    if not len(inv):
        _say("清单是空的。先运行：bdh scan <截图目录>")
        return 1

    with BestdoriClient(s) as client:
        catalog = client.fetch_catalog()

    plan = build_import_plan(inv, catalog, remote_keys=None, skip_unconfirmed=args.confirmed_only)
    _say(plan.summary())

    if args.plan_out:
        out = Path(args.plan_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        import json as _json

        out.write_text(_json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        _say(f"计划已写入：{out}")

    if args.plan_only:
        for e in plan.entries[:50]:
            _say(f"  {e.card_id:>6}  {e.character:<8} {e.title:<18} {e.rarity}★")
        if len(plan) > 50:
            _say(f"  ... 还有 {len(plan) - 50} 条")
        return 0

    importer = PlaywrightImporter(headless=args.headless)
    if args.inspect:
        importer.inspect()
        return 0

    outcome = importer.run(plan, apply=args.apply, progress=_say)
    _say(outcome.summary())
    if outcome.failed:
        _say("失败明细：")
        for f in outcome.failed[:20]:
            _say(f"  card {f['cardId']}: {f['error']}")
    return 0 if not outcome.failed else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    s = _settings(args)
    from .web.app import create_app

    app = create_app(s)
    _say(f"BestdoriHelper Web UI → http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    """启动桌面 GUI（PySide6）。"""
    try:
        from .gui.app import run as run_gui
    except ImportError as e:  # pragma: no cover
        _say("未安装 PySide6，桌面界面无法启动。请执行：")
        _say("  pip install PySide6")
        _say(f"（原始错误：{e}）")
        return 2

    s = _settings(args)
    _say(f"BestdoriHelper 桌面版启动中…（数据目录 {s.home}）")
    return run_gui(s)


# ---------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bdh",
        description="BanG Dream! 少女乐团派对 —— 卡面截图批量识别 + Bestdori 卡册同步",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--server", default=Server.CN.value, choices=[s.value for s in Server],
                   help="服务器区域（默认 cn 国服）")
    p.add_argument("--lang", type=int, choices=list(LANGUAGES), help="语言下标，默认跟随服务器")
    p.add_argument("--home", help="数据目录，默认 ~/.bestdori-helper")
    p.add_argument("--urls", action="store_true",
                   help="每格后面打印 Bestdori 卡面详情页网址，方便人工核对识别结果")
    p.add_argument("--no-verify", action="store_true",
                   help="关掉 SIFT 几何校验（默认开启）。关掉后退回纯全局特征识别，"
                        "真实截图 top-1 准确率会从 100% 掉到 67.9%")
    p.add_argument("--no-refine", action="store_true",
                   help="关掉卡框贴合（默认开启）—— 即直接按等分格子切，"
                        "不做「格内找卡框边界」这一步。贴合后切出来的是纯卡面，"
                        "不带相邻卡面的边缘")
    p.add_argument(
        "--source",
        choices=["thumb", "original"],
        default=None,
        help="卡图来源：thumb 用 Bestdori 180x180 方形缩略图建库（默认，取景与游戏内卡面格一致）；"
             "original 用 1334x1002 横向原图。两者指纹库分开存，互不覆盖。",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("sync", help="同步 Bestdori 卡牌数据到本地").set_defaults(func=cmd_sync)

    pi = sub.add_parser("index", help="构建 / 查看卡面指纹库")
    pi.add_argument("index_action", choices=["build", "stats"], help="build 构建，stats 查看")
    pi.add_argument("--no-trained", action="store_true", help="不处理特训后卡面")
    pi.add_argument("--cards", help="只处理指定卡牌 ID，逗号分隔（用于小规模试跑）")
    pi.add_argument("--workers", type=int, help="卡图下载并发度，默认 12；网络差就调小")
    pi.set_defaults(func=cmd_index)

    ps = sub.add_parser("scan", help="识别截图并写入清单")
    ps.add_argument("paths", nargs="+", help="截图文件或目录")
    ps.add_argument("--mode", choices=["auto", "grid", "single"], default="auto",
                    help="切分模式：auto 自动 / grid 按行列 / single 整图一张")
    ps.add_argument("--rows", type=int, help="grid 模式行数")
    ps.add_argument("--cols", type=int, help="grid 模式列数")
    ps.add_argument("--inset", type=float, default=0.0, help="单元格向内收缩比例，去掉边框")
    ps.add_argument(
        "--region",
        help="卡面网格区域 x0,y0,x1,y1（0~1 归一化）；整屏 UI 截图必填，同类界面只需量一次",
    )
    ps.add_argument("--ocr", action="store_true", help="启用 OCR 辅助（需装 rapidocr-onnxruntime）")
    ps.add_argument("--no-ocr", action="store_true", help="即使装了 OCR 也不使用")
    ps.add_argument("--strict", action="store_true", help="有歧义的结果不入清单，只标记待确认")
    ps.add_argument("--min-confidence", type=float, default=0.0, help="低于该置信度不入清单")
    ps.set_defaults(func=cmd_scan)

    pl = sub.add_parser("list", help="查看清单")
    pl.add_argument("--pending", action="store_true", help="只看待确认项")
    pl.add_argument("--limit", type=int, default=40, help="最多显示多少条")
    pl.set_defaults(func=cmd_list)

    pe = sub.add_parser("export", help="导出清单")
    pe.add_argument("-f", "--format", choices=["markdown", "csv", "json", "ids"], default="markdown")
    pe.add_argument("-o", "--output", help="输出文件；不指定则打印到终端")
    pe.set_defaults(func=cmd_export)

    pb = sub.add_parser("import-bestdori", help="把清单同步到 Bestdori「我的卡牌」")
    pb.add_argument("--apply", action="store_true", help="真正写入（默认只试运行）")
    pb.add_argument("--plan-only", action="store_true", help="只打印计划，不开浏览器")
    pb.add_argument("--plan-out", help="把计划写成 JSON 文件")
    pb.add_argument("--confirmed-only", action="store_true", help="只导入已人工确认的条目")
    pb.add_argument("--inspect", action="store_true", help="打开页面并打印元素，用于校准选择器")
    pb.add_argument("--headless", action="store_true", help="无头模式（首次登录不要用）")
    pb.set_defaults(func=cmd_import_bestdori)

    pv = sub.add_parser("serve", help="启动本地 Web UI")
    pv.add_argument("--port", type=int, default=8787)
    pv.set_defaults(func=cmd_serve)

    pg = sub.add_parser("gui", help="启动桌面 GUI（需要 PySide6）")
    pg.set_defaults(func=cmd_gui)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        _say("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
