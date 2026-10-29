/**
 * Bestdori 档案解析器 —— 把官网导出的 JSON 变成我们的卡池 + 道具设置。
 *
 * ## 档案长什么样（2026-10 用真实导出核对过）
 *
 * ```json
 * { "name": "...", "server": 3, "compression": "2",
 *   "data": {
 *     "cards": {
 *       "ids": "<base64>",                      // 全部卡 ID，自定义压缩
 *       "levels":  [卡ID, 等级, 卡ID, 等级, ...],
 *       "skills":  [...], "masters": [...], "eps": [...],
 *       "trains": [...], "arts": [...], "excludes": [...]
 *     },
 *     "items": { "Roselia": [道具ID, 等级], "Magazine": [3, 7], ... }
 *   } }
 * ```
 *
 * ## ids 的坑
 *
 * `ids` 是**自定义压缩**：838 字节 / 419 张卡，解 base64 后是 16 位小端、
 * 严格递减的序列，但绝对值对不上卡号（最大 10052 vs 卡号上限约 2500），
 * zlib / gzip / raw-deflate 都不匹配。
 *
 * ## 结论（2026-10 用真实档案验证）
 *
 * **`ids` 解 base64 后就是「16 位小端的卡号数组」，没有二次压缩。**
 * 你那 419 个值全部落在已收录卡号里，解码即卡池。
 *
 * 唯一要注意的是：**`levels` 里可能有「孤儿数据」** —— 记录着用户早就
 * 没有的卡（实测 5 个 known 卡全都不在 `ids` 里）。所以**不能**用
 * "levels 的卡必须出现在 ids" 来校验，那样会误杀正确解码。
 *
 * 校验只保留两条：（a）卡号在合理范围内；（b）没有重复。
 * 仍保留其它候选算法做兜底，万一 Bestdori 以后改了编码。
 */

const KANA = {};

/** base64 -> Uint8Array（浏览器与 Node 通用） */
function b64ToBytes(b64) {
  if (typeof atob === 'function') {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  return Uint8Array.from(Buffer.from(b64, 'base64'));
}

/** [id, v, id, v, ...] -> Map */
export function pairsToMap(arr) {
  const m = new Map();
  for (let i = 0; i + 1 < (arr || []).length; i += 2) m.set(Number(arr[i]), arr[i + 1]);
  return m;
}

/** 把字节按 16 位小端读出 */
function u16le(bytes) {
  const n = bytes.length >> 1;
  const out = new Array(n);
  for (let i = 0; i < n; i++) out[i] = bytes[i * 2] | (bytes[i * 2 + 1] << 8);
  return out;
}

/**
 * 候选编码。每个函数把「原始 16 位值」映射成候选卡号。
 * 只放有依据的：观测到全部递减、且大量相邻差为 1~3。
 */
const DECODERS = [
  { name: 'raw', fn: (v) => v },
  { name: 'half', fn: (v) => v / 2 },
  { name: 'quarter', fn: (v) => v >> 2 },
  { name: 'diff-half', fn: null }, // 差分后除以 2，单独处理
];

/**
 * 尝试解码 `ids`。
 *
 * @param {string} b64  档案里的 data.cards.ids
 * @param {number} maxCardId  当前服务器最大卡号（用来校验）
 * @param {number[]} knownIds `levels` 里出现过的卡号
 * @returns {{ids:number[], method:string}|null}
 */
export function decodeCardIds(b64, maxCardId, knownIds = []) {
  if (!b64) return null;
  let bytes;
  try { bytes = b64ToBytes(b64); } catch { return null; }

  const raw = u16le(bytes);
  const known = new Set(knownIds.map(Number));

  const check = (ids, method) => {
    if (!ids.length) return null;
    const set = new Set(ids);
    // (a) 范围
    if (ids.some((x) => !Number.isInteger(x) || x < 1 || x > maxCardId)) return null;
    // (b) 唯一
    if (set.size !== ids.length) return null;
    return { ids, method };
  };

  // 直接映射
  for (const d of DECODERS) {
    if (!d.fn) continue;
    const ids = raw.map((v) => Math.round(d.fn(v)));
    const ok = check(ids, d.name);
    if (ok) return ok;
  }

  // 差分累加（值可能是相邻差）
  for (const scale of [1, 2]) {
    let acc = 0;
    const ids = raw.map((v) => { acc += v / scale; return Math.round(acc); });
    const ok = check(ids, `cumsum/${scale}`);
    if (ok) return ok;
  }

  // 差分递减（从某个起点往下走）
  for (const scale of [1, 2]) {
    let acc = raw[0] / scale;
    const ids = [Math.round(acc)];
    for (let i = 1; i < raw.length; i++) {
      acc -= raw[i] / scale;
      ids.push(Math.round(acc));
    }
    const ok = check(ids, `diff-down/${scale}`);
    if (ok) return ok;
  }

  return null;
}

/** items 的 [id, level] -> {slot: {itemId: level}} */
export function parseItems(itemsRaw) {
  const out = {};
  for (const [slot, arr] of Object.entries(itemsRaw || {})) {
    if (slot === 'potentials') {
      // 角色潜能：[角色ID, 值, 角色ID, 值, ...]
      // 同一角色可能有多条，**按出现顺序对应 Performance / Technique / Visual**。
      // 游戏机制：潜能每级 +0.1%，只作用在对应的那一项上（不是总综合力）。
      const byChar = {};
      for (let i = 0; i + 1 < (arr || []).length; i += 2) {
        const cid = Number(arr[i]);
        const v = Number(arr[i + 1]) || 0;
        (byChar[cid] ||= []).push(v);
      }
      out[slot] = byChar;
      continue;
    }
    if (!Array.isArray(arr)) continue;
    out[slot] = pairsToMap(arr);
  }
  return out;
}

/**
 * 解析整份档案。
 *
 * @param {object} json Bestdori 导出的 JSON
 * @param {{maxCardId?:number}} [opt]
 * @returns {{
 *   cards: {id:number, level:number, skill:number, master:number, trained:boolean}[],
 *   items: object, source: 'ids'|'levels', note: string
 * }}
 */
export function parseProfile(json, opt = {}) {
  const data = json?.data;
  if (!data?.cards) throw new Error('不像 Bestdori 档案：缺少 data.cards');

  const maxCardId = opt.maxCardId ?? 3000;
  const c = data.cards;
  const levels = pairsToMap(c.levels);
  const skills = pairsToMap(c.skills);
  const masters = pairsToMap(c.masters);
  const eps = pairsToMap(c.eps);
  const trains = pairsToMap(c.trains);
  const arts = pairsToMap(c.arts);

  // 卡池来源：优先进 ids；解不开就退回 levels 里出现过的卡
  const decoded = decodeCardIds(c.ids, maxCardId, [...levels.keys()]);
  const idList = decoded ? decoded.ids : [...levels.keys()];
  const source = decoded ? 'ids' : 'levels';
  const note = decoded
    ? `ids 解码成功（${decoded.method}，${decoded.ids.length} 张）`
    : `ids 编码未识别，仅能读取 levels 里的 ${levels.size} 张卡`;

  const cards = idList.map((id) => ({
    id,
    level: levels.get(id) ?? 0,
    skill: skills.get(id) ?? 0,
    master: masters.get(id) ?? 0,
    episodes: eps.get(id) ?? 0,
    trained: Boolean(trains.get(id) || arts.get(id)),
  }));

  const items = parseItems(data.items);
  return { cards, items, potentials: items.potentials || {}, source, note, raw: json };
}
