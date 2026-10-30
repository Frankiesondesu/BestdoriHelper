# HHWX 可吸收经验

读的对象：[BluewaterAlnilamII/hhwx](https://github.com/BluewaterAlnilamII/hhwx)，
重点是 `crates/bandori-medley-search/`（组曲最优编组的求解器）和
`documents/bandori-team-builder/` 的三份设计文档。实际读的是 Rust 源码，
不是只读文档 —— 文档里的说法逐条对着代码核过。

---

## 一句话结论

**方法论值得吸收，数值口径不能照搬。**

HHWX 是「原生游戏口径」的实现：它用 1024 加权顺序、实现了 0.75 秒技能排队。
本站对齐的是 **Bestdori**：120 均匀顺序、无排队。两边差 0.048% / 0.10%，
都在 `docs/开发与验证.md` 里量化过。所以 HHWX 的**常数和公式不能抄**，
但它的**搜索结构和验证纪律**是这类问题里最成熟的一套，可以直接搬。

---

## 二、本轮已经吸收并落地的

### 1. combo 段折叠（`docs/src/team.js` 的 `scoreTeam`）

HHWX 的 `exact_score.rs` 里有个 `ComboGroup { start, end, rate, fever }` ——
它不逐音符算分，而是把「combo 率 + Fever 状态」相同的连续音符折成一段。
段内每音符基础分是**常数**，于是逐音符的 O(n) 循环塌成 O(段数)。

本站原来老老实实逐音符算。改完后：

| | 原来 | 现在 |
|---|---|---|
| 实测段数 | — | **13**（expert/1、expert/540、special/786、expert/325 全是 13） |
| 每音符耗时 | 0.42 µs | **0.24 µs** |
| 对拍 | 24/24 逐位相等 | **24/24 逐位相等，0.00000%** |

段数只跟 combo 表（12 档）和 Fever 边界有关，**与音符数无关** ——
所以谱面越长收益越大。这不是近似：段内取值完全相同，`count * v` 与逐个累加
逐位相等（全是整数，`Math.floor` 结果一致）。

### 2. 对拍脚本必须「同源 + 同口径」

HHWX 的测试文档反复强调：**「分数更高不代表计分正确，因为多算也会抬高结果」**。
本站 `team_prune_check.mjs` 正好踩了这条，而且踩了两层：

1. 它**自己抄了一份**候选池构造逻辑，而不是用 `bestTeams` 的那份 ——
   两边一旦不同步，对拍就变成「拿 A 的输入比 B 的输出」，看着全绿，什么都没证明。
2. 「全量精确」那条路径**漏了支援乐队**，而 `bestTeams` 已经加了。
   结果 mission_live 出现「剪枝版 PT 5135 vs 全量 4710」的 **-9.02% 假差距**，
   而脚本用 `Math.max(worst, gap)` 把负数吞掉了，还打印「✓ 最大差距 0.00%」。

改法：

- 把候选池构造抽成 `export function buildCandidatePool(pool, ctx, opt)`，
  `bestTeams` 和验证脚本都调它 —— **输入同源**。
- 全量路径也调 `pickSupportTeam` / `prepareSupportPool` —— **口径一致**。
- 负差距单独判定为「两条路径口径不一致」，直接判失败，不再当通过。

改完：3 期活动（mission_live / versus / challenge）差距全部 **0.00%**。

### 3. 新增 `scripts/team_shortlist_rank.mjs`

给 `bestTeams` 加了 `opt.preciseLimit` 和 `onStats.bestRank`：把截断阈值开到全量，
就能知道**真正的最优解在粗排序列里排第几** —— 这是量化「启发式截断有多少余量」
最省的办法，不用对每期活动做全量精确枚举。

实测（曲目 God knows... Lv.28，1081 notes，最近 6 期活动）：

| 活动 | 类型 | 合法组合数（去重后） | 最优解粗排排名 |
|---|---|---|---|
| 327 黎明的Stand By | mission_live | 12,354 | **第 0 位** |
| 326 迈向广阔世界的自由变奏 | versus | 23,482 | **第 0 位** |
| 325 All☆Stars CiRCRiNG Fes! | challenge | 38,661 | **第 0 位** |
| 324 悠然一刻、HelloHappy温泉之旅 | festival | 16,098 | **第 0 位** |
| 323 纵使千言万语亦不足以表达 | medley | 11,922 | **第 0 位** |
| 322 Re:Like Re:Live | live_try | 16,638 | **第 0 位** |

`PRECISE_LIMIT = 600` 只覆盖 1.5%~5% 的候选，而**六期活动的最优解每次都排第一**。
说明 `roughKey` 这个代理值在这些场景下非常可靠，600 的余量极大。

> 注意这**不是**「600 一定不会漏」的证明。它只是说：在这 6 期、这首谱面上，
> 代理值排序把最优解放在了第一位。换更极端的卡池（比如某期成员卡特别多、
> 加成差特别悬殊）时仍可能失效。真正的证明只有换成 HHWX 那种安全上界。

`PRECISE_LIMIT = 600` 覆盖 4.9% 的候选，而最优解每次都排第一 —— 余量很足。
（第 3 期跑到一半时环境出问题，见文末。）

---

## 三、值得吸收、本轮没做的（按性价比排序）

### A. 把「启发式截断」换成「有证明的上界剪枝」★★★

这是两者最本质的差别。

| | 本站现在 | HHWX |
|---|---|---|
| 排序依据 | `roughKey`（代理值） | 真上界 `team_upper` |
| 丢弃条件 | 排到 600 名之后 | **上界严格小于当前最好分** |
| 等分处理 | 可能被切掉 | **显式保留**（`<` 不是 `<=`） |
| 是否可证明 | 否 | 是（文档 §8 有完整因果链） |

HHWX 的剪枝长这样（`search.rs`）：

```rust
if state.incumbent_score().is_some_and(|incumbent| {
    finite_slot_sum([rows[0][zero].upper_score, maximums[1], maximums[2]]) < incumbent
}) { /* 才剪 */ }
```

代价：真上界一定比代理值松，能剪掉的会变少，可能要精确算更多组。
**前置条件**：得先有一个可信的上界。本站 `roughKey` 里的
`(1 + (up/5)*0.35) * 0.9` 是估算，不是上界 —— 用它剪枝会**算错**。

一个可行的宽松上界（可以直接推）：

```
ub = baseSum + (maxMul - 1) * Σ_{w<5} W[w] + (mul_0 - 1) * W[5]
```

`W[w]` 是第 w 个窗口的 `inner` 之和（每个音符属于且仅属于一个窗口，
所以 `Σ W[w] = baseSum`）。它是真上界（放松了「每个成员只能用一次」的
双射约束、放松了 `floor`、也放松了技能时长截断），但很松 —— 松到可能没什么剪枝力。
要做就得先实测「松多少」，再决定值不值。

**建议**：先跑 `team_shortlist_rank.mjs` 看更多期活动。如果最优解长期稳定在
前几十名，说明代理值已经够可靠，投入改造的收益不大；一旦出现排名接近 600 的期，
再动手。

### B. `exact` / `incomplete` 状态语义 ★★★

HHWX 的返回值是枚举，不是裸结果（`output.rs`）：

```rust
enum MedleySearchOutcomeV1 {
    Exact { best: Option<...>, discovered, diagnostics },
    Incomplete { reason: SearchIncompleteReasonV1, best_so_far: Option<...>, discovered, diagnostics },
}
```

`reason` 有 8 种受控原因：`Cancelled` / `TimedOut` / `MemoryExhausted` /
`InvalidData` / `ArithmeticOverflow` / `ScorerDisagreement` /
`CountOrIndexOverflow` / `InternalFailure`。

而且文档明确：`exact` + `best = null` 表示**已证明无解**，`incomplete` 两种情况都不保证；
`discovered` 列表是**诊断用，不是经过证明的全局 Top N**。

本站现在 `bestTeams` 直接返回 `topN` 数组，界面上写「最优」。但实际是
「启发式截断下的最优」—— 这个差别对用户是有意义的（尤其候选池被
`POOL_LIMIT` 限过之后）。成本很低：给返回值加一个
`{ teams, exact: boolean, reason?: string }`，UI 上标注一下。

### C. 上界要**单独**对拍，不能只对拍最终结果 ★★★

本站现在只验证「剪枝结果 vs 全量枚举结果」，从没单独验证过 `roughKey` 本身。
HHWX 专门有这类测试：**「单队上界覆盖每个合法的极小完成」** ——
用穷举的小规模完成去检查上界确实 ≥ 实际值。

这条最直接的价值：`exact_local_score` 里那句

```rust
if score > row.upper_score {
    return Err(abort(SearchIncompleteReasonV1::ScorerDisagreement));
}
```

**每次精确计分都回头校验「有没有超出上界」，超出就报 bug** ——
等于把上界安全性的断言嵌进了生产路径，跑一次搜索就顺便验一次。
一旦有了真上界，这个自检是白送的。

### D. 资源边界也要测 ★★

HHWX 的 `SearchControl` 把资源显式化：`memory_budget_bytes` + `stop_check` 回调 +
`strict_improvement` 回调。测试里专门有「**存储预算为零时返回 `incomplete`**」。

本站对应的资源边界是 `PRECISE_LIMIT` 和 `POOL_LIMIT` —— 从没测过「把它们调到很小
会怎样」。至少应该有一个断言：`preciseLimit = 1` 时函数不崩、返回合法队伍。

### E. 整数精确累加 ★★（本站已天然满足，但没写死）

HHWX 用 `i128` 累加分数，只在最后转 `f64`，并在 `upper_bound.rs` 里用
`next_up()` 两次来给浮点舍入留余量（`R(n) = 2^52/(2^52-n)`）。

本站的 `scoreTeam` 里所有中间量（`inner`、`gain`、120 次排列的累加）**全是整数**，
分数约 1.6e6 × 120 = 1.9e8，远小于 2^53 —— 所以累加是精确的，
`total / 120` 只是一次除法，和 Bestdori 的 `C/120` 逐位一致。
**这条现在是对的，但靠的是「恰好没超 2^53」**。值得在注释里写死，
并在 `team_alg_check.mjs` 里加一个 `Number.isSafeInteger(total)` 的断言。

### F. 「三条独立正确性问题」的测试分层 ★★

HHWX 把验证拆成三个**互不替代**的问题：

1. 输入与计分是否正确
2. 搜索是否完整（剪枝是否由安全上界证明）
3. 浏览器交付是否用了当前产物

并明确写着：「任何单次基准测试都不能同时回答这三个问题。分数更高不代表计分正确，
达到历史分数不代表最优，运行很快也不能证明上界安全。」

本站现在三件事混在几个脚本里。值得按这个维度重排一遍脚本清单，
每个脚本标注「这个检查证明了什么 / **没证明什么**」。

### G. 「已证明 / 假设 / 无法恢复」三级证据分类 ★

HHWX 对历史分数回归目标强制分级。本站对应的场景是：社区文档、视频、
Bestdori 源码、我们自己的实测 —— 现在混在一起说。已经踩过一次
（`SCORE_SCALE = 1.45` 拿社区锚点拟合）。可以在 `docs/开发与验证.md`
里把「依据来源分三层」那张表再明确一级：**哪些是源码逐字读出来的、
哪些是实测拟合的、哪些只是文档声称**。

---

## 四、明确不吸收的

| 内容 | 为什么不要 |
|---|---|
| 1024 加权顺序（`ORDER_WEIGHTS`，5×5 列和 1024） | 本站对齐 Bestdori 的 120 均匀。差 0.048%，但方向不同 —— 抄了就对不上 Bestdori |
| `SKILL_WAIT_SECONDS = 0.75` 技能排队 | 同上，Bestdori 没有。实测影响 0.10%（仅 0.7% 谱面触发） |
| 三队联合分配 + 分支定界 + 前后表 DP | 那是**组曲**（3 首歌 × 5 张卡 × 15 张不重复）的问题，本站是单队。复杂度差一个量级 |
| Rust + WASM + `i128` + `next_up()` | 本站 12k~24k 个组合，JS 几百毫秒。上这套是杀鸡用牛刀 |
| `design-qa.md`（76 KB） | 是 UI 设计验收记录，与算法无关 |

---

## 五、本轮改动的文件

| 文件 | 改了什么 |
|---|---|
| `docs/src/team.js` | ① `scoreTeam` 折叠成 combo 段；② 抽出 `buildCandidatePool`；③ 加 `opt.preciseLimit`；④ 加 `onStats.bestRank` |
| `scripts/team_prune_check.mjs` | 重写：同源候选池 + 支援口径一致 + 负差距判失败 |
| `scripts/team_shortlist_rank.mjs` | 新增：量化截断余量 |

回归结果（全部通过）：

| 脚本 | 结果 |
|---|---|
| `team_vs_bestdori.mjs` | **11/11** |
| `score_vs_bestdori.mjs` | 24 谱面 × 5 技能类型，**逐位相等 0.00000%** |
| `team_prune_check.mjs` | 3 期活动（mission_live / versus / challenge）**全部 0.00%** |
| `area_pick_check.mjs` | **22/22** |
| `team_alg_check.mjs` | 全部通过 |
| `team_render_check.mjs` | 全部通过 |
| `team_shortlist_rank.mjs` | 6 期活动最优解全部排第 0 位 |

折叠优化顺带也让全量枚举变快了（`team_prune_check` 里 challenge 那期
67 s → 48 s，含 CPU 争用差异，仅供参考）。

---

## 六、没有做的部分

- 上面「值得吸收但没做」的 A~G 七条，本轮都**没有实现**，只是给出了改造方向和前置条件。
- 上界剪枝（A）要动手，第一步应该是实测「宽松上界到底松多少」——
  如果松到几乎剪不掉东西，就不值得替换现在的启发式截断。
