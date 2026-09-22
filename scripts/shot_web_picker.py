"""抓「自选卡面」面板的截图，用来肉眼确认缩略图都正常。

为什么需要：卡池里 188 张特殊卡没有缩略图，前端靠 thumbs.json 清单挑变体、
缺图渲染占位块。清单或排序一改就可能退化（踩过：把默认排序改成「新卡在前」
之后，缺图的 90000+ 段占满开头，看着像「全部 404」）。跑一下这个脚本，
看一眼默认视图开头是不是都有图。

产物：.bdh-test/picker_default.png

用法：
    python scripts/shot_web_picker.py            # 打线上
    python scripts/shot_web_picker.py --local     # 起本地服务打本地
"""
import pathlib, functools, http.server, threading, sys
from playwright.sync_api import sync_playwright

REPO = pathlib.Path(__file__).resolve().parents[1]
local = "--local" in sys.argv
httpd = None
if local:
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(REPO / "docs"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 8802), h)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    URL = "http://127.0.0.1:8802"
else:
    URL = "https://frankiesondesu.github.io/BestdoriHelper"

try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
        page = b.new_context(viewport={"width": 1200, "height": 900}).new_page()
        page.goto(URL, wait_until="load", timeout=90000)
        page.wait_for_function(
            "() => document.getElementById('statusText')?.textContent?.includes('就绪')",
            timeout=180000)
        page.evaluate("""() => {
            const S = window.__bdh.S;
            S.shots = [{name:'t', w:100, h:100, url:'', cells:[
              {box:{x:0,y:0,w:50,h:50}, cardId:1858, trained:false, score:0.9, gap:0.1, top3:[]}]}];
            window.__bdh.buildInventoryFromShots();
            window.__bdh.renderAll();
            window.__bdh.showTab('result');
        }""")
        page.wait_for_timeout(800)
        page.eval_on_selector("[data-pick-manual]", "el => el.click()")
        page.wait_for_selector("#cardSearch", timeout=15000)
        page.wait_for_timeout(4000)
        out = REPO / ".bdh-test" / "picker_default.png"
        page.screenshot(path=str(out))
        print(f"已保存 {out.relative_to(REPO)}")
        b.close()
finally:
    if httpd:
        httpd.shutdown()
