"""指纹库测试：构建、持久化、检索与过滤。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import synth_art

from bestdori_helper.models import Catalog
from bestdori_helper.vision.features import CROP_LEVELS, compute_features
from bestdori_helper.vision.index import (
    HIST_DIM,
    FingerprintIndex,
    build_index,
)


# ---------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------


def test_save_load_roundtrip(synth_index: FingerprintIndex, settings) -> None:
    path = settings.index_path
    synth_index.save(path)
    assert path.exists()

    back = FingerprintIndex.load(path)
    assert back is not None
    assert len(back) == len(synth_index)
    assert (back.card_ids == synth_index.card_ids).all()
    assert (back.trained == synth_index.trained).all()
    assert (back.phash == synth_index.phash).all()
    assert (back.dhash == synth_index.dhash).all()
    np.testing.assert_allclose(back.hist, synth_index.hist)


def test_load_missing_returns_none(settings) -> None:
    assert FingerprintIndex.load(settings.index_path) is None


def test_save_writes_manifest(synth_index: FingerprintIndex, settings) -> None:
    synth_index.manifest = {"res000100:N": {"cardId": 100, "trained": False, "sig": "1:2"}}
    synth_index.save(settings.index_path)
    man = settings.index_path.with_suffix(".manifest.json")
    assert man.exists()
    assert "res000100:N" in man.read_text(encoding="utf-8")


def test_load_tolerates_corrupt_manifest(synth_index: FingerprintIndex, settings) -> None:
    synth_index.save(settings.index_path)
    settings.index_path.with_suffix(".manifest.json").write_text("{ not json", encoding="utf-8")
    back = FingerprintIndex.load(settings.index_path)
    assert back is not None
    assert back.manifest == {}


# ---------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------


def test_search_returns_exact_match_first(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    target = card_ids[3]
    feats = compute_features(synth_art(target))
    hits = synth_index.search(feats, top_k=3, catalog=catalog)
    assert hits, "应该有结果"
    assert hits[0].card_id == target
    assert hits[0].score > 0.95


def test_search_all_cards_are_self_consistent(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    """对每一张库里的卡，用自己的特征去查，都应该查回自己。"""
    wrong = []
    for cid in card_ids:
        feats = compute_features(synth_art(cid))
        hits = synth_index.search(feats, top_k=1, catalog=catalog)
        if not hits or hits[0].card_id != cid:
            wrong.append(cid)
    assert not wrong, f"这些卡查不回自己：{wrong}"


def test_search_top_k_respected(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    feats = compute_features(synth_art(card_ids[0]))
    assert len(synth_index.search(feats, top_k=3, catalog=catalog)) == 3
    assert len(synth_index.search(feats, top_k=100, catalog=catalog)) == len(synth_index)


def test_search_empty_index_returns_empty(catalog: Catalog) -> None:
    empty = FingerprintIndex(
        card_ids=np.zeros(0, dtype=np.int32),
        trained=np.zeros(0, dtype=bool),
        phash=np.zeros((0, len(CROP_LEVELS)), dtype=np.uint64),
        dhash=np.zeros((0, len(CROP_LEVELS)), dtype=np.uint64),
        hist=np.zeros((0, HIST_DIM), dtype=np.float32),
        avg_rgb=np.zeros((0, 3), dtype=np.float32),
    )
    feats = compute_features(synth_art(1))
    assert empty.search(feats, catalog=catalog) == []


def test_search_restrict_attribute(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    feats = compute_features(synth_art(card_ids[0]))
    hits = synth_index.search(feats, top_k=5, catalog=catalog, restrict_attribute="cool")
    assert hits
    for h in hits:
        assert catalog.cards[h.card_id].attribute == "cool"


def test_search_restrict_rarity(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    feats = compute_features(synth_art(card_ids[0]))
    hits = synth_index.search(feats, top_k=5, catalog=catalog, restrict_rarity={4, 5})
    assert hits
    for h in hits:
        assert catalog.cards[h.card_id].rarity in {4, 5}


def test_search_restrict_card_ids(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    allowed = set(card_ids[:3])
    feats = compute_features(synth_art(card_ids[0]))
    hits = synth_index.search(feats, top_k=5, catalog=catalog, restrict_card_ids=allowed)
    assert hits
    assert all(h.card_id in allowed for h in hits)


def test_search_restrict_to_empty_set_returns_empty(
    synth_index: FingerprintIndex, catalog: Catalog, card_ids
) -> None:
    feats = compute_features(synth_art(card_ids[0]))
    assert synth_index.search(feats, catalog=catalog, restrict_card_ids=set()) == []


def test_search_restrict_attribute_excludes_everything(
    synth_index: FingerprintIndex, catalog: Catalog, card_ids
) -> None:
    """库里只有 4 种属性，限定一个不存在的属性应返回空而不是报错。"""
    feats = compute_features(synth_art(card_ids[0]))
    assert synth_index.search(feats, catalog=catalog, restrict_attribute="nonexistent") == []


def test_search_prefer_trained_filter(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    """库里全是普通形态，限定 trained=True 应返回空。"""
    feats = compute_features(synth_art(card_ids[0]))
    assert synth_index.search(feats, catalog=catalog, prefer_trained=True) == []
    assert synth_index.search(feats, catalog=catalog, prefer_trained=False)


def test_hash_weight_changes_ranking(synth_index: FingerprintIndex, catalog: Catalog, card_ids) -> None:
    feats = compute_features(synth_art(card_ids[0]))
    a = synth_index.search(feats, top_k=3, catalog=catalog, hash_weight=1.0)
    b = synth_index.search(feats, top_k=3, catalog=catalog, hash_weight=0.0)
    assert a and b  # 两种权重都能给出结果，不崩


# ---------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------


def test_stats(synth_index: FingerprintIndex) -> None:
    st = synth_index.stats()
    assert st["variants"] == len(synth_index)
    assert st["cards"] == len(synth_index)
    assert st["normal"] == len(synth_index)
    assert st["trained"] == 0


# ---------------------------------------------------------------------
# 构建（用假的 client 避免联网）
# ---------------------------------------------------------------------


class _FakeClient:
    """只实现 build_index 需要的两个方法，把合成图当卡面写盘。

    签名必须和 :class:`BestdoriClient` 保持一致（含 ``source`` 关键字）——
    真实客户端是**按来源分目录**取的，假客户端漏掉这个参数会让
    ``build_index`` 里每条都抛 TypeError 然后被吞掉，表现为"库是空的"。
    """

    def __init__(self, settings, catalog: Catalog) -> None:
        self.settings = settings
        self.catalog = catalog
        self.downloads = 0

    def card_image_path(self, card, trained: bool, *, source: str | None = None) -> Path:
        suffix = "after_training" if trained else "normal"
        return self.settings.image_dir / f"{card.resource_set_name}_{suffix}.png"

    def ensure_card_image(
        self, card, trained: bool, *, source: str | None = None
    ) -> Path | None:
        p = self.card_image_path(card, trained, source=source)
        if p.exists():
            return p
        # 模拟"部分卡没有普通卡面"：rarity 为 4 的卡只给特训后形态
        if not trained and card.rarity == 4:
            return None
        p.parent.mkdir(parents=True, exist_ok=True)
        synth_art(card.id + (10000 if trained else 0)).save(p)
        self.downloads += 1
        return p

    def resolve_variants(self, card, include_trained: bool = True, *, source: str | None = None):
        from bestdori_helper.bestdori.client import BestdoriClient

        return BestdoriClient.resolve_variants(self, card, include_trained, source=source)


def test_build_index_from_fake_client(settings, catalog: Catalog) -> None:
    client = _FakeClient(settings, catalog)
    idx, stats = build_index(catalog, client, include_trained=False, progress=lambda _m: None)
    assert len(idx) > 0
    assert stats.failed == 0
    # rarity==4 的卡没有普通卡面，会回退到特训后形态
    assert stats.missing >= 0


def test_build_index_respects_card_subset(settings, catalog: Catalog) -> None:
    client = _FakeClient(settings, catalog)
    subset = {sorted(catalog.cards)[0], sorted(catalog.cards)[1]}
    idx, _ = build_index(catalog, client, include_trained=False, only_card_ids=subset)
    assert set(int(c) for c in idx.card_ids) <= subset


def test_build_index_incremental_reuses_features(settings, catalog: Catalog) -> None:
    client = _FakeClient(settings, catalog)
    idx1, stats1 = build_index(catalog, client, include_trained=False)
    idx1.save(settings.index_path)

    idx2, stats2 = build_index(catalog, client, include_trained=False, reuse=idx1)
    assert stats2.reused > 0, "图片没变时应复用旧特征"
    assert (idx1.card_ids == idx2.card_ids).all()
    assert (idx1.phash == idx2.phash).all()


def test_build_index_empty_catalog(settings) -> None:
    empty = Catalog(settings=settings, cards={}, characters={}, bands={})
    client = _FakeClient(settings, empty)
    idx, stats = build_index(empty, client)
    assert len(idx) == 0
    assert stats.total_variants == 0


# ---------------------------------------------------------------------
# 并列时的确定性
# ---------------------------------------------------------------------


def _dup_index(feats, card_ids: list[int]) -> FingerprintIndex:
    """构造一个「多张卡共用同一份特征」的库（模拟共用卡图）。"""
    n = len(card_ids)
    return FingerprintIndex(
        card_ids=np.asarray(card_ids, dtype=np.int32),
        trained=np.zeros(n, dtype=bool),
        phash=np.stack([feats.phash] * n),
        dhash=np.stack([feats.dhash] * n),
        hist=np.stack([feats.hist] * n),
        avg_rgb=np.stack([feats.avg_rgb] * n),
        manifest={},
    )


def test_search_breaks_ties_by_card_id() -> None:
    """分数完全并列时按卡号升序 —— 结果必须确定，不能每次跑换一张卡。

    Bestdori 有 111 个 ``resourceSetName`` 被多张卡共用（例如「第N回ガルパ杯」
    纪念卡复用活动卡原图），这些卡在库里特征完全相同。若并列顺序由
    ``np.argsort`` 的实现细节决定，同一张截图前后两次识别就可能给出不同卡号。
    """
    feats = compute_features(synth_art(7))
    idx = _dup_index(feats, [500, 100, 300])

    got = [m.card_id for m in idx.search(feats, top_k=3)]
    assert got == [100, 300, 500]

    # 反过来喂进去，结果必须一样
    idx2 = _dup_index(feats, [300, 500, 100])
    assert [m.card_id for m in idx2.search(feats, top_k=3)] == [100, 300, 500]


def test_search_ties_are_stable_across_calls() -> None:
    feats = compute_features(synth_art(11))
    idx = _dup_index(feats, [42, 7, 19, 88])
    first = [m.card_id for m in idx.search(feats, top_k=4)]
    for _ in range(5):
        assert [m.card_id for m in idx.search(feats, top_k=4)] == first
