/**
 * 用真实结算数据检验分数模型 —— 不标定常数，只做「上界断言」。
 *
 * ## 为什么不是「拿一局反推系数」
 *
 * 一局结算里同时混着三个变量：判定准度、断连位置、技能覆盖率。拿一个数去反推
 * 一个常数，等于把这三样噪声全吸进常数里 —— 拟合出来的东西没有物理意义，
 * 换一局就变。这正是上一轮 `SCORE_SCALE = 1.45` 出错的根源。
 *
 * ## 正确的判据：上界
 *
 * 本模型算的是 **「全 PERFECT + 全程不断连 + 最优技能顺序」** 的分数。
 * 任何真实演奏都只会更低（判定倍率 ≤ 1.1、连击系数在断连后重置、技能顺序取遍
 * 120 种的最优）。所以：
 *
 *     实测分 ≤ 理论分      ← 必须成立，且**与准度、断连、角色评级无关**
 *
 * 违反这条 → 模型一定有 bug（低估了某个加成，或公式抄错）。
 * 满足这条 → 模型没问题，偏差就是「这一局的手感」，不是模型误差。
 *
 * 角色评级为什么不影响：它只决定综合力，而综合力在结算界面**直接显示**。
 * 所以拿显示的综合力当输入，评级就被消掉了。
 *
 * ## 用法
 *
 * ```bash
 * # 一局
 * node scripts/score_bound.mjs --song 1 --diff expert \
 *   --team 2430,2431,2434,2032,1867 --power 196413 --score 1180000
 *
 * # 多局（推荐：单局噪声大，看分布）
 * node scripts/score_bound.mjs --song 1 --diff expert \
 *   --team 2430,2431,2434,2032,1867 --score 1180000 --score 1192000 --score 1175000
 * ```
 *
 * `--power` 省略时用队伍自己算出的综合力。
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { scoreTeam, parseChart, JUDGE } from '../docs/src/team.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => JSON.parse(readFileSync(join(ROOT, p), 'utf8'));

const TEAM = read('docs/data/team.json');
const SONGS = read('docs/data/songs.json');

// ---- 参数 ----------------------------------------------------------------
const argv = process.argv.slice(2);
const arg = (k, d = null) => {
  const i = argv.indexOf(`--${k}`);
  return i >= 0 ? argv[i + 1] : d;
};
const argsAll = (k) => {
  const out = [];
  argv.forEach((a, i) => { if (a === `--${k}`) out.push(argv[i + 1]); });
  return out;
};

const songId = arg('song');
const diff = arg('diff', 'expert');
const teamIds = (arg('team') || '').split(',').map((x) => x.trim()).filter(Boolean);
const powerArg = arg('power') != null ? Number(arg('power')) : null;
const skillLevel = Number(arg('level', 5));
const observed = argsAll('score').map(Number).filter((x) => Number.isFinite(x) && x > 0);

if (!songId) {
  console.error('缺少 --song（歌曲 ID，见 docs/data/songs.json）');
  process.exit(2);
}

// ---- 载入谱面 ------------------------------------------------------------
let chart;
try {
  chart = read(`docs/data/charts/${diff}/${songId}.json`);
} catch {
  console.error(`找不到谱面 docs/data/charts/${diff}/${songId}.json`);
  process.exit(2);
}
const parsed = parseChart(chart);
const N = parsed.notes.length;
const songLevel = SONGS[songId]?.lv?.[diff] ?? 26;
const songName = SONGS[songId]?.t || `#${songId}`;

// ---- 队伍 ------------------------------------------------------------------
if (!teamIds.length) {
  console.error('缺少 --team（5 张卡 ID，逗号分隔）。技能覆盖率必须按真实队伍算，');
  console.error('否则「上界」不成立 —— 随便假设一个技能倍率，算出来的东西不是上界。');
  process.exit(2);
}
if (teamIds.length !== 5) {
  console.error(`--team 需要正好 5 张卡，收到 ${teamIds.length} 张`);
  process.exit(2);
}
const team = teamIds.map((id) => {
  const c = TEAM.cards[id];
  if (!c) { console.error(`卡 ${id} 不存在`); process.exit(2); }
  return { ...c, id: Number(id) };
});

// ---- 理论上界 --------------------------------------------------------------
// optimizeOrder: 枚举 120 种技能顺序取最优 —— 真实演奏不可能比这更高，
// 所以这才是严格的上界。默认的「升序」只是启发式，不能当上界用。
const bound = scoreTeam(team, parsed, {
  songLevel,
  skills: TEAM.skills,
  level: skillLevel,
  characters: TEAM.characters,
  optimizeOrder: true,
});

const power = powerArg ?? bound.power;
// 分数对综合力是**严格线性**的（每 NOTE 基础分 = 综合力 × 系数），
// 所以换用结算界面显示的综合力时，等比缩放即可，不必重算技能。
const boundAtPower = bound.score * (power / bound.power);

const fmt = (x) => Math.round(x).toLocaleString('en-US');

console.log(`谱面  ${songName}  ${diff.toUpperCase()} Lv${songLevel}  ${N} notes  ${parsed.duration.toFixed(1)}s`);
console.log(`队伍  综合力 ${fmt(power)}${powerArg != null ? '（结算界面）' : '（模型计算）'}`);
console.log(`理论上界（全 P + 全连 + 最优技能序）  ${fmt(boundAtPower)}`);
console.log('');

if (!observed.length) {
  console.log('没给 --score，只输出上界。');
  console.log('把你的结算分数加上（可给多个），会算出比值和等效准度。');
  process.exit(0);
}

// ---- 逐局对比 --------------------------------------------------------------
const ratios = observed.map((s) => s / boundAtPower);
const over = observed.filter((s) => s > boundAtPower * 1.0005);   // 0.05% 容差

console.log('实测分数        比值(实测/上界)   等效 P 率');
const GREAT_R = JUDGE.great / JUDGE.perfect;   // 0.8 / 1.1
observed.forEach((s, i) => {
  const r = ratios[i];
  // r = p + (1-p)·GREAT_R  →  p = (r - GREAT_R) / (1 - GREAT_R)
  // 「其余按 GREAT 算」是乐观估计：真实演奏里还有 GOOD（0.5）和断连，
  // 所以算出来的 p 是**上界**，实际准度只会更低。
  const p = (r - GREAT_R) / (1 - GREAT_R);
  const pTxt = p >= 0 ? `${(p * 100).toFixed(1)}%` : '—';
  console.log(`${fmt(s).padStart(12)}   ${r.toFixed(4)}            ${pTxt.padStart(6)}`);
});

const mean = ratios.reduce((a, b) => a + b, 0) / ratios.length;
console.log('');
console.log(`均值比值 ${mean.toFixed(4)}   最低 ${Math.min(...ratios).toFixed(4)}   最高 ${Math.max(...ratios).toFixed(4)}`);

// 常见准度对应的理论比值（P 率 p，其余按 GREAT 算）
const ref = [1, 0.99, 0.98, 0.95, 0.9, 0.8];
console.log('\n参考：不同 Perfect 率对应的比值（其余按 GREAT）');
for (const p of ref) {
  const r = p + (1 - p) * (JUDGE.great / JUDGE.perfect);
  console.log(`  P ${(p * 100).toFixed(0).padStart(3)}%  →  ${r.toFixed(4)}`);
}

console.log('');
if (over.length) {
  console.log(`✗ ${over.length}/${observed.length} 局**超过理论上界**`);
  console.log('  这说明模型低估了某个加成 —— 是 bug，不是「打得准」。');
  console.log('  先查：加成作用域、道具件数、特训固定值、技能次数。');
  process.exitCode = 1;
} else {
  console.log(`✓ ${observed.length} 局全部 ≤ 理论上界 —— 模型没有被证伪`);
  const meanP = (mean - GREAT_R) / (1 - GREAT_R);
  console.log(`  均值比值 ${mean.toFixed(4)} 相当于「约 ${(meanP * 100).toFixed(1)}% P + 其余全 GREAT」`);
  console.log('  对照上面的参考表，看是否和你对自己的准度估计吻合。');
  console.log('  若吻合 → 模型没问题，剩下的差距是你的失误量，不是公式误差。');
}
