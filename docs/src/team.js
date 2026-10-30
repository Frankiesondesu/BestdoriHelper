/**
 * 组队助手 · 出分计算引擎
 *
 * 目标：按游戏《BanG Dream! 少女乐团派对》的实际计分规律，算出某支队伍在
 * 某首歌上的**预计分数 / 活动点数**，并在一组候选卡里挑出最优的 5 张。
 *
 * ## 三层结构
 *
 * ```
 * ① 综合力  = 卡面 + 区域道具 + 活动加成        （加算，见 totalPower）
 * ② 分数    = 综合力 × (1+Lv加成) × 300% × 判定 × (1+COMBO) × (1+技能)
 * ③ 点数    = 各活动类型自己的公式              （见 eventPoints）
 * ```
 *
 * ## ① 综合力（对照 Bestdori 组队工具源码核对过）
 *
 * ```
 * 卡面 = Σ(基础三围 + 星光三围 + 训练 + 剧情) × (1 + 角色评级)
 * 道具 = Σ(卡面 × 该卡吃到的道具百分比)
 * 活动 = Σ(卡面 × 该卡的活动加成百分比)
 * ```
 *
 * 三块**加算**。道具和活动加成乘在同一个卡面上，彼此没有交叉项。
 *
 * ## ② 分数
 *
 * ```
 * Lv加成 = (歌曲等级 - 5) × 1%
 * 判定：PERFECT 110% / GREAT 80% / GOOD 50% / BAD 及以下 0%
 * COMBO：0~20 0%、21~50 1%、51~100 2%、…、701+ 11%
 * 技能：一首歌发动 **6 次**，覆盖到的 NOTE 才吃加成
 * ```
 *
 * 技能 6 次是**所有模式都一样**的，区别只在「谁提供技能」：
 *
 * | 模式 | 前 5 次 | 第 6 次 |
 * |------|---------|---------|
 * | 协力（任务/试炼） | 5 名玩家的队长技能，顺序随机 | 出分最高者的队长技能 |
 * | 专门（竞演/组曲/FES/挑战） | 自己队伍 5 张卡，顺序随机 | 自己队长的技能 |
 *
 * 由此得到两条实践结论：队长位一定要放技能最强的卡；协力模式里「提高队长
 * 技能等级」的收益比换一张强卡更直接。
 *
 * 本引擎把 6 个发动点按 NOTE 数均分，每次从该点对应的**时间**起持续
 * `duration` 秒（随技能等级 5→7 秒）。默认只算一种顺序（队员按技能强度升序，
 * 让强技能覆盖更值钱的后段 NOTE）；需要精确比较时传 `optimizeOrder: true`
 * 枚举 120 种排列 —— 代价是 120 倍耗时，浏览器里会卡死。
 *
 * ## ③ 点数
 *
 * 见 `eventPoints`。要点是「加成只走一边」：
 *
 * | 活动 | 加成作用域 |
 * |------|-----------|
 * | 传统 / 任务 / 试炼 / 组曲 | 活动点数（`pt = base × (1 + Σ加成) × 火`） |
 * | 竞演 / 挑战 / FES | 综合力（进分数，点数不再乘加成） |
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

/**
 * 分数标定系数 —— **保持 1.0，不要动**。
 *
 * Bestdori 前端计算引擎里算分数的函数（`ct`）的累加式是：
 *
 * ```js
 * 每 NOTE = Math.floor(Math.floor(power * l * combo * fever * judge) * skillMul)
 * l       = (3 + 0.03 * (playLevel - 5)) / noteCount
 * combo   = ut(连击数)     // 1, 1.01, 1.02 … 1.11 的分段表
 * judge   = 1.1 * perfect率 + 0.8 * (1 - perfect率)
 * ```
 *
 * **综合力是直接乘进去的，没有任何额外常数。** 本引擎的 `(1+Lv加成) × 3`
 * 恰好等于 `3 + 0.03×(Lv-5)`，连击表、判定、技能倍率也逐项一致 ——
 * 所以这里必须是 1.0 才能对齐。
 *
 * 历史：原来写的是 1.45，来自「30 万综合力打 Lv28 约 280 万分」这个社区锚点。
 * 但用 Bestdori 的公式反算，30 万综合力只有约 **222 万**，锚点本身不可靠。
 * 保留这个变量只是留一个显式调节点，**默认必须是 1**。
 */
const SCORE_SCALE = 1;

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
 * 区域道具「等级 → 单件加成百分比」。
 *
 * ⚠️ **每个品类的曲线都不一样**，不能用一个系数乘等级。数据来自 Bestdori 的
 * `areaItems/main.5.json`（74 件道具，逐件实测），全是线性的：
 *
 * | 品类 | Lv1 | Lv8 | 公式 |
 * |------|-----|-----|------|
 * | 乐器（话筒/吉他/贝斯/鼓/键盘） | 2% | 5.5% | `1.5 + 0.5×L` |
 * | 海报 / 传单 | 6% | 13% | `5 + L` |
 * | 属性道具（椰子树/足浴池…） | 2% | 16% | `2×L` |
 * | 杂志（电影/音乐/时尚） | 8% | 22% | `6 + 2×L` |
 * | 大家的话筒（共通·乐器） | 1% | 2.75% | `0.75 + 0.25×L` |
 * | 大家的海报（共通·海报） | 3% | 6.5% | `2.5 + 0.5×L` |
 *
 * 早期实现写的是 `0.015 × 等级`，把乐器 Lv8 算成 12%（实际 5.5%）、
 * 属性道具也算成 12%（实际 16%）—— 两头都错，而且错的方向相反。
 */
export const AREA_ITEM_CURVE = {
  band: (lv) => (lv > 0 ? 1.5 + 0.5 * lv : 0),
  bandPoster: (lv) => (lv > 0 ? 5 + lv : 0),
  everyone: (lv) => (lv > 0 ? 0.75 + 0.25 * lv : 0),
  everyonePoster: (lv) => (lv > 0 ? 2.5 + 0.5 * lv : 0),
  attr: (lv) => (lv > 0 ? 2 * lv : 0),
  magazine: (lv) => (lv > 0 ? 6 + 2 * lv : 0),
};

/**
 * 每个品类**同时生效的件数**。
 *
 * Bestdori 的 `it()` 是 `e.band[row].forEach(...)` —— 一行 7 件**全部相加**。
 * 而 7 件里前 5 件是乐器、后 2 件是海报/传单，曲线不同：
 *
 * ```
 * 一支满级乐队 = 5×5.5% + 2×13% = 53.5%      （不是 5.5%，也不是 7×5.5%=38.5%）
 * 大家的话筒满级 = 5×2.75% + 2×6.5% = 26.75%
 * ```
 *
 * 早期实现只算 1 件，综合力少算一半以上。
 */
export const AREA_ITEM_SLOTS = {
  band: 5, bandPoster: 2,
  everyone: 5, everyonePoster: 2,
  attr: 1, magazine: 1,
};

/** 一支乐队道具全开的总百分比（Lv0 → 0） */
export function bandItemPct(lv) {
  if (!(lv > 0)) return 0;
  return AREA_ITEM_SLOTS.band * AREA_ITEM_CURVE.band(lv)
    + AREA_ITEM_SLOTS.bandPoster * AREA_ITEM_CURVE.bandPoster(lv);
}

/** 「大家的话筒」一整套的总百分比 */
export function everyoneItemPct(lv) {
  if (!(lv > 0)) return 0;
  return AREA_ITEM_SLOTS.everyone * AREA_ITEM_CURVE.everyone(lv)
    + AREA_ITEM_SLOTS.everyonePoster * AREA_ITEM_CURVE.everyonePoster(lv);
}

/**
 * 属性道具 / 杂志的**中文名 → 作用对象**。
 *
 * 只涉及 11 件（4 属性 × 2 槽位 + 3 本杂志），改名概率极低，内置比多拉一次表划算。
 * `my-items.json` 的 `detail.<槽位>.name` 是 OCR 出来的中文名，靠这张表反查。
 */
const ITEM_META = {
  椰子树: { attr: 'powerful' },
  足浴池: { attr: 'cool' },
  米歇尔的铜像: { attr: 'happy' },
  喷泉: { attr: 'pure' },
  肉酱意大利面: { attr: 'powerful' },
  紫莓冰霜: { attr: 'cool' },
  马卡龙塔: { attr: 'happy' },
  水果挞: { attr: 'pure' },
  电影杂志: { stat: 0 },
  音乐杂志: { stat: 1 },
  时尚杂志: { stat: 2 },
};

/**
 * 给一支队伍挑最优的区域道具。
 *
 * 道具槽分四类，**作用域和互斥关系各不相同**：
 *
 * | 品类 | 槽位数 | 生效范围 | 互斥 |
 * |------|--------|----------|------|
 * | 乐器（吉他/贝斯/鼓/键盘/话筒） | 5 | 专有→该乐队成员；共通→全队 | **同槽只能装一件** |
 * | 海报 / 传单 | 2 | 同上 | 同上 |
 * | 属性道具 | Menu + Plaza 各 1 | 该属性成员（同属性才叠加） | 不互斥 |
 * | 杂志 | 1 | 全队，**只加一围** | 不互斥 |
 *
 * 关键规则（Frankieson 2026-10-30 指出）：**只有同一件乐器不能重复选**。
 * 装了「大家的键盘」就不能再装有咲的键盘，但「大家的键盘 + 多惠的吉他」
 * 可以同时存在 —— 它们是不同槽位。
 *
 * 所以专有套和共通套的关系是**逐槽二选一**：
 * - 不是整队相加（那会把 5 件乐器算成 10 件）
 * - 也不是整套二选一（那会漏掉混装）
 *
 * ⚠️ 不能简单地"选人最多的乐队"——如果 PPP 道具 Lv8 而 HHW 只有 Lv3，
 * 队里 PPP 2 人、HHW 3 人时，2×5.5% > 3×3%，该开 PPP。判据是
 * **卡面 × 曲线**，不是人数。
 *
 * @param {object[]} team 5 张卡（需 ch / a / sx）
 * @param {object} levels {道具槽: 等级}（来自 my-items.json）
 * @param {object} [chars] {角色ID: {b}} 用于查每张卡的乐队
 * @param {object} [detail] my-items.json 的 detail（含 OCR 出来的道具中文名）
 * @param {number[]} [weights] 每张卡的卡面权重（省略则用 sx 三围之和）
 * @returns {{area:object, bandSlot:string|null, attrSlot:string|null}}
 */
//: 道具槽位名 -> 角色数据里的乐队名。
//: 两套命名不一致（槽位是 HelloHappyWorld，乐队是 Hello, Happy World!），
//: 不映射就永远匹配不到，会静默退到"随便一个能匹配的乐队"。
const SLOT_TO_BAND = {
  PoppinParty: "Poppin'Party",
  Afterglow: 'Afterglow',
  PastelPalettes: 'Pastel*Palettes',
  Roselia: 'Roselia',
  HelloHappyWorld: 'Hello, Happy World!',
  Morfonica: 'Morfonica',
  RaiseASuilen: 'RAISE A SUILEN',
  MyGO: 'MyGO!!!!!',
};

export function pickAreaItems(team, levels, chars, detail, weights) {
  const lv = (k) => Number(levels?.[k] || 0);
  const bandOf = (c) => c.band ?? chars?.[c.ch]?.b ?? null;

  // 卡面权重：比较「开专有」和「开共通」哪个收益大时必须用它。
  // 专有只作用于该乐队成员、共通作用于全队，人数和卡面都不一样 ——
  // 只比百分比会算错（3 张高综合力 vs 5 张平均）。
  const wOf = (c, i) => {
    const w = weights?.[i];
    if (typeof w === 'number' && w > 0) return w;
    const s = c?.sx;
    return Array.isArray(s) ? (s[0] + s[1] + s[2]) || 1 : 1;
  };

  // 统计每支乐队 / 每种属性的成员数与卡面之和
  const bandCnt = {};
  const bandSum = {};
  const attrCnt = {};
  let teamSum = 0;
  team.forEach((c, i) => {
    const w = wOf(c, i);
    teamSum += w;
    const b = bandOf(c);
    if (b) {
      bandCnt[b] = (bandCnt[b] || 0) + 1;
      bandSum[b] = (bandSum[b] || 0) + w;
    }
    if (c.a) attrCnt[c.a] = (attrCnt[c.a] || 0) + 1;
  });

  const EV = lv('Everyone');
  const SKIP = ['Menu', 'Plaza', 'Magazine', 'Everyone', 'potentials'];

  // ① 选乐队：按「这套道具给它的成员带来多少加成」排，不是按人数
  let bandBest = null;
  for (const slot of Object.keys(levels || {})) {
    if (SKIP.includes(slot)) continue;
    const band = SLOT_TO_BAND[slot];
    const L = lv(slot);
    if (!band || !(L > 0) || !bandSum[band]) continue;
    const gain = bandSum[band] * (AREA_ITEM_SLOTS.band * AREA_ITEM_CURVE.band(L)
                                + AREA_ITEM_SLOTS.bandPoster * AREA_ITEM_CURVE.bandPoster(L));
    if (!bandBest || gain > bandBest.gain) bandBest = { slot, band, L, gain };
  }

  // ② 逐槽位二选一：这个槽装「专有」还是「大家的」
  //
  //    判据是**综合力增量**：专有 = 该乐队成员卡面 × 专有曲线，
  //    共通 = 全队卡面 × 共通曲线。
  //
  //    满级时专有（5.5% / 13%）全面压过共通（2.75% / 6.5%），
  //    结果就是 5×5.5 + 2×13 = 53.5%，共通道具一件都不装。
  //    但专有只有 Lv1、共通 Lv8 时，共通反而更值 —— 这才需要逐槽算。
  const pickSlots = (perSlotBand, perSlotEv, slots) => {
    const bandGain = bandBest ? bandSum[bandBest.band] * perSlotBand : 0;
    const evGain = teamSum * perSlotEv;
    return bandBest && bandGain >= evGain
      ? { band: slots * perSlotBand, ev: 0 }
      : { band: 0, ev: slots * perSlotEv };
  };

  const instr = pickSlots(
    AREA_ITEM_CURVE.band(bandBest?.L ?? 0),
    AREA_ITEM_CURVE.everyone(EV),
    AREA_ITEM_SLOTS.band,
  );
  const post = pickSlots(
    AREA_ITEM_CURVE.bandPoster(bandBest?.L ?? 0),
    AREA_ITEM_CURVE.everyonePoster(EV),
    AREA_ITEM_SLOTS.bandPoster,
  );

  const bandPct = instr.band + post.band;
  const everyonePct = instr.ev + post.ev;

  // ② 属性道具：Menu / Plaza 两件，只有属性和队伍主属性一致才生效，同属性叠加
  const topAttr = Object.entries(attrCnt).sort((a, b) => b[1] - a[1])[0]?.[0] || null;
  const attrOf = (slot) => ITEM_META[detail?.[slot]?.name]?.attr || null;
  let attrPct = 0;
  let attrSlot = null;
  for (const slot of ['Menu', 'Plaza']) {
    const a = attrOf(slot);
    // 没 OCR 到名字时退回「两件都当成主属性」的乐观估计（旧行为）
    const hit = a ? a === topAttr : true;
    if (!hit || !lv(slot)) continue;
    attrPct += AREA_ITEM_CURVE.attr(lv(slot));
    if (!attrSlot) attrSlot = slot;
  }

  // ③ 杂志：一本，只加一围（电影=表演 / 音乐=技巧 / 时尚=视觉）
  const magStat = ITEM_META[detail?.Magazine?.name]?.stat ?? null;
  const magPct = magStat != null ? AREA_ITEM_CURVE.magazine(lv('Magazine')) : 0;

  const area = {
    // ⚠️ 这些值都是**小数**（0.535 = 53.5%），`totalPower` 直接乘卡面
    // `band` 只作用于 bandBand 成员，`everyone` 作用于全队 —— 两者分开存，
    // 因为逐槽混装时可能同时非零（例：乐器全专有 + 海报全共通）。
    band: bandPct / 100,
    bandBand: bandPct > 0 ? bandBest.band : undefined,
    everyone: everyonePct / 100,
    attr: attrPct / 100,
    attrAttr: topAttr || undefined,
    magazine: 0,                    // 杂志走 param 通道，只加一围
    param: magPct / 100,
    paramStat: magStat,
    paramAttr: undefined,
  };
  return {
    area,
    bandSlot: bandBest?.slot || null,
    attrSlot,
    bandScore: bandPct > 0 ? bandPct / 100 : 0,
    attrScore: attrPct > 0 ? attrPct / 100 : 0,
  };
}

/**
 * 星光等级带来的三围提升（**每一围**的增量）。
 *
 * 公式来自 Bestdori 组队工具的源码（`app.js` 里单卡综合力函数）：
 *
 * ```js
 * var c = 50 * t.rarity * i;     // i = 星光等级
 * m[0] += c; m[1] += c; m[2] += c;
 * ```
 *
 * 也就是 **每级每围 +50 × 星级**：
 * 4★ 每级 +200（三围共 600）、5★ 每级 +250（共 750）、3★ 每级 +150。
 * 社区口径只写了 4★/5★ 的数字，和这个公式完全吻合；1~3★ 用同一公式外推。
 *
 * 这部分是**面板的一部分**（和剧情加成一样），会被角色评级、区域道具、活动加成
 * 一起放大。
 *
 * ⚠️ Bestdori 的 `stat` 只给到基础三围（level / episodes / training），
 * **不含星光**，必须在这里补。不补的话 5★ 满星光卡会少 3000 综合力，
 * 一支队差 1.5 万（约 7%），足以让「满星光通用卡」和「白板档期卡」的取舍算反。
 *
 * ⚠️ 星光还有**第二个作用**：按星级/等级给 0.2%~15% 的**活动加成百分比**。
 * 那是另一条路径（`eventBonusFor` 里的 `star` 项），别和这里的三围混起来。
 */
export function starStat(card) {
  const rank = Number(card.master ?? 0);
  if (!(rank > 0)) return 0;
  return 50 * (card.r || 0) * rank;
}

/**
 * 队伍综合力。
 *
 * ## 公式（**已对照 Bestdori 组队工具源码逐行核对**）
 *
 * Bestdori 的 `app.js` 里，单卡综合力是这么算的（去掉混淆后的等价代码）：
 *
 * ```js
 * // ① 卡面：基础三围（按等级插值）
 * // ② 星光：每级每围 +50 × 星级
 * // ③ 训练：+stat.training
 * // ④ 剧情：+stat.episodes[0..ep-1]
 * // ⑤ 角色评级：m[i] += Math.floor(m[i] * 角色评级)
 * // ⑥ 道具 + 活动加成：m[i] += m[i] * (道具% + 活动%)
 * return m[0] + m[1] + m[2];
 * ```
 *
 * 全队综合力 = `Math.floor(Σ 单卡综合力)`。
 *
 * 所以：
 *
 * ```
 * 卡面   = Σ(基础三围 + 星光三围 + 训练 + 剧情) × (1 + 角色评级)
 * 道具   = Σ(卡面 × 该卡吃到的道具百分比)
 * 活动   = Σ(卡面 × 该卡的活动加成百分比)
 * 综合力 = 卡面 + 道具 + 活动
 * ```
 *
 * **三块是加算，不是连乘。** 关键点：道具和活动加成**都乘在同一个卡面上**，
 * 彼此之间没有交叉项。早期实现写成 `卡面 × (1+道具) × (1+活动)`，多算了
 * 道具×活动 的交叉项 —— 道具 35% + 活动 50% 时连乘是 2.025，加算是 1.85，
 * **高估 9.5%**，而且「道具越满、活动加成越高的队被高估得越多」，排序会歪。
 *
 * ## 逐「围」而不是逐「卡」
 *
 * 加成不是全队一个数，也不是每张三围都一样：
 *
 * | 来源 | 作用范围 |
 * |------|----------|
 * | 乐队道具 / 属性道具 / 杂志 | 命中的卡，**三围同值** |
 * | 参数道具（表演/技巧/视觉） | 命中的卡，**只有一围** |
 * | 活动加成 | 命中的卡，三围同值 |
 * | 活动参数加成（`eventCharacterParameterBonus`） | 属性+角色**双匹配**的卡，**只有一围 +50%** |
 *
 * 所以内部按 `[表演, 技巧, 视觉]` 三个数组算，最后再求和。
 *
 * @param {object[]} team 5 张卡（需含 `sx` 满级三围、`ep` 剧情加成、`master` 星光等级）
 * @param {object} opts
 * @param {number} [opts.charRank=0]  角色评级总额，0.11 表示 11%（按 5:6 拆成潜能/任务）
 * @param {number} [opts.charPotential] 潜能解放，默认 `charRank × 5/11`
 * @param {number} [opts.charMission]   角色任务，默认 `charRank × 6/11`
 * @param {object} [opts.area]        {band, bandBand, everyone, attr, attrAttr, magazine, param, paramStat, paramAttr}
 * @param {object} [opts.event]       活动数据
 * @param {object} [opts.characters]  {角色ID: {b}} 补乐队名
 */
export function totalPower(team, opts = {}) {
  const chars = opts.characters || null;
  const area = opts.area || { band: 0, attr: 0, magazine: 0 };
  const ev = opts.event || null;
  const charRank = opts.charRank ?? 0;
  // 角色评级 = 潜能解放（上限 5%）+ 角色任务（上限 6%），**两项各自向下取整**。
  // 合并成 11% 再取整会多 1 点 —— Bestdori 就是合并算的，所以它比游戏高 1~2。
  const pot = opts.charPotential ?? charRank * (5 / 11);
  const mis = opts.charMission ?? charRank * (6 / 11);

  let bandParam = 0;   // 乐队参数
  let itemBonus = 0;   // 区域道具奖励
  let eventAdd = 0;    // 活动加成

  for (const c of team) {
    const s = c.sx || [0, 0, 0];
    const e = c.ep || [0, 0, 0];
    const star = starStat(c);

    // ① 卡面：三围 + 剧情 + 星光，再乘角色评级（**逐项向下取整**，与游戏一致）
    //
    // 贴吧「邦邦综合力计算详解」用 6198 这个例子定死了这里：
    //   floor(6198×5%) + floor(6198×6%) = 309 + 371 = 680   ← 游戏显示
    //   floor(6198×11%)                 = 681               ← 多 1
    // 所以必须拆成两段，不能合并。
    const panel = [0, 1, 2].map((i) => {
      const v = s[i] + e[i] + star;
      return v + Math.floor(v * pot) + Math.floor(v * mis);
    });
    bandParam += panel[0] + panel[1] + panel[2];

    // ② 区域道具：按「这张卡」匹配的乐队/属性道具 + 杂志（杂志全队生效）
    // ⚠️ 卡对象来自 team.json，**只有 ch/r/a/sk/sx 等，没有 band 字段**。
    // 直接比较 c.band 会永远 false，导致乐队道具静默失效（实测踩过）。
    const cardBand = c.band ?? chars?.[c.ch]?.b;
    let areaPct = 0;
    // 专有：只作用于该乐队成员
    if (area.bandBand === undefined || area.bandBand === cardBand) areaPct += (area.band || 0);
    // 共通（大家的乐器/海报）：全队生效，与专有**逐槽互斥**，不叠加同一件
    areaPct += (area.everyone || 0);
    if (!area.attrAttr || area.attrAttr === c.a) areaPct += (area.attr || 0);
    areaPct += (area.magazine || 0);
    if (typeof opts.areaBonus === 'number') areaPct = opts.areaBonus;   // 兼容旧写法

    const areaArr = [areaPct, areaPct, areaPct];
    // 参数道具只加一围（游戏里「パフォーマンス+XX%」这类）
    if (area.paramStat != null && area.param > 0
        && (!area.paramAttr || area.paramAttr === c.a)) {
      areaArr[area.paramStat] += area.param;
    }

    // ③ 活动加成：逐卡百分比；综合力型活动才有值
    const eb = ev ? eventBonusFor(c, ev, c.master ?? 0) : null;
    const evPct = eb ? eb.parameter / 100 : 0;
    const evArr = [evPct, evPct, evPct];
    if (eb?.paramStat) evArr[eb.paramStat.stat] += eb.paramStat.pct / 100;

    for (let i = 0; i < 3; i++) {
      itemBonus += panel[i] * areaArr[i];
      eventAdd += panel[i] * evArr[i];
    }
  }

  // 道具加成、活动加成**各自取整**，再与乐队参数相加。
  //
  // 贴吧「邦邦综合力计算详解」的原例：
  //   道具 186875×51.5% + … = 146292.505 → 146292
  //   活动 …                =  12583.14  →  12583
  // 两项分别 floor 后相加，不是先加再 floor。Bestdori 是整体 floor，
  // 所以它和游戏会差 1~2 点。
  return bandParam + Math.floor(itemBonus) + Math.floor(eventAdd);
}

/**
 * 活动加成作用在「活动点数」还是「综合力」。
 *
 * 这一条决定了同一份加成到底进哪里 —— **不能两头都算**，否则等于算两遍。
 *
 * ## 实际数据（`docs/data/events.json` 的 `ab` 字段，2026-10-30 统计）
 *
 * | 活动类型 | `ab` 取值 | 个数 |
 * |----------|-----------|------|
 * | story | 无 | 25（全部） |
 * | mission_live | `(20,0)` / `(20,20)` / 无 | 43 / 15 / 6 |
 * | live_try | `(20,0)` / `(20,20)` / 无 | 52 / 1 / 15 |
 * | versus | `(0,20)` / `(20,20)` / 无 | 44 / 4 / 26 |
 * | festival | `(0,20)` / `(20,20)` | 11 / 3 |
 * | **medley** | **`(0,20)`** | **28（全部）** |
 * | **challenge** | **`(20,20)`** / 无 | **31 / 23** |
 *
 * 括号是 `(pointPercent, parameterPercent)`。
 *
 * ## 当前口径
 *
 * 依据 B 站专栏《邦邦乐队综合力计算公式》（cv42816643，2025-08-25）：
 * 「活动加成不常驻，只在特定活动类型的特定玩法内生效（**cp、对邦、组曲、5v5** 的
 * 活动专属房间）」——
 *
 * | 中文 | 类型 | 加成作用域 |
 * |------|------|-----------|
 * | 任务演出 / 试炼演出 | mission_live / live_try | 活动点数 |
 * | 传统 | story | 活动点数（老活动，无 `ab` 数据） |
 * | cp（挑战） | challenge | **综合力** |
 * | 对邦（竞演） | versus | **综合力** |
 * | 组曲 | medley | **综合力** |
 * | 5v5（团队 FES） | festival | **综合力** |
 *
 * ## 与 `ab` 数据的两处出入（已知，暂不改）
 *
 * | 类型 | `ab` = (pointPercent, parameterPercent) | 本站 | 说明 |
 * |------|----------------------------------------|------|------|
 * | medley | `(0,20)` × 28（全部） | 综合力 | ✓ 已对齐 |
 * | challenge | `(20,20)` × 31 | 综合力 | `(20,20)` 语义未确认 |
 *
 * `(20,20)` 可能表示「PT 和综合力各加一份」，也可能只是数据格式兼容。既然专栏
 * 明确把 cp 归到综合力一侧，这里就按综合力算，`pointPercent` 那一份先不重复加。
 *
 * @param {object} event
 * @returns {'pt'|'param'}
 */
export function eventChannel(event) {
  const ty = event?.ty || '';
  return ty === 'versus' || ty === 'challenge' || ty === 'festival' || ty === 'medley'
    ? 'param' : 'pt';
}

/**
 * 活动加成明细。
 *
 * ## 单卡加成（逐项相加，**一个卡只有一个数**）
 *
 * | 项 | 触发条件 | 数值 | 数据来源 |
 * |----|----------|------|----------|
 * | 属性 | 卡的属性 = 活动属性 | +10% | `attr[]` |
 * | 角色 | 卡的角色在加成名单里 | +20% | `ch[]` |
 * | 双匹配 | **同时**匹配属性和角色 | 额外 +20% | `ab.pointPercent \|\| ab.parameterPercent` |
 * | 当期卡 | 卡本身是本期活动卡 | +20% | `members[]` |
 * | 星光 | 按星级 + 星光等级查表 | 0.2~15% | `limitBreaks[]` |
 *
 * 官方公告原文：「属性一致时可获得 10% 的加成，角色一致时加成 20%；且在属性·
 * 角色同时符合加成条件时，加成将提升至 50%！」—— 50% = 10 + 20 + 20，
 * 所以「双匹配」不是取代前两项，而是那额外的 20%。
 *
 * 于是（以 5★/4★ 满星光、非当期卡为例）：
 *
 * ```
 * 非当期 5★ 双匹配 = 10 + 20 + 20 + 0 + 15 = 65%
 * 非当期 4★ 双匹配 = 10 + 20 + 20 + 0 +  6 = 56%
 * 当期   5★ 双匹配 = 10 + 20 + 20 + 20 + 15 = 85%
 * ```
 *
 * 视频里那支队显示 +56% / +65%，是因为那几张卡**不是当期卡**。
 * 「档期卡比同色五星通用卡更强」的量化来源就是这个 20%。
 *
 * ## 返回值为什么有 `point` 和 `parameter` 两份
 *
 * 同一个 `bonus` 数，按活动类型只喂其中一边（见 `eventChannel`）。
 * 两边都返回是为了让调用方按场景取，**但绝不能相加**。
 *
 * ## `paramStat`（活动参数加成）
 *
 * 竞演 / 挑战 / FES 的活动数据里还有一个 `eventCharacterParameterBonus`
 * （本站 `events.json` 存成 `cp`），形如 `{performance: 50}`。
 * Bestdori 源码里它这么用：
 *
 * ```js
 * if (角色命中 && 属性命中) {
 *   if (eventParam) L[eventParam-1] += 0.5;   // 指定那一围 +50%
 *   L = L.map(t => t + 双匹配加成);
 * }
 * ```
 *
 * 即：**属性+角色双匹配**的卡，指定那一围再 +50%。只在综合力型活动生效。
 *
 * ⚠️ 星光等级的单位就是百分数：1★ 是 0.2（即 0.2%），4★ 是 6，5★ 是 15。
 * 早期代码按「>1 视为整数、<=1 视为小数」归一化，把 1★/2★ 的 0.2 放大成了 20%。
 *
 * @param {object} card    卡（需含 id / ch / a / r）
 * @param {object} event   活动数据（events.json 的一条）
 * @param {number} [limitBreakRank=0] 这张卡的星光等级 0~4
 * @returns {{bonus:number, point:number, parameter:number,
 *            paramStat:{stat:number,pct:number}|null,
 *            matched:boolean, member:boolean, parts:object}}
 */
export function eventBonusFor(card, event, limitBreakRank = 0) {
  const empty = { bonus: 0, point: 0, parameter: 0, paramStat: null, matched: false, member: false,
                  parts: { attr: 0, char: 0, double: 0, member: 0, star: 0 } };
  if (!event) return empty;

  const attrHit = (event.attr || []).some((x) => x.a === card.a);
  const charHit = (event.ch || []).some((x) => x.c === card.ch);
  // 当期卡：按**卡 ID** 判定，不是角色
  const memberHit = card.id != null && (event.mb || []).includes(Number(card.id));

  const attrPct = (event.attr || []).find((x) => x.a === card.a)?.p || 0;
  const charPct = (event.ch || []).find((x) => x.c === card.ch)?.p || 0;
  // 双匹配：Bestdori 的 eventMatchBonus = pointPercent || parameterPercent
  const ab = event.ab || {};
  const doublePct = (attrHit && charHit) ? (ab.pointPercent || ab.parameterPercent || 0) : 0;
  const memberPct = memberHit ? (event.mp || 0) : 0;

  // 星光等级：直接就是百分数，**不做量纲归一化**
  let star = 0;
  const row = (event.lb || []).find((x) => x.r === card.r && x.k === limitBreakRank);
  if (row) star = row.p || 0;

  const parts = {
    attr: attrHit ? attrPct : 0,
    char: charHit ? charPct : 0,
    double: doublePct,
    member: memberPct,
    star,
  };
  const bonus = parts.attr + parts.char + parts.double + parts.member + parts.star;

  // 活动参数加成：只有「属性+角色双匹配」且活动是综合力型时才生效
  let paramStat = null;
  const cp = event.cp || {};
  if (attrHit && charHit) {
    const idx = ['performance', 'technique', 'visual'].findIndex((k) => (cp[k] || 0) > 0);
    if (idx >= 0 && eventChannel(event) === 'param') {
      paramStat = { stat: idx, pct: cp[['performance', 'technique', 'visual'][idx]] };
    }
  }

  const ch = eventChannel(event);
  const matched = attrHit || charHit || memberHit;
  return {
    bonus,
    point: ch === 'pt' ? bonus : 0,
    parameter: ch === 'param' ? bonus : 0,
    paramStat,
    matched,
    member: memberHit,
    parts,
  };
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
  // 本地预拉的谱面是「已换算好时间」的精简格式 {d, n, s, f}，
  // 直接返回，省掉 BPM 换算（826 首 × 5 难度都存这份）。
  //
  //   n 每颗 NOTE 的时间
  //   s 技能触发点在 n 里的下标（谱面真实的 skill 标记，不是等距网格）
  //   f Fever 区间 [起, 止]（秒，闭区间）；区间内每颗音符分数翻倍
  //
  // 老文件没有 s/f —— 那时前端只能按 6 等分猜触发点、且完全不知道 Fever。
  // 缺 s 时退回等距网格，缺 f 时按无 Fever 处理（见 `triggerIndices` / `scoreTeam`）。
  if (chart && Array.isArray(chart.n)) {
    const skillSet = new Set(chart.s || []);
    const fever = Array.isArray(chart.f) && chart.f.length === 2 ? chart.f : null;
    return {
      notes: chart.n.map((t, i) => ({
        time: t,
        beat: 0,
        skill: skillSet.has(i),
        fever: !!fever && t >= fever[0] && t <= fever[1],
      })),
      duration: chart.d ?? (chart.n[chart.n.length - 1] || 0),
      fever,
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

  // 连击数 ≠ 事件数。官方 `songs.json` 的 `notes` 就是连击数口径：
  //   Single / Flick / Directional → 1
  //   Long  → len(connections)（头尾各 1）
  //   Slide → **跳过 `hidden` 的连接点**（那是渲染插值，不是连击点）
  //
  // ⚠️ 两个踩过的坑：Directional 只在 SPECIAL 出现，漏掉会让歌 3 少 127 颗；
  // SPECIAL 的弧形滑条有大量 hidden 插值点，歌 9 有 608 个，全算进去
  // 690 → 1270（虚高 84%）。全库 162 首 SPECIAL 已验证。
  //
  // 同时保留 `skill` / `fever` 标记 —— 触发点要用谱面真实位置，
  // Fever 区间内的音符分数翻倍，两者都不能靠猜。
  const feverStart = chart.find((e) => e.type === 'System' && e.data === 'cmd_fever_start.wav');
  const feverEnd = chart.find((e) => e.type === 'System' && e.data === 'cmd_fever_end.wav');
  const fb0 = feverStart ? feverStart.beat : -1;
  const fb1 = feverEnd ? feverEnd.beat : -1;
  const inFever = (b) => fb0 >= 0 && b >= fb0 && b <= fb1;

  const pts = [];
  for (const e of chart) {
    const t = e.type;
    if (t === 'Single' || t === 'Flick' || t === 'Directional') {
      if (typeof e.beat === 'number') {
        pts.push({ beat: e.beat, skill: !!e.skill, fever: inFever(e.beat) });
      }
    } else if (t === 'Long' || t === 'Slide') {
      const conns = (e.connections || []).filter(
        (c) => !c.hidden && typeof c.beat === 'number',
      );
      // Long 只算头尾两个端点；Slide 每个非 hidden 的连接点都算
      const keep = t === 'Long' && conns.length > 1
        ? [conns[0], conns[conns.length - 1]]
        : conns;
      for (const c of keep) {
        pts.push({ beat: c.beat, skill: !!c.skill, fever: inFever(c.beat) });
      }
    }
  }
  // 同拍时触发音符排在前（Bestdori 的排序口径）
  pts.sort((a, b) => a.beat - b.beat || (b.skill ? 1 : 0) - (a.skill ? 1 : 0));

  const notes = pts
    .map((p) => ({ beat: p.beat, time: beatToTime(p.beat), skill: p.skill, fever: p.fever }))
    .sort((a, b) => a.time - b.time || (b.skill ? 1 : 0) - (a.skill ? 1 : 0));

  const duration = notes.length ? notes[notes.length - 1].time : 0;
  return { notes, duration, fever: fb0 >= 0 ? [beatToTime(fb0), beatToTime(fb1)] : null };
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
 * 一次 LIVE 里技能发动几次。
 *
 * **所有模式都是 6 次**（这一点查社区资料确认过，早期版本误以为协力只有 5 次）。
 * 区别只在「谁提供技能」：
 *
 * | 模式 | 前 5 次 | 第 6 次（返场） |
 * |------|---------|----------------|
 * | 协力（任务/试炼） | **5 名玩家的队长**技能，顺序随机 | 出分最高者的队长技能 |
 * | 专门（竞演/组曲/FES/挑战/巡回） | 自己队伍 5 张卡的技能，顺序随机 | 自己队长的技能 |
 *
 * 由此得到两条实践结论：
 * 1. **队长位一定要放技能最强的卡**。协力时你的队长技能会被全队共享；专门
 *    模式里队长还额外提供第 6 次返场。
 * 2. 协力模式的前 5 次技能来自别人，个人无法控制 —— 所以协力活动里「提高
 *    队长技能等级」的收益比换一张强卡更直接。
 *
 * 对单人模拟而言，两种模式的技能**总次数与覆盖率相同**，所以计分模型一致。
 *
 * @param {object} [event] 活动数据（保留参数，便于以后按模式细分）
 * @returns {number} 6
 */
export function skillSlots(event) {  // eslint-disable-line no-unused-vars
  return 6;
}

// 旧的 `skillBonusPerNote`（等距网格 + 取最大）已删除。
// 它有两个错：触发点按 6 等分猜、触发音符自己还吃到了这次技能。
// 现在的做法在 `scoreTeam` 里：`triggerIndices()` 读谱面真实标记，
// 每个 (窗口, 成员) 的增分只算一次，再对 120 种顺序查表求平均。

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
 * 技能触发点在 NOTE 序列里的下标。
 *
 * 优先用谱面里真实的 `skill: true` 标记（`parseChart` 会带出来）。老数据没有
 * 这个标记时退回「6 等分」—— 实测两者差 ±4%，不是零但也不是灾难。
 *
 * 注意：一首歌的触发点数量由谱面决定（通常是 6 个，`slots` 只做上限）。
 */
function triggerIndices(notes, slots) {
  // 按**时刻**去重：同一时刻可能有多个音符都带 `skill` 标记
  // （Slide 的每个连接点都会标）。实测 `special/786` 有 17 个带标记的音符，
  // 但只有 6 个不同时刻 —— 游戏里永远是 6 次发动，不能当成 17 次。
  const idx = [];
  const seen = new Set();
  for (let i = 0; i < notes.length; i++) {
    if (!notes[i].skill) continue;
    const t = notes[i].time;
    if (seen.has(t)) continue;
    seen.add(t);
    idx.push(i);
    if (idx.length >= slots) break;
  }
  if (idx.length) return idx;
  const n = notes.length;
  const out = [];
  for (let k = 0; k < slots; k++) out.push(Math.floor((n * k) / slots));
  return out;
}

/**
 * 计算一支队伍在某张谱面上的分数。
 *
 * 模型与 Bestdori `c0f0` 导出的 `ct()` 逐项对齐（`ct` 会把 120 种技能顺序
 * 全算一遍再取平均，这里用同样的口径，但**不重复扫谱面**）：
 *
 * ```
 * 每 NOTE 基础分 = floor(综合力 × 谱面系数 × combo × fever × 判定)
 * 技能后分数     = floor(基础分 × 技能倍率)
 * ```
 *
 * ## 为什么不用「只算一种顺序」了
 *
 * 原来默认只算一种（技能升序），理由是枚举 120 序会慢 120 倍。但 120 序的
 * 平均可以**代数化简**：每个 (窗口 w, 成员 m) 的整数增分 `E[w][m]` 只跟窗口
 * 位置和成员技能有关，与整体顺序无关（窗口终点由下个触发点截断，不依赖顺序）。
 * 于是先把 K×5 个增分算出来，再对 120 种排列查表求和 —— 谱面只扫 5 遍，
 * 而结果是**精确期望**，不是启发式。
 *
 * ## Fever
 *
 * 谱面里 `cmd_fever_start/end.wav` 之间（闭区间）的每颗音符**分数翻倍**。
 * 漏掉这一项会系统性低估近三成（实测 +27.6% ~ +29.4%）。
 *
 * @param {object[]} team   5 张卡，`team[0]` 为队长（C 位）
 * @param {object}   parsed `parseChart` 的结果
 * @param {object}   ctx    {skills, level, songLevel, charRank, area, event, judge, optimizeOrder}
 * @returns {{score:number, avgScore:number, maxScore:number, power:number,
 *            bestOrder:number[]}}
 */
export function scoreTeam(team, parsed, ctx) {
  const notes = parsed.notes || [];
  const n = notes.length;
  if (!n) {
    return { score: 0, avgScore: 0, maxScore: 0, power: 0, bestOrder: [] };
  }

  // ⚠️ 综合力必须**先向下取整**再计分 —— Bestdori 的 `ct()` 拿到的 bp 就是
  // `Math.floor(五张卡 power 之和)`（见 ToolTeamBuilder 的 `e.bp`）。
  // 不取整会多出 0.1~0.6 点综合力，分数上差几十（相对 0.0001%，但没理由不对齐）。
  // `ctx.power` 可以直接覆盖，供对拍时锁定同一个值。
  const power = ctx.power ?? Math.floor(totalPower(team, ctx));
  const songLevel = ctx.songLevel ?? 26;
  const levelBonus = 1 + (songLevel - 5) * 0.01;
  const basePerNote = (power * levelBonus * 3 * SCORE_SCALE) / n;
  const judge = ctx.judge ?? JUDGE.perfect;

  // ① 每 NOTE 的整数基础分（含 combo 与 fever，不含技能）
  //    ⚠️ `comboBonus` 返回的是**加成值**（0.00~0.11），要自己加 1
  //
  // 这里**不逐音符存**，而是折叠成「连续段」：同一段内 combo 率与 fever 都不变，
  // 所以 `floor(basePerNote * (1+combo) * fever * judge)` 是**常数**。
  // 实测任何谱面都只有 13 段（combo 表 12 档 + Fever 边界），与音符数无关 ——
  // 逐音符的 O(n) 循环因此塌成 O(13)，整支队计分快一个数量级。
  //
  // 这不是近似：段内每个音符取值完全相同，`count * v` 与逐个累加逐位相等（全整数）。
  const groups = [];
  {
    let start = 0;
    let v = Math.floor(basePerNote * 1 * (notes[0].fever ? 2 : 1) * judge);
    let rate = 1 + comboBonus(1);
    let fever = notes[0].fever ? 2 : 1;
    for (let i = 1; i < n; i++) {
      const r = 1 + comboBonus(i + 1);
      const f = notes[i].fever ? 2 : 1;
      if (r === rate && f === fever) continue;
      groups.push({ start, end: i, v });
      start = i; rate = r; fever = f;
      v = Math.floor(basePerNote * r * f * judge);
    }
    groups.push({ start, end: n, v });
  }
  let baseSum = 0;
  for (const g of groups) baseSum += (g.end - g.start) * g.v;

  // ② 触发点 → ③ 每个 (窗口, 成员) 的整数增分
  const slots = skillSlots(ctx.event);
  const level = ctx.level ?? 5;
  const trig = triggerIndices(notes, slots);
  const K = trig.length;

  // 成员的技能倍率 / 持续时长只跟卡有关，跟窗口无关，先算一次
  const mulArr = [];
  const durArr = [];
  for (const card of team) {
    const sk = ctx.skills?.[String(card.sk)] || {};
    mulArr.push(1 + (sk.up || 0) / 100);
    durArr.push(skillDuration(sk, level));
  }
  // 最后一个 `time <= t` 的下标（`time` 非递减，二分）。等价于原来「扫到超时就停」。
  const lastIdxAtOrBefore = (t) => {
    let lo = 0, hi = n - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (notes[mid].time <= t) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    return ans;
  };

  const gain = [];
  for (let w = 0; w < K; w++) {
    // 窗口终点：下个触发点之前的最后一颗（Bestdori 在下一颗触发点处换窗口）
    const last = w + 1 < K ? trig[w + 1] : n - 1;
    const a = trig[w] + 1;   // 触发音符**自己不吃**这次技能
    const t0 = notes[trig[w]].time;
    const row = new Array(team.length).fill(0);
    for (let m = 0; m < team.length; m++) {
      const b = Math.min(last, lastIdxAtOrBefore(t0 + durArr[m]));
      if (b < a) continue;
      const mul = mulArr[m];
      let sum = 0;
      for (const g of groups) {
        if (g.end <= a) continue;
        if (g.start > b) break;
        const lo = g.start > a ? g.start : a;
        const hi = g.end - 1 < b ? g.end - 1 : b;
        sum += (Math.floor(g.v * mul) - g.v) * (hi - lo + 1);
      }
      row[m] = sum;
    }
    gain.push(row);
  }

  // ④ 120 种顺序的期望（查表，不重扫谱面）
  const perms = permutations5([0, 1, 2, 3, 4]);
  let total = 0;
  let max = -Infinity;
  let bestPerm = perms[0];
  for (const perm of perms) {
    let s = baseSum;
    for (let w = 0; w < K; w++) {
      // 前 5 个窗口按顺序；第 6 个固定是队长 team[0]
      s += gain[w][w < 5 ? perm[w] : 0] || 0;
    }
    total += s;
    if (s > max) { max = s; bestPerm = perm; }
  }
  const avg = total / perms.length;

  return {
    score: Math.floor(ctx.optimizeOrder ? max : avg),
    avgScore: avg,
    maxScore: max,
    power,
    bestOrder: bestPerm,
  };
}

// ---------------------------------------------------------------------------
// 活动点数
// ---------------------------------------------------------------------------

/**
 * 火（ブースト）倍率。索引 = 消耗的火数 0/1/2/3。
 *
 * 1 火 = 5 倍是 GBP 一直没变的设定；0 火（不消耗）是 1 倍。
 * 这张表照抄 Bestdori 的 `[1,5,10,15]`。
 */
const FLAME_MULT = [1, 5, 10, 15];

/**
 * 国服点数公式的版本分界线（unix 秒）。
 *
 * Bestdori 组队工具按「活动开始时间」选公式版本：
 *
 * ```js
 * eventFormula = [各服阈值].findIndex(t => t > startAt)
 * ```
 *
 * 这里取国服的两条线：2019-03-29 之前是 V1（V0 在国服没出现过），
 * 2023-01-18 起是 V2。本站 327 期活动横跨 2019-06 ~ 2026-10，
 * 184 期走 V1、143 期走 V2，两个都得实现。
 */
const CN_FORMULA_CUT = [1553821200, 1674003600];

/**
 * 这期活动用的是第几版点数公式。
 * @param {object} event
 * @returns {0|1|2}
 */
export function formulaVersion(event) {
  const s = Number(event?.s) || 0;
  if (!s) return 2;
  return s < CN_FORMULA_CUT[0] ? 0 : s < CN_FORMULA_CUT[1] ? 1 : 2;
}

/**
 * 分段软上限（Bestdori 的 `calcWithSoftCap`）。
 *
 * 分数超过阈值后，超出部分的「每 unit 分换多少点数」逐段打折：
 * 第 s 段按 `1/(s+1)` 折算。V0/V1 时代用这个压制高分溢出，
 * V2 改成硬上限（直接 floor(score/U)），不再打折。
 *
 * @param {number} t        分数
 * @param {number} unit     每 unit 分换 1 点
 * @param {number[]} cuts   各段阈值，末位通常是 Infinity
 */
function softCap(t, unit, cuts) {
  let r = 0;
  for (let i = 0; i < cuts.length; i++) {
    const n = cuts[i];
    if (!(t > n)) {
      r += Math.floor((t / unit / (i + 1)) * 100);
      break;
    }
    t -= n;
    r += Math.floor((n / unit / (i + 1)) * 100);
  }
  return Math.floor(r / 100);
}

/**
 * 一次 LIVE 能拿到的活动点数。
 *
 * ## 公式来源
 *
 * 下面每一条都对应 Bestdori 组队工具源码里的一个函数（`ToolTeamBuilder.js`
 * 的 `calculateMaximize` + `calcEPVS` / `calcEPFestival` / `calcEPCL`），
 * 不是社区口口相传的近似值。早期版本用「(50 + min(分数/1万,150)) × (1+加成)」
 * 一个式子套所有活动，误差大到会改变队伍排序。
 *
 * ## 通用（传统 / 任务 / 试炼）—— `W + 分数项`
 *
 * ```
 * V2: base = W + floor(score/U) + floor(teamScore/U/10)
 * V1: base = W + softCap(score, U, [160万,15万,25万,40万])
 *          + softCap(teamScore, 10U, [640万,60万,100万,160万])
 * V0: base = W + floor(min(score,150万)/U)
 *          + floor((min(score+teamScore,750万) - min(score,150万))/U/10)
 * ```
 *
 * | 活动 | W（V0/V1 → V2） | U（V0/V1 → V2） |
 * |------|-----------------|-----------------|
 * | 传统 story | 50 | 10,000 |
 * | 任务 mission_live | 40 → **120** | 10,000 → **15,000** |
 * | 试炼 live_try | 40 → **130** | 13,000 → **26,000** |
 *
 * 最后 `pt = floor(base × (1 + Σ加成)) × 火倍率`。
 *
 * ⚠️ **Σ加成 是 5 张卡的加成求和，不是平均！** 游戏结算界面写的
 * 「活动加成 +325%」就是 5 张卡相加，PT 直接乘 4.25。早期实现用平均值，
 * 把点数低估了约 5 倍。
 *
 * ## 竞演（versus）
 * ```
 * V2: base = floor(score/6500) + [200,173,146,123,100][名次-1]
 * pt = base × 火倍率
 * ```
 * **这里不再乘加成** —— 竞演的加成已经进了综合力，再乘一遍等于算两次。
 *
 * ## FES（festival）
 * ```
 * V2: base = floor(score/6500) + 50 + (胜 ? 125 : 0) + [125,117,110,105,100][名次-1]
 * ```
 *
 * ## 挑战（challenge）
 * ```
 * V2: base = floor(score/450) + 3250
 * pt = base × [1,2,4][CP]
 * ```
 *
 * ## 组曲（medley）
 * Bestdori 组队工具**没有实现组曲点数**（`eventType 0` 时 W/U 为空），
 * 所以这一支仍用社区口径：
 * ```
 * pt = (score/18500 + 30|65|100) × (1 + Σ加成) × BoostBonus
 * ```
 *
 * @param {number} score 打歌分数
 * @param {object} event 活动数据（需要 `ty`；`s` 用来判断公式版本）
 * @param {object} [opt]
 * @param {number} [opt.bonusSum]  5 张卡的活动加成**合计**（百分数，如 325）
 * @param {number} [opt.flame]     火数 0~3（默认 1 火 = ×5）
 * @param {number} [opt.flameMult] 直接给火倍率（覆盖 `flame`）
 * @param {number} [opt.boost]     旧写法，等价于 `flameMult`
 * @param {number} [opt.teamScore] 队友分数合计（协力时是另外 4 人的分数和）
 * @param {number} [opt.rank]      名次索引 0~4（竞演 / FES）
 * @param {boolean} [opt.win]      FES 是否获胜
 * @param {number} [opt.cp]        挑战 CP 档位 0~2
 * @param {number} [opt.medleySongs] 组曲曲数 1~3
 * @param {number} [opt.boostSum]  组曲各曲火倍率之和
 * @returns {number}
 */
/**
 * 支援乐队（Mission Live 的「组合加成」）。
 *
 * 规则来自 Bestdori `ToolTeamBuilder.js` 的 `X()`，逐字对照：
 *
 * ```js
 * X = function (t) {                       // t = 主队
 *   var e = [], a = [], r = t.map(c => c.collection.id), s = [], n = 0, l = 0;
 *   while (e.length < 5 && l < F.length) {  // F = 按 bpSupport 降序排好的全卡池
 *     var i = F[l];
 *     if (s.includes(i.apiCard.characterId)) continue;  // 同角色不要
 *     if (r.includes(i.id)) continue;                   // 主队的卡不要
 *     if (a.includes(i.id)) continue;                   // 重复卡不要
 *     e.push(i); n += i.bpSupport; l++;
 *   }
 *   return { bp: n, team: e };
 * };
 * ```
 *
 * 三个要点：
 *
 * 1. **支援综合力不含区域道具** —— `bpSupport` 只传了角色评级和活动加成，
 *    道具是主队共用的，支援队吃不到。
 * 2. **贪心**：按单卡综合力降序，取满 5 个不同角色为止。不是最优匹配，
 *    是 Bestdori 的做法，照抄才能对拍。
 * 3. **贡献 = `floor(综合力 / 3000)` 点**，且**不被活动加成放大**
 *    （`e.ep += Math.floor(e.supportBP / 3e3)` 在 `e.ep *= (1+bonus)` 之后）。
 *
 * @param {object[]} pool     全部可用卡
 * @param {object[]} mainTeam 主队 5 张（这些卡不能进支援队）
 * @param {object}   ctx      {event, charRank, charPotential, charMission, characters}
 * @returns {{team:object[], power:number, points:number}}
 */
/**
 * 预算「每张卡的支援综合力」并按降序排好。
 *
 * 支援队的选择只跟「卡的 solo 综合力」有关（不吃道具），跟主队无关 ——
 * 所以全池算一次就够。`bestTeams` 要对多支候选队算支援，用这个避免重复计算
 * （2490 张 × 每队一次 = 太慢）。
 *
 * @param {object[]} pool 全部可用卡
 * @param {object}   ctx  {event, charRank, charPotential, charMission}
 * @returns {{c:object, ch:number, power:number}[]} 按 power 降序
 */
export function prepareSupportPool(pool, ctx = {}) {
  const solo = { ...ctx, area: null, areaBonus: undefined };
  const out = [];
  for (const c of pool || []) {
    const ch = Number(c.ch);
    if (!ch || !c.sx) continue;
    out.push({ c, ch, power: totalPower([c], solo) });
  }
  out.sort((a, b) => b.power - a.power);
  return out;
}

export function pickSupportTeam(pool, mainTeam, ctx = {}) {
  const mainIds = new Set((mainTeam || []).map((c) => Number(c.id)));

  // 已经过 `prepareSupportPool` 的池子直接复用，否则现算
  let scored;
  if (pool && pool.length && pool[0].c && typeof pool[0].power === 'number') {
    scored = pool;
  } else {
    scored = prepareSupportPool(pool, ctx);
  }

  // ⚠️ **先排序、后去重** —— 顺序不能换。
  // 先按角色去重会把「卡号最小」的那张留下（池子是卡号升序），实测选中
  // 一批 2★（16,765）而不是三围最高的 5★（46,200），支援综合力少 65%。
  // Bestdori 的 `X()` 也是 `F.sort()` 之后才在 while 里跳重复角色。
  const team = [];
  const usedChar = new Set();
  const usedCard = new Set();
  for (const x of scored) {
    if (team.length >= 5) break;
    if (mainIds.has(Number(x.c.id))) continue;
    if (usedChar.has(x.ch) || usedCard.has(Number(x.c.id))) continue;
    usedChar.add(x.ch);
    usedCard.add(Number(x.c.id));
    team.push(x);
  }

  const power = team.reduce((a, x) => a + x.power, 0);
  return {
    team: team.map((x) => x.c),
    power,
    points: Math.floor(power / 3000),
  };
}

/**
 * 活动点数。
 *
 * @param {number} score 单场得分
 * @param {object} event 活动对象
 * @param {object} opt   {formula, flame, boost, bonusSum, teamScore, rank, win, cp,
 *                        supportPower, ...}
 *
 * `supportPower` 只对 **mission_live** 生效 —— 那是唯一有支援乐队的活动类型。
 * Bestdori 的 `e.ep += Math.floor(e.supportBP / 3e3)` 出现在 `calculateMaximize`
 * 的 mission_live 分支，其它类型没有这一项。
 */
export function eventPoints(score, event, opt = {}) {
  const ty = event?.ty || 'story';
  const v = opt.formula ?? formulaVersion(event);
  const flameMult = opt.flameMult
    ?? (opt.flame != null ? (FLAME_MULT[opt.flame] ?? 5) : (opt.boost ?? 5));
  // 加成是**求和**（5 张卡相加）。旧参数名 `bonus` 也按求和解释。
  const bonusSum = (opt.bonusSum ?? opt.bonus ?? 0) / 100;
  const teamScore = opt.teamScore ?? 0;

  if (ty === 'versus') {
    const rank = Math.min(Math.max(opt.rank ?? 0, 0), 4);
    const base = v <= 1
      ? softCap(score, 5500, [2100000, 150000, 250000, Infinity]) + [60, 52, 44, 37, 30][rank]
      : Math.floor(score / 6500) + [200, 173, 146, 123, 100][rank];
    return Math.floor(base * flameMult);
  }

  if (ty === 'festival') {
    const rank = Math.min(Math.max(opt.rank ?? 0, 0), 4);
    const win = opt.win ?? true;
    const base = v <= 1
      ? softCap(score, 5500, [2625000, 187500, 312500, Infinity])
        + 20 + (win ? 50 : 0) + [50, 47, 44, 42, 40][rank]
      : Math.floor(score / 6500) + 50 + (win ? 125 : 0) + [125, 117, 110, 105, 100][rank];
    return Math.floor(base * flameMult);
  }

  if (ty === 'challenge') {
    const base = v <= 1
      ? softCap(score, 300, [2100000, 150000, 250000, Infinity]) + 1000
      : Math.floor(score / 450) + 3250;
    return Math.floor(base * ([1, 2, 4][opt.cp ?? 0] ?? 1));
  }

  if (ty === 'medley') {
    const songs = opt.medleySongs ?? 3;
    const medleyBonus = songs >= 3 ? 100 : songs === 2 ? 65 : 30;
    const boostSum = opt.boostSum ?? 15;   // 3 首各 3 火 = 5+5+5
    // ⚠️ **不乘加成**：组曲的加成作用于综合力（`ab` 全是 `(0,20)`），
    // 已经通过变高的 `score` 体现，再乘一遍等于算两次。
    // 这个公式本身是社区口径（Bestdori 没实现组曲点数），可信度低于前几套。
    return Math.floor((score / 18500 + medleyBonus) * boostSum);
  }

  // ---- 传统 / 任务 / 试炼 ------------------------------------------------
  const [W, U] = ty === 'mission_live' ? (v <= 1 ? [40, 1e4] : [120, 15000])
    : ty === 'live_try' ? (v <= 1 ? [40, 13e3] : [130, 26000])
      : [50, 1e4];

  let base;
  if (v === 0) {
    const t = Math.min(1.5e6, score);
    const a = Math.min(7.5e6, score + teamScore);
    base = W + Math.floor(t / U) + Math.floor((a - t) / U / 10);
  } else if (v === 1) {
    base = W + softCap(score, U, [1.6e6, 1.5e5, 2.5e5, 4e5, Infinity])
      + softCap(teamScore, 10 * U, [6.4e6, 6e5, 1e6, 1.6e6, Infinity]);
  } else {
    base = W + Math.floor(score / U) + Math.floor(teamScore / U / 10);
  }

  // 先乘加成 → 再加支援 → 最后乘火。
  //
  // 顺序照抄 Bestdori：
  //   e.ep = Math.floor(e.ep * (1 + e.bonus));
  //   e.ep += Math.floor(e.supportBP / 3e3);
  // 支援项**不被活动加成放大**（放在乘加成之后），但**在火倍率之内**
  // （实测一局：663 × 40 = 26,520，663 里含支援的 78）。
  const withBonus = Math.floor(base * (1 + bonusSum));
  const support = ty === 'mission_live' && opt.supportPower > 0
    ? Math.floor(opt.supportPower / 3000)
    : 0;
  return Math.floor((withBonus + support) * flameMult);
}

// ---------------------------------------------------------------------------
// 组队
// ---------------------------------------------------------------------------

/**
 * 构造组队用的候选池（`bestTeams` 的第一阶段）。
 *
 * 单独抽出来是为了**让验证脚本用同一份输入**。原先 `team_prune_check.mjs`
 * 自己抄了一份候选池构造逻辑，两边一旦不同步，对拍就变成「拿 A 的输入比 B 的
 * 输出」—— 看着全绿，其实什么都没证明。现在两边都调这个函数。
 *
 * @param {object[]} pool
 * @param {object}   ctx
 * @param {{poolLimit?:number}} [opt]
 * @returns {object[]} 去重后的候选卡数组
 */
export function buildCandidatePool(pool, ctx = {}, opt = {}) {
  const ev = ctx.event || null;
  const limit = opt.poolLimit ?? POOL_LIMIT;
  const memberIds = new Set((ev?.mb || []).map(Number));

  // ⚠️ **不能只按三围取前 N**：活动加成卡的属性/角色不一定是三围最高的，
  // 单靠三围过滤会把它们整批筛掉，最后算出的队伍一点加成都不吃（实测踩过）。
  //
  // 候选池 = 三围最高的 N 张 ∪ **全部成员卡** ∪ 其余加成卡里最强的若干张。
  //
  // 「全部成员卡」这一条是后加的，很关键：成员卡（本期活动卡）往往不是三围
  // 最高的那一批，但它是唯一能吃到「成员 +20%」的卡。漏掉它，算法就只能推荐
  // 高综合力的通用卡 —— 而实测（视频里的对照）恰恰是「5 张都吃 +20% 的低综合力
  // 队」拿到的 PT 更多。
  const withSum = pool.map((c) => ({
    c, sum: (c.sx || [0, 0, 0]).reduce((a, b) => a + b, 0),
  })).sort((a, b) => b.sum - a.sum);

  const picked = new Map();
  const charCnt = new Map();
  // 同一个角色最多放 2 张进候选池：既要留出「同角色强弱卡二选一」的余地，
  // 又不能让它霸占名额 —— 游戏里一队 5 人必须是 5 个**不同角色**，池子里
  // 全是同一个角色的话，连一个合法组合都凑不出来（实测：某角色有 40 多张
  // 高分卡时，候选池 12 张全被它占满，枚举结果为空）。
  const CAP_PER_CHAR = 2;
  const tryPick = (card) => {
    const k = card.ch;
    const used = charCnt.get(k) || 0;
    if (used >= CAP_PER_CHAR) return false;
    charCnt.set(k, used + 1);
    picked.set(card.id ?? card, card);
    return true;
  };

  // ① 先保证角色多样性：每个角色先各取 1 张（三围从高到低）
  const sumSlots = ev ? Math.max(8, limit - 4) : limit;
  for (const x of withSum) {
    if (picked.size >= sumSlots) break;
    if ((charCnt.get(x.c.ch) || 0) === 0) tryPick(x.c);
  }
  // ② 再按三围补满剩余名额
  for (const x of withSum) {
    if (picked.size >= sumSlots) break;
    tryPick(x.c);
  }

  if (ev) {
    const scored = withSum
      .map((x) => ({ ...x, bonus: eventBonusFor(x.c, ev, 0).bonus }))
      .filter((x) => x.bonus > 0);

    // ③ 成员卡：全进（每期只有 3~10 张，且加成最高，不能漏）
    for (const x of scored) {
      if (memberIds.has(Number(x.c.id))) picked.set(x.c.id ?? x.c, x.c);
    }

    // ④ 其余加成卡：按「加成优先、三围次之」取前 BONUS_LIMIT 张
    const others = scored
      .filter((x) => !memberIds.has(Number(x.c.id)))
      .sort((a, b) => (b.bonus - a.bonus) || (b.sum - a.sum))
      .slice(0, BONUS_LIMIT);
    for (const x of others) picked.set(x.c.id ?? x.c, x.c);
  }

  return [...picked.values()];
}

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
  // ⚠️ 排序口径必须跟「有没有活动」绑定。
  //
  // 早期默认按**分数**排：结果是「综合力最高的 5 张」永远排在最前，而真正能拿
  // 活动点数的「档期卡队」被挤出 TopN —— 这正是视频里演示的误区（综合力 38006
  // 的队 PT 反而更低）。有活动就必须按 points 排，分数只是中间量。
  const sortBy = opt.sortBy || (ctx.event ? 'points' : 'score');
  // 给了实际道具等级就**自动为每支队挑最优道具**，而不是全队共用一个手填值
  const itemLevels = opt.itemLevels || null;
  const chars = opt.characters || ctx.characters || null;

  // 候选池构造见 `buildCandidatePool` —— 抽出去是为了让验证脚本用**同一份输入**。
  const ev = ctx.event || null;

  // 支援乐队（Mission Live 专属）：池子按 solo 综合力排一次就够，跟主队无关。
  // 只有 mission_live 有点数上的支援项，别的活动不算 —— 算了也是白算。
  const supportPool = ev?.ty === 'mission_live' && opt.supportBand !== false
    ? prepareSupportPool(pool, ctx)
    : null;

  const ranked = buildCandidatePool(pool, ctx, opt);

  // ---------------------------------------------------------------------------
  // 两阶段枚举
  //
  // 精确算一支队要遍历 6 个窗口 × 5 名成员，每格扫一遍音符；候选池 23 张时
  // C(23,5)=3.4 万组 × 2 个 C 位，全量跑要十几秒 —— 浏览器里就是「点了没反应」。
  //
  // 所以先用**便宜的上界**粗排一遍，只把最有希望的 PRECISE_LIMIT 组交给精确
  // 计算。上界要覆盖三围、技能强度、活动加成三项（只按三围排会漏掉技能怪）。
  //
  // ⚠️ 这是**启发式截断，不是有证明的剪枝**：`roughKey` 里的技能覆盖率
  // （0.35）和判定折扣（0.9）都是估算值，代理值排序错了就会把真正的最优解
  // 切掉。`scripts/team_prune_check.mjs` 用全量枚举盯着这条，目前 3 期活动
  // 差距 0.00%。要更强保证就得把 `roughKey` 换成真正的上界（见开发与验证.md
  // 的「编组算法」一节），代价是上界变松后能剪掉的变少。
  // ---------------------------------------------------------------------------
  const PRECISE_LIMIT = opt.preciseLimit ?? 600;
  const skillsTbl = ctx.skills || {};
  const bonusCache = new Map();
  // 星光等级从卡对象上取（导入档案时有 master 字段）。缺省 0 = 没升星光，
  // 这是保守估计 —— 宁可算低，也不要凭空给 15%。
  const bonusOf = (c) => {
    const k = c.id ?? c;
    if (!bonusCache.has(k)) {
      bonusCache.set(k, ev ? eventBonusFor(c, ev, c.master ?? 0).bonus : 0);
    }
    return bonusCache.get(k);
  };
  const upOf = (c) => (skillsTbl[String(c.sk)]?.up || 0) / 100;

  const results = [];
  const n = ranked.length;
  const total = n >= 5
    ? (n * (n - 1) * (n - 2) * (n - 3) * (n - 4)) / 120
    : 0;
  const onProgress = opt.onProgress || null;

  /** 粗排用的上界：三围 → 近似分数 → 近似 PT（有活动时按 PT 排，和最终口径一致） */
  const levelBonus = 1 + ((ctx.songLevel ?? 26) - 5) * 0.01;
  // 角色评级在游戏里是「潜能解放 5% + 角色任务 6%」分别向下取整，
  // 粗排也必须按同一口径拆开，否则代理值和精确值在边界上会打架。
  const charRank = ctx.charRank ?? 0;
  const potRank = ctx.charPotential ?? charRank * (5 / 11);
  const misRank = ctx.charMission ?? charRank * (6 / 11);
  const flameOpt = ctx.flame != null ? { flame: ctx.flame } : { boost: ctx.boost ?? 5 };
  // 综合力型活动（竞演/挑战/FES）的加成进综合力，所以粗排必须把它算进「卡面」，
  // 否则代理值和真实点数脱节 —— 实测会漏掉真正的最优解，剪枝差距到 4.7%。
  const channel = ev ? eventChannel(ev) : 'pt';
  const roughKey = (five) => {
    // 粗排必须覆盖「卡面 + 星光 + 剧情 + 角色评级」，否则满星光的通用卡会被低估
    let panel = 0;        // 纯卡面
    let weighted = 0;     // 卡面 × (1 + 单卡活动加成)
    let up = 0;
    let bonus = 0;
    for (const c of five) {
      const s = c.sx || [0, 0, 0];
      const e = c.ep || [0, 0, 0];
      const star = starStat(c);
      let cardPanel = 0;
      for (let i = 0; i < 3; i++) {
        const v = s[i] + e[i] + star;
        cardPanel += v + Math.floor(v * potRank) + Math.floor(v * misRank);
      }
      const b = bonusOf(c);
      panel += cardPanel;
      weighted += cardPanel * (1 + b / 100);
      up += upOf(c);
      bonus += b;
    }
    // 技能平均覆盖率按 35% 估（6 次发动 × 5~7 秒 ≈ 35 秒 / 约 100 秒曲子），
    // 0.9 是判定/combo 的保守折扣 —— 只要单调就行，不用和真实分数等值
    const base = channel === 'param' ? weighted : panel;
    const approx = base * levelBonus * 3 * SCORE_SCALE * (1 + (up / 5) * 0.35) * 0.9;
    if (!ev) return approx;
    // 有活动时直接用点数排序：光按分数排会把「分数略高但加成低很多」的队排前面
    // （正是要避免的误区）。加成要传**合计**，和结算口径一致。
    return eventPoints(approx, ev, { bonusSum: bonus, ...flameOpt });
  };

  const cands = [];
  let done = 0;
  let skippedDup = 0;
  for (let a = 0; a < n - 4; a++) {
    for (let b = a + 1; b < n - 3; b++) {
      for (let c = b + 1; c < n - 2; c++) {
        for (let d = c + 1; d < n - 1; d++) {
          for (let e = d + 1; e < n; e++) {
            done += 1;
            if (done % 4000 === 0) {
              if (onProgress) onProgress(0.6 * done / total);
              await new Promise((r) => setTimeout(r, 0));
            }
            const five = [ranked[a], ranked[b], ranked[c], ranked[d], ranked[e]];
            // ⚠️ 游戏规则：**同一角色一队只能上一张卡**。不排除的话，某个角色
            // 恰好有多张高分卡（5★/4★/3★ 同角色）时，算法会选出「两张香澄」这种
            // 游戏里根本组不出来的队 —— 分数还特别高，看起来像最优解。
            const k0 = five[0].ch, k1 = five[1].ch, k2 = five[2].ch,
                  k3 = five[3].ch, k4 = five[4].ch;
            if (k0 === k1 || k0 === k2 || k0 === k3 || k0 === k4
                || k1 === k2 || k1 === k3 || k1 === k4
                || k2 === k3 || k2 === k4 || k3 === k4) {
              skippedDup += 1;
              continue;
            }
            cands.push({ five, key: roughKey(five) });
          }
        }
      }
    }
  }
  cands.sort((x, y) => y.key - x.key);
  const shortlist = cands.slice(0, PRECISE_LIMIT);
  cands.length = 0;   // 释放，几万个对象占内存
  // 调试用：有多少组合因为「同角色重复」被排除（正常应占少数）
  if (opt.onStats) opt.onStats({ combos: total, skippedDup, shortlist: shortlist.length });

  // 真正最优解在粗排序列里排第几。把 `preciseLimit` 开到全量（比如 1e9）跑一次，
  // 这个数就是「截断余量」：排名远小于 600 说明启发式很稳，接近 600 就该警惕。
  let bestIdx = -1;
  let bestSeen = -Infinity;

  for (let i = 0; i < shortlist.length; i++) {
    const five = shortlist[i].five;
    if (i % 80 === 0) {
      if (onProgress) onProgress(0.6 + 0.4 * (i / shortlist.length));
      await new Promise((r) => setTimeout(r, 0));
    }
    // 只试技能最强的 2 张当 C 位。第 6 次是队长返场，技能弱的卡当队长一定更差；
    // 全试 5 张等于把枚举量乘 5，收益却为零。
    const bySkill = [...five].sort((x, y) => upOf(y) - upOf(x));
    const capTry = 2;
    let best = null;
    for (const capCard of bySkill.slice(0, capTry)) {
      const team = [capCard, ...five.filter((x) => x !== capCard)];
      // 每支队单独算道具：同一套道具不可能对所有队伍都最优
      let ctx2 = ctx;
      let areaPick = null;
      if (itemLevels) {
        // 权重用**真实卡面**（三围 + 剧情 + 星光 + 角色评级），
        // 而不是原始三围 —— 比较「专有 vs 共通」时差的就是这一块。
        const w = team.map((c) => {
          const s = c.sx || [0, 0, 0];
          const e = c.ep || [0, 0, 0];
          const star = starStat(c);
          let t = 0;
          for (let i = 0; i < 3; i++) {
            const v = s[i] + e[i] + star;
            t += v + Math.floor(v * potRank) + Math.floor(v * misRank);
          }
          return t || 1;
        });
        areaPick = pickAreaItems(team, itemLevels, chars, opt.itemDetail, w);
        ctx2 = { ...ctx, area: areaPick.area, characters: chars };
      }
      const r = scoreTeam(team, parsed, ctx2);
      if (!best || r.score > best.score) {
        best = { team, ...r, area: areaPick?.area, bandSlot: areaPick?.bandSlot };
      }
    }
    const row = { team: best.team, score: best.score, power: best.power };
    // 活动加成：逐卡算，存一份**合计**（游戏结算界面显示的就是这个数）和一份
    // 平均（方便看「平均每张卡吃多少」）。
    // ⚠️ 进点数公式的必须是**合计**：PT = base × (1 + Σ加成)，5 张 65% 的队
    // 就是 ×4.25。早期版本传了平均值，点数被低估约 5 倍。
    if (ev) {
      const bonuses = best.team.map((c) => bonusOf(c));
      row.bonusSum = Math.round(bonuses.reduce((a, b) => a + b, 0) * 10) / 10;
      row.bonusPct = row.bonusSum / bonuses.length;
      // 支援乐队：只在 mission_live 有点数贡献，且**不被活动加成放大**
      // （见 `eventPoints` 里 `floor(base*(1+bonus)) + floor(support/3000)` 的顺序）
      const sup = supportPool ? pickSupportTeam(supportPool, best.team, ctx) : null;
      row.supportPower = sup ? sup.power : 0;
      row.supportPoints = sup ? sup.points : 0;
      row.supportTeam = sup ? sup.team : null;
      row.points = eventPoints(best.score, ev, {
        bonusSum: row.bonusSum,
        supportPower: row.supportPower,
        ...flameOpt,
      });
      // 支援实际贡献了多少点（含火倍率）。再算一次不传支援的版本相减 ——
      // 只对 topN 队做，开销可忽略，但能让界面把点数构成讲清楚。
      row.supportAdded = row.supportPoints
        ? row.points - eventPoints(best.score, ev, { bonusSum: row.bonusSum, ...flameOpt })
        : 0;
      row.channel = eventChannel(ev);
    }
    results.push(row);
    const cmp = sortBy === 'points' ? (row.points ?? 0) : row.score;
    if (cmp > bestSeen) { bestSeen = cmp; bestIdx = i; }
  }

  if (onProgress) onProgress(1);
  // 第二次 onStats：补上 `bestRank`（第一次在枚举前，给进度用）
  if (opt.onStats) opt.onStats({ bestRank: bestIdx });
  results.sort((a, b) => {
    if (sortBy === 'points') {
      // **直接比点数**。点数公式已经把分数和加成（或综合力里的加成）都算进去了，
      // 不需要也不应该再手写「加成优先」的规则 —— 那样会错过「加成略低但分数高
      // 很多」的最优解（混合编组场景：凑不齐 5 张档期卡时，用部分档期卡提加成 +
      // 最强卡提分数）。点数相同再加成高者在前（同分时更稳）。
      const d = (b.points ?? 0) - (a.points ?? 0);
      if (d !== 0) return d;
      return (b.bonusPct ?? 0) - (a.bonusPct ?? 0);
    }
    return b.score - a.score;
  });
  return results.slice(0, topN);
}
