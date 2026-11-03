/**
 * 道具图标模板匹配（灰度归一化互相关，NCC）。
 *
 * 为什么不靠 OCR 认名字：商店截图里四列名字挨得近，Tesseract 会把
 * 「Roselia的宣传单」读成「的宣传单」（乐队名直接丢了）。图标是**固定素材**，
 * 而且和 Bestdori 的 areaitem 缩略图是同一张画。
 *
 * 做法：
 *   1. 模板（74 张，180×180 透明底）白底合成 → 灰度 → 缩到 32×32 → 去均值归一化
 *   2. 截图整体缩到「图标约 32px」的尺度（2640 宽 → 约 455）
 *   3. 以「等级」文字为锚点，在它上方 ±4px 里滑窗，算 NCC，取最高
 *   4. 最高分不够高、或与第二名太接近 → 返回 null（宁可少认也不错认）
 *
 * 实测：4 张卡（椰子树/足浴池/铜像/喷泉）全部命中，分差 0.06~0.19。
 */

const N = 32;          // 模板边长（像素）
const ICON_RATIO = 185 / 2640;  // 图标边长 / 截图宽度（实测 2640 宽时 185px）
// 注意：缩放比要按**传入画布的实际宽度**算 —— 识别前会把图放大 2 倍，
// 写死一个常数会让 2x 画布上的图标被缩过头（实测 0 命中）。

let TEMPLATES = null;
let loading = null;

/** 图片 → N×N 灰度、去均值、单位化 */
function toVec(img) {
  const c = document.createElement('canvas');
  c.width = N; c.height = N;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.fillStyle = '#fff';
  g.fillRect(0, 0, N, N);
  g.drawImage(img, 0, 0, N, N);
  const d = g.getImageData(0, 0, N, N).data;
  const v = new Float32Array(N * N);
  let sum = 0;
  let cr = 0; let cg = 0; let cb = 0;
  for (let i = 0, j = 0; i < d.length; i += 4) {
    const y = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    v[j++] = y; sum += y;
    cr += d[i]; cg += d[i + 1]; cb += d[i + 2];
  }
  const n = d.length / 4;
  const mean = sum / v.length;
  let norm = 0;
  for (let i = 0; i < v.length; i++) { v[i] -= mean; norm += v[i] * v[i]; }
  norm = Math.sqrt(norm) || 1;
  for (let i = 0; i < v.length; i++) v[i] /= norm;
  v.meanRgb = [cr / n, cg / n, cb / n];
  return v;
}

/** 载入 74 个图标模板（只做一次） */
export function loadItemTemplates() {
  if (TEMPLATES) return Promise.resolve(TEMPLATES);
  if (loading) return loading;
  loading = (async () => {
    const meta = await fetch('data/items.json').then((r) => r.json());
    const out = [];
    await Promise.all(meta.items.map(async (it) => {
      const img = new Image();
      img.src = `data/item-icons/${String(it.id).padStart(5, '0')}.png`;
      try { await img.decode(); } catch { return; }
      out.push({ id: it.id, name: it.n, slot: it.s, attr: it.a || '', v: toVec(img) });
    }));
    TEMPLATES = out;
    return out;
  })();
  return loading;
}

/** 整张图 → 灰度 Float32Array（缩放后），只算一次 */
export function prepareGray(canvas) {
  const scale = N / (canvas.width * ICON_RATIO);
  const w = Math.round(canvas.width * scale);
  const h = Math.round(canvas.height * scale);
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.imageSmoothingQuality = 'high';
  g.drawImage(canvas, 0, 0, w, h);
  const d = g.getImageData(0, 0, w, h).data;
  const gray = new Float32Array(w * h);
  const rgb = new Uint8ClampedArray(w * h * 3);
  for (let i = 0, j = 0; i < d.length; i += 4, j++) {
    gray[j] = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    rgb[j * 3] = d[i]; rgb[j * 3 + 1] = d[i + 1]; rgb[j * 3 + 2] = d[i + 2];
  }
  return { gray, rgb, w, h, scale };
}

/**
 * 在锚点上方找图标。
 * @param {{gray:Float32Array,w:number,h:number}} G prepareGray 的结果
 * @param {{x:number,y:number}} anchor 原图坐标（1x）
 * @param {Array} tpl loadItemTemplates() 的结果
 */
export function matchIconAt(G, anchor, tpl, debug = false) {
  const { gray, rgb, w, h, scale } = G;
  const ax = anchor.x * scale;
  const ay = anchor.y * scale;
  const iconPx = N;                       // 缩放后图标正好 N 像素
  const baseX = Math.round(ax - 8 * scale);
  const baseY = Math.round(ay - (iconPx + 12 * scale));
  if (baseX < 0 || baseY < 0 || baseX + N > w || baseY + N > h) {
    return debug ? { stage: 'bounds', baseX, baseY, w, h } : null;
  }

  // 每个模板在所有滑窗位置里取最高分 —— 第二名必须是**另一个模板**，
  // 否则同一个图标在隔壁位置的得分会把分差压成 0.002（实测过）。
  const bestByTpl = new Map();
  const R = 5; // 搜索半径（缩放后像素）
  for (let dy = -R; dy <= R; dy++) {
    for (let dx = -R; dx <= R; dx++) {
      const x0 = baseX + dx;
      const y0 = baseY + dy;
      if (x0 < 0 || y0 < 0 || x0 + N > w || y0 + N > h) continue;
      // 取 32×32 灰度块，去均值 + 归一化
      const p = new Float32Array(N * N);
      let sum = 0;
      for (let yy = 0; yy < N; yy++) {
        const off = (y0 + yy) * w + x0;
        for (let xx = 0; xx < N; xx++) { const v = gray[off + xx]; p[yy * N + xx] = v; sum += v; }
      }
      const mean = sum / p.length;
      let norm = 0;
      for (let i = 0; i < p.length; i++) { p[i] -= mean; norm += p[i] * p[i]; }
      norm = Math.sqrt(norm) || 1;
      for (let i = 0; i < p.length; i++) p[i] /= norm;

      // 这块的 RGB 均值（同一位置只算一次）
      let pr = 0; let pg = 0; let pb = 0;
      for (let yy = 0; yy < N; yy++) {
        const off = (y0 + yy) * w + x0;
        for (let xx = 0; xx < N; xx++) {
          const o = (off + xx) * 3;
          pr += rgb[o]; pg += rgb[o + 1]; pb += rgb[o + 2];
        }
      }
      const pn = N * N;
      const pMean = [pr / pn, pg / pn, pb / pn];

      for (const t of tpl) {
        let dot = 0;
        const tv = t.v;
        for (let i = 0; i < p.length; i++) dot += p[i] * tv[i];
        const prev = bestByTpl.get(t);
        if (!prev || dot > prev.score) {
          const c = t.v.meanRgb || [0, 0, 0];
          bestByTpl.set(t, {
            score: dot,
            color: Math.hypot(pMean[0] - c[0], pMean[1] - c[1], pMean[2] - c[2]),
          });
        }
      }
    }
  }
  if (!bestByTpl.size) return null;
  const ranked = [...bestByTpl.entries()].sort((a, b) => b[1].score - a[1].score);
  const [bestT, bestV] = ranked[0];
  const secondV = ranked[1] ? ranked[1][1] : null;
  if (debug) {
    return {
      name: bestT.name,
      best: +bestV.score.toFixed(3),
      second: secondV ? +secondV.score.toFixed(3) : null,
      color: Math.round(bestV.color),
      color2: secondV ? Math.round(secondV.color) : null,
    };
  }
  if (bestV.score < 0.6) return null;
  // 同一种乐器（5 把贝斯）在灰度上几乎一样 —— 分差小的时候用颜色裁决，
  // 颜色也更接近才算数，否则宁可放弃。
  const gap = bestV.score - (secondV?.score ?? -2);
  const colorWin = secondV && bestV.color * 2.2 < secondV.color;
  if (gap < 0.08 && !colorWin) return null;
  return { slot: bestT.slot, id: bestT.id, name: bestT.name, score: bestV.score };
}
