"""一次性诊断：把官网顶部栏 / 侧栏的可见链接全 dump 出来，确认登录入口在哪。

不猜 —— 之前 PPT 里写「右上角 Log In」，得先验证。
用法：python scripts/_probe_header.py
"""

from __future__ import annotations

import json

from playwright.sync_api import sync_playwright

URL = "https://bestdori.com/profile/manager"


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  device_scale_factor=1)
        pg = ctx.new_page()
        pg.goto(URL, wait_until="load", timeout=90000)
        pg.wait_for_timeout(5000)
        try:
            pg.get_by_text("Close", exact=True).first.click(timeout=8000)
        except Exception:
            pass
        pg.wait_for_timeout(1500)
        pg.evaluate("window.scrollTo(0, 0)")
        pg.wait_for_timeout(500)

        data = pg.evaluate("""() => {
            const vis = e => {
                const r = e.getBoundingClientRect();
                return r.width > 1 && r.height > 1;
            };
            const all = [...document.querySelectorAll('a, button')].filter(vis);
            return all.map(e => {
                const r = e.getBoundingClientRect();
                return {
                    tag: e.tagName,
                    text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 30),
                    href: e.getAttribute('href') || '',
                    x: Math.round(r.x), y: Math.round(r.y),
                    w: Math.round(r.width), h: Math.round(r.height)
                };
            });
        }""")

        print("== 顶部栏（y < 70）==")
        for d in data:
            if d["y"] < 70:
                print(f"  {d['tag']:<6} ({d['x']:>4},{d['y']:>3}) {d['w']:>4}×{d['h']:<3} "
                      f"{d['text']!r} {d['href'][:50]}")

        print("\n== 含 log / sign / account 的链接 ==")
        for d in data:
            blob = (d["text"] + d["href"]).lower()
            if any(k in blob for k in ("log", "sign", "account", "oauth", "twitter", "discord")):
                print(f"  {d['tag']:<6} ({d['x']:>4},{d['y']:>3}) {d['w']:>4}×{d['h']:<3} "
                      f"{d['text']!r} {d['href'][:60]}")

        print("\n== 侧栏（x < 240 的链接）==")
        for d in data:
            if d["x"] < 240 and d["tag"] == "A":
                print(f"  ({d['x']:>4},{d['y']:>4}) {d['w']:>4}×{d['h']:<3} "
                      f"{d['text']!r} {d['href'][:40]}")

        # 分组标题（HOME / COMMUNITY / ... / PROFILE）不是链接，单独抓
        groups = pg.evaluate("""() => {
            const names = ['HOME','COMMUNITY','LEADERBOARD','GAME','INFO','TOOL','PROFILE'];
            const vis = e => { const r = e.getBoundingClientRect();
                               return r.width > 1 && r.height > 1; };
            return [...document.querySelectorAll('div,span,p,a,button')]
                .filter(vis)
                .filter(e => names.includes((e.innerText || '').trim()) &&
                             e.getBoundingClientRect().x < 240)
                .map(e => { const r = e.getBoundingClientRect();
                            return {text: e.innerText.trim(),
                                    x: Math.round(r.x), y: Math.round(r.y),
                                    w: Math.round(r.width), h: Math.round(r.height)};
                });
        }""")
        print("\n== 侧栏分组标题 ==")
        seen = set()
        for g in groups:
            if g["text"] in seen:
                continue
            seen.add(g["text"])
            print(f"  ({g['x']:>4},{g['y']:>4}) {g['w']:>4}×{g['h']:<3} {g['text']!r}")

        # 主区标题（Profiles / Cloud Storage / Import / Premade Profile）
        # 它们不是 h1/h2/h3，按「字号 >= 20px 且在主区内」来抓
        heads = pg.evaluate("""() => {
            const vis = e => { const r = e.getBoundingClientRect();
                               return r.width > 1 && r.height > 1; };
            return [...document.querySelectorAll('div,span,p,h1,h2,h3')]
                .filter(vis)
                .filter(e => e.getBoundingClientRect().x > 240 &&
                             parseFloat(getComputedStyle(e).fontSize) >= 20 &&
                             e.children.length === 0 &&
                             (e.innerText || '').trim())
                .map(e => { const r = e.getBoundingClientRect();
                            return {text: e.innerText.trim().slice(0, 30),
                                    size: Math.round(parseFloat(getComputedStyle(e).fontSize)),
                                    x: Math.round(r.x), y: Math.round(r.y),
                                    w: Math.round(r.width), h: Math.round(r.height)};
                });
        }""")
        print("\n== 主区大字号文本 ==")
        for h in heads:
            print(f"  ({h['x']:>4},{h['y']:>4}) {h['w']:>4}×{h['h']:<3} "
                  f"{h['size']:>3}px  {h['text']!r}")

        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
