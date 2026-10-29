/**
 * 「道具」页 —— 从游戏截图里识别区域道具等级。
 *
 * 独立成页的好处：不用为了改道具设置而进组队页，识别过程也不会打断计算。
 * 结果存 localStorage（`bdh.items.v1`），组队页初始化时自动读取。
 */

import { recognizeImage, parseOcrLines } from './ocr.js';

const ITEM_SLOTS_CN = {
  PoppinParty: "Poppin'Party", Afterglow: 'Afterglow', PastelPalettes: 'Pastel*Palettes',
  Roselia: 'Roselia', HelloHappyWorld: 'Hello Happy World', Morfonica: 'Morfonica',
  RaiseASuilen: 'RAISE A SUILEN', MyGO: 'MyGO!!!!!', Everyone: '全员（CiRCLE）',
  Magazine: '杂志', Plaza: '广场', Menu: '咖啡厅',
};

const BANDS = ['PoppinParty', 'Afterglow', 'PastelPalettes', 'Roselia',
  'HelloHappyWorld', 'Morfonica', 'RaiseASuilen', 'MyGO'];
const OTHER = ['Menu', 'Plaza', 'Magazine', 'Everyone'];

const STORE = 'bdh.items.v1';

const $ = (id) => document.getElementById(id);

/** 读已保存的道具 {槽位: 等级} */
export function loadItems() {
  try {
    const raw = localStorage.getItem(STORE);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveItems(obj) {
  try { localStorage.setItem(STORE, JSON.stringify(obj)); } catch { /* 配額 */ }
}

/** 等级 -> 近似百分比（每级 1.5%）；界面可改 */
function toPct(lv) { return Math.round(lv * 1.5 * 10) / 10; }

function renderTable(items) {
  const box = $('itTable');
  const entries = Object.entries(items || {});
  if (!entries.length) {
    box.innerHTML = '<div class="empty">还没有数据。选几张截图，或导入 my-items.json。</div>';
    return;
  }

  const row = (slot, lv) => `<tr>
      <td>${ITEM_SLOTS_CN[slot] || slot}</td>
      <td class="mono">Lv${lv}</td>
      <td class="mono">≈ ${toPct(lv)}%</td>
    </tr>`;

  const bandRows = BANDS.filter((b) => b in items).map((b) => row(b, items[b])).join('');
  const otherRows = OTHER.filter((b) => b in items).map((b) => row(b, items[b])).join('');

  box.innerHTML = `<div style="overflow-x:auto"><table>
    <thead><tr><th>道具</th><th>等级</th><th>换算</th></tr></thead>
    <tbody>
      ${bandRows || '<tr><td colspan="3" class="footnote">没有乐队道具</td></tr>'}
      ${otherRows}
    </tbody></table></div>`;
}

function refreshPctInputs(items) {
  const bands = BANDS.filter((b) => b in items).map((b) => items[b]);
  const bandLv = bands.length ? Math.max(...bands) : 0;
  const attrLv = Math.max(items.Menu || 0, items.Plaza || 0);
  const magLv = Math.max(items.Magazine || 0, items.Everyone || 0);
  if ($('itBandPct')) $('itBandPct').value = toPct(bandLv);
  if ($('itAttrPct')) $('itAttrPct').value = toPct(attrLv);
  if ($('itMagPct')) $('itMagPct').value = toPct(magLv);
}

/** 把当前三个百分比写进组队页用的设置里 */
function savePct() {
  try {
    const raw = localStorage.getItem('bdh.team.settings.v1');
    const cfg = raw ? JSON.parse(raw) : {};
    cfg.bandPct = $('itBandPct').value;
    cfg.attrPct = $('itAttrPct').value;
    cfg.magPct = $('itMagPct').value;
    localStorage.setItem('bdh.team.settings.v1', JSON.stringify(cfg));
    $('itProgress').textContent = '✓ 已保存，组队页会使用这三个值';
    setTimeout(() => { $('itProgress').textContent = ''; }, 3000);
  } catch (e) {
    $('itProgress').textContent = '保存失败：' + e.message;
  }
}

export function initItemsTab() {
  const items = loadItems();
  renderTable(items);
  refreshPctInputs(items);

  if ($('itPick') && !$('itPick').dataset.bound) {
    $('itPick').dataset.bound = '1';

    $('itPick').onclick = () => $('itFiles').click();

    $('itFiles').onchange = async (e) => {
      const files = [...(e.target.files || [])];
      if (!files.length) return;
      const prog = $('itProgress');
      const merged = { ...loadItems() };
      let ok = 0;
      const failed = [];

      try {
        for (let i = 0; i < files.length; i++) {
          prog.textContent = `识别中 ${i + 1}/${files.length}…（首次需下载模型约 10MB）`;
          let lines = null;
          try {
            lines = await recognizeImage(files[i], (p) => {
              prog.textContent = `识别中 ${i + 1}/${files.length}… ${Math.round(p * 100)}%`;
            });
          } catch (err) {
            failed.push(`${files[i].name}（${err.message}）`);
            continue;
          }
          const r = parseOcrLines(lines);
          const n = Object.keys(r.items).length;
          if (n) {
            ok += 1;
            for (const [slot, lv] of Object.entries(r.items)) {
              if (!(slot in merged) || lv > merged[slot]) merged[slot] = lv;
            }
          } else {
            failed.push(files[i].name);
          }
        }

        saveItems(merged);
        renderTable(merged);
        refreshPctInputs(merged);
        prog.textContent = `完成：${ok}/${files.length} 张识别成功`
          + (failed.length ? `；未读出：${failed.join('、')}` : '');
      } catch (err) {
        prog.textContent = 'OCR 失败：' + err.message;
      }
      e.target.value = '';
    };

    $('itImportJson').onclick = () => $('itJsonFile').click();
    $('itJsonFile').onchange = async (e) => {
      const f = e.target.files?.[0];
      if (!f) return;
      try {
        const data = JSON.parse(await f.text());
        // 支持两种：{items:{槽位:[id,lv]}} 或 {槽位:lv} 或 {槽位:[id,lv]}
        const src = data.items || data;
        const out = {};
        for (const [slot, v] of Object.entries(src)) {
          if (Array.isArray(v)) out[slot] = Number(v[1] ?? v[0]) || 0;
          else out[slot] = Number(v) || 0;
        }
        saveItems(out);
        renderTable(out);
        refreshPctInputs(out);
        $('itProgress').textContent = `已导入 ${Object.keys(out).length} 个槽位`;
      } catch (err) {
        $('itProgress').textContent = '导入失败：' + err.message;
      }
      e.target.value = '';
    };

    $('itClear').onclick = () => {
      saveItems({});
      renderTable({});
      refreshPctInputs({});
      $('itProgress').textContent = '已清空';
    };

    $('itSavePct').onclick = savePct;
  }
}
