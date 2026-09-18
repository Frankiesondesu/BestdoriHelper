/**
 * Bestdori 账号接口客户端 —— 移植自 `bridge/bestdori_api.py`。
 *
 * ## 为什么需要它 / 为什么只能在原生壳里跑
 *
 * 桌面版能直接调 Bestdori 的内部接口，是因为 Python 没有同源策略。
 * 浏览器有 —— 实测（真实 Chromium 从 localhost 发起）Bestdori 的接口
 * **连公开的 `/api/cards/all.5.json` 都返回 `TypeError: Failed to fetch`**，
 * 说明它完全没设 CORS 头。
 *
 * 所以在网页里这段代码只用于**离线编解码**（导入计划、导出）；
 * 真正发请求必须走原生壳：Capacitor 的 `CapacitorHttp` 会把
 * `window.fetch` / `XMLHttpRequest` 换成原生实现（OkHttp），
 * 原生层没有同源策略，请求就通了。本模块只用 `fetch`，
 * 因此**同一份代码在浏览器和原生壳里都能跑**，无需条件分支。
 *
 * ## 编解码必须与 Bestdori 前端互逆
 *
 * 云端档案的存储格式是从 Bestdori 前端 bundle 逆向的：
 * 卡号存成 base64(Uint16 小端)，其余字段走游程编码。
 * 这里逐行照抄 Python 实现，并用离线对照测试保证两边输出完全一致。
 */

/** 卡号超过此值时解码要 +65536（前端 importProfile 的兼容分支，照抄保持互逆） */
export const ID_BIG = 24464;
/** 支持的云存储压缩版本 */
export const SUPPORTED_COMPRESSION = '2';

export const API_BASE = 'https://bestdori.com/api';
export const LOGIN_URL = `${API_BASE}/user/login`;
export const ME_URL = `${API_BASE}/user/me`;
export const PROFILES_URL = `${API_BASE}/user/profiles`;

// ---------------------------------------------------------------------
// base64 <-> 字节
// ---------------------------------------------------------------------

function bytesToBase64(bytes) {
  let s = '';
  const CHUNK = 0x8000; // 一次传太多字符会爆栈
  for (let i = 0; i < bytes.length; i += CHUNK) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(s);
}

function base64ToBytes(b64) {
  const s = atob(b64);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

// ---------------------------------------------------------------------
// 编解码（与前端 exportProfile / importProfile 互逆）
// ---------------------------------------------------------------------

/** 游程解码：`[次数, 值, 次数, 值, …]` 展开成值序列（前端 `w`） */
export function rleDecode(arr) {
  const out = [];
  if (!arr || !arr.length) return out;
  for (let i = 0; i + 1 < arr.length; i += 2) {
    const count = Number(arr[i]);
    const value = arr[i + 1];
    for (let k = 0; k < count; k++) out.push(value);
  }
  return out;
}

/** 游程编码：连续相同值压成 `[次数, 值]` 对再展平（前端 `S`） */
export function rleEncode(values) {
  const pairs = [];
  for (const v of values) {
    const last = pairs[pairs.length - 1];
    if (last && last[1] === v) last[0] += 1;
    else pairs.push([1, v]);
  }
  const out = [];
  for (const [n, v] of pairs) { out.push(n); out.push(v); }
  return out;
}

/** 卡号列表 -> base64(Uint16 小端)（前端 exportProfile 的逆） */
export function encodeIds(ids) {
  const buf = new Uint8Array(ids.length * 2);
  const dv = new DataView(buf.buffer);
  for (let i = 0; i < ids.length; i++) dv.setUint16(i * 2, ids[i], true);
  return bytesToBase64(buf);
}

/** base64(Uint16 小端) -> 卡号列表（前端 `x` 里 ids 的逆） */
export function decodeIds(b64) {
  const bytes = base64ToBytes(b64);
  const n = Math.floor(bytes.length / 2);
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = [];
  for (let i = 0; i < n; i++) {
    const v = dv.getUint16(i * 2, true);
    out.push(v > ID_BIG ? v + 65536 : v);
  }
  return out;
}

/**
 * 云存储条目 -> `Map<cardId, {id, level, master, skill, ep, train, art, exclude}>`
 * 与前端 `x` 一致：ids 解码后与其余字段按下标对齐。
 */
export function decodeCards(entry) {
  if (entry.compression !== SUPPORTED_COMPRESSION) {
    throw new Error(`不支持的档案版本 compression=${entry.compression}（当前支持 ${SUPPORTED_COMPRESSION}）`);
  }
  const cards = entry.data.cards;
  const ids = decodeIds(cards.ids);
  const levels = rleDecode(cards.levels || []);
  const masters = rleDecode(cards.masters || []);
  const skills = rleDecode(cards.skills || []);
  const eps = rleDecode(cards.eps || []);
  const trains = rleDecode(cards.trains || []);
  const arts = rleDecode(cards.arts || []);
  const excludes = rleDecode(cards.excludes || []);

  const pick = (arr, i, dflt) => (i < arr.length ? arr[i] : dflt);

  const out = new Map();
  ids.forEach((cid, i) => {
    out.set(cid, {
      id: cid,
      level: pick(levels, i, 1),
      master: pick(masters, i, 0),
      skill: pick(skills, i, 0),
      ep: pick(eps, i, 0),
      train: pick(trains, i, 0),
      art: pick(arts, i, 0),
      exclude: Boolean(pick(excludes, i, false)),
    });
  });
  return out;
}

/**
 * 卡牌列表 -> 云存储的 `data.cards`（exportProfile 的逆）。
 * @param {Map<number, object>} entries
 * @param {number[]|null} idsOrder 输出顺序；不传就按卡号排序（稳定、可复现）
 */
export function encodeCards(entries, idsOrder = null) {
  const order = (idsOrder ? [...idsOrder] : [...entries.keys()].sort((a, b) => a - b))
    .filter((i) => entries.has(i));
  const col = (key) => order.map((i) => entries.get(i)[key]);
  return {
    ids: encodeIds(order),
    levels: rleEncode(col('level')),
    masters: rleEncode(col('master')),
    skills: rleEncode(col('skill')),
    eps: rleEncode(col('ep')),
    trains: rleEncode(col('train')),
    arts: rleEncode(col('art')),
    excludes: rleEncode(order.map((i) => (entries.get(i).exclude ? 1 : 0))),
  };
}

/**
 * 构造一张新卡的条目 —— 默认值与 Bestdori 网页上手动添加一致。
 *
 * 前端 `onStartAction`（mode=1）添加时：
 * `level = levelLimit + (training ? training.levelLimit : 0)`、
 * `train/art = training ? 1 : 0`、`ep = 剧情数`、其余 0。
 *
 * **`train/art` 要以「这张卡有没有特训形态」为准，不是看 `trained` 参数。**
 * 1★/2★ 卡没有特训形态（实测 681 张），对它们设 train=1 会写出非法数据。
 * 注意也不能用 `tl > 0` 来判断 —— 实测有 153 张 4★ 卡的
 * `training.levelLimit` 就是 0，但它们**是**可特训的。所以 cards.json 里
 * 单独导出了一个 `tr` 标志。
 *
 * `cardMeta` 为 null（卡池快照缺失）时无从判断，此时按调用方的 trained
 * 语义设置 —— 宁可多给一个特训标记（网页上可改），也不把用户确认过的
 * 「特训后」悄悄降成「未特训」。
 *
 * @param {object|null} cardMeta cards.json 里的条目（含 ll / tl / tr / ep）
 */
export function newCardEntry(cardId, cardMeta, trained) {
  if (!cardMeta) {
    return {
      id: cardId, level: 1, master: 0, skill: 0, ep: 0,
      train: trained ? 1 : 0, art: trained ? 1 : 0, exclude: false,
    };
  }
  const levelLimit = Number(cardMeta.ll) || 1;
  const trainBonus = Number(cardMeta.tl) || 0;
  const eps = Number(cardMeta.ep) || 0;
  const trainable = Number(cardMeta.tr) === 1;
  const useTrain = Boolean(trained) && trainable;
  return {
    id: cardId,
    level: useTrain ? levelLimit + trainBonus : levelLimit,
    master: 0,
    skill: 0,
    ep: eps,
    train: useTrain ? 1 : 0,
    art: useTrain ? 1 : 0,
    exclude: false,
  };
}

// ---------------------------------------------------------------------
// 登录会话
// ---------------------------------------------------------------------

/**
 * 从**原生 cookie jar** 读某个 URL 的 cookie；不可用返回 null。
 *
 * 为什么要这条兜底：原生壳里 `CapacitorHttp` 接管 fetch 之后，
 * 响应头里的 `set-cookie` 不保证透出来（各家平台实现不一致）。
 * `CapacitorCookies` 是 Capacitor 内置的插件，能直接读原生 jar，
 * 拿到的还是一次性全量 cookie，比逐个解析 Set-Cookie 更可靠。
 * 纯浏览器里这个插件不存在，函数返回 null，走响应头解析那条路。
 */
async function nativeCookies(url) {
  try {
    const p = globalThis.Capacitor?.Plugins?.CapacitorCookies;
    if (!p || typeof p.getCookies !== 'function') return null;
    const res = await p.getCookies({ url });
    const jar = res && typeof res === 'object' && 'value' in res ? res.value : res;
    if (!jar || typeof jar !== 'object' || Array.isArray(jar)) return null;
    const parts = Object.entries(jar)
      .filter(([k, v]) => k && v !== undefined && v !== null)
      .map(([k, v]) => `${k}=${v}`);
    return parts.length ? parts.join('; ') : null;
  } catch {
    return null;
  }
}

/** 把 `a=1; b=2` 形式的 cookie 串合并进现有串（同名覆盖） */
function mergeCookie(current, incoming) {
  const map = new Map();
  const add = (s) => {
    for (const part of String(s).split(';')) {
      const t = part.trim();
      if (!t || !t.includes('=')) continue;
      map.set(t.split('=')[0].trim(), t);
    }
  };
  add(current);
  add(incoming);
  return [...map.values()].join('; ');
}

/**
 * 把会话 cookie **也写进原生 CookieManager**。
 *
 * 为什么非此不可：原生壳里的请求由 CapacitorHttp（OkHttp/WebView）发出，
 * 它用的是**原生 cookie jar**。只存 localStorage 的话，原生 jar 是空的，
 * 进程重启后原生请求就是匿名的 —— 表现就是"每次打开 App 都要重新登录"。
 * 原生 CookieManager 自己会持久化，这才是"记住登录"的那一份。
 */
async function persistCookieToNative(url, cookieStr) {
  try {
    const p = globalThis.Capacitor?.Plugins?.CapacitorCookies;
    if (!p || typeof p.setCookie !== 'function') return false;
    for (const part of String(cookieStr).split(';')) {
      const t = part.trim();
      const i = t.indexOf('=');
      if (i <= 0) continue;
      await p.setCookie({ url, key: t.slice(0, i).trim(), value: t.slice(i + 1).trim() });
    }
    return true;
  } catch {
    return false;   // 网页版没有这个插件，忽略
  }
}

/**
 * 已登录的 Bestdori 会话（cookie 持久化）。
 *
 * 会话里是 Bestdori 下发的 cookie，等价于浏览器登录态 —— **不要外传**。
 * 存哪儿由调用方决定：网页版/APP 用 localStorage，桌面版是 session.json。
 */
export class BestdoriAccount {
  /**
   * @param {object} [opts]
   * @param {string} [opts.baseUrl]
   * @param {{get: (k: string) => string|null, set: (k: string, v: string) => void,
   *          remove?: (k: string) => void}} [opts.store] 持久化后端
   */
  constructor(opts = {}) {
    this.baseUrl = opts.baseUrl || 'https://bestdori.com';
    this.store = opts.store || null;
    this.cookie = this.store?.get('bdh.session.cookie') || '';
    this.user = null;
  }

  /** 会话里有没有 cookie（不代表一定还有效，有效性要 me() 才知道） */
  get hasSession() {
    return Boolean(this.cookie);
  }

  saveSession() {
    if (this.store) this.store.set('bdh.session.cookie', this.cookie);
    // 同时写进原生 cookie jar（让它自己持久化、原生请求自动携带）。
    // 不 await：这是锦上添花，不该拖慢登录流程。
    if (this.cookie) persistCookieToNative(this.baseUrl, this.cookie);
  }

  clearSession() {
    this.cookie = '';
    this.user = null;
    if (this.store) {
      if (this.store.remove) this.store.remove('bdh.session.cookie');
      else this.store.set('bdh.session.cookie', '');
    }
  }

  /**
   * 发一个请求。原生壳里 `fetch` 已被 CapacitorHttp 接管，所以这里
   * 不需要区分平台。
   *
   * cookie 手动管理：原生 HTTP 下 `Cookie` 不是禁头，可以自由设置；
   * 响应里的 `set-cookie` 也读得到。这样不依赖 WebView 的 cookie jar。
   */
  async request(method, url, { body = null, headers = {} } = {}) {
    const h = { 'User-Agent': 'BestdoriHelper/1.0', ...headers };
    if (body !== null) h['Content-Type'] = 'application/json';
    if (this.cookie) h['Cookie'] = this.cookie;

    const res = await fetch(url, {
      method,
      headers: h,
      body: body === null ? undefined : JSON.stringify(body),
      credentials: 'omit',   // 不用浏览器的 cookie jar，自己管
    });

    // 取回服务端新下发的会话 cookie（login 的 Set-Cookie）。
    // 优先问原生 cookie jar —— 原生 HTTP 下响应头的 set-cookie 不保证透出来。
    const fromNative = await nativeCookies(url);
    if (fromNative) {
      this.cookie = mergeCookie(this.cookie, fromNative);
      this.saveSession();
    } else {
      const sc = res.headers.get('set-cookie');
      if (sc) {
        const pair = sc.split(';')[0].trim();
        if (pair && !/=\s*$/.test(pair)) {
          this.cookie = mergeCookie(this.cookie, pair);
          this.saveSession();
        }
      }
    }
    return res;
  }

  /**
   * 登录态探针：已登录返回用户数据，未登录返回 null。
   *
   * 判定**不能只认 `result`** —— 那是未登录响应里才有的字段
   * （`{result: false, code: "LOGIN_REQUIRED"}`），登录后大概率直接返回
   * 用户对象。所以以「是不是未登录」为准。
   */
  async me() {
    try {
      const r = await this.request('GET', `${this.baseUrl}/api/user/me`);
      const data = await r.json();
      if (!data || typeof data !== 'object' || !Object.keys(data).length) return null;
      if (data.code === 'LOGIN_REQUIRED' || data.result === false) return null;
      this.user = data;
      return data;
    } catch {
      return null;
    }
  }

  /** 账号密码登录；成功后 cookie 存盘 */
  async login(username, password) {
    const r = await this.request('POST', `${this.baseUrl}/api/user/login`, {
      body: { username, password },
    });
    const data = await r.json();
    if (!data || !data.result) {
      throw new Error(`登录失败（${data?.code ?? 'UNKNOWN'}）。请检查用户名/密码。`);
    }
    this.saveSession();
    return data;
  }

  /** 拉取云端全部档案（原始条目，未解码） */
  async fetchProfiles() {
    const r = await this.request('GET', `${this.baseUrl}/api/user/profiles`);
    const data = await r.json();
    if (!data || !data.result) throw new Error(`读取档案失败（${data?.code ?? 'UNKNOWN'}）`);
    if (!Array.isArray(data.profiles)) throw new Error('读取档案失败：响应里没有 profiles 数组');
    return data.profiles;
  }

  /** 写回全部档案 */
  async storeProfiles(profiles) {
    const r = await this.request('POST', `${this.baseUrl}/api/user/profiles`, {
      body: { profiles },
    });
    const data = await r.json();
    if (!data || !data.result) throw new Error(`写入档案失败（${data?.code ?? 'UNKNOWN'}）`);
    return data;
  }
}

// ---------------------------------------------------------------------
// 后台导入
// ---------------------------------------------------------------------

/** 一次导入的统计 */
export class ImportStats {
  constructor() {
    this.profilesTotal = 0;
    this.profilesTouched = 0;
    this.added = 0;
    this.upgraded = 0;
    this.already = 0;
  }

  summary() {
    return `档案 ${this.profilesTotal} 份（改动 ${this.profilesTouched} 份）：` +
      `新增 ${this.added}，升级为特训后 ${this.upgraded}，已存在 ${this.already}`;
  }
}

/**
 * 把清单增量合并进云端档案。
 *
 * 读 → 合并 → **全量写回**（Bestdori 的写接口没有逐卡端点）。
 * 合并规则：
 *
 * - 远端没有这张卡 → **新增**（默认值与网页手动添加一致）
 * - 远端有、但远端未特训而本地是特训后 → **升级**（train/art 置 1，
 *   等级按特训后上限拉满）；远端已特训而本地未特训则不动（本地数据更弱，不覆盖）
 * - 两者一致 → 跳过
 *
 * @param {BestdoriAccount} account
 * @param {Array<{cardId:number, trained:boolean}>} cards 本地清单
 * @param {object} cardMetaMap cards.json 的 cards 字段（cardId -> 元数据）
 * @param {object} [opts]
 * @param {number} [opts.profileIndex]
 * @param {boolean} [opts.apply] false 时只算不写（试运行）
 */
export async function importInventory(account, cards, cardMetaMap, opts = {}) {
  const profileIndex = opts.profileIndex ?? 0;
  const apply = opts.apply !== false;

  const stats = new ImportStats();
  const profiles = await account.fetchProfiles();
  stats.profilesTotal = profiles.length;
  if (!profiles.length) {
    throw new Error('云端没有档案。请先到 Bestdori 的 Profile Manager 创建一个。');
  }
  if (profileIndex < 0 || profileIndex >= profiles.length) {
    throw new Error(`档案序号 ${profileIndex} 不存在（共 ${profiles.length} 份）`);
  }

  const entry = profiles[profileIndex];
  const cardsMap = decodeCards(entry);

  for (const c of cards) {
    const cid = Number(c.cardId);
    if (!Number.isFinite(cid)) continue;
    const meta = cardMetaMap?.[String(cid)] ?? null;
    const existing = cardsMap.get(cid);
    if (!existing) {
      cardsMap.set(cid, newCardEntry(cid, meta, c.trained));
      stats.added += 1;
    } else if (c.trained && !existing.train) {
      // 远端只有未特训，本地确认了特训后 -> 升级
      const up = newCardEntry(cid, meta, true);
      existing.train = up.train;
      existing.art = up.art;
      existing.level = up.level;
      stats.upgraded += 1;
    } else {
      stats.already += 1;
    }
  }

  entry.data.cards = encodeCards(cardsMap);
  stats.profilesTouched = 1;

  if (apply) await account.storeProfiles(profiles);
  return stats;
}

/**
 * 只读地算一遍「要导入哪些」—— 不开浏览器、不写任何东西。
 * 用于「生成导入计划」按钮。
 */
export async function buildImportPlan(account, cards, cardMetaMap, opts = {}) {
  const profileIndex = opts.profileIndex ?? 0;
  const profiles = await account.fetchProfiles();
  if (!profiles.length) {
    throw new Error('云端没有档案。请先到 Bestdori 的 Profile Manager 创建一个。');
  }
  if (profileIndex < 0 || profileIndex >= profiles.length) {
    throw new Error(`档案序号 ${profileIndex} 不存在（共 ${profiles.length} 份）`);
  }
  const remote = decodeCards(profiles[profileIndex]);
  const plan = { toAdd: [], toUpgrade: [], already: [] };
  for (const c of cards) {
    const cid = Number(c.cardId);
    const existing = remote.get(cid);
    if (!existing) plan.toAdd.push({ cardId: cid, trained: Boolean(c.trained) });
    else if (c.trained && !existing.train) plan.toUpgrade.push({ cardId: cid });
    else plan.already.push({ cardId: cid });
  }
  return { plan, profilesTotal: profiles.length, profileIndex };
}
