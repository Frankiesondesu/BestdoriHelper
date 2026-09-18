"""本地 Web UI（FastAPI）。

启动::

    bdh serve            # 默认 http://127.0.0.1:8787

页面提供：卡面截图拖拽上传 → 识别结果可视化 → 人工确认 → 导出 / 同步 Bestdori。
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ..bestdori.client import BestdoriClient
from ..bridge.bestdori_import import build_import_plan
from ..config import ATTRIBUTE_COLOR, LANGUAGES, Server, Settings
from ..inventory.export import to_csv, to_json, to_markdown
from ..inventory.store import Inventory
from ..models import OwnedCard
from ..vision.index import FingerprintIndex, build_index
from ..vision.ocr import get_ocr_engine
from ..vision.recognize import recognize_image
from ..vision.verify import build_verifier

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass
class AppState:
    """进程内共享状态。"""

    settings: Settings
    client: BestdoriClient | None = None
    catalog = None
    index: FingerprintIndex | None = None
    inventory: Inventory | None = None
    #: 后台任务进度
    job: dict = field(default_factory=lambda: {"running": False, "kind": "", "message": "", "error": ""})
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def client_or_new(self) -> BestdoriClient:
        if self.client is None:
            self.client = BestdoriClient(self.settings)
        return self.client

    def catalog_or_load(self):
        if self.catalog is None:
            self.catalog = self.client_or_new().fetch_catalog()
        return self.catalog

    def inventory_or_load(self) -> Inventory:
        if self.inventory is None:
            self.inventory = Inventory.load(self.settings.inventory_path)
        return self.inventory

    def index_or_load(self) -> FingerprintIndex | None:
        if self.index is None:
            self.index = FingerprintIndex.load(self.settings.index_path)
        return self.index


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    state = AppState(settings=settings)

    app = FastAPI(title="BestdoriHelper", docs_url="/api/docs")

    # ---- 页面 --------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    # ---- 状态 --------------------------------------------------------

    @app.get("/api/state")
    def api_state() -> dict:
        idx = state.index_or_load()
        inv = state.inventory_or_load()
        catalog = state.catalog_or_load()  # 本地缓存，加载很快
        return {
            "server": settings.server.value,
            "serverName": {"jp": "日服", "cn": "国服", "tw": "台服", "en": "国际服", "kr": "韩服"}[settings.server.value],
            "lang": settings.lang,
            "langName": LANGUAGES.get(settings.lang or 0, ""),
            "home": str(settings.home),
            "catalogLoaded": True,
            "catalogCount": len(catalog.cards),
            "index": idx.stats() if idx else None,
            "inventoryCount": len(inv),
            "inventoryStats": inv.stats(catalog).to_dict(),
            "job": state.job,
            "attributeColors": ATTRIBUTE_COLOR,
        }

    @app.get("/api/job")
    def api_job() -> dict:
        return state.job

    # ---- 数据同步 ----------------------------------------------------

    @app.post("/api/sync")
    def api_sync(background: BackgroundTasks) -> dict:
        if state.job["running"]:
            raise HTTPException(409, "已有任务在执行")

        def work() -> None:
            state.job.update(running=True, kind="sync", message="同步中…", error="")
            try:
                state.catalog = state.client_or_new().fetch_catalog(
                    refresh=True, progress=lambda m: state.job.update(message=m)
                )
                state.job["message"] = f"同步完成，共 {len(state.catalog.cards)} 张卡"
            except Exception as e:  # noqa: BLE001
                log.exception("同步失败")
                state.job["error"] = str(e)
                state.job["message"] = "同步失败"
            finally:
                state.job["running"] = False

        background.add_task(work)
        return {"ok": True}

    @app.post("/api/index/build")
    def api_index_build(
        background: BackgroundTasks,
        include_trained: bool = Form(True),
        limit: int = Form(0),
    ) -> dict:
        if state.job["running"]:
            raise HTTPException(409, "已有任务在执行")

        def work() -> None:
            state.job.update(running=True, kind="index", message="构建指纹库…", error="")
            try:
                client = state.client_or_new()
                catalog = state.catalog_or_load()
                only = None
                if limit > 0:
                    only = set(sorted(catalog.cards)[:limit])
                old = FingerprintIndex.load(settings.index_path)
                idx, stats = build_index(
                    catalog, client,
                    include_trained=include_trained,
                    only_card_ids=only,
                    reuse=old,
                    progress=lambda m: state.job.update(message=m),
                )
                idx.save(settings.index_path)
                state.index = idx
                state.job["message"] = stats.summary()
            except Exception as e:  # noqa: BLE001
                log.exception("构建指纹库失败")
                state.job["error"] = str(e)
                state.job["message"] = "构建失败"
            finally:
                state.job["running"] = False

        background.add_task(work)
        return {"ok": True}

    # ---- 识别 --------------------------------------------------------

    @app.post("/api/scan")
    async def api_scan(
        files: list[UploadFile] = File(...),
        mode: str = Form("auto"),
        rows: int = Form(0),
        cols: int = Form(0),
        inset: float = Form(0.0),
        use_ocr: bool = Form(False),
        use_attribute_hint: bool = Form(True),
        absorb: bool = Form(True),
        include_ambiguous: bool = Form(True),
        min_confidence: float = Form(0.0),
    ) -> JSONResponse:
        idx = state.index_or_load()
        if idx is None or len(idx) == 0:
            raise HTTPException(400, "指纹库为空，请先构建指纹库")
        catalog = state.catalog_or_load()
        ocr = get_ocr_engine(prefer=use_ocr)

        results = []
        total = {"added": 0, "skipped": 0, "pending": 0}
        inv = state.inventory_or_load()
        verifier = build_verifier(catalog)

        with tempfile.TemporaryDirectory() as td:
            for uf in files:
                suffix = Path(uf.filename or "upload.png").suffix.lower()
                if suffix not in IMAGE_SUFFIXES:
                    continue
                tmp = Path(td) / (uf.filename or "upload.png")
                tmp.write_bytes(await uf.read())

                res = recognize_image(
                    tmp, idx, catalog,
                    mode=mode,
                    rows=rows or None,
                    cols=cols or None,
                    inset=inset,
                    ocr=ocr,
                    use_ocr=use_ocr and ocr.available,
                    use_attribute_hint=use_attribute_hint,
                    verifier=verifier,
                )
                results.append(res.to_dict(catalog))

                if absorb:
                    delta = inv.absorb_recognition(
                        res, min_confidence=min_confidence, include_ambiguous=include_ambiguous
                    )
                    for k in total:
                        total[k] += delta[k]

        if absorb:
            inv.save()

        return JSONResponse({
            "results": results,
            "absorbed": total if absorb else None,
            "inventoryCount": len(inv),
        })

    # ---- 清单 --------------------------------------------------------

    @app.get("/api/inventory")
    def api_inventory() -> dict:
        catalog = state.catalog_or_load()
        inv = state.inventory_or_load()
        items = []
        for oc in inv.all():
            info = catalog.describe(oc.card_id, oc.trained)
            items.append({**info, "confidence": round(oc.confidence, 4),
                          "matchedBy": oc.matched_by, "confirmed": oc.confirmed})
        return {
            "count": len(items),
            "stats": inv.stats(catalog).to_dict(),
            "items": items,
            "updatedAt": inv.updated_at,
        }

    @app.post("/api/inventory/confirm")
    def api_inventory_confirm(payload: dict) -> dict:
        inv = state.inventory_or_load()
        cid = int(payload.get("cardId", 0))
        trained = bool(payload.get("trained", False))
        oc = inv.get(cid, trained)
        if oc is None:
            raise HTTPException(404, "清单里没有这张卡")
        oc.confirmed = True
        inv.save()
        return {"ok": True}

    @app.post("/api/inventory/remove")
    def api_inventory_remove(payload: dict) -> dict:
        inv = state.inventory_or_load()
        n = inv.remove(int(payload.get("cardId", 0)),
                       None if payload.get("trained") is None else bool(payload["trained"]))
        inv.save()
        return {"ok": True, "removed": n}

    @app.post("/api/inventory/clear")
    def api_inventory_clear() -> dict:
        inv = state.inventory_or_load()
        n = inv.clear()
        inv.save()
        return {"ok": True, "removed": n}

    @app.get("/api/export")
    def api_export(fmt: str = "markdown") -> Response:
        catalog = state.catalog_or_load()
        inv = state.inventory_or_load()
        if fmt == "csv":
            return Response(to_csv(inv, catalog), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="cards.csv"'})
        if fmt == "json":
            return Response(to_json(inv, catalog), media_type="application/json; charset=utf-8")
        return Response(to_markdown(inv, catalog), media_type="text/markdown; charset=utf-8")

    # ---- Bestdori 同步 -----------------------------------------------

    @app.post("/api/import/plan")
    def api_import_plan(payload: dict | None = None) -> dict:
        catalog = state.catalog_or_load()
        inv = state.inventory_or_load()
        skip = bool((payload or {}).get("confirmedOnly", False))
        plan = build_import_plan(inv, catalog, remote_keys=None, skip_unconfirmed=skip)
        return {"summary": plan.summary(), **plan.to_dict()}

    @app.post("/api/import/run")
    def api_import_run(payload: dict) -> dict:
        """启动浏览器自动化导入。需要在有图形界面的机器上运行。"""
        from ..bridge.bestdori_import import PlaywrightImporter

        catalog = state.catalog_or_load()
        inv = state.inventory_or_load()
        plan = build_import_plan(inv, catalog, remote_keys=None,
                                 skip_unconfirmed=bool(payload.get("confirmedOnly", False)))
        if not len(plan):
            return {"ok": True, "message": "没有需要导入的卡牌"}

        importer = PlaywrightImporter(headless=False)
        outcome = importer.run(plan, apply=bool(payload.get("apply", False)),
                               progress=lambda m: state.job.update(message=m))
        return {"ok": not outcome.failed, "summary": outcome.summary(),
                "failed": outcome.failed}

    # ---- 卡图 --------------------------------------------------------

    @app.get("/api/card-image/{card_id}")
    def api_card_image(card_id: int, trained: int = 0) -> Response:
        catalog = state.catalog_or_load()
        card = catalog.card(card_id)
        if card is None:
            raise HTTPException(404, "没有这张卡")
        path = state.client_or_new().ensure_card_image(card, bool(trained))
        if path is None or not path.exists():
            path = state.client_or_new().ensure_card_image(card, not bool(trained))
        if path is None or not path.exists():
            raise HTTPException(404, "该卡没有卡图")
        return FileResponse(path, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/api/cards")
    def api_cards(q: str = "", limit: int = 60) -> dict:
        """卡牌搜索（供人工修正候选时使用）。"""
        catalog = state.catalog_or_load()
        q = q.strip().lower()
        out = []
        for card in catalog.cards.values():
            info = catalog.describe(card.id, False)
            hay = f"{info['title']} {info['character']} {info['band']} {card.id}".lower()
            if q and q not in hay:
                continue
            out.append({**info, "characterId": card.character_id})
            if len(out) >= limit:
                break
        return {"items": out}

    @app.get("/api/servers")
    def api_servers() -> dict:
        return {"current": settings.server.value, "options": [s.value for s in Server]}

    return app


def serve(settings: Settings | None = None, port: int = 8787) -> None:
    import uvicorn

    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
