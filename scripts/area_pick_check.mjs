/**
 * 区域道具「逐槽位二选一」回归测试。
 *
 * 规则（Frankieson 2026-10-30 确认）：
 *   同一件乐器只能装一件 —— 装了「大家的键盘」就不能再有咲的键盘；
 *   但「大家的键盘 + 多惠的吉他」可以共存（不同槽位、不同乐器）。
 *
 * 所以专有套和共通套不是相加，也不是整套二选一，而是每个槽独立取优。
 *
 * 跑法：node scripts/area_pick_check.mjs
 */
import { readFileSync } from 'node:fs';
import { pickAreaItems, totalPower, AREA_ITEM_CURVE, AREA_ITEM_SLOTS } from '../docs/src/team.js';

let pass = 0;
let fail = 0;

function ok(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  ✓ ${name}${detail ? '  ' + detail : ''}`); }
  else { fail++; console.log(`  ✗ ${name}${detail ? '  ' + detail : ''}`); }
}

/** 造一张卡：band = 乐队名，sx = 三围 */
const card = (band, sx = 30000) => ({ ch: 1, a: 'cool', band, sx: [sx, sx, sx] });

/** 只带乐队 + Everyone 两行的道具表（前端存的是扁平数字，不是 [旧,新] 数组） */
function lvTable({ bandLv = 0, evLv = 0, bandSlot = 'PoppinParty' } = {}) {
  return { [bandSlot]: bandLv, Everyone: evLv };
}

const pct = (x) => `${(x * 100).toFixed(2)}%`;

console.log('\n[1] 五张同乐队、专有满级 → 53.5%，共通一件不装');
{
  const team = Array.from({ length: 5 }, () => card("Poppin'Party"));
  const r = pickAreaItems(team, lvTable({ bandLv: 8, evLv: 8 }), null, null);
  const expect = 5 * AREA_ITEM_CURVE.band(8) + 2 * AREA_ITEM_CURVE.bandPoster(8);
  ok('band = 53.5%', Math.abs(r.area.band - expect / 100) < 1e-9, pct(r.area.band));
  ok('everyone = 0', r.area.everyone === 0, pct(r.area.everyone));
  ok('bandBand 有值', r.area.bandBand === "Poppin'Party", String(r.area.bandBand));
}

console.log('\n[2] 只有 1 张本乐队卡、共通满级 → 共通全胜');
{
  const team = [card("Poppin'Party"), ...Array.from({ length: 4 }, () => card('Roselia'))];
  const r = pickAreaItems(team, lvTable({ bandLv: 8, evLv: 8 }), null, null);
  // 专有单槽收益 = 1 人卡面 × 5.5%，共通 = 5 人卡面 × 2.75%
  ok('band = 0', r.area.band === 0, pct(r.area.band));
  const expectEv = 5 * AREA_ITEM_CURVE.everyone(8) + 2 * AREA_ITEM_CURVE.everyonePoster(8);
  ok('everyone = 26.75%', Math.abs(r.area.everyone - expectEv / 100) < 1e-9, pct(r.area.everyone));
  ok('bandBand 为空', r.area.bandBand === undefined);
}

console.log('\n[3] 专有 Lv1、共通 Lv8 → 共通更好（专有等级低时会被反超）');
{
  const team = Array.from({ length: 5 }, () => card("Poppin'Party"));
  const r = pickAreaItems(team, lvTable({ bandLv: 1, evLv: 8 }), null, null);
  // 专有单槽 2.0%，共通 2.75%；5 人同队 → 共通赢
  ok('everyone > 0', r.area.everyone > 0, pct(r.area.everyone));
  ok('band = 0', r.area.band === 0, pct(r.area.band));
}

console.log('\n[4] 专有 Lv8、共通 Lv1 → 专有全胜');
{
  const team = Array.from({ length: 5 }, () => card("Poppin'Party"));
  const r = pickAreaItems(team, lvTable({ bandLv: 8, evLv: 1 }), null, null);
  ok('band = 53.5%', Math.abs(r.area.band - 0.535) < 1e-9, pct(r.area.band));
  ok('everyone = 0', r.area.everyone === 0, pct(r.area.everyone));
}

console.log('\n[5] 绝不出现「两套相加」的 75%+');
{
  const team = Array.from({ length: 5 }, () => card("Poppin'Party"));
  const r = pickAreaItems(team, lvTable({ bandLv: 8, evLv: 8 }), null, null);
  const total = r.area.band + r.area.everyone;
  ok('总加成 ≤ 53.5%', total <= 0.535 + 1e-9, pct(total));
  // 旧的错误实现会得到 53.5 + 26.75 = 80.25%
  ok('不是 80.25%（旧 bug）', Math.abs(total - 0.8025) > 1e-6, pct(total));
}

console.log('\n[6] 混装：乐器走共通、海报走专有（同一队里两套并存）');
{
  // 专有 Lv1、共通 Lv4，4/5 张是本乐队（卡面占比 r = 0.8）：
  //   乐器：专有 0.8×2.00 = 1.60  vs 共通 0.75+0.25×4 = 1.75 → 共通
  //   海报：专有 0.8×6.00 = 4.80  vs 共通 2.50+0.50×4 = 4.50 → 专有
  // 这正好卡在两者的交叉区间，是唯一能同时出现两种来源的形态。
  const team = [card("Poppin'Party"), card("Poppin'Party"), card("Poppin'Party"),
    card("Poppin'Party"), card('Roselia')];
  const r = pickAreaItems(team, lvTable({ bandLv: 1, evLv: 4 }), null, null);
  // 海报专有 2 × 6 = 12%
  ok('band = 12%（2 件海报）', Math.abs(r.area.band - 0.12) < 1e-9, pct(r.area.band));
  // 乐器共通 5 × 1.75 = 8.75%
  ok('everyone = 8.75%（5 件乐器）', Math.abs(r.area.everyone - 0.0875) < 1e-9, pct(r.area.everyone));
  ok('两套同时非零', r.area.band > 0 && r.area.everyone > 0);
  ok('合计 20.75%', Math.abs(r.area.band + r.area.everyone - 0.2075) < 1e-9,
    pct(r.area.band + r.area.everyone));
}

console.log('\n[7] 综合力落地：5 张 PPP + PPP Lv8 → 加成正好 53.5%');
{
  const team = Array.from({ length: 5 }, () => card("Poppin'Party", 10000));
  const r = pickAreaItems(team, lvTable({ bandLv: 8, evLv: 8 }), null, null);
  const p = totalPower(team, { area: r.area });
  // 裸队 5 × 30000 = 150000，加成 53.5% → 150000 × 1.535 = 230250
  ok('综合力 = 230,250', p === 230250, String(p));
}

console.log('\n[8] 道具等级全 0 → 加成为 0');
{
  const team = Array.from({ length: 5 }, () => card("Poppin'Party"));
  const r = pickAreaItems(team, lvTable({ bandLv: 0, evLv: 0 }), null, null);
  ok('band = 0', r.area.band === 0);
  ok('everyone = 0', r.area.everyone === 0);
}

console.log('\n[9] 真实档案（docs/data/my-items.json）：PPP Lv8 / 共通 Lv6');
{
  const raw = JSON.parse(readFileSync(new URL('../docs/data/my-items.json', import.meta.url), 'utf8'));
  // 档案里是 [旧, 新] 数组，前端导入时会取 v[1]，这里照做
  const levels = {};
  for (const [k, v] of Object.entries(raw.items || {})) {
    levels[k] = Array.isArray(v) ? Number(v[1] ?? v[0]) || 0 : Number(v) || 0;
  }
  const team = Array.from({ length: 5 }, () => card("Poppin'Party", 20000));
  const r = pickAreaItems(team, levels, null, raw.detail);
  // PPP Lv8 专有全面压过 Everyone Lv6，共通道具一件都不该装
  ok('band = 53.5%', Math.abs(r.area.band - 0.535) < 1e-9, pct(r.area.band));
  ok('everyone = 0', r.area.everyone === 0, pct(r.area.everyone));
  const total = r.area.band + r.area.everyone;
  ok('不是旧 bug 的 75.75%', Math.abs(total - 0.7575) > 1e-6, pct(total));
  // 旧实现把两套相加（53.5 + 22.25 = 75.75%），量化它高估了多少综合力
  const pNew = totalPower(team, { area: r.area });
  const pOld = totalPower(team, { area: { ...r.area, band: 0.7575, everyone: 0 } });
  console.log(`    综合力：新 ${pNew.toLocaleString()} / 旧(相加) ${pOld.toLocaleString()}` +
    `　旧模型高估 ${((pOld / pNew - 1) * 100).toFixed(1)}%`);
  console.log(`    道具等级 ${JSON.stringify(levels)}`);
}

console.log(`\n${'─'.repeat(40)}`);
console.log(fail === 0 ? `✓ 全部通过（${pass} 项）` : `✗ ${fail} 项失败 / 共 ${pass + fail} 项`);
process.exit(fail === 0 ? 0 : 1);
