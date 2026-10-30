/**
 * 验证两阶段剪枝没有漏掉真正的最优解。
 *
 * 做法：对同一份候选池，用「全量精确枚举」和「bestTeams 的剪枝枚举」各算一遍，
 * 比较最优活动点数。剪枝版只要 **等于或接近** 全量版就算通过（剪枝是近似，
 * 但差距应该在 0.5% 以内）。
 *
 * 用法：node scripts/team_prune_check.mjs
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const D = (p) => JSON.parse(readFileSync(join(ROOT, 'docs/data', p), 'utf8'));
const TEAM = D('team.json');
const EVENTS = D('events.json');
const SONGS = D('songs.json');

const { bestTeams, eventBonusFor, eventPoints, scoreTeam, parseChart } = await import(
  pathToFileURL(join(ROOT, 'docs/src/team.js')).href
);

const songId = Object.keys(SONGS).find((k) => SONGS[k].lv?.expert >= 27) || Object.keys(SONGS)[0];
const parsed = parseChart(D(`charts/expert/${songId}.json`));
const songLevel = SONGS[songId].lv.expert;
const pool = Object.entries(TEAM.cards)
  .map(([id, c]) => ({ id: Number(id), ...c }))
  .filter((c) => c.r >= 4 && c.sx);

console.log(`曲目 ${SONGS[songId].t} Lv.${songLevel} / ${parsed.notes.length} notes，卡池 ${pool.length} 张`);

let worst = 0;
// 取最近 3 期有成员加成的活动
const ids = Object.keys(EVENTS).sort((a, b) => Number(b) - Number(a))
  .filter((k) => (EVENTS[k].mb || []).length).slice(0, 3);

for (const eid of ids) {
  const ev = EVENTS[eid];
  const ctx = {
    skills: TEAM.skills, level: 5, songLevel, potentials: null,
    area: { band: 0, attr: 0, magazine: 0 }, event: ev, charRank: 0.11, flame: 1,
  };

  // ---- 剪枝版 -------------------------------------------------------------
  const t1 = Date.now();
  const fast = await bestTeams(pool, parsed, ctx, { topN: 1, characters: TEAM.characters });
  const dtFast = Date.now() - t1;

  // ---- 全量精确版：候选池构造和 bestTeams 一致，但枚举全部组合 -------------
  const withSum = pool.map((c) => ({ c, sum: c.sx.reduce((a, b) => a + b, 0) }))
    .sort((a, b) => b.sum - a.sum);
  const memberIds = new Set((ev.mb || []).map(Number));
  const picked = new Map();
  const charCnt = new Map();
  const tryPick = (card) => {
    const k = card.ch;
    const used = charCnt.get(k) || 0;
    if (used >= 2) return false;
    charCnt.set(k, used + 1);
    picked.set(card.id, card);
    return true;
  };
  for (const x of withSum) { if (picked.size >= 8) break; if (!charCnt.get(x.c.ch)) tryPick(x.c); }
  for (const x of withSum) { if (picked.size >= 8) break; tryPick(x.c); }
  const scored = withSum.map((x) => ({ ...x, bonus: eventBonusFor(x.c, ev, 0).bonus }))
    .filter((x) => x.bonus > 0);
  for (const x of scored) if (memberIds.has(Number(x.c.id))) picked.set(x.c.id, x.c);
  for (const x of scored.filter((x) => !memberIds.has(Number(x.c.id)))
    .sort((a, b) => (b.bonus - a.bonus) || (b.sum - a.sum)).slice(0, 12)) picked.set(x.c.id, x.c);

  const cand = [...picked.values()];
  const n = cand.length;
  let bestPt = 0, bestScore = 0, bestTeam = null;
  const t2 = Date.now();
  for (let a = 0; a < n - 4; a++) for (let b = a + 1; b < n - 3; b++)
    for (let c = b + 1; c < n - 2; c++) for (let d = c + 1; d < n - 1; d++)
      for (let e = d + 1; e < n; e++) {
        const five = [cand[a], cand[b], cand[c], cand[d], cand[e]];
        // 同角色不能重复上阵（和 bestTeams 内部约束保持一致）
        if (new Set(five.map((x) => x.ch)).size !== 5) continue;
        const bySkill = [...five].sort((x, y) =>
          (TEAM.skills[String(y.sk)]?.up || 0) - (TEAM.skills[String(x.sk)]?.up || 0));
        for (const cap of bySkill.slice(0, 2)) {
          const team = [cap, ...five.filter((x) => x !== cap)];
          const r = scoreTeam(team, parsed, ctx);
          // 进点数的是 5 张卡的加成**合计**，不是平均
          const bon = team.reduce((s, x) => s + eventBonusFor(x, ev, 0).bonus, 0);
          const pt = eventPoints(r.score, ev, { bonusSum: bon, flame: 1 });
          if (pt > bestPt) { bestPt = pt; bestScore = r.score; bestTeam = team.map((x) => x.id); }
        }
      }
  const dtFull = Date.now() - t2;

  const gap = bestPt ? ((bestPt - (fast[0]?.points ?? 0)) / bestPt) * 100 : 0;
  worst = Math.max(worst, gap);
  console.log(`\n活动 ${eid} ${ev.n} (${ev.ty}) 候选 ${n} 张 = C(${n},5)=${Math.round(n * (n - 1) * (n - 2) * (n - 3) * (n - 4) / 120)} 组`);
  console.log(`  全量精确: PT ${bestPt}  分数 ${bestScore}  ${dtFull}ms  [${bestTeam}]`);
  console.log(`  剪枝枚举: PT ${fast[0]?.points}  分数 ${fast[0]?.score}  ${dtFast}ms  [${fast[0]?.team.map((x) => x.id)}]`);
  console.log(`  差距 ${gap.toFixed(2)}%`);
}

console.log(worst <= 0.5 ? `\n✓ 剪枝最大差距 ${worst.toFixed(2)}%（≤0.5%，可接受）` : `\n✗ 剪枝差距 ${worst.toFixed(2)}% 过大`);
process.exit(worst <= 0.5 ? 0 : 1);
