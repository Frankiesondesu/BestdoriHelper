"""卡面指纹库：构建、保存、检索。

指纹库存为一个 ``.npz``，每行对应「某张卡的某个卡面变体」：

    card_ids   (n,)      int32    卡牌 ID
    trained    (n,)      bool     是否特训后卡面
    phash      (n, 4)    uint64   4 档中心裁切的 pHash
    dhash      (n, 4)    uint64   4 档中心裁切的 dHash
    hist       (n, 768)  float32  HSV 分块直方图（4x4 网格 x 3 通道 x 16 bin）
    avg_rgb    (n, 3)    float32  平均色

``.npz`` 旁边放一个 ``.manifest.json`` 记录每个变体对应图片文件的大小与
修改时间，用于增量重建（图片没变就复用旧特征，不必重新计算）。
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image

from ..bestdori.client import BestdoriClient
from ..models import Card, Catalog
from .features import (
    CROP_LEVELS,
    HIST_BINS,
    HIST_GRID,
    ImageFeatures,
    best_hash_similarity,
    color_similarity,
    compute_features,
)

log = logging.getLogger(__name__)

N_CROPS = len(CROP_LEVELS)
HIST_DIM = HIST_GRID * HIST_GRID * 3 * HIST_BINS


@dataclass
class BuildStats:
    """指纹库构建统计。"""

    total_variants: int = 0
    downloaded: int = 0
    reused: int = 0
    missing: int = 0
    failed: int = 0

    def summary(self) -> str:
        return (
            f"共 {self.total_variants} 个卡面变体：新下载 {self.downloaded}，"
            f"复用缓存 {self.reused}，资源不存在 {self.missing}，失败 {self.failed}"
        )


@dataclass
class Match:
    """一条检索结果。"""

    card_id: int
    trained: bool
    score: float
    hash_similarity: float
    color_similarity: float
    #: SIFT 比值检验后的好匹配数（几何校验没跑时是 0）
    good: int = 0
    #: RANSAC 几何一致的内点数 —— 判定"是不是同一张画"的可靠依据
    inliers: int = 0

    def to_dict(self) -> dict:
        return {
            "cardId": self.card_id,
            "trained": self.trained,
            "score": round(self.score, 4),
            "hashSimilarity": round(self.hash_similarity, 4),
            "colorSimilarity": round(self.color_similarity, 4),
            "good": self.good,
            "inliers": self.inliers,
        }


class FingerprintIndex:
    """内存中的指纹库。"""

    def __init__(
        self,
        card_ids: np.ndarray,
        trained: np.ndarray,
        phash: np.ndarray,
        dhash: np.ndarray,
        hist: np.ndarray,
        avg_rgb: np.ndarray,
        manifest: dict[str, dict] | None = None,
    ) -> None:
        self.card_ids = card_ids
        self.trained = trained
        self.phash = phash
        self.dhash = dhash
        self.hist = hist
        self.avg_rgb = avg_rgb
        self.manifest = manifest or {}

    def __len__(self) -> int:
        return int(self.card_ids.shape[0])

    # ---- 持久化 ------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> FingerprintIndex | None:
        if not path.exists():
            return None
        data = np.load(path)
        # 特征格式变过（颜色直方图从全局 48 维改成 4x4 分块 768 维）。
        # 旧库必须整库重建 —— 混着用会在 cosine 那步直接广播报错，
        # 不如在这里明确判为"不可用"，让上层提示用户重建。
        if int(data["hist"].shape[1]) != HIST_DIM:
            log.warning(
                "指纹库特征格式过旧（hist %d 维，当前需要 %d 维），请重新构建：%s",
                data["hist"].shape[1], HIST_DIM, path,
            )
            return None
        man_path = path.with_suffix(".manifest.json")
        manifest = {}
        if man_path.exists():
            try:
                manifest = json.loads(man_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("manifest 损坏，忽略：%s", man_path)
        return cls(
            card_ids=data["card_ids"],
            trained=data["trained"],
            phash=data["phash"],
            dhash=data["dhash"],
            hist=data["hist"],
            avg_rgb=data["avg_rgb"],
            manifest=manifest,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            card_ids=self.card_ids,
            trained=self.trained,
            phash=self.phash,
            dhash=self.dhash,
            hist=self.hist,
            avg_rgb=self.avg_rgb,
        )
        path.with_suffix(".manifest.json").write_text(
            json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8"
        )

    # ---- 检索 --------------------------------------------------------

    def search(
        self,
        features: ImageFeatures,
        *,
        top_k: int = 5,
        hash_weight: float = 0.40,
        restrict_card_ids: Iterable[int] | None = None,
        restrict_rarity: set[int] | None = None,
        restrict_attribute: str | None = None,
        catalog: Catalog | None = None,
        prefer_trained: bool | None = None,
    ) -> list[Match]:
        """检索最相似的卡面。

        ``restrict_*`` 参数用于「多信号融合」：当 UI 分析或 OCR 已经给出
        星级/属性/角色线索时，把候选集缩小到对应范围，识别准确率会显著提升。

        ``hash_weight`` 默认 0.40 —— 分块颜色直方图（``HIST_GRID``）比原来的
        全局直方图信息量大得多，所以在融合里该给它更大权重。实测 0.40 时
        真实截图上的区分度最好（见 ``.bdh-test/threshold_scan.py``）。
        """
        n = len(self)
        if n == 0:
            return []

        mask = np.ones(n, dtype=bool)
        if restrict_card_ids is not None:
            allowed = np.fromiter(set(restrict_card_ids), dtype=np.int32)
            if allowed.size == 0:
                return []
            mask &= np.isin(self.card_ids, allowed)
        if restrict_rarity and catalog is not None:
            rar = np.array([catalog.cards.get(int(c), Card(0, 0, 0, "")).rarity for c in self.card_ids])
            mask &= np.isin(rar, sorted(restrict_rarity))
        if restrict_attribute and catalog is not None:
            att = np.array(
                [catalog.cards.get(int(c), Card(0, 0, 0, "")).attribute for c in self.card_ids]
            )
            mask &= att == restrict_attribute
        if prefer_trained is not None:
            mask &= self.trained == prefer_trained

        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return []

        hsim, _ = best_hash_similarity(features, self.phash[idx], self.dhash[idx])
        csim = color_similarity(features.hist, self.hist[idx])
        score = hash_weight * hsim + (1.0 - hash_weight) * csim

        # 用 lexsort 而不是 argsort：Bestdori 上有 **111 个 resourceSetName 被
        # 多张卡共用**（例如「第N回ガルパ杯」纪念卡复用活动卡的原图），这些卡的
        # 卡图文件是同一个，特征完全相同，分数必然并列。argsort 在并列时顺序由
        # 实现细节决定，同样的输入在多跑几次、或查询图稍有变化时就会给出不同的
        # 卡号 —— 表现为"上次认出来是这张、这次变成那张"。补一个卡号升序的
        # 次级键，让结果确定。
        order = np.lexsort((self.card_ids[idx], -score))[: max(1, top_k)]
        out: list[Match] = []
        for o in order:
            row = int(idx[o])
            out.append(
                Match(
                    card_id=int(self.card_ids[row]),
                    trained=bool(self.trained[row]),
                    score=float(score[o]),
                    hash_similarity=float(hsim[o]),
                    color_similarity=float(csim[o]),
                )
            )
        return out

    def stats(self) -> dict:
        return {
            "variants": len(self),
            "cards": int(np.unique(self.card_ids).size),
            "normal": int((~self.trained).sum()),
            "trained": int(self.trained.sum()),
        }


# ---------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------


def _file_sig(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


@dataclass(slots=True)
class _Variant:
    """指纹库里的一行：某张卡的某个卡面形态。"""

    card: Card
    path: Path
    trained: bool


def _collect_variants(
    catalog: Catalog,
    client: BestdoriClient,
    *,
    include_trained: bool,
    only_card_ids: set[int] | None,
    stats: BuildStats,
    say: Callable[[str], None],
) -> list[_Variant]:
    """解析每张卡实际存在的卡面文件（这一步会触发下载）。

    下载是**并发**的。实测串行下一张图约 3 秒，全量 4000 多张要三个多小时；
    ``Settings.max_concurrency`` 定义了却一直没用上，这里补上。

    ``httpx.Client`` 本身线程安全；每张卡由单个线程处理，写的是各自的文件
    （含 ``.part`` 临时文件和 ``.missing`` 标记），互不冲突。``ex.map`` 按
    输入顺序返回，所以结果顺序是确定的。
    """
    cards = [
        catalog.cards[cid]
        for cid in sorted(catalog.cards)
        if not only_card_ids or cid in only_card_ids
    ]
    out: list[_Variant] = []
    workers = max(1, int(getattr(client.settings, "max_concurrency", 8) or 1))
    lock = threading.Lock()
    progress = {"done": 0, "downloaded": 0, "missing": 0}

    def work(card: Card) -> list[_Variant]:
        before = {
            client.card_image_path(card, False): client.card_image_path(card, False).exists(),
            client.card_image_path(card, True): client.card_image_path(card, True).exists(),
        }
        try:
            variants = client.resolve_variants(card, include_trained=include_trained)
        except Exception as e:  # noqa: BLE001 - 单张失败不该中断整体构建
            log.warning("卡图解析失败 card=%s: %s", card.id, e)
            with lock:
                stats.failed += 1
                progress["done"] += 1
            return []

        rows: list[_Variant] = []
        fresh = 0
        for path, trained in variants:
            if not before.get(path, False):
                fresh += 1
            rows.append(_Variant(card=card, path=path, trained=trained))

        with lock:
            progress["done"] += 1
            progress["downloaded"] += fresh
            if not rows:
                progress["missing"] += 1
            stats.downloaded = progress["downloaded"]
            stats.missing = progress["missing"]
            done = progress["done"]
            if done % 25 == 0 or done == len(cards):
                say(f"下载卡图 {done}/{len(cards)} …")
        return rows

    if workers == 1 or len(cards) <= 1:
        for card in cards:
            out.extend(work(card))
        return out

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rows in ex.map(work, cards):
            out.extend(rows)
    return out


def build_index(
    catalog: Catalog,
    client: BestdoriClient,
    *,
    include_trained: bool = True,
    only_card_ids: set[int] | None = None,
    reuse: FingerprintIndex | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[FingerprintIndex, BuildStats]:
    """构建指纹库。

    ``reuse`` 传入旧库时会做增量：图片文件未变化的变体直接复用旧特征，
    省掉最耗时的 DCT 计算。
    """
    say = progress or (lambda _m: None)
    stats = BuildStats()

    old_rows: dict[tuple[int, bool], dict] = {}
    if reuse is not None:
        for i in range(len(reuse)):
            old_rows[(int(reuse.card_ids[i]), bool(reuse.trained[i]))] = {
                "phash": reuse.phash[i],
                "dhash": reuse.dhash[i],
                "hist": reuse.hist[i],
                "avg_rgb": reuse.avg_rgb[i],
            }

    say("解析卡面资源…")
    variants = _collect_variants(
        catalog,
        client,
        include_trained=include_trained,
        only_card_ids=only_card_ids,
        stats=stats,
        say=say,
    )
    stats.total_variants = len(variants)

    card_ids: list[int] = []
    trained_flags: list[bool] = []
    ph_list: list[np.ndarray] = []
    dh_list: list[np.ndarray] = []
    hist_list: list[np.ndarray] = []
    avg_list: list[np.ndarray] = []
    manifest: dict[str, dict] = {}

    for i, v in enumerate(variants, 1):
        key = f"{v.card.resource_set_name}:{'T' if v.trained else 'N'}"
        if i % 50 == 0 or i == len(variants):
            say(f"计算指纹 {i}/{len(variants)} …")

        sig = _file_sig(v.path)
        manifest[key] = {"cardId": v.card.id, "trained": v.trained, "sig": sig}

        cached = old_rows.get((v.card.id, v.trained))
        if cached is not None and reuse is not None:
            if reuse.manifest.get(key, {}).get("sig") == sig:
                stats.reused += 1
                card_ids.append(v.card.id)
                trained_flags.append(v.trained)
                ph_list.append(cached["phash"])
                dh_list.append(cached["dhash"])
                hist_list.append(cached["hist"])
                avg_list.append(cached["avg_rgb"])
                continue

        try:
            with Image.open(v.path) as im:
                feats = compute_features(im)
        except Exception as e:  # noqa: BLE001 — 单张失败不应中断整体构建
            log.warning("特征计算失败 %s: %s", v.path.name, e)
            stats.failed += 1
            continue

        card_ids.append(v.card.id)
        trained_flags.append(v.trained)
        ph_list.append(feats.phash)
        dh_list.append(feats.dhash)
        hist_list.append(feats.hist)
        avg_list.append(feats.avg_rgb)

    say("整理指纹库…")
    index = FingerprintIndex(
        card_ids=np.asarray(card_ids, dtype=np.int32),
        trained=np.asarray(trained_flags, dtype=bool),
        phash=np.stack(ph_list) if ph_list else np.zeros((0, N_CROPS), dtype=np.uint64),
        dhash=np.stack(dh_list) if dh_list else np.zeros((0, N_CROPS), dtype=np.uint64),
        hist=np.stack(hist_list) if hist_list else np.zeros((0, HIST_DIM), dtype=np.float32),
        avg_rgb=np.stack(avg_list) if avg_list else np.zeros((0, 3), dtype=np.float32),
        manifest=manifest,
    )
    return index, stats
