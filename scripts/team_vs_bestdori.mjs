/**
 * 拿 Bestdori **自己的计算代码**跑一遍，和本站的 `docs/src/team.js` 逐项对数字。
 *
 * ## 为什么要这么干
 *
 * 之前靠「读源码猜公式」，猜错过五次（加成取 max、双匹配只读一个字段、连乘、
 * 点数取平均、技能次数）。读源码只能知道「它大概这么写」，**跑一遍才知道
 * 我们算出来是不是同一个数**。所以这个脚本不重写公式，而是：
 *
 *  1. 把 Bestdori 线上 `app.js` 里的计算模块（webpack `c0f0`）切出来直接调；
 *  2. 用**真实卡数据**喂进去，逐卡 / 逐队比对；
 *  3. 差异按「归因」分类打印，一眼看出是哪一层写错了。
 *
 * ## Bestdori 那三个函数的真实签名（从 `et` 函数体反推，不是文档）
 *
 * ```js
 * // 单卡综合力
 * d(card, epCount, trained, level, master, [charRank×3], [bonus×3])
 *   card      原始卡对象（要有 stat / levelLimit / rarity）
 *   epCount   算几段剧情（0 / 1 / 2）
 *   trained   是否特训（特训的固定加成是**额外**加的，见下）
 *   level     等级；≠满级时按稀有度插值表插值
 *   master    星光等级 0~5
 *   charRank  角色评级，逐围 `m += floor(m*t)`
 *   bonus     道具+活动加成，逐围 `m += m*t`（**不取整**）
 *
 * // 组队（22 个位置参数，只列用得到的）
 * et(chars, bands, itemTable, skills, members, skillLv,
 *    itemLevels, attrIdx, bandIdx, magIdx, channel, attrBonus,
 *    charBonus, memberCards, doublePct, starTable, cpStat, ...)
 *
 * // 由技能推分数
 * ct(skills, skillLv, skillIds, skillLvs, notes, a, perfectRate, fever, base)
 * ```
 *
 * ## 跑法
 *
 * ```bash
 * node scripts/team_vs_bestdori.mjs             # 抽样 400 张卡
 * node scripts/team_vs_bestdori.mjs --all       # 全部 2490 张
 * node scripts/team_vs_bestdori.mjs --refresh   # 重新抓 Bestdori 的 JS
 * ```
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

import { loadBestdoriCalc } from './bestdori_ref.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const J = (p) => JSON.parse(readFileSync(join(ROOT, p), 'utf8'));

const ALL = process.argv.includes('--all');
const SAMPLE = ALL ? Infinity : 400;

// ---------------------------------------------------------------------------
// 数据
// ---------------------------------------------------------------------------

/** 本站精简卡（sx 满级三围 / ep 剧情合计 / r 星级 / a 属性 / ch 角色） */
const TEAM = J('docs/data/team.json');
/** Bestdori 原始卡（stat[等级] / stat.training / stat.episodes / levelLimit） */
const RAW = J('.bdh-test/bdapi/cards.json');
const CHARS = J('.bdh-test/bdapi/characters.json');
/**
 * ⚠️ 必须用 `bands/main`（8 个主乐队），**不能**用 `bands/all`（113 个，含子团）。
 * `et` 里 `N = parseInt(Object.keys(bands)[bandIdx])` 拿乐队 id，
 * 而道具表的行顺序是 `[1,2,3,4,5,18,21,45]` —— 只有 8 个主乐队才对齐。
 */
const BANDS = J('.bdh-test/bdapi/bandsMain.json');
const ITEMS = J('.bdh-test/bdapi/areaItems.json');
const SKILLS = J('.bdh-test/bdapi/skills.json');

const { exports: BD } = await loadBestdoriCalc({
  debug: true,
  refresh: process.argv.includes('--refresh'),
});

const { d: bdCardPower, f: bdTeam } = BD;

/** 本站算法 */
const {
  totalPower, eventBonusFor, starStat,
} = await import(pathToFileURL(join(ROOT, 'docs/src/team.js')).href);

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

const ATTRS = ['powerful', 'cool', 'happy', 'pure'];

/** Bestdori 的「满级」= 特训前上限 + 特训加的上限 */
function bdMaxLevel(card) {
  return card.levelLimit + (card.stat.training ? card.stat.training.levelLimit : 0);
}

/** Bestdori 的剧情段数 */
function bdEpCount(card) {
  return (card.stat.episodes || []).length;
}

/** 把本站卡 + 星光等级 拼成 totalPower 能吃的形状 */
function myCard(id, master = 0) {
  const c = TEAM.cards[id];
  return { ...c, id: Number(id), master };
}

const fmt = (n) => (Math.round(n * 1000) / 1000).toLocaleString('en-US');
const pct = (a, b) => (b === 0 ? 0 : ((a - b) / b) * 100);

/** 差异直方图：key 是差值，value 是出现次数 */
function histogram(diffs) {
  const m = new Map();
  for (const v of diffs) m.set(v, (m.get(v) || 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
}

function report(title, rows) {
  console.log(`\n${title}`);
  const diffs = rows.map((r) => r.diff);
  const bad = rows.filter((r) => (r.ok === undefined ? r.diff !== 0 : !r.ok));
  const maxAbs = diffs.reduce((a, v) => Math.max(a, Math.abs(v)), 0);
  console.log(`  样本 ${rows.length}   不一致 ${bad.length}   最大差 ${maxAbs}`);
  if (bad.length) {
    for (const [v, n] of histogram(diffs)) {
      if (v === 0) continue;
      console.log(`    差值 ${v > 0 ? '+' : ''}${v}  × ${n}`);
    }
    console.log('    前 3 例：');
    for (const r of bad.slice(0, 3)) {
      console.log(`      #${r.id}  bd=${fmt(r.bd)}  本站=${fmt(r.mine)}  差 ${r.diff > 0 ? '+' : ''}${r.diff}`
        + `  (${r.note || ''})`);
    }
  }
  return bad.length === 0;
}

// ---------------------------------------------------------------------------
// [1] 单卡卡面：只看三围 + 星光 + 特训 + 剧情，无道具无活动
// ---------------------------------------------------------------------------

function testPanel() {
  const ids = Object.keys(RAW);
  const step = Math.max(1, Math.floor(ids.length / SAMPLE));
  const rows = [];
  for (let i = 0; i < ids.length && rows.length < SAMPLE; i += step) {
    const id = ids[i];
    if (!TEAM.cards[id]) continue;
    const raw = RAW[id];
    const lv = bdMaxLevel(raw);
    if (!raw.stat[lv]) continue;
    const master = 5;
    const bd = bdCardPower(raw, bdEpCount(raw), true, lv, master, [0, 0, 0], [0, 0, 0]);
    const mine = totalPower([myCard(id, master)], { charRank: 0 });
    const trainFlat = raw.stat.training
      ? raw.stat.training.performance + raw.stat.training.technique + raw.stat.training.visual
      : 0;
    rows.push({
      id, bd, mine, diff: bd - mine,
      note: trainFlat ? `特训固定 +${trainFlat}` : '无特训',
    });
  }
  return rows;
}

// ---------------------------------------------------------------------------
// [2] 加上角色评级（检验逐围 floor 的位置）
// ---------------------------------------------------------------------------

function testCharRank(rank = 0.11) {
  const ids = Object.keys(RAW);
  const step = Math.max(1, Math.floor(ids.length / SAMPLE));
  const rows = [];
  for (let i = 0; i < ids.length && rows.length < SAMPLE; i += step) {
    const id = ids[i];
    if (!TEAM.cards[id]) continue;
    const raw = RAW[id];
    const lv = bdMaxLevel(raw);
    if (!raw.stat[lv]) continue;
    const master = 5;
    const bd = bdCardPower(raw, bdEpCount(raw), true, lv, master, [rank, rank, rank], [0, 0, 0]);
    const mine = totalPower([myCard(id, master)], { charRank: rank });
    // ⚠️ 这里**故意不要求和 Bestdori 相等**。
    // 角色评级 = 潜能解放 5% + 角色任务 6%，游戏里两项各自向下取整：
    //     floor(v×5%) + floor(v×6%)       ← 本站，贴吧实测口径
    //     floor(v×11%)                    ← Bestdori，合并取整，每围多 0~1
    // 所以本站恒比 Bestdori 小 0~3（三围各差 1）。差超出这个范围才算错。
    const d = bd - mine;
    rows.push({
      id, bd, mine, diff: d,
      note: `评级 ${rank * 100}%`,
      ok: d >= 0 && d <= 3,
    });
  }
  return rows;
}

// ---------------------------------------------------------------------------
// [3] 加上道具 / 活动加成数组（检验「加算」与「无取整」）
// ---------------------------------------------------------------------------

function testBonus() {
  const ids = Object.keys(RAW);
  const step = Math.max(1, Math.floor(ids.length / SAMPLE));
  const rows = [];
  // 三档加成，覆盖「道具 16%」「道具 35%」「道具 35% + 单围再 +50%」
  const CASES = [
    { bonus: [0.16, 0.16, 0.16], note: '全围 16%' },
    { bonus: [0.35, 0.35, 0.35], note: '全围 35%' },
    { bonus: [0.35, 0.35, 0.85], note: '全围 35% + 视觉再 +50%' },
  ];
  for (let i = 0; i < ids.length && rows.length < SAMPLE; i += step) {
    const id = ids[i];
    if (!TEAM.cards[id]) continue;
    const raw = RAW[id];
    const lv = bdMaxLevel(raw);
    if (!raw.stat[lv]) continue;
    const master = 5;
    for (const cs of CASES) {
      const bd = bdCardPower(raw, bdEpCount(raw), true, lv, master, [0, 0, 0], cs.bonus);
      // 本站：拆成「全围」+「单围参数」，走 area 通道（**小数**，不是百分数）
      const all = cs.bonus[0];
      const extra = cs.bonus[2] - cs.bonus[0];
      const mine = totalPower([myCard(id, master)], {
        charRank: 0,
        area: {
          band: all, attr: 0, magazine: 0,
          param: extra, paramStat: 2,
        },
      });
      // Bestdori 的 d() 返回**浮点**（不取整），本站整体 floor —— 比 floor 后的值
      rows.push({ id, bd, mine, diff: bd - mine, ok: Math.abs(bd - mine) < 1e-6 || Math.floor(bd) === mine, note: cs.note });
    }
  }
  return rows;
}

// ---------------------------------------------------------------------------
// [4] 整队综合力：Bestdori 的 et() vs 本站 totalPower()
// ---------------------------------------------------------------------------

/** Bestdori `i.band` 的行顺序 —— 道具 id 按这个顺序分组 */
const BAND_ORDER = [1, 2, 3, 4, 5, 18, 21, 45];

function buildItemLevels({ charRank = 0, bandLv = 7, attrLv = 7, attrRow1 = 0, magLv = 7 } = {}) {
  return {
    // 9 行：8 个乐队 + 1 行「共通道具」（Everyone's Mic 那一组）
    band: [...BAND_ORDER.map(() => Array(7).fill(bandLv)), Array(7).fill(0)],
    // 2 行属性道具：第 0 行 = Menu，第 1 行 = Plaza
    attr: [Array(4).fill(attrLv), Array(4).fill(attrRow1)],
    // 1 行杂志：[表演, 技巧, 视觉]
    param: [Array(3).fill(magLv)],
    // 角色评级：索引 = characterId-1，值以「千分之一」为单位（11 → 1.1%）
    char: Array.from({ length: 200 }, () => charRank * 1000),
  };
}

/**
 * 用 Bestdori 的 `et` 算一支队。
 *
 * ⚠️ `channel`：1 = 加成进点数，2 = 加成进综合力，其他 = 都不进。
 * 这和本站的 `eventChannel()` 是同一个判定。
 */
function bdTeamPower(ids, opt = {}) {
  const {
    master = 5, skillLv = 4, charRank = 0,
    bandIdx = 0, attrIdx = 0, magIdx = null, magLv = 7,
    bandLv = 7, attrLv = 7, attrRow1 = 0,
    channel = 0, attrPct = 0, charPct = 0, doublePct = 0,
    memberPct = 0, cpStat = null, starPct = 0,
  } = opt;

  const members = ids.map((id) => {
    const raw = RAW[id];
    return {
      apiCard: raw,
      skill: skillLv,
      ep: bdEpCount(raw),
      train: true,
      level: bdMaxLevel(raw),
      master,
    };
  });

  // 事件加成数组：属性按 ["powerful","cool","happy","pure"] 排，角色按 charId-1
  const attrBonus = [0, 0, 0, 0];
  attrBonus[attrIdx] = attrPct;
  const charBonus = Array.from({ length: 200 }, () => 0);
  for (const id of ids) charBonus[RAW[id].characterId - 1] = charPct;

  const memberCards = memberPct
    ? ids.map((id) => ({ id: Number(id), percent: memberPct }))
    : [];

  // 星光加成表 [rarity-1][master]，单位是百分比
  const starTable = starPct
    ? Array.from({ length: 5 }, (_, r) =>
      Array.from({ length: 6 }, (_, m) => (m === master ? starPct : 0)))
    : [];

  const out = bdTeam(
    CHARS,                                         // 1 角色表（取 bandId）
    BANDS,                                         // 2 乐队表（keys 顺序 = 道具行顺序）
    ITEMS,                                         // 3 道具效果表
    SKILLS,                                        // 4 技能表（只影响排序）
    members,                                       // 5 队员
    0,                                             // 6 ⚠️ 见下：et 把「围下标」和「技能等级」复用了同一个位置
    buildItemLevels({ charRank, bandLv, attrLv, attrRow1, magLv }), // 7 道具等级 + 角色评级
    attrIdx,                                       // 8 属性道具下标
    bandIdx,                                       // 9 乐队道具行
    magIdx,                                        // 10 杂志下标
    channel,                                       // 11 加成作用域 1=点数 2=综合力
    attrBonus,                                     // 12 属性加成
    charBonus,                                     // 13 角色加成
    memberCards,                                   // 14 当期卡加成
    doublePct,                                     // 15 双匹配加成
    starTable,                                     // 16 星光加成
    cpStat,                                        // 17 活动参数加成（1 基）
  );
  return out.reduce((a, m) => a + m.power, 0);
}

/** 挑一支「同乐队 + 同属性」的 5 张 5★，这样道具能全吃满 */
function pickTestTeam() {
  const byKey = new Map();
  for (const id of Object.keys(TEAM.cards)) {
    const c = TEAM.cards[id];
    if (c.r !== 5) continue;
    const key = `${TEAM.characters[c.ch]?.b}|${c.a}`;
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(id);
  }
  let best = null;
  for (const [key, list] of byKey) {
    const used = new Set();
    const five = [];
    for (const id of list.sort((a, b) => starStat(myCard(b, 5)) - starStat(myCard(a, 5)))) {
      const ch = TEAM.cards[id].ch;
      if (used.has(ch)) continue;
      used.add(ch);
      five.push(id);
      if (five.length === 5) break;
    }
    if (five.length === 5 && (!best || list.length > best.list.length)) best = { key, five, list };
  }
  if (!best) throw new Error('找不到同乐队同属性的 5 张 5★');
  return best;
}

// ---------------------------------------------------------------------------
// 主流程
// ---------------------------------------------------------------------------

console.log('Bestdori 计算引擎已加载');
console.log(`  单卡综合力 d()   ${typeof bdCardPower}`);
console.log(`  组队计算   et()  ${typeof bdTeam}`);

const pass = [];

const r1 = testPanel();
pass.push(['[1] 单卡卡面（星光5 + 特训 + 剧情）', report('[1] 单卡卡面（星光5 + 特训 + 剧情）', r1)]);
{
  const tr = r1.filter((r) => r.note.startsWith('特训'));
  const nt = r1.filter((r) => r.note === '无特训');
  if (tr.length) {
    console.log(`    可特训 ${tr.length} 张，差值集合 = ${[...new Set(tr.map((r) => r.diff))].sort((a, b) => a - b).join(', ')}`);
  }
  if (nt.length) {
    console.log(`    不可特训 ${nt.length} 张，差值集合 = ${[...new Set(nt.map((r) => r.diff))].sort((a, b) => a - b).join(', ')}`);
  }
}

pass.push(['[2] 单卡 + 角色评级 11%', report('[2] 单卡 + 角色评级 11%', testCharRank(0.11))]);
console.log('    ↑ 本站按「潜能 5% + 任务 6%」分别取整（游戏口径），'
  + 'Bestdori 合并 11% 取整，所以本站恒比它小 0~3');
pass.push(['[3] 单卡 + 道具/活动加成数组', report('[3] 单卡 + 道具/活动加成数组', testBonus())]);

// ---------------------------------------------------------------------------
// [4] 整队：逐层加道具，看每一步的增量对不对
// ---------------------------------------------------------------------------

console.log('\n[4] 整队综合力');
const { key, five } = pickTestTeam();
const team = five.map((id) => myCard(id, 5));
const teamAttr = TEAM.cards[five[0]].a;
const attrIdx = ATTRS.indexOf(teamAttr);
const teamBand = TEAM.characters[TEAM.cards[five[0]].ch]?.b;
const norm = (x) => String(x || '').replace(/[＊]/g, '*').trim();
const bandIdx = BAND_ORDER.findIndex((b) => norm(BANDS[b]?.bandName?.[1]) === norm(teamBand));

console.log(`  队伍：${key}  卡号 ${five.join(', ')}`);
console.log(`  属性下标 ${attrIdx}(${teamAttr})   乐队行 ${bandIdx}(${teamBand})`);

/** 一层一层往上加，每层单独比 */
function layer(name, bdOpt, myArea, myEvent) {
  const bd = bdTeamPower(five, bdOpt);
  const mine = totalPower(team, {
    charRank: 0,
    characters: TEAM.characters,
    area: myArea,
    event: myEvent || null,
  });
  const d = bd - mine;
  // Bestdori 的 et() 返回**未取整**的总和，本站最后统一 floor —— 差 < 1 即一致
  const ok = Math.abs(d) < 1;
  console.log(`  ${name.padEnd(24, ' ')} bd=${fmt(bd).padStart(12)}  本站=${fmt(mine).padStart(12)}`
    + `  差 ${(d > 0 ? '+' : '') + fmt(d)}${ok ? '  ✓' : ''}`);
  return ok;
}

/** 一支满级乐队道具的总加成：5 件乐器 ×5.5% + 2 件海报 ×13% = 53.5% */
const BAND_PCT = 5 * 5.5 + 2 * 13;
/**
 * 属性道具：Menu 16% + Plaza 16%。⚠️ Bestdori 的 `at()` 把「等级下标 0」
 * 当成 Lv1 算（2%），没有「没这件道具」的状态，所以两个槽位最低各 2%。
 * 这里按 Bestdori 的口径取 16 + 2。
 */
const ATTR_PCT = 16 + 2;
const myBand = bandIdx >= 0 ? teamBand : undefined;
const layers = [];

layers.push(layer('4a 裸队', { channel: 0 }, { band: 0, attr: 0, magazine: 0 }));
layers.push(layer('4b + 乐队道具 Lv8（7件）',
  { channel: 0, bandIdx, bandLv: 7 },
  { band: BAND_PCT / 100, bandBand: myBand, attr: 0, magazine: 0 }));
layers.push(layer('4c + 属性道具 16%',
  { channel: 0, bandIdx, bandLv: 7, attrIdx, attrLv: 7 },
  { band: BAND_PCT / 100, bandBand: myBand, attr: ATTR_PCT / 100, attrAttr: teamAttr, magazine: 0 }));
layers.push(layer('4d + 杂志 22%（视觉）',
  { channel: 0, bandIdx, bandLv: 7, attrIdx, attrLv: 7, magIdx: 2, magLv: 7 },
  { band: BAND_PCT / 100, bandBand: myBand, attr: ATTR_PCT / 100, attrAttr: teamAttr,
    magazine: 0, param: 22 / 100, paramStat: 2 }));

// 4e 再加活动加成（综合力通道）
const ev = {
  ty: 'versus',
  attr: [{ a: teamAttr, p: 30 }],
  ch: five.map((id) => ({ c: TEAM.cards[id].ch, p: 20 })),
  ab: { pointPercent: 65, parameterPercent: 65 },
  mb: [], mp: 0, lb: [],
  cp: { performance: 50 },
};
layers.push(layer('4e + 活动加成（综合力）',
  { channel: 2, bandIdx, bandLv: 7, attrIdx, attrLv: 7, magIdx: 2, magLv: 7,
    attrPct: 30, charPct: 20, doublePct: 65, cpStat: 1 },
  { band: BAND_PCT / 100, bandBand: myBand, attr: ATTR_PCT / 100, attrAttr: teamAttr,
    magazine: 0, param: 22 / 100, paramStat: 2 },
  ev));

console.log('  单卡加成分解：');
for (const id of five) {
  const eb = eventBonusFor(myCard(id, 5), ev, 5);
  console.log(`    #${id}  合计 ${eb.bonus}%  ${JSON.stringify(eb.parts)}`
    + `  参数围=${eb.paramStat ? ['表演', '技巧', '视觉'][eb.paramStat.stat] : '无'}`);
}

pass.push(['[4] 整队综合力（逐层加道具+活动）', layers.every(Boolean)]);

// ---------------------------------------------------------------------------
// [5] 「大家的话筒」独立一行 —— et() 只读一行，这一行要单独验
// ---------------------------------------------------------------------------

{
  const I = BD.__internal;
  const levels = buildItemLevels({ bandLv: 7 });
  levels.band[8] = Array(7).fill(7);          // 共通那一行拉满
  const pct = I.it(0, levels, ITEMS, 8);      // 表演围的加成比例
  // 共通行 7 件里有 5 件 2.75%、2 件（CiRCLE 海报/传单）6.5%，逐件累加
  const expect = I.i.band[8]
    .reduce((a, id) => a + (ITEMS[id]?.performance?.['8']?.[0] || 0), 0) / 100;
  const okRow = Math.abs(pct - expect) < 1e-9;
  console.log(`\n[5] 大家的话筒（共通行）  it()=${(pct * 100).toFixed(2)}%  期望 ${(expect * 100).toFixed(2)}%`
    + `  ${okRow ? '✓' : '✗'}`);
  pass.push(['[5] 共通道具 7 件叠加', okRow]);
}

// ---------------------------------------------------------------------------
// [6] 分数：Bestdori 的 ct() vs 本站 scoreTeam()
// ---------------------------------------------------------------------------

console.log('\n[6] 分数（同一份谱面、同一个综合力）');
{
  // ct 需要**完整**技能表（level 10 才带 activationEffect），
  // level 5 那份是摘要版，喂进去 st() 会直接 return 1（技能白给）。
  const SKILLS10 = J('.bdh-test/bdapi/skills10.json');
  const chart = await (await fetch('https://bestdori.com/api/charts/7/expert.json')).json();

  const bpm = chart.filter((e) => e.type === 'BPM').sort((a, b) => a.beat - b.beat);
  const beatToTime = (beat) => {
    let t = 0;
    for (let i = 0; i < bpm.length; i++) {
      const cur = bpm[i]; const next = bpm[i + 1];
      if (beat <= cur.beat) return t;
      const span = Math.min(beat, next ? next.beat : beat) - cur.beat;
      if (span > 0) t += (span * 60) / cur.bpm;
      if (beat <= (next ? next.beat : beat)) return t;
    }
    return t;
  };
  const fs = chart.find((e) => e.type === 'System' && e.data === 'cmd_fever_start.wav');
  const fe = chart.find((e) => e.type === 'System' && e.data === 'cmd_fever_end.wav');
  const fi = fs ? fs.beat : -1; const fo = fe ? fe.beat : -1;
  const notes = [];
  for (const e of chart) {
    if (e.type === 'Single' || e.type === 'Directional') {
      notes.push({ time: beatToTime(e.beat), fever: e.beat >= fi && e.beat <= fo, skill: e.skill });
    } else if (e.type === 'Slide' || e.type === 'Long') {
      for (const c of e.connections || []) {
        if (!c.hidden) notes.push({ time: beatToTime(c.beat), fever: c.beat >= fi && c.beat <= fo, skill: c.skill });
      }
    }
  }
  notes.sort((a, b) => a.time - b.time || !!b.skill - !!a.skill);

  const { scoreTeam, parseChart } = await import(pathToFileURL(join(ROOT, 'docs/src/team.js')).href);
  const ids = ['1867', '2430', '2431', '2434', '2032'];
  const five = ids.map((id) => myCard(id, 5));
  const power = totalPower(five, { charRank: 0 });
  const skillIds = five.map((c) => c.sk);
  const skillLvs = five.map(() => 4);

  // Bestdori：ct(skills, serverPref, skillIds, skillLevels, notes, playLevel, prate, fever, bp)
  // ⚠️ serverPref：0=日 1=国 2=台 3=英 4=韩。技能和道具的数值表都按这个下标取，
  //    第 5 位（韩服）经常是 null，传错会静默变成 0。
  // 第 8 位 `fever` 必须是 **true** —— 谱面里 cmd_fever_start/end 之间的音符分数翻倍，
  // 关掉它就等于把 Fever 从模型里摘掉（实测影响 +28%，比任何一处偏差都大）。
  const bd = BD.e(SKILLS10, 1, skillIds, skillLvs, notes, 28, 1, true, power);
  // 本站：用 parseChart 走真实触发点 + Fever，不再只喂时间
  const mine = scoreTeam(five, parseChart(chart),
    { skills: TEAM.skills, level: 5, songLevel: 28, charRank: 0 });

  const ratio = mine.avgScore / bd.avgScore;
  const ratioMax = mine.maxScore / bd.maxScore;
  console.log(`  谱面 God knows... EXPERT Lv28  ${notes.length} notes`
    + `  触发 ${notes.filter((n) => n.skill).length}  Fever ${notes.filter((n) => n.fever).length}`);
  console.log(`  Bestdori  ct: avg=${fmt(bd.avgScore)}  min=${fmt(bd.minScore)}  max=${fmt(bd.maxScore)}`);
  console.log(`  本站 scoreTeam: avg=${fmt(mine.avgScore)}  max=${fmt(mine.maxScore)}`);
  console.log(`  比值 本站/avg = ${ratio.toFixed(4)}   本站/max = ${ratioMax.toFixed(4)}`);
  // ct 的 avg 就是 120 种技能顺序的算术平均；本站用「每窗口每成员增分表 + 查表」
  // 算同一个量，谱面只扫 5 遍而不是 120 遍，结果应当逐位接近。
  // 残差只来自：ct 每个 NOTE 都 floor，本站也 floor（口径一致），所以容差给 0.5%。
  const okRatio = Math.abs(ratio - 1) < 0.005;
  pass.push(['[6] 分数与 Bestdori 一致（本站/avg < 0.5%）', okRatio]);
}

// ---------------------------------------------------------------------------
// [7] 点数：直接切 Bestdori 的 calcEPVS / calcEPFestival / calcEPCL 来跑
// ---------------------------------------------------------------------------

console.log('\n[7] 活动点数（竞演 / FES / 挑战）');
{
  const { eventPoints } = await import(pathToFileURL(join(ROOT, 'docs/src/team.js')).href);
  const chunk = readFileSync(join(ROOT, '.bdh-test/bestdori/ToolTeamBuilder.js'), 'utf8');
  const a = chunk.lastIndexOf(',calcWithSoftCap:function');
  const b = chunk.indexOf(',onCalculate:function');
  if (a < 0 || b < 0) throw new Error('找不到 calcEP* 方法');
  // eslint-disable-next-line no-new-func
  const EP = new Function(`return {${chunk.slice(a + 1, b)}}`)();
  EP.calcWithSoftCap = EP.calcWithSoftCap.bind(EP);
  EP.calcEPVS = EP.calcEPVS.bind(EP);
  EP.calcEPFestival = EP.calcEPFestival.bind(EP);
  EP.calcEPCL = EP.calcEPCL.bind(EP);

  const SCORES = [800000, 1500000, 2500000, 3800000];
  let bad = 0;
  for (const sc of SCORES) {
    for (const rank of [0, 2, 4]) {
      for (const flame of [0, 3]) {
        const bdVS = EP.calcEPVS(2, sc, rank, flame);
        const myVS = eventPoints(sc, { ty: 'versus', s: 0 }, { rank, flame });
        if (bdVS !== myVS) { bad++; console.log(`  ✗ vs score=${sc} rank=${rank} flame=${flame}: bd=${bdVS} mine=${myVS}`); }

        const bdFes = EP.calcEPFestival(2, sc, 0, rank, flame);
        const myFes = eventPoints(sc, { ty: 'festival', s: 0 }, { rank, flame, win: true });
        if (bdFes !== myFes) { bad++; console.log(`  ✗ fes score=${sc} rank=${rank} flame=${flame}: bd=${bdFes} mine=${myFes}`); }
      }
    }
    for (const cp of [0, 1, 2]) {
      const bdCL = EP.calcEPCL(2, sc, cp);
      const myCL = eventPoints(sc, { ty: 'challenge', s: 0 }, { cp });
      if (bdCL !== myCL) { bad++; console.log(`  ✗ cl score=${sc} cp=${cp}: bd=${bdCL} mine=${myCL}`); }
    }
  }
  console.log(`  竞演 / FES / 挑战 × ${SCORES.length} 档分数 × 名次/火/CP 组合：不一致 ${bad} 处`);
  pass.push(['[7] 竞演/FES/挑战 点数逐字一致', bad === 0]);

  // 通用型（传统/任务/试炼）用 W/U + 软上限，单独核对一组
  const gen = [
    { ty: 'story', W: 50, U: 1e4 },
    { ty: 'mission_live', W: 120, U: 15000 },
    { ty: 'live_try', W: 130, U: 26000 },
  ];
  let badGen = 0;
  for (const g of gen) {
    for (const sc of SCORES) {
      const bd = g.W + Math.floor(sc / g.U);
      const mine = eventPoints(sc, { ty: g.ty, s: 0 }, { formula: 2, flame: 0, bonusSum: 0 });
      if (bd !== mine) { badGen++; console.log(`  ✗ ${g.ty} score=${sc}: bd=${bd} mine=${mine}`); }
    }
  }
  console.log(`  传统 / 任务 / 试炼（V2，1 火）不一致 ${badGen} 处`);
  pass.push(['[7] 通用型点数一致', badGen === 0]);
}

// ---------------------------------------------------------------------------
// [8] 支援乐队（Mission Live 的「组合加成」）
// ---------------------------------------------------------------------------

console.log('\n[8] 支援乐队');
{
  const { eventPoints, pickSupportTeam, prepareSupportPool } = await import(
    pathToFileURL(join(ROOT, 'docs/src/team.js')).href
  );

  // 8a. 一局真实结算的逐项对账（用户 2026-10-30 提供的截图）
  //   基本 120 + floor(1,747,439/15,000)=116  → 236
  //   加成 148%：floor(236 × 2.48) = 585
  //   支援 234,012 → floor(234012/3000) = 78
  //   585 + 78 = 663，× 40 火 = 26,520
  const got = eventPoints(1747439, { ty: 'mission_live' }, {
    formula: 2, flameMult: 40, bonusSum: 148, supportPower: 234012,
  });
  console.log(`  实测样本（score 1,747,439 / 加成 148% / 支援 234,012 / 火×40）`);
  console.log(`    本站 = ${got.toLocaleString()}   结算界面 = 26,520   ${got === 26520 ? '✓' : '✗'}`);
  pass.push(['[8] 支援项与真实结算逐项一致（26,520）', got === 26520]);

  // 8b. 支援队的三条硬性约束（Bestdori `X()` 的 while 循环条件）
  const pool = Object.entries(TEAM.cards)
    .map(([id, c]) => ({ id: Number(id), ...c }))
    .filter((c) => c.sx);
  const main = pool.slice(0, 5);
  const sup = pickSupportTeam(prepareSupportPool(pool, { charRank: 0 }), main, { charRank: 0 });
  const chars = new Set(sup.team.map((c) => c.ch));
  const overlap = sup.team.filter((c) => main.some((m) => m.id === c.id)).length;
  console.log(`  支援队 ${sup.team.map((c) => c.id).join(',')}`
    + `  综合力 ${fmt(sup.power)} → ${sup.points} P`);
  console.log(`    张数 ${sup.team.length}/5   角色去重 ${chars.size}/5   与主队重叠 ${overlap}`);
  pass.push(['[8] 支援队 = 5 张 / 角色不重复 / 不占主队卡',
    sup.team.length === 5 && chars.size === 5 && overlap === 0]);

  // 8c. 支援队必须是「每个角色最强的卡」，不能是卡号最小的
  //   踩过：先按角色去重再排序，选中一批 2★（16,765），综合力少 65%
  const topSum = Math.max(...pool.map((c) => c.sx.reduce((a, b) => a + b, 0)));
  const pickedMax = Math.max(...sup.team.map((c) => c.sx.reduce((a, b) => a + b, 0)));
  console.log(`    选中卡的最高三围 ${fmt(pickedMax)} / 全池最高 ${fmt(topSum)}`);
  pass.push(['[8] 支援队取的是每个角色最强的卡', pickedMax === topSum]);
}

console.log('\n────────────────────────────────────');
for (const [name, ok] of pass) console.log(`${ok ? '✓' : '✗'} ${name}`);
console.log(`\n共 ${pass.length} 组，通过 ${pass.filter((x) => x[1]).length} 组`);
