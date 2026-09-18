/**
 * 网格切分：从整屏截图里切出卡面候选框。
 *
 * 移植自 `vision/detect.py`（Python 版），保留同样的策略与放弃条件。
 *
 * 自动推断的优先级：
 *
 * 1. **按卡面实际位置切**（背景间隙法，默认开）—— 最贴合真实界面：
 *    先在整屏 UI 里定位卡面区，再在区内按背景间隙切，框直接落在卡面上。
 *    **整屏 UI 截图必须靠它** —— 光靠等分救不回来（实测把 2400×1080 按 4×7
 *    等分得到 343×270 的框，连左侧菜单都算进去了）。
 * 2. **等分**（调用方给了 rows/cols）—— 最可控
 * 3. 都不成立 → 整张图当作单一卡面
 *
 * 每一步都有明确的放弃条件，切不出规整网格就退回上一步，不会比等分更差。
 */

// ---- 常量（与 Python 版一致）------------------------------------------

/** 单个单元格的最小边长（像素） */
export const MIN_CELL = 40;
/** 判定「空白间隙」的最小连续像素数 */
export const MIN_GUTTER = 4;

/** 「横贯卡面区的长背景带」的最短长度 */
export const AREA_MIN_SPAN = 240;
/** 长背景带位置聚类的容差（像素） */
export const AREA_POS_TOL = 16;
/** 判定一行/列是「背景」的内容像素占比上限（实测内容行 0.61 / 间隙行 0.02~0.23） */
export const GAP_CONTENT_MAX = 0.25;
/** 一组内容带的边长相差不超过这个比例，才算"同一批卡面" */
export const BAND_SIZE_TOL = 0.2;
/** 卡面区里的内容像素占比下限 */
export const AREA_MIN_CONTENT = 0.15;

/** 背景判定：饱和度 ≤ 此值 **且** 亮度落在两端，才算 UI 背景 */
export const BG_SAT_MAX = 40;
export const BG_VAL_MIN = 200;
export const BG_VAL_MAX = 40;

/** 贴合：投影中判定「有内容」的占比下限 */
export const REFINE_CONTENT_FRACTION = 0.05;
/** 认定边界要求的连续内容像素数 */
export const REFINE_RUN = 2;
/** 开头那段内容超过格子的这个比例，就认定它是卡面本身而非邻居探进来的边 */
export const REFINE_MAX_SKIP_RATIO = 0.25;
/** 每条边最多向内收格子的这个比例（兜底，防止在卡面内部平坦区误判） */
export const REFINE_MAX_SHRINK_PER_EDGE = 0.25;
/** 贴合后卡框不得小于格子的这个比例 */
export const REFINE_MIN_RATIO = 0.45;

/** 矩形框 */
export class Box {
  constructor(x, y, w, h) {
    this.x = x;
    this.y = y;
    this.w = w;
    this.h = h;
  }
}

// ---- 内容 / 背景判定 ---------------------------------------------------

/**
 * 逐像素判断「卡面内容」还是「UI 背景」。
 *
 * 背景 = **低饱和** 且 **亮度在两端**（很亮如奶白底，很暗如深色底）。
 *
 * 必须写成"低饱和 **且** 两端亮度"而不是只看亮度：四属性标准色里有纯色卡框
 * （V=255 但 S 很高），只看亮度会把它们当背景切掉。
 *
 * @param {Uint8ClampedArray|Uint8Array} rgb 行优先 RGB，3 字节/像素
 * @returns {Uint8Array} 1 = 内容，0 = 背景
 */
export function contentMask(rgb, w, h) {
  const n = w * h;
  const mask = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const o = i * 3;
    const r = rgb[o], g = rgb[o + 1], b = rgb[o + 2];
    const hi = r > g ? (r > b ? r : b) : (g > b ? g : b);
    const lo = r < g ? (r < b ? r : b) : (g < b ? g : b);
    const sat = hi - lo;
    const isBg = sat <= BG_SAT_MAX && (hi <= BG_VAL_MAX || hi >= BG_VAL_MIN);
    mask[i] = isBg ? 0 : 1;
  }
  return mask;
}

/**
 * 对矩阵的**每一行**求最长连续背景段，返回 {starts, ends, lengths}。
 *
 * 对应 Python 版的向量化实现：把每个内容像素当断点，用前缀最大值传播断点位置，
 * 当前位置减断点位置就是连续长度。这里同样一趟扫描完成。
 */
function longestBgRuns(mask, w, h) {
  const starts = new Int32Array(h);
  const ends = new Int32Array(h);
  const lengths = new Int32Array(h);
  for (let y = 0; y < h; y++) {
    const off = y * w;
    let bestLen = 0, bestStart = 0, bestEnd = 0;
    let curStart = -1;
    for (let x = 0; x < w; x++) {
      if (mask[off + x]) {
        // 内容像素：结算当前背景段
        if (curStart >= 0) {
          const len = x - curStart;
          if (len > bestLen) { bestLen = len; bestStart = curStart; bestEnd = x; }
          curStart = -1;
        }
      } else if (curStart < 0) {
        curStart = x;
      }
    }
    if (curStart >= 0) {
      const len = w - curStart;
      if (len > bestLen) { bestLen = len; bestStart = curStart; bestEnd = w; }
    }
    starts[y] = bestStart;
    ends[y] = bestEnd;
    lengths[y] = bestLen;
  }
  return { starts, ends, lengths };
}

/** 把排好序的整数切成连续段（相邻差不超 tol 归为一段） */
function groupConsecutive(values, tol = 2) {
  const out = [];
  for (const v of values) {
    if (out.length && v - out[out.length - 1][out[out.length - 1].length - 1] <= tol) {
      out[out.length - 1].push(v);
    } else {
      out.push([v]);
    }
  }
  return out;
}

/**
 * 从一维「是否有内容」投影中提取连续内容区间。
 * @param {Uint8Array} profile 1=有内容
 */
function contentBands(profile, minGutter = MIN_GUTTER, minSize = MIN_CELL) {
  const bands = [];
  let start = -1;
  let gap = 0;
  for (let i = 0; i < profile.length; i++) {
    if (profile[i]) {
      if (start < 0) start = i;
      gap = 0;
    } else if (start >= 0) {
      gap++;
      if (gap >= minGutter) {
        const end = i - gap + 1;
        if (end - start >= minSize) bands.push([start, end]);
        start = -1;
        gap = 0;
      }
    }
  }
  if (start >= 0) {
    const end = profile.length - (gap < minGutter ? gap : 0);
    if (end - start >= minSize) bands.push([start, end]);
  }
  return bands;
}

/**
 * 把长度接近的带归成一组，返回最大的那组（保持原顺序）。
 * 用途：区域内除卡面行外可能混进标题栏/按钮（高度不同），按高度分组取最大组。
 */
function largestConsistentGroup(bands, tol = BAND_SIZE_TOL) {
  if (!bands.length) return [];
  let best = [];
  for (const [a, b] of bands) {
    const size = b - a;
    if (size <= 0) continue;
    const group = bands.filter(([x, y]) => Math.abs((y - x) - size) <= tol * size);
    if (group.length > best.length) best = group;
  }
  return best;
}

// ---- 卡面区定位 --------------------------------------------------------

/**
 * 自动找出卡面网格所在的区域；找不到返回 null。
 *
 * 判据是**横贯整片卡面区的长背景带** —— 实测 4 条卡面行间隙都在 x=684~1935 上
 * 连续是背景（长 1251px），位置一致、反复出现、y 间距等距；而 UI 元素不会这样重复。
 */
export function detectCardArea(rgb, w, h, opts = {}) {
  const minSpan = opts.minSpan ?? AREA_MIN_SPAN;
  if (h < MIN_CELL * 2 || w < MIN_CELL * 2) return null;

  const mask = contentMask(rgb, w, h);
  const { starts, ends, lengths } = longestBgRuns(mask, w, h);

  const keep = [];
  for (let y = 0; y < h; y++) if (lengths[y] >= minSpan) keep.push(y);
  if (keep.length < 2) return null;

  // 以**最长**的那条带为种子聚类。卡面区的横向间隙是整幅图里最长的等距重复
  // 背景带（实测 1251px），而左侧菜单项之间的空隙只有约 350px。
  // 早先按"位置中位数"聚类会挑中菜单区 —— 菜单项之间的空隙行数更多，中位数被带偏。
  let seed = keep[0];
  for (const y of keep) if (lengths[y] > lengths[seed]) seed = y;
  const a0 = starts[seed], b0 = ends[seed];

  const core = [];
  for (const y of keep) {
    if (Math.abs(starts[y] - a0) <= AREA_POS_TOL && Math.abs(ends[y] - b0) <= AREA_POS_TOL) {
      core.push([y, starts[y], ends[y]]);
    }
  }
  if (core.length < 2) return null;

  const medOf = (idx) => {
    const v = core.map((c) => c[idx]).sort((p, q) => p - q);
    return v[v.length >> 1];
  };
  const ax = medOf(1);
  const bx = medOf(2);
  const ys = core.map((c) => c[0]).sort((p, q) => p - q);

  // 长背景带要能切成**至少三段**连续区（顶、行间、底）。只有两段说明图上
  // 只有孤零零一块内容，不是阵列；不加这条的话整片纯色会被当长带而返回整张图。
  if (groupConsecutive(ys).length < 3) return null;

  // 纵向**不外扩**：实测第一条长带 y=276、最后一条 y=961，已刚好包住卡面区
  // （真实卡面区是 292~972）。按行高外扩会把顶栏和底部按钮放进来，
  // 那些 UI 元素污染区域内的行投影，结果切出一堆竖长条。
  const y0 = Math.max(0, ys[0]);
  const y1 = Math.min(h, ys[ys.length - 1] + 1);
  // 横向略微外扩无妨（左边是菜单、右边空白，不会进来周期性结构）
  const x0 = Math.max(0, ax - AREA_POS_TOL);
  const x1 = Math.min(w, bx + AREA_POS_TOL);
  if (x1 - x0 < MIN_CELL || y1 - y0 < MIN_CELL) return null;

  // 区域内得有实在的内容
  let cnt = 0;
  for (let y = y0; y < y1; y++) {
    const off = y * w;
    for (let x = x0; x < x1; x++) cnt += mask[off + x];
  }
  if (cnt / ((y1 - y0) * (x1 - x0)) < AREA_MIN_CONTENT) return null;

  return new Box(x0, y0, x1 - x0, y1 - y0);
}

// ---- 按间隙切 ----------------------------------------------------------

/**
 * 在 area 内按背景间隙切出卡框；切不出规整网格就返回 null。
 *
 * 先按行找间隙得到「卡面行」，再在每行内按列找间隙得到「卡面列」，交叉即卡框。
 */
export function splitByGaps(rgb, w, h, area, opts = {}) {
  const minSize = opts.minSize ?? MIN_CELL;
  const x0 = Math.max(0, area.x);
  const y0 = Math.max(0, area.y);
  const x1 = Math.min(w, area.x + area.w);
  const y1 = Math.min(h, area.y + area.h);
  if (x1 - x0 < minSize || y1 - y0 < minSize) return null;

  const aw = x1 - x0, ah = y1 - y0;
  // 只在区域内做内容判定：把区域外的像素当背景，省一次大数组分配
  const full = contentMask(rgb, w, h);

  // 行投影：区域内每行的内容占比
  const rowHas = new Uint8Array(ah);
  for (let y = 0; y < ah; y++) {
    const off = (y0 + y) * w + x0;
    let c = 0;
    for (let x = 0; x < aw; x++) c += full[off + x];
    rowHas[y] = c / aw > GAP_CONTENT_MAX ? 1 : 0;
  }
  const rows = largestConsistentGroup(contentBands(rowHas, MIN_GUTTER, minSize));
  if (!rows.length) return null;

  const colsPerRow = [];
  for (const [ra, rb] of rows) {
    const colHas = new Uint8Array(aw);
    for (let x = 0; x < aw; x++) {
      let c = 0;
      for (let y = ra; y < rb; y++) c += full[(y0 + y) * w + x0 + x];
      colHas[x] = c / (rb - ra) > GAP_CONTENT_MAX ? 1 : 0;
    }
    const cols = contentBands(colHas, MIN_GUTTER, Math.max(MIN_CELL >> 1, minSize >> 1));
    if (!cols.length) return null;
    colsPerRow.push(cols);
  }

  // 列的划分取**众数**那一行的结果，行全部保留。
  // 个别行的卡面上会有一条低内容占比的竖带（浅色衣料），被当成间隙多切一刀 ——
  // 实测第 1、5 张各有一行切成 8 列而其余是 7 列。卡的列位置在所有行上一样，
  // 拿多数行的划分当准绳即可；分歧太大才放弃。
  const counts = colsPerRow.map((c) => c.length);
  const tally = new Map();
  for (const c of counts) tally.set(c, (tally.get(c) ?? 0) + 1);
  let modeCount = counts[0], modeFreq = 0;
  for (const [k, v] of tally) if (v > modeFreq) { modeFreq = v; modeCount = k; }
  if (modeFreq * 2 < rows.length) return null;

  const cols = colsPerRow.find((c) => c.length === modeCount);
  if (cols.length < 2 && rows.length < 2) return null;

  const boxes = [];
  for (const [ra, rb] of rows) {
    for (const [cx, ce] of cols) {
      boxes.push(new Box(x0 + cx, y0 + ra, ce - cx, rb - ra));
    }
  }
  return boxes;
}

// ---- 卡框贴合 ----------------------------------------------------------

/**
 * 从 start 沿 step 方向定位这一侧的卡框边缘；定不出来返回 null。
 *
 * 分两步：① 跳过开头那段内容（那可能是相邻卡面探进来的一条边）；② 跳过背景带，
 * 再遇到的内容就是本格卡框的边缘。
 *
 * 为什么"从边界出发找第一段内容"而不是"找包含中心的整段内容"：卡面中间可能
 * 出现大片低饱和区域（浅色衣料、天空、白底），按"整段"切会把它误当成卡框外的
 * 背景，一刀切掉半张卡；从边界出发的扫描走不到卡面中间去。
 */
function scanEdge(flags, start, step, run = REFINE_RUN, maxSkipRatio = REFINE_MAX_SKIP_RATIO) {
  const n = flags.length;
  if (n === 0) return null;
  const inside = (k) => k >= 0 && k < n;

  let i = start, skipped = 0;
  while (inside(i) && flags[i]) { i += step; skipped++; }
  if (skipped && skipped >= maxSkipRatio * n) return null; // 这段太长，是卡面本身
  if (!inside(i)) return null; // 一路都是内容，没有分界

  while (inside(i) && !flags[i]) i += step;
  if (!inside(i)) return null; // 只有背景，没找到卡面

  let length = 0, j = i;
  while (inside(j) && flags[j]) { j += step; length++; }
  if (length < run) return null; // 太窄，多半是噪点
  return i;
}

/**
 * 把等分格子收缩到真正的卡框上。检测不出边界时原样返回。
 *
 * **只在格子内部收缩，绝不向外扩展** —— 向外扩展有吃到相邻卡面的风险
 * （那正是本功能要消除的问题）。代价是格子边界已切进卡面内部时补不回来：
 * 少几个像素不致命，混进别的卡才致命。
 */
export function refineBox(rgb, w, h, box) {
  const x0 = Math.max(0, box.x);
  const y0 = Math.max(0, box.y);
  const x1 = Math.min(w, box.x + box.w);
  const y1 = Math.min(h, box.y + box.h);
  if (x1 - x0 < MIN_CELL || y1 - y0 < MIN_CELL) return box;

  const mw = x1 - x0, mh = y1 - y0;
  if (mh < 8 || mw < 8) return box;

  const sub = new Uint8ClampedArray(mw * mh * 3);
  for (let y = 0; y < mh; y++) {
    const src = ((y0 + y) * w + x0) * 3;
    for (let k = 0; k < mw * 3; k++) sub[y * mw * 3 + k] = rgb[src + k];
  }
  const mask = contentMask(sub, mw, mh);

  // 投影只在格子中心的一半上统计：四边的窄条可能压着邻居卡面，让它们参与会带偏结论
  const qy0 = mh >> 2, qy1 = mh - (mh >> 2);
  const qx0 = mw >> 2, qx1 = mw - (mw >> 2);
  const colHit = new Uint8Array(mw);
  for (let x = 0; x < mw; x++) {
    let c = 0;
    for (let y = qy0; y < qy1; y++) c += mask[y * mw + x];
    colHit[x] = c / (qy1 - qy0) >= REFINE_CONTENT_FRACTION ? 1 : 0;
  }
  const rowHit = new Uint8Array(mh);
  for (let y = 0; y < mh; y++) {
    let c = 0;
    for (let x = qx0; x < qx1; x++) c += mask[y * mw + x];
    rowHit[y] = c / (qx1 - qx0) >= REFINE_CONTENT_FRACTION ? 1 : 0;
  }

  let left = scanEdge(colHit, 0, 1);
  let right = scanEdge(colHit, mw - 1, -1);
  let top = scanEdge(rowHit, 0, 1);
  let bottom = scanEdge(rowHit, mh - 1, -1);

  // 兜底：任何一条边要收掉超过 25% 就放弃那条边 —— 这类极端收缩几乎都是
  // 卡面内部平坦区被误判成"卡框外的背景"，照做会切掉小半张卡
  const limitX = mw * REFINE_MAX_SHRINK_PER_EDGE;
  const limitY = mh * REFINE_MAX_SHRINK_PER_EDGE;
  if (left === null || left > limitX) left = null;
  if (right === null || (mw - 1 - right) > limitX) right = null;
  if (top === null || top > limitY) top = null;
  if (bottom === null || (mh - 1 - bottom) > limitY) bottom = null;
  if (left === null && right === null && top === null && bottom === null) return box;

  // **四条边各自独立**：定不出来的那条保持原边界，其余照常贴合。
  // 实测第 3~5 张截图的格子上边界正好切在本格卡面内部，那种情况下
  // "上边没有分界"是对的，但不该因此把左右两条已找到的边也放弃。
  const nx0 = x0 + (left !== null ? left : 0);
  const nx1 = x0 + (right !== null ? right + 1 : mw);
  const ny0 = y0 + (top !== null ? top : 0);
  const ny1 = y0 + (bottom !== null ? bottom + 1 : mh);
  if (nx1 <= nx0 || ny1 <= ny0) return box;

  const nw = nx1 - nx0, nh = ny1 - ny0;
  if (nw < REFINE_MIN_RATIO * box.w || nh < REFINE_MIN_RATIO * box.h) return box;
  return new Box(nx0, ny0, nw, nh);
}

// ---- 等分 --------------------------------------------------------------

function evenSplit(total, parts) {
  const out = [];
  for (let i = 0; i < parts; i++) {
    out.push([Math.round((total * i) / parts), Math.round((total * (i + 1)) / parts)]);
  }
  return out;
}

function boxesFromBands(rowBands, colBands, w, h, margin = 0) {
  const out = [];
  for (const [ry, rb] of rowBands) {
    for (const [cx, ce] of colBands) {
      let x = cx, y = ry, bw = ce - cx, bh = rb - ry;
      if (margin > 0) {
        const nx = Math.max(0, x - margin), ny = Math.max(0, y - margin);
        bw = Math.min(w, x + bw + margin) - nx;
        bh = Math.min(h, y + bh + margin) - ny;
        x = nx; y = ny;
      }
      out.push(new Box(x, y, bw, bh));
    }
  }
  return out;
}

// ---- 主入口 ------------------------------------------------------------

/**
 * 切分出一组卡面候选框。
 *
 * @param {Uint8ClampedArray|Uint8Array} rgb 行优先 RGB
 * @param {number} w
 * @param {number} h
 * @param {object} [opts]
 * @param {number|null} [opts.rows] 指定行数
 * @param {number|null} [opts.cols] 指定列数
 * @param {number} [opts.margin] 每框向外扩张像素
 * @param {[number,number,number,number]|null} [opts.region] 卡面区域（0~1 归一化）
 * @param {boolean} [opts.preferGaps] 是否先试「按卡面实际位置切」，默认 true
 * @returns {Box[]}
 */
export function detectBoxes(rgb, w, h, opts = {}) {
  const rows = opts.rows ?? null;
  const cols = opts.cols ?? null;
  const margin = opts.margin ?? 0;
  const region = opts.region ?? null;
  const preferGaps = opts.preferGaps !== false;

  // region 转像素
  let px = null;
  if (region) {
    let [a, b, c, d] = region;
    let x0f = Math.min(a, c), x1f = Math.max(a, c);
    let y0f = Math.min(b, d), y1f = Math.max(b, d);
    const rx0 = Math.max(0, Math.min(w, Math.round(x0f * w)));
    const rx1 = Math.max(0, Math.min(w, Math.round(x1f * w)));
    const ry0 = Math.max(0, Math.min(h, Math.round(y0f * h)));
    const ry1 = Math.max(0, Math.min(h, Math.round(y1f * h)));
    if (rx1 - rx0 >= MIN_CELL && ry1 - ry0 >= MIN_CELL) px = [rx0, ry0, rx1, ry1];
  }

  // 0) 按卡面实际位置切
  if (preferGaps) {
    const area = px
      ? new Box(px[0], px[1], px[2] - px[0], px[3] - px[1])
      : detectCardArea(rgb, w, h);
    if (area) {
      const byGap = splitByGaps(rgb, w, h, area);
      if (byGap && fitsGrid(byGap, rows, cols)) {
        return grow(byGap, w, h, margin);
      }
    }
  }

  // 限定区域时：在区域内等分，再把坐标平移回整幅图
  if (px) {
    const [x0, y0, x1, y1] = px;
    const sw = x1 - x0, sh = y1 - y0;
    const sub = new Uint8ClampedArray(sw * sh * 3);
    for (let y = 0; y < sh; y++) {
      const src = ((y0 + y) * w + x0) * 3;
      for (let k = 0; k < sw * 3; k++) sub[y * sw * 3 + k] = rgb[src + k];
    }
    const inner = detectBoxes(sub, sw, sh, { ...opts, region: null });
    return inner.map((b) => new Box(b.x + x0, b.y + y0, b.w, b.h));
  }

  // 行列都指定了 —— 最可控的路径
  if (rows > 0 && cols > 0) {
    return boxesFromBands(evenSplit(h, rows), evenSplit(w, cols), w, h, margin);
  }

  // 兜底：整张图当一个卡面
  return [new Box(0, 0, w, h)];
}

function fitsGrid(boxes, rows, cols) {
  if (boxes.length < 2) return false;
  if (rows && cols && boxes.length !== rows * cols) return false;
  return true;
}

function grow(boxes, w, h, margin) {
  if (margin <= 0) return boxes;
  return boxes.map((b) => {
    const x = Math.max(0, b.x - margin);
    const y = Math.max(0, b.y - margin);
    return new Box(x, y, Math.min(w, b.x + b.w + margin) - x, Math.min(h, b.y + b.h + margin) - y);
  });
}
