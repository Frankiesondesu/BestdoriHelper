"""图像特征提取：感知哈希（pHash / dHash）+ 颜色直方图。

纯 numpy 实现，不依赖 scipy / imagehash，安装更轻。

为什么用「多档中心裁切」
------------------------
游戏内缩略图与 Bestdori 原图的**取景/画幅并不完全一致**：游戏里会叠加边框、
星级、属性图标，某些界面还会裁掉两侧。为对这种差异鲁棒，我们对每张图抽取
四档中心裁切指纹：

    crop 0: 全幅        (0.000 ~ 1.000)
    crop 1: 中心 90%    (0.050 ~ 0.950)
    crop 2: 中心 75%    (0.125 ~ 0.875)
    crop 3: 中心 60%    (0.200 ~ 0.800)

查询时同样抽取 4 档，与指纹库做全组合比对取最优。即使游戏缩略图裁掉了边缘
或带了边框，也总有一档能对上。

颜色特征用 **4x4 网格分块直方图**而不是全局直方图 —— 全局直方图丢掉了
"颜色在哪"，同角色同属性的卡几乎分不开。见 ``HIST_GRID``。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

#: 中心裁切档位 (left, top, right, bottom)，取值为 0~1 的比例
CROP_LEVELS: tuple[tuple[float, float, float, float], ...] = (
    (0.00, 0.00, 1.00, 1.00),
    (0.05, 0.05, 0.95, 0.95),
    (0.125, 0.125, 0.875, 0.875),
    (0.20, 0.20, 0.80, 0.80),
)

#: pHash 内部工作尺寸（标准做法：32x32 后取左上 8x8 低频）
PHASH_WORK = 32
#: 哈希边长（8 => 64 bit）
HASH_SIZE = 8
#: 每通道颜色直方图 bin 数
HIST_BINS = 16

#: 颜色直方图的空间网格边长（4 => 4x4 共 16 块）。
#:
#: **全局直方图丢掉了"颜色在哪"** —— 同角色同属性的不同卡配色往往很接近，
#: 全局直方图几乎分不开它们，只能靠哈希的 128 bit 硬扛。分块之后
#: "左上是蓝、右下是粉"这类空间布局也成了证据。实测在真实截图 140 格上，
#: top1 与 top2 的差距中位数从 0.014 提到 0.034（翻倍），
#: 可直接采信的格子从 37.9% 提到 45.0%。
HIST_GRID = 4

_POPCOUNT_TABLE = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint16)


def popcount_u64(x: np.ndarray) -> np.ndarray:
    """对 uint64 数组逐元素求二进制 1 的个数，保持原有的维度。"""
    x = np.ascontiguousarray(x, dtype=np.uint64)
    if x.size == 0:
        return np.zeros(x.shape, dtype=np.int32)
    b = x.view(np.uint8).reshape(*x.shape, 8)
    return _POPCOUNT_TABLE[b].sum(axis=-1).astype(np.int32)


def _dct_matrix(n: int) -> np.ndarray:
    """DCT-II 变换矩阵 D，使 X = D @ x @ D.T。"""
    k = np.arange(n, dtype=np.float64)[:, None]
    i = np.arange(n, dtype=np.float64)[None, :]
    return np.cos(np.pi * (2.0 * i + 1.0) * k / (2.0 * n))


_DCT_CACHE: dict[int, np.ndarray] = {}


def _get_dct(n: int) -> np.ndarray:
    if n not in _DCT_CACHE:
        _DCT_CACHE[n] = _dct_matrix(n)
    return _DCT_CACHE[n]


def _to_gray_array(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float64)


def phash_bits(gray: np.ndarray, hash_size: int = HASH_SIZE) -> int:
    """感知哈希（DCT 版）。返回 hash_size^2 位的整数。"""
    work = PHASH_WORK
    im = Image.fromarray(gray.astype(np.uint8)).resize((work, work), Image.Resampling.LANCZOS)
    a = np.asarray(im, dtype=np.float64)
    d = _get_dct(work)
    low = (d @ a @ d.T)[:hash_size, :hash_size]
    flat = low.flatten()
    med = np.median(flat[1:])  # 排除 DC 分量，避免整体亮度主导
    bits = (flat > med).astype(np.uint8)
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return out


def dhash_bits(gray: np.ndarray, hash_size: int = HASH_SIZE) -> int:
    """差异哈希：水平相邻像素比较。"""
    im = Image.fromarray(gray.astype(np.uint8)).resize(
        (hash_size + 1, hash_size), Image.Resampling.LANCZOS
    )
    a = np.asarray(im, dtype=np.int16)
    diff = a[:, 1:] > a[:, :-1]
    out = 0
    for b in diff.flatten():
        out = (out << 1) | int(b)
    return out


def _center_crop(img: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    w, h = img.size
    l, t, r, b = box
    x0, y0 = int(w * l), int(h * t)
    x1, y1 = max(x0 + 8, int(w * r)), max(y0 + 8, int(h * b))
    return img.crop((x0, y0, min(x1, w), min(y1, h)))


def _histogram(img: Image.Image) -> np.ndarray:
    """HSV 三通道各 16 bin 直方图，L1 归一化。"""
    hsv = np.asarray(img.convert("HSV"), dtype=np.uint8)
    bins = np.arange(HIST_BINS + 1, dtype=np.int32) * (256 // HIST_BINS)
    parts = []
    for ch in range(3):
        h, _ = np.histogram(hsv[:, :, ch], bins=bins)
        parts.append(h.astype(np.float32))
    v = np.concatenate(parts)
    total = v.sum()
    return v / total if total > 0 else v


def spatial_histogram(img: Image.Image, grid: int = HIST_GRID) -> np.ndarray:
    """网格分块 HSV 直方图。

    每块**单独**做 L1 归一化 —— 这样一块小区域（比如角落的装饰）和一大片
    背景在余弦里权重相当，不会被面积压掉。拼接之后再整体 L2 归一化，
    交给 :func:`color_similarity` 做余弦。

    这样比全局直方图多出「颜色分布在哪」的信息。实测明显提升区分度，
    见 ``HIST_GRID`` 的注释。
    """
    w, h = img.size
    parts = []
    for gy in range(grid):
        for gx in range(grid):
            x0, y0 = int(w * gx / grid), int(h * gy / grid)
            x1, y1 = int(w * (gx + 1) / grid), int(h * (gy + 1) / grid)
            if x1 <= x0 or y1 <= y0:
                parts.append(np.zeros(3 * HIST_BINS, dtype=np.float32))
            else:
                parts.append(_histogram(img.crop((x0, y0, x1, y1))))
    v = np.concatenate(parts).astype(np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


@dataclass(slots=True)
class ImageFeatures:
    """一张图（某张卡面）的完整特征。"""

    phash: np.ndarray      # (n_crops,) uint64
    dhash: np.ndarray      # (n_crops,) uint64
    hist: np.ndarray       # (HIST_GRID^2 * 3 * HIST_BINS,) float32，L2 归一化
    avg_rgb: np.ndarray    # (3,) float32，0~1

    @property
    def n_crops(self) -> int:
        return int(self.phash.shape[0])

    def to_arrays(self) -> dict[str, np.ndarray]:
        return {
            "phash": self.phash.astype(np.uint64),
            "dhash": self.dhash.astype(np.uint64),
            "hist": self.hist.astype(np.float32),
            "avg_rgb": self.avg_rgb.astype(np.float32),
        }


def compute_features(img: Image.Image) -> ImageFeatures:
    """提取一张卡面的多档指纹与颜色特征。"""
    img = img.convert("RGB")
    ph, dh = [], []
    for box in CROP_LEVELS:
        crop = _center_crop(img, box)
        gray = _to_gray_array(crop)
        ph.append(phash_bits(gray))
        dh.append(dhash_bits(gray))

    mid = _center_crop(img, CROP_LEVELS[2]).resize((160, 120), Image.Resampling.LANCZOS)
    hist = spatial_histogram(mid)
    avg = np.asarray(mid, dtype=np.float32).reshape(-1, 3).mean(axis=0) / 255.0

    return ImageFeatures(
        phash=np.asarray(ph, dtype=np.uint64),
        dhash=np.asarray(dh, dtype=np.uint64),
        hist=hist.astype(np.float32),
        avg_rgb=avg.astype(np.float32),
    )


def best_hash_similarity(
    q: ImageFeatures, db_phash: np.ndarray, db_dhash: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """查询特征与指纹库做全组合（crop × crop）比对。

    ``db_phash`` / ``db_dhash`` 形状为 (n, n_crops)。

    返回 ``(similarity, normalized_distance)``，形状均为 (n,)，取值 0~1。
    """
    if db_phash.shape[0] == 0:
        return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)

    total_bits = HASH_SIZE * HASH_SIZE * 2  # pHash + dHash 合计位数
    pd = popcount_u64(db_phash[:, :, None] ^ q.phash[None, None, :])
    dd = popcount_u64(db_dhash[:, :, None] ^ q.dhash[None, None, :])
    dist = (pd + dd).min(axis=(1, 2)).astype(np.float32)
    sim = 1.0 - dist / float(total_bits)
    return np.clip(sim, 0.0, 1.0), np.clip(dist / float(total_bits), 0.0, 1.0)


def color_similarity(q_hist: np.ndarray, db_hist: np.ndarray) -> np.ndarray:
    """直方图余弦相似度，形状 (n,)。"""
    if db_hist.shape[0] == 0:
        return np.zeros(0, dtype=np.float32)
    a = q_hist[None, :]
    num = (db_hist * a).sum(axis=1)
    den = np.linalg.norm(db_hist, axis=1) * np.linalg.norm(a) + 1e-8
    return np.clip(num / den, 0.0, 1.0).astype(np.float32)
