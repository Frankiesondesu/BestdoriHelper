/**
 * 指纹库：加载、检索。
 *
 * 与 Python 版 `vision/index.py` 的打分一致：
 *
 *     score = 0.40 * hash_sim + 0.60 * color_sim
 *
 * 哈希用 4×4 档位**全组合**取最小汉明距离；颜色用 768 维分块直方图的余弦。
 *
 * 并列时按 card_id 升序收尾 —— Bestdori 上有 111 个 resourceSetName 被多张卡
 * 共用（同一张画），特征完全相同、分数必然并列，没有这个次级键结果就不可复现。
 */

import { HIST_DIM, N_CROPS } from './features.js';

const MAGIC = 0x42444650; // 'BDFP'

/** 单字节 popcount 查表 */
const POPCOUNT = new Uint8Array(256);
for (let i = 0; i < 256; i++) POPCOUNT[i] = POPCOUNT[i >> 1] + (i & 1);

function popcount32(x) {
  return (
    POPCOUNT[x & 0xff] +
    POPCOUNT[(x >>> 8) & 0xff] +
    POPCOUNT[(x >>> 16) & 0xff] +
    POPCOUNT[(x >>> 24) & 0xff]
  );
}

/** 一条检索结果 */
export class Match {
  constructor(cardId, trained, score, hashSimilarity, colorSimilarity) {
    this.cardId = cardId;
    this.trained = trained;
    this.score = score;
    this.hashSimilarity = hashSimilarity;
    this.colorSimilarity = colorSimilarity;
    /** 几何校验后填：好匹配数 */
    this.good = 0;
    /** 几何校验后填：RANSAC 内点数 */
    this.inliers = 0;
  }
}

export class FingerprintIndex {
  constructor(fields) {
    Object.assign(this, fields);
  }

  get length() {
    return this.cardIds.length;
  }

  /**
   * 从 `fingerprints.bin` 的 ArrayBuffer 构造。
   *
   * 文件是 AoS（每条记录紧密排列），这里重排成 SoA（每个字段一个 typed array），
   * 检索时内存访问更连续。重排是一次性的，约几十毫秒。
   */
  static fromArrayBuffer(buf) {
    const dv = new DataView(buf);
    if (dv.getUint32(0, true) !== MAGIC) throw new Error('指纹库文件魔数不对');
    const version = dv.getUint32(4, true);
    if (version !== 1) throw new Error(`指纹库版本不支持：${version}`);
    const count = dv.getUint32(8, true);
    const histDim = dv.getUint32(12, true);
    const nCrops = dv.getUint32(16, true);
    if (histDim !== HIST_DIM || nCrops !== N_CROPS) {
      throw new Error(`指纹库维度不匹配：histDim=${histDim} nCrops=${nCrops}`);
    }

    const REC = 4 + 1 + nCrops * 2 * 4 * 2 + histDim + 3 * 4;
    const HEADER = 20;

    const cardIds = new Int32Array(count);
    const trained = new Uint8Array(count);
    const phash = new Uint32Array(count * nCrops * 2);
    const dhash = new Uint32Array(count * nCrops * 2);
    const hist = new Uint8Array(count * histDim);
    const avgRgb = new Float32Array(count * 3);
    const histNorm = new Float32Array(count);

    const u8 = new Uint8Array(buf);
    for (let i = 0; i < count; i++) {
      let o = HEADER + i * REC;
      cardIds[i] = dv.getInt32(o, true); o += 4;
      trained[i] = dv.getUint8(o); o += 1;
      const hp = i * nCrops * 2;
      for (let k = 0; k < nCrops * 2; k++, o += 4) phash[hp + k] = dv.getUint32(o, true);
      const hd = i * nCrops * 2;
      for (let k = 0; k < nCrops * 2; k++, o += 4) dhash[hd + k] = dv.getUint32(o, true);
      const hh = i * histDim;
      hist.set(u8.subarray(o, o + histDim), hh);
      o += histDim;
      // 预计算量化后直方图的 L2 模长，检索时不用重复算
      let sq = 0;
      for (let k = 0; k < histDim; k++) {
        const v = hist[hh + k];
        sq += v * v;
      }
      histNorm[i] = Math.sqrt(sq);
      for (let k = 0; k < 3; k++, o += 4) avgRgb[i * 3 + k] = dv.getFloat32(o, true);
    }

    return new FingerprintIndex({
      cardIds, trained, phash, dhash, hist, avgRgb, histNorm, nCrops, histDim,
    });
  }

  /**
   * 检索最相似的卡面。
   *
   * @param {import('./features.js').Features} q 查询特征
   * @param {object} [opts]
   * @param {number} [opts.topK]
   * @param {number} [opts.hashWeight] 哈希权重，默认 0.40
   * @param {Set<number>|null} [opts.restrictCardIds] 限定候选卡号
   * @param {Uint8Array|null} [opts.mask] 逐条掩码（1=参与）
   * @returns {Match[]} 按 score 降序、并列时卡号升序
   */
  search(q, opts = {}) {
    const topK = opts.topK ?? 5;
    const hw = opts.hashWeight ?? 0.4;
    const restrict = opts.restrictCardIds ?? null;
    const mask = opts.mask ?? null;
    const n = this.length;
    if (n === 0) return [];

    const { phash, dhash, hist, histNorm, cardIds, trained } = this;
    const nCrops = this.nCrops;
    const histDim = this.histDim;
    const TOTAL_BITS = 64 * 2;

    // 查询侧直方图的模长（一次）
    let qSq = 0;
    for (let k = 0; k < histDim; k++) qSq += q.hist[k] * q.hist[k];
    const qNorm = Math.sqrt(qSq);

    const scores = new Float32Array(n);
    const hsims = new Float32Array(n);
    const csims = new Float32Array(n);

    for (let i = 0; i < n; i++) {
      if (mask && !mask[i]) continue;
      if (restrict && !restrict.has(cardIds[i])) continue;

      // --- 哈希：4×4 档位全组合取最小距离 ---
      let best = TOTAL_BITS + 1;
      const bp = i * nCrops * 2;
      for (let qi = 0; qi < nCrops; qi++) {
        const qh = qi * 2;
        for (let di = 0; di < nCrops; di++) {
          const dh = di * 2;
          let d =
            popcount32(q.phash[qh] ^ phash[bp + dh]) +
            popcount32(q.phash[qh + 1] ^ phash[bp + dh + 1]) +
            popcount32(q.dhash[qh] ^ dhash[bp + dh]) +
            popcount32(q.dhash[qh + 1] ^ dhash[bp + dh + 1]);
          if (d < best) best = d;
          if (best === 0) break;
        }
        if (best === 0) break;
      }
      const hsim = 1 - best / TOTAL_BITS;

      // --- 颜色：余弦 ---
      const hb = i * histDim;
      let dot = 0;
      for (let k = 0; k < histDim; k++) dot += q.hist[k] * hist[hb + k];
      const den = qNorm * histNorm[i] + 1e-8;
      let csim = dot / den;
      if (csim > 1) csim = 1;
      else if (csim < 0) csim = 0;

      const s = hw * hsim + (1 - hw) * csim;
      scores[i] = s;
      hsims[i] = hsim;
      csims[i] = csim;
    }

    // 取 top-K：先收集索引，按 (score 降序, cardId 升序) 排序
    const order = [];
    for (let i = 0; i < n; i++) {
      if (mask && !mask[i]) continue;
      if (restrict && !restrict.has(cardIds[i])) continue;
      order.push(i);
    }
    order.sort((a, b) => {
      const d = scores[b] - scores[a];
      if (d !== 0) return d;
      return cardIds[a] - cardIds[b];
    });

    const out = [];
    const lim = Math.min(topK, order.length);
    for (let k = 0; k < lim; k++) {
      const i = order[k];
      out.push(new Match(cardIds[i], trained[i] === 1, scores[i], hsims[i], csims[i]));
    }
    return out;
  }
}
