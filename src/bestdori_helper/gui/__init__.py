"""桌面 GUI（PySide6 / Qt6）。

启动::

    bdh gui
    python -m bestdori_helper.gui

GUI 只是既有服务层的一层外壳：识别、指纹库、清单、导入计划全部复用
``vision`` / ``inventory`` / ``bridge`` 里的函数，不重复实现业务逻辑。
"""

from __future__ import annotations

__all__ = ["run"]


def run(*args, **kwargs):  # pragma: no cover - 延迟导入，避免无 Qt 环境直接崩
    from .app import run as _run

    return _run(*args, **kwargs)
