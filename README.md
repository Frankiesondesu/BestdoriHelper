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
| **网页版** | <https://frankiesondesu.github.io/BestdoriHelper/>，无需安装 | 快速识别 + 导出清单 |
| **手机 App** | [Releases](../../releases) 下载 `app-debug.apk` | 想在手机上直接同步到 Bestdori |
| **Windows 桌面版** | [Releases](../../releases) 下载 `BestdoriHelper-win.zip`，解压双击 `BestdoriHelper.exe` | 界面最完整 |

三个平台**共用同一套识别算法与清单格式**，只是界面不同。
各端具体有什么、哪里不一样，见 **[三端功能对照](docs/三端功能对照.md)**。

### 从源码跑

| 双击 | 作用 |
| --- | --- |
| `run-web.bat` | 打开**网页版**（起本地静态服务并自动开浏览器，只用标准库）|
| `run-gui.bat` | 打开**桌面界面** |

```bash
python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -e ".[gui]"                          # 只要命令行可以去掉 [gui]
python -m bestdori_helper.gui                    # 桌面界面
python -m bestdori_helper.cli --help             # 命令行
python scripts/serve_web.py                      # 本地起网页版（等价 run-web.bat）
```

> 网页版必须走 http，**不能直接双击 `docs/index.html`** —— 它用了 ES module 和
> fetch，`file://` 协议下会被浏览器的同源策略挡掉。

打 Windows 包：`python scripts/build_win.py`（产物在 `dist/`）。

---

## 文档

| 文档 | 讲什么 |
| --- | --- |
| [**三端功能对照**](docs/三端功能对照.md) | 各端有什么、差在哪 —— **加功能前先看这张** |
| [识别原理](docs/识别原理.md) | 怎么认出卡面、为什么用方形缩略图、SIFT 几何校验 |
| [使用 · 桌面版](docs/使用-桌面版.md) | 桌面界面怎么用 |
| [使用 · 网页版](docs/使用-网页版.md) | 网页版用法、部署、静态数据怎么来 |
| [使用 · 安卓版](docs/使用-安卓版.md) | App 怎么装、同步流程、为什么必须打包成 App |
| [使用 · 命令行](docs/使用-命令行.md) | `bdh` 各子命令 |
| [Bestdori 集成](docs/Bestdori-集成.md) | 卡图资源、档案格式、各种导入方式 |
| [项目结构](docs/项目结构.md) | 目录与模块划分 |
| [开发与验证](docs/开发与验证.md) | 测试与自检脚本怎么跑 |
| [已知限制](docs/已知限制.md) | 目前的不足 |
| [设计文档](docs/设计文档.md) | 完整设计与实现细节（很长）|

---

## 它是怎么认出卡面的（摘要）

不用 OCR（游戏字体、缩放、压缩都会让 OCR 翻车），而是**拿 Bestdori 自己的卡图
建指纹库做视觉比对**：全局特征（pHash/dHash + 分块颜色直方图）粗筛 top-K，
再用 **SIFT + RANSAC 几何校验**做二次排序。

两个关键选择，理由见 [识别原理](docs/识别原理.md)：

- **用 180×180 方形缩略图建库，而不是横向原图** —— 游戏里的卡面格是方形脸部
  特写；用原图建库时真实截图只能匹配 11.4%，换缩略图后 **40.0%**
- **靠几何校验，而不是调全局特征阈值** —— 只看全局特征 top-1 只有 67.9%，
  加上几何校验后真实截图 **100%**（内点数是客观判据，比肉眼可靠）

---

## 安装（含可选组件）

```bash
git clone <repo> && cd BestdoriHelper
python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -e .                                 # 核心（含命令行）
pip install -e ".[gui]"                          # 桌面界面（PySide6）
```

可选：

```bash
pip install rapidocr-onnxruntime                        # OCR 辅助信号
pip install playwright && playwright install chromium   # 浏览器自动导入
```

> pHash / dHash 是用 numpy 自己实现的，**不需要** scipy / imagehash，安装很轻。
