"""从游戏 UI 外观估计属性（用于缩小候选集）。

原理
----
游戏里卡面的属性指示有两种 UI 形式：

1. **边框色**——一些界面（比如乐队详情页）会在卡面外缘画一圈属性色；
2. **右上角图标**——「成员一览」这类列表界面用一张小图标表示属性。

算法两条路都试：

- 取图片外圈（边框区域）中**高饱和**像素，统计色相直方图，取峰值所在
  色相归类到最近的四属性；
- 取右上角一个小 ROI，统计里面高饱和像素的色相。

两者任一置信度达标就用。卡面缩略图往往带白底或低饱和外圈，**两种
途径在某些截图上会同时失效**——遇到这种情况就放弃，不强行猜。

星级不在此处估计：数星星对缩放/抗锯齿极其敏感，可靠性低。星级直接从
指纹匹配到的卡牌记录里读取即可。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

#: 四属性参考色相（0~360）
ATTRIBUTE_HUE: dict[str, float] = {
    "powerful": 0.0,
    "happy": 30.0,
    "pure": 120.0,
    "cool": 210.0,
}

#: 高饱和像素的最低饱和度（0~255）
MIN_SATURATION = 70
#: 高饱和像素的最低明度（0~255）
MIN_VALUE = 60
#: 认定该色相有效所需的最少像素数
MIN_PIXELS = 40
#: 属性提示的可信度门槛，达标才用它缩小候选集
ATTRIBUTE_HINT_MIN_CONF = 0.45


@dataclass(slots=True)
class AttributeHint:
    """属性估计结果。"""

    attribute: str | None
    confidence: float
    votes: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "attribute": self.attribute,
            "confidence": round(self.confidence, 3),
            "votes": {k: round(v, 3) for k, v in self.votes.items()},
        }


def _border_mask(h: int, w: int, band: float = 0.14) -> np.ndarray:
    """构造外圈边框区域掩码。"""
    bh = max(2, int(h * band))
    bw = max(2, int(w * band))
    mask = np.zeros((h, w), dtype=bool)
    mask[:bh, :] = True
    mask[-bh:, :] = True
    mask[:, :bw] = True
    mask[:, -bw:] = True
    return mask


def _hue_votes(hue: np.ndarray) -> dict[str, float]:
    """对一组色相按四属性做硬投票：以参考色相为中心 ±22.5° 算一票。

    四属性参考：powerful 0° / happy 30° / pure 120° / cool 210°，桶不重叠。
    落在桶外的不计票——比软投票（线性衰减）更能抵御相邻属性的干扰，
    例如图标里夹着的橙色装饰条不会把蓝色的酷炫拉到快乐。
    """
    h = ((hue.astype(np.float64) + 180.0) % 360.0) - 180.0
    votes: dict[str, float] = {}
    for a, ref in ATTRIBUTE_HUE.items():
        votes[a] = float(((h >= ref - 22.5) & (h <= ref + 22.5)).sum())
    return votes


def _votes_to_hint(votes: dict[str, float]) -> AttributeHint:
    total = sum(votes.values())
    if total <= 0:
        return AttributeHint(attribute=None, confidence=0.0, votes={})
    norm = {k: v / total for k, v in votes.items()}
    best = max(norm, key=lambda k: norm[k])
    return AttributeHint(attribute=best, confidence=norm[best], votes=norm)


def estimate_attribute(img: Image.Image, band: float = 0.14) -> AttributeHint:
    """估计卡面属性。返回 ``attribute=None`` 表示不确定。

    优先看**外圈边框色**（老的实现），主要用于带属性色边框的界面。
    """
    hsv = np.asarray(img.convert("HSV"), dtype=np.uint8)
    h, w, _ = hsv.shape
    mask = _border_mask(h, w, band)

    sat = hsv[:, :, 1][mask].astype(np.int16)
    val = hsv[:, :, 2][mask].astype(np.int16)
    hue = hsv[:, :, 0][mask].astype(np.float64) * 360.0 / 256.0

    keep = (sat >= MIN_SATURATION) & (val >= MIN_VALUE)
    if int(keep.sum()) < MIN_PIXELS:
        return AttributeHint(attribute=None, confidence=0.0, votes={})

    return _votes_to_hint(_hue_votes(hue[keep]))


#: 卡面右上角属性图标的归一化 ROI（相对图片宽高）。
ICON_ROI = (0.70, 0.05, 0.95, 0.25)


def estimate_attribute_icon(
    img: Image.Image, roi: tuple[float, float, float, float] = ICON_ROI
) -> AttributeHint:
    """估计卡面属性——通过右上角小图标。

    用于「成员一览」这类列表界面：外圈是白底，但卡面格右上角有一枚
    带色背景的属性图标（月牙/星星/火焰……）。

    实测发现**面板背景本身就是属性色**（不是橙色按钮），所以直接找 ROI
    里出现频率最高的色相，再归类到最近的属性就行。把饱和度大于 60 的像素
    收进 10° 一桶的直方图，找最高的桶；桶的总票数 > 30% 才算可信。

    算法：在归一化 ROI（默认图片右上角 25% 宽 × 20% 高）里：
    1. 滤掉白色（val > 245）和接近灰的像素（sat < 60）；
    2. 把剩余像素的 hue 分到 36 个 10° 桶里；
    3. 取最高的桶 → 该桶的 hue 中位 → 找最近的属性。
    """
    w, h = img.size
    x0 = int(w * roi[0]); x1 = max(x0 + 4, int(w * roi[2]))
    y0 = int(h * roi[1]); y1 = max(y0 + 4, int(h * roi[3]))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return AttributeHint(attribute=None, confidence=0.0, votes={})

    crop = img.crop((x0, y0, x1, y1))
    hsv = np.asarray(crop.convert("HSV"), dtype=np.uint8)
    sat = hsv[:, :, 1].astype(np.int16)
    val = hsv[:, :, 2].astype(np.int16)
    # 只留"有颜色"的像素：sat 够高就说明不是白/灰。
    # ⚠️ 不要再加 ``val <= 245`` 这类"排白"条件 —— 四属性的标准色里
    # 三个的 V 就是 255（#FF4D4D / #4D9BFF / #FFB84D），加了这个条件会把
    # 图标本体当白色滤掉，剩下的全是卡面画色，提示就会自信地报错属性。
    # 白色本身 sat≈0，靠饱和度已经排除了；这里只需要再排掉近黑的噪声像素。
    keep = (sat >= 60) & (val >= 30)
    if int(keep.sum()) < 6:
        return AttributeHint(attribute=None, confidence=0.0, votes={})

    hue = hsv[:, :, 0][keep].astype(np.float64) * 360.0 / 256.0
    bins = np.arange(0, 372, 10)  # 0,10,...,360
    hist, _ = np.histogram(hue, bins=bins)
    total = int(hist.sum())
    peak = int(np.argmax(hist))
    if hist[peak] / total < 0.30:
        # 直方图太分散，可能是被遮挡/图标太小
        return AttributeHint(attribute=None, confidence=0.0, votes={})

    # 该桶的色相中位（更稳定）
    lo, hi = bins[peak], bins[peak + 1]
    in_peak = hue[(hue >= lo) & (hue < hi)]
    peak_hue = float(np.median(in_peak)) if in_peak.size else (lo + 5)

    # 归到最近的属性
    best_attr = None
    best_dist = 999.0
    for a, ref in ATTRIBUTE_HUE.items():
        d = min(abs(peak_hue - ref), 360.0 - abs(peak_hue - ref))
        if d < best_dist:
            best_dist = d
            best_attr = a
    if best_attr is None or best_dist > 30:
        return AttributeHint(attribute=None, confidence=0.0, votes={})

    conf = float(hist[peak]) / total
    return AttributeHint(attribute=best_attr, confidence=conf, votes={best_attr: conf})


def estimate_attribute_combined(img: Image.Image) -> AttributeHint:
    """边框 + 图标两条路都试。

    实测经验：「成员一览」这类列表界面的卡面外圈是白底，不是属性色——边框
    路径在这种界面上经常把 JPEG 压缩留下的微弱偏色（多数偏红橙）报成
    "强力"。所以优先信图标（卡面格右上角就是为表达属性设计的），边框
    只在图标没读出来时兜底。
    """
    b = estimate_attribute_icon(img)
    if b.attribute is not None and b.confidence >= 0.40:
        return b
    a = estimate_attribute(img)
    if a.attribute is not None:
        return a
    return b
