"""bestdori 子包：公开 API 客户端与资源下载。"""

from .client import BANDS_ENDPOINT, CARDS_ENDPOINT, CHARACTERS_ENDPOINT, BestdoriClient

__all__ = [
    "BestdoriClient",
    "CARDS_ENDPOINT",
    "CHARACTERS_ENDPOINT",
    "BANDS_ENDPOINT",
]
