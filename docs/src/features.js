/**
 * 图像特征提取：pHash / dHash / 4×4 分块 HSV 直方图。
 *
 * 与 Python 版（`vision/features.py`）**算法对齐**，但不追求逐位一致 ——
 * 关键在于：指纹库和查询端用的是**同一份本文件**（构建时用 Node 跑它生成
 * 指纹库，运行时浏览器用同一份算查询特征），所以天然自洽。
 *
 * 为什么不用「和 PIL 的 LANCZOS 逐位对齐」：PIL 的重采样实现细节（边界处理、
 * 归一化方式）难以在 JS 里完全复刻，而 pHash 是低频特征，几个 bit 的差异
 * 会直接污染检索结果。改用「同一份代码算两端」把这个问题整个绕开。
 *
 * 四个中心裁切档位与 Python 版一致：
 *   crop 0: 全幅 (0,0,1,1)   crop 1: 中心 90% (0.05)
 *   crop 2: 中心 75% (0.125) crop 3: 中心 60% (0.20)
 */

/** 中心裁切档位 (left, top, right, bottom)，0~1 比例 */
export const CROP_LEVELS = [
  [0.0, 0.0, 1.0, 1.0],
  [0.05, 0.05, 0.95, 0.95],
  [0.125, 0.125, 0.875, 0.875],
  [0.2, 0.2, 0.8, 0.8],
];

/** pHash 内部工作尺寸（32×32 后取左上 8×8 低频） */
export const PHASH_WORK = 32;
/** 哈希边长（8 => 64 bit） */
export const HASH_SIZE = 8;
/** 每通道直方图 bin 数 */
export const HIST_BINS = 16;
/** 颜色直方图的空间网格边长（4 => 4×4 = 16 块，合计 768 维） */
export const HIST_GRID = 4;
/** 直方图用的中间图尺寸（与 Python 版一致） */
export const HIST_W = 160;
export const HIST_H = 120;

/** 特征维度常量，供指纹库读写用 */
export const HIST_DIM = HIST_GRID * HIST_GRID * 3 * HIST_BINS; // 768
export const N_CROPS = CROP_LEVELS.length;

// ---------------------------------------------------------------------
// 重采样（Lanczos3，可分离卷积）
// ---------------------------------------------------------------------

const LANCZOS_A = 3;

function lanczosKernel(x) {
  if (x === 0) return 1;
  const ax = x < 0 ? -x : x;
  if (ax >= LANCZOS_A) return 0;
  const pix = Math.PI * x;
  return (LANCZOS_A * Math.sin(pix) * Math.sin(pix / LANCZOS_A)) / (pix * pix);
}

/**
 * 预计算一维重采样权重表。
 * 返回 [[[srcIndex, weight], ...], ...]，长度 = dstSize。
 */
function buildWeights(srcSize, dstSize) {
  const scale = dstSize / srcSize;
  // 下采样时要把核按比例展宽，否则会 aliasing
  const filterScale = scale < 1 ? 1 / scale : 1;
  const support = LANCZOS_A * filterScale;
  const table = [];
  for (let i = 0; i < dstSize; i++) {
    const center = (i + 0.5) / scale - 0.5;
    const left = Math.max(0, Math.ceil(center - support));
    const right = Math.min(srcSize - 1, Math.floor(center + support));
    const row = [];
    let sum = 0;
    for (let j = left; j <= right; j++) {
      const w = lanczosKernel((j - center) / filterScale);
      if (w === 0) continue;
      row.push([j, w]);
      sum += w;
    }
    if (sum === 0) {
      // 退化情况：核完全落在边界外，取最近像素
      const j = Math.min(srcSize - 1, Math.max(0, Math.round(center)));
      row.length = 0;
      row.push([j, 1]);
    } else {
      for (const p of row) p[1] /= sum;
    }
    table.push(row);
  }
  return table;
}

/**
 * 可分离 Lanczos3 重采样。
 *
 * @param {Uint8Array|Float32Array} src 源数据，行优先、单通道
 * @param {number} sw 源宽
 * @param {number} sh 源高
 * @param {number} dw 目标宽
 * @param {number} dh 目标高
 * @returns {Float32Array} 目标数据
 */
export function resizeGray(src, sw, sh, dw, dh) {
  const wx = buildWeights(sw, dw);
  const wy = buildWeights(sh, dh);
  // 先水平
  const tmp = new Float32Array(dw * sh);
  for (let y = 0; y < sh; y++) {
    const srcOff = y * sw;
    const dstOff = y * dw;
    for (let x = 0; x < dw; x++) {
      const row = wx[x];
      let acc = 0;
      for (let k = 0; k < row.length; k++) acc += src[srcOff + row[k][0]] * row[k][1];
      tmp[dstOff + x] = acc;
    }
  }
  // 再垂直
  const out = new Float32Array(dw * dh);
  for (let y = 0; y < dh; y++) {
    const row = wy[y];
    const dstOff = y * dw;
    for (let x = 0; x < dw; x++) {
      let acc = 0;
      for (let k = 0; k < row.length; k++) acc += tmp[row[k][0] * dw + x] * row[k][1];
      out[dstOff + x] = acc;
    }
  }
  return out;
}

// ---------------------------------------------------------------------
// 色彩空间
// ---------------------------------------------------------------------

/**
 * RGBA → 灰度（ITU-R 601-2，与 PIL 的 "L" 模式一致）。
 * @param {Uint8ClampedArray|Uint8Array} rgba
 * @param {number} n 像素数
 * @returns {Uint8Array}
 */
export function toGray(rgba, n) {
  const g = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const o = i * 4;
    g[i] = (rgba[o] * 299 + rgba[o + 1] * 587 + rgba[o + 2] * 114 + 500) / 1000;
  }
  return g;
}

/**
 * RGBA → HSV（H/S/V 各 0~255，与 PIL 的 "HSV" 模式量化一致）。
 * 逐像素写入调用方给的三个数组。
 */
export function toHsv(rgba, n, hOut, sOut, vOut) {
  for (let i = 0; i < n; i++) {
    const o = i * 4;
    const r = rgba[o], g = rgba[o + 1], b = rgba[o + 2];
    const maxc = r > g ? (r > b ? r : b) : (g > b ? g : b);
    const minc = r < g ? (r < b ? r : b) : (g < b ? g : b);
    const delta = maxc - minc;
    vOut[i] = maxc;
    if (delta === 0) {
      hOut[i] = 0;
      sOut[i] = 0;
      continue;
    }
    let h;
    if (maxc === r) h = (60 * (((g - b) / delta) % 6) + 360) % 360;
    else if (maxc === g) h = (60 * ((b - r) / delta)) + 120;
    else h = (60 * ((r - g) / delta)) + 240;
    hOut[i] = Math.round((h * 255) / 360) % 256;
    sOut[i] = Math.round((delta * 255) / maxc);
  }
}

// ---------------------------------------------------------------------
// 哈希
// ---------------------------------------------------------------------

/** DCT-II 矩阵 D，使 X = D · x · Dᵀ。n 只有 32，缓存住。 */
const _dctCache = new Map();
function dctMatrix(n) {
  let d = _dctCache.get(n);
  if (d) return d;
  d = new Float64Array(n * n);
  for (let k = 0; k < n; k++) {
    for (let i = 0; i < n; i++) {
      d[k * n + i] = Math.cos((Math.PI * (2 * i + 1) * k) / (2 * n));
    }
  }
  _dctCache.set(n, d);
  return d;
}

/**
 * pHash：灰度 → 32×32 → DCT-II → 左上 8×8 → 与中位数比较得 64 bit。
 *
 * @param {Uint8Array|Float32Array} gray 单通道灰度（行优先）
 * @param {number} w
 * @param {number} h
 * @returns {[number, number]} [高 32 位, 低 32 位]
 */
export function phashBits(gray, w, h) {
  const work = PHASH_WORK;
  const small = resizeGray(gray, w, h, work, work);
  const d = dctMatrix(work);

  // 二维 DCT：tmp = D · a，再 low = tmp · Dᵀ，只取左上 8×8
  const hs = HASH_SIZE;
  const low = new Float64Array(hs * hs);
  // 先算 D · a 的前 hs 行（只需要前 8 行）
  const rows = new Float64Array(hs * work);
  for (let k = 0; k < hs; k++) {
    const dOff = k * work;
    const rOff = k * work;
    for (let j = 0; j < work; j++) {
      let acc = 0;
      for (let i = 0; i < work; i++) acc += d[dOff + i] * small[i * work + j];
      rows[rOff + j] = acc;
    }
  }
  // 再乘 Dᵀ 的前 hs 列
  for (let k = 0; k < hs; k++) {
    for (let l = 0; l < hs; l++) {
      let acc = 0;
      for (let j = 0; j < work; j++) acc += rows[k * work + j] * d[l * work + j];
      low[k * hs + l] = acc;
    }
  }

  // 中位数（排除 DC 分量，避免整体亮度主导）
  const rest = Array.from(low.slice(1)).sort((a, b) => a - b);
  const mid = rest.length >> 1;
  const med = rest.length % 2 ? rest[mid] : (rest[mid - 1] + rest[mid]) / 2;

  let hi = 0, lo = 0;
  for (let i = 0; i < hs * hs; i++) {
    const bit = low[i] > med ? 1 : 0;
    if (i < 32) hi = ((hi << 1) | bit) >>> 0;
    else lo = ((lo << 1) | bit) >>> 0;
  }
  return [hi, lo];
}

/**
 * dHash：灰度 → 9×8 → 水平相邻比较得 64 bit。
 * @returns {[number, number]} [高 32 位, 低 32 位]
 */
export function dhashBits(gray, w, h) {
  const hs = HASH_SIZE;
  const small = resizeGray(gray, w, h, hs + 1, hs);
  let hi = 0, lo = 0;
  for (let y = 0; y < hs; y++) {
    for (let x = 0; x < hs; x++) {
      const bit = small[y * (hs + 1) + x + 1] > small[y * (hs + 1) + x] ? 1 : 0;
      const idx = y * hs + x;
      if (idx < 32) hi = ((hi << 1) | bit) >>> 0;
      else lo = ((lo << 1) | bit) >>> 0;
    }
  }
  return [hi, lo];
}

// ---------------------------------------------------------------------
// 颜色直方图
// ---------------------------------------------------------------------

/** 单块 HSV 三通道各 16 bin，L1 归一化，写入 out[off..off+47] */
function blockHistogram(rgba, w, h, x0, y0, x1, y1, out, off) {
  const n = (x1 - x0) * (y1 - y0);
  if (n <= 0) return;
  const hist = new Float32Array(3 * HIST_BINS);
  const bw = HIST_BINS;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const o = (y * w + x) * 4;
      const r = rgba[o], g = rgba[o + 1], b = rgba[o + 2];
      const maxc = r > g ? (r > b ? r : b) : (g > b ? g : b);
      const minc = r < g ? (r < b ? r : b) : (g < b ? g : b);
      const delta = maxc - minc;
      let hq = 0, sq = 0;
      if (delta !== 0) {
        let hh;
        if (maxc === r) hh = (60 * (((g - b) / delta) % 6) + 360) % 360;
        else if (maxc === g) hh = 60 * ((b - r) / delta) + 120;
        else hh = 60 * ((r - g) / delta) + 240;
        hq = Math.round((hh * 255) / 360) % 256;
        sq = Math.round((delta * 255) / maxc);
      }
      // bin 索引：与 numpy.histogram(bins=0,16,...,256) 一致 —— 左闭右开，
      // 最后一档含右端点
      hist[Math.min(bw - 1, hq >> 4)] += 1;
      hist[bw + Math.min(bw - 1, sq >> 4)] += 1;
      hist[2 * bw + Math.min(bw - 1, maxc >> 4)] += 1;
    }
  }
  // L1 归一化
  let total = 0;
  for (let i = 0; i < hist.length; i++) total += hist[i];
  const scale = total > 0 ? 1 / total : 0;
  for (let i = 0; i < hist.length; i++) out[off + i] = hist[i] * scale;
}

/**
 * 4×4 网格分块 HSV 直方图。
 *
 * 每块**单独** L1 归一化（小块不会被面积压掉权重），拼接后整体 L2 归一化。
 *
 * @returns {Float32Array} 长度 HIST_DIM
 */
export function spatialHistogram(rgba, w, h, grid = HIST_GRID) {
  const out = new Float32Array(grid * grid * 3 * HIST_BINS);
  let off = 0;
  for (let gy = 0; gy < grid; gy++) {
    for (let gx = 0; gx < grid; gx++) {
      const x0 = Math.floor((w * gx) / grid);
      const y0 = Math.floor((h * gy) / grid);
      const x1 = Math.floor((w * (gx + 1)) / grid);
      const y1 = Math.floor((h * (gy + 1)) / grid);
      blockHistogram(rgba, w, h, x0, y0, x1, y1, out, off);
      off += 3 * HIST_BINS;
    }
  }
  let norm = 0;
  for (let i = 0; i < out.length; i++) norm += out[i] * out[i];
  norm = Math.sqrt(norm);
  if (norm > 0) for (let i = 0; i < out.length; i++) out[i] /= norm;
  return out;
}

// ---------------------------------------------------------------------
// 裁切与总入口
// ---------------------------------------------------------------------

/**
 * 从 RGBA 里裁出一个矩形，返回新的 RGBA 缓冲。
 * 边界按 Python 版 `_center_crop` 的规则夹取（至少 8px 宽高）。
 */
export function cropRgba(rgba, w, h, box) {
  let x0 = Math.floor(w * box[0]);
  let y0 = Math.floor(h * box[1]);
  let x1 = Math.max(x0 + 8, Math.floor(w * box[2]));
  let y1 = Math.max(y0 + 8, Math.floor(h * box[3]));
  x1 = Math.min(x1, w);
  y1 = Math.min(y1, h);
  x0 = Math.max(0, Math.min(x0, x1 - 1));
  y0 = Math.max(0, Math.min(y0, y1 - 1));
  const cw = x1 - x0;
  const ch = y1 - y0;
  const out = new Uint8ClampedArray(cw * ch * 4);
  for (let y = 0; y < ch; y++) {
    const srcOff = ((y + y0) * w + x0) * 4;
    out.set(rgba.subarray(srcOff, srcOff + cw * 4), y * cw * 4);
  }
  return { data: out, width: cw, height: ch };
}

/** 特征容器。哈希用 [hi, lo] 两个 uint32 存，汉明距离走查表。 */
export class Features {
  constructor() {
    this.phash = new Uint32Array(N_CROPS * 2);
    this.dhash = new Uint32Array(N_CROPS * 2);
    this.hist = new Float32Array(HIST_DIM);
    this.avgRgb = new Float32Array(3);
  }
}

/**
 * 提取一张图的完整特征。
 *
 * @param {Uint8ClampedArray|Uint8Array} rgba 行优先 RGBA
 * @param {number} w
 * @param {number} h
 * @returns {Features}
 */
export function computeFeatures(rgba, w, h) {
  const f = new Features();

  for (let c = 0; c < N_CROPS; c++) {
    const crop = cropRgba(rgba, w, h, CROP_LEVELS[c]);
    const gray = toGray(crop.data, crop.width * crop.height);
    const [ph, pl] = phashBits(gray, crop.width, crop.height);
    const [dh, dl] = dhashBits(gray, crop.width, crop.height);
    f.phash[c * 2] = ph;
    f.phash[c * 2 + 1] = pl;
    f.dhash[c * 2] = dh;
    f.dhash[c * 2 + 1] = dl;
  }

  // 颜色特征用第 3 档裁切（中心 75%），缩到 160×120
  const mid = cropRgba(rgba, w, h, CROP_LEVELS[2]);
  const mw = mid.width, mh = mid.height;
  const sw = HIST_W, sh = HIST_H;
  const nMid = mw * mh;

  // 三通道各自重采样，拼回 RGBA（alpha 补 255）
  const chans = new Uint8ClampedArray(sw * sh * 4);
  const one = new Uint8Array(nMid);
  for (let ch = 0; ch < 3; ch++) {
    for (let i = 0; i < nMid; i++) one[i] = mid.data[i * 4 + ch];
    const r = resizeGray(one, mw, mh, sw, sh);
    for (let i = 0, n = sw * sh; i < n; i++) {
      chans[i * 4 + ch] = Math.max(0, Math.min(255, Math.round(r[i])));
    }
  }
  for (let i = 0, n = sw * sh; i < n; i++) chans[i * 4 + 3] = 255;

  f.hist = spatialHistogram(chans, sw, sh);

  let sr = 0, sg = 0, sb = 0;
  const n = sw * sh;
  for (let i = 0; i < n; i++) {
    sr += chans[i * 4];
    sg += chans[i * 4 + 1];
    sb += chans[i * 4 + 2];
  }
  f.avgRgb[0] = sr / n / 255;
  f.avgRgb[1] = sg / n / 255;
  f.avgRgb[2] = sb / n / 255;
  return f;
}
