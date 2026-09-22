"""自检：导入页的截图缩略图条。

网页版导入截图后，原来只显示一行文字「已选 N 张：文件名、文件名…」——
选错文件夹、多选了一张都得等识别完才发现。缩略图条让人一眼看见选了什么。

这个脚本验 11 项：未导入时隐藏 / 导入后出现 / 数量与计数正确 /
缩略图真的解码出来 / 有文件名 / 点缩略图能放大 / 点 × 能移除 / 清空后隐藏。

桌面端不需要这个 —— 它的文件列表本来就带 64×48 缩略图图标（保持宽高比）。

产物：.bdh-test/import_strip.png
用法：python scripts/import_strip_check.py            # 打本地
    python scripts/import_strip_check.py --url <线上地址>   # 打线上
"""
import argparse, pathlib, functools, http.server, threading, sys
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--url", default=None, help="已有服务地址；不给就自己起一个")
ap.add_argument("--port", type=int, default=8803)
args = ap.parse_args()

REPO = pathlib.Path(__file__).resolve().parents[1]
photos = sorted((REPO / "Photo").glob("*.jpg"))[:3]
httpd = None
if args.url:
    URL = args.url.rstrip("/")
else:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(REPO / "docs"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    URL = f"http://127.0.0.1:{args.port}"

ok = fail = 0
def check(cond, msg):
    global ok, fail
    print(f"  {'✓' if cond else '✗'} {msg}")
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)

try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
        page = b.new_context(viewport={"width": 1200, "height": 900}).new_page()
        page.goto(URL, wait_until="load", timeout=90000)
        page.wait_for_function(
            "() => document.getElementById('statusText')?.textContent?.includes('就绪')",
            timeout=180000)

        check(page.is_hidden("#fileCard"), "未导入时缩略图卡是隐藏的")

        page.set_input_files("#fileInput", [str(p) for p in photos])
        page.wait_for_timeout(1500)
        check(page.is_visible("#fileCard"), "导入后缩略图卡出现")
        n = page.eval_on_selector_all(".file-thumb", "els => els.length")
        check(n == len(photos), f"缩略图数量 {n}（期望 {len(photos)}）")
        check(page.text_content("#fileCount") == str(len(photos)), "计数文字正确")

        # 图片真的解码出来了
        loaded = page.eval_on_selector_all(
            ".file-thumb img", "els => els.filter(i => i.complete && i.naturalWidth > 0).length")
        check(loaded == len(photos), f"{loaded}/{len(photos)} 张缩略图成功解码")
        names = page.eval_on_selector_all(".file-thumb figcaption", "els => els.map(e => e.textContent)")
        check(all(names), f"每张都有文件名：{names[0][:24]}…")

        # 点缩略图 → 弹层
        page.eval_on_selector(".file-thumb", "el => el.click()")
        page.wait_for_timeout(700)
        check(page.is_visible("#lightbox"), "点缩略图能打开弹层")
        check("lightbox" not in page.text_content("#lbTitle").lower(),
              f"弹层标题是文件名：{page.text_content('#lbTitle')[:30]}")
        page.eval_on_selector("#lbClose", "el => el.click()")
        page.wait_for_timeout(400)

        # 移除一张
        page.eval_on_selector(".file-del", "el => el.click()")
        page.wait_for_timeout(600)
        n2 = page.eval_on_selector_all(".file-thumb", "els => els.length")
        check(n2 == len(photos) - 1, f"点 × 后剩 {n2} 张")
        check(page.text_content("#fileCount") == str(len(photos) - 1), "计数同步更新")

        # 清空
        page.eval_on_selector("#btnClear", "el => el.click()")
        page.wait_for_timeout(600)
        check(page.is_hidden("#fileCard"), "清空后缩略图卡隐藏")

        # 截图（有图的状态）
        page.set_input_files("#fileInput", [str(p) for p in photos])
        page.wait_for_timeout(1500)
        out = REPO / ".bdh-test" / "import_strip.png"
        page.screenshot(path=str(out))
        print(f"  截图：{out.relative_to(REPO)}")
        b.close()
finally:
    if httpd:
        httpd.shutdown()

print(f"\n通过 {ok} / 失败 {fail}")
sys.exit(1 if fail else 0)
