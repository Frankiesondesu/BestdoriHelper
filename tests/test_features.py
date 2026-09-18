"""特征提取测试：pHash / dHash / popcount / 直方图。"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import synth_art
from PIL import Image

from bestdori_helper.vision.features import (
    CROP_LEVELS,
    HASH_SIZE,
    HIST_BINS,
    HIST_GRID,
    best_hash_similarity,
    color_similarity,
    compute_features,
    dhash_bits,
    phash_bits,
    popcount_u64,
)


# ---------------------------------------------------------------------
# popcount
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [(0b0, 0), (0b1, 1), (0b1011, 3), (0b11111111, 8), (0xFFFFFFFFFFFFFFFF, 64)],
)
def test_popcount_values(value: int, expected: int) -> None:
    got = popcount_u64(np.asarray([value], dtype=np.uint64))
    assert int(got[0]) == expected


def test_popcount_preserves_1d_shape() -> None:
    got = popcount_u64(np.asarray([0b1011, 0b1111], dtype=np.uint64))
    assert got.shape == (2,)


def test_popcount_preserves_2d_shape() -> None:
    """回归：早期实现用 reshape(-1, 8) 把多维拍平了，导致索引维度对不上。"""
    a = np.asarray([[0b1, 0b11], [0b111, 0b1111]], dtype=np.uint64)
    got = popcount_u64(a)
    assert got.shape == (2, 2)
    assert got.tolist() == [[1, 2], [3, 4]]


def test_popcount_preserves_3d_shape() -> None:
    a = np.ones((4, 3, 2), dtype=np.uint64)
    got = popcount_u64(a)
    assert got.shape == (4, 3, 2)
    assert (got == 1).all()


def test_popcount_empty() -> None:
    got = popcount_u64(np.zeros(0, dtype=np.uint64))
    assert got.shape == (0,)


# ---------------------------------------------------------------------
# pHash / dHash
# ---------------------------------------------------------------------


def _gray(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float64)


def test_phash_is_deterministic() -> None:
    img = synth_art(1)
    assert phash_bits(_gray(img)) == phash_bits(_gray(img))


def test_phash_same_image_distance_zero() -> None:
    h = phash_bits(_gray(synth_art(7)))
    assert popcount_u64(np.asarray([np.uint64(h ^ h)], dtype=np.uint64))[0] == 0


def test_phash_different_images_differ() -> None:
    a = phash_bits(_gray(synth_art(1)))
    b = phash_bits(_gray(synth_art(2)))
    dist = int(popcount_u64(np.asarray([np.uint64(a ^ b)], dtype=np.uint64))[0])
    assert dist > 0, "不同卡面的 pHash 不应该完全相同"


def test_phash_bit_width() -> None:
    h = phash_bits(_gray(synth_art(3)))
    assert 0 <= h < (1 << (HASH_SIZE * HASH_SIZE))


def test_dhash_detects_horizontal_step() -> None:
    """左黑右白：只有边界附近的相邻比较会翻转，其余相等 → 部分位置 1。"""
    arr = np.zeros((32, 32), dtype=np.uint8)
    arr[:, 16:] = 255
    h = dhash_bits(arr.astype(np.float64))
    ones = int(popcount_u64(np.asarray([np.uint64(h)], dtype=np.uint64))[0])
    assert 0 < ones < HASH_SIZE * HASH_SIZE, "边界应该只翻转一部分位，而不是全部或没有"


def test_dhash_step_direction_flips_bits() -> None:
    """把左右明暗对调，dHash 应该基本按位取反。

    不要求 64 位全翻转 —— 重采样后边界附近会有几处两侧相等，
    这些位置在两个方向下都是 0，不会翻转。
    """
    left_dark = np.zeros((32, 32), dtype=np.uint8)
    left_dark[:, 16:] = 255
    left_bright = np.zeros((32, 32), dtype=np.uint8)
    left_bright[:, :16] = 255

    a = dhash_bits(left_dark.astype(np.float64))
    b = dhash_bits(left_bright.astype(np.float64))
    total = HASH_SIZE * HASH_SIZE
    dist = int(popcount_u64(np.asarray([np.uint64(a ^ b)], dtype=np.uint64))[0])
    assert dist >= total * 0.7, f"明暗对调后大部分位应翻转，实际只翻了 {dist}/{total}"


def test_dhash_flat_image_is_zero() -> None:
    flat = np.full((32, 32), 128.0)
    assert dhash_bits(flat) == 0


# ---------------------------------------------------------------------
# compute_features
# ---------------------------------------------------------------------


def test_compute_features_shapes() -> None:
    f = compute_features(synth_art(5))
    assert f.n_crops == len(CROP_LEVELS)
    assert f.phash.shape == (len(CROP_LEVELS),)
    assert f.dhash.shape == (len(CROP_LEVELS),)
    assert f.hist.shape == (HIST_GRID * HIST_GRID * 3 * HIST_BINS,)
    assert f.avg_rgb.shape == (3,)


def test_histogram_is_l2_normalized() -> None:
    """分块直方图整体做 L2 归一化，交给余弦相似度用。"""
    f = compute_features(synth_art(9))
    assert float((f.hist ** 2).sum()) == pytest.approx(1.0, abs=1e-4)


def test_avg_rgb_in_range() -> None:
    f = compute_features(synth_art(11))
    assert ((f.avg_rgb >= 0) & (f.avg_rgb <= 1)).all()


def test_features_accept_grayscale_input() -> None:
    """灰度图应该也能处理（内部会 convert("RGB")）。"""
    f = compute_features(synth_art(13).convert("L"))
    assert f.hist.shape == (HIST_GRID * HIST_GRID * 3 * HIST_BINS,)


# ---------------------------------------------------------------------
# 相似度
# ---------------------------------------------------------------------


def test_identical_image_similarity_is_one() -> None:
    img = synth_art(21)
    f = compute_features(img)
    db_p = f.phash[None, :]
    db_d = f.dhash[None, :]
    sim, dist = best_hash_similarity(f, db_p, db_d)
    assert float(sim[0]) == pytest.approx(1.0)
    assert float(dist[0]) == pytest.approx(0.0)


def test_different_images_have_lower_similarity() -> None:
    a = compute_features(synth_art(31))
    b = compute_features(synth_art(32))
    sim, _ = best_hash_similarity(a, b.phash[None, :], b.dhash[None, :])
    assert float(sim[0]) < 1.0


def test_best_hash_similarity_picks_best_crop_combination() -> None:
    """查询特征与库中不同裁切档比对时应取最优，而不是只比第一档。"""
    a = compute_features(synth_art(41))
    b = compute_features(synth_art(42))
    db_p = np.stack([a.phash, b.phash])
    db_d = np.stack([a.dhash, b.dhash])
    sim, _ = best_hash_similarity(a, db_p, db_d)
    assert int(np.argmax(sim)) == 0


def test_best_hash_similarity_empty_index() -> None:
    f = compute_features(synth_art(51))
    sim, dist = best_hash_similarity(
        f, np.zeros((0, len(CROP_LEVELS)), dtype=np.uint64), np.zeros((0, len(CROP_LEVELS)), dtype=np.uint64)
    )
    assert sim.shape == (0,) and dist.shape == (0,)


def test_color_similarity_identical() -> None:
    f = compute_features(synth_art(61))
    got = color_similarity(f.hist, f.hist[None, :])
    assert float(got[0]) == pytest.approx(1.0, abs=1e-5)


def test_color_similarity_empty() -> None:
    f = compute_features(synth_art(62))
    got = color_similarity(f.hist, np.zeros((0, HIST_GRID * HIST_GRID * 3 * HIST_BINS), dtype=np.float32))
    assert got.shape == (0,)


def test_similarity_is_bounded() -> None:
    a = compute_features(synth_art(71))
    b = compute_features(synth_art(72))
    sim, dist = best_hash_similarity(a, b.phash[None, :], b.dhash[None, :])
    assert 0.0 <= float(sim[0]) <= 1.0
    assert 0.0 <= float(dist[0]) <= 1.0
