/**
 * 组队算法回归检查（Node 直跑，不依赖浏览器）。
 *
 * 校验的是「公式本身对不对」，所以关键几项用**合成数据**断言（不依赖任何一期
 * 具体活动），再用真实数据跑端到端，防止集成层出问题。
 *
 *  1. 单卡活动加成：65% / 56% / 85% 这几个锚点
 *  2. 星光三围：每级每围 +50×星级
 *  3. 综合力：卡面 + 道具 + 活动 **加算**（不是连乘）
 *  4. 活动参数加成：双匹配的卡，指定那一围 +50%
 *  5. 技能发动次数：所有模式都是 6 次
 *  6. 活动点数：各类型的 V2 公式 + 加成用**合计**不是平均
 *  7. 端到端：吃满加成的队 vs 综合力最高的队
 *
 * 用法：
 *   node scripts/team_alg_check.mjs            # 用 events.json 里最新一期活动
 *   node scripts/team_alg_check.mjs 327        # 指定活动 id
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const D = (p) => JSON.parse(readFileSync(join(ROOT, 'docs/data', p), 'utf8'));

const TEAM = D('team.json');
const EVENTS = D('events.json');
const SONGS = D('songs.json');

// Windows 下动态 import 必须给 file:// URL，直接传 `D:\...` 会报
// ERR_UNSUPPORTED_ESM_URL_SCHEME（协议被当成 'd:'）
const {
  eventBonusFor, bestTeams, eventPoints, scoreTeam, skillSlots, parseChart,
  starStat, totalPower, eventChannel, formulaVersion,
} = await import(pathToFileURL(join(ROOT, 'docs/src/team.js')).href);

let failed = 0;
const ok = (cond, msg) => {
  if (cond) console.log(`  ✓ ${msg}`);
  else { console.log(`  ✗ ${msg}`); failed++; }
};
const near = (a, b, tol = 0.01) => Math.abs(a - b) <= tol;

// ---------------------------------------------------------------------------
// 1. 单卡活动加成（合成活动，锚定三个已知数字）
// ---------------------------------------------------------------------------
console.log('\n[1] 单卡活动加成');

const LB = [
  { r: 1, k: 4, p: 0.5 }, { r: 2, k: 4, p: 1 }, { r: 3, k: 4, p: 4 },
  { r: 4, k: 4, p: 6 }, { r: 5, k: 4, p: 15 },
];
const mkEv = (over = {}) => ({
  ty: 'mission_live',
  attr: [{ a: 'cool', p: 10 }],
  ch: [{ c: 1, p: 20 }],
  ab: { pointPercent: 20, parameterPercent: 0 },
  mb: [900001],
  mp: 20,
  lb: LB,
  cp: {},
  s: 1792990800,
  ...over,
});
const card = (o) => ({ id: 1, ch: 1, a: 'cool', r: 5, ...o });

const evPt = mkEv();

const c5 = card({ id: 1 });
const b5 = eventBonusFor(c5, evPt, 4);
ok(near(b5.bonus, 65), `非当期 5★ 双匹配满星光 = ${b5.bonus}%（应 65 = 10+20+20+0+15）`);

const b4 = eventBonusFor(card({ id: 1, r: 4 }), evPt, 4);
ok(near(b4.bonus, 56), `非当期 4★ 双匹配满星光 = ${b4.bonus}%（应 56 = 10+20+20+0+6）`);

const bm = eventBonusFor(card({ id: 900001 }), evPt, 4);
ok(near(bm.bonus, 85), `当期 5★ 双匹配满星光 = ${bm.bonus}%（应 85 = 65+20）`);
ok(bm.member === true, '当期卡 member 标记为 true');

const bAttr = eventBonusFor(card({ id: 1, ch: 2 }), evPt, 0);
ok(near(bAttr.bonus, 10), `只匹配属性 = ${bAttr.bonus}%（应 10）`);

const bChar = eventBonusFor(card({ id: 1, a: 'happy' }), evPt, 0);
ok(near(bChar.bonus, 20), `只匹配角色 = ${bChar.bonus}%（应 20）`);

const bMemOff = eventBonusFor(card({ id: 900001, ch: 2, a: 'happy' }), evPt, 0);
ok(near(bMemOff.bonus, 20), `当期卡但属性/角色都不符 = ${bMemOff.bonus}%（应 20）`);

// 作用域：点数型只填 point，综合力型只填 parameter
ok(b5.point === 65 && b5.parameter === 0, '点数型活动：加成只进 point');
const evParam = mkEv({ ty: 'versus', ab: { pointPercent: 0, parameterPercent: 20 } });
const bP = eventBonusFor(c5, evParam, 4);
ok(bP.point === 0 && bP.parameter === 65, '综合力型活动：加成只进 parameter');
ok(eventChannel(evParam) === 'param' && eventChannel(evPt) === 'pt', 'eventChannel 分类正确');

// 双匹配的 20% 取自 pointPercent || parameterPercent（综合力型活动里 pointPercent 是 0）
const bDbl = eventBonusFor(card({ id: 1 }), evParam, 0);
ok(near(bDbl.bonus, 50), `综合力型活动双匹配 = ${bDbl.bonus}%（10+20+20，不能因为 pointPercent=0 就丢掉）`);

// ---------------------------------------------------------------------------
// 2. 星光三围
// ---------------------------------------------------------------------------
console.log('\n[2] 星光三围（每级每围 +50 × 星级）');
ok(starStat({ r: 5, master: 4 }) === 1000, '5★ 星光 4 → 每围 +1000（共 +3000）');
ok(starStat({ r: 4, master: 4 }) === 800, '4★ 星光 4 → 每围 +800（共 +2400）');
ok(starStat({ r: 3, master: 4 }) === 600, '3★ 星光 4 → 每围 +600');
ok(starStat({ r: 5, master: 0 }) === 0, '星光 0 → 0');

// ---------------------------------------------------------------------------
// 3. 综合力：加算而不是连乘
// ---------------------------------------------------------------------------
console.log('\n[3] 综合力结构');

const flat = (n, o = {}) => Array.from({ length: 5 }, (_, i) => ({
  id: 100 + i, ch: i + 1, a: 'cool', r: 5, sx: [10000, 10000, 10000], ep: [0, 0, 0], master: 0, ...o,
}));

// 无活动：卡面 5×30000 = 150000，道具 60% → +90000
const area = { band: 0.35, bandBand: undefined, attr: 0.20, attrAttr: undefined, magazine: 0.05 };
const p0 = totalPower(flat(), { area });
ok(p0 === 240000, `纯卡面+道具 = ${p0}（应 240000 = 150000×1.6）`);

// 加活动 50%（综合力型）：应为 150000 + 90000 + 75000 = 315000
// 连乘会得到 150000×1.6×1.5 = 360000，差 14%
// 注意：这里要让 5 张卡**全部双匹配**（角色 1~5 都在名单里），否则只有 1 张吃满 50%
const ALL_CH = [1, 2, 3, 4, 5].map((c) => ({ c, p: 20 }));
const evAdd = mkEv({
  ty: 'versus', ab: { pointPercent: 0, parameterPercent: 20 }, ch: ALL_CH, mb: [], mp: 0,
});
const pAdd = totalPower(flat(), { area, event: evAdd });
ok(pAdd === 315000, `卡面+道具+活动 = ${pAdd}（加算 315000，连乘会是 360000）`);
ok(pAdd !== 360000, '确认不是连乘');

// 角色评级乘在卡面上，会被道具和活动一起放大。
// ⚠️ 潜能解放（5%）和角色任务（6%）是**分别向下取整**的，所以测试要给整数百分比，
// 否则 floor 会吃掉几个点（比如 10% 拆成 4.545%+5.455%，每围各丢 1）。
const pRank = totalPower(flat(), {
  area: { band: 0, attr: 0, magazine: 0 },
  charPotential: 0.05,
  charMission: 0.05,
});
ok(pRank === 165000, `角色评级 10%（5%+5%）→ ${pRank}（应 150000×1.1）`);

// 潜能 5% + 任务 6% 时，三围各取整一次：10000 → 500 + 600 = 1100，不是 10000×11%=1100
const pRank11 = totalPower(flat(), {
  area: { band: 0, attr: 0, magazine: 0 },
  charPotential: 0.05,
  charMission: 0.06,
});
ok(pRank11 === 166500, `角色评级 11%（5%+6%）→ ${pRank11}（应 150000×1.11）`);

// 参数加成：双匹配的卡，指定那一围再 +50%
const evCp = mkEv({
  ty: 'versus',
  ab: { pointPercent: 0, parameterPercent: 20 },
  ch: ALL_CH, mb: [], mp: 0,
  cp: { performance: 50, technique: 0, visual: 0 },
});
// 每卡：panel 30000，evPct 0.5 → 表演 1.0、技巧/视觉 0.5
//      = 10000×2 + 10000×1.5 + 10000×1.5 = 50000 → 5 卡 250000
const pCp = totalPower(flat(), { area: { band: 0, attr: 0, magazine: 0 }, event: evCp });
ok(pCp === 250000, `参数加成（表演 +50%）= ${pCp}（应 250000）`);

// 参数加成只在综合力型活动生效（点数型活动里 cp 不该动综合力）
const evCpPt = { ...evCp, ty: 'mission_live', ab: { pointPercent: 20, parameterPercent: 0 } };
const pCpPt = totalPower(flat(), { area: { band: 0, attr: 0, magazine: 0 }, event: evCpPt });
ok(pCpPt === 150000, `点数型活动不进综合力 = ${pCpPt}（应 150000）`);

// ---------------------------------------------------------------------------
// 4. 技能发动次数
// ---------------------------------------------------------------------------
console.log('\n[4] 技能发动次数（所有模式都是 6 次）');
for (const ty of ['mission_live', 'live_try', 'versus', 'medley', 'challenge', 'festival']) {
  ok(skillSlots({ ty }) === 6, `${ty} = 6 次`);
}

// ---------------------------------------------------------------------------
// 5. 活动点数公式
// ---------------------------------------------------------------------------
console.log('\n[5] 活动点数');

const V2 = 1792990800;   // 2026 年，走 V2
const V1 = 1600000000;   // 2020 年，走 V1
ok(formulaVersion({ s: V2 }) === 2 && formulaVersion({ s: V1 }) === 1, '公式版本按开始时间判定');

// 任务 V2：base = 120 + floor(200万/15000) = 253，加成 325% → ×4.25
const ptM = eventPoints(2000000, { ty: 'mission_live', s: V2 }, { bonusSum: 325, flame: 1 });
ok(ptM === Math.floor(Math.floor(120 + 133) * 4.25) * 5 || ptM === Math.floor(Math.floor(120 + 133) * 4.25) * 5,
  `任务 V2 点数 = ${ptM}（base 253 × 4.25 × 5）`);
ok(ptM === 5375, `任务 V2 = ${ptM}（应 5375）`);

// 加成必须是**合计**：用平均（65）会低 5 倍左右
const ptAvg = eventPoints(2000000, { ty: 'mission_live', s: V2 }, { bonusSum: 65, flame: 1 });
ok(ptM > ptAvg * 2.5, `加成用合计 ${ptM} 明显高于用平均 ${ptAvg}（4.25 / 1.65 ≈ 2.6 倍）`);

// 传统：base = 50 + 200 = 250，无加成
const ptS = eventPoints(2000000, { ty: 'story', s: V2 }, { bonusSum: 0, flame: 1 });
ok(ptS === 1250, `传统 V2 = ${ptS}（应 (50+200)×5）`);

// 竞演：floor(200万/6500)=307 + 200（第 1 名）= 507，×5，且不吃加成
const ptV0 = eventPoints(2000000, { ty: 'versus', s: V2 }, { bonusSum: 0, flame: 1 });
const ptV325 = eventPoints(2000000, { ty: 'versus', s: V2 }, { bonusSum: 325, flame: 1 });
ok(ptV0 === 2535, `竞演 V2 = ${ptV0}（应 (307+200)×5）`);
ok(ptV0 === ptV325, '竞演点数不受加成影响（加成已进综合力，不能算两遍）');

// 竞演名次：第 5 名更低
const ptV5 = eventPoints(2000000, { ty: 'versus', s: V2 }, { flame: 1, rank: 4 });
ok(ptV5 === Math.floor((307 + 100) * 5), `竞演第 5 名 = ${ptV5}`);

// FES：307 + 50 + 125（胜）+ 125（第 1 名）= 607
const ptF = eventPoints(2000000, { ty: 'festival', s: V2 }, { flame: 1, win: true, rank: 0 });
ok(ptF === 3035, `FES V2 胜 = ${ptF}（应 (307+50+125+125)×5）`);

// 挑战：floor(200万/450)=4444 + 3250 = 7694
const ptC = eventPoints(2000000, { ty: 'challenge', s: V2 }, { flame: 1 });
ok(ptC === 7694, `挑战 V2 = ${ptC}（应 4444+3250）`);

// V1 的分段软上限：分数越高，边际点数越低
const v1a = eventPoints(1000000, { ty: 'mission_live', s: V1 }, { bonusSum: 0, flame: 1 });
const v1b = eventPoints(2000000, { ty: 'mission_live', s: V1 }, { bonusSum: 0, flame: 1 });
ok(v1b > v1a && (v1b - v1a) < (v1a - 200), `V1 软上限生效（100万→${v1a}，200万→${v1b}）`);

// 组曲：走社区口径，按曲数给固定加成
const ptMed3 = eventPoints(2000000, { ty: 'medley', s: V2 }, { bonusSum: 0, medleySongs: 3 });
const ptMed1 = eventPoints(2000000, { ty: 'medley', s: V2 }, { bonusSum: 0, medleySongs: 1 });
ok(ptMed3 > ptMed1, `组曲 3 首 ${ptMed3} > 1 首 ${ptMed1}`);

// ---------------------------------------------------------------------------
// 6. 端到端
// ---------------------------------------------------------------------------
const evId = process.argv[2] || Object.keys(EVENTS).sort((a, b) => Number(b) - Number(a))[0];
const ev = EVENTS[evId];
console.log(`\n[6] 端到端：活动 ${evId} ${ev.n}（${ev.ty}，作用域 ${eventChannel(ev)}）`);

// 真实数据里也要有当期卡能命中
if ((ev.mb || []).length) {
  const anyMember = (ev.mb || []).map((id) => ({ id, ...TEAM.cards[String(id)] })).find((c) => c.r);
  if (anyMember) {
    const bb = eventBonusFor(anyMember, ev, 0);
    ok(bb.member === true && bb.bonus >= (ev.mp || 0),
      `真实当期卡 ${anyMember.id} → ${bb.bonus}%（含成员 ${bb.parts.member}%）`);
  }
}

const songId = Object.keys(SONGS).find((k) => SONGS[k].lv?.expert >= 26) || Object.keys(SONGS)[0];
const chart = D(`charts/expert/${songId}.json`);
const parsed = parseChart(chart);
const songLevel = SONGS[songId].lv.expert;
console.log(`  ${SONGS[songId].t}（EXPERT Lv.${songLevel}, ${parsed.notes.length} notes）`);

const pool = Object.entries(TEAM.cards)
  .map(([id, c]) => ({ id: Number(id), ...c }))
  .filter((c) => c.r >= 4 && c.sx);

const ctx = {
  skills: TEAM.skills,
  level: 5,
  songLevel,
  area: { band: 0, attr: 0, magazine: 0 },
  charRank: 0.11,
  event: ev,
  flame: 1,
};

const t0 = Date.now();
const res = await bestTeams(pool, parsed, ctx, { topN: 5, characters: TEAM.characters });
const dt = Date.now() - t0;
console.log(`  卡池 ${pool.length} 张，枚举耗时 ${dt} ms`);

for (const r of res.slice(0, 3)) {
  const names = r.team.map((c) => `${c.id}(${c.r}★${c.a.slice(0, 2)})`).join(' ');
  console.log(`   加成 ${r.bonusSum}% | 综合力 ${r.power} | 分数 ${r.score} | PT ${r.points} | ${names}`);
}

const top = res[0];
ok(top.bonusSum >= 150, `最优队活动加成合计 ${top.bonusSum}%（应显著高于 0）`);
ok(res.every((r) => new Set(r.team.map((c) => c.ch)).size === 5), 'Top5 全部满足「同角色唯一」');
ok(res.every((r, i) => i === 0 || res[i - 1].points >= r.points), '结果按点数降序');

// 综合力最高的 5 张做对照
const byPower = [...pool].sort((a, b) =>
  (b.sx[0] + b.sx[1] + b.sx[2]) - (a.sx[0] + a.sx[1] + a.sx[2])).slice(0, 5);
const pw = scoreTeam(byPower, parsed, ctx);
const pwBonus = byPower.reduce((a, c) => a + eventBonusFor(c, ev, 0).bonus, 0);
const pwPts = eventPoints(pw.score, ev, { bonusSum: pwBonus, flame: 1 });
console.log(`  「纯综合力最高 5 张」：综合力 ${pw.power}，加成 ${pwBonus.toFixed(1)}%，PT ${pwPts}`);
console.log(`  「算法最优 5 张」：综合力 ${top.power}，加成 ${top.bonusSum}%，PT ${top.points}`);
ok(top.points >= pwPts, '算法推荐的队点数不低于「纯综合力最高」的队');

console.log(failed ? `\n✗ ${failed} 项未通过` : '\n✓ 全部通过');
process.exit(failed ? 1 : 0);
