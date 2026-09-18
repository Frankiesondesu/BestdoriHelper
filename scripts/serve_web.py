"""启动网页版（docs/ 是纯静态站点，起一个本地静态服务即可）。

为什么要起服务而不是直接双击 index.html：网页版用了 ES module
（``import('./src/*.js')``）和 fetch，**``file://`` 协议下会被浏览器的同源
策略挡掉**，必须通过 http:// 访问。

用法::

    python scripts/serve_web.py          # 自动挑端口并打开浏览器
    python scripts/serve_web.py 8080     # 指定端口

对应根目录的 ``run-web.bat``，双击那个即可。
"""

from __future__ import annotations

import functools
import http.server
import socket
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """安静一点，别把每个请求都打到控制台（会刷屏）。"""

    def log_message(self, *args) -> None:  # noqa: ANN002
        pass


def pick_port(preferred: int | None = None, tries: int = 30) -> int | None:
    """挑一个空闲端口；指定的端口被占用时往后找一个。"""
    if preferred:
        candidates = [preferred]
    else:
        candidates = []
    candidates += list(range(8000, 8000 + tries))
    for p in candidates:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return None


def main() -> int:
    if not (DOCS / "index.html").exists():
        print(f"✗ 没找到 {DOCS / 'index.html'}，脚本要在仓库里运行")
        return 1

    preferred = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    port = pick_port(preferred)
    if port is None:
        print("✗ 找不到可用端口")
        return 1

    handler = functools.partial(_QuietHandler, directory=str(DOCS))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)

    url = f"http://127.0.0.1:{port}/"
    print("=" * 46)
    print(f"  网页版已启动：{url}")
    print("  服务的是 docs/（纯静态，不上传任何数据）")
    print("  关闭这个窗口就会停止服务")
    print("=" * 46)

    # 稍微等服务器就绪再开浏览器，否则可能白屏
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
