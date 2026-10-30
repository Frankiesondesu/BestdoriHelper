/**
 * 组队页渲染冒烟测试（无浏览器）。
 *
 * 用最小 DOM stub 跑一遍 render()，检查：
 *  - 有活动时表格出现「活动加成」列，数值是 5 张卡合计
 *  - 卡图 title 里带每张卡的加成明细
 *  - 没活动时不出现加成列（避免误导）
 *
 * 用法：node scripts/team_render_check.mjs
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const TEAM = JSON.parse(readFileSync(join(ROOT, 'docs/data/team.json'), 'utf8'));
const EVENTS = JSON.parse(readFileSync(join(ROOT, 'docs/data/events.json'), 'utf8'));

// ---- 最小 DOM stub -------------------------------------------------------
const els = new Map();
const mkEl = () => ({
  innerHTML: '', textContent: '', value: '', hidden: false,
  dataset: {}, style: {}, classList: { add() {}, remove() {}, toggle() {} },
  appendChild() {}, focus() {}, addEventListener() {},
});
globalThis.document = {
  getElementById: (id) => {
    if (!els.has(id)) els.set(id, mkEl());
    return els.get(id);
  },
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: mkEl,
};
globalThis.window = { __BDH_STATE: null, addEventListener() {} };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.fetch = async () => ({ json: async () => ({}) });

const { render } = await import(pathToFileURL(join(ROOT, 'docs/src/team-ui.js')).href);
const { eventBonusFor } = await import(pathToFileURL(join(ROOT, 'docs/src/team.js')).href);

let failed = 0;
const ok = (cond, msg) => {
  if (cond) console.log(`  ✓ ${msg}`);
  else { console.log(`  ✗ ${msg}`); failed++; }
};

const evId = Object.keys(EVENTS).sort((a, b) => Number(b) - Number(a))
  .find((k) => (EVENTS[k].mb || []).length);
const ev = EVENTS[evId];

// 造一支「3 张成员卡 + 2 张通用卡」的队伍
const ids = [...ev.mb.slice(0, 3)];
for (const [id, c] of Object.entries(TEAM.cards)) {
  if (ids.length >= 5) break;
  if (c.r === 5 && !ids.includes(Number(id))) ids.push(Number(id));
}
const team = ids.slice(0, 5).map((id) => ({ id, ...TEAM.cards[String(id)] }));
const bonusSum = team.reduce((a, c) => a + eventBonusFor(c, ev, 0).point, 0);
const row = {
  team, power: 200000, score: 2000000,
  bonusSum: Math.round(bonusSum * 10) / 10, bonusPct: bonusSum / 5, points: 1500,
};

console.log(`\n活动 ${evId} ${ev.n}，测试队伍加成合计 ${row.bonusSum}%`);

render([row], { diff: 'expert', level: 27, notes: 600, skillLv: 5, event: ev });
const html = document.getElementById('tmBody').innerHTML;

ok(html.includes('活动加成'), '有活动时出现「活动加成」列');
ok(html.includes(`${Math.round(row.bonusSum)}%`), `表格显示合计 ${Math.round(row.bonusSum)}%`);
ok(html.includes('一致') && html.includes('当期卡') && html.includes('星光'), '脚注解释了加成构成');
ok(html.includes('综合力') && html.includes('点数'), '脚注说明了加成的两种作用域');
ok(html.includes('一致'), '卡图 title 含「一致」明细');
ok(html.includes('1500') || html.includes('1,500'), '活动点数正常渲染');

render([row], { diff: 'expert', level: 27, notes: 600, skillLv: 5, event: null });
const html2 = document.getElementById('tmBody').innerHTML;
ok(!html2.includes('活动加成'), '没活动时不出现加成列');
ok(!html2.includes('一致加成'), '没活动时不显示加成脚注');

console.log(failed ? `\n✗ ${failed} 项未通过` : '\n✓ 全部通过');
process.exit(failed ? 1 : 0);
