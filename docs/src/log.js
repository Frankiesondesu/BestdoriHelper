/**
 * 运行日志（右上角 ☰ 抽屉）。
 *
 * 以前只有 app.js 会写日志，组队页和道具页一行都不写 —— 用户点开抽屉看到的
 * 还是几分钟前「已加入 1 张」那种旧记录，自然觉得日志没用。
 *
 * 现在抽成独立模块，三个页面共用同一份实现；抽屉关着的时候有新日志，
 * ☰ 按钮上会亮一个小圆点。
 */

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

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

/**
 * 追加一行日志。
 * @param {string} msg
 * @param {''|'ok'|'warn'|'err'} cls
 */
export function log(msg, cls = '') {
  const el = document.getElementById('log');
  if (!el) return;
  const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  const line = document.createElement('div');
  line.innerHTML = `<span class="t">${t}</span> `
    + (cls ? `<span class="${cls}">${esc(msg)}</span>` : esc(msg));
  el.appendChild(line);
  // 只留最近 400 行，长会话不把 DOM 撑爆
  while (el.childElementCount > 400) el.removeChild(el.firstElementChild);
  el.scrollTop = el.scrollHeight;
  markLogUnread();
}
