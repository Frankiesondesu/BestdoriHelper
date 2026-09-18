"""从整屏截图中切分出单张卡面缩略图。

三种模式
--------
``single``  整张图就是一张卡面（或只关心最大的一块内容）。
``grid``    用户给定行列数，等分切分（最稳，推荐先用这个）。
``auto``    自动检测。

自动检测的三级策略
------------------
按可靠性依次尝试，前一级不成立才退到下一级：

1. **周期性检测**（自相关找等距重复结构）—— 卡牌列表本质是周期性的，
   无论卡框紧贴还是有背景间隙、卡面本身有没有大片平坦区域，只要网格规整
   就能测出周期。**这是主力路径。**
2. **纹理带检测**（局部方差）—— 卡面有纹理、卡框平坦时有效。
3. **背景色带检测** —— 背景纯净且与卡框对比明显时有效。

都不成立时把整张图当作单一卡面。

两个历史教训（都踩过）
----------------------
**一、为什么不用「与背景色差异」当主力。** 最初用四边中位数当背景色，
差异大的算内容。这在**卡框紧贴、没有背景间隙**的布局下会整块吞掉 ——
相邻卡面的彩色边框彼此相接，中间没有背景像素，于是所有卡被识别成一个
巨大的内容块。

**二、为什么周期检测前要先剥掉外边距。** 周期换算格数靠「总长 ≈ 格数 ×
周期」，但真实截图四周常有一圈纯背景留白，会让比例对不上。实测 2 行网格
高 288px、周期 130px 时比例是 2.215，超出容差被当成假周期丢掉，退回到
纹理路径切错。**合成测试图恰好没有外边距，所以这个缺陷一直没暴露，直到
拿真实卡图跑才现形。** 现在统一先 :func:`_active_extent` 剥边距再做比例校验。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

#: 局部方差的窗口边长（像素）
TEXTURE_WINDOW = 5
#: 判定「有纹理」的方差阈值（灰度方差，0~65025）
TEXTURE_VAR_THRESHOLD = 18.0
#: 一列/行中被判定为有纹理的像素占比超过该值，才算作卡面所在列/行
TEXTURE_FRACTION = 0.35
#: 自相关峰值的最低强度，低于此值认为没有找到可靠周期
PERIOD_MIN_PEAK = 0.20
#: 自相关求周期时的最小/最大候选周期（像素）
PERIOD_MIN = 40
#: 估计内容范围时，边缘能量低于峰值该比例的位置算作空白外边距
EXTENT_FLOOR = 0.05
#: 背景色法的差异阈值（0~255，三通道之和）
DEFAULT_DIFF_THRESHOLD = 28
#: 判定为「空白间隙」的最小连续像素数
MIN_GUTTER = 4
#: 单个单元格的最小边长（像素）
MIN_CELL = 40


@dataclass(slots=True)
class Box:
    """一个裁剪框（左上角 + 宽高）。"""

    x: int
    y: int
    w: int
    h: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


# ---------------------------------------------------------------------
# 一维投影 -> 区间
# ---------------------------------------------------------------------


def _content_bands(
    profile: np.ndarray, min_gutter: int = MIN_GUTTER, min_size: int = MIN_CELL
) -> list[tuple[int, int]]:
    """从一维「是否有内容」投影中提取连续内容区间。"""
    bands: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0
    for i, has in enumerate(profile):
        if has:
            if start is None:
                start = i
            gap = 0
        else:
            if start is not None:
                gap += 1
                if gap >= min_gutter:
                    end = i - gap + 1
                    if end - start >= min_size:
                        bands.append((start, end))
                    start = None
                    gap = 0
    if start is not None:
        end = len(profile) - (gap if gap < min_gutter else 0)
        if end - start >= min_size:
            bands.append((start, end))
    return bands


# ---------------------------------------------------------------------
# 内容检测
# ---------------------------------------------------------------------


def _box_mean(a: np.ndarray, k: int) -> np.ndarray:
    """用积分图做 k×k 均值滤波（边界复制），不依赖 scipy。"""
    if k < 2:
        return a
    pad = k // 2
    padded = np.pad(a, pad, mode="edge")
    ii = np.cumsum(np.cumsum(padded, axis=0), axis=1)
    ii = np.pad(ii, ((1, 0), (1, 0)))

    h, w = a.shape
    y0 = np.arange(h)
    x0 = np.arange(w)
    y1 = y0 + k
    x1 = x0 + k

    total = (
        ii[np.ix_(y1, x1)]
        - ii[np.ix_(y0, x1)]
        - ii[np.ix_(y1, x0)]
        + ii[np.ix_(y0, x0)]
    )
    return total / float(k * k)


def _texture_profiles(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """基于局部纹理方差，返回 (列投影, 行投影)，True 表示该列/行属于卡面。"""
    gray = (
        0.299 * rgb[:, :, 0].astype(np.float64)
        + 0.587 * rgb[:, :, 1].astype(np.float64)
        + 0.114 * rgb[:, :, 2].astype(np.float64)
    )
    mean = _box_mean(gray, TEXTURE_WINDOW)
    mean_sq = _box_mean(gray * gray, TEXTURE_WINDOW)
    var = np.maximum(mean_sq - mean * mean, 0.0)

    textured = var > TEXTURE_VAR_THRESHOLD
    col_profile = textured.mean(axis=0) >= TEXTURE_FRACTION
    row_profile = textured.mean(axis=1) >= TEXTURE_FRACTION
    return col_profile, row_profile


def _background_color(arr: np.ndarray) -> np.ndarray:
    """用四条边缘的中位数估计背景色。"""
    top = arr[:4, :, :].reshape(-1, 3)
    bottom = arr[-4:, :, :].reshape(-1, 3)
    left = arr[:, :4, :].reshape(-1, 3)
    right = arr[:, -4:, :].reshape(-1, 3)
    edge = np.concatenate([top, bottom, left, right], axis=0)
    return np.median(edge, axis=0)


def _color_profiles(rgb: np.ndarray, threshold: int) -> tuple[np.ndarray, np.ndarray]:
    """基于「与背景色差异」的投影（纹理法的兜底）。"""
    bg = _background_color(rgb)
    diff = np.abs(rgb.astype(np.int16) - bg[None, None, :]).sum(axis=2)
    content = diff > threshold * 3
    return content.mean(axis=0) > 0.12, content.mean(axis=1) > 0.12


def _edge_profiles(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """逐列/逐行的边缘能量（相邻像素灰度差的绝对值之和）。"""
    gray = (
        0.299 * rgb[:, :, 0].astype(np.float64)
        + 0.587 * rgb[:, :, 1].astype(np.float64)
        + 0.114 * rgb[:, :, 2].astype(np.float64)
    )
    col_edge = np.abs(np.diff(gray, axis=1)).sum(axis=0)   # 长度 w-1
    row_edge = np.abs(np.diff(gray, axis=0)).sum(axis=1)   # 长度 h-1
    return col_edge, row_edge


def _candidate_periods(
    profile: np.ndarray, min_period: int = PERIOD_MIN, top_n: int = 8
) -> list[int]:
    """返回按自相关强度排序的候选周期。

    为什么需要「候选列表」而不是只取最强峰：卡面图案本身有重复结构，
    它的自相关峰可能比网格周期还强。实测就遇到过 —— 卡面高度 90px、
    网格周期 100px 时，最强峰落在 90 上。只取一个峰会直接失败，
    而按强度依次尝试就能在第二个候选上命中 100。
    """
    n = len(profile)
    if n < min_period * 2:
        return []
    x = profile - profile.mean()
    if float(np.abs(x).sum()) < 1e-6:
        return []

    ac = np.correlate(x, x, mode="full")[n - 1 :]
    if ac[0] <= 0:
        return []
    ac = ac / ac[0]

    # 搜索范围上限用 n 而不是 n//2。看似应该限制在 n/2（至少容纳两个周期），
    # 但一维差分让 profile 比原图少 1 个像素：H=200 的 2 行网格周期正好是
    # 100，而 n=199 时 n//2=99 —— 真周期刚好被截断，实测就栽在这里。
    # 放宽到 n 是安全的：周期过大会被 _count_from_period 的格数校验挡掉。
    if n <= min_period:
        return []
    seg = ac[min_period:n]

    peaks: list[tuple[int, float]] = []
    for i in range(1, len(seg) - 1):
        if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] >= PERIOD_MIN_PEAK:
            peaks.append((min_period + i, float(seg[i])))

    # 自相关曲线可能有平台，局部极大筛选会漏掉真正的最高点，补上全局最大
    gmax = min_period + int(np.argmax(seg))
    if float(ac[gmax]) >= PERIOD_MIN_PEAK and gmax not in {p for p, _ in peaks}:
        peaks.append((gmax, float(ac[gmax])))

    peaks.sort(key=lambda t: -t[1])
    return [p for p, _ in peaks[:top_n]]


def _dominant_period(profile: np.ndarray, min_period: int = PERIOD_MIN) -> int | None:
    """最强候选周期（保留给单值场景使用）。"""
    cands = _candidate_periods(profile, min_period=min_period)
    return cands[0] if cands else None


def _count_from_period(total: int, period: int | None, min_cell: int = MIN_CELL) -> int | None:
    """把周期换算成单元格个数，并做合理性校验。

    校验的是「总宽是不是周期的近似整数倍」—— 如果测到的周期跟总宽对不上，
    说明那个自相关峰不是真正的网格周期（可能是卡片内部图案的重复），
    此时宁可返回 None 让上层走别的策略，也不要切出错误的格数。
    """
    if not period or period < min_cell:
        return None
    ratio = total / period
    count = int(round(ratio))
    if count < 2:
        return None
    if abs(ratio - count) > 0.18:
        return None
    return count


# ---------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------


def _first_valid_count(profile: np.ndarray, total: int, min_cell: int = MIN_CELL) -> int | None:
    """在候选周期里找出第一个能推出合理格数的，返回格数。"""
    for period in _candidate_periods(profile):
        count = _count_from_period(total, period, min_cell=min_cell)
        if count:
            return count
    return None


def _active_extent(profile: np.ndarray, min_size: int = MIN_CELL) -> tuple[int, int] | None:
    """估计一维能量曲线里「有内容」的起止范围，用来剥掉首尾的空白外边距。

    为什么需要它：周期换算格数靠的是「总长 ≈ 格数 × 周期」这个比例，
    但真实截图四周常有一圈纯背景外边距（留白、安全区），会让比例对不上。
    实测：2 行网格高 288px、周期 130px 时比例是 2.215，超出容差被当成
    假周期丢掉，结果退回到较弱的纹理投影路径上切错。**合成测试图恰好没有
    外边距，所以这个问题一直没暴露，直到拿真实卡图跑才现形。**

    阈值取**峰值的固定比例**而不是中位数：内容可能占满整幅图，此时中位数
    本身就是内容水平，拿它当基线会把内容一起切掉。背景区的边缘能量接近 0，
    所以按峰值比例切很安全。
    """
    n = len(profile)
    if n < min_size * 2:
        return None
    peak = float(profile.max())
    if peak <= 0:
        return None
    active = np.flatnonzero(profile > peak * EXTENT_FLOOR)
    if active.size == 0:
        return None
    start, end = int(active[0]), int(active[-1]) + 1
    if end - start < min_size * 2:
        return None
    return start, end


def _grid_from_profile(
    profile: np.ndarray, total: int, min_cell: int = MIN_CELL
) -> tuple[int, int, int] | None:
    """从一维边缘能量曲线推断网格，返回 ``(格数, 周期, 起始相位)``。

    相位是相对整幅图的绝对偏移（已把内容起点加回去），可以直接喂给
    :func:`_bands_from_period`。

    周期检测与比例校验都在**剥掉外边距后的内容段**上做，这样"首尾留白"
    不会污染比例；而 90px 卡面内重复 vs 100px 网格周期这类假周期，
    仍然会被严格的比例校验挡掉。
    """
    ext = _active_extent(profile, min_size=min_cell)
    if ext is None:
        return None
    start, end = ext
    span = end - start
    sub = profile[start:end]

    for period in _candidate_periods(sub):
        count = _count_from_period(span, period, min_cell=min_cell)
        if count is None:
            continue
        phase = _best_phase(sub, period, count, span)
        return count, period, start + phase
    return None


def _bands_from_grid(total: int, grid: tuple[int, int, int] | None, count: int) -> list[tuple[int, int]]:
    """把网格推断结果转成内容区间；没推断出来就退回等分。"""
    if grid is None:
        return _even_split(total, count)
    _count, period, phase = grid
    return _bands_from_period(total, period, count, phase)


def _best_phase(profile: np.ndarray, period: int, count: int, total: int) -> int:
    """找最佳相位，使预测出的格子边界落在边缘能量最高处。

    等分切分默认从 0 开始，但真实截图左侧常有一条外边距，会导致每个框都
    偏移若干像素。用相位搜索对齐一下，框就贴得准很多。

    ⚠️ 相位搜索范围必须限制在 ``[0, total - count*period]`` 内。
    如果放开到 ``[0, period)``，网格会被整体推出画布，最后一格被裁掉，
    格数就少了一个 —— 这是实测踩过的坑。
    """
    n = len(profile)
    if period <= 1 or n == 0 or count < 1:
        return 0

    max_phase = int(max(0, total - count * period))
    if max_phase <= 0:
        return 0  # 网格刚好铺满，没有可调空间
    hi = min(max_phase, period - 1)

    best_phase, best_score = 0, -1.0
    for phase in range(hi + 1):
        score = 0.0
        for k in range(count + 1):
            idx = int(phase + k * period) - 1
            if 0 <= idx < n:
                score += float(profile[idx])
        if score > best_score:
            best_score, best_phase = score, phase
    return best_phase


def _bands_from_period(total: int, period: int, count: int, phase: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for k in range(count):
        a = int(phase + k * period)
        b = int(phase + (k + 1) * period)
        a, b = max(0, a), min(total, b)
        if b - a >= MIN_CELL:
            out.append((a, b))
    return out


def _boxes_from_bands(
    row_bands: list[tuple[int, int]], col_bands: list[tuple[int, int]], w: int, h: int, margin: int
) -> list[Box]:
    boxes: list[Box] = []
    for (y0, y1) in row_bands:
        for (x0, x1) in col_bands:
            x = max(0, x0 - margin)
            y = max(0, y0 - margin)
            bx = Box(x=x, y=y, w=min(w, x1 + margin) - x, h=min(h, y1 + margin) - y)
            if bx.w >= MIN_CELL and bx.h >= MIN_CELL:
                boxes.append(bx)
    return boxes


# ---------------------------------------------------------------------
# 区域（Region）
# ---------------------------------------------------------------------
#
# 真实游戏截图里，卡面网格**嵌在一整套 UI 中**：左侧菜单、顶栏、底部按钮
# 各有各的周期性，全图求自相关只会测出 UI 的周期。
#
# 实测：一张 2400×1080 的「成员一览」截图，真实是 7×4 = 28 格，
# 直接跑 auto 却切出 156 个框（26×6）。加区域约束后列检测立刻正确。
#
# 所以对这类截图，正确做法是让用户框出卡面区域（同类界面只需框一次，
# 存成预设复用），而不是硬追求全自动。

Region = tuple[float, float, float, float]


def region_to_px(
    region: Region | None, w: int, h: int, min_size: int = MIN_CELL
) -> tuple[int, int, int, int] | None:
    """把归一化区域 ``(x0, y0, x1, y1)`` 换成像素坐标并夹到图内。

    坐标用 0~1 归一化（相对图片宽高），这样同一套预设能跨不同分辨率复用 ——
    手机截图分辨率经常变，用绝对像素存预设一换设备就废了。
    """
    if region is None:
        return None
    try:
        x0f, y0f, x1f, y1f = (float(v) for v in region)
    except (TypeError, ValueError):
        return None
    # 容错：允许用户把两个角写反
    x0f, x1f = min(x0f, x1f), max(x0f, x1f)
    y0f, y1f = min(y0f, y1f), max(y0f, y1f)
    x0 = max(0, min(w, int(round(x0f * w))))
    x1 = max(0, min(w, int(round(x1f * w))))
    y0 = max(0, min(h, int(round(y0f * h))))
    y1 = max(0, min(h, int(round(y1f * h))))
    if x1 - x0 < min_size or y1 - y0 < min_size:
        return None
    return x0, y0, x1, y1


# ---------------------------------------------------------------------
# 按卡面实际位置切（背景间隙法）
# ---------------------------------------------------------------------
#
# 为什么光靠"等分 + 贴合"不够：等分那一步假定卡面网格正好铺满被切的矩形，
# 而真实截图里卡面网格是**嵌在 UI 中间**的（左侧菜单、顶栏、底部按钮）。
# 实测把整张 2400x1080 按 4x7 等分，得到的是 343x270 的框 —— 连菜单都算进去了；
# 而且贴合一格也救不回来，因为整图等分框里全是 UI 内容，找不到背景分界。
#
# 卡面网格有两个 UI 不会有的特征：
#
# 1. **横向间隙是横贯整片卡面区的长背景带。** 实测 4 条卡面行间隙都在
#    x=684~1935 上连续是背景（长 1251px），位置一致、反复出现。
# 2. **这些间隙的 y 间距等距**（卡面行高一致，实测 148px）。
#
# 所以先靠特征 1 找到卡面区，再在区内按背景间隙切格子 —— 切出来的框直接就是
# 卡面的实际位置，不需要先等分再收缩。

#: 「横贯卡面区的长背景带」的最短长度（像素）。
#: 取 240 是留足余量：实测卡面区上的长带约 1250px，而零碎的 UI 间隙远小于此。
AREA_MIN_SPAN = 240
#: 长背景带位置聚类的容差（像素）
AREA_POS_TOL = 16
#: 判定一行/列是「背景」的内容像素占比上限。
#:
#: 实测（卡面区内 1282x702 的样本）：内容行的占比中位数是 **0.61**，而卡面行
#: 之间的间隙行是 **0.02~0.23** —— 取 0.25 落在两者之间，两边都留了余量。
#:
#: 早先写的是 0.05，那是在整幅图上量的数值。区域内不一样：卡面的柔边会溢出到
#: 间隙里，把占比抬上去。阈值太严的后果很具体 —— 实测第 3 张截图有两行卡面
#: 被粘成了一条 328px 的带（正好两行高），于是只切出 14 格。
GAP_CONTENT_MAX = 0.25
#: 一组内容带的边长相差不超过这个比例，才算"同一批卡面"
BAND_SIZE_TOL = 0.20
#: 卡面区里的内容像素占比下限（卡面区大部分是画面，不会是一片空白）
AREA_MIN_CONTENT = 0.15


def _group_consecutive(values: list[int], tol: int = 2) -> list[list[int]]:
    """把排好序的整数切成连续段（相邻差不超 ``tol`` 归为一段）。"""
    out: list[list[int]] = []
    for v in values:
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def _longest_bg_runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """对矩阵的**每一行**求最长连续背景段，返回 ``(起, 止)`` 两个数组。

    必须向量化：整张 2400x1080 有一百万个像素，用 Python 循环逐行扫要好几秒，
    而这个检测每次识别都要跑。技巧是把每个内容像素当成一个"断点"，
    用前缀最大值传播断点位置，当前位置减去断点位置就是连续长度。
    """
    n, m = mask.shape
    if n == 0 or m == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    idx = np.arange(m)
    reset = np.where(mask, idx, -1)
    np.maximum.accumulate(reset, axis=1, out=reset)
    run = idx[None, :] - reset
    end = run.argmax(axis=1)
    length = run[np.arange(n), end]
    return end - length + 1, end + 1


def _largest_consistent_group(
    bands: list[tuple[int, int]], tol: float = BAND_SIZE_TOL
) -> list[tuple[int, int]]:
    """把长度接近的带归成一组，返回最大的那组（保持原有顺序）。

    用途：区域内除了卡面行，还可能混进标题栏、按钮之类高度不同的内容带。
    卡面行的高度是一致的，所以按高度分组、取最大的一组就能把它们挑出来。
    """
    if not bands:
        return []
    best: list[tuple[int, int]] = []
    for a, b in bands:
        size = b - a
        if size <= 0:
            continue
        group = [(x, y) for x, y in bands if abs((y - x) - size) <= tol * size]
        if len(group) > len(best):
            best = group
    return best


def detect_card_area(
    rgb: np.ndarray, *, min_span: int = AREA_MIN_SPAN
) -> Box | None:
    """自动找出卡面网格所在的区域；找不到返回 ``None``。

    判据是**横贯整片卡面区的长背景带**（见本节开头）。找到的长带会按位置聚类，
    取最大的一组，其 ``(起, 止)`` 中位数就是卡面区的水平范围。
    """
    h, w = rgb.shape[:2]
    if h < MIN_CELL * 2 or w < MIN_CELL * 2:
        return None

    mask = content_mask(rgb)
    starts, ends = _longest_bg_runs(mask)
    lengths = ends - starts
    keep = np.flatnonzero(lengths >= min_span)
    if keep.size < 2:
        return None

    # 以**最长**的那条带为种子来聚类。
    #
    # 卡面区的横向间隙是整幅图里最长的等距重复背景带（实测 1251px），
    # 而左侧菜单项之间的空隙只有约 350px。早先是按"位置中位数"聚类，
    # 结果挑中了菜单区 —— 菜单项之间的空隙**行数更多**，中位数被带偏了。
    # 按长度取种子就没有这个问题。
    seed = int(keep[int(np.argmax(lengths[keep]))])
    a0, b0 = int(starts[seed]), int(ends[seed])
    core = [
        (int(y), int(starts[y]), int(ends[y]))
        for y in keep
        if abs(int(starts[y]) - a0) <= AREA_POS_TOL
        and abs(int(ends[y]) - b0) <= AREA_POS_TOL
    ]
    if len(core) < 2:
        return None

    a0 = int(np.median([c[1] for c in core]))
    b0 = int(np.median([c[2] for c in core]))
    ys = sorted(c[0] for c in core)

    # ys 是"长背景带"所在的行，把它们切成连续段 —— 每一段就是一条横向间隙。
    # **卡面阵列至少有三条**（顶、行间、底）：只有两条说明图上只有孤零零
    # 一块内容，不是阵列；一条都没有更谈不上。不加这一条的话，整片纯色
    # （比如空白图）会被当成长带而返回整张图。
    if len(_group_consecutive(ys)) < 3:
        return None

    # 纵向**不外扩**：实测第一条长带 y=276、最后一条 y=961，已经刚好把卡面区
    # 包住（真实卡面区是 292~972）。早先按"一个行高"外扩，正好把顶栏和底部
    # 按钮放了进来 —— 那些 UI 元素会污染区域内的行投影，结果切出一堆竖长条。
    y0, y1 = max(0, ys[0]), min(h, ys[-1] + 1)
    # 横向略微外扩无妨（左边是菜单、右边是空白，不会进来周期性结构），
    # 反倒能兜住卡面柔边没完全进入背景带的情况。
    x0 = max(0, a0 - AREA_POS_TOL)
    x1 = min(w, b0 + AREA_POS_TOL)
    if x1 - x0 < MIN_CELL or y1 - y0 < MIN_CELL:
        return None
    # 区域内得有实在的内容 —— 卡面区大部分是画面，不会是一片空白
    if float(mask[y0:y1, x0:x1].mean()) < AREA_MIN_CONTENT:
        return None
    return Box(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def split_by_gaps(
    rgb: np.ndarray, area: Box, *, min_size: int = MIN_CELL
) -> list[Box] | None:
    """在 ``area`` 内按背景间隙切出卡框；切不出规整网格就返回 ``None``。

    先按行找背景间隙得到「卡面行」，再在每一行内按列找背景间隙得到「卡面列」，
    交叉即卡框。**要求所有卡面行的列数一致** —— 不一致说明有空槽位或混进了 UI，
    这种情况不硬猜，退回上层逻辑。
    """
    h, w = rgb.shape[:2]
    x0, y0 = max(0, area.x), max(0, area.y)
    x1, y1 = min(w, area.x + area.w), min(h, area.y + area.h)
    if x1 - x0 < min_size or y1 - y0 < min_size:
        return None

    mask = content_mask(rgb[y0:y1, x0:x1])

    row_has = mask.mean(axis=1) > GAP_CONTENT_MAX
    rows = _largest_consistent_group(_content_bands(row_has, min_size=min_size))
    if not rows:
        return None

    cols_per_row: list[list[tuple[int, int]]] = []
    for ra, rb in rows:
        col_has = mask[ra:rb].mean(axis=0) > GAP_CONTENT_MAX
        cols = _content_bands(col_has, min_size=max(MIN_CELL // 2, min_size // 2))
        if not cols:
            return None
        cols_per_row.append(cols)

    # 列的划分取**众数**那一行的结果，行则全部保留。
    #
    # 为什么不是"所有行的列数必须完全一致"：个别行的卡面上会有一条低内容占比的
    # 竖带（浅色衣料之类），被当成间隙多切一刀 —— 实测第 1、5 张截图就各有
    # 一行切成 8 列，而其余 3 行都是 7 列。原来要求完全一致，结果是整张图退回
    # 旧的等分路径（切成 156 格，全切在 UI 上）。
    #
    # 卡的列位置在所有行上是一样的，所以拿多数行的划分当准绳即可；
    # 只有分歧确实很大时才放弃。
    counts = [len(c) for c in cols_per_row]
    mode_count = max(set(counts), key=counts.count)
    if counts.count(mode_count) * 2 < len(rows):
        return None  # 各行分歧太大，不猜
    cols = next(c for c in cols_per_row if len(c) == mode_count)
    if len(cols) < 2 and len(rows) < 2:
        return None

    return [
        Box(x=x0 + cx, y=y0 + ra, w=ce - cx, h=rb - ra)
        for ra, rb in rows
        for cx, ce in cols
    ]


def _fits_grid(boxes: list[Box], rows: int | None, cols: int | None) -> bool:
    """间隙切分的结果是否满足调用方对行列数的要求。

    调用方给了 ``rows`` / ``cols`` 就按数量校验（不符宁可退回等分，也不自作主张）；
    没给则只要切出两个以上格子就接受。
    """
    if len(boxes) < 2:
        return False
    if rows and cols and len(boxes) != rows * cols:
        return False
    return True


def _grow(boxes: list[Box], w: int, h: int, margin: int) -> list[Box]:
    """把每个框向外扩张 ``margin`` 像素（夹在图内）。"""
    if margin <= 0:
        return boxes
    out: list[Box] = []
    for b in boxes:
        x, y = max(0, b.x - margin), max(0, b.y - margin)
        out.append(
            Box(x, y, min(w, b.x + b.w + margin) - x, min(h, b.y + b.h + margin) - y)
        )
    return out


def detect_boxes(
    img: Image.Image,
    *,
    threshold: int = DEFAULT_DIFF_THRESHOLD,
    rows: int | None = None,
    cols: int | None = None,
    margin: int = 0,
    region: Region | None = None,
    prefer_gaps: bool = True,
) -> list[Box]:
    """切分出一组卡面候选框。

    :param rows: 指定行数，为 None 时自动推断
    :param cols: 指定列数，为 None 时自动推断
    :param margin: 每个框向外扩张的像素数
    :param region: 卡面区域 ``(x0, y0, x1, y1)``，0~1 归一化。给定时只在该
        区域内切分，返回的框坐标仍是**整幅图**的坐标系（可直接喂给
        :func:`crop_box`）。
    :param prefer_gaps: 先尝试「按卡面实际位置切」（在区域内找背景间隙，
        见本节末尾）。**整屏 UI 截图必须靠它** —— 光靠等分 + 贴合救不回来，
        因为整图等分框里全是 UI 内容，一个背景分界都找不到。切不出规整网格
        时自动退回下面的路径，所以关掉它只是回到"纯等分 + 贴合"。

    自动推断的优先级：

    1. **按卡面实际位置切**（背景间隙法，``prefer_gaps=True`` 时）—— 最贴合
       真实界面：框直接落在卡面上，不需要先等分再收缩
    2. **周期性检测**（自相关找等距重复结构）—— 网格规整时最稳，
       不受卡框紧贴/有间隙、卡面有无平坦区域影响
    3. **纹理带检测**（局部方差）—— 卡面有纹理、卡框平坦时有效
    4. **背景色带检测** —— 背景纯净且与卡框对比明显时有效
    5. 都不成立 → 整张图当作单一卡面
    """
    w0, h0 = img.size
    rgb = np.asarray(img.convert("RGB"))
    h, w, _ = rgb.shape

    # 0) 先试「按卡面实际位置切」：在区域内找背景间隙直接切格子。
    #    区域优先用调用方给的 ``region``，没给就自动找（见上一节）。
    #    这一步解决的是"卡面网格嵌在 UI 里"的情况 —— 实测把整张 2400x1080
    #    按 4x7 等分会得到 343x270 的框，连左侧菜单都算进去了。
    px = region_to_px(region, w0, h0)
    if prefer_gaps:
        area = (
            Box(px[0], px[1], px[2] - px[0], px[3] - px[1])
            if px is not None
            else detect_card_area(rgb)
        )
        if area is not None:
            by_gap = split_by_gaps(rgb, area)
            if by_gap and _fits_grid(by_gap, rows, cols):
                return _grow(by_gap, w, h, margin)

    # 限定区域时：退回在区域内做等分/周期检测，再把坐标平移回整幅图
    if px is not None:
        x0, y0, x1, y1 = px
        sub = img.crop(px)
        inner = detect_boxes(
            sub,
            threshold=threshold,
            rows=rows,
            cols=cols,
            margin=margin,
            prefer_gaps=prefer_gaps,
        )
        return [Box(b.x + x0, b.y + y0, b.w, b.h) for b in inner]

    # 行列都指定了 —— 直接等分，这是最可控的路径
    if rows and rows > 0 and cols and cols > 0:
        return _boxes_from_bands(_even_split(h, rows), _even_split(w, cols), w, h, margin)

    n_cols, n_rows = cols, rows
    col_grid: tuple[int, int, int] | None = None
    row_grid: tuple[int, int, int] | None = None
    if n_cols is None or n_rows is None:
        col_edge, row_edge = _edge_profiles(rgb)
        if n_cols is None:
            col_grid = _grid_from_profile(col_edge, w)
            n_cols = col_grid[0] if col_grid else None
        if n_rows is None:
            row_grid = _grid_from_profile(row_edge, h)
            n_rows = row_grid[0] if row_grid else None

        if n_cols and n_rows and n_cols >= 2 and n_rows >= 2:
            col_bands = _bands_from_grid(w, col_grid, n_cols)
            row_bands = _bands_from_grid(h, row_grid, n_rows)
            if col_bands and row_bands:
                return _boxes_from_bands(row_bands, col_bands, w, h, margin)

    # 退回投影带法：纹理优先，背景色兜底
    col_profile, row_profile = _texture_profiles(rgb)
    col_bands = _content_bands(col_profile)
    row_bands = _content_bands(row_profile)

    if len(col_bands) < 2 and len(row_bands) < 2:
        c2, r2 = _color_profiles(rgb, threshold)
        b2c, b2r = _content_bands(c2), _content_bands(r2)
        if len(b2c) * len(b2r) > len(col_bands) * len(row_bands):
            col_bands, row_bands = b2c, b2r

    if rows and rows > 0:
        row_bands = _even_split(h, rows)
    if cols and cols > 0:
        col_bands = _even_split(w, cols)

    if not col_bands or not row_bands:
        # 没有检测到网格 —— 整张图作为单一卡面
        return [Box(margin, margin, w - 2 * margin, h - 2 * margin)]

    return _boxes_from_bands(row_bands, col_bands, w, h, margin)


def _even_split(total: int, parts: int) -> list[tuple[int, int]]:
    step = total / parts
    return [(int(i * step), int((i + 1) * step)) for i in range(parts)]


def crop_box(img: Image.Image, box: Box, *, inset: float = 0.0) -> Image.Image:
    """按框裁剪；``inset`` 为向内收缩比例（0.05 = 四周各收 5%）。"""
    x0, y0, x1, y1 = box.as_tuple()
    if inset > 0:
        dw = int((x1 - x0) * inset)
        dh = int((y1 - y0) * inset)
        x0, y0, x1, y1 = x0 + dw, y0 + dh, x1 - dw, y1 - dh
    return img.crop((x0, y0, x1, y1))


# ---------------------------------------------------------------------
# 卡框贴合（refine）
# ---------------------------------------------------------------------
#
# 等分网格切出来的是「格子」，不是「卡框」。
#
# 实测（2400x1080 的真实截图、4x7 网格）：格子是 170x170，而卡框只有约
# 150x145 —— 每边约 10px 是背景留白。更麻烦的是相邻卡面之间的间隙只有
# 20~25px，格子的上下边常常正好压在邻居卡面的边缘上，切出来的图里就混进了
# 别的卡的一条边（表现为"交界处混乱"）。
#
# 做法：从格子的四条边分别向内扫描 —— 先跳过开头那段内容（那可能是相邻卡面
# 探进来的边），再跳过背景带，遇到的第一段连续内容就是本格卡框的那条边。
#
# 三个设计要点：
#
# **一、为什么"从边界扫描"而不是"找包含中心的整段内容"。** 卡面中间可能出现
# 大片低饱和区域（浅色衣料、天空、白底）。按"整段内容"去找边界，会把这种
# 区域误当成卡框外的背景，一刀切掉半张卡 —— 实测在合成基准上把 170px 的格子
# 切到过 80px。从边界出发的扫描够不到卡面中间，天然免疫这个问题。
#
# **二、为什么不向外扩展。** 向外扩展有吃到相邻卡面的风险（那正是本功能要
# 消除的问题），而向内收缩只会让切出来的图更干净。代价是当格子边界已经切进
# 本格卡面内部时补不回来 —— 少几个像素不致命，混进别的卡才致命。
#
# **三、为什么四条边各自独立。** 实测有的截图上边界正好切在本格卡面内部，
# 那种情况下"上边找不到分界"本身是对的，但不该因此放弃左右两条已经找到的边。

#: 背景判定：饱和度不超过此值 **且** 亮度落在两端（很亮或很暗）的像素算作
#: UI 背景。游戏内底色是奶白 / 浅灰，模拟基准里则是深色；两端都认，
#: 免得判据被某一种配色绑死。
#:
#: 必须写成"低饱和 **且** 两端亮度"而不是只看亮度：四属性标准色里有纯色卡框
#: （V=255，S 却很高），只看亮度会把它们当背景切掉。
BG_SAT_MAX = 40
BG_VAL_MIN = 200
BG_VAL_MAX = 40
#: 投影中判定「这一行 / 列有内容」所需的内容像素占比下限
REFINE_CONTENT_FRACTION = 0.05
#: 认定"边界"要求连续多少个内容像素 —— 单个像素的抗锯齿 / JPEG 噪点不算
REFINE_RUN = 2
#: 开头那段内容占格子超过这个比例，就认定它是卡面本身而不是邻居探进来的边
REFINE_MAX_SKIP_RATIO = 0.25
#: 每条边最多向内收格子尺寸的这个比例
#:
#: 真实卡框只比格子小 8~10%。收超过 25% 基本可以断定是在卡面内部的平坦区
#: （浅色衣料 / 天空 / 白底）上误判了 —— 实测把卡图放大 2.4 倍的失真截图会这样。
#: 这是个兜底：宁可整条边不收缩，也不要切掉小半张卡。
REFINE_MAX_SHRINK_PER_EDGE = 0.25
#: 贴合后的卡框尺寸不得小于格子的这个比例，否则视为检测失败退回原框
REFINE_MIN_RATIO = 0.45


def content_mask(rgb: np.ndarray) -> np.ndarray:
    """逐像素判断「属于卡面内容」还是「UI 背景」。

    背景 = **低饱和** 且 **亮度在两端**（很亮如奶白底，很暗如深色底）。
    中间的灰色、以及所有高饱和像素都算内容。

    :param rgb: ``(h, w, 3)`` 的 RGB 数组
    :return: ``(h, w)`` 的布尔数组，True 表示卡面内容
    """
    hi = rgb.max(axis=2).astype(np.int16)
    lo = rgb.min(axis=2).astype(np.int16)
    sat = hi - lo
    is_bg = (sat <= BG_SAT_MAX) & ((hi <= BG_VAL_MAX) | (hi >= BG_VAL_MIN))
    return ~is_bg


def _scan_edge(
    flags: np.ndarray,
    start: int,
    step: int,
    *,
    run: int = REFINE_RUN,
    max_skip_ratio: float = REFINE_MAX_SKIP_RATIO,
) -> int | None:
    """从 ``start`` 沿 ``step`` 方向定位这一侧的卡框边缘。定不出来返回 ``None``。

    分两步走：

    1. **跳过开头那段内容。** 格子大体是对准卡框的，如果边界上已经是内容，
       那只可能是相邻卡面探进来的一条边（十几像素的量级）。
        **但如果这段内容本身占了大半个格子，它只可能是本格卡面** ——
        （基准图里卡框铺满格子就是这种情形），此时不能跳过，直接判定这一侧
        "没有可用分界"。
    2. **跳过背景带，再遇到的内容就是本格卡框的边缘。**

    为什么要"从边界出发找第一段内容"，而不是"找包含中心的整段内容"：
    卡面中间可能出现大片低饱和区域（浅色衣料、天空、白底），按"整段"切会把
    它误当成卡框外的背景，一刀切掉半张卡；而从边界出发的扫描走不到卡面中间去。
    """
    n = len(flags)
    if n == 0:
        return None

    def inside(k: int) -> bool:
        return 0 <= k < n

    i = start
    skipped = 0
    while inside(i) and flags[i]:
        i += step
        skipped += 1
    if skipped and skipped >= max_skip_ratio * n:
        return None  # 这段太长，是卡面本身，不是邻居探进来的边
    if not inside(i):
        return None  # 一路都是内容，没有分界

    while inside(i) and not flags[i]:
        i += step
    if not inside(i):
        return None  # 只有背景，没找到卡面

    length = 0
    j = i
    while inside(j) and flags[j]:
        j += step
        length += 1
    if length < run:
        return None  # 太窄，多半是噪点
    return i


def refine_box(rgb: np.ndarray, box: Box) -> Box:
    """把等分格子收缩到真正的卡框上。检测不出边界时原样返回。

    **只在格子内部收缩，绝不向外扩展。** 这是刻意的取舍：向外扩展有吃到
    相邻卡面的风险（那正是本功能要消除的问题），而向内收缩只会让切出来的图
    更干净。代价是当格子边界已经切进本格卡面内部时补不回来 —— 少几个像素
    不致命，混进别的卡才致命。

    :param rgb: 整幅图的 ``(h, w, 3)`` RGB 数组
    :param box: 等分（或自动检测）得到的格子
    :return: 贴合卡框的框；任何一步不确信就返回入参 ``box``
    """
    h, w = rgb.shape[:2]
    x0, y0 = max(0, box.x), max(0, box.y)
    x1, y1 = min(w, box.x + box.w), min(h, box.y + box.h)
    if x1 - x0 < MIN_CELL or y1 - y0 < MIN_CELL:
        return box

    mask = content_mask(rgb[y0:y1, x0:x1])
    mh, mw = mask.shape
    if mh < 8 or mw < 8:
        return box

    # 投影只在格子中心的一半上统计：格子四边的窄条可能压着邻居卡面，
    # 让它们参与会把"这一列/行有没有内容"的结论带偏。
    col_hit = mask[mh // 4 : mh - mh // 4, :].mean(axis=0) >= REFINE_CONTENT_FRACTION
    row_hit = mask[:, mw // 4 : mw - mw // 4].mean(axis=1) >= REFINE_CONTENT_FRACTION

    left = _scan_edge(col_hit, 0, 1)
    right = _scan_edge(col_hit, mw - 1, -1)
    top = _scan_edge(row_hit, 0, 1)
    bottom = _scan_edge(row_hit, mh - 1, -1)

    # 兜底：任何一条边要收掉超过 25% 就直接放弃那条边（见
    # REFINE_MAX_SHRINK_PER_EDGE）。这类极端收缩几乎都是卡面内部平坦区被
    # 误判成"卡框外的背景"，照做会切掉小半张卡。
    limit_x = mw * REFINE_MAX_SHRINK_PER_EDGE
    limit_y = mh * REFINE_MAX_SHRINK_PER_EDGE
    if left is None or left > limit_x:
        left = None
    if right is None or (mw - 1 - right) > limit_x:
        right = None
    if top is None or top > limit_y:
        top = None
    if bottom is None or (mh - 1 - bottom) > limit_y:
        bottom = None
    if left is None and right is None and top is None and bottom is None:
        return box  # 四条边都没分界，原样返回

    # **四条边各自独立**：定不出来的那条保持原边界，其余照常贴合。
    # 不要求四边同时成功 —— 实测第 3~5 张截图的格子上边界正好切在本格卡面内部，
    # 那种情况下"上边没有分界"是对的，但不该因此把左右两条已经找到的边也放弃。
    nx0 = x0 + (left if left is not None else 0)
    nx1 = x0 + (right + 1 if right is not None else mw)
    ny0 = y0 + (top if top is not None else 0)
    ny1 = y0 + (bottom + 1 if bottom is not None else mh)
    if nx1 <= nx0 or ny1 <= ny0:
        return box

    nw, nh = nx1 - nx0, ny1 - ny0
    if nw < REFINE_MIN_RATIO * box.w or nh < REFINE_MIN_RATIO * box.h:
        return box
    return Box(x=nx0, y=ny0, w=nw, h=nh)


def refine_boxes(img: Image.Image, boxes: list[Box]) -> list[Box]:
    """对一组格子逐个做卡框贴合（整图只转一次 RGB，避免每个框重复转）。"""
    if not boxes:
        return []
    rgb = np.asarray(img.convert("RGB"))
    return [refine_box(rgb, b) for b in boxes]
