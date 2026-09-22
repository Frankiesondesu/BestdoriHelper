"""抓一张桌面端「识别结果」页的实图，用来肉眼核对右侧核对面板的布局。

为什么需要：右侧核对面板是 QScrollArea，内容比视口高得多（约 890px / 530px），
控件一旦被挤出可视区，**测试不一定能发现**（布局不变量测试能覆盖一部分，
但「看起来对不对」还得看）。改过面板布局后跑一下这个脚本，看一眼就放心。

注意两点：
  * 不要设 QT_QPA_PLATFORM=offscreen —— 离屏模式不加载中文字体，全渲染成方块
  * 必须走真实事件循环（QTimer + app.exec），否则 QScrollArea 的延迟布局没结算，
    量到的几何是错的（实测同一个按钮会量成 x=509，真实值是 284）

产物：.bdh-test/gui_correction_panel.png
用法：python scripts/shot_gui_panel.py
"""
import os, sys, tempfile
sys.path.insert(0, "src")
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from bestdori_helper.config import Settings
from bestdori_helper.gui.app import MainWindow
from bestdori_helper.gui.theme import apply_theme
from bestdori_helper.inventory.store import Inventory
from bestdori_helper.models import Band, Card, Catalog, Character, OwnedCard
from bestdori_helper.vision.detect import Box
from bestdori_helper.vision.index import Match
from bestdori_helper.vision.recognize import RecognitionResult, RecognizedItem

app = QApplication.instance() or QApplication([])
apply_theme(app)
tmp = Path(tempfile.mkdtemp())
s = Settings(server="cn", home=tmp / "home"); s.ensure_dirs()

cat = Catalog(settings=s)
cat.bands[1] = Band(id=1, names=["Poppin'Party"] * 5)
cat.characters[1] = Character(id=1, names=["户山香澄"] * 5, band_id=1)
cat.characters[2] = Character(id=2, names=["凑友希那"] * 5, band_id=2)
for i in range(6):
    cat.cards[1100 + i] = Card(id=1100 + i, character_id=[1, 2][i % 2], rarity=3 + i % 3,
                               attribute=["powerful", "cool", "happy"][i % 3],
                               prefix=[f"卡名{i}"] * 5, resource_set_name=f"res{i:06d}")

inv = Inventory(s.inventory_path)
inv.add(OwnedCard(card_id=1101, trained=False, confidence=0.66,
                  matched_by="fingerprint", confirmed=False))
inv.save()

win = MainWindow(s)
win.thumb.request = lambda card, trained: None
win.state.catalog = cat
win.state.inventory = inv
win._refresh_state()

res = RecognitionResult(image="shot.png", width=400, height=300)
for i, cid in enumerate([1101, 1103, None]):
    res.items.append(RecognizedItem(
        box=Box(i * 200, 0, 200, 150), card_id=cid, trained=False,
        confidence=0.66 if cid else 0.0,
        status="ambiguous" if cid else "unknown",
        matched_by="fingerprint",
        candidates=[Match(card_id=1101 + k, trained=False, score=0.66 - k * 0.03,
                          hash_similarity=0.7, color_similarity=0.6) for k in range(3)],
    ))
win.state.scan_results = [res]
win._reload_result_table()
win.result_table.selectRow(0)
win.nav.setCurrentRow(1)          # 切到「识别结果」页
win.show()


def shoot():
    out = Path(".bdh-test/gui_correction_panel.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    win.grab().save(str(out))
    print("已保存", out, win.size().width(), "x", win.size().height(), flush=True)
    app.quit()


# 真机窗口：字体/主题都真实加载，离屏模式只会渲染成方块
QTimer.singleShot(2500, shoot)
app.exec()
