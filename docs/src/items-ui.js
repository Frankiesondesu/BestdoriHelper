/**
 * 「道具」页 —— 从游戏截图里识别区域道具等级。
 *
 * 独立成页的好处：不用为了改道具设置而进组队页，识别过程也不会打断计算。
 * 结果存 localStorage（`bdh.items.v1`），组队页初始化时自动读取。
 */

import { recognizeImage, parseOcrWords, readItemNames, prewarm, setItemNames } from './ocr.js';
import { loadItemTemplates, prepareGray, matchIconAt } from './item-match.js';
import { bandItemPct, everyoneItemPct, AREA_ITEM_CURVE } from './team.js';

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
/** 道具**明细**（OCR 出来的中文名）单独存一个键 —— 组队页要靠名字判断
 *  「这件属性道具管哪个属性」「这本杂志加哪一围」。等级本身仍是扁平表。 */
const STORE_DETAIL = 'bdh.itemsDetail.v1';

const $ = (id) => document.getElementById(id);

/** 读已保存的道具 {槽位: 等级} */
export function loadItems() {
  try {
    const raw = localStorage.getItem(STORE);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

/** 读道具明细 {槽位: {level, name, file}}（可能为空） */
export function loadItemDetail() {
  try {
    const raw = localStorage.getItem(STORE_DETAIL);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveItemDetail(d) {
  try { localStorage.setItem(STORE_DETAIL, JSON.stringify(d || {})); } catch { /* 配额 */ }
}

function saveItems(obj) {
  try { localStorage.setItem(STORE, JSON.stringify(obj)); } catch { /* 配額 */ }
}

/**
 * 等级 -> 实际加成百分比。
 *
 * ⚠️ 四个品类的曲线不一样，而且乐队道具/大家的话筒是**7 件叠加**，
 * 所以不能用一个「每级 1.5%」糊过去（旧版就是这么错的）。
 */
function toPct(slot, lv) {
  const n = Number(lv) || 0;
  if (n <= 0) return 0;
  const C = AREA_ITEM_CURVE;
  let pct;
  if (slot === 'Menu' || slot === 'Plaza') pct = C.attr(n);
  else if (slot === 'Magazine') pct = C.magazine(n);
  else if (slot === 'Everyone') pct = everyoneItemPct(n);
  else pct = bandItemPct(n);
  return Math.round(pct * 10) / 10;
}

/**
 * 三路证据合并，优先级：逐卡名称 > 图标 > 整图名字。
 *
 * - 逐卡名称：把每张卡的名字条裁出来单独 OCR（4 倍放大），准确率最高
 * - 图标：固定素材，能区分「同名不同件」，但几何一变就失效
 * - 整图名字：四列会糊在一起，只作兜底
 */
async function mergeResults(parsed, canvas, onStep) {
  const out = {
    items: { ...parsed.items },
    detail: [...parsed.detail],
    unmatched: [...parsed.unmatched],
    iconHits: 0,
    nameHits: 0,
  };
  if (!parsed.anchors?.length || !canvas) return out;

  // 1. 逐卡名称：裁「名称条」再单独 OCR。
  //    实测在部分版式上会退化成乱码（卡片版式有两种，深色条位置不一致），
  //    所以只在**整图名字没认出来**的时候补一刀，避免覆盖更可靠的结果。
  if (!Object.keys(out.items).length) {
    try {
      const names = await readItemNames(canvas, parsed.anchors, (p) => onStep && onStep('name', p));
      for (const n of names) {
        let slot = null;
        let used = '';
        for (const line of (n.lines?.length ? n.lines : [n.name])) {
          const s2 = matchSlotLoose(line);
          if (s2) { slot = s2; used = line; break; }
        }
        if (!slot) continue;
        out.nameHits += 1;
        out.items[slot] = Math.max(out.items[slot] || 0, n.level);
        out.detail.push({ slot, level: n.level, name: used, by: 'name' });
      }
    } catch { /* 名称这一路失败不影响图标 */ }
  }

  // 2. 图标
  try {
    const tpl = await loadItemTemplates();
    const G = prepareGray(canvas);
    for (const a of parsed.anchors) {
      const hit = matchIconAt(G, a, tpl);
      if (!hit) continue;
      out.iconHits += 1;
      out.items[hit.slot] = Math.max(out.items[hit.slot] || 0, a.level);
      out.detail.push({ slot: hit.slot, level: a.level, name: hit.name, by: 'icon' });
    }
  } catch { /* 图标不可用就只靠名称 */ }

  return out;
}

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
      <td class="mono">≈ ${toPct(slot, lv)}%</td>
    </tr>`;

  const bandRows = BANDS.filter((b) => b in items).map((b) => row(b, items[b])).join('');
  const otherRows = OTHER.filter((b) => b in items).map((b) => row(b, items[b])).join('');

  box.innerHTML = `<div style="overflow-x:auto"><table>
    <thead><tr><th>道具</th><th>等级</th><th>换算</th></tr></thead>
    <tbody>
      ${bandRows || '<tr><td colspan="3" class="footnote">没有乐队道具</td></tr>'}
      ${otherRows}
    </tbody></table></div>
    <p class="footnote">「换算」= 把这一套 7 件全装上时的加成。
    专有道具与共通道具<b>按槽位二选一</b>：装了「大家的键盘」就不能再装有咲的键盘，
    但不同乐器可以混装（大家的键盘 + 多惠的吉他没问题）。
    组队页会按实际队伍逐槽取优，所以这里两行的数字<b>不会相加</b>。</p>`;
}

function refreshPctInputs(items) {
  const bands = BANDS.filter((b) => b in items).map((b) => items[b]);
  const bandLv = bands.length ? Math.max(...bands) : 0;
  const attrLv = Math.max(items.Menu || 0, items.Plaza || 0);
  const magLv = Math.max(items.Magazine || 0, items.Everyone || 0);
  if ($('itBandPct')) $('itBandPct').value = toPct(BANDS.find((b) => b in items) || 'PoppinParty', bandLv);
  if ($('itAttrPct')) $('itAttrPct').value = toPct('Menu', attrLv);
  if ($('itMagPct')) $('itMagPct').value = toPct('Magazine', magLv);
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
  // 进页就在后台把 OCR 模型备好，用户点「识别」时不用等下载
  // 图标模板只有 74 张小图，进页就顺手拉下来（约 700KB）；
  // 同一份清单也喂给名字匹配（官方名模糊匹配比关键词表稳）
  loadItemTemplates()
    .then((list) => setItemNames(list.map((t) => ({ n: t.name, s: t.slot }))))
    .catch(() => {});
  prewarm((st) => {
    const prog = $('itProgress');
    if (!prog) return;
    if (st === 'loading') prog.textContent = 'OCR 模型后台准备中…（首次约 26MB，之后走缓存）';
    else if (st === 'ready') {
      prog.textContent = 'OCR 已就绪，选截图即可开始';
      setTimeout(() => { if (prog.textContent?.startsWith('OCR 已就绪')) prog.textContent = ''; }, 4000);
    } else if (st === 'failed') prog.textContent = 'OCR 模型加载失败（网络问题？），可改用「导入 JSON…」';
  });

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
      const unknown = [];
      let iconTotal = 0;
      let nameTotal = 0;

      try {
        for (let i = 0; i < files.length; i++) {
          prog.textContent = `识别中 ${i + 1}/${files.length}…（首次约 26MB，随站点下载）`;
          let res = null;
          try {
            res = await recognizeImage(files[i], (p) => {
              prog.textContent = `识别中 ${i + 1}/${files.length}… ${Math.round(p * 100)}%`;
            });
          } catch (err) {
            failed.push(`${files[i].name}（${err.message}）`);
            continue;
          }
          const parsed = parseOcrWords(res.words);
          const r = await mergeResults(parsed, res.canvas, (kind, p) => {
            if (kind === 'name') prog.textContent = `识别中 ${i + 1}/${files.length}… 读道具名 ${Math.round(p * 100)}%`;
          });
          const n = Object.keys(r.items).length;
          if (n) {
            ok += 1;
            for (const [slot, lv] of Object.entries(r.items)) {
              if (!(slot in merged) || lv > merged[slot]) merged[slot] = lv;
            }
          } else {
            failed.push(files[i].name);
          }
          iconTotal += r.iconHits || 0;
          nameTotal += r.nameHits || 0;
          for (const u of r.unmatched) unknown.push(u);
        }

        saveItems(merged);
        renderTable(merged);
        refreshPctInputs(merged);
        prog.textContent = `完成：${ok}/${files.length} 张识别成功`
          + (nameTotal || iconTotal ? `（名称 ${nameTotal} 件 / 图标 ${iconTotal} 件）` : '')
          + (failed.length ? `；未读出：${failed.join('、')}` : '')
          + (unknown.length ? `；认不准 ${unknown.length} 件（${unknown.slice(0, 4).join('、')}${unknown.length > 4 ? '…' : ''}）` : '');
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
        // my-items.json 里还有 detail（OCR 出来的中文名），组队页要靠它判断
        // 「属性道具管哪个属性」「杂志加哪一围」
        if (data.detail && typeof data.detail === 'object') saveItemDetail(data.detail);
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
      saveItemDetail({});
      renderTable({});
      refreshPctInputs({});
      $('itProgress').textContent = '已清空';
    };

    $('itSavePct').onclick = savePct;
  }
}
