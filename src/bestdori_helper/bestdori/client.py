"""Bestdori 公开 API 客户端。

已实测确认的接口::

    GET /api/cards/all.5.json        全部卡牌（约 2468 张）
    GET /api/characters/all.2.json   全部角色
    GET /api/bands/all.1.json        全部乐队
    GET /api/explorer/{server}/assets/_info.json   资源索引

注意：``/api/cards/{id}.json`` 这种单卡路径在 Bestdori 上**不存在**（会返回
SPA 首页），必须使用 ``all.N.json`` 全量接口后本地索引。资源图片必须带
User-Agent，否则 nginx 会返回 403 或首页 HTML。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from ..config import DEFAULT_UA, Settings
from ..models import Band, Card, Catalog, Character

log = logging.getLogger(__name__)

BASE = "https://bestdori.com"
#: Bestdori 用版本号后缀做缓存失效，版本升级时旧路径会 404
CARDS_ENDPOINT = "/api/cards/all.5.json"
CHARACTERS_ENDPOINT = "/api/characters/all.2.json"
BANDS_ENDPOINT = "/api/bands/all.1.json"


class BestdoriClient:
    """同步 HTTP 客户端，带磁盘缓存。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.settings.ensure_dirs()
        self._client = httpx.Client(
            base_url=BASE,
            timeout=self.settings.request_timeout,
            headers={"User-Agent": DEFAULT_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            follow_redirects=True,
        )

    # ---- 生命周期 ----------------------------------------------------

    def __enter__(self) -> BestdoriClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ---- 原始请求 ----------------------------------------------------

    def get_json(self, path: str) -> Any:
        r = self._client.get(path, headers={"Accept": "application/json"})
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "json" not in ctype:
            raise RuntimeError(
                f"{path} 未返回 JSON（content-type={ctype!r}）。"
                "接口版本可能已变更，请检查 CARDS_ENDPOINT 常量。"
            )
        return r.json()

    def download(self, url: str, dest: Path) -> bool:
        """下载到 dest；已存在则跳过。返回是否新下载。

        临时文件名必须**带上当前线程的唯一后缀**：Bestdori 上有 111 个
        ``resourceSetName`` 被多张卡共用（最多 3 张卡同一个），并发下载时
        两个线程会写同一个 ``dest.part``，Windows 上后到的那个直接
        ``Permission denied``，卡图就悄悄丢了。实测全库构建时这类失败会成片出现。

        ``replace`` 同样可能失败（目标正被别的线程占用），所以带退避重试；
        无论成败都要清掉自己的临时文件，否则缓存目录会攒下一堆 ``.part``。
        """
        if dest.exists() and dest.stat().st_size > 0:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp: Path | None = None
        try:
            r = self._client.get(url)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "image" not in ctype:
                # 不存在该变体（例如未特训卡面的卡）
                return False
            tmp = dest.with_name(f"{dest.name}.{os.getpid():x}-{threading.get_ident():x}.part")
            tmp.write_bytes(r.content)

            for attempt in range(4):
                try:
                    tmp.replace(dest)
                    tmp = None
                    return True
                except OSError:
                    if dest.exists() and dest.stat().st_size > 0:
                        # 另一个线程已经先把同名文件写好了，等价于成功
                        log.debug("同名卡图已被其他线程写入：%s", dest.name)
                        return True
                    time.sleep(0.05 * (attempt + 1))

            log.warning("卡图落盘失败（临时文件被占用）：%s", dest.name)
            return False
        except httpx.HTTPError as e:
            log.debug("下载失败 %s: %s", url, e)
            return False
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    # ---- 数据快照 ----------------------------------------------------

    def _cached_or_fetch(self, path: str, cache: Path, refresh: bool) -> Any:
        if cache.exists() and not refresh:
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("缓存损坏，重新拉取：%s", cache)
        log.info("拉取 %s", path)
        data = self.get_json(path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def fetch_catalog(self, refresh: bool = False, progress: Callable[[str], None] | None = None) -> Catalog:
        """拉取卡牌 + 角色 + 乐队，组装成 Catalog。"""
        s = self.settings
        say = progress or (lambda _m: None)

        say("同步卡牌数据…")
        raw_cards = self._cached_or_fetch(CARDS_ENDPOINT, s.cards_json, refresh)
        say(f"  卡牌 {len(raw_cards)} 张")

        say("同步角色数据…")
        raw_chars = self._cached_or_fetch(CHARACTERS_ENDPOINT, s.characters_json, refresh)
        say(f"  角色 {len(raw_chars)} 名")

        say("同步乐队数据…")
        raw_bands = self._cached_or_fetch(BANDS_ENDPOINT, s.bands_json, refresh)
        say(f"  乐队 {len(raw_bands)} 支")

        return Catalog(
            settings=s,
            cards={int(k): Card.from_api(int(k), v) for k, v in raw_cards.items()},
            characters={int(k): Character.from_api(int(k), v) for k, v in raw_chars.items()},
            bands={int(k): Band.from_api(int(k), v) for k, v in raw_bands.items()},
        )

    # ---- 卡面图片 ----------------------------------------------------

    def card_image_path(self, card: Card, trained: bool, *, source: str | None = None) -> Path:
        """卡图在本地缓存里的路径。

        ``source`` 决定用哪一套 Bestdori 资源（默认取 ``settings.image_source``）：

        - ``thumb``      -> ``cache/thumbs/{server}/``   180x180 方形缩略图
        - ``original``   -> ``cache/images/{server}/``   1334x1002 横向原图

        两套分开存，因为**画幅完全不同**，混在一起会让指纹库失去可比性。
        """
        src = source or self.settings.image_source
        suffix = "after_training" if trained else "normal"
        base = self.settings.thumb_dir if src == "thumb" else self.settings.image_dir
        return base / f"{card.resource_set_name}_{suffix}.png"

    def _missing_marker(self, card: Card, trained: bool, source: str | None = None) -> Path:
        return self.card_image_path(card, trained, source=source).with_suffix(".missing")

    def _art_url(self, card: Card, trained: bool, source: str) -> str:
        server = self.settings.server.value
        return card.thumb_url(server, trained) if source == "thumb" else card.image_url(server, trained)

    def ensure_card_image(
        self, card: Card, trained: bool, *, source: str | None = None
    ) -> Path | None:
        """确保卡面在本地缓存中，返回路径；资源不存在则返回 None。

        注意：约 6.5% 的卡（生日卡 ``birthday``、部分 ``campaign`` 卡）
        **没有** ``card_normal.png``，它们的卡面只存在于 ``card_after_training.png``。
        这类卡由 :meth:`resolve_variants` 负责回退。
        """
        src = source or self.settings.image_source
        dest = self.card_image_path(card, trained, source=src)
        if dest.exists() and dest.stat().st_size > 0:
            return dest

        marker = self._missing_marker(card, trained, src)
        if marker.exists():
            return None  # 已确认过该资源不存在，不再重复请求

        url = self._art_url(card, trained, src)
        if self.download(url, dest) and dest.exists():
            return dest

        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("missing", encoding="utf-8")
        return None

    def resolve_variants(
        self, card: Card, include_trained: bool = True, *, source: str | None = None
    ) -> list[tuple[Path, bool]]:
        """列出该卡**实际存在**的卡面变体。

        返回 ``[(图片路径, 是否为特训后形态), ...]``，至少一项为空时说明
        该卡在 Bestdori 上没有卡图。

        - 普通卡：``[(normal, False)]``，3★ 以上再加 ``(after_training, True)``
        - 生日卡 / 部分活动卡：只有 ``[(after_training, True)]``
        """
        out: list[tuple[Path, bool]] = []

        p_normal = self.ensure_card_image(card, False, source=source)
        if p_normal is not None:
            out.append((p_normal, False))

        if include_trained and card.rarity >= 3:
            p_trained = self.ensure_card_image(card, True, source=source)
            if p_trained is not None:
                out.append((p_trained, True))

        if not out:
            # 没有普通卡面 —— 生日卡等只有特训后形态
            p_trained = self.ensure_card_image(card, True, source=source)
            if p_trained is not None:
                out.append((p_trained, True))

        return out

    def has_trained_art(self, card: Card, *, source: str | None = None) -> bool:
        """判断该卡是否存在特训后卡面。"""
        if card.rarity < 3:
            return False
        return self.ensure_card_image(card, True, source=source) is not None
