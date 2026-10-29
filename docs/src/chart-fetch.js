/**
 * 谱面获取 —— **不预存**，用户选歌后才去取。
 *
 * ## 为什么要分三级
 *
 * Bestdori 没有 CORS 头，浏览器直连 `fetch` 会被拦。
 *
 * 1. **本地缓存**（localStorage）：同一首歌算第二次不再请求
 * 2. **仓库内文件**：如果仓库里曾经采过这首（`data/charts/{diff}/{id}.json`），
 *    直接用 —— 零请求、零延迟。**这一级可以没有**，删掉目录也照常工作
 * 3. **在线代理**：真人第一次选这首时，经 CORS 代理现拉
 *
 * 代理是公开服务，有速率限制，所以**必须缓存**，不能每次计算都打过去。
 */

/** 候选 CORS 代理，按顺序尝试 */
const PROXIES = [
  (url) => `https://corsproxy.io/?${encodeURIComponent(url)}`,
  (url) => `https://api.allorigins.win/raw?url=${encodeURIComponent(url)}`,
  (url) => `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(url)}`,
];

const MEM = new Map();               // 进程内缓存
const LS_PREFIX = 'bdh.chart.v1.';
const LS_LIMIT = 40;                 // localStorage 最多留 40 首，避免占满配额

function lsKey(diff, sid) { return `${LS_PREFIX}${diff}.${sid}`; }

function readCache(diff, sid) {
  const key = lsKey(diff, sid);
  if (MEM.has(key)) return MEM.get(key);
  try {
    const raw = localStorage.getItem(key);
    if (raw) {
      const v = JSON.parse(raw);
      MEM.set(key, v);
      return v;
    }
  } catch { /* 配额或坏数据 */ }
  return null;
}

function writeCache(diff, sid, data) {
  const key = lsKey(diff, sid);
  MEM.set(key, data);
  try {
    localStorage.setItem(key, JSON.stringify(data));
    // 粗粒度淘汰：超过上限时清掉最早的一批键
    const keys = Object.keys(localStorage).filter((k) => k.startsWith(LS_PREFIX));
    if (keys.length > LS_LIMIT) {
      for (const k of keys.slice(0, keys.length - LS_LIMIT)) localStorage.removeItem(k);
    }
  } catch { /* 写不进去就算了，内存里还有 */ }
}

/** 是不是我们精简过的谱面格式 */
function isCompact(v) {
  return v && Array.isArray(v.n) && v.n.length > 0;
}

/**
 * 取一首歌的谱面。返回 `{d, n}`（精简格式）或抛出错误。
 *
 * @param {string} sid  歌曲 ID
 * @param {string} diff easy|normal|hard|expert|special
 * @param {{allowProxy?:boolean}} [opt] allowProxy=false 时只查缓存和仓库
 */
export async function fetchChart(sid, diff, opt = {}) {
  const allowProxy = opt.allowProxy !== false;

  const cached = readCache(diff, sid);
  if (isCompact(cached)) return cached;

  // ① 仓库里已采的（可选，没有就跳过）
  try {
    const r = await fetch(`data/charts/${diff}/${sid}.json`);
    if (r.ok) {
      const data = await r.json();
      if (isCompact(data)) { writeCache(diff, sid, data); return data; }
    }
  } catch { /* 没有这个文件是正常的 */ }

  if (!allowProxy) throw new Error('谱面不在本地');

  // ② 经代理现拉 Bestdori
  const src = `https://bestdori.com/api/charts/${sid}/${diff}.json`;
  let lastErr = null;
  for (const make of PROXIES) {
    try {
      const r = await fetch(make(src), { cache: 'force-cache' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const raw = await r.json();
      const compact = compactChart(raw);
      if (!isCompact(compact)) throw new Error('谱面格式不对');
      writeCache(diff, sid, compact);
      return compact;
    } catch (e) {
      lastErr = e;
    }
  }
  throw new Error(`谱面获取失败（本地没有，代理也不通：${lastErr?.message || '未知'}）`);
}

/**
 * Bestdori 原始谱面 -> 精简 `{d, n}`。
 *
 * ⚠️ `Long`（长条）**没有 beat 字段**，时间在 `connections[0].beat` 里。
 * 漏掉它会让 NOTE 数少 15% 左右，每 NOTE 分虚高、总分系统性偏高。
 */
export function compactChart(chart) {
  const bpm = (chart || [])
    .filter((e) => e.type === 'BPM' && typeof e.bpm === 'number')
    .sort((a, b) => a.beat - b.beat);
  if (!bpm.length) bpm.push({ beat: 0, bpm: 120 });

  const at = (beat) => {
    let t = 0;
    for (let i = 0; i < bpm.length; i++) {
      const cur = bpm[i];
      const next = bpm[i + 1];
      const start = cur.beat;
      const end = next ? next.beat : beat;
      if (beat <= start) return t;
      const span = Math.min(beat, end) - start;
      if (span > 0) t += (span * 60) / cur.bpm;
      if (beat <= end) return t;
    }
    return t;
  };

  const startBeat = (e) => {
    if (e.type === 'Long') {
      const c = e.connections || [];
      return c.length ? c[0].beat : null;
    }
    return e.beat;
  };

  const notes = [];
  for (const e of chart || []) {
    if (!['Single', 'Long', 'Flick'].includes(e.type)) continue;
    const b = startBeat(e);
    if (typeof b === 'number') notes.push(at(b));
  }
  notes.sort((a, b) => a - b);
  return { d: notes.length ? +notes[notes.length - 1].toFixed(3) : 0,
           n: notes.map((x) => +x.toFixed(3)) };
}

/** 清掉某首歌的缓存（调试用） */
export function clearChartCache() {
  MEM.clear();
  for (const k of Object.keys(localStorage)) {
    if (k.startsWith(LS_PREFIX)) localStorage.removeItem(k);
  }
}
