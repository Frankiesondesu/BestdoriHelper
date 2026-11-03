/**
 * 组队页 UI：选难度/歌曲 → 从「清单」里的卡池算最优 5 人队。
 *
 * 数据全部来自本地（不需要联网、不需要代理）：
 *   data/team.json             卡牌三围/技能/角色
 *   data/songs.json            曲名与难度
 *   data/charts/{diff}/{id}.json   预拉的谱面（note 时间表）
 */

import { parseChart, bestTeams, eventBonusFor, pickAreaItems } from './team.js';
import { parseProfile } from './profile.js';
import { recognizeImage, parseOcrLines } from './ocr.js';
import { fetchChart } from './chart-fetch.js';
import { log } from './log.js';

let TEAM = null;
let SONGS = null;
let EVENTS = null;
/** 导入的 Bestdori 档案（有值时优先用它当卡池） */
let PROFILE = null;
/** 最近一次计算结果与它对应的口径 —— 详情弹层点开时用 */
let lastResults = null;
let lastMeta = null;
/** 排序口径：'points' 追活动点数 / 'score' 追单局分数 */
let sortMode = 'points';

const ATTR_CN = { powerful: '强力', happy: '快乐', pure: '纯洁', cool: '酷炫' };
const ATTR_COLOR = { powerful: '#ff4d4d', happy: '#ffb84d', pure: '#4dd98a', cool: '#4d9bff' };

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
  // 窄屏的 select 只显示得下「进行中 ·」，活动全名放在它下面那行
  if ($('tmEventName')) $('tmEventName').textContent = e.n || '';
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
      // master 是**星光等级**（0~4），直接决定活动加成里的 0~15% —— 不能丢
      if (meta) out.push({ ...meta, id: c.id, trained: c.trained, master: c.master ?? 0 });
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

function teamCards(list, ev) {
  return list.map((c) => {
    const info = (window.__BDH_STATE?.meta?.cards || {})[String(c.id)] || {};
    const name = info.name || `#${c.id}`;
    // 每张卡自己的活动加成（属性+角色+成员+星光）。鼠标悬停能看出「这队为什么
    // 加成高」—— 尤其是哪张卡是本期成员卡、哪张只是凑属性的通用卡。
    const b = ev ? eventBonusFor(c, ev, c.master ?? 0) : null;
    // 用 `bonus`（单卡加成原始值），不要用 `point` —— 综合力型活动里 point 恒为 0
    const tip = b
      ? `${name}｜活动加成 +${b.bonus}%（属性${b.parts.attr} 角色${b.parts.char} 一致${b.parts.double} 星光${b.parts.star}）${b.member ? ' · 本期活动卡' : ''}`
      : name;
    return `<img src="data/thumbs/${c.id}_n.webp" alt="${name}" title="${tip}"
                 loading="lazy" onerror="this.onerror=null;this.src='data/thumbs/${c.id}_t.webp'">`;
  }).join('');
}

export function render(results, meta) {
  lastResults = results;
  lastMeta = meta;
  if (!results.length) {
    $('tmBody').innerHTML = '<div class="empty">没有可用的卡。先在「结果」页识别截图。</div>';
    return;
  }
  const ev = meta.event || null;
  const withBonus = ev && results.some((r) => r.bonusSum != null);

  // 取舍说明：按点数排时对照「分数最高」的队，按分数排时对照「点数最高」的队。
  const byScore = meta.sortMode === 'score';
  const cmp = meta.compare;
  let tradeoff = '';
  if (cmp && withBonus && cmp !== results[0]) {
    if (!byScore) {
      const d = Math.round((results[0].points ?? 0) - (cmp.points ?? 0));
      const pct = cmp.score ? (1 - results[0].score / cmp.score) * 100 : 0;
      const bd = (results[0].bonusSum ?? 0) - (cmp.bonusSum ?? 0);
      if (d > 0) {
        tradeoff = `<p class="footnote">
          <b>为什么不是综合力最高的那队？</b> 只看分数的话，
          <b>${Math.round(cmp.score).toLocaleString()}</b> 分（综合力
          ${Math.round(cmp.power).toLocaleString()}、加成 ${Math.round(cmp.bonusSum)}%）
          确实更高，但点数只有 <b>${Math.round(cmp.points).toLocaleString()}</b>，
          比推荐队少 <b>${d.toLocaleString()}</b>。推荐队分数低
          <b>${pct.toFixed(1)}%</b>，加成多 <b>${Math.round(bd)}</b> 个百分点 ——
          加成是直接乘在点数上的，所以按点数排时档期卡优先。
        </p>`;
      }
    } else {
      const d = Math.round((cmp.points ?? 0) - (results[0].points ?? 0));
      const pct = results[0].score ? (1 - cmp.score / results[0].score) * 100 : 0;
      const bd = (cmp.bonusSum ?? 0) - (results[0].bonusSum ?? 0);
      if (d > 0) {
        tradeoff = `<p class="footnote">
          <b>这队分数最高，但不是点数最高的。</b> 按点数排的推荐队是
          <b>${Math.round(cmp.points).toLocaleString()}</b> 点（加成
          ${Math.round(cmp.bonusSum)}%），比这队多 <b>${d.toLocaleString()}</b>；
          它的分数低 <b>${pct.toFixed(1)}%</b>、加成多 <b>${Math.round(bd)}</b> 个百分点。
          打歌榜看这张表，刷活动点数切回「按点数」。
        </p>`;
      }
    }
  }

  const rows = results.map((r, i) => `
    <tr class="tm-row" data-team="${i}" title="点开看这支队伍的卡面明细">
      <td class="mono">${i + 1}</td>
      <td><div class="tm-team">${teamCards(r.team, ev)}</div></td>
      <td class="mono">${r.power.toLocaleString()}</td>
      ${withBonus ? `<td class="mono"><b>${Math.round(r.bonusSum)}%</b>
        <div class="footnote">均 ${r.bonusPct.toFixed(1)}%</div></td>` : ''}
      <td class="mono">${r.score.toLocaleString()}</td>
      <td class="mono">${r.points == null ? '—' : `<b>${r.points.toLocaleString()}</b>`
        + (r.supportAdded ? `<div class="footnote">含支援 +${r.supportAdded.toLocaleString()}</div>` : '')}</td>
    </tr>`).join('');

  $('tmBody').innerHTML = `
    <div class="card">
      <div class="card-label">最优队伍（按${byScore ? '分数' : '点数'}） <span class="footnote">${meta.diff} · Lv${meta.level} ·
        ${meta.notes} NOTE · 技能等级 ${meta.skillLv} · 点任意一行看详情</span></div>
      <div style="overflow-x:auto">
        <table>
          <thead><tr><th>#</th><th>队伍（左起为队长）</th><th>综合力</th>
            ${withBonus ? '<th>活动加成</th>' : ''}
            <th>预计分数</th><th>活动点数</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="footnote">分数模型：综合力 × Lv加成 × 300% × 判定/COMBO/技能。
        这里是<b>全 PERFECT + 全程不断连</b>的理论值，实战一定更低 ——
        它用于横向比较队伍强弱，不是「这一局能打多少分」。</p>
      ${tradeoff}
      ${withBonus ? `<p class="footnote">活动加成 = 属性 + 角色 + 一致 + 当期卡 + 星光，
        逐卡相加；表格里是该队 5 张卡的<b>合计</b>，游戏结算界面显示的就是这个数，
        点数公式也是按这个合计乘的。<br>
        竞演 / 挑战 / FES / 组曲的加成作用在<b>综合力</b>上（进分数），任务 / 试炼 /
        传统的加成作用在<b>点数</b>上 —— 同一份加成只走一边，不重复计算。
        综合力高但没吃加成的队，点数往往不如档期卡队，这就是排序按点数而不是分数的原因。</p>` : ''}
    </div>`;

  // 点行看详情（手机上表格窄，详情放进弹层而不是继续加列）
  $('tmBody').querySelectorAll('[data-team]').forEach((tr) => {
    tr.onclick = () => openTeamDetail(Number(tr.dataset.team));
  });
}

/** 卡面显示信息：名字/角色/乐队/稀有度/属性 */
function cardMeta(id) {
  const meta = window.__BDH_STATE?.meta;
  const c = meta?.cards?.[String(id)] || {};
  const ch = meta?.characters?.[String(c.ch)] || {};
  const band = meta?.bands?.[String(ch.b)] || {};
  return {
    name: c.t || `卡 ${id}`,
    chName: ch.n || '',
    band: band.n || '',
    r: c.r || 0,
    a: c.a || '',
  };
}

/** 打开某支队伍的详情：合计数据 + 五张卡的卡名/编号/属性/技能/加成 */
function openTeamDetail(i) {
  const r = lastResults?.[i];
  const box = $('teamLb');
  if (!r || !box) return;
  const ev = lastMeta?.event || null;

  const cards = r.team.map((c, k) => {
    const m = cardMeta(c.id);
    const sk = TEAM.skills?.[String(c.sk)] || {};
    const b = ev ? eventBonusFor(c, ev, c.master ?? 0) : null;
    const ac = ATTR_COLOR[m.a] || '#888';
    const tags = [
      m.a ? `<span class="pill" style="color:${ac};border-color:${ac}66">${ATTR_CN[m.a] || m.a}</span>` : '',
      m.r ? `<span class="pill">${m.r}★</span>` : '',
      sk.up != null ? `<span class="pill">技能 +${sk.up}%</span>` : '',
      b && b.bonus ? `<span class="pill ok">加成 +${b.bonus}%</span>` : '',
      k === 0 ? '<span class="pill warn">队长</span>' : '',
    ].filter(Boolean).join('');

    return `<a class="tm-dc" href="https://bestdori.com/info/cards/${c.id}"
        target="_blank" rel="noopener"
        title="在 Bestdori 打开卡 ${c.id}">
      <img src="data/thumbs/${c.id}_n.webp" alt="" loading="lazy"
           onerror="this.onerror=null;this.src='data/thumbs/${c.id}_t.webp'">
      <span class="tm-dc-main">
        <span class="tm-dc-name">${m.name}</span>
        <span class="tm-dc-sub">${[m.chName, m.band, `卡 ${c.id}`].filter(Boolean).join(' · ')}</span>
        <span class="tm-dc-tags">${tags}</span>
      </span>
      <span class="tm-dc-go">↗</span>
    </a>`;
  }).join('');

  const stat = (label, value, sub = '') =>
    `<div class="tm-detail-stat"><span>${label}</span><b>${value}</b>` +
    (sub ? `<em>${sub}</em>` : '') + '</div>';

  $('teamLbTitle').textContent = `第 ${i + 1} 名队伍`;
  $('teamLbBody').innerHTML = `
    <div class="tm-detail-sum">
      ${stat('综合力', r.power.toLocaleString())}
      ${stat('预计分数', r.score.toLocaleString())}
      ${stat('活动点数', r.points == null ? '—' : r.points.toLocaleString(),
        r.supportAdded ? `含支援 +${r.supportAdded.toLocaleString()}` : '')}
      ${ev ? stat('活动加成', `${Math.round(r.bonusSum)}%`, `均 ${r.bonusPct.toFixed(1)}%`) : ''}
    </div>
    <div class="tm-detail-list">${cards}</div>
    <p class="footnote" style="margin:10px 0 0">
      分数是<b>全 PERFECT + 全程不断连</b>的理论值，用来横向比较队伍，不是这一局的实际得分。
      点任意一张卡可在 Bestdori 打开卡面页核对。
    </p>`;

  box.hidden = false;
  $('teamLbClose').onclick = () => { box.hidden = true; };
  box.querySelector('.lb-body').scrollTop = 0;
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
    sortMode,
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
  if (cfg.sortMode === 'score' || cfg.sortMode === 'points') {
    sortMode = cfg.sortMode;
    document.querySelectorAll('#tmSort button').forEach((b) => {
      b.classList.toggle('on', b.dataset.sort === sortMode);
    });
  }
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
/**
 * 吃进一段档案文本（文件导入和粘贴共用同一条路径）。
 * @returns {number} 卡池张数
 * @throws {Error} 解析失败时抛出可读的原因
 */
const PROFILE_STORE = 'bdh.profile.v1';

function importProfileText(text) {
  const raw = String(text || '').replace(/^\uFEFF/, '').trim();
  if (!raw) throw new Error('内容是空的 —— 先把档案粘进来');
  let json;
  try {
    json = JSON.parse(raw);
  } catch (e) {
    throw new Error('不是合法 JSON：' + e.message);
  }
  PROFILE = parseProfile(json, { maxCardId: 999999 });
  applyProfileItems(PROFILE.items);
  log(`导入档案：卡池 ${PROFILE.cards.length} 张 · ${PROFILE.note}`, 'ok');
  const pool0 = pool();
  setPoolSource('profile', PROFILE.note);
  $('tmPoolCount').textContent = pool0.length;
  $('tmClearProfile').hidden = false;
  renderImported(pool0);
  persist();
  // 原样存一份（1~2 KB）：刷新后不用再粘一次
  try { localStorage.setItem(PROFILE_STORE, raw); } catch { /* 配额 */ }
  return pool0.length;
}

/**
 * 「粘贴文本…」：直接把官网导出的 JSON 粘进来，不经过文件。
 * 面板是 index.html 里的 #profileLb（和选歌弹窗同一套样式），不用全屏浮层。
 */
function openProfilePaste() {
  const box = $('profileLb');
  if (!box) return;
  box.hidden = false;
  $('tmPasteErr').textContent = '';
  const ta = $('tmPasteText');
  ta.focus();
  // 光标落到末尾，粘贴后能立刻看到内容
  ta.setSelectionRange(ta.value.length, ta.value.length);

  const doImport = () => {
    $('tmPasteErr').textContent = '';
    try {
      const n = importProfileText(ta.value);
      box.hidden = true;
      $('tmHint').textContent = `已导入档案：卡池 ${n} 张`;
      requestAnimationFrame(() => {
        $('tmPoolBox').scrollIntoView({ block: 'center', behavior: 'smooth' });
      });
    } catch (e) {
      $('tmPasteErr').textContent = e.message;
    }
  };

  $('tmPasteDo').onclick = doImport;
  $('pasteClose').onclick = () => { box.hidden = true; };
  // 一边改一边清错误，别让上一次的红字一直挂着
  ta.oninput = () => { $('tmPasteErr').textContent = ''; };
  ta.onkeydown = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); doImport(); }
  };
  $('tmPasteFile').onclick = () => { box.hidden = true; $('tmProfileFile').click(); };
}

function bindProfileImport() {
  const btn = $('tmImportProfile');
  if (!btn || btn.dataset.bound) return;
  btn.dataset.bound = '1';
  btn.onclick = () => $('tmProfileFile').click();
  if ($('tmPasteProfile')) $('tmPasteProfile').onclick = openProfilePaste;
  $('tmProfileFile').onchange = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      const n = importProfileText(await f.text());
      $('tmHint').textContent = `已导入档案：卡池 ${n} 张`;
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
    try { localStorage.removeItem(PROFILE_STORE); } catch { /* 忽略 */ }
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

/**
 * 仓库里自带的道具等级（`data/my-items.json`，就是之前用截图识别出来的那份）。
 *
 * 浏览器 localStorage 里没有「道具页」的结果时，用这份当默认值 ——
 * 否则组队计算会当成「一件道具都没点」，综合力直接少 20%~30%，
 * 推荐出来的队伍和实际能打出的分对不上。
 */
let DEFAULT_ITEMS = null;
async function loadDefaultItems() {
  if (DEFAULT_ITEMS !== null) return DEFAULT_ITEMS;
  try {
    const j = await fetch('data/my-items.json').then((r) => r.json());
    const levels = {};
    for (const [k, v] of Object.entries(j.items || {})) {
      levels[k] = Array.isArray(v) ? Number(v[1]) || 0 : Number(v) || 0;
    }
    DEFAULT_ITEMS = { levels, detail: j.detail || null };
  } catch {
    DEFAULT_ITEMS = { levels: {}, detail: null };
  }
  return DEFAULT_ITEMS;
}

/** 排序切换：点了就重算（有结果时），并记住选择 */
function bindSort() {
  const box = $('tmSort');
  if (!box || box.dataset.bound) return;
  box.dataset.bound = '1';
  box.querySelectorAll('button').forEach((btn) => {
    btn.onclick = () => {
      const v = btn.dataset.sort;
      if (v === sortMode) return;
      sortMode = v;
      box.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === btn));
      persist();
      if (lastResults) runTeamCalc();
    };
  });
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
  bindSort();

  // 道具：优先用「道具」页存的结果，没有就用仓库自带的 my-items.json
  try {
    const rawItems = localStorage.getItem('bdh.items.v1');
    let items = null;
    if (rawItems) items = JSON.parse(rawItems);
    if (!items || !Object.keys(items).length) {
      const d = await loadDefaultItems();
      items = Object.fromEntries(Object.entries(d.levels).map(([k, v]) => [k, [0, v]]));
      if (Object.keys(items).length && $('tmItemInfo')) {
        $('tmItemInfo').textContent =
          `已载入上次识别的道具（${Object.keys(items).length} 个槽位）· 可在「道具」页重新识别`;
      }
    }
    if (items && Object.keys(items).length) applyProfileItems(items);
  } catch { /* 坏数据忽略 */ }
  // 上次粘贴/导入的档案：刷新后自动恢复，不用再粘一次
  if (!PROFILE) {
    try {
      const saved = localStorage.getItem(PROFILE_STORE);
      if (saved) {
        PROFILE = parseProfile(JSON.parse(saved), { maxCardId: 999999 });
        // 这里**不能**再 applyProfileItems：那会用档案里的等级覆盖用户手改过的
        // 三组百分比（restore() 刚从设置里恢复的正是用户最后改的值）。
      }
    } catch { /* 坏数据就当没有 */ }
  }
  if (PROFILE) {
    setPoolSource('profile', PROFILE.note);
    $('tmClearProfile').hidden = false;
    renderImported(pool());
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
  const t0 = performance.now();
  try {
    // 谱面按需取：本地缓存 -> 仓库文件 -> 在线代理（见 chart-fetch.js）
    $('tmBody').innerHTML = '<div class="empty">正在获取谱面…</div>';
    const chart = await fetchChart(sid, diff);
    const parsed = parseChart(chart);
    const evId = $('tmEvent').value;
    const ev = evId ? EVENTS[evId] : null;
    log(`组队计算：${SONGS[sid]?.t || sid} · ${diff} · 卡池 ${p.length} 张 · `
      + `活动 ${ev ? ev.n : '不计'} · 按${sortMode === 'score' ? '分数' : '点数'}`);
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

    // 分片枚举：边算边刷进度，避免页面看起来卡死
    $('tmBody').innerHTML = `
      <div class="card">
        <div class="card-label">正在计算…</div>
        <div style="height:6px;border-radius:3px;background:var(--panel-2);
                    overflow:hidden;margin:10px 0">
          <div id="tmProg" style="height:100%;width:0%;background:var(--accent);
               transition:width .12s linear"></div>
        </div>
        <div class="footnote" id="tmProgTxt">候选 ${p.length} 张，正在枚举组合…</div>
      </div>`;
    // 道具等级，按可信度取第一个有值的：
    //   ① 「道具」页 OCR / 导入的结果（用户最近确认过的）
    //   ② 导入的 Bestdori 档案里带的 items（等级是官方数据）
    //   ③ 仓库自带的 my-items.json（之前识别出来的那份）
    let itemLevels = null;
    let itemDetail = null;
    try {
      const raw = localStorage.getItem('bdh.items.v1');
      if (raw) {
        const it = JSON.parse(raw);
        if (it && Object.keys(it).length) itemLevels = it;
      }
      if (!itemLevels && PROFILE?.items) {
        const lv = {};
        for (const [slot, m] of Object.entries(PROFILE.items)) {
          if (slot === 'potentials' || !m) continue;
          const vals = (m instanceof Map ? [...m.values()] : Object.values(m))
            .map(Number).filter((x) => !Number.isNaN(x));
          if (vals.length) lv[slot] = Math.max(...vals);
        }
        if (Object.keys(lv).length) itemLevels = lv;
      }
      if (!itemLevels) {
        const d = await loadDefaultItems();
        if (Object.keys(d.levels).length) { itemLevels = d.levels; itemDetail = d.detail; }
      }
      // 道具明细（中文名）——用来判断属性道具管哪个属性、杂志加哪一围
      const dRaw = localStorage.getItem('bdh.itemsDetail.v1');
      if (dRaw) itemDetail = JSON.parse(dRaw);
    } catch { /* 忽略 */ }

    if (itemLevels) {
      const n = Object.keys(itemLevels).length;
      const head = Object.entries(itemLevels).slice(0, 4)
        .map(([k, v]) => `${k} Lv${v}`).join(' · ');
      log(`道具：${n} 个槽位参与计算（${head}${n > 4 ? ' …' : ''}）`);
    } else {
      log('道具：没有数据，按 0 加成算 —— 去「道具」页识别或导入', 'warn');
    }

    let loggedStep = -1;   // 日志里每 25% 记一行，避免刷屏
    let results = await bestTeams(p, parsed, ctx, {
      topN: 30,
      sortBy: sortMode,                // ← 按点数 / 按分数
      itemLevels,                      // ← 有实际等级就逐队自动选道具
      itemDetail,                      // ← 道具中文名（判断属性/杂志作用围）
      characters: TEAM.characters,
      onProgress: (f) => {
        const bar = $('tmProg');
        if (bar) bar.style.width = `${Math.round(f * 100)}%`;
        const t = $('tmProgTxt');
        if (t) t.textContent =
          `枚举组合 ${Math.round(f * 100)}%（卡池 ${p.length} 张，三围前列 + 全部档期卡参与枚举）`;
        const step = Math.floor(f * 4);
        if (step > loggedStep) {
          loggedStep = step;
          log(`枚举进度 ${Math.min(100, step * 25)}%（候选 ${p.length} 张）`);
        }
      },
    });

    // 活动点数已经在 bestTeams 里算好；这里按当前口径再排一次并截断到 10 队
    results.sort((a, b) => (sortMode === 'score'
      ? b.score - a.score
      : (b.points ?? 0) - (a.points ?? 0)));
    results = results.slice(0, 10);

    // 再算一次「另一种口径」的最优队，用来说明取舍。
    // 它常常不在当前排序的 Top30 里（比如加成 0 的队点数很低），所以必须单独算。
    let compare = null;
    try {
      const other = await bestTeams(p, parsed, ctx, {
        topN: 1, itemLevels, itemDetail, characters: TEAM.characters,
        sortBy: sortMode === 'points' ? 'score' : 'points',
      });
      compare = other[0] || null;
    } catch { /* 对照算不出来不影响主结果 */ }

    const best = results[0];
    if (best) {
      log(`完成：综合力 ${Math.round(best.power).toLocaleString()} · `
        + `分数 ${Math.round(best.score).toLocaleString()} · `
        + `加成 ${Math.round(best.bonusSum ?? 0)}% · `
        + `点数 ${Math.round(best.points ?? 0).toLocaleString()} · `
        + `${((performance.now() - t0) / 1000).toFixed(1)}s`, 'ok');
      if (compare && compare !== best) {
        log(`对照（按${sortMode === 'points' ? '分数' : '点数'}）：`
          + `综合力 ${Math.round(compare.power).toLocaleString()} · `
          + `分数 ${Math.round(compare.score).toLocaleString()} · `
          + `点数 ${Math.round(compare.points ?? 0).toLocaleString()}`);
      }
    }

    render(results, {
      diff, level: ctx.songLevel, notes: parsed.notes.length,
      skillLv: ctx.level, event: ev, compare, sortMode,
    });
    // 结果块在设置区下面，手机上整块落在首屏之外 —— 算完直接把用户带过去，
    // 否则点完按钮只看到页面没动，很容易以为没生效。
    requestAnimationFrame(() => {
      $('tmBody').scrollIntoView({ block: 'start', behavior: 'smooth' });
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
