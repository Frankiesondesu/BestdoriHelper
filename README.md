# BestdoriHelper

《BanG Dream! 少女乐团派对》玩家的卡面管理工具。

**做三件事：**

1. **批量导入截图** —— 把游戏里截的卡面列表图丢进来，自动切分出每一张卡
2. **自动识别卡面** —— 认出角色、卡名、星级、属性，生成卡牌清单
3. **同步到 Bestdori** —— 把清单写进 Bestdori 的「我的卡牌」(`/profile/cards`)

---

## 三种用法，挑一个

| 平台 | 怎么获得 | 适合 |
| --- | --- | --- |
| **网页版** | 直接打开 GitHub Pages 地址（见仓库首页 About 里的链接），无需安装 | 只想快速识别 + 导出清单 |
| **手机 App** | 到 [Releases](../../releases) 下载 `app-debug.apk` | 想在手机上直接同步到 Bestdori |
| **Windows 桌面版** | 到 [Releases](../../releases) 下载 `BestdoriHelper-win.zip`，解压后双击 `BestdoriHelper.exe` | 界面最完整（核对面板、原图缩放、候选点选） |

三个平台**共用同一套识别算法与清单格式**，只是界面不同。差异在这两点：

- **只有安卓 App 能直接同步到 Bestdori**。Bestdori 的接口不返回 CORS 头，
  浏览器里所有请求都会被拦（实测连公开的卡牌接口都拦）；网页版请在「清单」页
  导出，再拿到桌面版或 App 里导入。App 走原生 HTTP，没有同源策略，所以能直连。
- **桌面版功能最全**：核对面板、原图滚轮缩放、候选逐条点选、导出四种格式。

### 从源码跑

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
python -m bestdori_helper.gui        # 桌面界面
python -m bestdori_helper.cli --help # 命令行
```

打 Windows 包：`python scripts/build_win.py`（产物在 `dist/`）。

---

## 它是怎么认出卡面的

不依赖 OCR 认字（游戏字体、缩放、压缩都会让 OCR 翻车），而是**用 Bestdori 自己的卡图建指纹库做视觉比对**：

```
Bestdori 缩略图 (180×180 方形，thumb/chara/)
        │  多档中心裁切（100% / 90% / 75% / 60%）
        ▼
   pHash + dHash (各 64 bit)  +  HSV 颜色直方图
        ▼
   指纹库 fingerprints.cn.thumb.npz
        ▲
        │  同样抽 4 档特征，与库里全组合比对
        │
游戏截图 ──► 网格切分 ──► 每个卡面 ──► 汉明距离 + 直方图余弦 ──► 卡牌 ID
```

### 为什么用「方形缩略图」而不是卡面原图

这是这套工具里最要紧的一个选择，值得单独说。

Bestdori 上同一张卡有**两套**图：

| 资源 | 尺寸 | 取景 |
| --- | --- | --- |
| `characters/resourceset/{名}_rip/card_normal.png` | 1334×1002 横向 | 完整卡面（半身+背景） |
| `thumb/chara/card{id//50:05d}_rip/{名}_normal.png` | **180×180 方形** | **脸部/上半身特写** |

游戏里的「成员一览」等界面，卡面格是**方形脸部特写** —— 取景和后者一致，和前者差了一倍多的画幅。

一开始用的是横向原图，结果 pHash 把整图压成 32×32 看低频结构时，同一张脸在两套画幅里落在完全不同的位置，**只能"猜角色"、猜不到具体哪张卡**：置信度卡在 0.76，top-1 与 top-2 差距中位只有 0.006。

换成方形缩略图建库后（同样 140 个真实卡面格）：

| 指标 | 横向原图库 | 方形缩略图库 |
| --- | --- | --- |
| 置信度 均值 / 最大 | 0.775 / 0.811 | **0.808 / 0.918** |
| 与次优差距 中位 / 最大 | 0.006 / 0.039 | **0.013 / 0.132** |
| 可直接采信（≥0.74 且 gap≥0.02） | 10.7% | **35.7%** |

两套库**分开存放、分开建库**，互不覆盖（`--source thumb|original` 切换，默认 `thumb`）。

> 顺带踩出来的一个坑：缩略图目录名里的数字是**卡牌 ID 的 50 张分桶**，不是资源名派生出来的。`id // 50` 补零 5 位 —— id=158 → `card00003_rip`，id=2468 → `card00049_rip`。写成按资源名拼就直接 404。

**为什么要多档裁切**：游戏格与缩略图的取景其实是一致的（直接反标定得到 z\* = 1.00），
但截图本身经过缩放和 JPEG 压缩，不同界面之间还有几个像素的取景抖动。抽 4 档中心裁切后做
**全组合**比对取最优，吸收的正是这部分抖动 —— 换成固定裁切对会明显变差（37.9% → 27.9%）。

**自动切分网格**：一张截图里有多个卡面，默认 `auto` 模式会自己找出网格。做法是**自相关找等距重复结构** —— 卡牌列表本质是周期性的，无论卡框是紧贴还是有背景间隙、卡面本身有没有大片平坦区域，只要网格规整就能测出周期。实测 7 种布局（含无间隙紧贴、粗细边框、UI 叠加）全部切对。

> ℹ️ 一般**不需要**手动传 `--region`：自动模式会先定位卡面区再切（见下节）。只有自动定位失败（比如界面很特殊）时才用它手动指定：`--region x0,y0,x1,y1`（0~1 归一化）。

**按卡面实际位置切（第 10 轮）**：真实截图里卡面网格是**嵌在 UI 中间**的
（左侧菜单、顶栏、底部按钮）。只靠"等分 + 贴合"救不回来 —— 实测把整张
2400×1080 按 4×7 等分得到 343×270 的框，连菜单都算进去了；而整图等分框里全是
UI 内容，一个背景分界都找不到，贴合无从下手。GUI 之前正是这个状态（调用识别时
没传 `--region`），所以用户看到的一直是"按空格子切"。

现在 `auto` / `grid` 模式都会先做两步：

1. **自动定位卡面区**：卡面的横向间隙是一条**横贯整片卡面区的长背景带**
   （实测 x=684~1935、长 1251px），同一位置在 4 个行间隙上反复出现，而 UI
   元素不会这样重复。取最长的那条带做种子聚类，就得到卡面区范围 ——
   **不需要手动填 `--region`**。
2. **区内按背景间隙切格子**：先按行找间隙得到"卡面行"，再在每行内按列找
   间隙得到"卡面列"，交叉就是卡框 —— **框直接落在卡面的实际位置上**，
   不再是等分的空格子。实测 5 张真实截图 5/5 切出 28 格，尺寸 143×141 左右，
   与真实卡框一致。

两步都有明确的放弃条件（定位不到区域 / 各行列数分歧太大就退回旧路径），
保证不会比从前更差。`--no-refine` 会同时关掉这两步和格内贴合，回到纯等分。

**卡框贴合（把格子收缩到卡面上）**：等分网格切出来的是「格子」，不是「卡框」。
实测真实截图里格子是 170×170、卡框只有约 150×145，每边约 10px 是背景留白；
而相邻卡面之间只有 20~25px 的间隙，**格子的上下边常常正好压在邻居卡面上**，
切出来的图里就混进了别的卡的一条边。所以切分之后每格还会再做一次贴合：
从格子四条边各自向内扫描 —— 跳过可能是邻居探进来的边缘、跳过背景带，
遇到的第一段连续内容就是卡框的那条边。

> 关键是**只向内收缩、绝不向外扩展**。向外扩展有吃到相邻卡面的风险（那正是要
> 消除的问题），向内收缩只会让结果更干净；代价是格子边界已经切进卡面内部时补不
> 回来 —— 少几个像素不致命，混进别的卡才致命。
>
> 也刻意**不按"找包含中心的整段内容"来定界**：卡面中间可能出现大片低饱和区域
> （浅色衣料、天空、白底），按整段切会把它误当成卡框外的背景，一刀切掉半张卡
> （实测在失真截图上把 170px 的格子切到过 80px）。从边界出发的扫描够不到卡面中间，
> 天然免疫这个问题。
>
> 四条边各自独立：定不出来的那条保持原边界，其余照常贴合 —— 实测第 3~5 张截图
> 的上边界正好切在本格卡面内部，那种情况下"上边找不到分界"本身是对的，但不该
> 因此放弃左右两条已经找到的边。

实测 140 个真实卡面格：宽度收到 **85.5%**、高度收到 **87.0%**，140/140 都能定出边界，
零放大。识别结果不变（认出的画 100% 正确），而自动采信从 139/140 提升到 **140/140**。

> 用 `--no-refine` 可以关掉这一步（直接按等分格子切），便于对照。

**卡牌图共用**：Bestdori 上有 **111 个 `resourceSetName` 被多张卡共用**（例如
「第N回ガルパ杯」纪念卡复用活动卡的原图），这些卡在指纹库里特征完全相同。
所以并列时的排序键最后加了一层**卡号升序**，保证同一张截图每次跑给出同样的卡号，
而不是在几张共用图的卡之间随机摇摆。这类"同一张画对应多张卡"的歧义无法从图像上
区分，需要人工确认。


**颜色特征用 4×4 分块直方图**而不是全局直方图 —— 全局直方图丢掉了"颜色在哪"，同角色同属性的卡几乎分不开。分块之后区分度接近翻倍（真实截图 top1 与 top2 的差距中位从 0.014 到 0.028）。见设计文档 §2.2。

**三路信号融合**：

| 信号 | 权重 | 作用 |
| --- | --- | --- |
| 指纹（pHash+dHash 0.40 / 4×4 分块颜色 0.60） | 主 | **粗筛**，取 top-K 候选 |
| **SIFT + RANSAC 几何校验** | **主** | **精筛**：对 top-K 二次排序，判定"是不是同一张画" |
| 属性提示（UI 边框颜色 → 四属性色相） | 辅 | 把候选集缩小到对应属性，专治「同角色多张卡」 |
| OCR 卡名/角色名（可选） | 辅 | 前两名咬得紧时做二次判定 |

识别结果页提供**逐格核对**：原图上把识别出的卡面自动框出来（点框即选中该格），
右侧把「截图里的那张卡面」和「Bestdori 的同一张卡图」并排显示，并给出
**卡面详情页网址**（可点开、可复制）方便到 Bestdori 上核对：
`https://bestdori.com/info/cards/{卡号}`。

命令行想拿网址加 `--urls`；JSON 导出里每条记录也带 `url` 字段。

识别本身是**两段式**的：

```
截图 ──► 粗筛：pHash + dHash + 4×4 分块颜色 ──► top-30 候选
                                                    │
                                                    ▼
                                          精筛：SIFT + RANSAC 几何校验
                                          （比关键点的几何对应关系）
                                                    │
                     ┌──────────────────────────────┼──────────────────────────┐
                     ▼                              ▼                          ▼
           内点 ≥ 30（同一张画）          10 ≤ 内点 < 30                内点 < 10
           → matched 直接采信            → ambiguous 列候选让人点       → 退回全局分判定
```

**为什么必须两段式**：全局特征（pHash / 颜色直方图）是**统计量**，只知道"整体上
像不像"，不知道"是不是同一张画"。同角色、同属性的不同卡配色和构图都很接近，
全局特征就分不开了 —— 实测真实截图 top-1 只有 **67.9%**。SIFT 比的是**具体纹理
关键点之间的几何对应**，是"同一张画"的硬证据：同一张画的匹配点又短又平行、
铺满整张脸（内点 100~270），不同张画稀疏散乱（内点 0~7），**中间地带几乎不存在**。

实测内点分布是强双峰的（140 个真实卡面格）：137 格能找到内点 ≥ 60 的候选，
剩下 3 格都 < 20，中间一格都没有。加了几何校验后 top-1 准确率 **67.9% → 100%**。

---

## 安装

```bash
git clone <repo> && cd BestdoriHelper

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e .
pip install -e ".[gui]"            # 桌面界面（PySide6）
```

可选组件：

```bash
pip install -e ".[gui]"            # 桌面 GUI（PySide6）
pip install rapidocr-onnxruntime   # OCR 辅助信号（纯 onnx，无系统依赖）
pip install playwright && playwright install chromium   # Bestdori 自动导入
```

> pHash / dHash 是用 numpy 自己实现的，**不需要** scipy / imagehash，安装很轻。

---

## 桌面界面（推荐）

```bash
run-gui.bat                        # Windows：直接双击这个文件
bdh gui                            # 或命令行启动
python -m bestdori_helper.gui      # 或直接跑模块
```

窗口分四页，按实际使用顺序排：

| 页 | 做什么 |
| --- | --- |
| **① 导入截图** | 拖入截图或整个文件夹，选切分模式（自动 / 指定行列 / 整图一张），调属性提示、OCR、自动入清单等选项，点「开始识别」 |
| **② 识别结果** | 每一格一行，带卡图和置信度。选中某行右侧会列出相似度最高的候选，**双击候选即可改过来**；也可以「手动指定卡牌…」在卡池里搜。修正会同步改写清单 |
| **③ 卡面清单** | 总数 / 特训后 / 待确认 / 4★5★ 统计，按卡名角色搜索、只看待确认、批量确认删除，导出 CSV / Markdown / JSON / ID 列表 |
| **④ 同步 Bestdori** | 依次是「同步卡池资料 → 构建指纹库 → 生成导入计划 → 试运行 / 实际写入」，另带「校准选择器」 |

设计上的两点：

- **界面不碰业务逻辑**，全部调用 `vision` / `inventory` / `bridge` 里已有的函数，所以 GUI 和命令行行为完全一致。
- **所有耗时操作都离开主线程**（`gui/workers.py` 里的 `Worker(QThread)`），识别几千格也不会假死；卡图缩略图走线程池异步加载，先占位再补图。

自检（窗口会显示 3 秒后自动关闭）：

```bash
python scripts/gui_check.py
```

---

## Web 界面（备选）

```bash
bdh serve            # http://127.0.0.1:8787
```

功能与桌面版一致，适合不想装 Qt 的场合。界面流程：拖入截图 → 选切分模式 → 识别 → 结果卡片（带卡图、置信度、候选列表）→ 清单统计与导出 → 同步 Bestdori。

---

## 网页版（GitHub Pages，纯静态）

不需要装任何东西，打开网页就能用：拖入截图 → 识别 → 出清单 → 导出。
**图片只在本机浏览器里处理，不上传**。整个识别链路（网格切分、特征提取、
指纹检索）都是 JS 重写的，跑在浏览器里。

```
docs/
├── index.html              单页应用
├── app.js                  主逻辑（导入 → 识别 → 清单 → 导出）
├── style.css               深色主题
├── src/
│   ├── features.js         pHash + dHash + 4×4 分块 HSV 直方图
│   ├── detect.js           网格切分（卡面区定位 + 间隙切分 + 卡框贴合）
│   └── index.js            指纹库加载与检索
├── data/
│   ├── cards.json          卡牌 / 角色 / 乐队元数据（240 KB）
│   ├── fingerprints.bin    指纹库（3865 条，3.3 MB）
│   └── thumbs/             卡面缩略图（3865 张 WebP，41 MB，按需加载）
└── build/                  构建与验证脚本（Node，不参与运行时）
```

### 部署到 GitHub Pages

站点就在 `docs/` 目录里，是纯静态文件，**不需要任何构建步骤**：

```
仓库 Settings → Pages → Source 选 "Deploy from a branch"
                        → Branch 选 main，目录选 /docs → Save
```

等一两分钟，访问 `https://<用户名>.github.io/<仓库名>/` 即可。
`docs/.nojekyll` 已经放好，避免 Jekyll 处理静态资源。

### 本地预览

```bash
python -m http.server 8788 --directory docs
# 打开 http://127.0.0.1:8788
```

> ⚠️ 不能直接双击 `index.html`（`file://`）—— 浏览器会拦截 `fetch` 读取
> `data/` 下的资源。必须走 HTTP。

### 静态数据怎么来的

浏览器里不可能现算 3865 张图的指纹（太慢），所以指纹库是**构建时生成**的：

```
Bestdori 缩略图 (180×180 PNG)
   │  ① python scripts/export_thumbs_raw.py     ← PIL 解码调色板 PNG，导出原始 RGB
   ▼
thumbs_rgb.bin + thumbs_index.json
   │  ② node docs/build/build_index.mjs         ← 跑 docs/src/features.js
   ▼
docs/data/fingerprints.bin
```

**关键：②用的是和浏览器端完全相同的那份 `features.js`。**
所以库端特征与查询端特征天然一致，不需要去追 JS 与 PIL 的 LANCZOS 重采样
逐位对齐（那条路很难走通，而且几个 bit 的哈希差异就会直接毁掉检索）。

元数据与缩略图另用一条命令导出：

```bash
python scripts/export_site_data.py        # cards.json + thumbs/*.webp
```

### 验证脚本

```bash
node docs/build/verify_selfcheck.mjs   # 自洽性：拿库里的图当查询，能否找回自己
node docs/build/verify_cells.mjs       # 端到端检索：140 个真实格跑 JS 版检索
node docs/build/verify_detect.mjs      # 网格切分：不传 region 能否切出 28 格
python scripts/verify_cells_py.py      # Python 对照（与 JS 结果逐格比对）
python scripts/site_check.py           # 真实浏览器自检（Playwright）
```

实测结论：

| 项 | 结果 |
| --- | --- |
| 自洽性（top-1 找回自己） | 58/61 = 95.1%（未拿回的 3 例是共用同一张卡图的卡，得分 0.9999，属预期） |
| JS vs Python 检索结果 | **140/140 完全一致**（卡号 + 特训态） |
| JS 网格切分 | 5 张真实截图 **5/5 自动切出 28 格**（不传 region） |
| 浏览器端速度 | 约 **10 ms/格**（140 格 1.4 秒） |

### 与桌面版的差异

| | 桌面版 | 网页版 |
| --- | --- | --- |
| 识别算法 | pHash/dHash/分块颜色 + **SIFT 几何校验** | 同样的粗筛，**无几何校验** |
| 同步 Bestdori | 后台接口直接写入 | 只导出清单（CSV/JSON/ID/Markdown） |
| OCR 辅助 | 可选 | 无 |

**为什么网页版没有几何校验**：桌面版用的是 `opencv-python` 的 SIFT；
浏览器侧的 OpenCV.js **官方构建不含任何特征检测/匹配**
（`ORB`、`detectAndCompute`、`DescriptorMatcher`、`RANSAC` 实测全都不在），
要用就得自己实现一套 ORB + RANSAC（约 250 行）。

而实测显示**当前切分质量下几何校验已无增量** —— 140 个真实格里，
SIFT 改变了 **0 格**的 top-1（粗筛 top-30 的 top-1 内点全部 ≥ 30）。
所以网页版先不做，把这条留作后续增强。

> 顺带说明：早期文档里"纯粗筛 top-1 只有 67.9%"是**第 6 轮（旧特征、
> 未加卡框贴合）**的历史数字，不适用于当前版本。

---

## 安卓 App（Capacitor）

手机端要的不是"把网页缩小"，而是**能像桌面版一样把清单写进 Bestdori 卡册**。
而这件事网页做不到 —— 见下面这节。

### 为什么网页版永远做不了，必须打包成 App

用真实 Chromium 从 localhost 页面发起请求，Bestdori 的接口**全部**被拦：

```
✗ GET  https://bestdori.com/api/cards/all.5.json   TypeError: Failed to fetch
✗ GET  https://bestdori.com/api/user/me            TypeError: Failed to fetch
✗ POST https://bestdori.com/api/user/login         TypeError: Failed to fetch
```

**连不需要登录的公开卡牌接口都被拦** —— Bestdori 完全没设 CORS 头。
所以：

| 形态 | 能否直连 Bestdori |
| --- | --- |
| 手机浏览器 / PWA | ❌ 同源策略拦死，无解 |
| **原生壳（Capacitor）** | ✅ 原生 HTTP 没有同源策略 |

Capacitor 的 `CapacitorHttp` 会把 `window.fetch` / `XMLHttpRequest`
**换成原生实现**（Android 上走 OkHttp），请求就通了 ——
官方文档原话：*"provides native http support via patching fetch and
XMLHttpRequest to use native libraries"*。

`capacitor.config.json` 里已经开好：

```json
{ "plugins": { "CapacitorHttp": { "enabled": true } } }
```

好处是**业务代码一行都不用改** —— `docs/src/bestdori.js` 里只写 `fetch`，
浏览器和原生壳跑的是同一份代码。

### 怎么拿到 APK

**方式一：本地构建（已验证可用）**

```bash
bash scripts/build_apk.sh
# → android/app/build/outputs/apk/debug/app-debug.apk
```

脚本会自动找 `~/.workbuddy-ai/binaries` 下预装的工具链，找不到再退回环境变量。
需要三样东西，都有版本要求：

| 组件 | 要求 | 备注 |
| --- | --- | --- |
| JDK | **17 或 21** | ⚠️ **JDK 25 不行** —— Gradle 8.11 / AGP 8.7 都不支持 |
| Android SDK | `platforms;android-35` + `build-tools;35.0.0` + `platform-tools` | 见下面的坑 |
| Gradle | 8.9+ | 本项目用 8.11.1 |

> ⚠️ **别让 AGP 自己下 SDK 组件。** 实测它会卡在
> `Preparing "Install Android SDK Platform 35"` 几乎不动（110 秒才 3 MB）。
> 同一个 `dl.google.com` 用 curl 直接下却很快。所以手工下这三个包再解压到位：
>
> | 包 | 解压后放到 |
> | --- | --- |
> | `platform-35_r02.zip`（内层 `android-35/`） | `platforms/android-35/` |
> | `build-tools_r35_windows.zip`（内层叫 `android-15/`，**要改名**） | `build-tools/35.0.0/` |
> | `platform-tools-latest-windows.zip` | `platform-tools/` |
>
> 包名从 `https://dl.google.com/android/repository/repository2-3.xml` 里查，
> 别猜（`build-tools` 那个是**下划线** `r35_windows`，不是连字符）。

**方式二：云端构建（本地什么都不用装）**

```
推到 GitHub → Actions 页面 → 「构建安卓 APK」→ 等几分钟
           → 下载 Artifacts 里的 bestdorihelper-debug-apk
```

或者打一个 tag，APK 会自动挂到 Release 上，手机直接点链接下载。

装的时候要允许「安装未知来源的应用」（侧载，不走应用商店）。

### 同步是怎么做的

```
登录（POST /api/user/login，拿会话 cookie）
   │
   ├─ 读取云端档案（GET /api/user/profiles）
   ├─ 增量合并（远端没有→新增；远端未特训而本地特训→升级；一致→跳过）
   └─ 全量写回（POST /api/user/profiles）
```

**编解码必须与 Bestdori 前端逐字节互逆** —— 云端档案格式是从它的前端
bundle 逆向的，差一个字节就会写坏用户的档案。所以移植后用**离线对照测试**
把两边钉死：

```bash
python scripts/export_codec_cases.py   # Python 算一遍当基准
node docs/build/verify_codec.mjs       # Node 跑同一批用例比对
# → 通过 54，失败 0
```

这个测试抓到了一个真 bug：**不能用「特训加成 > 0」判断卡能不能特训** ——
实测有 **153 张 4★ 卡**的 `training.levelLimit` 就是 0，但它们是**可特训**的。
所以 `cards.json` 里单独导出了一个 `tr` 标志。

### 验证到什么程度

| 项 | 方式 | 结果 |
| --- | --- | --- |
| 编解码与 Python 一致 | 离线对照测试（54 项） | ✅ 全过 |
| 同步链路（登录→计划→写入） | mock 网络 + 假 `window.Capacitor` | ✅ 5/5 检查通过 |
| 手机端布局 | Playwright 模拟 Pixel 7 | ✅ 无横向溢出、面板高度正确 |
| APK 能构建出来 | 本地 Gradle 构建 | ✅ 46.2 MB，`CapacitorHttp` 已打包进去 |
| **原生 HTTP 真的绕过 CORS** | **需要真机装 APK 验证** | ⚠️ **未验证** |

最后一项是唯一没验证的假设：`CapacitorHttp` 的 fetch 替换在真机上是否
如文档所述工作。要装到手机上跑一次才知道。

```bash
python scripts/sync_check.py     # 同步链路（mock 网络，不需要账号）
```

---

## 快速开始（命令行）

```bash
# 1. 同步 Bestdori 卡牌数据（约 2468 张卡，几秒钟）
bdh sync

# 2. 构建卡面指纹库
#    默认用 Bestdori 的 180×180 方形缩略图，全部约 80 MB，首次几分钟
#    想改用横向原图：bdh --source original index build（约 1~2 GB，慢得多）
#    想先试跑：bdh index build --cards 1,2,3,4,5,6
bdh index build

# 3. 批量识别截图
bdh scan ~/Pictures/Bandori/          # 整个目录
bdh scan shot1.png shot2.png --mode grid --rows 2 --cols 4

# 4. 看清单
bdh list
bdh list --pending                    # 只看待人工确认的

# 5. 导出
bdh export -f markdown -o 卡牌清单.md
bdh export -f csv -o cards.csv
bdh export -f ids                     # 纯卡牌 ID 列表

# 6. 同步到 Bestdori（默认只试运行，不会写入）
bdh import-bestdori --plan-only       # 先看要导入哪些（零依赖）
# 桌面 GUI 的「④ 同步 Bestdori」走后台接口写入（推荐，不弹浏览器）
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--server cn\|jp\|tw\|en\|kr` | 服务器区域，默认 `cn`（影响卡名语言与卡图来源） |
| `--source thumb\|original` | 卡图来源。`thumb`（默认）= 180×180 方形缩略图，取景与游戏内卡面格一致；`original` = 1334×1002 横向原图。两套指纹库分开存 |
| `--lang 0..4` | 语言下标：0 日 / 1 英 / 2 繁中 / 3 简中 / 4 韩，默认跟随服务器 |
| `--home <dir>` | 数据目录，默认 `~/.bestdori-helper`（也可用环境变量 `BESTDORI_HELPER_HOME`） |
| `--mode auto\|grid\|single` | 切分模式：自动检测 / 指定行列 / 整图一张 |
| `--region x0,y0,x1,y1` | 卡面网格区域（0~1 归一化）。**通常不用给**，自动定位失败时才手动指定 |
| `--ocr` | 启用 OCR 辅助 |
| `--urls` | 每格后面打印 Bestdori 卡面详情页网址，便于人工核对 |
| `--no-verify` | 关掉 SIFT 几何校验（默认开启）。关掉后退回纯全局特征，真实截图 top-1 准确率 100% → 67.9% |
| `--no-refine` | 关掉卡框贴合（默认开启），即直接按等分格子切 |

---

## 关于 Bestdori 集成

这部分是实测确认过的，不是猜的。

**公开数据接口**（无需登录）：

| 接口 | 内容 |
| --- | --- |
| `GET /api/cards/all.5.json` | 全部卡牌，2468 张 |
| `GET /api/characters/all.2.json` | 全部角色，268 名 |
| `GET /api/bands/all.1.json` | 全部乐队，111 支 |

**卡图资源**（两套，用途不同）：

```
# ① 方形缩略图 180×180 —— 建指纹库用这套（默认）
https://bestdori.com/assets/{server}/thumb/chara/card{id//50:05d}_rip/{resourceSetName}_{normal|after_training}.png

# ② 横向原图 1334×1002 —— 展示/备份用
https://bestdori.com/assets/{server}/characters/resourceset/{resourceSetName}_rip/card_{normal|after_training}.png
```

⚠️ 四个坑，代码里都已经处理：

1. **必须带 User-Agent**，否则 nginx 返回 403 或首页 HTML
2. **约 6.5% 的卡没有 `card_normal.png`** —— 生日卡（`birthday`）、部分活动卡（`campaign`）、少数 `limited`/`kirafes` 卡，它们的卡面**只存在于 `card_after_training.png`**。`resolve_variants()` 会自动回退，并写 `.missing` 标记避免重复请求。
3. `/api/cards/{id}.json` 这种单卡路径**不存在**，会返回 SPA 首页，必须用全量接口本地索引。
4. 缩略图目录名里的数字是**卡牌 ID 的 50 张分桶**（`id // 50` 补零 5 位），不是资源名派生出来的。另外有 **111 个 `resourceSetName` 被两张卡共用**（涉及 231 张卡），按资源名建反查表会静默丢掉 125 张卡。


**写入端**：Bestdori 的卡牌登记在「我的卡牌」页面，路由是

```
/profile/manager    Profile Manager  创建档案（服务器 / 活动 / 大师等级 / 技能等级）
/profile/cards      Profile Cards    ← 登记持有卡牌，本工具的写入目标
/profile/items      Profile Items    区域道具
```

这些是**登录态下的账号数据**，必须登录才能读写。所以提供三层方案：

1. **导入计划**（`--plan-only` / `--plan-out`）—— 算出「本地有、远端没有」的卡，导出成 JSON / CSV / ID 列表，零依赖
2. **后台接口（GUI「④ 同步」在用，推荐）** —— `POST /api/user/login` 拿会话，
   `GET/POST /api/user/profiles` 读写档案，**不弹任何浏览器窗口**。
   接口与档案存储格式是从 Bestdori 前端 bundle 逆向出来的，见 `bridge/bestdori_api.py`
3. **浏览器自动化**（`PlaywrightImporter`，仅 CLI 保留）—— 用户实测 `/profile/cards/add`
   是 404，且不愿弹浏览器，GUI 已不再使用；仅作备用

⚠️ Bestdori 是 Vue SPA，DOM 会随版本变化。自动化模块**不硬编码单一选择器**，而是每个操作给一组候选、取第一个命中的。若全部落空：

```bash
bdh import-bestdori --inspect     # 打开页面并打印输入框/按钮/类名
```

把新选择器补进 `bridge/bestdori_import.py` 的 `SelectorConfig` 即可。

---

## 项目结构

```
src/bestdori_helper/
├── config.py              服务器/语言/目录配置，四属性定义，卡图来源（thumb/original）
├── models.py              Card / Character / Band / Catalog / OwnedCard（含 thumb_url）
├── bestdori/client.py     公开 API 客户端、双来源卡图下载、变体回退、负缓存
├── vision/
│   ├── features.py        pHash + dHash（纯 numpy）+ HSV 直方图
│   ├── index.py           指纹库构建 / 增量更新 / 检索
│   ├── detect.py          从整屏截图切分卡面网格 + 卡框贴合
│   ├── ui_hints.py        从属性图标 / 边框颜色估计属性
│   ├── verify.py          SIFT + RANSAC 几何校验（粗筛后的二次排序）
│   ├── ocr.py             可选 OCR 引擎 + 文字匹配
│   └── recognize.py       多信号融合流水线
├── inventory/
│   ├── store.py           清单存储、去重、统计
│   └── export.py          Markdown / CSV / JSON / ID 列表
├── bridge/bestdori_import.py   导入计划 + Playwright 自动化
├── gui/
│   ├── app.py             主窗口（四页 + 工具栏 + 日志面板）
│   ├── workers.py         后台任务线程，业务函数都在这里被调用
│   ├── widgets.py         缩略图异步加载 / 拖放区 / 统计卡 / 选卡对话框
│   └── theme.py           深色主题（QSS + 调色板）
├── web/app.py             FastAPI 后端
├── web/static/index.html  单页前端（无构建步骤）
└── cli.py                 命令行入口
run-gui.bat                双击启动桌面界面

docs/                      网页版（GitHub Pages，纯静态，识别全在浏览器里跑）
├── index.html / app.js / style.css
├── src/                   features.js（特征）/ detect.js（切分）/ index.js（检索）
│                          bestdori.js（Bestdori 接口客户端，登录 / 档案读写）
├── data/                  cards.json + fingerprints.bin + thumbs/*.webp
└── build/                 构建与验证脚本（Node）

android/                   Capacitor 安卓工程（cap add android 生成，勿手改）
capacitor.config.json      webDir=docs；启用 CapacitorHttp（绕过 CORS 的关键）
package.json               Capacitor 依赖
.github/workflows/android.yml   云端打包 APK

scripts/                   构建与自检
├── export_thumbs_raw.py   缩略图 → 原始 RGB（给 Node 算指纹）
├── export_site_data.py    卡牌元数据 + WebP 缩略图
├── export_shots_raw.py    整张截图 → 原始 RGB（给 Node 验切分）
├── export_codec_cases.py  生成 Bestdori 编解码的 Python 基准用例
├── verify_cells_py.py     Python 侧检索对照
├── site_check.py          网页版真实浏览器自检（Playwright，支持设备模拟）
└── sync_check.py          Bestdori 同步链路验证（mock 网络）
```

---

## 已知限制

- **指纹识别**：真实截图 top-1 **卡号**准确率 **99.3%**（136/137 可评估格，5 张 2400×1080 真实截图），**认出的画 100% 正确**。差的 1 格不是认错 —— 那格里 Bestdori 上的两张卡共用同一个 `resourceSetName`（同一张画），从图像上无法区分，只能人工确认。同角色同属性的多张卡靠全局特征分不开（只有 67.9%），**必须靠 SIFT 几何校验二次排序**才能到 100% —— 所以 `opencv-python` 不是可选项，是核心依赖。**卡面清单页的人工校正流程仍然是设计的一部分**（面对"库里还没有的新卡"和低质量截图）。
- **指纹库构建要下载全部卡图**。默认用缩略图，约 80 MB / 3600 张，首次几分钟；改用 `--source original` 则是 1~2 GB、十几分钟。之后是增量更新，只处理变动的图。想省空间可以 `--no-trained`（跳过特训后卡面，识别不了已特训的形态）。
- **换 `--source` 要重建库**：两套卡图画幅不同、特征不可比，指纹库分开存，切换后旧库不会自动迁移。
- **新卡滞后**：Bestdori 收录后 `bdh sync` + `bdh index build` 才能认出来。没收录的卡会报 `unknown`。
- **自动定位卡面区有前提**：它靠「卡面行间隙是一条横贯卡面区的长背景带」来定位。换到别的界面（非成员一览）若定位失败，会退回等分/周期检测，此时再手动给 `--region`。
- **属性提示是启发式的**，只在属性图标/边框颜色明显时才用；一旦发现它把正确答案排除了会自动退回全库重搜。
- **星级不做视觉估计**：数星星对缩放和抗锯齿太敏感。星级直接取指纹匹配到的卡牌记录，反而更准。
- **自动导入依赖 Bestdori 的内部接口**（`api/user/login`、`api/user/profiles`）与档案存储格式，两者都是从其前端 bundle 逆向的；Bestdori 若改数据结构，需要用「校准选择器」或抓包重新确认。
- **网页版没有几何校验**，也**不能同步到 Bestdori**（只导出清单）。几何校验缺的原因是浏览器侧 OpenCV.js 不含特征检测/匹配，且实测当前切分质量下它已无增量（见「网页版」一节）。
- **网页版的 `docs/data/` 有约 45 MB 静态数据要入库**。卡池更新后需重跑导出脚本，git 仓库会随之增长（每版留一份历史）。若在意体积，可把 `data/thumbs/` 改为部署前临时生成、不入库。
- **安卓 App 的「原生 HTTP 绕过 CORS」尚未在真机验证**。这是整个方案唯一没验证的假设 —— 逻辑（编解码、同步链路、布局）都已离线验过，但 `CapacitorHttp` 替换 `fetch` 之后请求是否真能通，要装到手机上跑一次才知道。
- **只有安卓**。iOS 要 macOS + Xcode 才能构建，且侧载需要开发者账号，本轮没做。iPhone 上目前只能当网页版用（也就是**不能同步到 Bestdori**）。
- **本地构建 APK 需要 JDK 17/21**（系统的 JDK 25 用不了），以及 Android SDK 的三个组件。`scripts/build_apk.sh` 会自动找，找不到会告诉你缺什么。

---

## 开发

```bash
pytest                          # 单元测试（318 个，全部离线，约 16 秒）
python scripts/e2e_check.py     # 端到端：合成模拟截图跑完整识别链路
python scripts/edge_cases.py    # 边界场景：auto 切分 + 属性提示 A/B
python scripts/gui_check.py     # 桌面界面自检（窗口显示 3 秒后自动关闭）

# 网页版（先起本地服务）
python -m http.server 8788 --directory docs
python scripts/site_check.py    # 真实浏览器跑一遍「上传 → 识别 → 出清单」
python scripts/site_check.py --device "Pixel 7"    # 手机视口 + 横向溢出体检
node docs/build/verify_detect.mjs    # JS 切分：不传 region 能否切出 28 格
node docs/build/verify_cells.mjs     # JS 检索：140 个真实格的结果
node docs/build/verify_selfcheck.mjs # JS 指纹库自洽性

# 安卓 App
python scripts/export_codec_cases.py && node docs/build/verify_codec.mjs
                                     # Bestdori 编解码：Python 与 JS 逐字节比对
python scripts/sync_check.py         # 同步链路（mock 网络，不需要真账号）
npx cap sync android                 # 网页资源同步进安卓工程
```

- **`pytest`** —— 覆盖特征提取、网格切分、指纹库、清单、导出、导入计划、识别流水线、卡图来源与属性提示，
  以及桌面界面（`test_gui_smoke.py` 用 Qt 的 `offscreen` 平台把窗口真正建出来，
  并走一遍「人工修正识别结果 → 清单同步改写」等关键路径）。未装 PySide6 时自动跳过 GUI 部分。
  全部用程序合成的假卡面，不联网、不依赖预先存在的指纹库。
- **`e2e_check.py`** —— 建小规模指纹库 → 合成 7 组不同难度（缩放 / JPEG 压缩 / 亮度色偏 /
  极小图 / 取景偏移）的模拟截图 → 跑识别 → 输出准确率。合成格严格按真实游戏格来：
  **正方形、尺寸正好等于画布等分、卡框按真实属性上色并带右上角属性图标**。
- **`edge_cases.py`** —— 专测 `auto` 自动切分（有/无背景间隙、粗细边框、UI 叠加）
  与属性提示的开/关对照。
- **`gui_check.py`** —— 在**真机**（非 offscreen）上把窗口显示出来，验证 Qt 平台插件、
  字体与主题可用。
