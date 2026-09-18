"""用 PyInstaller 打包 Windows 桌面版。

产物::

    <outdir>/BestdoriHelper/          onedir 目录（双击其中的 exe 即可运行）
    <outdir>/BestdoriHelper-win.zip   分发包（Release 用）

**为什么用 onedir 而不是 onefile**：onefile 每次启动都要把上百 MB 的内容解压到
临时目录，PySide6 + opencv 这套实测启动要十几秒；onedir 直接加载，秒开。
分发时打成 zip 一样方便。

用法::

    python scripts/build_win.py                     # 输出到 dist/
    python scripts/build_win.py --outdir dist-win   # 换个目录（见下）
    python scripts/build_win.py --dir               # 只出目录，不压 zip（CI 省时间）

**为什么有 --outdir**：本脚本刻意**不主动删除**任何已有目录。
某些受限环境对"批量删除"有保护，会直接终止进程（本机实测：删 406 个文件触发
SAFE_DELETE_BULK_CONFIRM_REQUIRED，脚本的 rmtree 和 PyInstaller 自己的清理
都会被拦）。输出到全新目录就能绕开；CI 是干净环境，用默认值即可。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

# GitHub 的 Windows runner 控制台默认用 **cp1252**，直接 print 中文会抛
# UnicodeEncodeError 把整个构建搞崩（实测崩在 print 那一行，看日志才发现）。
# 本机是中文 Windows 所以从来没暴露过。这里统一按 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass      # 老版本 Python 没有 reconfigure，忽略即可

REPO = Path(__file__).resolve().parents[1]
NAME = "BestdoriHelper"

#: 明确用不到的重物，排掉能省不少体积。
#: 实测（onedir 未排除前 396 MB）：cv2 112M / **playwright 103M** /
#: PySide6 92M / numpy.libs 21M / PIL 13M。
#: 其中 playwright 是最大的一块"白带的"—— 它自带一份 Chromium，只有 CLI 的
#: 浏览器导入用得到，而桌面版走的是后台接口（bridge/bestdori_api.py）。
#: 排除它不影响桌面版任何功能，只是打包版里点浏览器导入会提示未安装。
EXCLUDES = [
    "tkinter", "matplotlib", "pandas", "scipy.sparse.linalg",
    "playwright", "greenlet", "pyee",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtQuick3D",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtTest", "PySide6.QtDesigner",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="dist", help="输出目录（默认 dist）")
    ap.add_argument("--dir", action="store_true", help="只出目录，不压 zip")
    args = ap.parse_args()

    out = REPO / args.outdir
    work = out / "_pyinstaller_work"      # 放进输出目录，别去碰旧的 build/
    out.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",                       # 不打控制台窗口
        "--name", NAME,
        "--distpath", str(out),
        "--workpath", str(work),
        "--specpath", str(work),
        "--paths", str(REPO / "src"),
        # Web UI 的静态文件是按 __file__ 相对路径读的，必须一起带上
        "--add-data",
        f"{REPO / 'src' / 'bestdori_helper' / 'web' / 'static'}"
        f"{os.pathsep}bestdori_helper/web/static",
        # 入口不在包里，显式告诉它要收集这个包
        "--hidden-import", "bestdori_helper.gui",
    ]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(REPO / "scripts" / "win_entry.py"))

    print("输出目录：" + str(out))
    rc = subprocess.call(cmd, cwd=str(REPO))
    if rc != 0:
        print(f"✗ PyInstaller 失败（退出码 {rc}）", file=sys.stderr)
        return rc

    app_dir = out / NAME
    exe = app_dir / f"{NAME}.exe"
    if not exe.exists():
        print(f"✗ 没找到产物 {exe}", file=sys.stderr)
        return 1

    size = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file())
    print(f"\n✓ 打包完成：{app_dir}")
    print(f"  可执行文件 {exe.name}，目录共 {size / 1e6:.1f} MB")

    if not args.dir:
        zip_path = out / f"{NAME}-win.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in sorted(app_dir.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(out))
        print(f"✓ 分发包：{zip_path}（{zip_path.stat().st_size / 1e6:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
