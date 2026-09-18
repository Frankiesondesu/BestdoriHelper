"""可选的 OCR 辅助信号。

指纹匹配是主信号；OCR 是**加分项**，用于两种情况：

1. 两张卡面视觉上极像（同角色不同卡、同卡不同特训），指纹分不出高下时，
   用卡名/角色名文字做二次判定。
2. 指纹库里没有这张卡（新卡、联动卡尚未同步）时，至少能报出文字线索。

OCR 引擎是可插拔的：装了 ``rapidocr-onnxruntime`` 就自动启用，否则退化为
空实现，整个流程照常工作。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

log = logging.getLogger(__name__)


@dataclass(slots=True)
class OcrLine:
    """一行 OCR 结果。"""

    text: str
    confidence: float = 0.0
    box: tuple[int, int, int, int] = (0, 0, 0, 0)

    def to_dict(self) -> dict:
        return {"text": self.text, "confidence": round(self.confidence, 3), "box": list(self.box)}


class OcrEngine(Protocol):
    """OCR 引擎接口。"""

    available: bool

    def read_text(self, img: Image.Image) -> list[OcrLine]:  # pragma: no cover - 协议
        ...


class NullOcr:
    """空实现：始终返回空结果。"""

    available = False

    def read_text(self, img: Image.Image) -> list[OcrLine]:
        return []


class RapidOcrEngine:
    """基于 rapidocr-onnxruntime 的实现（纯 onnx，无需系统级依赖）。"""

    available = True

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]

        self._engine = RapidOCR()

    def read_text(self, img: Image.Image) -> list[OcrLine]:
        import numpy as np

        result, _ = self._engine(np.asarray(img.convert("RGB")))
        lines: list[OcrLine] = []
        for item in result or []:
            try:
                box, text, score = item[0], item[1], float(item[2])
            except (IndexError, TypeError, ValueError):
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(
                OcrLine(
                    text=str(text),
                    confidence=score,
                    box=(int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))),
                )
            )
        return lines


def get_ocr_engine(prefer: bool = True) -> OcrEngine:
    """尝试加载 OCR 引擎，失败则返回空实现。"""
    if not prefer:
        return NullOcr()
    try:
        return RapidOcrEngine()
    except Exception as e:  # noqa: BLE001 - 任何导入/初始化失败都优雅降级
        log.info("OCR 不可用（%s），将只使用指纹匹配。可执行 pip install rapidocr-onnxruntime 启用。", e)
        return NullOcr()


# ---------------------------------------------------------------------
# 文字 -> 卡牌 匹配
# ---------------------------------------------------------------------


def _normalize(s: str) -> str:
    """去掉空白与常见标点，便于比对。"""
    out = []
    for ch in s:
        if ch.isspace():
            continue
        if ch in "！？。、，,.!?·・「」『』【】[]()（）:：;；'\"":
            continue
        out.append(ch)
    return "".join(out).lower()


def score_text_against_catalog(
    lines: list[OcrLine], catalog, card_ids: list[int] | None = None
) -> dict[int, float]:
    """把 OCR 文本与卡牌库做模糊比对，返回 ``{card_id: 命中分}``。

    命中分含义：卡名(prefix) 命中 +1.0，角色名命中 +0.4，两者都命中再 +0.3。
    """
    if not lines:
        return {}

    joined = _normalize(" ".join(ln.text for ln in lines))
    if not joined:
        return {}

    targets = card_ids if card_ids is not None else list(catalog.cards.keys())
    hits: dict[int, float] = {}

    for cid in targets:
        card = catalog.cards.get(cid)
        if card is None:
            continue
        title = _normalize(card.title(catalog.settings))
        char = _normalize(catalog.character_name(card))
        s = 0.0
        title_hit = bool(title) and len(title) >= 2 and title in joined
        char_hit = bool(char) and len(char) >= 2 and char in joined
        if title_hit:
            s += 1.0
        if char_hit:
            s += 0.4
        if title_hit and char_hit:
            s += 0.3
        if s > 0:
            hits[cid] = s
    return hits
