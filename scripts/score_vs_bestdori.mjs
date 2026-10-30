/**
 * 分数对拍：本站 `scoreTeam()` vs Bestdori `ct()`（多谱面 × 多技能类型）。
 *
 * 与 `team_vs_bestdori.mjs` 的 [6] 的分工：那个只测一首歌、一组技能，
 * 比值 1.0000 可能是巧合。这个扫 24 个谱面 × 5 种技能类型，**逐位比较**。
 *
 * 分两个实验：
 *
 *   A. **算法骨架对拍** —— 两边喂同一份 notes、同一个综合力、**同一批纯 score
 *      型技能**（`st()` 走 `r = 1 + C/100` 分支，等价于固定倍率）。
 *      这一步验证「窗口划分 / combo / fever / 两次 floor / 120 序平均」这套骨架。
 *
 *   B. **归一化损失** —— 站点 `team.json` 把技能压成 `{d, up}` 固定倍率，
 *      而 Bestdori `st()` 对连击型/条件型技能算的是逐音符动态倍率。
 *      这一步量化「用固定倍率替代动态倍率」错多少。
 *
 * 实测结论（2026-10-30，normal 24 首）：
 *
 *   A. 24/24 逐位相等，相对差 0.00000%
 *
 *   B. 按 4★+ 卡池占比加权后的总影响：
 *        P=100%  -0.055%     P=95%  +0.40%     P=90%  +0.69%
 *      分类型（P=90%）：
 *        score                   59.6%   0.000%
 *        life                    12.2%   0.000%
 *        continued               22.3%  +3.069%   ← 连击型，P 越低越偏低
 *        rate_up_with_perfect     4.4%  -1.126%
 *        great_half               1.5%  +3.790%
 *      所以「站点不模拟动态技能」在默认 P=100% 下几乎无代价；
 *      玩家真打 P=90% 时低估约 0.7%，属于可接受范围，不值得为它重写计分内核。
 *
 * 用法：
 *   node scripts/score_vs_bestdori.mjs
 *   node scripts/score_vs_bestdori.mjs --verbose
 *   node scripts/score_vs_bestdori.mjs --prate=0.95 --diff=hard
 */

import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..');
const VERBOSE = process.argv.includes('--verbose');

const { loadBestdoriCalc } = await import(pathToFileURL(join(ROOT, 'scripts/bestdori_ref.mjs')).href);
const { exports: BD } = await loadBestdoriCalc({ debug: true });

const team = await import(pathToFileURL(join(ROOT, 'docs/src/team.js')).href);
const { scoreTeam, totalPower } = team;

const J = (p) => JSON.parse(readFileSync(join(ROOT, p), 'utf8'));
const fmt = (x) => (x ?? 0).toLocaleString('en-US', { maximumFractionDigits: 2 });

const SKILLS10 = J('.bdh-test/bdapi/skills10.json');
const RAWCARDS = J('.bdh-test/bdapi/cards.json');
const SONGS = J('docs/data/songs.json');

// ---------------------------------------------------------------------------
// 谱面
// ---------------------------------------------------------------------------

function notesFromCompact(o) {
  const sk = new Set(o.s || []);
  const f = Array.isArray(o.f) && o.f.length === 2 ? o.f : null;
  return o.n.map((t, i) => ({
    time: t,
    skill: sk.has(i),
    fever: !!f && t >= f[0] && t <= f[1],
  }));
}

function pickCharts(limit, diff = 'normal') {
  const files = readdirSync(join(ROOT, 'docs/data/charts', diff)).sort(
    (a, b) => Number(a.replace('.json', '')) - Number(b.replace('.json', '')),
  );
  const step = Math.max(1, Math.floor(files.length / limit));
  const out = [];
  for (let i = 0; i < files.length && out.length < limit; i += step) {
    const sid = files[i].replace('.json', '');
    const o = J(`docs/data/charts/${diff}/${files[i]}`);
    if (!o.n?.length) continue;
    const notes = notesFromCompact(o);
    if (notes.filter((n) => n.skill).length !== 6) continue;  // 只要标准 6 触发
    const meta = SONGS[sid];
    if (!meta) continue;
    out.push({ sid, notes, level: meta.lv?.[diff] ?? 26, title: meta.t });
  }
  return out;
}

// ---------------------------------------------------------------------------
// 技能类型判定（用 Bestdori 原始的 activationEffect）
// ---------------------------------------------------------------------------

function skillKind(sid) {
  const t = SKILLS10[sid]?.activationEffect?.activateEffectTypes;
  if (!t) return 'none';
  const keys = Object.keys(t);
  if (keys.includes('score_rate_up_with_perfect')) return 'rate_up_with_perfect';
  if (keys.includes('score_continued_note_judge')) return 'continued';
  if (keys.includes('score_under_great_half')) return 'great_half';
  if (keys.includes('score_over_life') || keys.includes('score_under_life')) return 'life';
  if (keys.includes('score_only_perfect')) return 'only_perfect';
  return 'score';
}

/** 从 Bestdori 技能表提取「站点归一化」后的 {d, up}（level 5） */
function normalizedSkill(sid) {
  const s = SKILLS10[sid];
  const t = s?.activationEffect?.activateEffectTypes;
  const dur = s?.duration ?? [5, 5.5, 6, 6.5, 7];
  let up = 0;
  for (const k of Object.keys(t || {})) {
    if (!k.startsWith('score')) continue;
    const v = t[k].activateEffectValue;
    up = Array.isArray(v) ? v[0] : v;
    break;
  }
  return { d: dur, up };
}

// ---------------------------------------------------------------------------
// 造队
// ---------------------------------------------------------------------------

/**
 * Bestdori 原始卡 → 站点格式。
 *
 * 口径对齐 `docs/data/team.json`（实测 card 1205 对得上）：
 *   sx = stat['60'] + stat.training 的三围    （9517 = 9117 + 400）
 *   ep = 所有 episode 的三围分别求和          （850 = 250 + 600）
 * ⚠️ `levelLimit` 是**解锁上限**（50/60），不是等级；满级固定 60。
 */
const K3 = ['performance', 'technique', 'visual'];
function toSiteCard(rawId, master = 0) {
  const r = RAWCARDS[rawId];
  const st = r.stat;
  const base = st['60'] ?? st[String(Math.max(...Object.keys(st).filter((k) => /^\d+$/.test(k)).map(Number)))];
  const tr = st.training;
  const sx = K3.map((k) => (base?.[k] ?? 0) + (master && tr ? (tr[k] ?? 0) : 0));
  const ep = K3.map((k) => (st.episodes || []).reduce((a, e) => a + (e[k] ?? 0), 0));
  return {
    id: Number(rawId),
    sk: Number(rawId),           // ← 用原始技能 id 当 key，绕过站点归一化
    sx,
    ep,
    r: r.rarity,
    master,
  };
}

// ---------------------------------------------------------------------------
// A. 算法骨架对拍（纯 score 型技能）
// ---------------------------------------------------------------------------

const DIFF = process.argv.find((a) => a.startsWith('--diff='))?.slice(7) || 'normal';
const charts = pickCharts(24, DIFF);
console.log(`谱面 ${charts.length} 个（${DIFF}，全部 6 触发）`);

const pureIds = Object.keys(RAWCARDS).filter((id) => {
  const r = RAWCARDS[id];
  return r.rarity === 5 && skillKind(r.skillId) === 'score';
});
const seenChar = new Set();
const teamIds = [];
for (const id of pureIds) {
  const ch = RAWCARDS[id].characterId;
  if (seenChar.has(ch)) continue;
  seenChar.add(ch);
  teamIds.push(id);
  if (teamIds.length === 5) break;
}

const five = teamIds.map((id) => toSiteCard(id));
const powerRaw = totalPower(five, { charRank: 0 });
const bp = Math.floor(powerRaw);

console.log(`\n队伍（纯 score 型 5★）: ${teamIds.join(' ')}`);
console.log(`综合力 raw=${fmt(powerRaw)}  floor=${fmt(bp)}`);

const siteSkills = {};
for (const id of teamIds) siteSkills[String(id)] = normalizedSkill(RAWCARDS[id].skillId);

const skillIds = teamIds.map((id) => RAWCARDS[id].skillId);
const skillLvs = teamIds.map(() => 4);   // ct 的 skillLv 是 0-based → 4 = 游戏内 Lv5

console.log('\n=== A. 算法骨架对拍（同一份输入，只比算法） ===');
const rowsA = [];
for (const ch of charts) {
  const bd = BD.e(SKILLS10, 1, skillIds, skillLvs, ch.notes, ch.level, 1, true, bp);
  const mine = scoreTeam(five, { notes: ch.notes, duration: ch.notes.at(-1).time }, {
    skills: siteSkills,
    level: 5,
    songLevel: ch.level,
    charRank: 0,
    power: bp,
  });
  const dAvg = mine.avgScore - bd.avgScore;
  const dMax = mine.maxScore - bd.maxScore;
  rowsA.push({ sid: ch.sid, title: ch.title, level: ch.level, n: ch.notes.length, bd, mine, dAvg, dMax });
}

const exactA = rowsA.filter((r) => r.dAvg === 0 && r.dMax === 0).length;
const maxRelA = Math.max(...rowsA.map((r) => Math.abs(r.dAvg) / (r.bd.avgScore || 1)));
console.log(`  逐位相等（avg 且 max）: ${exactA}/${rowsA.length}`);
console.log(`  相对差最大 ${(maxRelA * 100).toFixed(5)}%`);
if (VERBOSE || exactA < rowsA.length) {
  for (const r of [...rowsA].sort((a, b) => Math.abs(b.dAvg) - Math.abs(a.dAvg)).slice(0, 8)) {
    console.log(`    ${String(r.sid).padStart(4)} Lv${r.level} n=${String(r.n).padStart(4)}`
      + `  bd=${fmt(r.bd.avgScore)}  mine=${fmt(r.mine.avgScore)}  Δ=${fmt(r.dAvg)}`
      + `  maxΔ=${fmt(r.dMax)}`);
  }
}

// ---------------------------------------------------------------------------
// B. 归一化损失（动态技能 vs 固定倍率）
// ---------------------------------------------------------------------------

console.log('\n=== B. 归一化损失（动态技能压成固定倍率错多少） ===');
const PRATE = Number(process.argv.find((a) => a.startsWith('--prate='))?.slice(8) ?? 1);
console.log(`  判定率 P = ${PRATE}（Bestdori 的 ct 第 7 参）`);
const rowsB = [];
for (const kind of ['continued', 'rate_up_with_perfect', 'great_half', 'score', 'life']) {
  const pool = Object.keys(RAWCARDS).filter((id) => {
    const r = RAWCARDS[id];
    return r.rarity >= 4 && skillKind(r.skillId) === kind;
  });
  const seen = new Set();
  const ids = [];
  for (const id of pool) {
    const ch = RAWCARDS[id].characterId;
    if (seen.has(ch)) continue;
    seen.add(ch);
    ids.push(id);
    if (ids.length === 5) break;
  }
  if (ids.length < 5) { console.log(`  ${kind}: 卡不够（${ids.length}），跳过`); continue; }

  const cards = ids.map((id) => toSiteCard(id));
  const p = Math.floor(totalPower(cards, { charRank: 0 }));
  const sk = {};
  for (const id of ids) sk[String(id)] = normalizedSkill(RAWCARDS[id].skillId);
  const sids = ids.map((id) => RAWCARDS[id].skillId);

  let worst = 0;
  let sum = 0;
  for (const ch of charts) {
    const bd = BD.e(SKILLS10, 1, sids, skillLvs, ch.notes, ch.level, PRATE, true, p);
    const mine = scoreTeam(cards, { notes: ch.notes, duration: ch.notes.at(-1).time }, {
      // ⚠️ ctx.judge 是**已合成的倍率** `1.1P + 0.8(1-P)`，不是 P 率本身
      skills: sk, level: 5, songLevel: ch.level, charRank: 0, power: p,
      judge: 1.1 * PRATE + 0.8 * (1 - PRATE),
    });
    const rel = (mine.avgScore - bd.avgScore) / (bd.avgScore || 1);
    worst = Math.max(worst, Math.abs(rel));
    sum += rel;
  }
  const mean = sum / charts.length;
  rowsB.push({ kind, mean, worst });
  console.log(`  ${kind.padEnd(11)} 平均偏差 ${(mean * 100).toFixed(3).padStart(8)}%   最大 ${(worst * 100).toFixed(3).padStart(7)}%`);
}

// ---------------------------------------------------------------------------
// C. 综合力取整的影响
// ---------------------------------------------------------------------------

console.log('\n=== C. 综合力取整（ct 的 bp 是 Math.floor） ===');
{
  const ch = charts[0];
  const noFloor = scoreTeam(five, { notes: ch.notes, duration: ch.notes.at(-1).time }, {
    skills: siteSkills, level: 5, songLevel: ch.level, charRank: 0,
  });
  const withFloor = scoreTeam(five, { notes: ch.notes, duration: ch.notes.at(-1).time }, {
    skills: siteSkills, level: 5, songLevel: ch.level, charRank: 0, power: bp,
  });
  console.log(`  totalPower 原值 ${fmt(powerRaw)}（小数部分 ${(powerRaw - bp).toFixed(4)}）`);
  console.log(`  用原值 avg=${fmt(noFloor.avgScore)}   用 floor avg=${fmt(withFloor.avgScore)}`
    + `   差 ${fmt(noFloor.avgScore - withFloor.avgScore)}`);
  console.log(`  scoreTeam 是否吃 opts.power：${Math.abs(noFloor.avgScore - withFloor.avgScore) > 0.5 ? '是' : '否（忽略了）'}`);
}
