"""PyInstaller 的打包入口（桌面 GUI 版）。

打包后的 exe 不能再用 ``python -m bestdori_helper.gui``，需要一个真实脚本
作为入口 —— 就是这里。逻辑与 ``gui/__main__.py`` 保持一致。
"""

from __future__ import annotations

import sys

from bestdori_helper.config import Settings
from bestdori_helper.gui.app import run

if __name__ == "__main__":
    sys.exit(run(Settings()))
