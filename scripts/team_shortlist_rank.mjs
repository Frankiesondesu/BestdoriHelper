/**
 * 量化「粗排 + 硬截断 600」到底有多少余量。
 *
 * `bestTeams` 现在是两阶段：先用 `roughKey`（代理值）把全部组合排一遍，只把
 * 前 `PRECISE_LIMIT` 组交给精确计分。**这是启发式截断，不是有证明的剪枝** ——
 * 代理值排错了就会把真正的最优解切掉。
 *
 * 直接测「漏没漏」很贵（要对每期活动做全量精确枚举）。但有个更省的办法：
 * 把 `preciseLimit` 开到全量，让 `onStats.bestRank` 告诉我们
 * **真正的最优解在粗排序列里排第几**。
 *
 * - `bestRank` 远小于 600 → 余量充足，代理值可靠
 * - `bestRank` 接近或超过 600 → 该期活动有被截断的风险
 *
 * 用法：
 *   node scripts/team_shortlist_rank.mjs            # 最近 6 期
 *   node scripts/team_shortlist_rank.mjs --events 12
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const D = (p) => JSON.parse(readFileSync(join(ROOT, 'docs/data', p), 'utf8'));
const TEAM = D('team.json');
const EVENTS = D('events.json');
const SONGS = D('songs.json');

const { bestTeams, parseChart } = await import(pathToFileURL(join(ROOT, 'docs/src/team.js')).href);

const argv = process.argv.slice(2);
const argOf = (name, dflt) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 ? Number(argv[i + 1]) : dflt;
};
const LIMIT = 600;
const N_EVENTS = argOf('events', 6);

const songId = Object.keys(SONGS).find((k) => SONGS[k].lv?.expert >= 27) || Object.keys(SONGS)[0];
const parsed = parseChart(D(`charts/expert/${songId}.json`));
const songLevel = SONGS[songId].lv.expert;
const pool = Object.entries(TEAM.cards)
  .map(([id, c]) => ({ id: Number(id), ...c }))
  .filter((c) => c.r >= 4 && c.sx);

console.log(`曲目 ${SONGS[songId].t} Lv.${songLevel} / ${parsed.notes.length} notes，卡池 ${pool.length} 张`);
console.log(`截断阈值 PRECISE_LIMIT = ${LIMIT}\n`);

const ids = Object.keys(EVENTS).sort((a, b) => Number(b) - Number(a))
  .filter((k) => (EVENTS[k].mb || []).length).slice(0, N_EVENTS);

let worstRatio = 0;
let risk = 0;

for (const eid of ids) {
  const ev = EVENTS[eid];
  const ctx = {
    skills: TEAM.skills, level: 5, songLevel, potentials: null,
    area: { band: 0, attr: 0, magazine: 0 }, event: ev, charRank: 0.11, flame: 1,
  };
  let stat = null;
  const t = Date.now();
  const full = await bestTeams(pool, parsed, ctx, {
    topN: 1, characters: TEAM.characters, preciseLimit: 1e9,
    onStats: (s) => { stat = { ...stat, ...s }; },
  });
  const dt = Date.now() - t;

  const rank = stat?.bestRank ?? -1;
  const shortlist = stat?.shortlist ?? 0;
  const ratio = rank >= 0 ? rank / LIMIT : Infinity;
  if (Number.isFinite(ratio)) worstRatio = Math.max(worstRatio, ratio);
  if (rank >= LIMIT) risk += 1;

  console.log(
    `活动 ${eid} ${ev.n} (${ev.ty})`.padEnd(44)
    + `候选 ${shortlist} 组  最优解排第 ${rank} 位  (阈值 ${LIMIT} 的 ${(ratio * 100).toFixed(1)}%)`
    + `  PT ${full[0]?.points}  ${dt}ms`,
  );
}

console.log('');
if (risk) {
  console.log(`✗ ${risk}/${ids.length} 期活动的最优解排在 ${LIMIT} 之后 —— 这些期会被截断漏掉`);
} else {
  console.log(`✓ 全部 ${ids.length} 期的最优解都在前 ${LIMIT} 之内（最差用到阈值的 ${(worstRatio * 100).toFixed(1)}%）`);
}
process.exit(risk ? 1 : 0);
