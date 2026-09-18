"""识别流水线：一张截图 -> 若干张卡牌。

多信号融合
----------
每个候选框依次经过三路信号，最后加权融合：

1. **指纹匹配**（主信号，权重 0.65 哈希 + 0.35 颜色）
2. **属性提示**（UI 边框颜色）—— 只用来缩小候选集，不直接加分
3. **OCR 文字**（卡名 / 角色名命中）—— 作为加成项，用于区分难例

判定分三档：

``matched``    指纹分够高，且与第二名的差距够大 → 直接采信
``ambiguous``  前几名咬得很近 → 列出候选，交给用户点选
``unknown``    分数过低 → 可能是指纹库里还没有的新卡
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..models import Catalog
from .detect import Box, crop_box, detect_boxes, refine_boxes, region_to_px
from .features import compute_features
from .index import FingerprintIndex, Match
from .ocr import OcrEngine, OcrLine, NullOcr, score_text_against_catalog
from .ui_hints import (
    ATTRIBUTE_HINT_MIN_CONF,
    AttributeHint,
    estimate_attribute_combined,
)
from .verify import (
    CONFIRM_INLIERS,
    DEFAULT_TOP_K as VERIFY_TOP_K,
    ESCALATE_TOP_K as VERIFY_ESCALATE_TOP_K,
    WEAK_INLIERS,
    Verifier,
)

log = logging.getLogger(__name__)

#: 指纹综合分达到此值才认为「匹配」。
#:
#: ⚠️ 这个阈值**跟特征方案绑定**：换特征一定会改分数尺度，必须一起重标定。
#: 历史：全局直方图时代是 0.74；改用 4x4 分块颜色直方图后分数整体下移
#: （真实截图 140 格上均值 0.808 -> 0.758），沿用 0.74 会把 71/140 判成
#: ``unknown``（而它们其实认对了）。
#:
#: 现取 0.66 —— 在真实截图上，[0.66, 0.76) 且 gap>=0.03 的 13 个格子
#: 逐格人工核验**全部正确**（见 ``.bdh-test/verify_new_matches.py``）。
MATCH_THRESHOLD = 0.66
#: 低于此分才判 ``unknown``（"可能是指纹库还没有的新卡"）。
#:
#: 这个下限**必须和 ``MATCH_THRESHOLD`` 分开** —— 两者回答的是不同问题：
#: ``MATCH_THRESHOLD`` 是"敢不敢自动采信"，``UNKNOWN_FLOOR`` 是"有没有候选"。
#: 合成一个阈值会出事：实测真实截图上有 16 个格子分数落在 0.60~0.66，
#: 正确卡其实是 top-1，只是分数偏低；用一个阈值会把它们全判成 unknown，
#: 用户连候选列表都看不到。
UNKNOWN_FLOOR = 0.55
#: 与第二名的最小分差；低于此值判为「需要人工确认」。
#: 分块颜色直方图把可用的区分度做大了（真实截图 gap 中位 0.014 -> 0.028），
#: 所以门槛跟着从 0.02 收到 0.03 —— 让「分差」承担主要判定职责。
AMBIGUOUS_GAP = 0.030
#: OCR 文字命中的最大加成
OCR_BOOST_MAX = 0.12
#: 单个候选框最多返回几个候选
TOP_K = 6
#: 返回给调用方的候选列表上限（几何校验会产出几十上百条，展示用不着那么多）
CANDIDATE_LIMIT = 8


@dataclass(slots=True)
class RecognizedItem:
    """一张被识别出来的卡面。"""

    box: Box
    card_id: int | None = None
    trained: bool = False
    confidence: float = 0.0
    status: str = "unknown"
    matched_by: str = ""
    candidates: list[Match] = field(default_factory=list)
    attribute_hint: AttributeHint | None = None
    ocr_lines: list[OcrLine] = field(default_factory=list)

    def to_dict(self, catalog: Catalog | None = None) -> dict:
        d = {
            "box": self.box.to_dict(),
            "cardId": self.card_id,
            "trained": self.trained,
            "confidence": round(self.confidence, 4),
            "status": self.status,
            "matchedBy": self.matched_by,
            "candidates": [c.to_dict() for c in self.candidates],
            "attributeHint": self.attribute_hint.to_dict() if self.attribute_hint else None,
        }
        if catalog is not None and self.card_id is not None:
            d["card"] = catalog.describe(self.card_id, self.trained)
        return d


@dataclass(slots=True)
class RecognitionResult:
    """一张截图的识别结果。"""

    image: str
    items: list[RecognizedItem] = field(default_factory=list)
    width: int = 0
    height: int = 0

    @property
    def matched(self) -> list[RecognizedItem]:
        return [i for i in self.items if i.status == "matched"]

    def to_dict(self, catalog: Catalog | None = None) -> dict:
        return {
            "image": self.image,
            "width": self.width,
            "height": self.height,
            "count": len(self.items),
            "matched": len(self.matched),
            "items": [i.to_dict(catalog) for i in self.items],
        }


def _fuse(
    fp_score: float,
    ocr_hits: dict[int, float],
    card_id: int,
) -> float:
    """把指纹分与 OCR 命中分融合。"""
    boost = min(OCR_BOOST_MAX, ocr_hits.get(card_id, 0.0) * OCR_BOOST_MAX)
    return min(1.0, fp_score + boost)


def _verified_shortlist(
    crop: Image.Image,
    index: FingerprintIndex,
    catalog: Catalog,
    verifier: Verifier,
    feats,
) -> list[Match]:
    """拿全库粗筛 top-K，再用几何校验排序；首轮没确认就扩大候选再试一次。

    **这里刻意不用属性提示做限制** —— 属性提示是启发式的，一旦"自信地判错"
    就会把正确的卡整个排除在候选之外，几何校验再强也救不回来。属性提示改在
    校验没确认时，作为退回路径的参考（见 :func:`recognize_crop`）。
    """
    short = index.search(feats, top_k=VERIFY_TOP_K, catalog=catalog)
    short, st = verifier.rerank(crop, short, catalog)
    if short and short[0].inliers < CONFIRM_INLIERS:
        wide = index.search(feats, top_k=VERIFY_ESCALATE_TOP_K, catalog=catalog)
        wide, _ = verifier.rerank(crop, wide, catalog)
        if wide and (not short or wide[0].inliers > short[0].inliers):
            log.debug("几何校验首轮未确认（内点 %s），扩大候选后提升到 %s",
                      short[0].inliers if short else 0, wide[0].inliers)
            return wide
    return short


def recognize_crop(
    img: Image.Image,
    index: FingerprintIndex,
    catalog: Catalog,
    *,
    box: Box | None = None,
    ocr: OcrEngine | None = None,
    use_ocr: bool = False,
    use_attribute_hint: bool = True,
    verifier: Verifier | None = None,
) -> RecognizedItem:
    """识别单个卡面裁剪。

    :param verifier: 几何校验器（:class:`~bestdori_helper.vision.verify.Verifier`）。
        给了就先做「粗筛 + SIFT 二次排序」，确认命中就**直接采信**，不再看
        全局相似度阈值 —— 全局特征分不出"同角色同属性的不同卡"，实测真实截图
        top-1 只有 67.9%，加上几何校验后是 100%。给 ``None`` 则走旧的纯全局路径。
    """
    box = box or Box(0, 0, img.width, img.height)
    feats = compute_features(img)

    hint: AttributeHint | None = None
    restrict_attr: str | None = None
    if use_attribute_hint:
        hint = estimate_attribute_combined(img)
        if hint.attribute and hint.confidence >= ATTRIBUTE_HINT_MIN_CONF:
            restrict_attr = hint.attribute

    candidates = index.search(
        feats, top_k=TOP_K, catalog=catalog, restrict_attribute=restrict_attr
    )
    used_hint = restrict_attr is not None

    # 属性提示把正确答案排除掉了 —— 退回全库重搜。
    # **只在 hint 本身不够确信时才退回**，否则受限搜索的 top-1 分数经常
    # 低于 0.74（候选集被砍 1/4 后最佳匹配仍可能不及 0.74），会触发误退。
    if (
        candidates
        and candidates[0].score < MATCH_THRESHOLD
        and used_hint
        and (hint is None or hint.confidence < 0.45)
    ):
        full = index.search(feats, top_k=TOP_K, catalog=catalog)
        if full and full[0].score > candidates[0].score + 0.01:
            candidates = full
            used_hint = False

    ocr_lines: list[OcrLine] = []
    ocr_hits: dict[int, float] = {}
    if use_ocr and ocr is not None and ocr.available:
        ocr_lines = ocr.read_text(img)
        ocr_hits = score_text_against_catalog(
            ocr_lines, catalog, [c.card_id for c in candidates] or None
        )

    if not candidates:
        return RecognizedItem(
            box=box,
            status="unknown",
            candidates=[],
            attribute_hint=hint,
            ocr_lines=ocr_lines,
        )

    # ---- 几何校验：粗筛 top-K + SIFT 二次排序 ----
    sift_ranked: list[Match] = []
    if verifier is not None:
        sift_ranked = _verified_shortlist(img, index, catalog, verifier, feats)

    # 融合打分并重排。次级键用卡号 —— 共用同一张卡图的几张卡分数会完全并列
    # （见 :meth:`FingerprintIndex.search` 的说明），必须有个确定的收尾键。
    scored = [(c, _fuse(c.score, ocr_hits, c.card_id)) for c in candidates]
    scored.sort(key=lambda t: (-t[1], t[0].card_id))
    top, top_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0

    signals = ["fingerprint"]
    if used_hint:
        signals.append("attribute")
    if ocr_hits.get(top.card_id, 0.0) > 0:
        signals.append("ocr")

    if top_score < UNKNOWN_FLOOR:
        status = "unknown"
    elif top_score < MATCH_THRESHOLD or top_score - second_score < AMBIGUOUS_GAP:
        status = "ambiguous"
    else:
        status = "matched"

    # 几何校验的结论**优先于**全局相似度：全局特征分不清"同角色同属性的不同卡"，
    # 而 SIFT 的几何一致性是"同一张画"的硬证据（真实截图 137/140 格能拿到
    # 内点 >= 60 的候选，而错配的候选内点都 < 20，中间地带一格没有）。
    if sift_ranked:
        best = sift_ranked[0]
        if best.inliers >= CONFIRM_INLIERS:
            top, top_score = best, best.score
            status = "matched"
            if "sift" not in signals:
                signals.append("sift")
        elif best.inliers >= WEAK_INLIERS:
            # 中间地带：几何上有几分像但不够硬。保守判为待确认 —— 这种格子
            # 在实测里一格都没有，宁可让人点一下，也不要自动采信。
            top, top_score = best, best.score
            status = "ambiguous"
            if "sift" not in signals:
                signals.append("sift")

    # 难例时：若 OCR 对某个候选有明确命中，让它优先
    if status == "ambiguous" and ocr_hits:
        best_ocr = max(scored, key=lambda t: ocr_hits.get(t[0].card_id, 0.0))
        if ocr_hits.get(best_ocr[0].card_id, 0.0) > ocr_hits.get(top.card_id, 0.0):
            top, top_score = best_ocr
            second_score = max((s for c, s in scored if c is not top), default=0.0)
            if top_score - second_score >= AMBIGUOUS_GAP:
                status = "matched"
                signals.append("ocr-tiebreak")

    # 候选列表：几何校验排出来的顺序更有意义（按"是不是同一张画"排），
    # 拼上全局分排出来的，去重后截断。
    merged: list[Match] = []
    seen: set[tuple[int, bool]] = set()
    for m in sift_ranked + [c for c, _ in scored]:
        key = (m.card_id, m.trained)
        if key in seen:
            continue
        seen.add(key)
        merged.append(m)
        if len(merged) >= CANDIDATE_LIMIT:
            break

    return RecognizedItem(
        box=box,
        card_id=top.card_id,
        trained=top.trained,
        confidence=top_score,
        status=status,
        matched_by="+".join(signals),
        candidates=merged,
        attribute_hint=hint,
        ocr_lines=ocr_lines,
    )


def recognize_image(
    path: str | Path,
    index: FingerprintIndex,
    catalog: Catalog,
    *,
    mode: str = "auto",
    rows: int | None = None,
    cols: int | None = None,
    inset: float = 0.0,
    ocr: OcrEngine | None = None,
    use_ocr: bool = False,
    use_attribute_hint: bool = True,
    region: tuple[float, float, float, float] | None = None,
    verifier: Verifier | None = None,
    refine_cells: bool | None = None,
) -> RecognitionResult:
    """识别一张截图。

    :param mode: ``auto`` 自动切分网格 / ``grid`` 按行列等分 / ``single`` 整图当一张
    :param rows: ``grid`` 模式的行数
    :param cols: ``grid`` 模式的列数
    :param inset: 每个单元格向内收缩比例，用于去掉边框
    :param region: 卡面区域 ``(x0, y0, x1, y1)``，0~1 归一化。**整屏 UI 截图
        必须给这个参数** —— 卡面网格嵌在 UI 里时，全图自动检测会切到 UI 上。
    :param verifier: 几何校验器，见 :func:`recognize_crop`
    :param refine_cells: 是否把等分格子收缩到真正的卡框上。``None`` 时取
        ``settings.refine_cells``。**等分出来的格子含留白，其上下边常常压在
        相邻卡面上**，不收缩的话切出来的图会混进别的卡的一条边。``single``
        模式下忽略此参数（用户已经自己框了范围，尊重它）。
    """
    path = Path(path)
    with Image.open(path) as im:
        img = im.convert("RGB")

    result = RecognitionResult(image=str(path), width=img.width, height=img.height)

    if mode == "single":
        px = region_to_px(region, img.width, img.height) if region is not None else None
        if px is not None:
            x0, y0, x1, y1 = px
            boxes = [Box(x0, y0, x1 - x0, y1 - y0)]
        else:
            boxes = [Box(0, 0, img.width, img.height)]
    else:
        if refine_cells is None:
            refine_cells = bool(getattr(catalog.settings, "refine_cells", True))
        # prefer_gaps：先按卡面实际位置切（找背景间隙），失败才退回等分/周期检测。
        # 关掉 refine_cells 就是彻底回到"纯等分"，便于对照。
        boxes = detect_boxes(
            img, rows=rows, cols=cols, region=region, prefer_gaps=refine_cells
        )
        if refine_cells:
            raw = boxes
            boxes = refine_boxes(img, raw)
            if boxes and raw:
                log.info(
                    "%s: 卡框贴合 %sx%s -> %sx%s（均值）",
                    path.name,
                    sum(b.w for b in raw) // len(raw),
                    sum(b.h for b in raw) // len(raw),
                    sum(b.w for b in boxes) // len(boxes),
                    sum(b.h for b in boxes) // len(boxes),
                )

    log.info("%s: 切分出 %d 个卡面候选", path.name, len(boxes))

    for box in boxes:
        crop = crop_box(img, box, inset=inset)
        if crop.width < 24 or crop.height < 24:
            continue
        item = recognize_crop(
            crop,
            index,
            catalog,
            box=box,
            ocr=ocr or NullOcr(),
            use_ocr=use_ocr,
            use_attribute_hint=use_attribute_hint,
            verifier=verifier,
        )
        result.items.append(item)

    return result
