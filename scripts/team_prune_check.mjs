/**
 * 验证两阶段剪枝没有漏掉真正的最优解。
 *
 * 做法：拿**同一份候选池**（`buildCandidatePool` 的返回值），用「全量精确枚举」
 * 和「bestTeams 的剪枝枚举」各算一遍，比较最优活动点数。
 *
 * 两条硬要求：
 *
 * 1. **输入必须同源。** 原先这里自己抄了一份候选池构造逻辑，两边一旦不同步，
 *    对拍就变成「拿 A 的输入比 B 的输出」—— 看着全绿，其实什么都没证明。
 *    现在两边都调 `buildCandidatePool`。
 * 2. **口径必须一致。** 全量版必须像 `bestTeams` 一样把支援乐队算进点数，
 *    否则 mission_live 会出现「剪枝版 PT 反而更高」的假差距（实测 -9.02%）。
 *    为此负差距单独判定为**口径不一致**，不算通过。
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

const {
  bestTeams, buildCandidatePool, eventBonusFor, eventPoints, pickSupportTeam,
  prepareSupportPool, scoreTeam, parseChart,
} = await import(pathToFileURL(join(ROOT, 'docs/src/team.js')).href);

const songId = Object.keys(SONGS).find((k) => SONGS[k].lv?.expert >= 27) || Object.keys(SONGS)[0];
const parsed = parseChart(D(`charts/expert/${songId}.json`));
const songLevel = SONGS[songId].lv.expert;
const pool = Object.entries(TEAM.cards)
  .map(([id, c]) => ({ id: Number(id), ...c }))
  .filter((c) => c.r >= 4 && c.sx);

console.log(`曲目 ${SONGS[songId].t} Lv.${songLevel} / ${parsed.notes.length} notes，卡池 ${pool.length} 张`);

let worst = 0;         // 剪枝比全量差多少（正数才是真损失）
let worstMismatch = 0; // 剪枝比全量好多少（说明两条路径口径不一致）
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

  // ---- 全量精确版：候选池与剪枝版**同源**，只是不做截断 ---------------------
  const cand = buildCandidatePool(pool, ctx);
  const n = cand.length;
  // 支援乐队池与主队无关，按 solo 综合力排一次即可（和 bestTeams 内部一致）
  const supportPool = ev.ty === 'mission_live' ? prepareSupportPool(pool, ctx) : null;

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
          const sup = supportPool ? pickSupportTeam(supportPool, team, ctx) : null;
          const pt = eventPoints(r.score, ev, {
            bonusSum: bon, supportPower: sup ? sup.power : 0, flame: 1,
          });
          if (pt > bestPt) { bestPt = pt; bestScore = r.score; bestTeam = team.map((x) => x.id); }
        }
      }
  const dtFull = Date.now() - t2;

  const fastPt = fast[0]?.points ?? 0;
  const gap = bestPt ? ((bestPt - fastPt) / bestPt) * 100 : 0;
  if (gap >= 0) worst = Math.max(worst, gap);
  else worstMismatch = Math.max(worstMismatch, -gap);

  console.log(`\n活动 ${eid} ${ev.n} (${ev.ty}) 候选 ${n} 张 = C(${n},5)=${Math.round(n * (n - 1) * (n - 2) * (n - 3) * (n - 4) / 120)} 组`);
  console.log(`  全量精确: PT ${bestPt}  分数 ${bestScore}  ${dtFull}ms  [${bestTeam}]`);
  console.log(`  剪枝枚举: PT ${fastPt}  分数 ${fast[0]?.score}  ${dtFast}ms  [${fast[0]?.team.map((x) => x.id)}]`);
  console.log(`  差距 ${gap.toFixed(2)}%${gap < 0 ? '  ← 剪枝版更高，两条路径口径不一致' : ''}`);
}

const ok = worst <= 0.5 && worstMismatch <= 0.001;
if (worstMismatch > 0.001) {
  console.log(`\n✗ 剪枝版比全量版高 ${worstMismatch.toFixed(2)}% —— 两条路径计分口径不同，对拍无效`);
} else if (!ok) {
  console.log(`\n✗ 剪枝差距 ${worst.toFixed(2)}% 过大`);
} else {
  console.log(`\n✓ 剪枝最大差距 ${worst.toFixed(2)}%（≤0.5%，可接受）`);
}
process.exit(ok ? 0 : 1);
