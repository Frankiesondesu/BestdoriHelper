# 与 Bestdori 的集成

> 回到 [README](../README.md)

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
