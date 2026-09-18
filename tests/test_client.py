"""Bestdori 客户端测试：磁盘缓存、卡图下载、并发安全。

全部离线：用假的 httpx 客户端替换真实连接，不联网。
"""

from __future__ import annotations

import threading
import time

from bestdori_helper.bestdori.client import BestdoriClient
from bestdori_helper.config import Settings


# ---------------------------------------------------------------------
# 假的 httpx 客户端
# ---------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: bytes, ctype: str = "image/png") -> None:
        self.content = content
        self.headers = {"content-type": ctype}

    def raise_for_status(self) -> None:
        return None


class _FakeHTTP:
    """每次 GET 都慢一点点，用来放大"并发写同一个文件"的竞态窗口。

    ``delay`` 是关键：不 sleep 的话线程会在写文件前就跑完，竞态复现不出来。
    """

    def __init__(self, content: bytes, ctype: str = "image/png", delay: float = 0.02) -> None:
        self.content = content
        self.ctype = ctype
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def get(self, url: str, **_kw: object) -> _FakeResponse:
        with self._lock:
            self.calls += 1
        time.sleep(self.delay)
        return _FakeResponse(self.content, self.ctype)

    def close(self) -> None:
        return None


def _client_with(settings: Settings, fake: _FakeHTTP) -> tuple[BestdoriClient, object]:
    """构造客户端并替换掉真实连接，返回 (客户端, 原连接) 供调用方关闭。"""
    client = BestdoriClient(settings)
    real = client._client
    client._client = fake  # type: ignore[assignment]
    return client, real


# ---------------------------------------------------------------------
# 基本行为
# ---------------------------------------------------------------------


def test_download_writes_file(settings: Settings) -> None:
    payload = b"\x89PNG\r\n\x1a\n" + b"a" * 2048
    client, real = _client_with(settings, _FakeHTTP(payload))
    try:
        dest = settings.image_dir / "res000001_normal.png"
        assert client.download("https://example.invalid/a.png", dest) is True
        assert dest.read_bytes() == payload
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


def test_download_skips_existing(settings: Settings) -> None:
    payload = b"\x89PNG\r\n\x1a\n" + b"b" * 1024
    fake = _FakeHTTP(payload)
    client, real = _client_with(settings, fake)
    try:
        dest = settings.image_dir / "res000002_normal.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        assert client.download("https://example.invalid/b.png", dest) is False
        assert fake.calls == 0, "文件已存在时不该再发请求"
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


def test_download_rejects_non_image(settings: Settings) -> None:
    """资源不存在时 Bestdori 会返回 SPA 首页（text/html），必须当成"没有"而不是写盘。"""
    client, real = _client_with(settings, _FakeHTTP(b"<!DOCTYPE html>", ctype="text/html"))
    try:
        dest = settings.image_dir / "res000003_normal.png"
        assert client.download("https://example.invalid/c.png", dest) is False
        assert not dest.exists()
        assert list(settings.image_dir.glob("*.part")) == []
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------
# 并发安全（回归）
# ---------------------------------------------------------------------


def test_concurrent_download_same_dest_does_not_collide(settings: Settings) -> None:
    """多张卡共用同一个 ``resourceSetName``，会并发下载**同一个目标路径**。

    旧实现把临时文件固定叫 ``dest.part``：两个线程同时写它，Windows 上后到
    的那个抛 ``PermissionError``（Errno 13），而这个异常不被 ``httpx.HTTPError``
    捕获，会一路冒到上层被当成"卡图解析失败"——卡图既没落盘、也没写
    ``.missing`` 标记，**静默丢失**。实测全库构建时这类失败成片出现。

    修复方式：临时文件名带上进程号 + 线程号，并容忍"别的线程已经写好了"。
    这里用 8 个线程同时下载同一个 dest 来复现。
    """
    payload = b"\x89PNG\r\n\x1a\n" + b"c" * 8192
    client, real = _client_with(settings, _FakeHTTP(payload, delay=0.02))
    try:
        dest = settings.image_dir / "res000004_normal.png"
        errors: list[BaseException] = []

        def work() -> None:
            try:
                client.download("https://example.invalid/d.png", dest)
            except BaseException as e:  # noqa: BLE001 —— 要把任何异常都收集起来
                errors.append(e)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"并发下载不该抛异常，实际：{errors!r}"
        assert dest.read_bytes() == payload, "落盘内容必须完整"
        leftovers = list(settings.image_dir.glob("*.part"))
        assert leftovers == [], f"不该留下临时文件：{leftovers}"
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


def test_concurrent_download_distinct_dests(settings: Settings) -> None:
    """不同目标并行下载互不干扰（并发度本身不能有副作用）。"""
    payload = b"\x89PNG\r\n\x1a\n" + b"d" * 4096
    client, real = _client_with(settings, _FakeHTTP(payload, delay=0.01))
    try:
        dests = [settings.image_dir / f"res00010{i}_normal.png" for i in range(12)]
        errors: list[BaseException] = []

        def work(d: object) -> None:
            try:
                client.download("https://example.invalid/e.png", d)  # type: ignore[arg-type]
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=work, args=(d,)) for d in dests]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        for d in dests:
            assert d.read_bytes() == payload
        assert list(settings.image_dir.glob("*.part")) == []
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------
# 卡图来源：thumb（180x180 方形缩略图） vs original（1334x1002 原图）
# ---------------------------------------------------------------------


def _card(card_id: int = 158, rs: str = "res013006"):
    from bestdori_helper.models import Card

    return Card(id=card_id, character_id=1, rarity=4, attribute="cool",
                resource_set_name=rs)


def test_card_image_path_follows_source(settings: Settings) -> None:
    client, real = _client_with(settings, _FakeHTTP(b"x"))
    try:
        card = _card()
        thumb = client.card_image_path(card, False, source="thumb")
        orig = client.card_image_path(card, False, source="original")
        assert thumb.parent == settings.thumb_dir
        assert orig.parent == settings.image_dir
        assert thumb != orig
        assert thumb.name == orig.name == "res013006_normal.png"
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


def test_card_image_path_defaults_to_settings_source(settings: Settings) -> None:
    settings.image_source = "thumb"
    client, real = _client_with(settings, _FakeHTTP(b"x"))
    try:
        assert client.card_image_path(_card(), False).parent == settings.thumb_dir
        settings.image_source = "original"
        assert client.card_image_path(_card(), False).parent == settings.image_dir
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


class _UrlRecorder(_FakeHTTP):
    """记录被请求的 URL，用来断言实际用的是哪一套资源地址。"""

    def __init__(self) -> None:
        super().__init__(b"\x89PNG\r\n\x1a\n" + b"z" * 512, delay=0.0)
        self.urls: list[str] = []

    def get(self, url: str, **_kw: object) -> _FakeResponse:
        self.urls.append(url)
        return super().get(url, **_kw)


def test_ensure_card_image_requests_thumb_url(settings: Settings) -> None:
    fake = _UrlRecorder()
    client, real = _client_with(settings, fake)
    try:
        p = client.ensure_card_image(_card(), False, source="thumb")
        assert p is not None and p.parent == settings.thumb_dir
        assert fake.urls == [
            "https://bestdori.com/assets/cn/thumb/chara/"
            "card00003_rip/res013006_normal.png"
        ]
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


def test_ensure_card_image_requests_original_url(settings: Settings) -> None:
    fake = _UrlRecorder()
    client, real = _client_with(settings, fake)
    try:
        p = client.ensure_card_image(_card(), False, source="original")
        assert p is not None and p.parent == settings.image_dir
        assert fake.urls == [
            "https://bestdori.com/assets/cn/characters/resourceset/"
            "res013006_rip/card_normal.png"
        ]
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


def test_missing_marker_is_per_source(settings: Settings) -> None:
    """负缓存标记必须跟着来源走，否则一个来源的 404 会把另一个也误标掉。"""
    client, real = _client_with(settings, _FakeHTTP(b"<html>", ctype="text/html"))
    try:
        card = _card()
        assert client.ensure_card_image(card, False, source="thumb") is None
        thumb_marker = client.card_image_path(card, False, source="thumb").with_suffix(".missing")
        orig_marker = client.card_image_path(card, False, source="original").with_suffix(".missing")
        assert thumb_marker.exists()
        assert not orig_marker.exists()
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------
# 卡图来源：thumb（180x180 方形缩略图） vs original（1334x1002 原图）
# ---------------------------------------------------------------------


def _card(card_id: int = 158, rs: str = "res013006"):
    from bestdori_helper.models import Card

    return Card(id=card_id, character_id=1, rarity=4, attribute="cool",
                resource_set_name=rs)


def test_card_image_path_follows_source(settings: Settings) -> None:
    client, real = _client_with(settings, _FakeHTTP(b"x"))
    try:
        card = _card()
        thumb = client.card_image_path(card, False, source="thumb")
        orig = client.card_image_path(card, False, source="original")
        assert thumb.parent == settings.thumb_dir
        assert orig.parent == settings.image_dir
        assert thumb != orig
        assert thumb.name == orig.name == "res013006_normal.png"
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


def test_card_image_path_defaults_to_settings_source(settings: Settings) -> None:
    settings.image_source = "thumb"
    client, real = _client_with(settings, _FakeHTTP(b"x"))
    try:
        assert client.card_image_path(_card(), False).parent == settings.thumb_dir
        settings.image_source = "original"
        assert client.card_image_path(_card(), False).parent == settings.image_dir
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


class _UrlRecorder(_FakeHTTP):
    """记录被请求的 URL，用来断言实际用的是哪一套资源地址。"""

    def __init__(self) -> None:
        super().__init__(b"\x89PNG\r\n\x1a\n" + b"z" * 512, delay=0.0)
        self.urls: list[str] = []

    def get(self, url: str, **_kw: object) -> _FakeResponse:
        self.urls.append(url)
        return super().get(url, **_kw)


def test_ensure_card_image_requests_thumb_url(settings: Settings) -> None:
    fake = _UrlRecorder()
    client, real = _client_with(settings, fake)
    try:
        p = client.ensure_card_image(_card(), False, source="thumb")
        assert p is not None and p.parent == settings.thumb_dir
        assert fake.urls == [
            "https://bestdori.com/assets/cn/thumb/chara/"
            "card00003_rip/res013006_normal.png"
        ]
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


def test_ensure_card_image_requests_original_url(settings: Settings) -> None:
    fake = _UrlRecorder()
    client, real = _client_with(settings, fake)
    try:
        p = client.ensure_card_image(_card(), False, source="original")
        assert p is not None and p.parent == settings.image_dir
        assert fake.urls == [
            "https://bestdori.com/assets/cn/characters/resourceset/"
            "res013006_rip/card_normal.png"
        ]
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]


def test_missing_marker_is_per_source(settings: Settings) -> None:
    """负缓存标记必须跟着来源走，否则一个来源的 404 会把另一个也误标掉。"""
    client, real = _client_with(settings, _FakeHTTP(b"<html>", ctype="text/html"))
    try:
        card = _card()
        assert client.ensure_card_image(card, False, source="thumb") is None
        thumb_marker = client.card_image_path(card, False, source="thumb").with_suffix(".missing")
        orig_marker = client.card_image_path(card, False, source="original").with_suffix(".missing")
        assert thumb_marker.exists()
        assert not orig_marker.exists()
    finally:
        client.close()
        real.close()  # type: ignore[attr-defined]
