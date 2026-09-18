/**
 * BestdoriHelper 网页版 —— 主逻辑。
 *
 * 全部在浏览器里跑，图片不上传：
 *
 *   截图 ──► 切分网格（detect.js）
 *        ──► 每格提特征（features.js）
 *        ──► 与指纹库比对（index.js）
 *        ──► 可选：ORB 几何校验（verify.js）
 *        ──► 清单 → 导出
 *
 * 指纹库（fingerprints.bin）和缩略图（thumbs/*.webp）都是构建时生成的静态资源。
 */

import { computeFeatures, cropRgba } from './src/features.js';
import { FingerprintIndex } from './src/index.js';
import { detectBoxes, refineBox } from './src/detect.js';

// ---- 全局状态 ---------------------------------------------------------

const S = {
  index: null,          // FingerprintIndex
  meta: null,           // cards.json
  files: [],            // 待识别的文件
  shots: [],            // 识别结果：[{name, w, h, url, cells: []}]
  activeShot: 0,
  inv: new Map(),       // cardKey -> {cardId, trained, score, gap, status, source, shot, cell}
  filter: { q: '', mode: 'all' },  // 清单的搜索与筛选状态
  tab: 'import',        // 当前分页
  selected: null,       // 当前选中的格 {shot, cell}
  busy: false,
  orb: null,            // 几何校验器（懒加载）
};

const $ = (id) => document.getElementById(id);

// ---- 工具 -------------------------------------------------------------

function log(msg, cls = '') {
  const el = $('log');
  const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  const line = document.createElement('div');
  line.innerHTML = `<span class="t">${t}</span> ${cls ? `<span class="${cls}">` : ''}${escapeHtml(msg)}${cls ? '</span>' : ''}`;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function setStatus(text, kind = '') {
  $('statusText').textContent = text;
  $('dot').className = 'dot ' + kind;
}

function setProgress(p) {
  const bar = $('progress');
  if (p === null) { bar.classList.remove('on'); return; }
  bar.classList.add('on');
  bar.firstElementChild.style.width = `${Math.round(p * 100)}%`;
}

const cardKey = (id, tr) => `${id}_${tr ? 't' : 'n'}`;
const thumbUrl = (id, tr) => `data/thumbs/${id}_${tr ? 't' : 'n'}.webp`;

/** 取卡牌显示信息 */
function cardInfo(id) {
  const c = S.meta?.cards?.[String(id)];
  if (!c) return { name: '（未知卡牌）', character: '', rarity: 0, attribute: '', color: '' };
  const ch = S.meta.characters?.[String(c.ch)] ?? {};
  const band = S.meta.bands?.[String(ch.b)] ?? {};
  return {
    name: c.t || `卡 ${id}`,
    character: ch.n || '',
    band: band.n || '',
    rarity: c.r || 0,
    attribute: c.a || '',
    type: c.ty || '',
    color: ch.c || '#888',
    resourceSet: c.rs || '',
  };
}

const ATTR_CN = { powerful: '强力', happy: '快乐', pure: '纯洁', cool: '酷炫' };
const ATTR_COLOR = { powerful: '#ff4d4d', happy: '#ffb84d', pure: '#4dd98a', cool: '#4d9bff' };

function rarityLabel(r) { return r ? `${r}★` : '?★'; }

function scoreClass(v) { return v >= 0.85 ? 'ok' : v >= 0.7 ? 'warn' : 'err'; }

// ---- 图片解码 ---------------------------------------------------------

/** 文件 → RGBA + 尺寸。用 createImageBitmap，不经过 DOM。 */
async function fileToRgba(file) {
  const bmp = await createImageBitmap(file);
  // 尺寸必须在 close() 之前取 —— ImageBitmap 关闭后再读 width/height 会得到 0
  const w = bmp.width;
  const h = bmp.height;
  const canvas = new OffscreenCanvas(w, h);
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(bmp, 0, 0);
  const img = ctx.getImageData(0, 0, w, h);
  bmp.close?.();
  return { rgba: img.data, w, h };
}

/** RGBA → RGB（detect.js 只需要三通道，省内存） */
function rgbaToRgb(rgba, n) {
  const out = new Uint8ClampedArray(n * 3);
  for (let i = 0; i < n; i++) {
    out[i * 3] = rgba[i * 4];
    out[i * 3 + 1] = rgba[i * 4 + 1];
    out[i * 3 + 2] = rgba[i * 4 + 2];
  }
  return out;
}

/**
 * 把一格的 RGBA 转成小缩略图 dataURL —— 用于结果表里跟 Bestdori 卡图并排核对。
 *
 * 直接存 dataURL 而不是 blob URL：格子数量多（140 格起步），blob URL 要手动
 * revoke，漏了就泄漏内存；dataURL 跟着对象一起被回收，省心。
 * 84×84 的 JPEG 约 3 KB，140 格也就 400 KB 量级。
 */
function makeCellThumb(rgba, w, h, size = 84) {
  try {
    const src = document.createElement('canvas');
    src.width = w; src.height = h;
    src.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(rgba), w, h), 0, 0);
    const dst = document.createElement('canvas');
    dst.width = size; dst.height = size;
    const ctx = dst.getContext('2d');
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(src, 0, 0, size, size);
    return dst.toDataURL('image/jpeg', 0.72);
  } catch (e) {
    console.warn('生成格子缩略图失败', e);
    return '';
  }
}

/** 置信度分档：与桌面版的 matched / ambiguous 对齐 */
function cellStatus(c) {
  if (c.cardId == null) return 'unknown';
  return (c.score >= 0.85 && c.gap >= 0.02) ? 'matched' : 'ambiguous';
}

// ---- 数据加载 ---------------------------------------------------------

async function loadData() {
  setStatus('加载卡牌数据…', 'busy');
  const metaRes = await fetch('data/cards.json');
  if (!metaRes.ok) throw new Error(`cards.json 加载失败：${metaRes.status}`);
  S.meta = await metaRes.json();
  log(`卡牌数据：${Object.keys(S.meta.cards).length} 张卡 / ` +
      `${Object.keys(S.meta.characters).length} 角色 / ${Object.keys(S.meta.bands).length} 乐队`);

  setStatus('加载指纹库…', 'busy');
  const fpRes = await fetch('data/fingerprints.bin');
  if (!fpRes.ok) throw new Error(`fingerprints.bin 加载失败：${fpRes.status}`);
  const buf = await fpRes.arrayBuffer();
  S.index = FingerprintIndex.fromArrayBuffer(buf);
  log(`指纹库：${S.index.length} 条，${(buf.byteLength / 1e6).toFixed(2)} MB`, 'ok');
}

// ---- 识别 -------------------------------------------------------------

/**
 * 识别一张图。
 * @returns {Promise<Array>} 每格 {box, cardId, trained, score, gap, top3}
 */
async function recognizeOne(rgba, w, h, opts) {
  const rgb = rgbaToRgb(rgba, w * h);

  let boxes;
  if (opts.mode === 'single') {
    boxes = [{ x: 0, y: 0, w, h }];
  } else {
    boxes = detectBoxes(rgb, w, h, {
      rows: opts.mode === 'grid' ? opts.rows : null,
      cols: opts.mode === 'grid' ? opts.cols : null,
      preferGaps: opts.refine,
    });
    if (opts.refine && opts.mode !== 'single') {
      boxes = boxes.map((b) => refineBox(rgb, w, h, b));
    }
  }

  const out = [];
  for (let i = 0; i < boxes.length; i++) {
    const b = boxes[i];
    const x0 = Math.max(0, b.x), y0 = Math.max(0, b.y);
    const x1 = Math.min(w, b.x + b.w), y1 = Math.min(h, b.y + b.h);
    if (x1 - x0 < 24 || y1 - y0 < 24) continue;

    const cell = cropRgba(rgba, w, h, [x0 / w, y0 / h, x1 / w, y1 / h]);
    const feats = computeFeatures(cell.data, cell.width, cell.height);
    const cellThumb = makeCellThumb(cell.data, cell.width, cell.height);
    let cands = S.index.search(feats, { topK: 6 });

    // 可选几何校验
    if (opts.useOrb && S.orb) {
      cands = await S.orb.rerank(cell, cands, S.meta);
    }

    const top = cands[0];
    const second = cands[1];
    if (!top) {
      out.push({ box: b, cellThumb, cardId: null, score: 0, gap: 0, top3: [] });
      continue;
    }
    out.push({
      box: b,
      cellThumb,
      cardId: top.cardId,
      trained: top.trained,
      score: top.score,
      gap: top.score - (second?.score ?? 0),
      inliers: top.inliers ?? 0,
      top3: cands.slice(0, 3),
    });
  }
  return out;
}

async function runRecognition() {
  if (S.busy || !S.index || !S.files.length) return;
  S.busy = true;
  $('btnRun').disabled = true;
  $('btnClear').disabled = true;
  setStatus('识别中…', 'busy');

  const opts = {
    mode: $('mode').value,
    rows: Number($('rows').value) || 4,
    cols: Number($('cols').value) || 7,
    refine: $('refine').checked,
    // 几何校验（ORB + RANSAC）暂未启用 —— OpenCV.js 官方构建不含特征检测/
    // 匹配（detectAndCompute / DescriptorMatcher / RANSAC 全都不在），要做得
    // 自己实现一套 ORB。而实测当前切分质量下，粗筛 top-1 已与几何校验等效
    // （140 格中 SIFT 改变了 0 格），所以先不做。见 docs/网页版说明.md。
    useOrb: false,
  };

  if (opts.useOrb && !S.orb) {
    try {
      log('正在加载 OpenCV.js（约 9MB，仅首次）…');
      setStatus('加载 OpenCV.js…', 'busy');
      const { OrbVerifier } = await import('./src/verify.js');
      S.orb = await OrbVerifier.create();
      log('OpenCV.js 就绪，几何校验已启用', 'ok');
    } catch (e) {
      log(`OpenCV.js 加载失败，退回纯指纹识别：${e.message}`, 'err');
      S.orb = null;
    }
  }

  S.shots = [];
  const t0 = performance.now();
  let totalCells = 0;

  for (let i = 0; i < S.files.length; i++) {
    const file = S.files[i];
    setProgress((i + 0.1) / S.files.length);
    log(`[${i + 1}/${S.files.length}] ${file.name}`);
    try {
      const { rgba, w, h } = await fileToRgba(file);
      const cells = await recognizeOne(rgba, w, h, opts);
      totalCells += cells.length;
      S.shots.push({ name: file.name, w, h, url: URL.createObjectURL(file), cells });
      log(`  切出 ${cells.length} 格`, 'ok');
    } catch (e) {
      log(`  失败：${e.message}`, 'err');
      console.error(e);
    }
    setProgress((i + 1) / S.files.length);
  }

  const ms = performance.now() - t0;
  setProgress(null);
  S.busy = false;
  $('btnRun').disabled = false;
  $('btnClear').disabled = false;
  S.activeShot = 0;
  S.selected = null;

  renderAll();
  // 识别完直接跳到结果页 —— 用户下一步必然是看结果，不该让他自己找
  if (S.shots.length) showTab('result');
  setStatus('就绪', 'ready');
  log(`完成：${S.files.length} 张截图 / ${totalCells} 格，用时 ${(ms / 1000).toFixed(1)}s` +
      `（${(ms / Math.max(1, totalCells)).toFixed(0)} ms/格）`, 'ok');
}

// ---- 清单 -------------------------------------------------------------

function addToInventory(cells, shotName, shotIndex) {
  let added = 0;
  cells.forEach((c, ci) => {
    if (c.cardId == null) return;
    const k = cardKey(c.cardId, c.trained);
    const prev = S.inv.get(k);
    // 同一张卡可能被多格认出（重复卡面），保留分高的那次
    if (!prev || c.score > prev.score) {
      S.inv.set(k, {
        cardId: c.cardId,
        trained: c.trained,
        score: c.score,
        gap: c.gap,
        status: cellStatus(c),
        source: shotName,
        shot: shotIndex,
        cell: ci,
      });
      added++;
    }
  });
  return added;
}

function buildInventoryFromShots() {
  S.inv.clear();
  S.shots.forEach((s, si) => addToInventory(s.cells, s.name, si));
  renderInventory();
}

/** 清单按当前筛选条件过滤后的列表 */
function filteredInventory() {
  const f = S.filter;
  let list = [...S.inv.values()];
  if (f.mode === 'confident') list = list.filter((x) => x.status === 'matched');
  else if (f.mode === 'pending') list = list.filter((x) => x.status === 'ambiguous');
  else if (f.mode === 'trained') list = list.filter((x) => x.trained);
  else if (f.mode === 'r45') list = list.filter((x) => (cardInfo(x.cardId).rarity || 0) >= 4);
  const q = f.q.trim().toLowerCase();
  if (q) {
    list = list.filter((x) => {
      const i = cardInfo(x.cardId);
      return (i.name + i.character + i.band + x.cardId).toLowerCase().includes(q);
    });
  }
  return list.sort((a, b) => a.cardId - b.cardId);
}

// ---- 渲染 -------------------------------------------------------------

function renderAll() {
  renderPreview();
  renderResults();
  buildInventoryFromShots();
  updateBadges();
  $('fileSummary').textContent = S.files.length
    ? `已选 ${S.files.length} 张：${S.files.map((f) => f.name).join('、').slice(0, 120)}`
    : '还没有选择截图';
}

function renderPreview() {
  const body = $('previewBody');
  // 换了截图就复位缩放；每次渲染都要重新 apply（#preview 会被重建，
  // transform 不保留）
  if (ZV.shot !== S.activeShot) { ZV.shot = S.activeShot; ZV.scale = 1; ZV.x = 0; ZV.y = 0; }
  const shot = S.shots[S.activeShot];
  if (!shot) {
    body.innerHTML = '<div class="empty">识别后在这里显示原图与切分框<br>点击框可选中对应行</div>';
    $('shotLabel').textContent = '未选择';
    $('zoomBadge').hidden = true;
    return;
  }
  $('shotLabel').textContent = S.shots.length > 1
    ? `${S.activeShot + 1}/${S.shots.length} · ${shot.name.slice(0, 28)}`
    : shot.name.slice(0, 32);

  // 多张截图时给个切换器
  const switcher = S.shots.length > 1
    ? `<div class="toolbar-row" style="margin-bottom:8px">` +
      S.shots.map((s, i) =>
        `<button class="small${i === S.activeShot ? ' primary' : ''}" data-shot="${i}">${i + 1}</button>`
      ).join('') + `</div>`
    : '';

  const boxes = shot.cells.map((c, i) => {
    const b = c.box;
    const cls = c.cardId == null ? 'unknown' : (c.score >= 0.85 && c.gap >= 0.02 ? '' : 'ambiguous');
    const sel = S.selected && S.selected.shot === S.activeShot && S.selected.cell === i ? ' sel' : '';
    const L = (b.x / shot.w) * 100, T = (b.y / shot.h) * 100;
    const W = (b.w / shot.w) * 100, H = (b.h / shot.h) * 100;
    return `<div class="cellbox ${cls}${sel}" data-cell="${i}" title="格 ${i}" ` +
      `style="left:${L}%;top:${T}%;width:${W}%;height:${H}%"></div>`;
  }).join('');

  body.innerHTML = switcher +
    `<div id="preview"><img src="${shot.url}" alt=""><div id="overlay">${boxes}</div></div>`;
  applyZoomView();   // #preview 刚被重建，缩放状态要重新贴上去

  body.querySelectorAll('[data-shot]').forEach((el) => {
    el.onclick = () => { S.activeShot = Number(el.dataset.shot); S.selected = null; renderAll(); };
  });
  body.querySelectorAll('[data-cell]').forEach((el) => {
    el.onclick = (ev) => {
      ev.stopPropagation();
      S.selected = { shot: S.activeShot, cell: Number(el.dataset.cell) };
      renderPreview(); renderResults();
      const row = document.querySelector(`tr.row[data-shot="${S.selected.shot}"][data-cell="${S.selected.cell}"]`);
      row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    };
  });
}

function renderResults() {
  const body = $('resultBody');
  const all = [];
  S.shots.forEach((s, si) => s.cells.forEach((c, ci) => all.push({ ...c, si, ci, shot: s })));

  if (!all.length) {
    body.innerHTML = '<div class="empty">还没有结果</div>';
    $('resultCount').textContent = '';
    return;
  }
  const matched = all.filter((r) => r.cardId != null && r.score >= 0.85 && r.gap >= 0.02).length;
  $('resultCount').textContent = `${all.length} 格 · 高置信 ${matched}`;

  const rows = all.map((r) => {
    const sel = S.selected && S.selected.shot === r.si && S.selected.cell === r.ci ? ' sel' : '';
    const info = r.cardId != null ? cardInfo(r.cardId) : null;
    const attrC = info ? (ATTR_COLOR[info.attribute] || '#888') : '#666';
    const thumb = r.cardId != null
      ? `<img src="${thumbUrl(r.cardId, r.trained)}" loading="lazy" alt="">`
      : '<div style="width:42px;height:42px;border-radius:5px;background:#26262f"></div>';
    // 对照视图：左=截图里那一格，右=匹配到的 Bestdori 卡图。
    // 并排放才能一眼看出「是不是同一张画」—— 这是核对的前提。
    const pair =
      `<div class="pair" data-cell="${r.si}:${r.ci}" title="点击查看卡面详情">` +
      (r.cellThumb
        ? `<img class="cellimg" src="${r.cellThumb}" alt="截图格">`
        : `<div class="noimg"></div>`) +
      (r.cardId != null
        ? `<img class="matchimg" src="${thumbUrl(r.cardId, r.trained)}" loading="lazy" alt="">`
        : `<div class="noimg"></div>`) +
      `</div>`;
    const name = info
      ? `<div class="name">${escapeHtml(info.name)}</div>` +
        `<div class="ch">${escapeHtml(info.character)}` +
        (info.band ? ` · ${escapeHtml(info.band)}` : '') + `</div>`
      : '<div class="name" style="color:var(--text-faint)">未识别</div>';
    const tags = info
      ? `<span class="pill" style="color:${attrC};border-color:${attrC}66">${ATTR_CN[info.attribute] || info.attribute}</span> ` +
        `<span class="pill">${rarityLabel(info.rarity)}</span>` +
        (r.trained ? ' <span class="pill ok">特训后</span>' : '')
      : '';
    const cands = (r.top3 || []).slice(0, 3).map((c, k) => {
      const ci2 = cardInfo(c.cardId);
      const cur = (c.cardId === r.cardId && c.trained === r.trained) ? ' cur' : '';
      return `<div class="cand${cur}" data-pick="${r.si}:${r.ci}:${c.cardId}:${c.trained ? 1 : 0}" ` +
        `title="${escapeHtml(ci2.name)}">` +
        `<img src="${thumbUrl(c.cardId, c.trained)}" loading="lazy" alt="">` +
        `<span>${escapeHtml(ci2.character || ci2.name).slice(0, 8)}</span>` +
        `<span class="mono" style="color:var(--text-faint)">${c.score.toFixed(2)}</span></div>`;
    }).join('');

    return `<tr class="row${sel}" data-shot="${r.si}" data-cell="${r.ci}">
      <td class="thumb">${pair}</td>
      <td>${name}${tags ? `<div style="margin-top:3px">${tags}</div>` : ''}</td>
      <td class="mono"><span class="score ${scoreClass(r.score)}">${r.score.toFixed(3)}</span>
        <div style="font-size:11px;color:var(--text-faint)">+${r.gap.toFixed(3)}${r.inliers ? ` · ${r.inliers}内点` : ''}</div></td>
      <td style="width:1%"><span class="pill">格${r.ci}</span></td>
    </tr>
    ${r.top3 && r.top3.length > 1 ? `<tr><td colspan="4" style="padding-top:0;border-bottom:1px solid rgba(46,46,58,.5)">
      <div class="candidates">${cands}</div></td></tr>` : ''}`;
  }).join('');

  body.innerHTML = `<table><thead><tr>
    <th title="左 = 截图里那一格，右 = 匹配到的 Bestdori 卡图">对照</th>
    <th>卡牌</th><th>置信度</th><th>位置</th>
  </tr></thead><tbody>${rows}</tbody></table>`;

  body.querySelectorAll('tr.row').forEach((tr) => {
    tr.onclick = () => {
      const si = Number(tr.dataset.shot), ci = Number(tr.dataset.cell);
      S.selected = { shot: si, cell: ci };
      if (si !== S.activeShot) S.activeShot = si;
      renderPreview(); renderResults();
    };
  });
  // 点缩略图 -> **直接放大那一张**（不再先开详情页）；
  // 放大状态下再点图片 -> 跳到对比视图。左图=截图格，右图=Bestdori 卡图。
  body.querySelectorAll('.pair[data-cell]').forEach((el) => {
    const [si, ci] = el.dataset.cell.split(':').map(Number);
    [['.cellimg', 'cell'], ['.matchimg', 'match']].forEach(([sel2, which]) => {
      const img = el.querySelector(sel2);
      if (!img) return;
      img.style.cursor = 'zoom-in';
      img.onclick = (ev) => {
        ev.stopPropagation();
        S.selected = { shot: si, cell: ci };
        if (si !== S.activeShot) S.activeShot = si;
        renderPreview();
        renderResults();
        zoomCellImage(si, ci, which);
      };
    });
  });
  body.querySelectorAll('[data-pick]').forEach((el) => {
    el.onclick = (ev) => {
      ev.stopPropagation();
      const [si, ci, cid, tr2] = el.dataset.pick.split(':').map(Number);
      const cell = S.shots[si].cells[ci];
      cell.cardId = cid;
      cell.trained = tr2 === 1;
      cell.score = Math.max(cell.score, 0.99);
      cell.gap = 1;
      buildInventoryFromShots();
      renderPreview(); renderResults();
      log(`格 ${ci} 手动改为卡 ${cid}${tr2 ? '（特训后）' : ''}`, 'ok');
    };
  });
}

function renderInventory() {
  const all = [...S.inv.values()];
  const list = filteredInventory();
  updateBadges();   // 清单变了，导航角标跟着变

  // 统计始终按**全量**算，不受筛选影响 —— 否则一筛选数字就变，没法看总量
  const box = $('summary');
  const nTrained = all.filter((x) => x.trained).length;
  const n45 = all.filter((x) => (cardInfo(x.cardId).rarity || 0) >= 4).length;
  const nPending = all.filter((x) => x.status === 'ambiguous').length;
  box.innerHTML = [
    ['总数', all.length], ['特训后', nTrained], ['4★5★', n45],
    ['待确认', nPending],
    ['角色数', new Set(all.map((x) => cardInfo(x.cardId).character)).size],
  ].map(([k, v]) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');

  const has = all.length > 0;
  for (const id of ['btnCsv', 'btnJson', 'btnIds', 'btnMarkdown']) $(id).disabled = !has;
  $('btnConfirmAll').disabled = nPending === 0;
  $('btnRemovePending').disabled = nPending === 0;
  $('btnClearInv').disabled = S.inv.size === 0;
  $('invHint').textContent = has ? '点行可跳到结果表对应格' : '';

  const shown = list.length;
  $('invCount').textContent = has
    ? (shown === all.length ? `${all.length} 张` : `筛出 ${shown} / ${all.length} 张`)
    : '';

  const body = $('invBody');
  if (!has) {
    body.innerHTML = '<div class="empty">识别出卡牌后，可在这里确认并导出</div>';
    return;
  }
  if (!shown) {
    body.innerHTML = '<div class="empty">没有符合筛选条件的卡牌</div>';
    return;
  }

  body.innerHTML = `<table><thead><tr>
    <th></th><th>卡牌</th><th>属性</th><th>置信度</th><th class="src">来源</th><th></th>
  </tr></thead><tbody>` +
    list.map((x) => {
      const i = cardInfo(x.cardId);
      const ac = ATTR_COLOR[i.attribute] || '#888';
      const st = x.status === 'matched'
        ? '<span class="pill ok">已确认</span>'
        : '<span class="pill warn">待确认</span>';
      return `<tr class="invrow" data-jump="${x.shot}:${x.cell}">
        <td class="thumb"><img src="${thumbUrl(x.cardId, x.trained)}" loading="lazy" alt=""></td>
        <td><div class="name">${escapeHtml(i.name)}</div>
            <div class="ch">${escapeHtml(i.character)} · 卡 ${x.cardId}</div></td>
        <td><span class="pill" style="color:${ac};border-color:${ac}66">${ATTR_CN[i.attribute] || i.attribute}</span>
            <span class="pill">${rarityLabel(i.rarity)}</span>${x.trained ? ' <span class="pill ok">特训后</span>' : ''}</td>
        <td class="mono"><span class="score ${scoreClass(x.score)}">${x.score.toFixed(3)}</span> ${st}</td>
        <td class="footnote src">${escapeHtml(x.source.slice(0, 20))}</td>
        <td><button class="small" data-del="${cardKey(x.cardId, x.trained)}">移除</button></td>
      </tr>`;
    }).join('') + '</tbody></table>';

  body.querySelectorAll('[data-del]').forEach((el) => {
    el.onclick = (ev) => {
      ev.stopPropagation();
      S.inv.delete(el.dataset.del);
      renderInventory();
    };
  });
  body.querySelectorAll('tr.invrow').forEach((tr) => {
    tr.onclick = () => {
      const [si, ci] = tr.dataset.jump.split(':').map(Number);
      if (Number.isNaN(si) || Number.isNaN(ci)) return;
      S.selected = { shot: si, cell: ci };
      if (si !== S.activeShot) S.activeShot = si;
      renderPreview(); renderResults();
      document.querySelector(`tr.row[data-shot="${si}"][data-cell="${ci}"]`)
        ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    };
  });
}

// ---- 导出 -------------------------------------------------------------

function download(name, text, mime = 'text/plain') {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function invList() {
  return [...S.inv.values()].sort((a, b) => a.cardId - b.cardId);
}

function exportCsv() {
  const rows = [['cardId', 'trained', '角色', '卡名', '星级', '属性', '乐队']];
  for (const x of invList()) {
    const i = cardInfo(x.cardId);
    rows.push([x.cardId, x.trained ? 1 : 0, i.character, i.name, i.rarity, i.attribute, i.band]);
  }
  const csv = rows.map((r) => r.map((v) => {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }).join(',')).join('\n');
  download('bestdori-cards.csv', '\ufeff' + csv, 'text/csv');
}

function exportJson() {
  const data = invList().map((x) => {
    const i = cardInfo(x.cardId);
    return {
      cardId: x.cardId, trained: x.trained, character: i.character, title: i.name,
      rarity: i.rarity, attribute: i.attribute, band: i.band,
      url: `https://bestdori.com/info/cards/${x.cardId}`,
    };
  });
  download('bestdori-cards.json', JSON.stringify(data, null, 2), 'application/json');
}

function exportIds() {
  download('bestdori-ids.txt', invList().map((x) => x.cardId).join('\n'));
}

function exportMarkdown() {
  const lines = ['# 卡牌清单', '', `共 ${S.inv.size} 张`, '',
    '| 卡号 | 角色 | 卡名 | 星级 | 属性 | 特训 | 链接 |',
    '| --- | --- | --- | --- | --- | --- | --- |'];
  for (const x of invList()) {
    const i = cardInfo(x.cardId);
    lines.push(`| ${x.cardId} | ${i.character} | ${i.name} | ${rarityLabel(i.rarity)} | ` +
      `${ATTR_CN[i.attribute] || i.attribute} | ${x.trained ? '是' : ''} | ` +
      `[Bestdori](https://bestdori.com/info/cards/${x.cardId}) |`);
  }
  download('卡牌清单.md', lines.join('\n'), 'text/markdown');
}

// ---- 导航（底部栏 / 侧边栏 + 分页）-------------------------------------

const TABS = ['import', 'result', 'inventory', 'sync'];

// ===== 通用弹层：看大图 / 卡牌详情 =====
//
// 手机上没有"悬停看细节"这回事，也没有桌面那种双栏核对面板 —— 详情只能靠
// 弹层。这里的 openLightbox 同时服务两件事：
//   · 放大看原图（zoomable=true，顶部出现缩放按钮）
//   · 看某张卡的详情（两张对照图 + 卡名/角色/属性 + Bestdori 卡面页链接）

let LB_SCALE = 1;
//: 大图模式下点图片要做什么（用来从"放大单图"跳到"两张对比"）
let LB_IMG_CLICK = null;

/** 打开弹层。
 *  :param zoomable: 显示缩放按钮（看单张大图时用）
 *  :param hint: 顶部提示条文字
 *  :param onImageClick: 点大图本体时的回调（用于跳到对比视图）
 */
function openLightbox(html, title, { zoomable = false, hint = '', onImageClick = null } = {}) {
  LB_SCALE = 1;
  LB_IMG_CLICK = onImageClick;
  $('lbTitle').textContent = title || '';
  $('lbBody').innerHTML = html;
  $('lbBody').scrollTop = 0;
  $('lbBody').scrollLeft = 0;
  $('lbHint').hidden = !hint;
  $('lbHint').textContent = hint || '';
  ['lbZoomOut', 'lbZoomIn', 'lbFit'].forEach((id) => { $(id).hidden = !zoomable; });
  applyLbScale();

  // 只有"大图模式"的那张直接子图才触发跳转 —— 详情里的对比图另有 handler，
  // 不靠父级冒泡，避免点小图也被当成"点大图"
  $('lbBody').onclick = (e) => {
    if (LB_IMG_CLICK && e.target.tagName === 'IMG' && e.target.parentElement === $('lbBody')) {
      LB_IMG_CLICK();
    }
  };
  const directImg = $('lbBody').querySelector(':scope > img');
  if (directImg) directImg.style.cursor = onImageClick ? 'zoom-in' : 'default';
  $('lightbox').hidden = false;
}

function closeLightbox() {
  $('lightbox').hidden = true;
  $('lbBody').innerHTML = '';   // 顺手释放大图，别攥着内存
  $('lbBody').onclick = null;
  LB_IMG_CLICK = null;
}

/** 1 = 适应容器宽度；放大靠百分比撑开，配合 .lb-body 的滚动就能拖动看细节 */
function setLbScale(s) {
  LB_SCALE = Math.min(4, Math.max(0.5, s));
  applyLbScale();
}

function applyLbScale() {
  const img = $('lbBody').querySelector('img');
  if (img) img.style.width = `${Math.round(LB_SCALE * 100)}%`;
}

// ===== 结果页原图：原位缩放（滚轮 / 双指 / 拖动）=====
//
// 用户要的是「原图窗口本身能放大」，不是按按钮另开一页。
// 缩放的是整个 #preview 容器 —— 切分框是画在它里面的绝对定位元素，
// 会跟着一起缩放，正好保证框始终压在对应卡面上。

const ZV = {
  scale: 1, x: 0, y: 0, shot: -1,
  dragging: false, sx: 0, sy: 0, bx: 0, by: 0, pinch: 0, pinchScale: 1,
};

function applyZoomView() {
  const el = $('preview');
  if (!el) return;
  el.style.transformOrigin = 'center center';
  el.style.transform = `translate(${ZV.x}px, ${ZV.y}px) scale(${ZV.scale})`;
  const badge = $('zoomBadge');
  if (badge) {
    badge.hidden = ZV.scale <= 1.01;
    badge.textContent = `${ZV.scale.toFixed(1)}×`;
  }
}

function setZoom(s) {
  ZV.scale = Math.min(8, Math.max(1, s));
  if (ZV.scale <= 1.01) { ZV.x = 0; ZV.y = 0; }   // 回到 1× 时自动居中
  applyZoomView();
}

const _touchDist = (t) => Math.hypot(
  t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);

/** 原图区手势：滚轮缩放、双指捏合、放大后拖动、双击复位 */
function bindPreviewZoom() {
  const body = $('previewBody');
  if (!body) return;

  body.addEventListener('wheel', (e) => {
    if (!$('preview')) return;
    e.preventDefault();            // 滚轮在这个区域专用于缩放，不滚页面
    setZoom(ZV.scale * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
  }, { passive: false });

  body.addEventListener('touchstart', (e) => {
    if (e.touches.length === 2) {
      ZV.pinch = _touchDist(e.touches);
      ZV.pinchScale = ZV.scale;
      ZV.dragging = false;
    } else if (e.touches.length === 1 && ZV.scale > 1.01) {
      ZV.dragging = true;
      ZV.sx = e.touches[0].clientX;
      ZV.sy = e.touches[0].clientY;
      ZV.bx = ZV.x;
      ZV.by = ZV.y;
    }
  }, { passive: true });

  body.addEventListener('touchmove', (e) => {
    if (e.touches.length === 2 && ZV.pinch) {
      e.preventDefault();
      setZoom(ZV.pinchScale * (_touchDist(e.touches) / ZV.pinch));
    } else if (ZV.dragging && e.touches.length === 1) {
      e.preventDefault();
      ZV.x = ZV.bx + (e.touches[0].clientX - ZV.sx);
      ZV.y = ZV.by + (e.touches[0].clientY - ZV.sy);
      applyZoomView();
    }
  }, { passive: false });

  body.addEventListener('touchend', () => { ZV.dragging = false; ZV.pinch = 0; });
  // 桌面上的快捷复位/快速放大
  body.addEventListener('dblclick', () => setZoom(ZV.scale > 1.01 ? 1 : 2.5));
}

/** 放大看「某一张」缩略图（截图格 或 Bestdori 卡图）。点图可跳到对比视图。 */
function zoomCellImage(si, ci, which) {
  const shot = S.shots[si];
  const r = shot && shot.cells ? shot.cells[ci] : null;
  if (!r) return;
  const isCell = which === 'cell';
  const src = isCell ? r.cellThumb : (r.cardId != null ? thumbUrl(r.cardId, r.trained) : '');
  if (!src) { log('这一格没有可放大的图', 'err'); return; }
  const info = r.cardId != null ? cardInfo(r.cardId) : null;
  openLightbox(
    `<img src="${src}" alt="">`,
    `${isCell ? '截图里这一格' : 'Bestdori 卡图'} · 第 ${ci + 1} 格` +
      (info ? ` · ${info.name}` : ''),
    { zoomable: true, hint: '点图片查看两张对比', onImageClick: () => showCardDetail(si, ci) },
  );
}

/** 看某一格的卡面详情（两张对照图 + 卡名属性 + 卡面页）。
 *  这里的图也能点：点哪张就放大哪张，来回切换。 */
function showCardDetail(si, ci) {
  const shot = S.shots[si];
  const r = shot && shot.cells ? shot.cells[ci] : null;
  if (!r) return;
  const info = r.cardId != null ? cardInfo(r.cardId) : null;
  const url = r.cardId != null ? `https://bestdori.com/info/cards/${r.cardId}` : '';
  const attrC = info ? (ATTR_COLOR[info.attribute] || '#888') : '#666';

  const imgs =
    '<div class="detail-imgs">' +
    (r.cellThumb
      ? `<figure><img src="${r.cellThumb}" alt="截图格" data-zoom="cell"
           title="点击放大这张"><figcaption>截图里这一格</figcaption></figure>`
      : '') +
    (r.cardId != null
      ? `<figure><img src="${thumbUrl(r.cardId, r.trained)}" loading="lazy" alt=""
           data-zoom="match" title="点击放大这张">
         <figcaption>Bestdori 同款卡图</figcaption></figure>`
      : '') +
    '</div>';

  const tags = info
    ? '<div class="detail-tags">' +
      `<span class="pill" style="color:${attrC};border-color:${attrC}66">${ATTR_CN[info.attribute] || info.attribute}</span>` +
      `<span class="pill">${rarityLabel(info.rarity)}</span>` +
      (r.trained ? '<span class="pill ok">特训后</span>' : '') +
      (r.score != null ? `<span class="pill">匹配 ${Number(r.score).toFixed(3)}</span>` : '') +
      '</div>'
    : '';

  const foot = url
    ? `<div class="field"><label>卡面详情页</label>
         <input type="text" readonly value="${url}" onclick="this.select()"></div>
       <a class="primary block detail-link" href="${url}" target="_blank" rel="noopener">
         在 Bestdori 打开卡面页 ↗</a>`
    : '<p class="footnote">这一格没有识别出卡牌，所以没有可核对的卡面页。</p>';

  openLightbox(
    imgs +
    `<h3 class="detail-name">${escapeHtml(info ? info.name : '未识别')}</h3>` +
    (info
      ? `<p class="detail-sub">${escapeHtml(info.character)}` +
        (info.band ? ` · ${escapeHtml(info.band)}` : '') +
        ` · 卡号 ${r.cardId}</p>`
      : '') +
    tags + foot,
    `卡牌详情 · 第 ${ci + 1} 格`,
  );

  // 详情里的两张图也能点开单独放大（与上面的大图互相切换）
  $('lbBody').querySelectorAll('.detail-imgs img[data-zoom]').forEach((img) => {
    img.style.cursor = 'zoom-in';
    img.onclick = (ev) => {
      ev.stopPropagation();
      zoomCellImage(si, ci, img.dataset.zoom);
    };
  });
}

/** 切到某一页 */
function showTab(name) {
  if (!TABS.includes(name)) return;
  S.tab = name;
  document.querySelectorAll('.view').forEach((v) => {
    v.classList.toggle('active', v.dataset.view === name);
  });
  document.querySelectorAll('.navitem').forEach((b) => {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  // 切页时把内容区滚回顶部，否则从长列表切过去会停在半空
  $('main').scrollTop = 0;
  // 结果页的原图是按容器宽度缩放的，切过去时重新量一次
  if (name === 'result') renderPreview();
}

/**
 * 更新导航角标。
 *
 * 规则：0 就不显示（空角标比没有更让人困惑），
 * 超过 99 显示 99+。
 */
function updateBadges() {
  const nCells = S.shots.reduce((s, x) => s + x.cells.length, 0);
  const nInv = S.inv.size;
  const nPending = [...S.inv.values()].filter((x) => x.status === 'ambiguous').length;

  const set = (id, n, muted = false) => {
    const el = $(id);
    if (!el) return;
    if (!n) { el.hidden = true; return; }
    el.hidden = false;
    el.textContent = n > 99 ? '99+' : String(n);
    el.classList.toggle('muted', muted);
  };
  set('badgeImport', S.files.length);
  set('badgeResult', nCells);
  set('badgeInventory', nInv);
  // 待确认的数挂在同步页 —— 那是最需要处理的数量
  set('badgeSync', nPending, true);
}

// ---- 运行日志抽屉 ------------------------------------------------------

function openLog(open) {
  $('logsheet').hidden = !open;
  $('logScrim').hidden = !open;
  if (open) $('log').scrollTop = $('log').scrollHeight;
}

// ---- Bestdori 同步（只在原生壳里可用）---------------------------------

/**
 * 是否跑在原生壳里。
 *
 * Capacitor 会往 WebView 注入 `window.Capacitor`；纯网页里没有这个全局。
 * 刻意**不** import `@capacitor/core` —— 那样纯网页版就得依赖一个用不上的包。
 */
const IS_NATIVE = typeof window.Capacitor !== 'undefined'
  && typeof window.Capacitor.isNativePlatform === 'function'
  && window.Capacitor.isNativePlatform();

/** 会话 cookie 存 localStorage（等价浏览器登录态，勿外传） */
const LS = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* 隐私模式 */ } },
  remove: (k) => { try { localStorage.removeItem(k); } catch { /* 同上 */ } },
};

let BD = null;            // BestdoriAccount
let BD_PROFILES = [];     // 云端档案列表

function setSyncState(text, kind = '') {
  const el = $('syncState');
  el.textContent = text;
  el.className = 'pill' + (kind ? ` ${kind}` : '');
}

async function initSync() {
  if (!IS_NATIVE) {
    // 网页版：把同步表单收起来，换成一段解释 —— 摆一个用不了的表单更糟
    $('syncCard').hidden = true;
    $('syncUnavailable').hidden = false;
    log('浏览器环境：Bestdori 接口有 CORS 限制（实测连公开接口都被拦），' +
        '同步功能只在安卓 App 里可用。网页版请用「清单」页导出。');
    return;
  }
  $('syncCard').hidden = false;
  $('syncUnavailable').hidden = true;
  const mod = await import('./src/bestdori.js');
  BD = new mod.BestdoriAccount({ store: LS });
  bindSync();
  if (BD.hasSession) {
    setSyncState('恢复登录…', 'warn');
    // 启动时网络常常还没就绪，第一次失败先重试一次再下结论。
    // **失败时绝不能清掉会话** —— 清了就真的要重新登录，一次误判就等于
    // 把"记住登录"作废（用户反馈的正是这个）。
    let me = await BD.me();
    if (!me) {
      await new Promise((r) => setTimeout(r, 1500));
      me = await BD.me();
    }
    if (me) await onLoggedIn(me, true);
    else {
      setSyncState('会话可能已过期，请重新登录');
      log('会话验证失败（已保留，未清除）。若反复失败请重新登录。', 'err');
    }
  }
}

function bindSync() {
  $('btnLogin').onclick = async () => {
    const u = $('bdUser').value.trim();
    const p = $('bdPass').value;
    if (!u || !p) { log('请填写用户名和密码', 'err'); return; }
    $('btnLogin').disabled = true;
    setSyncState('登录中…', 'warn');
    try {
      await BD.login(u, p);
      $('bdPass').value = '';
      log('Bestdori 登录成功', 'ok');
      await onLoggedIn(await BD.me());
    } catch (e) {
      setSyncState('登录失败', 'err');
      log(`登录失败：${e.message}`, 'err');
    } finally {
      $('btnLogin').disabled = false;
    }
  };

  $('btnLogout').onclick = () => {
    BD.clearSession();
    BD_PROFILES = [];
    $('syncPanel').hidden = true;
    $('syncLogin').hidden = false;
    setSyncState('未登录');
    log('已退出 Bestdori');
  };

  $('btnPlan').onclick = () => runPlan(true);
  $('btnImport').onclick = () => runImport();
  $('btnPeek').onclick = () => peekProfile();
  $('btnPeekClose').onclick = () => { $('peekBox').hidden = true; };
  // 换档案时收起预览，否则看到的还是上一份的内容
  $('profileSel').onchange = () => { $('peekBox').hidden = true; };
}

/** 一次渲染多少张卡（一次塞几千个 <img> 手机会卡；lazy 也救不了元素数量） */
const PEEK_LIMIT = 300;

/** 查看选中档案里已登记了哪些卡 —— 只读，不改动云端 */
async function peekProfile() {
  const box = $('peekBox');
  if (!box.hidden) { box.hidden = true; return; }   // 再点一次收起

  const mod = await import('./src/bestdori.js');
  const idx = Number($('profileSel').value) || 0;
  const entry = BD_PROFILES[idx];
  if (!entry) { log('还没读取到云端档案，先重新登录试试', 'err'); return; }

  let cards;
  try {
    cards = mod.decodeCards(entry);
  } catch (e) {
    log(`档案解析失败：${e.message}`, 'err');
    return;
  }
  // decodeCards 返回的是 Map（不是普通对象）—— 用 Object.keys 会得到空数组，
  // 表现就是"档案里一张卡都没有"。
  const ids = [...cards.keys()].sort((a, b) => a - b);
  if (!ids.length) {
    $('peekStat').textContent = `档案 ${idx + 1} 里还没有卡。`;
    $('peekGrid').innerHTML = '';
    box.hidden = false;
    return;
  }
  const trained = ids.filter((i) => cards.get(i).train).length;
  $('peekStat').textContent =
    `档案 ${idx + 1}：${ids.length} 张（特训后 ${trained}）` +
    (ids.length > PEEK_LIMIT ? ` · 只显示前 ${PEEK_LIMIT} 张` : '');

  $('peekGrid').innerHTML = ids.slice(0, PEEK_LIMIT).map((id) => {
    const info = cardInfo(id);
    const isTrained = cards.get(id).train;
    const t = isTrained ? 't' : 'n';
    const other = t === 't' ? 'n' : 't';
    // 约 6.5% 的卡没有 *_normal 图，加载失败就换另一形态
    return `<figure class="peek-cell" title="${escapeHtml(info.name)}${isTrained ? ' · 特训后' : ''}">
      <img src="data/thumbs/${id}_${t}.webp" loading="lazy" alt=""
           onerror="this.onerror=null;this.src='data/thumbs/${id}_${other}.webp'">
      <figcaption>${escapeHtml(info.name)}</figcaption>
    </figure>`;
  }).join('');
  box.hidden = false;
  log(`云端档案 ${idx + 1}：${ids.length} 张（特训后 ${trained}）`);
}

async function onLoggedIn(me, silent = false) {
  const name = me?.username || me?.name || me?.email || '已登录';
  setSyncState(`已登录：${name}`, 'ok');
  if (!silent) log(`Bestdori 已登录：${name}`, 'ok');
  $('syncLogin').hidden = true;
  $('syncPanel').hidden = false;
  await loadProfiles();
}

async function loadProfiles() {
  const sel = $('profileSel');
  sel.innerHTML = '<option>读取中…</option>';
  try {
    BD_PROFILES = await BD.fetchProfiles();
    if (!BD_PROFILES.length) {
      sel.innerHTML = '<option>（云端没有档案）</option>';
      $('syncResult').textContent = '请先到 Bestdori 的 Profile Manager 创建一个档案。';
      return;
    }
    sel.innerHTML = BD_PROFILES.map((p, i) => {
      const nm = p?.name || p?.data?.name || `档案 ${i + 1}`;
      return `<option value="${i}">${escapeHtml(String(nm))}</option>`;
    }).join('');
    $('syncResult').textContent = `云端共 ${BD_PROFILES.length} 份档案。`;
  } catch (e) {
    sel.innerHTML = '<option>读取失败</option>';
    $('syncResult').textContent = e.message;
    log(`读取档案失败：${e.message}`, 'err');
  }
}

/** 只算不写，给出「要新增 / 要升级 / 已存在」的数量 */
async function runPlan(verbose) {
  if (!BD || !S.inv.size) { log('清单为空，先识别一些截图', 'err'); return null; }
  const mod = await import('./src/bestdori.js');
  const idx = Number($('profileSel').value) || 0;
  setSyncState('生成计划…', 'warn');
  try {
    const { plan, profilesTotal } = await mod.buildImportPlan(
      BD, [...S.inv.values()], S.meta.cards, { profileIndex: idx });
    const txt = `档案 ${idx + 1}/${profilesTotal}：新增 ${plan.toAdd.length}，` +
      `升级为特训后 ${plan.toUpgrade.length}，已存在 ${plan.already.length}`;
    $('syncResult').textContent = txt;
    if (verbose) log(`导入计划 —— ${txt}`, 'ok');
    setSyncState('已登录', 'ok');
    return plan;
  } catch (e) {
    setSyncState('计划失败', 'err');
    $('syncResult').textContent = e.message;
    log(`生成计划失败：${e.message}`, 'err');
    return null;
  }
}

async function runImport() {
  if (!BD || !S.inv.size) { log('清单为空，先识别一些截图', 'err'); return; }
  const plan = await runPlan(false);
  if (!plan) return;
  const n = plan.toAdd.length + plan.toUpgrade.length;
  if (!n) { log('远端已经是最新的，没有需要写入的卡', 'ok'); return; }
  const ok = window.confirm(
    `即将写入 Bestdori（全量覆盖该档案的卡牌列表）：\n\n` +
    `  新增 ${plan.toAdd.length} 张\n` +
    `  升级为特训后 ${plan.toUpgrade.length} 张\n` +
    `  已存在跳过 ${plan.already.length} 张\n\n` +
    `确定继续？`);
  if (!ok) { log('已取消导入'); return; }

  const mod = await import('./src/bestdori.js');
  const idx = Number($('profileSel').value) || 0;
  $('btnImport').disabled = true;
  setSyncState('写入中…', 'warn');
  try {
    const stats = await mod.importInventory(
      BD, [...S.inv.values()], S.meta.cards, { profileIndex: idx, apply: true });
    setSyncState('已登录', 'ok');
    $('syncResult').textContent = stats.summary();
    log(`同步完成 —— ${stats.summary()}`, 'ok');
    await loadProfiles();
  } catch (e) {
    setSyncState('写入失败', 'err');
    log(`写入失败：${e.message}`, 'err');
  } finally {
    $('btnImport').disabled = false;
  }
}

// ---- 事件绑定 ---------------------------------------------------------

function bind() {
  // 导航分页
  document.querySelectorAll('.navitem').forEach((b) => {
    b.onclick = () => showTab(b.dataset.tab);
  });

  // 原图：滚轮/双指原位缩放（不是按钮另开一页）
  bindPreviewZoom();

  // 弹层
  $('lbClose').onclick = () => closeLightbox();
  $('lbZoomIn').onclick = () => setLbScale(LB_SCALE * 2);
  $('lbZoomOut').onclick = () => setLbScale(LB_SCALE / 2);
  $('lbFit').onclick = () => setLbScale(1);
  // 点弹层背景（而不是内容）也关掉
  $('lightbox').onclick = (e) => { if (e.target.id === 'lightbox') closeLightbox(); };

  // 运行日志抽屉
  $('btnLog').onclick = () => openLog(true);
  $('btnLogClose').onclick = () => openLog(false);
  $('logScrim').onclick = () => openLog(false);
  // 抽屉开着时按 Esc 关掉（桌面习惯）
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('lightbox').hidden) { closeLightbox(); return; }
    if (!$('logsheet').hidden) openLog(false);
  });

  const dz = $('dropzone');
  const fi = $('fileInput');
  const di = $('dirInput');

  dz.onclick = () => fi.click();
  fi.onchange = () => addFiles([...fi.files]);
  di.onchange = () => addFiles([...di.files]);

  ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.remove('over');
  }));
  dz.addEventListener('drop', (e) => {
    const items = [...(e.dataTransfer?.files ?? [])].filter((f) => f.type.startsWith('image/'));
    if (items.length) addFiles(items);
  });

  $('mode').onchange = () => {
    const grid = $('mode').value === 'grid';
    $('rows').disabled = !grid;
    $('cols').disabled = !grid;
  };

  $('btnRun').onclick = () => runRecognition().catch((e) => {
    log(`识别失败：${e.message}`, 'err'); console.error(e);
    setStatus('出错', 'err'); S.busy = false;
    $('btnRun').disabled = false;
  });

  $('btnClear').onclick = () => {
    S.files = []; S.shots = []; S.inv.clear(); S.selected = null;
    $('fileInput').value = ''; $('dirInput').value = '';
    $('btnRun').disabled = true; $('btnClear').disabled = true;
    $('resultCount').textContent = '';
    renderAll();
    log('已清空');
  };

  // 清单：搜索 / 筛选 / 批量
  $('invSearch').oninput = (e) => { S.filter.q = e.target.value; renderInventory(); };
  $('invFilter').onchange = (e) => { S.filter.mode = e.target.value; renderInventory(); };
  $('btnConfirmAll').onclick = () => {
    let n = 0;
    for (const x of S.inv.values()) if (x.status === 'ambiguous') { x.status = 'matched'; n++; }
    renderInventory();
    log(`已确认 ${n} 张待确认卡牌`, 'ok');
  };
  $('btnClearInv').onclick = () => {
    const n = S.inv.size;
    if (!n) return;
    if (!confirm(
      `确定清空吗？共 ${n} 张卡。\n\n` +
      '注意：网页/手机端的清单是由识别结果生成的，所以识别结果也会一并清掉。'
    )) return;
    // 只清 S.inv 没用 —— renderAll 里的 buildInventoryFromShots 会立刻
    // 从识别结果把清单重建回来（桌面版的清单是独立文件，所以那边没这问题）。
    // 这里要连识别结果一起清，语义才对得上。
    S.shots = [];
    S.inv.clear();
    S.selected = null;
    S.activeShot = 0;
    renderAll();
    log(`已清空清单与识别结果（${n} 张）`, 'ok');
  };

  $('btnRemovePending').onclick = () => {
    let n = 0;
    for (const [k, x] of [...S.inv]) if (x.status === 'ambiguous') { S.inv.delete(k); n++; }
    renderInventory();
    log(`已移除 ${n} 张待确认卡牌`, 'ok');
  };

  $('btnCsv').onclick = exportCsv;
  $('btnJson').onclick = exportJson;
  $('btnIds').onclick = exportIds;
  $('btnMarkdown').onclick = exportMarkdown;
}

function addFiles(files) {
  const imgs = files.filter((f) => f.type.startsWith('image/'));
  if (!imgs.length) { log('没有可用的图片文件', 'err'); return; }
  const seen = new Set(S.files.map((f) => `${f.name}:${f.size}`));
  let added = 0;
  for (const f of imgs) {
    const k = `${f.name}:${f.size}`;
    if (seen.has(k)) continue;
    seen.add(k); S.files.push(f); added++;
  }
  $('btnRun').disabled = !S.files.length;
  $('btnClear').disabled = !S.files.length;
  renderAll();
  log(`已加入 ${added} 张（共 ${S.files.length} 张）`, 'ok');
}

// ---- 启动 -------------------------------------------------------------

// ---- 启动 -------------------------------------------------------------

/**
 * 调试钩子：方便在浏览器控制台里查看状态，也供自动化测试注入数据
 * （`scripts/sync_check.py` 用它塞一份受控的清单来验证同步逻辑）。
 */
window.__bdh = {
  S,
  cardInfo,
  renderAll,
  renderInventory,
  buildInventoryFromShots,
  addFiles,
  runRecognition,
  runPlan,
  runImport,
  peekProfile,
  showCardDetail,
  showTab,
  openLog,
  get account() { return BD; },
  get isNative() { return IS_NATIVE; },
};

(async function init() {
  bind();
  showTab('import');
  try {
    await loadData();
    setStatus('就绪', 'ready');
    log('准备就绪，拖入截图开始识别', 'ok');
    await initSync();
  } catch (e) {
    setStatus('数据加载失败', 'err');
    log(`初始化失败：${e.message}`, 'err');
    log('如果是本地打开（file://），浏览器会拦截 fetch。请用本地 HTTP 服务打开：', 'err');
    log('  python -m http.server 8000 --directory docs', '');
    console.error(e);
  }
})();
