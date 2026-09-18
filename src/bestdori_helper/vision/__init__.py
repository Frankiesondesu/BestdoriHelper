"""vision 子包：把截图变成卡牌 ID。"""

from .detect import Box, crop_box, detect_boxes
from .features import ImageFeatures, compute_features
from .index import BuildStats, FingerprintIndex, Match, build_index
from .recognize import RecognizedItem, RecognitionResult, recognize_image

__all__ = [
    "Box",
    "crop_box",
    "detect_boxes",
    "ImageFeatures",
    "compute_features",
    "BuildStats",
    "FingerprintIndex",
    "Match",
    "build_index",
    "RecognizedItem",
    "RecognitionResult",
    "recognize_image",
]
