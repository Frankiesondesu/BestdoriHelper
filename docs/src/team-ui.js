/**
 * 组队页 UI：选难度/歌曲 → 从「清单」里的卡池算最优 5 人队。
 *
 * 数据全部来自本地（不需要联网、不需要代理）：
 *   data/team.json             卡牌三围/技能/角色
 *   data/songs.json            曲名与难度
 *   data/charts/{diff}/{id}.json   预拉的谱面（note 时间表）
 */

import { parseChart, bestTeams, eventBonusFor, eventPoints } from './team.js';
import { parseProfile } from './profile.js';
import { recognizeImage, parseOcrLines } from './ocr.js';
import { fetchChart } from './chart-fetch.js';

let TEAM = null;
let SONGS = null;
let EVENTS = null;
/** 导入的 Bestdori 档案（有值时优先用它当卡池） */
let PROFILE = null;

const $ = (id) => document.getElementById(id);

async function ensureData() {
  if (TEAM && SONGS) return;
  const [t, s, e] = await Promise.all([
    fetch('data/team.json').then((r) => r.json()),
    fetch('data/songs.json').then((r) => r.json()),
    fetch('data/events.json').then((r) => r.json()),
  ]);
  TEAM = t;
  SONGS = s;
  EVENTS = e;
}

/** 按当前难度重填歌曲下拉 */
const EVENT_TYPE_CN = {
  mission_live: '任务', live_try: '试炼', versus: '竞演', medley: '组曲',
  festival: 'Fes', challenge: '挑战', story: '故事',
};

function fillEvents() {
  const sel = $('tmEvent');
  if (!sel || sel.dataset.ready) return;

  const now = Math.floor(Date.now() / 1000);
  // **按开始时间倒序**，不是按 eventId —— 编号大不代表时间新
  // （国服 5001 编号很大，实际是 2022 年的活动）
  const list = Object.entries(EVENTS).sort((a, b) => (b[1].s || 0) - (a[1].s || 0));

  const fmt = (sec) => {
    if (!sec) return '';
    const d = new Date(sec * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };

  sel.innerHTML = '<option value="">不计活动加成</option>' + list.map(([id, e]) => {
    const live = e.s && e.e && e.s <= now && now <= e.e;
    const date = fmt(e.s);
    const tag = live ? '进行中 · ' : (e.e && now > e.e ? '' : '');
    return `<option value="${id}">${tag}[${EVENT_TYPE_CN[e.ty] || e.ty}] ${date} ${e.n || id}</option>`;
  }).join('');
  sel.dataset.ready = '1';

  // 默认选中：进行中的那期；没有就选时间最新的
  const live = list.find(([, e]) => e.s && e.e && e.s <= now && now <= e.e);
  const pick = live ? live[0] : list[0]?.[0];
  if (pick && !sel.value) {
    sel.value = pick;
    setTimeout(() => { if ($('tmEvent').value === pick) updateEventHint(); }, 0);
  }
}

/** 活动选中后在提示行显示加成摘要 */
function updateEventHint() {
  const id = $('tmEvent').value;
  const hint = $('tmHint');
  if (!hint) return;
  if (!id) { hint.textContent = ''; return; }
  const e = EVENTS[id];
  if (!e) return;
  const parts = [];
  if (e.attr?.length) parts.push('属性 ' + e.attr.map((a) => `${a.a}+${a.p}%`).join('/'));
  if (e.ch?.length) parts.push(`角色 ${e.ch.length} 人 +${e.ch[0].p}%`);
  if (e.ab?.pointPercent) parts.push(`双重 +${e.ab.pointPercent}% 点`);
  if (e.ab?.parameterPercent) parts.push(`双重 +${e.ab.parameterPercent}% 综合力`);
  hint.textContent = parts.join(' · ');
}

const BAND_NAME = {
  1: "Poppin'Party", 2: 'Afterglow', 3: 'Pastel*Palettes', 4: 'Roselia',
  5: 'Hello, Happy World!', 6: 'Morfonica', 7: 'RAISE A SUILEN',
  8: 'MyGO!!!!!', 9: 'Ave Mujica', 10: '其他',
};

/** 封面 URL。N = max(10, floor(songId/10)*10)（Bestdori 的资源分桶） */
export function jacketUrl(sid, name, server = 'cn') {
  if (!name) return '';
  // N 是**向上取整**到 10 的倍数：12->20、47->50、128->130、777->780，1~9->10。
  // 写成向下取整会让绝大多数歌 404；而 Bestdori 对不存在的路径返回 SPA 的
  // HTML（HTTP 200），所以只表现为"图片显示不出来"，不会报错。
  const n = Math.max(10, Math.ceil(Number(sid) / 10) * 10);
  return `https://bestdori.com/assets/${server}/musicjacket/musicjacket${n}_rip/`
    + `assets-star-forassetbundle-startapp-musicjacket-musicjacket${n}-${name}-jacket.png`;
}

/** 某首歌在指定难度下的等级 */
function levelOf(s, diff) { return s?.lv?.[diff] ?? null; }

const PAGE_SIZE = 40;      // 一次只渲染 40 行 —— 避免同时请求几百张封面
let songPage = 0;
let songFiltered = [];
/** 当前选中的歌曲 ID。
 *  ⚠️ 不要存在那个 <select> 里：select 是空的（没有 option），给它设 value
 *  会直接变成空字符串，表现就是"点了但一直显示未选择"。 */
let pickedSongId = '';

/** 当前筛选后的歌曲（不渲染） */
function filterSongs() {
  const diff = $('songDiff').value || $('tmDiff').value;
  const band = $('songBand').value;
  const q = ($('songSearch')?.value || '').trim().toLowerCase();

  let list = Object.entries(SONGS).filter(([, s]) => (s.d || []).includes(diff));
  if (band) list = list.filter(([, s]) => String(s.b) === band);
  if (q) {
    list = list.filter(([, s]) =>
      (s.t || '').toLowerCase().includes(q) ||
      (BAND_NAME[s.b] || '').toLowerCase().includes(q));
  }
  list.sort((a, b) => (levelOf(a[1], diff) ?? 0) - (levelOf(b[1], diff) ?? 0));
  return list;
}

function songRowHtml(id, s, diff) {
  const lv = levelOf(s, diff);
  return `<button class="song-row" data-sid="${id}"
      style="display:flex;gap:10px;align-items:center;text-align:left;padding:7px 9px;
             border-radius:9px;border:1px solid var(--border-2);background:var(--panel-2);
             cursor:pointer;width:100%">
    <img src="${jacketUrl(id, s.j)}" alt="" referrerpolicy="no-referrer" loading="lazy"
         decoding="async"
         style="width:44px;height:44px;border-radius:7px;object-fit:cover;flex:none;background:var(--panel)">
    <span style="flex:1;min-width:0">
      <span style="display:block;font-size:14px;font-weight:600;overflow:hidden;
                   text-overflow:ellipsis;white-space:nowrap">${s.t}</span>
      <span style="display:block;font-size:12px;color:var(--text-dim);margin-top:2px">
        ${BAND_NAME[s.b] || '—'} · ${(s.d || []).length} 难度</span>
    </span>
    <span style="flex:none;font-size:13px;font-weight:700;color:var(--accent)">Lv${lv ?? '?'}</span>
  </button>`;
}

/** 追加一页 */
function appendPage() {
  const diff = $('songDiff').value || $('tmDiff').value;
  const from = songPage * PAGE_SIZE;
  const slice = songFiltered.slice(from, from + PAGE_SIZE);
  const wrap = document.createElement('div');
  wrap.style.display = 'contents';
  wrap.innerHTML = slice.map(([id, s]) => songRowHtml(id, s, diff)).join('');
  wrap.querySelectorAll('[data-sid]').forEach((el) => {
    el.onclick = () => {
      pickedSongId = el.dataset.sid;
      updatePicked();
      closeSongPicker();
    };
  });
  $('songList').appendChild(wrap);
  songPage += 1;
  const shown = Math.min(songPage * PAGE_SIZE, songFiltered.length);
  $('songMore').textContent = songFiltered.length
    ? `已显示 ${shown} / ${songFiltered.length}${shown < songFiltered.length ? '（继续滚动加载）' : ''}`
    : '没有匹配的歌曲';
}

/** 重新筛选（重置到第一页） */
function renderSongList(reset = true) {
  songFiltered = filterSongs();
  if (reset) { songPage = 0; $('songList').innerHTML = ''; }
  appendPage();
}

function openSongPicker() {
  $('songLb').hidden = false;
  renderSongList(true);
  $('songSearch')?.focus();
}

function closeSongPicker() {
  $('songLb').hidden = true;
  // 关掉就把已渲染的行清掉，避免图片缓存一直占着
  $('songList').innerHTML = '';
  songPage = 0;
}

function updatePicked() {
  const s = SONGS[pickedSongId];
  const diff = $('tmDiff').value;
  $('tmSongPicked').textContent = s
    ? `${s.t} · ${diff.toUpperCase()} Lv${levelOf(s, diff) ?? '?'}`
    : '未选择';
}

function pool() {
  // 导入过档案就用档案的卡池（完整），否则用清单里识别到的卡
  if (PROFILE) {
    const out = [];
    for (const c of PROFILE.cards) {
      const meta = TEAM.cards[String(c.id)];
      if (meta) out.push({ ...meta, id: c.id, trained: c.trained });
    }
    return out;
  }
  const state = window.__BDH_STATE;
  if (!state) return [];
  const ids = [...state.inv.values()]
    .map((x) => x.cardId)
    .filter((id) => id != null);
  const seen = new Set();
  const out = [];
  for (const id of ids) {
    const c = TEAM.cards[String(id)];
    if (!c || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, ...c });
  }
  return out;
}

function teamCards(list) {
  return list.map((c) => {
    const info = (window.__BDH_STATE?.meta?.cards || {})[String(c.id)] || {};
    const name = info.name || `#${c.id}`;
    return `<img src="data/thumbs/${c.id}_n.webp" alt="${name}" title="${name}"
                 loading="lazy" onerror="this.onerror=null;this.src='data/thumbs/${c.id}_t.webp'">`;
  }).join('');
}

function render(results, meta) {
  if (!results.length) {
    $('tmBody').innerHTML = '<div class="empty">没有可用的卡。先在「结果」页识别截图。</div>';
    return;
  }
  const rows = results.map((r, i) => `
    <tr>
      <td class="mono">${i + 1}</td>
      <td><div class="tm-team">${teamCards(r.team)}</div></td>
      <td class="mono">${r.power.toLocaleString()}</td>
      <td class="mono">${r.score.toLocaleString()}</td>
      <td class="mono">${r.points == null ? '—' : `<b>${r.points.toLocaleString()}</b>`}</td>
    </tr>`).join('');

  $('tmBody').innerHTML = `
    <div class="card">
      <div class="card-label">最优队伍 <span class="footnote">${meta.diff} · Lv${meta.level} ·
        ${meta.notes} NOTE · 技能等级 ${meta.skillLv}</span></div>
      <div style="overflow-x:auto">
        <table>
          <thead><tr><th>#</th><th>队伍（左起为队长）</th><th>综合力</th>
            <th>预计分数</th><th>活动点数</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="footnote">分数模型：综合力 × Lv加成 × 300% × 判定/COMBO/技能。
        用于横向比较队伍强弱，绝对值与实战可能有几个百分点偏差。</p>
    </div>`;
}

// ---------------------------------------------------------------------------
// 设置持久化
// ---------------------------------------------------------------------------

const STORE_KEY = 'bdh.team.settings.v1';

/** 把当前面板上的设置读成一个对象 */
function readSettings() {
  return {
    diff: $('tmDiff').value,
    skillLv: $('tmSkillLv').value,
    rank: $('tmRank').value,
    eventId: $('tmEvent').value,
    bandSel: $('tmBandSel').value,
    bandPct: $('tmBand').value,
    attrSel: $('tmAttrSel').value,
    attrPct: $('tmAttr').value,
    magPct: $('tmMag').value,
  };
}

function applySettings(cfg) {
  if (!cfg) return;
  const set = (id, v) => { if (v != null && $(id)) $(id).value = v; };
  set('tmSkillLv', cfg.skillLv); set('tmRank', cfg.rank);
  set('tmEvent', cfg.eventId);
  set('tmBandSel', cfg.bandSel); set('tmBand', cfg.bandPct);
  set('tmAttrSel', cfg.attrSel); set('tmAttr', cfg.attrPct);
  set('tmMag', cfg.magPct);
  if (cfg.diff && cfg.diff !== $('tmDiff').value) { $('tmDiff').value = cfg.diff; renderSongList(); }
}

/** 任何一项变了就存一次（localStorage，换页/刷新都还在） */
function persist() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(readSettings())); } catch { /* 无痕模式 */ }
}

function restore() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) { applySettings(JSON.parse(raw)); return true; }
  } catch { /* 忽略坏数据 */ }
  return false;
}

function bindPersistence() {
  for (const id of ['tmSkillLv', 'tmRank', 'tmEvent', 'tmBandSel', 'tmBand', 'tmAttrSel', 'tmAttr', 'tmMag']) {
    const el = $(id);
    if (el && !el.dataset.persist) { el.addEventListener('change', persist); el.dataset.persist = '1'; }
  }
}

/** 导出：给一份可以备份/换设备用的 JSON */
function exportSettings() {
  const cfg = { _type: 'bdh-team-settings', _version: 1, ...readSettings() };
  const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'bdh-team-settings.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function importSettings(file) {
  try {
    const cfg = JSON.parse(await file.text());
    applySettings(cfg);
    persist();
    $('tmHint').textContent = '已导入设置';
    setTimeout(() => { $('tmHint').textContent = ''; }, 2500);
  } catch (e) {
    $('tmHint').textContent = '导入失败：' + e.message;
  }
}

/** 导入 Bestdori 档案：拿到完整卡池 + 道具设置 */
function bindProfileImport() {
  const btn = $('tmImportProfile');
  if (!btn || btn.dataset.bound) return;
  btn.dataset.bound = '1';
  btn.onclick = () => $('tmProfileFile').click();
  $('tmProfileFile').onchange = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      const json = JSON.parse(await f.text());
      PROFILE = parseProfile(json, { maxCardId: 999999 });
      applyProfileItems(PROFILE.items);
      const pool0 = pool();
      setPoolSource('profile', PROFILE.note);
      $('tmPoolCount').textContent = pool0.length;
      $('tmClearProfile').hidden = false;
      renderImported(pool0);
      persist();
    } catch (err) {
      $('tmHint').textContent = '导入失败：' + err.message;
    }
    e.target.value = '';
  };
  // 道具识别已独立成「道具」页；这里只负责把结果显示出来 + 提供跳转
  if ($('tmGoItems')) {
    $('tmGoItems').onclick = () => {
      const nav = document.querySelector('.navitem[data-tab="items"]');
      if (nav) nav.click();
    };
  }

  $('tmClearProfile').onclick = () => {
    PROFILE = null;
    $('tmPoolSource').textContent = '清单里已识别的卡';
    $('tmPoolCount').textContent = pool().length;
    $('tmClearProfile').hidden = true;
    if ($('tmImported')) $('tmImported').hidden = true;
    setPoolSource('list');
    $('tmPoolCount').textContent = pool().length;
    persist();
  };
}

/**
 * 档案里的道具 -> 界面三组百分比 + 明细文案。
 *
 * Bestdori 的 items 按槽位存，每槽是 `[道具ID, 等级, ...]`：
 *   PoppinParty / Afterglow / ...  -> 乐队道具
 *   Menu / Plaza                   -> 属性道具
 *   Magazine                       -> 杂志（全队）
 *   Everyone                       -> 全队通用
 *
 * ⚠️ 档案里存的是**道具等级**（1~7），不是加成百分比。等级→百分比要按游戏
 * 每个道具的实际曲线换算，而 Bestdori 没给这张表。这里只做**近似占位**
 * （每级 1.5%），界面上会标注，具体数值用户可按游戏内显示改。
 */
const ITEM_SLOTS_CN = {
  PoppinParty: "Poppin'Party", Afterglow: 'Afterglow', PastelPalettes: 'Pastel*Palettes',
  Roselia: 'Roselia', HelloHappyWorld: 'HHW', Morfonica: 'Morfonica',
  RaiseASuilen: 'RAS', MyGO: 'MyGO', Everyone: '全员',
  Magazine: '杂志', Plaza: '广场', Menu: '菜单',
};

function describeItems(items) {
  const parts = [];
  for (const [slot, m] of Object.entries(items || {})) {
    if (slot === 'potentials') continue;
    const levels = m instanceof Map ? [...m.values()] : Object.values(m || {});
    if (!levels.length) continue;
    const lv = Math.max(...levels.map(Number).filter((x) => !Number.isNaN(x)));
    if (!lv) continue;
    parts.push(`${ITEM_SLOTS_CN[slot] || slot} Lv${lv}`);
  }
  return parts;
}

/** 卡池来源标识：档案（完整）/ 清单（识图兜底） */
function setPoolSource(kind, note) {
  const badge = $('tmPoolBadge');
  const src = $('tmPoolSource');
  const hint = $('tmPoolHint');
  if (!badge) return;
  if (kind === 'profile') {
    badge.textContent = '完整档案';
    badge.style.background = '#2d5a3d';
    badge.style.color = '#9fe0b0';
    src.textContent = note || '导入的档案';
    if (hint) hint.textContent = '已覆盖清单，计算用这套卡池';
  } else {
    badge.textContent = '清单';
    badge.style.background = '#3a3f52';
    badge.style.color = '#c9cfe0';
    src.textContent = '清单里已识别的卡';
    if (hint) hint.textContent = '导入档案会覆盖清单，得到完整卡池';
  }
}

/** 导入后把「你到底导进来了什么」摊开给人看 */
function renderImported(list) {
  const box = $('tmImported');
  const body = $('tmImportedBody');
  if (!box || !body) return;
  if (!list || !list.length) { box.hidden = true; return; }

  const byAttr = {}, byBand = {}, byRarity = {};
  for (const c of list) {
    const ch = TEAM.characters?.[String(c.ch)] || {};
    byAttr[c.a] = (byAttr[c.a] || 0) + 1;
    const b = ch.b || '其他';
    byBand[b] = (byBand[b] || 0) + 1;
    byRarity[c.r] = (byRarity[c.r] || 0) + 1;
  }
  const chip = (k, v) => `<span style="display:inline-block;padding:2px 9px;margin:2px 5px 2px 0;
      border-radius:999px;background:var(--panel-2);border:1px solid var(--border-2);
      font-size:12px">${k} <b>${v}</b></span>`;
  const rows = (obj, fmt) => Object.entries(obj).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => chip(fmt(k), v)).join('');

  const potN = Object.keys(PROFILE?.potentials || {}).length;
  const itemN = Object.keys(PROFILE?.items || {})
    .filter((k) => k !== 'potentials').length;

  $('tmImportedMeta').textContent =
    `卡 ${list.length} 张 · 道具 ${itemN} 组 · 潜能 ${potN} 个角色`;

  body.innerHTML = `
    <div style="font-size:12px;color:var(--text-dim);margin-bottom:4px">属性</div>
    <div>${rows(byAttr, (k) => k)}</div>
    <div style="font-size:12px;color:var(--text-dim);margin:10px 0 4px">乐队</div>
    <div>${rows(byBand, (k) => k.replace('Hello, Happy World!', 'HHW').replace('Pastel*Palettes', 'PasPare').split(' ')[0])}</div>
    <div style="font-size:12px;color:var(--text-dim);margin:10px 0 4px">稀有度</div>
    <div>${rows(byRarity, (k) => k + '★')}</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:12px">
      ${list.slice(0, 24).map((c) => {
        const ch = TEAM.characters?.[String(c.ch)] || {};
        const nm = (ch.n || '?').replace(/\s/g, '');
        return `<img src="data/thumbs/${c.id}_n.webp" alt="${nm}" title="卡${c.id} ${nm}"
          loading="lazy" referrerpolicy="no-referrer"
          onerror="this.onerror=null;this.style.opacity=.2"
          style="width:52px;height:52px;border-radius:8px;object-fit:cover;
                 background:var(--panel-2);border:1px solid var(--border-2)">`;
      }).join('')}
      ${list.length > 24 ? `<span class="footnote" style="align-self:center">…共 ${list.length} 张</span>` : ''}
    </div>`;
  box.hidden = false;
}

function applyProfileItems(items) {
  const lvOf = (slot) => {
    const m = items?.[slot];
    if (!m) return 0;
    const vals = (m instanceof Map ? [...m.values()] : Object.values(m)).map(Number).filter((x) => !Number.isNaN(x));
    return vals.length ? Math.max(...vals) : 0;
  };
  // 取所有乐队槽的最大等级（用户通常只点一两个乐队）
  const bandLv = Math.max(0, ...Object.keys(items || {})
    .filter((k) => !['Menu', 'Plaza', 'Magazine', 'Everyone', 'potentials'].includes(k))
    .map(lvOf));
  const attrLv = Math.max(lvOf('Menu'), lvOf('Plaza'));
  const magLv = Math.max(lvOf('Magazine'), lvOf('Everyone'));

  const toPct = (lv) => Math.round(lv * 1.5 * 10) / 10;   // 近似：每级 1.5%
  if ($('tmBand')) $('tmBand').value = toPct(bandLv);
  if ($('tmAttr')) $('tmAttr').value = toPct(attrLv);
  if ($('tmMag')) $('tmMag').value = toPct(magLv);

  const parts = describeItems(items);
  if ($('tmItemInfo')) {
    $('tmItemInfo').textContent = parts.length
      ? `档案读到：${parts.join(' · ')}（等级→百分比是近似值，可按游戏内改）`
      : '档案里没有道具数据';
  }
}

export async function initTeamTab() {
  try {
    await ensureData();
  } catch (e) {
    $('tmBody').innerHTML = `<div class="empty">数据加载失败：${e.message}</div>`;
    return;
  }
  if (!$('tmBandSel').dataset.ready) {
    const bands = [...new Set(Object.values(TEAM.characters || {}).map((c) => c.b))].sort();
    $('tmBandSel').innerHTML = '<option value="">（不指定）</option>' +
      bands.map((b) => `<option value="${b}">${b}</option>`).join('');
    $('tmBandSel').dataset.ready = '1';
  }
  fillEvents();
  renderSongList();
  restore();
  bindPersistence();
  bindProfileImport();

  // 道具页存的结果（bdh.items.v1）：有的话回填明细和百分比
  try {
    const rawItems = localStorage.getItem('bdh.items.v1');
    if (rawItems) {
      const items = JSON.parse(rawItems);
      if (items && Object.keys(items).length) {
        applyProfileItems(Object.fromEntries(
          Object.entries(items).map(([k, v]) => [k, [0, v]])));
      }
    }
  } catch { /* 坏数据忽略 */ }
  if (PROFILE) {
    setPoolSource('profile', PROFILE.note);
    $('tmClearProfile').hidden = false;
  } else {
    setPoolSource('list');
  }
  if (!$('tmSong').dataset.ready) {
    $('tmDiff').onchange = () => {
      if ($('songDiff')) $('songDiff').value = $('tmDiff').value;
      renderSongList(); updatePicked();
    };
    // 弹窗内的乐队/难度筛选
    const bandSel = $('songBand');
    if (bandSel) {
      const ids = [...new Set(Object.values(SONGS).map((s) => String(s.b)))]
        .filter((b) => BAND_NAME[b])
        .sort((a, b) => Number(a) - Number(b));
      bandSel.innerHTML = '<option value="">全部乐队</option>'
        + ids.map((b) => `<option value="${b}">${BAND_NAME[b]}</option>`).join('');
      bandSel.onchange = () => renderSongList(true);
    }
    if ($('songDiff')) {
      $('songDiff').value = $('tmDiff').value;
      $('songDiff').onchange = () => {
        $('tmDiff').value = $('songDiff').value;   // 主表单跟着变，计算才用对难度
        renderSongList(true);
        updatePicked();
      };
    }
    $('tmPickSong').onclick = openSongPicker;
    $('songLbClose').onclick = closeSongPicker;
    $('songSearch').oninput = () => renderSongList(true);
    // 列表滚到底部附近时追加下一页
    $('songLb').querySelector('.lb-body').onscroll = (e) => {
      const el = e.currentTarget;
      if (el.scrollTop + el.clientHeight >= el.scrollHeight - 120
          && songPage * PAGE_SIZE < songFiltered.length) {
        appendPage();
      }
    };
    $('tmSong').dataset.ready = '1';
    updatePicked();
  }
  const n = pool().length;
  $('tmPoolCount').textContent = n;
  $('tmHint').textContent = n < 5 ? '（至少需要 5 张卡）' : '';
}

export async function runTeamCalc() {
  await ensureData();
  const p = pool();
  if (p.length < 5) {
    $('tmBody').innerHTML = '<div class="empty">清单里的卡不足 5 张，先在「结果」页识别截图。</div>';
    return;
  }
  const diff = $('tmDiff').value;
  const sid = pickedSongId;
  if (!sid) return;

  $('tmBody').innerHTML = '<div class="empty">计算中…</div>';
  try {
    // 谱面按需取：本地缓存 -> 仓库文件 -> 在线代理（见 chart-fetch.js）
    $('tmBody').innerHTML = '<div class="empty">正在获取谱面…</div>';
    const chart = await fetchChart(sid, diff);
    const parsed = parseChart(chart);
    const evId = $('tmEvent').value;
    const ev = evId ? EVENTS[evId] : null;
    const area = {
      band: Number($('tmBand').value) / 100,
      bandBand: $('tmBandSel').value || undefined,
      attr: Number($('tmAttr').value) / 100,
      attrAttr: $('tmAttrSel').value || undefined,
      magazine: Number($('tmMag').value) / 100,
    };
    const ctx = {
      skills: TEAM.skills,
      level: Number($('tmSkillLv').value),
      songLevel: SONGS[sid]?.lv?.[diff] ?? 26,
      charRank: Number($('tmRank').value),
      potentials: PROFILE?.potentials || null,   // 档案里的角色潜力，按角色加成
      area,
      event: ev,
    };

    // 组合枚举是同步的，交给下一帧再跑，别把「计算中」卡住
    await new Promise((r) => setTimeout(r, 0));
    let results = bestTeams(p, parsed, ctx, { topN: 30 });

    // 活动点数：逐卡算加成，取全队平均（与游戏结算的加成口径一致）
    if (ev) {
      for (const r of results) {
        const bonuses = r.team.map((c) => eventBonusFor(c, ev, 0).point);
        const avg = bonuses.reduce((a, b) => a + b, 0) / bonuses.length;
        r.bonusPct = avg;
        r.points = eventPoints(r.score, ev, { bonus: avg, boost: 5 });
      }
      // 换活动时，追点数比追分数更常见 —— 有活动就按点数排
      results.sort((a, b) => b.points - a.points);
    }
    results = results.slice(0, 10);
    render(results, {
      diff, level: ctx.songLevel, notes: parsed.notes.length,
      skillLv: ctx.level,
    });
  } catch (e) {
    $('tmBody').innerHTML = `<div class="empty">${e.message}</div>`;
  }
}

export function bindTeamTab() {
  const btn = $('tmRun');
  if (btn) btn.onclick = runTeamCalc;
  if ($('tmExport')) $('tmExport').onclick = exportSettings;
  if ($('tmImport')) $('tmImport').onclick = () => $('tmImportFile').click();
  if ($('tmImportFile')) {
    $('tmImportFile').onchange = (e) => {
      const f = e.target.files?.[0];
      if (f) importSettings(f);
      e.target.value = '';
    };
  }
}
