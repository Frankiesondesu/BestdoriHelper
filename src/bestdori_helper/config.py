"""全局配置：服务器区域、语言、目录布局。

Bestdori 的多语言数组下标顺序（由 API 实测确认）::

    [0] 日本語   [1] English   [2] 繁體中文   [3] 简体中文   [4] 한국어

卡面图片资源路径（需带 User-Agent，否则返回 SPA 外壳）::

    https://bestdori.com/assets/{server}/characters/resourceset/{resourceSetName}_rip/card_normal.png
    https://bestdori.com/assets/{server}/characters/resourceset/{resourceSetName}_rip/card_after_training.png

``card_after_training.png`` 仅在该卡有特训后卡面时存在，否则返回首页 HTML。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Server(str, Enum):
    """Bestdori 支持的服务器区域。"""

    JP = "jp"
    CN = "cn"
    TW = "tw"
    EN = "en"
    KR = "kr"


#: 语言下标 -> 人类可读名称
LANGUAGES: dict[int, str] = {
    0: "日本語",
    1: "English",
    2: "繁體中文",
    3: "简体中文",
    4: "한국어",
}

#: 服务器 -> 默认语言下标
SERVER_DEFAULT_LANG: dict[Server, int] = {
    Server.JP: 0,
    Server.CN: 3,
    Server.TW: 2,
    Server.EN: 1,
    Server.KR: 4,
}


class Attribute(str, Enum):
    """卡面属性（四属性）。"""

    POWERFUL = "powerful"
    COOL = "cool"
    PURE = "pure"
    HAPPY = "happy"


#: 属性 -> 中文名
ATTRIBUTE_CN: dict[str, str] = {
    "powerful": "强力",
    "cool": "酷炫",
    "pure": "纯洁",
    "happy": "快乐",
}

#: 属性 -> 主题色（用于 UI 与候选过滤）
ATTRIBUTE_COLOR: dict[str, str] = {
    "powerful": "#FF4D4D",
    "cool": "#4D9BFF",
    "pure": "#4DD98A",
    "happy": "#FFB84D",
}

#: 星级 -> 中文名
RARITY_CN: dict[int, str] = {
    1: "1★",
    2: "2★",
    3: "3★",
    4: "4★",
    5: "5★",
}

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 BestdoriHelper/0.1"
)


def _default_home() -> Path:
    """数据目录：可用 BESTDORI_HELPER_HOME 覆盖。"""
    env = os.environ.get("BESTDORI_HELPER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".bestdori-helper"


#: 卡图来源的说明文案（CLI 与 GUI 共用，避免两边说法不一致）
SOURCE_LABEL: dict[str, str] = {
    "thumb": "Bestdori 180x180 方形缩略图 thumb/chara/（取景与游戏内卡面格一致，推荐）",
    "original": "Bestdori 1334x1002 横向原图 characters/resourceset/",
}

#: 合法的卡图来源
IMAGE_SOURCES: tuple[str, ...] = ("thumb", "original")


@dataclass
class Settings:
    """运行期配置。"""

    server: Server = Server.CN
    lang: int | None = None
    home: Path = field(default_factory=_default_home)
    request_timeout: float = 30.0
    #: 卡图下载并发度。串行下载全量卡图要三个多小时，实测 3 秒/张；
    #: 并发 12 后整体快一个量级。Bestdori 是社区站，别调得太激进。
    max_concurrency: int = 12
    #: 指纹图统一缩放尺寸（宽, 高）
    fingerprint_size: tuple[int, int] = (256, 192)
    #: 指纹库用哪一套卡图建库。
    #: ``thumb`` = 180x180 方形缩略图（``thumb/chara/``），取景与游戏内卡面格一致；
    #: ``original`` = 1334x1002 横向原图（``characters/resourceset/``）。
    image_source: str = "thumb"

    #: 是否启用 SIFT 几何校验二次排序。
    #:
    #: 全局特征（pHash/dHash/分块颜色）分不出"同角色同属性的不同卡"，真实截图
    #: top-1 只有 67.9%；加上几何校验后 100%（137 个可评估格）。需要 opencv，
    #: 没装或关掉时自动退回旧路径。见 ``vision/verify.py``。
    verify_sift: bool = True

    #: 是否把等分格子收缩到真正的卡框上（见 ``vision/detect.refine_boxes``）。
    #:
    #: 等分网格切出来的是「格子」而不是「卡框」：实测格子 170x170、卡框只有
    #: 约 150x141，每边约 10px 留白；而相邻卡面之间只有 20~25px 间隙，格子的
    #: 上下边常常正好压在邻居卡面上 —— 切出来的图里就混进别的卡的一条边。
    #: 开启后每格会先贴合卡框再送去识别。
    refine_cells: bool = True

    def __post_init__(self) -> None:
        if self.lang is None:
            self.lang = SERVER_DEFAULT_LANG[self.server]
        if isinstance(self.server, str):
            self.server = Server(self.server)
        self.home = Path(self.home)

    # ---- 目录布局 ----------------------------------------------------

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"

    @property
    def data_dir(self) -> Path:
        return self.home / "data"

    @property
    def image_dir(self) -> Path:
        """Bestdori 卡面原图缓存（1334x1002）。"""
        return self.cache_dir / "images" / self.server.value

    @property
    def thumb_dir(self) -> Path:
        """Bestdori 卡面缩略图缓存（180x180，``thumb/chara/``）。

        和原图分开存：两套资源画幅不同，混在一个目录里靠文件名区分容易出事，
        而且指纹库必须知道自己是拿哪一套建的。
        """
        return self.cache_dir / "thumbs" / self.server.value

    @property
    def source_dir(self) -> Path:
        """当前 ``image_source`` 对应的卡图目录。"""
        return self.thumb_dir if self.image_source == "thumb" else self.image_dir

    @property
    def index_path(self) -> Path:
        """指纹库文件。

        **必须按 ``image_source`` 分开存** —— 两套卡图画幅不同，特征分布也
        不同，同一个文件被两边轮流覆盖会让识别结果莫名其妙地漂。``original``
        沿用旧文件名，免掉已建好的库要重跑一次。
        """
        if self.image_source == "original":
            return self.data_dir / f"fingerprints.{self.server.value}.npz"
        return self.data_dir / f"fingerprints.{self.server.value}.{self.image_source}.npz"

    @property
    def cards_json(self) -> Path:
        """卡牌元数据快照。"""
        return self.data_dir / f"cards.{self.server.value}.json"

    @property
    def characters_json(self) -> Path:
        return self.data_dir / f"characters.{self.server.value}.json"

    @property
    def bands_json(self) -> Path:
        return self.data_dir / f"bands.{self.server.value}.json"

    @property
    def inventory_path(self) -> Path:
        """用户持有的卡牌清单。"""
        return self.home / "inventory.json"

    @property
    def inbox_dir(self) -> Path:
        """默认的截图投放目录。"""
        d = self.home / "screenshots"
        return d

    def ensure_dirs(self) -> None:
        for d in (
            self.cache_dir,
            self.data_dir,
            self.image_dir,
            self.thumb_dir,
            self.home,
            self.inbox_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    # ---- 文本取值 ----------------------------------------------------

    def pick(self, values: list[str] | None, fallback: str = "") -> str:
        """从 Bestdori 的多语言数组中取出当前语言的值。"""
        if not values:
            return fallback
        idx = self.lang or 0
        if 0 <= idx < len(values) and values[idx]:
            return values[idx]
        for v in values:
            if v:
                return v
        return fallback
