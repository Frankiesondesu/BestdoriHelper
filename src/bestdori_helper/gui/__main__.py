"""``python -m bestdori_helper.gui`` 入口。"""

from __future__ import annotations

import sys

from ..config import Settings
from .app import run

if __name__ == "__main__":
    sys.exit(run(Settings()))
