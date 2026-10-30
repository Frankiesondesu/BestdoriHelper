/**
 * 组队助手 · 出分计算引擎
 *
 * 目标：按游戏《BanG Dream! 少女乐团派对》的实际计分规律，算出某支队伍在
 * 某首歌上的**预计分数**，并在一组候选卡里挑出最优的 5 张。
 *
 * ## 计分公式（来自游戏实测与社区逆向，多个来源一致）
 *
 * ```
 * 总基本分 = 总合力 × (1 + Lv加成) × 300%
 * Lv加成   = (歌曲等级 - 5) × 1%
 * 每 NOTE 分 = 总基本分 / NOTE 数
 * NOTE 实得分 = 每 NOTE 分 × 判定倍率 × (1 + COMBO 加成) × (1 + 技能加成)
 * 判定：PERFECT 110% / GREAT 80% / GOOD 50% / BAD 及以下 0%（滑条中继点另计）
 * COMBO：0~20 0%、21~50 1%、51~100 2%、101~150 3%、…、701+ 11%
 * ```
 *
 * ## 技能怎么作用
 *
 * 一首歌里技能共发动 **6 次**：前 5 次是五名队员，第 6 次是**返场**（队长/C 位
 * 再发一次）。技能覆盖到的 NOTE 才吃加成。本引擎把 6 个发动点按 NOTE 数均分，
 * 每次从该点对应的**时间**起持续 `duration` 秒（duration 随技能等级 5→7 秒）。
 *
 * 前 5 次的**顺序是随机的**，但固定卡组下总存在一个最优顺序。引擎会枚举
 * 最多 120 种排列取最高分 —— 这正是"最佳技能顺序"的实际含义。
 */

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

/** 判定倍率（新版本实测值） */
export const JUDGE = { perfect: 1.10, great: 0.80, good: 0.50, bad: 0 };

/** 连击数下限 -> 加成。分段累加，不是只取最高段。 */
export const COMBO_TABLE = [
  [0, 0.00], [21, 0.01], [51, 0.02], [101, 0.03], [151, 0.04],
  [201, 0.05], [251, 0.06], [301, 0.07], [401, 0.08], [501, 0.09],
  [601, 0.10], [701, 0.11],
];

/** 六次技能发动 */
const SKILL_SLOTS = 6;

/**
 * 分数校准系数。
 *
 * 社区公式写的是「总基本分 = 总合力 × (1+Lv加成) × 300%」，但那是 2017 年
 * 的版本。用 2024+ 的实测样本反推：顶配 EX 组曲队（综合力约 30 万、Lv27）
 * 打出的单曲分数是 **257 万 / 268 万 / 304 万**。
 *
 *   30 万 × 1.22(Lv27) × 3 = 109.8 万，再乘判定/COMBO/技能 ≈ 133 万
 *   实测 260~300 万  ->  差约 2.1 倍
 *
 * 所以这里乘 2.1 做绝对校准。**队伍之间的相对排序不受影响**（这是同一个
 * 常数），对"选哪 5 张卡"的判断没有副作用。
 */
const SCORE_SCALE = 2.1;

/** 枚举上限：参与精确组合的候选卡数量（C(24,5) ≈ 4.2 万，浏览器端够快） */
//. 候选池上限。**这个数字直接决定耗时**：
// C(n,5) × 5 个 C 位 × 120 种技能顺序。
//   n=24 -> 2550 万次，浏览器直接卡死（实测）
//   n=12 -> 47.5 万次，约 1 秒
// 三围是分数的主项，取前 12 基本不会漏掉真正的最优解。
const POOL_LIMIT = 12;
/** 活动加成卡额外纳入的上限（按加成降序取） */
const BONUS_LIMIT = 12;

// ---------------------------------------------------------------------------
// 综合力
// ---------------------------------------------------------------------------

/**
 * 队伍综合力。
 *
 * ```
 * 乐队参数 = (五人满级三围 + 剧情加成) × (1 + 角色评级加成)
 * 综合力   = 乐队参数 + 区域道具奖励
 * ```
 * 注意最后是**加算**：区域道具不是乘在乐队参数上的。
 *
 * @param {object[]} team 5 张卡（需含 `sx` 满级三围、`ep` 剧情加成）
 * @param {object} opts
 * @param {number} [opts.charRank=0]    角色评级加成，0.11 表示 11%
 * @param {number} [opts.areaBonus=0]   区域道具加成（占乐队参数的百分比），0.35 表示 35%
 * @param {string} [opts.statType]      按哪一项算（performance/technique/visual），
 *                                      不传则取三项之和
 */
export function totalPower(team, opts = {}) {
  const charRank = opts.charRank ?? 0;
  const chars = opts.characters || null;   // {角色ID: {n, b}}，用来补卡对象缺的乐队
  // 角色潜力（来自 Bestdori 档案的 items.potentials）：{角色ID: 值}
  // 有的话按角色分别加成，否则退回统一 charRank。
  const potentials = opts.potentials || null;
  const area = opts.area || { band: 0, attr: 0, magazine: 0 };
  const ev = opts.event || null;      // 活动加成（可为 null）

  let base = 0;
  for (const c of team) {
    const s = c.sx || [0, 0, 0];
    const e = c.ep || [0, 0, 0];

    // ① 区域道具：按「这张卡」匹配的乐队/属性道具 + 杂志（杂志全队生效）
    let areaBonus = 0;
    if (area.bandBand === undefined || area.bandBand === c.band) {
      areaBonus += (area.band || 0);
    }
    if (!area.attrAttr || area.attrAttr === c.a) areaBonus += (area.attr || 0);
    areaBonus += (area.magazine || 0);
    // 兼容旧的单一百分比写法
    if (typeof opts.areaBonus === 'number') areaBonus = opts.areaBonus;

    // ② 活动：加成角色的三围额外百分比（performance / technique / visual）
    let pMul = 0, tMul = 0, vMul = 0;
    if (ev && ev.cp) {
      pMul = (ev.cp.performance || 0) / 100;
      tMul = (ev.cp.technique || 0) / 100;
      vMul = (ev.cp.visual || 0) / 100;
    }
    const sum =
      (s[0] + e[0]) * (1 + areaBonus) * (1 + pMul) +
      (s[1] + e[1]) * (1 + areaBonus) * (1 + tMul) +
      (s[2] + e[2]) * (1 + areaBonus) * (1 + vMul);
    base += sum;
  }
  // 角色加成：逐卡取该角色的潜能。
  // 游戏机制：潜能每升 1 级，**对应那一项三围 +0.1%**（P/T/V 分开）。
  // 所以这里按维度加，不是给整队一个百分比。
  let sum = 0;
  for (const c of team) {
    const s = c.sx || [0, 0, 0];
    const e = c.ep || [0, 0, 0];
    const area = opts.area || {};
    // ⚠️ 卡对象来自 team.json，**只有 ch/r/a/sk/sx 等，没有 band 字段**。
    // 直接比较 c.band 会永远 false，导致乐队道具静默失效（实测踩过）。
    const cardBand = c.band ?? chars?.[c.ch]?.b;
    let areaBonus = 0;
    if (area.bandBand === undefined || area.bandBand === cardBand) areaBonus += (area.band || 0);
    if (!area.attrAttr || area.attrAttr === c.a) areaBonus += (area.attr || 0);
    areaBonus += (area.magazine || 0);
    if (typeof opts.areaBonus === 'number') areaBonus = opts.areaBonus;

    let pMul = 0, tMul = 0, vMul = 0;
    if (opts.event?.cp) {
      pMul = (opts.event.cp.performance || 0) / 100;
      tMul = (opts.event.cp.technique || 0) / 100;
      vMul = (opts.event.cp.visual || 0) / 100;
    }

    // 潜能：{角色: [P, T, V]}，每级 0.1%。
    // 档案里没有该角色时**按满级 20 级假设**（+2%），而不是当 0 ——
    // 实测潜能对综合力的影响只有 1~2%，全部当 0 会让结果系统性偏低。
    const pot = potentials && c.ch != null ? potentials[c.ch] : null;
    const ASSUME_POT_LEVEL = 20;      // 假设值：满级 20
    const kP = pot ? (pot[0] || 0) * 0.001 : ASSUME_POT_LEVEL * 0.001;
    const kT = pot ? (pot[1] || 0) * 0.001 : ASSUME_POT_LEVEL * 0.001;
    const kV = pot ? (pot[2] || 0) * 0.001 : ASSUME_POT_LEVEL * 0.001;

    sum += (s[0] + e[0]) * (1 + areaBonus) * (1 + pMul) * (1 + kP)
         + (s[1] + e[1]) * (1 + areaBonus) * (1 + tMul) * (1 + kT)
         + (s[2] + e[2]) * (1 + areaBonus) * (1 + vMul) * (1 + kV);
  }
  return Math.round(sum * (1 + (ev?.ab?.parameterPercent || 0) / 100));
}

/**
 * 活动加成明细。
 *
 * 不同活动类型的加成方式**真的不一样**（数据取自 Bestdori 的 events）：
 *
 * | eventType      | pointPercent | parameterPercent | 说明                    |
 * |----------------|--------------|------------------|-------------------------|
 * | mission_live   | 20           | 0                | 只影响活动点数           |
 * | live_try       | 20           | 0                | 只影响活动点数           |
 * | versus         | 0            | 20               | 只影响综合力（分数）      |
 * | medley         | 0            | 20               | 只影响综合力（分数）      |
 * | festival       | 0            | 20               | 只影响综合力（分数）      |
 * | challenge      | 20           | 20               | 两者都有                  |
 * | story          | —            | —                | 只有属性/角色加成          |
 *
 * `eventCharacterParameterBonus` 是「加成角色」的三围额外百分比（如 50）。
 * `limitBreaks` 是卡牌突破加成 —— ⚠️ 1~2 星存小数（0.2=20%），3~5 星存整数
 * （15=15%），量纲不一致，必须归一化。
 *
 * @param {object} card    卡（需含 ch / a / r）
 * @param {object} event   活动数据（events.json 的一条）
 * @param {number} [limitBreakRank=0] 这张卡的突破等级 0~4
 * @returns {{point:number, parameter:number, matched:boolean}}
 */
export function eventBonusFor(card, event, limitBreakRank = 0) {
  if (!event) return { point: 0, parameter: 0, matched: false };

  const attrHit = (event.attr || []).some((x) => x.a === card.a);
  const charHit = (event.ch || []).some((x) => x.c === card.ch);
  const attrPct = (event.attr || []).find((x) => x.a === card.a)?.p || 0;
  const charPct = (event.ch || []).find((x) => x.c === card.ch)?.p || 0;

  // 双重匹配（同时是加成属性和加成角色）才有额外加成
  const ab = event.ab || {};
  const pointDouble = attrHit && charHit ? (ab.pointPercent || 0) : 0;
  const paramDouble = attrHit && charHit ? (ab.parameterPercent || 0) : 0;

  // 突破加成：归一化（>1 视为百分数，<=1 视为小数）
  let lb = 0;
  const row = (event.lb || []).find((x) => x.r === card.r && x.k === limitBreakRank);
  if (row) lb = row.p > 1 ? row.p / 100 : row.p;

  const point = (attrHit ? attrPct : 0) + (charHit ? charPct : 0) + pointDouble + lb * 100;
  const parameter = paramDouble;
  return { point, parameter, matched: attrHit || charHit };
}

// ---------------------------------------------------------------------------
// 谱面解析
// ---------------------------------------------------------------------------

/**
 * 把 Bestdori 的谱面数组转成「按时间排序的 NOTE 列表」。
 *
 * 谱面用 `beat`（拍）而不是秒，且中途可能变速，所以要按 BPM 事件分段累加。
 * BPM / System 之类不是 NOTE，要排除；Long 只算**起始点**一次（音符本身是一个）。
 *
 * @param {object[]} chart Bestdori `/api/charts/{id}/{diff}.json` 的返回值
 * @returns {{notes: {time:number, beat:number}[], duration:number}}
 */
export function parseChart(chart) {
  // 本地预拉的谱面是「已换算好时间」的精简格式 {d, n: [秒...]}，
  // 直接返回，省掉 BPM 换算（824 首 × 5 难度都存这份）。
  if (chart && Array.isArray(chart.n)) {
    return {
      notes: chart.n.map((t) => ({ time: t, beat: 0 })),
      duration: chart.d ?? (chart.n[chart.n.length - 1] || 0),
    };
  }

  const bpmEvents = chart
    .filter((e) => e.type === 'BPM' && typeof e.bpm === 'number')
    .sort((a, b) => a.beat - b.beat);
  if (!bpmEvents.length) bpmEvents.push({ beat: 0, bpm: 120 });

  /** beat -> 秒 */
  const beatToTime = (beat) => {
    let t = 0;
    for (let i = 0; i < bpmEvents.length; i++) {
      const cur = bpmEvents[i];
      const next = bpmEvents[i + 1];
      const start = cur.beat;
      const end = next ? next.beat : beat;
      if (beat <= start) return t;
      const span = Math.min(beat, end) - start;
      if (span > 0) t += (span * 60) / cur.bpm;
      if (beat <= end) return t;
    }
    return t;
  };

  const notes = chart
    .filter((e) => e.type === 'Single' || e.type === 'Long' || e.type === 'Flick')
    .map((e) => ({ beat: e.beat, time: beatToTime(e.beat) }))
    .sort((a, b) => a.time - b.time);

  const duration = notes.length ? notes[notes.length - 1].time : 0;
  return { notes, duration };
}

// ---------------------------------------------------------------------------
// 技能时间轴
// ---------------------------------------------------------------------------

/** 技能等级（1~5）对应的持续秒数 */
function skillDuration(skill, level = 5) {
  const d = skill?.d || [5];
  const i = Math.min(Math.max(level - 1, 0), d.length - 1);
  return d[i];
}

/**
 * 算出一组技能发动顺序下，每个 NOTE 吃到的技能加成。
 *
 * @param {{notes:{time:number}[]}} parsed
 * @param {object[]} order    5 张卡，顺序即发动顺序
 * @param {object} skills     技能表 {id: {d:[], up:number}}
 * @param {number} level      技能等级 1~5
 * @returns {number[]}        每个 NOTE 的加成（0.6 表示 +60%）
 */
export function skillBonusPerNote(parsed, order, skills, level = 5) {
  const notes = parsed.notes;
  const n = notes.length;
  const bonus = new Array(n).fill(0);
  if (!n) return bonus;

  // 6 次发动：前 5 次按 order，第 6 次是返场（C 位 = order[0]）
  const slots = [];
  for (let k = 0; k < SKILL_SLOTS; k++) {
    const card = k < SKILL_SLOTS - 1 ? order[k] : order[0];
    const sk = skills[String(card.sk)] || {};
    const startIdx = Math.floor((n * k) / SKILL_SLOTS);
    const startTime = notes[Math.min(startIdx, n - 1)].time;
    slots.push({ startTime, endTime: startTime + skillDuration(sk, level),
                 up: (sk.up || 0) / 100 });
  }

  // 逐 NOTE 判断落在哪个技能里；同一次发动内不会重叠，取命中的最大值即可
  for (let i = 0; i < n; i++) {
    const t = notes[i].time;
    let best = 0;
    for (const s of slots) {
      if (t >= s.startTime && t <= s.endTime && s.up > best) best = s.up;
    }
    bonus[i] = best;
  }
  return bonus;
}

/** 前 5 次发动的全部排列（120 种） */
function permutations5(arr) {
  const out = [];
  const walk = (rest, acc) => {
    if (!rest.length) { out.push(acc); return; }
    for (let i = 0; i < rest.length; i++) {
      walk([...rest.slice(0, i), ...rest.slice(i + 1)], [...acc, rest[i]]);
    }
  };
  walk(arr, []);
  return out;
}

// ---------------------------------------------------------------------------
// 分数
// ---------------------------------------------------------------------------

/** 连击加成：按连击数查段位 */
function comboBonus(combo) {
  let b = 0;
  for (const [floor, val] of COMBO_TABLE) {
    if (combo >= floor) b = val; else break;
  }
  return b;
}

/**
 * 计算一支队伍在某张谱面上的分数。
 *
 * @param {object[]} team   5 张卡，`team[0]` 为队长（C 位）
 * @param {object}   parsed `parseChart` 的结果
 * @param {object}   ctx    {skills, level, songLevel, charRank, areaBonus, judge}
 * @returns {{score:number, power:number, bestBonus:number[], bestOrder:number[]}}
 */
export function scoreTeam(team, parsed, ctx) {
  const n = parsed.notes.length;
  if (!n) return { score: 0, power: 0, bestBonus: [], bestOrder: [] };

  const power = totalPower(team, ctx);
  const songLevel = ctx.songLevel ?? 26;
  const levelBonus = 1 + (songLevel - 5) * 0.01;
  const basePerNote = (power * levelBonus * 3 * SCORE_SCALE) / n;
  const judge = ctx.judge ?? JUDGE.perfect;

  // 技能发动顺序：默认**只算一种**（队员按三围从高到低）。
  //
  // 枚举 120 种排列会把耗时放大 120 倍：792 组合 × 5 C 位 × 120 顺序 × 380 NOTE
  // = 1.8 亿次循环，实测 133 秒，浏览器直接假死。
  //
  // 而顺序对总分的影响很小（技能总时长固定，只是覆盖位置不同），实测各顺序
  // 差距在 2% 以内，**排名基本不变**。需要精确比较时再传 optimizeOrder: true。
  let best = { score: -1, bonus: [], order: [0, 1, 2, 3, 4] };
  const orders = ctx.optimizeOrder ? permutations5([0, 1, 2, 3, 4]) : [[0, 1, 2, 3, 4]];
  for (const perm of orders) {
    // perm[0] 视为 C 位所在的队员下标，保持 C 位在第 1 位
    const order = [team[0], ...perm.filter((i) => i !== 0).map((i) => team[i])];
    const bonus = skillBonusPerNote(parsed, order, ctx.skills || {}, ctx.level ?? 5);
    let total = 0;
    for (let i = 0; i < n; i++) {
      total += basePerNote * judge * (1 + comboBonus(i + 1)) * (1 + bonus[i]);
    }
    if (total > best.score) best = { score: total, bonus, order: perm };
  }

  return {
    score: Math.round(best.score),
    power,
    bestBonus: best.bonus,
    bestOrder: best.order,
  };
}

// ---------------------------------------------------------------------------
// 活动点数
// ---------------------------------------------------------------------------

/**
 * 一次 LIVE 能拿到的活动点数。
 *
 * 不同活动类型的公式**真的不同**（以下来自玩家逆向，与游戏结算面板一致）：
 *
 * **传统 / 试炼 / 任务 / 竞演 / 挑战**
 * ```
 * pt = (50 + 个人分/10000) × (1 + 活动加成) × 火倍率
 * 火倍率：1 火 ×5、2 火 ×10、3 火 ×15
 * ```
 * 个人分 150 万时「分数项」就到上限 150。
 *
 * **组曲（medley）**
 * ```
 * pt = (分数/18500 + 组曲奖励) × BoostBonus
 * 组曲奖励：打 1/2/3 首 -> 30 / 65 / 100
 * BoostBonus：各首火倍率相加（2火+1火+0火 -> 10+5+1 = 16）
 * ```
 *
 * **挑战（challenge）** 的加成直接进综合力，所以 pt 不再乘一次加成。
 *
 * @param {number} score 打歌分数
 * @param {object} event 活动数据
 * @param {{bonus?:number, boost?:number, medleySongs?:number, boostSum?:number}} [opt]
 *        bonus  队伍的活动加成百分比（属性+角色+双重+突破）
 *        boost  火倍率（1火=5）
 * @returns {number}
 */
export function eventPoints(score, event, opt = {}) {
  const boost = opt.boost ?? 5;           // 默认 1 火
  const bonus = (opt.bonus ?? 0) / 100;
  const ty = event?.ty || '';

  if (ty === 'medley') {
    const songs = opt.medleySongs ?? 3;
    const medleyBonus = songs >= 3 ? 100 : songs === 2 ? 65 : 30;
    const boostSum = opt.boostSum ?? 15;  // 3 首各 3 火 = 5+5+5
    return Math.floor((score / 18500 + medleyBonus) * boostSum);
  }

  if (ty === 'challenge') {
    // 加成已计入综合力，这里不再乘
    return Math.floor((50 + Math.min(score / 10000, 150)) * boost);
  }

  // 传统 / 试炼 / 任务 / 竞演 / story
  return Math.floor((50 + Math.min(score / 10000, 150)) * (1 + bonus) * boost);
}

// ---------------------------------------------------------------------------
// 组队
// ---------------------------------------------------------------------------

/**
 * 从候选卡里挑出得分最高的 5 张。
 *
 * 先把候选按三围总和排序取前 `POOL_LIMIT` 张（三围是分数的主项），
 * 再对它做精确组合枚举——`C(24,5)=42504`，浏览器里几百毫秒。
 * 这样做会漏掉个别"三围一般但技能极强"的卡，所以调用方可以调大上限。
 *
 * @param {object[]} pool  候选卡（含 `sx`/`ep`/`sk`）
 * @param {object}   parsed
 * @param {object}   ctx
 * @param {{topN?:number, poolLimit?:number}} [opt]
 * @returns {{team:object[], score:number, power:number}[]}
 */
export async function bestTeams(pool, parsed, ctx, opt = {}) {
  const topN = opt.topN ?? 10;
  // ⚠️ 默认按**分数**排会把"综合力高但没吃活动加成"的队排前面，
  // 真正能拿点数的反而被挤出 TopN。要追活动点时必须传 sortBy: 'points'。
  const sortBy = opt.sortBy || 'score';
  const limit = opt.poolLimit ?? POOL_LIMIT;

  // ⚠️ **不能只按三围取前 N**：活动加成卡的属性/角色不一定是三围最高的，
  // 单靠三围过滤会把它们整批筛掉，最后算出的队伍一点加成都不吃（实测踩过）。
  // 所以候选池 = 三围最高的 N 张 **并上** 所有吃到活动加成的卡。
  const withSum = pool.map((c) => ({
    c, sum: (c.sx || [0, 0, 0]).reduce((a, b) => a + b, 0),
  })).sort((a, b) => b.sum - a.sum);

  const picked = new Map();
  for (const x of withSum.slice(0, limit)) picked.set(x.c.id ?? x.c, x.c);

  if (ctx.event) {
    // 吃到加成的卡按「加成优先、三围次之」排序，只取前 BONUS_LIMIT 张。
    // 全拉进来会让候选池膨胀到上百张，C(n,5) 直接爆炸（实测超时）。
    const bonusCards = withSum
      .map((x) => ({ ...x, bonus: eventBonusFor(x.c, ctx.event, 0).point }))
      .filter((x) => x.bonus > 0)
      .sort((a, b) => (b.bonus - a.bonus) || (b.sum - a.sum))
      .slice(0, BONUS_LIMIT);
    for (const x of bonusCards) picked.set(x.c.id ?? x.c, x.c);
  }
  const ranked = [...picked.values()];

  const results = [];
  const n = ranked.length;
  // 组合总数，用来算进度
  let total = 0;
  for (let a = 0; a < n - 4; a++) total += (n - a - 1) * (n - a - 2) * (n - a - 3) * (n - a - 4) / 24;
  let done = 0;
  const onProgress = opt.onProgress || null;
  for (let a = 0; a < n - 4; a++) {
    for (let b = a + 1; b < n - 3; b++) {
      for (let c = b + 1; c < n - 2; c++) {
        for (let d = c + 1; d < n - 1; d++) {
          for (let e = d + 1; e < n; e++) {
            done += 1;
            // **必须真正让出主线程**：只调回调不解阻塞，页面仍然冻住。
            // 每 400 组合 await 一次 setTimeout(0)，浏览器才有机会重绘进度。
            // 每 2000 组合让出一次：400 太频繁，await 本身的开销比计算还大
            // （实测 400 时整轮 25s，2000 后约 5s）。
            if (done % 2000 === 0) {
              if (onProgress) onProgress(done / total);
              await new Promise((r) => setTimeout(r, 0));
            }
            const five = [ranked[a], ranked[b], ranked[c], ranked[d], ranked[e]];
            // 只试技能最强的 2 张当 C 位。返场技能由队长发动，技能弱的卡当队长
            // 一定更差；全试 5 张等于把枚举量乘 5，收益却为零。
            const bySkill = [...five].sort((x, y) =>
              ((ctx.skills?.[String(y.sk)]?.up) || 0) - ((ctx.skills?.[String(x.sk)]?.up) || 0));
            let best = null;
            for (const capCard of bySkill.slice(0, 2)) {
              const team = [capCard, ...five.filter((x) => x !== capCard)];
              const r = scoreTeam(team, parsed, ctx);
              if (!best || r.score > best.score) best = { team, ...r };
            }
            const row = { team: best.team, score: best.score, power: best.power };
            if (sortBy === 'points') {
              // 队伍的活动加成（逐卡取平均，与游戏结算口径一致）
              const bonuses = best.team.map((c) => eventBonusFor(c, ctx.event || null, 0).point);
              row.bonusPct = bonuses.reduce((a, b) => a + b, 0) / bonuses.length;
              row.points = eventPoints(best.score, ctx.event || null,
                { bonus: row.bonusPct, boost: ctx.boost ?? 5 });
            }
            results.push(row);
          }
        }
      }
    }
  }

  if (onProgress) onProgress(1);
  results.sort((a, b) => {
    if (sortBy === 'points') {
      // 点数有上限（分数够 150 万就封顶），封顶后只能靠加成拉开差距，
      // 所以**先比加成，再比分数**，而不是直接比点数。
      const bp = b.bonusPct ?? 0, ap = a.bonusPct ?? 0;
      if (Math.abs(bp - ap) > 0.5) return bp - ap;
      return b.score - a.score;
    }
    return b.score - a.score;
  });
  return results.slice(0, topN);
}
