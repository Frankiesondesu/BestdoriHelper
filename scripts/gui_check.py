"""GUI 启动自检：真机（非 offscreen）构建窗口并显示 3 秒后自动退出。

用途：确认 Qt 平台插件、字体、主题在**这台机器**上真的能跑起来。
如果窗口一闪而过且退出码为 0，说明桌面环境正常。

::

    python scripts/gui_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from bestdori_helper.config import Settings  # noqa: E402
from bestdori_helper.gui.app import MainWindow  # noqa: E402
from bestdori_helper.gui.theme import apply_theme  # noqa: E402


def main(seconds: float = 3.0) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)

    settings = Settings()
    settings.ensure_dirs()

    window = MainWindow(settings)
    window.show()
    print(f"窗口已显示：{window.width()}x{window.height()}，平台 = {app.platformName()}")
    print(f"数据目录：{settings.home}")

    QTimer.singleShot(int(seconds * 1000), app.quit)
    code = app.exec()
    print(f"GUI 自检完成，退出码 {code}")
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
