/**
 * 运行日志（右上角 ☰ 抽屉）。
 *
 * 三个页面（导入 / 清单 / 道具 / 组队）共用同一份实现。几条设计约束：
 *
 * 1. **不打断回看**：用户往上翻历史时，新日志不再把他拽回底部，
 *    只在 ☰ 上亮未读点。滚回底部后恢复自动跟随。
 * 2. **进度用同一行刷新**：枚举/OCR 这类高频进度如果每次追加一行，
 *    400 行窗口几秒就被刷满，真正有用的信息反而被挤掉。
 * 3. **可复制**：出问题时让用户贴日志，比让他复述现象准得多。
 */

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const MAX_LINES = 400;
const FOLLOW_PX = 28;

/** 当前进度行（下一行普通日志会把它定稿） */
let progressEl = null;

const logEl = () => document.getElementById('log');

/** 是否贴着底部 —— 决定要不要自动跟随 */
function atBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= FOLLOW_PX;
}

/** 抽屉关着时给 ☰ 加个未读点 */
export function markLogUnread() {
  const btn = document.getElementById('btnLog');
  const sheet = document.getElementById('logsheet');
  if (!btn || (sheet && !sheet.hidden)) return;
  btn.classList.add('unread');
}

export function clearLogUnread() {
  const btn = document.getElementById('btnLog');
  if (btn) btn.classList.remove('unread');
}

function stamp() {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

function trim(el) {
  while (el.childElementCount > MAX_LINES) {
    const first = el.firstElementChild;
    if (first === progressEl) progressEl = null;
    el.removeChild(first);
  }
}

/** 把未定稿的进度行收尾，避免它挡住下一条日志的位置 */
function endProgress() {
  if (progressEl && progressEl.isConnected) progressEl.classList.remove('prog');
  progressEl = null;
}

/**
 * 追加一行日志。
 * @param {string} msg
 * @param {''|'ok'|'warn'|'err'} cls
 */
export function log(msg, cls = '') {
  const el = logEl();
  if (!el) return;
  endProgress();
  const follow = atBottom(el);
  const line = document.createElement('div');
  line.innerHTML = `<span class="t">${stamp()}</span> `
    + (cls ? `<span class="${cls}">${esc(msg)}</span>` : esc(msg));
  el.appendChild(line);
  trim(el);
  if (follow) el.scrollTop = el.scrollHeight;
  markLogUnread();
}

/**
 * 进度行：同一个位置反复刷新，不进历史。
 * 适合「枚举 37%」「OCR 第 2/12 张」这类高频更新。
 * @param {string} msg
 * @param {''|'ok'|'warn'|'err'} cls
 */
export function logProgress(msg, cls = '') {
  const el = logEl();
  if (!el) return;
  const follow = atBottom(el);
  if (!progressEl || !progressEl.isConnected) {
    progressEl = document.createElement('div');
    progressEl.className = 'prog';
    progressEl.innerHTML = `<span class="t">${stamp()}</span> <span class="pv"></span>`;
    el.appendChild(progressEl);
    trim(el);
  }
  const pv = progressEl.querySelector('.pv');
  pv.className = `pv${cls ? ` ${cls}` : ''}`;
  pv.textContent = msg;
  if (follow) el.scrollTop = el.scrollHeight;
  markLogUnread();
}

/** 进度结束：把最后一条进度定稿成普通日志行（保留最终数字） */
export function logProgressDone(msg, cls = 'ok') {
  const el = logEl();
  if (!el) return;
  if (!progressEl || !progressEl.isConnected) { log(msg, cls); return; }
  const pv = progressEl.querySelector('.pv');
  progressEl.classList.remove('prog');
  pv.className = `pv${cls ? ` ${cls}` : ''}`;
  pv.textContent = msg;
  progressEl = null;
  el.scrollTop = el.scrollHeight;
}

/** 清空日志（抽屉头部按钮） */
export function clearLog() {
  const el = logEl();
  if (el) el.innerHTML = '';
  progressEl = null;
}

/**
 * 复制一段文本到剪贴板。剪贴板 API 在 http / 非安全上下文不可用，
 * 回退到隐藏 textarea + execCommand。
 * @param {string} text
 * @returns {Promise<boolean>}
 */
export async function copyText(text) {
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

/** 复制全部日志 */
export async function copyLog() {
  const el = logEl();
  if (!el) return false;
  return copyText([...el.children].map((n) => n.textContent.trim()).join('\n'));
}
