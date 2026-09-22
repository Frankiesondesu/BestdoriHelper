"""BestdoriHelper —— 卡面截图批量识别 + Bestdori 卡册同步。

模块划分::

    bestdori_helper.bestdori   Bestdori 公开 API 客户端与资源下载
    bestdori_helper.vision     图像识别（指纹匹配 / OCR 辅助 / 网格切分）
    bestdori_helper.inventory  持有卡牌清单的存储与导出
    bestdori_helper.bridge     把清单写回 Bestdori（浏览器自动化 / 内部接口）
    bestdori_helper.web        本地 Web UI
    bestdori_helper.cli        命令行入口
"""

__version__ = "1.1.0"
