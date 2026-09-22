# STORY.md — BestdoriHelper 使用说明

## ① 用户意图对齐

- **目标受众**：玩《BanG Dream! 少女乐团派对》的普通玩家。场景是在群 / 贴吧 /
  视频里把工具分享给别人，或者自己边看边操作。**观众不是程序员**。
- **核心目标**：看完能回答三件事 ——「这软件是干嘛的」「我该用哪个版本」
  「具体怎么一步步操作」。看完就应该能直接上手，不需要再问人。
- **PPT 长度**：13 页。
- **视觉调性**：清爽工具感、克制、信息清楚优先。不炫技。
- **内容边界**：
  - **必讲**：三端怎么选、四步完整流程、每一步的具体操作、两种同步方式、注意点
  - **不讲**：识别算法、pHash/SIFT、指纹库怎么建、CORS 是什么、Capacitor 原理、
    构建与部署流程
  - **禁碰**：代码片段、算法名词（「指纹」「特征」「几何校验」一律不出现）、
    开发者才关心的技术取舍

> **一句话定位**：这不是技术分享，是**说明书**。

---

## ② 页面布局骨架

### 分章与页面总数

全篇 **13 页**，分 **2 章**（目录声明 2 章 → 有且仅有 2 个章节扉页）：

- **第一章「开始使用」**（第 3–9 页）：三端怎么选 → 四步流程 → 逐步操作
- **第二章「同步到 Bestdori」**（第 10–12 页）：两种同步方式

### 目录 ↔ 章节扉页契约

| 目录第 k 章 | 章节扉页所在页 | 扉页编号 | 标题（逐字一致） | 页码区间 |
| --- | --- | --- | --- | --- |
| 第 1 章 | 第 3 页 | 01 | 开始使用 | 03–09 |
| 第 2 章 | 第 10 页 | 02 | 同步到 Bestdori | 10–12 |

✅ 2 个扉页齐全、编号连续（01、02）、标题与页码区间与目录逐字一致。

### Hero 页定位

- **第 1 页**（封面）、**第 5 页**（四步走完，全篇内容核心）、**第 13 页**（结尾）
- Hero 占比 3/13 = **23%**，落在 20–30% ✓
- 任意两个 Hero 之间至少隔 1 个 Supporting：1→5 隔 3 页，5→13 隔 7 页 ✓

### rhythm 曲线

```
1 peak → 2 trans → 3 trans → 4 valley → 5 peak → 6 valley
  → 7 peak → 8 valley → 9 trans → 10 trans → 11 peak → 12 valley → 13 peak
```

- 无「连续 ≥ 3 页 valley」✓（第 6–8 页被第 7 页的 peak 打断）
- 章节切换处（3、10）用 transition ✓

### 非对称版式预算

13 页中 **11 页用非对称版式**（85%）✓ ≥ 40%

- 对称版式仅 2 页（第 3、10 页，章节扉页）—— 达到上限，不再增加 ✓

---

## ③ 页面大纲

### 第 1 页
- `title`：BestdoriHelper
- `type`：cover ｜ `role`：hero ｜ `rhythm`：peak
- `layout`：全幅图+骑线文字
- `visual`：L1: cover_bg.png（全幅背景，占 100%）
- `visual_role`：atmosphere
- `density`：字数约 30 / 图片 1 张 / 留白约 55%
- `anti_pattern`：禁止标题 + 装饰小图（200×70）；禁止铺满正文段落；禁止把标题塞进卡片
- `description`：主标题「BestdoriHelper」，副标题「把游戏截图变成 Bestdori 卡册 —— 三步搞定」。封面只做一件事：让人知道这是什么。

### 第 2 页
- `title`：这份说明讲什么
- `type`：catalog ｜ `role`：supporting ｜ `rhythm`：transition
- `layout`：左标题+右内容
- `visual`：L3: 章节序号色块（角标）
- `visual_role`：evidence
- `density`：字数约 90 / 图片 0 张 / 留白约 35%
- `anti_pattern`：禁止把目录做成四个等宽卡片；禁止章节条目只写编号不写标题
- `description`：两个章节 —— 01 开始使用（三端怎么选、四步流程、逐步操作）；02 同步到 Bestdori（安卓一键同步 / 网页版导出导入）。

### 第 3 页
- `title`：开始使用
- `type`：section ｜ `role`：transition ｜ `rhythm`：transition
- `layout`：全屏视觉+大标题
- `visual`：L1: section1_bg.png（全幅背景）
- `visual_role`：atmosphere
- `density`：字数约 20 / 图片 1 张 / 留白约 65%
- `anti_pattern`：禁止四卡片预览；禁止铺满正文段落；禁止在扉页写操作步骤
- `description`：第一章扉页，编号 01。只出现章节名。

### 第 4 页
- `title`：三个版本，怎么选
- `type`：content ｜ `role`：supporting ｜ `rhythm`：valley
- `layout`：非对称双栏（65:35）
- `visual`：L2: three_platforms.png（占右 35%）
- `visual_role`：evidence
- `density`：字数约 200 / 图片 1 张 / 留白约 25%
- `anti_pattern`：禁止 50:50 等分双栏；禁止把三端做成等宽三卡；禁止只列名称不说适用场景
- `description`：网页版免安装、打开就能用，适合快速识别后导出；安卓 App 能在手机上直接同步到 Bestdori；Windows 桌面版功能最全。**结论：想同步选安卓，图省事选网页，要功能全选桌面。**

### 第 5 页
- `title`：四步走完
- `type`：content ｜ `role`：hero ｜ `rhythm`：peak
- `layout`：巨型数字+洞察
- `visual`：L1: 巨型数字「4」（≥ 96px）+ Diagram(横向步骤条)
- `visual_role`：anchor
- `density`：字数约 80 / 图片 0 张 / 留白约 40%
- `anti_pattern`：禁止等宽卡片横排；禁止把四个步骤缩成小字列表；禁止用 L3 角标顶替 L1
- `description`：整份说明的主干 —— ① 导入截图 ② 核对修正 ③ 生成清单 ④ 导出或同步。**后面每一页展开其中一步。**

### 第 6 页
- `title`：第一步 · 导入截图
- `type`：content ｜ `role`：supporting ｜ `rhythm`：valley
- `layout`：左大图+右侧文字
- `visual`：L1: step1_import.png（占左 58%）
- `visual_role`：evidence
- `density`：字数约 180 / 图片 1 张 / 留白约 25%
- `anti_pattern`：禁止把界面图缩小成角标；禁止只写「拖入截图」四个字就收尾
- `description`：把游戏里截的卡面列表图拖进来（支持多选、也能直接选整个文件夹）。切分模式默认「自动」就够了，识别不准再改「指定行列」。**记得勾上「卡框贴合」，它能让每一格切得更准。**

### 第 7 页
- `title`：第二步 · 核对与修正
- `type`：content ｜ `role`：supporting ｜ `rhythm`：peak
- `layout`：上大图+下方卡片
- `visual`：L1: step2_compare.png（占上 60%）
- `visual_role`：anchor
- `density`：字数约 190 / 图片 1 张 / 留白约 20%
- `anti_pattern`：禁止 50:50 等分双栏；禁止把「截图格 ↔ 匹配卡图」的对照关系拆成两页
- `description`：识别结果每一行都并排显示「截图里那一格」和「匹配到的卡图」，**扫一眼就知道对不对**。认错了点候选卡片直接改，也可以在「手动指定卡牌」里搜；实在拿不准就「忽略这一格」。点缩略图能看大图。

### 第 8 页
- `title`：第三步 · 卡面清单
- `type`：content ｜ `role`：supporting ｜ `rhythm`：valley
- `layout`：左标题+右内容
- `visual`：L2: step3_inventory.png（占右 45%）
- `visual_role`：evidence
- `density`：字数约 170 / 图片 1 张 / 留白约 25%
- `anti_pattern`：禁止把统计数字做成四张等宽卡片；禁止只列按钮不解释用途
- `description`：清单自动汇总总数、特训后、4★5★、待确认。顶部能按卡名 / 角色搜索，也能一键筛出「待确认」。**低置信度的会自动标成待确认，重点检查这些就行。**

### 第 9 页
- `title`：第四步 · 导出清单
- `type`：content ｜ `role`：supporting ｜ `rhythm`：transition
- `layout`：非对称双栏（60:40）
- `visual`：L2: step4_export.png（占右 40%）
- `visual_role`：evidence
- `density`：字数约 130 / 图片 1 张 / 留白约 30%
- `anti_pattern`：禁止 50:50 等分双栏；禁止只写格式名不说用途
- `description`：支持 CSV、JSON、ID 列表、Markdown 四种格式。**CSV 给表格软件看，ID 列表给别的工具用，Markdown 方便贴到群里分享。**

### 第 10 页
- `title`：同步到 Bestdori
- `type`：section ｜ `role`：transition ｜ `rhythm`：transition
- `layout`：全屏视觉+大标题
- `visual`：L1: section2_bg.png（全幅背景）
- `visual_role`：atmosphere
- `density`：字数约 20 / 图片 1 张 / 留白约 65%
- `anti_pattern`：禁止四卡片预览；禁止铺满正文段落；禁止在扉页解释技术原因
- `description`：第二章扉页，编号 02。只出现章节名。

### 第 11 页
- `title`：方式一 · 安卓 App 一键同步
- `type`：content ｜ `role`：supporting ｜ `rhythm`：peak
- `layout`：非对称双栏（60:40）
- `visual`：L1: sync_app.png（占右 40%）+ Diagram(四步流程)
- `visual_role`：anchor
- `density`：字数约 150 / 图片 1 张 / 留白约 30%
- `anti_pattern`：禁止把「登录 → 选档案 → 生成计划 → 开始导入」四步做成等宽四卡
- `description`：装好 App 后：登录 Bestdori → 选一份云端档案 → 点「生成导入计划」先看要写什么 → 确认后「开始导入」。**导入前会弹确认框，不会直接动手。**

### 第 12 页
- `title`：方式二 · 网页版导出档案导入
- `type`：content ｜ `role`：supporting ｜ `rhythm`：valley
- `layout`：左标题+右内容
- `visual`：L2: sync_web.png（占右 45%）
- `visual_role`：evidence
- `density`：字数约 160 / 图片 1 张 / 留白约 28%
- `anti_pattern`：禁止用「因为浏览器同源策略」这类技术解释开头；禁止把操作步骤藏在小字里
- `description`：网页版点「导出 Bestdori 档案」→ 复制内容 → 打开 bestdori.com/profile/manager → 点 Import → 粘贴。**账号密码不经过任何第三方。**

### 第 13 页
- `title`：现在就去试
- `type`：ending ｜ `role`：hero ｜ `rhythm`：peak
- `layout`：全幅图+骑线文字
- `visual`：L1: ending_bg.png（全幅背景）
- `visual_role`：atmosphere
- `density`：字数约 60 / 图片 1 张 / 留白约 55%
- `anti_pattern`：禁止「感谢观看」四个字单独占一页；禁止塞二维码以外的多余信息
- `description`：网页版地址 `frankiesondesu.github.io/BestdoriHelper`，打开就能用，不用装东西。安卓 App 和 Windows 版在 Releases 里下载。

---

## Checklist 自检

- ✅ Hero 页 3 个（23%），落在 20–30%
- ✅ 无「连续 ≥ 3 页 supporting + valley」
- ✅ `N卡片横排` 出现 0 次
- ✅ 非对称版式 11/13 = 85% ≥ 40%
- ✅ 相邻两页版式均不同
- ✅ `左大图+右侧文字`(1) + `非对称双栏`(3) = 4/13 = 31% ≤ 40%
- ✅ 每页都有 `role` / `rhythm` / `visual_role` / `anti_pattern`
- ✅ 第 4、5、11 页的数值/结论都给了判断，不是纯信息板
- ✅ `type: section` 扉页 2 个 = 目录声明章节数 2
- ✅ 扉页编号连续（01、02），标题与页码区间与目录逐字一致
