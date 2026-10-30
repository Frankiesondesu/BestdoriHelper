/**
 * 把 Bestdori 组队工具的计算模块（webpack module `c0f0`）从线上 JS 里挖出来，
 * 在 Node 里直接跑 —— 这样「Bestdori 怎么算的」不是猜的，是**它自己的代码**。
 *
 * ## 为什么可行
 *
 * Bestdori 没开源，但前端是普通 webpack 打包，模块就是
 * `c0f0:function(t,e,n){...}` 这种形式。`c0f0` 依赖的 11 个模块（2fdb / 456d /
 * 55dd / 6762 / 6b54 / 7514 / a481 / ac6a / aef6 / f559 / f576）**全是 core-js
 * 的 polyfill**（String.includes / padStart / Array.sort / Object.keys …），
 * 在 Node 里本来就是原生实现，所以可以把 `n()` 换成空壳，模块照样跑得起来。
 *
 * ## 导出的函数（按 webpack 的导出名）
 *
 * | 导出名 | 作用 |
 * |--------|------|
 * | `d`  | 单卡综合力：`d(card, ep, train, level, master, [charRank×3], [bonus×3])` |
 * | `f`  | 组队计算（含区域道具、活动加成、技能顺序） |
 * | `e`  | 由技能推分数（`lt` 的封装） |
 * | `g` / `h` | 技能 tag 工具 |
 *
 * ## 缓存
 *
 * 下载下来的 JS 存在 `.bdh-test/bestdori/`（gitignored）。文件带 hash，所以按
 * 页面里发现的文件名缓存；重新抓用 `--refresh`。
 *
 * 用法：
 *   node scripts/bestdori_ref.mjs            # 自检：打印导出的函数名
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const CACHE = join(ROOT, '.bdh-test', 'bestdori');
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)';

const REFRESH = process.argv.includes('--refresh');

async function get(url, binary = false) {
  const r = await fetch(url, { headers: { 'User-Agent': UA } });
  if (!r.ok) throw new Error(`${url} -> HTTP ${r.status}`);
  return binary ? Buffer.from(await r.arrayBuffer()) : r.text();
}

/** 从组队工具页面找出当前 app.js 的文件名 */
async function findAppJs() {
  const html = await get('https://bestdori.com/tool/teambuilder');
  const m = html.match(/\/js\/(app\.[0-9a-f]+\.js)/);
  if (!m) throw new Error('页面里找不到 app.<hash>.js');
  return m[1];
}

async function loadAppJs(force = false) {
  mkdirSync(CACHE, { recursive: true });
  let name;
  try {
    name = await findAppJs();
  } catch (err) {
    // 离线时退回缓存里最新的一份
    const cached = existsSync(CACHE)
      ? readdir(CACHE).filter((f) => f.startsWith('app.')).sort().pop()
      : null;
    if (!cached) throw err;
    name = cached;
  }
  const p = join(CACHE, name);
  if (force || REFRESH || !existsSync(p)) {
    writeFileSync(p, await get(`https://bestdori.com/js/${name}`), 'utf-8');
  }
  return { name, src: readFileSync(p, 'utf-8') };
}

function readdir(dir) {
  // 避免额外 import：同步列目录
  const fs = require('node:fs');
  return fs.readdirSync(dir);
}
// eslint-disable-next-line no-undef
const require = (await import('node:module')).createRequire(import.meta.url);

/**
 * 从打包文件里按「键名 + 花括号配对」切出一个 webpack 模块。
 * 跳过字符串 / 模板串 / 正则，避免花括号被字面量里的 `}` 打断。
 */
function sliceModule(src, id) {
  const key = `${id}:function(`;
  const at = src.indexOf(key);
  if (at < 0) throw new Error(`找不到模块 ${id}`);
  let i = src.indexOf('{', at);
  let depth = 0;
  let quote = null;
  for (let k = i; k < src.length; k++) {
    const c = src[k];
    if (quote) {
      if (c === '\\') { k++; continue; }
      if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'" || c === '`') { quote = c; continue; }
    if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) return src.slice(at + key.length - 'function('.length, k + 1);
    }
  }
  throw new Error(`模块 ${id} 花括号不配对`);
}

/** 极简 webpack require 垫片：polyfill 模块直接返回空对象 */
function makeRequire() {
  const cache = new Map();
  const stub = (id) => {
    if (!cache.has(id)) cache.set(id, {});
    return cache.get(id);
  };
  return stub;
}

/**
 * 内部函数名（webpack 模块里没导出，但对拍要直接调）。
 *
 * `d` / `f` / `e` 是导出的三个入口，但它们内部又调 `at`/`it`/`rt`（道具）、
 * `st`/`mt`（技能倍率）、`ut`（combo 倍率）、`lt`（技能顺序 → 分数）、
 * `ot`（技能 tag → 倍率）、`i`（道具 id → 名称表）。对拍时这些也得能用，
 * 所以在模块函数体末尾插一句 `Object.assign(e, {__internal: {...}})`。
 */
const INTERNAL = ['at', 'it', 'rt', 'st', 'mt', 'ut', 'lt', 'ot', 'tt', 'i', 'j'];

/** 在模块函数体的最后一个 `}` 前插入一行，把内部符号挂到 exports 上 */
function injectInternals(code) {
  const close = code.lastIndexOf('}');
  if (close < 0) throw new Error('模块源码结尾找不到 }');
  const line = `Object.assign(e,{__internal:{${INTERNAL.join(',')}}});`;
  return code.slice(0, close) + line + code.slice(close);
}

export async function loadBestdoriCalc({ debug = false, refresh = false } = {}) {
  const { name, src } = await loadAppJs(refresh);
  let code = sliceModule(src, 'c0f0');
  if (debug) code = injectInternals(code);
  // eslint-disable-next-line no-new-func
  const modFn = new Function(`return (${code})`)();
  const exportsObj = {};
  const n = makeRequire();
  n.d = (target, key, getter) => {
    if (!(key in target)) Object.defineProperty(target, key, { get: getter, enumerable: true });
  };
  n.r = (target) => {
    if (!target.__esModule) Object.defineProperty(target, '__esModule', { value: true });
  };
  n.o = (o, p) => Object.prototype.hasOwnProperty.call(o, p);
  n.n = (m) => () => m && m.__esModule ? m.default : m;
  modFn({}, exportsObj, n);
  return { exports: exportsObj, appJs: name };
}

// 自检
if (process.argv[1] && process.argv[1].endsWith('bestdori_ref.mjs')) {
  const { exports, appJs } = await loadBestdoriCalc();
  const keys = Object.keys(exports).sort();
  console.log(`app.js = ${appJs}`);
  console.log(`导出 ${keys.length} 个: ${keys.join(',')}`);
  for (const k of ['d', 'f', 'e', 'g', 'h', 'eb']) {
    console.log(`  ${k}: ${typeof exports[k]}`);
  }
}
