"""后台任务线程。

GUI 里所有耗时操作（网络同步、指纹计算、批量识别、浏览器自动化）都必须
离开主线程，否则窗口会假死。这里统一用 ``Worker(QThread)`` 包一层：

    任务函数签名固定为 ``fn(say)``，``say(str)`` 用来回报进度文字。

任务函数里**不碰任何 Qt 对象**，只做纯计算/IO，结果通过信号回主线程。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from ..bestdori.client import BestdoriClient
from ..config import Settings
from ..inventory.store import Inventory
from ..models import Catalog
from ..vision.index import FingerprintIndex, build_index
from ..vision.ocr import get_ocr_engine
from ..vision.recognize import recognize_image
from ..vision.verify import build_verifier

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

Say = Callable[[str], None]


class Worker(QThread):
    """在后台线程执行 ``fn(say)``。"""

    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[Say], Any], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._cancel = False

    def cancel(self) -> None:
        """请求取消。任务函数需自行检查 ``self.cancelled`` 才会真正中断。"""
        self._cancel = True

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:  # noqa: D102 - QThread 约定
        try:
            result = self._fn(self.progress.emit)
        except Exception as e:  # noqa: BLE001 - 后台异常必须转成信号，不能吞掉
            log.exception("后台任务失败")
            self.failed.emit(str(e) or e.__class__.__name__)
            return
        self.done.emit(result)


# ---------------------------------------------------------------------
# 任务工厂
# ---------------------------------------------------------------------


def iter_images(paths: list[str]) -> list[Path]:
    """把文件/目录混合的路径列表展开成图片文件列表。"""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            out.extend(sorted(f for f in p.rglob("*") if f.suffix.lower() in IMAGE_SUFFIXES))
        elif p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
            out.append(p)
    # 去重但保持顺序
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def task_sync(settings: Settings, *, refresh: bool = True) -> Callable[[Say], Catalog]:
    def run(say: Say) -> Catalog:
        client = BestdoriClient(settings)
        try:
            return client.fetch_catalog(refresh=refresh, progress=say)
        finally:
            client.close()

    return run


def task_build_index(
    settings: Settings,
    catalog: Catalog,
    *,
    include_trained: bool,
    limit: int = 0,
) -> Callable[[Say], tuple[FingerprintIndex, Any]]:
    def run(say: Say) -> tuple[FingerprintIndex, Any]:
        client = BestdoriClient(settings)
        try:
            old = FingerprintIndex.load(settings.index_path)
            only = None
            if limit and limit > 0:
                only = set(sorted(catalog.cards)[:limit])
                old = None  # 局部重建：不复用，避免留下无关行
                say(f"局部重建：只处理前 {len(only)} 张卡，新库会覆盖旧库")
            elif old is not None:
                say(f"发现已有指纹库（{len(old)} 条），做增量更新")
            idx, stats = build_index(
                catalog,
                client,
                include_trained=include_trained,
                only_card_ids=only,
                reuse=old,
                progress=say,
            )
            idx.save(settings.index_path)
            return idx, stats
        finally:
            client.close()

    return run


def task_scan(
    settings: Settings,
    index: FingerprintIndex,
    catalog: Catalog,
    paths: list[str],
    *,
    mode: str = "auto",
    rows: int | None = None,
    cols: int | None = None,
    inset: float = 0.0,
    use_ocr: bool = False,
    use_attribute_hint: bool = True,
    absorb: bool = True,
    min_confidence: float = 0.0,
    include_ambiguous: bool = True,
) -> Callable[[Say], dict]:
    def run(say: Say) -> dict:
        images = iter_images(paths)
        if not images:
            raise RuntimeError("没有找到可处理的图片（支持 png/jpg/jpeg/webp/bmp）")

        ocr = get_ocr_engine(prefer=use_ocr)
        if use_ocr and not ocr.available:
            say("⚠️ 未安装 OCR 引擎，本次跳过文字辅助（pip install rapidocr-onnxruntime）")

        inv = Inventory.load(settings.inventory_path)
        verifier = build_verifier(catalog)
        if verifier is None:
            say("几何校验不可用（需要 opencv-python），本次用纯全局特征识别")
        results = []
        total = {"added": 0, "skipped": 0, "pending": 0}

        for i, path in enumerate(images, 1):
            say(f"[{i}/{len(images)}] {path.name}")
            res = recognize_image(
                path,
                index,
                catalog,
                mode=mode,
                rows=rows,
                cols=cols,
                inset=inset,
                ocr=ocr,
                use_ocr=use_ocr and ocr.available,
                use_attribute_hint=use_attribute_hint,
                verifier=verifier,
            )
            results.append(res)
            say(f"    切分 {len(res.items)} 格，命中 {len(res.matched)}")
            if absorb:
                delta = inv.absorb_recognition(
                    res,
                    min_confidence=min_confidence,
                    include_ambiguous=include_ambiguous,
                )
                for k in total:
                    total[k] += delta[k]

        if absorb:
            inv.save()

        return {
            "results": results,
            "absorbed": total if absorb else None,
            "images": [str(p) for p in images],
            "inventory": inv,
        }

    return run


def task_import_plan(catalog: Catalog, inv: Inventory, *, confirmed_only: bool) -> Callable[[Say], Any]:
    def run(say: Say) -> Any:
        from ..bridge.bestdori_import import build_import_plan

        say("计算导入计划…")
        plan = build_import_plan(inv, catalog, remote_keys=None, skip_unconfirmed=confirmed_only)
        say(plan.summary())
        return plan

    return run


def task_api_login(settings: Settings, username: str, password: str) -> Callable[[Say], dict | None]:
    """后台账号登录：成功后会话 cookie 存本机，之后同步不再需要登录。

    **登录成功与否以 login 接口自己的响应为准**（它就是 ``result: true``）；
    ``user/me`` 只用来补充显示用户名，它读不到身份字段不影响导入 ——
    别反过来用 me 判定登录，否则会出现「日志说登录成功、弹窗却说失败」。
    """

    def run(say: Say) -> dict | None:
        from ..bridge.bestdori_api import BestdoriAccount

        say("登录 Bestdori（后台接口）…")
        acc = BestdoriAccount(settings)
        data = acc.login(username, password)      # 失败会抛 RuntimeError
        say("登录成功，会话已保存到本机（session.json，请勿外传）")
        info = dict(data)
        me = acc.me()
        if me is None:
            say("提示：已登录，但 /api/user/me 没返回身份字段（不影响导入）。")
        else:
            say(f"账号：{me.get('username') or me.get('name') or '(接口未给用户名)'}")
        info["_me"] = me or {}
        return info

    return run


def task_api_import(
    settings: Settings,
    inv: Inventory,
    catalog: Catalog,
    *,
    profile_index: int = 0,
    only_confirmed: bool = False,
) -> Callable[[Say], Any]:
    """后台导入：读云端档案 -> 增量合并 -> 写回。全程 HTTP，不弹浏览器。"""

    def run(say: Say) -> Any:
        from ..bridge.bestdori_api import BestdoriAccount, import_inventory

        acc = BestdoriAccount(settings)
        if acc.me() is None and not acc.restore():
            raise RuntimeError(
                "尚未登录 Bestdori。请先在「同步 Bestdori」页填写账号并登录（会话只需登录一次）。"
            )
        say("已登录，读取云端档案…")
        profiles = acc.fetch_profiles()
        say(f"云端共 {len(profiles)} 份档案，正在合并清单（目标：第 {profile_index} 份）…")
        stats = import_inventory(
            acc,
            inv,
            catalog,
            profile_index=profile_index,
            only_confirmed=only_confirmed,
        )
        say(stats.summary())
        say("完成。可到 Bestdori「我的卡牌」页面核对。")
        return stats

    return run
