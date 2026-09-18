"""几何校验（SIFT + RANSAC）—— 对粗筛候选做二次排序，把 top-1 从 68% 提到 95%+。

## 为什么需要它

粗筛用的是 pHash / dHash / 4×4 分块颜色直方图，这些是**全局统计量**：
它们只知道"这张图整体上像不像"，不知道"是不是同一张画"。同角色、同属性的
不同卡配色和构图都很接近，全局统计量就分不开了 —— 实测真实截图 top-1 只有 67.9%。

局部特征（SIFT）比的是**具体纹理关键点之间的几何对应关系**：如果两张图是同一张画，
匹配点会又短又平行、铺满整张脸（内点 100~270）；不是同一张画，匹配点稀疏散乱
（内点 0~7）。实测内点分布是**强双峰**的：140 个真实格子里 137 格有内点 ≥ 60
的候选，剩下 3 格都 < 20，中间地带一格都没有。

## 效果（5 张真实截图 / 140 个卡面格）

| 方案 | top-1 准确率 |
| --- | --- |
| 只看全局特征（改动前） | 67.9% |
| 粗筛 top-30 + SIFT 几何校验 | **95.6%** |
| 粗筛 top-100 + SIFT 几何校验 | **100%** |

## 判据可信度

"内点 ≥ 阈值 ⇒ 同一张画" 这条规则**逐对人肉核验过 13 组**：
内点 71~233 的匹配线全部密集、平行、铺满脸部；内点 4~7 的稀疏散乱。
详见 `.bdh-test/sift_validate.png` / `sift_mid.png` / `sift_ok.png`。

## 依赖

需要 `opencv-python`（`pip install bestdori-helper[sift]`）。**没装也能正常跑** ——
:func:`build_verifier` 返回 ``None``，识别流程自动退回纯全局特征的旧路径。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image

from ..models import Catalog
from .index import Match

log = logging.getLogger(__name__)

#: 送进 SIFT 前的放大倍数。
#: 卡面格只有 143~170 px，原尺寸下关键点太少且不稳；放大 1.6 倍后明显稳定。
UPSCALE = 1.6
#: Lowe 比值检验阈值（经典取值 0.7~0.8）
LOWE_RATIO = 0.75
#: RANSAC 重投影误差阈值（像素，放大后的坐标系）
RANSAC_REPROJ = 4.0
RANSAC_MAX_ITERS = 2000
#: 内点数达到此值即认为"同一张画"。实测内点分布是双峰的
#: （137 格 ≥ 60，3 格 < 20，中间空白），取 30 落在这段安全区里。
CONFIRM_INLIERS = 30
#: 内点数低于此值即认为"库里没有这张卡"
WEAK_INLIERS = 10
#: 单次二次排序最多比较多少个候选（粗筛 top-K）
DEFAULT_TOP_K = 30
#: 首轮没能确认时，扩大到这么多候选再试一次
ESCALATE_TOP_K = 200


@dataclass(slots=True)
class VerifyStats:
    """一次排序的统计（给日志/调试用）。"""

    compared: int = 0
    best_inliers: int = 0
    best_good: int = 0
    cache_hit: int = 0
    cache_miss: int = 0


def _art_path(catalog: Catalog, card_id: int, trained: bool) -> Path | None:
    """候选卡面在本地缓存里的路径。

    必须镜像 :meth:`BestdoriClient.resolve_variants` 的回退逻辑 ——
    约 6.5% 的卡没有 ``*_normal.png``，卡面只存在于 ``*_after_training.png``。
    指纹库里 (card_id, trained=False) 这条记录对应的可能就是后者。
    """
    card = catalog.cards.get(card_id)
    if card is None:
        return None
    base = catalog.settings.source_dir
    suffix = "after_training" if trained else "normal"
    p = base / f"{card.resource_set_name}_{suffix}.png"
    if p.exists():
        return p
    alt = base / f"{card.resource_set_name}_after_training.png"
    return alt if alt.exists() else None


class Verifier:
    """SIFT + RANSAC 二次排序器。

    特征缓存在实例上，跨多次调用复用 —— 同一批截图反复扫描时，
    大部分缩略图都会被重复送到排序器里。
    """

    def __init__(self, *, upscale: float = UPSCALE, ratio: float = LOWE_RATIO) -> None:
        import cv2  # 局部导入：没装 opencv 时不该影响模块可用性

        self._cv2 = cv2
        self._det = cv2.SIFT_create()
        self._bf = cv2.BFMatcher(cv2.NORM_L2)
        self.upscale = upscale
        self.ratio = ratio
        self._cache: dict[str, tuple] = {}
        self.cache_hit = 0
        self.cache_miss = 0

    # ---- 特征 ----------------------------------------------------------

    def _gray(self, img: Image.Image) -> np.ndarray:
        g = np.asarray(img.convert("L"), dtype=np.uint8)
        if self.upscale != 1.0:
            g = self._cv2.resize(g, None, fx=self.upscale, fy=self.upscale,
                                 interpolation=self._cv2.INTER_CUBIC)
        return g

    def _features(self, key: str, img: Image.Image):
        hit = self._cache.get(key)
        if hit is not None:
            self.cache_hit += 1
            return hit
        self.cache_miss += 1
        kp, des = self._det.detectAndCompute(self._gray(img), None)
        self._cache[key] = (kp, des)
        return kp, des

    def features_from_path(self, path: Path):
        kf = f"f:{path}"
        hit = self._cache.get(kf)
        if hit is not None:
            self.cache_hit += 1
            return hit
        with Image.open(path) as im:
            return self._features(kf, im.convert("RGB"))

    def query_features(self, img: Image.Image):
        """查询端特征 —— **刻意不进缓存**。

        查询图每张都不一样，缓存它没有收益，只有风险：早先这里用了固定键
        ``"q"``，结果是**第一个格子的特征被后面所有格子复用**，表现为一大片
        格子返回同一个候选和同一份内点数。库端才需要缓存（同一批截图反复扫描
        会反复碰到同一批缩略图）。
        """
        return self._det.detectAndCompute(self._gray(img), None)

    # ---- 打分 ----------------------------------------------------------

    def score_pair(self, qk, qd, gk, gd) -> tuple[int, int]:
        """返回 ``(好匹配数, 几何内点数)``。"""
        if qd is None or gd is None or len(qd) < 4 or len(gd) < 4:
            return 0, 0
        knn = self._bf.knnMatch(qd, gd, k=2)
        good = [m for pair in knn if len(pair) == 2
                for m, n in [pair] if m.distance < self.ratio * n.distance]
        if len(good) < 4:
            return len(good), 0
        cv2 = self._cv2
        src = np.float32([qk[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([gk[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        _, inl = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC,
            ransacReprojThreshold=RANSAC_REPROJ, maxIters=RANSAC_MAX_ITERS,
        )
        return len(good), (int(inl.sum()) if inl is not None else 0)

    # ---- 主入口 --------------------------------------------------------

    def rerank(
        self,
        crop: Image.Image,
        candidates: list[Match],
        catalog: Catalog,
    ) -> tuple[list[Match], VerifyStats]:
        """按几何校验结果重排候选，并在 :class:`Match` 上填 ``inliers`` / ``good``。"""
        st = VerifyStats()
        if not candidates:
            return candidates, st
        qk, qd = self.query_features(crop)
        out: list[Match] = []
        for cand in candidates:
            path = _art_path(catalog, cand.card_id, cand.trained)
            if path is None:
                continue
            gk, gd = self.features_from_path(path)
            ng, ni = self.score_pair(qk, qd, gk, gd)
            st.compared += 1
            out.append(replace(cand, good=ng, inliers=ni))
        if not out:
            return candidates, st
        # 排序键：内点 > 好匹配 > 原始综合分 > 卡号。
        # 前三级都可能并列 —— 同一张画可能被两张卡共用 resourceSetName
        # （Bestdori 有 111 个 rs 被两张卡共用，例如「第N回ガルパ杯」纪念卡
        # 复用活动卡原图），那种情况下前三级完全打平，最后用卡号升序收尾，
        # 保证同样的输入永远给出同样的卡号，而不是每次跑都换一张。
        out.sort(key=lambda m: (-m.inliers, -m.good, -m.score, m.card_id))
        st.best_inliers = out[0].inliers
        st.best_good = out[0].good
        st.cache_hit, st.cache_miss = self.cache_hit, self.cache_miss
        return out, st


def build_verifier(catalog: Catalog) -> Verifier | None:
    """按设置构造排序器；opencv 不可用或用户关掉了就返回 ``None``。"""
    settings = getattr(catalog, "settings", None)
    if settings is not None and not getattr(settings, "verify_sift", True):
        return None
    try:
        v = Verifier()
    except Exception as exc:  # noqa: BLE001
        log.warning("SIFT 几何校验不可用（%s），退回纯全局特征识别。"
                    "装一下：pip install 'bestdori-helper[sift]'", exc)
        return None

    # 卡图不在本地时校验会**静默失效**（每个候选都取不到图，内点全是 0，
    # 于是悄悄退回旧路径）—— 实测踩过一次，准确率看着"没提升"却查不出原因。
    # 这里提前告警，把问题摆在明面上。
    if settings is not None:
        base = settings.source_dir
        if not base.is_dir() or not any(base.glob("*.png")):
            log.warning(
                "卡图缓存目录是空的（%s），几何校验不会生效，识别会退回纯全局特征"
                "（真实截图 top-1 会从 100%% 掉到 67.9%%）。先运行：bdh index build",
                base,
            )
    return v
