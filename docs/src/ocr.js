/**
 * 浏览器端 OCR —— 读游戏「商店 / 道具」截图里的道具等级。
 *
 * ## 为什么是 tesseract.js
 *
 * GitHub Pages 是纯静态站，跑不了 Python。要在**打开网页就能用**的前提下做
 * 识别，只能把 OCR 放进浏览器。`tesseract.js` 是 wasm 版的 Tesseract：
 *
 * - **免安装、免后端**，一个 `<script>` 即可
 * - 中文模型随站点提供（约 26 MB），**进「道具」页才下载**，不拖慢首屏
 * - 首次之后走浏览器缓存
 *
 * ## 识别策略
 *
 * 截图布局是固定的「等级 X  Y/Z / 道具名 / 价格」，但 Tesseract 给的是
 * **纯文本行**，没有坐标（除非用 blocks 模式）。这里采用**行序配对**：
 *
 *   「等级7」的下一行如果是「8/8」，再下一行就是道具名
 *
 * 比依赖坐标稳，也不受分辨率影响。
 */

/**
 * 运行时**全部放在本站**（`docs/vendor/tesseract/`），不再走 CDN。
 *
 * 2026-11 实测：jsDelivr 在这台机器上直接 `ERR_CONNECTION_RESET`，
 * 三个备用 CDN 也一样 —— 结果就是「道具」页连脚本都加载不出来，
 * 提示"OCR 模型加载失败（网络问题？）"，点识别毫无反应。
 * 国内网络下 jsDelivr 被重置是常态，靠 CDN 兜底等于没兜底。
 *
 * 现在带的文件（共约 12 MB，只在进「道具」页时才下载）：
 *   tesseract.min.js / worker.min.js
 *   tesseract-core-simd-lstm.wasm.js / tesseract-core-lstm.wasm.js（SIMD 与非 SIMD 各一份）
 *   chi_sim.traineddata.gz / eng.traineddata.gz（tessdata_fast）
 */
const VENDOR = new URL('../vendor/tesseract/', import.meta.url).href;

/** worker / core / 语言包都指向本地目录；corePath 给目录，tesseract 会自己挑 SIMD 版本 */
const OCR_OPTS = {
  workerPath: VENDOR + 'worker.min.js',
  corePath: VENDOR,
  langPath: VENDOR,
  cachePath: 'bdh-ocr',
};

let loading = null;
/** 复用的 worker（创建一次，别每次识别都新建——语言包要重新载入） */
let workerPromise = null;
let warmState = 'idle';    // idle | loading | ready | failed

/** 按顺序尝试多个 CDN，哪个通用哪个 */
function loadScript() {
  if (window.Tesseract) return Promise.resolve();
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = VENDOR + 'tesseract.min.js';
    s.onload = () => resolve();
    s.onerror = () => reject(new Error('OCR 运行时加载失败：vendor/tesseract/tesseract.min.js'));
    document.head.appendChild(s);
  });
  return loading;
}

//: 道具名关键词 -> Bestdori 的 items 槽位（与桌面脚本保持一致）
const BAND_ALIASES = [
  ['popipa', 'PoppinParty'], ['poppin', 'PoppinParty'],
  ['afterglow', 'Afterglow'],
  ['paspare', 'PastelPalettes'], ['pastel', 'PastelPalettes'],
  ['roselia', 'Roselia'],
  ['hello happy', 'HelloHappyWorld'], ['hhw', 'HelloHappyWorld'],
  ['monica', 'Morfonica'], ['morfonica', 'Morfonica'],
  ['ras', 'RaiseASuilen'], ['raise', 'RaiseASuilen'],
  ['mygo', 'MyGO'],
];
const ITEM_KINDS = ['吉他', '贝斯', '爵士鼓', '鼓', '键盘', '麦克风', '海报', '宣传单', '传单'];
const MENU_KW = ['意大利面', '意面', '冰霜', '马卡龙', '水果挞', '海螺包', '咖啡', '蛋糕', '芭菲', '圣代', '松饼', '布丁', '曲奇', '沙拉', '寿司', '饭团'];
const PLAZA_KW = ['椰子树', '足浴池', '铜像', '喷泉', '盆栽', '长椅', '路灯', '花坛', '秋千', '钟楼'];
const MAG_KW = ['电影杂志', '音乐杂志', '时尚杂志', '杂志'];

export function matchSlot(name) {
  if (!name) return null;
  const n = name.toLowerCase();
  if (MAG_KW.some((k) => name.includes(k))) return 'Magazine';
  if (ITEM_KINDS.some((k) => name.includes(k))) {
    for (const [alias, slot] of BAND_ALIASES) if (n.includes(alias)) return slot;
    if (n.includes('circle')) return 'Everyone';
  }
  if (MENU_KW.some((k) => name.includes(k))) return 'Menu';
  if (PLAZA_KW.some((k) => name.includes(k))) return 'Plaza';
  return null;
}

/**
 * 从带坐标的 OCR 行里解析「槽位 -> 等级」。
 *
 * 靠**几何关系**配对，而不是文本顺序：
 *   1. 找到所有 `等级X` 的行
 *   2. 在它的**正下方**（y 更大）找道具名：同一列附近、纵向 20~110px
 * 这就是截图里「等级 / 名称 / 价格」的固定布局。
 *
 * @param {{text:string,x:number,y:number}[]} lines
 */
export function parseOcrLines(lines) {
  const items = {};
  const detail = [];
  const unmatched = [];
  const used = new Set();

  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].text.match(/等级\s*(\d+)/);
    if (!m) continue;
    const level = Number(m[1]);
    const lvLine = lines[i];

    // 在下方找最近的、水平位置相近的候选名
    let best = null;
    let bestDy = Infinity;
    for (let j = 0; j < lines.length; j++) {
      if (j === i || used.has(j)) continue;
      const t = lines[j].text;
      if (/等级|提升|售完|购买|确认|剩余|完成|设置|EXP/.test(t)) continue;
      if (/^[\d\s/.,%:]+$/.test(t)) continue;          // 纯数字/进度/时间
      if (/[¥￥]/.test(t)) continue;

      const dy = lines[j].y - lvLine.y;
      if (dy < 15 || dy > 130) continue;                  // 必须在下方一行的范围
      if (Math.abs(lines[j].x - lvLine.x) > 260) continue; // 同一列附近
      if (dy < bestDy) { bestDy = dy; best = j; }
    }

    if (best == null) { unmatched.push(`Lv${level} ?`); continue; }
    used.add(best);
    const name = lines[best].text;
    const slot = matchSlot(name);
    if (slot) {
      if (!(slot in items) || level > items[slot]) items[slot] = level;
      detail.push({ slot, level, name });
    } else {
      unmatched.push(`Lv${level} ${name}`);
    }
  }
  return { items, detail, unmatched };
}

/**
 * 词级解析 —— 比行级稳。
 *
 * 为什么不用 `data.lines`：游戏商店里四件道具的「等级」在同一条基线上，
 * Tesseract 会把 4 列**合并成一行**（实测 "等级7 8/8 等级8 /8 等级4 5/8…"），
 * 行级解析只能配到第一件。词级有各自的 x，可以按「离哪个等级最近」分列。
 *
 * 匹配是**保守**的：认不准就返回 null 进 unmatched，宁可少认也不错认 ——
 * 道具等级错了会直接影响编组算分。
 *
 * @param {{text:string,x:number,y:number,w:number,h:number}[]} words
 */
export function parseOcrWords(words) {
  const items = {};
  const detail = [];
  const unmatched = [];
  const anchors = [];      // 每个「等级N」的位置，图标匹配用
  if (!words?.length) return { items, detail, unmatched, anchors };

  // 1. 按 y 聚成行
  const sorted = [...words].sort((a, b) => a.y - b.y);
  const rows = [];
  for (const w of sorted) {
    const row = rows[rows.length - 1];
    if (row && Math.abs(w.y - row[0].y) <= 26) row.push(w);
    else rows.push([w]);
  }
  rows.forEach((r) => r.sort((a, b) => a.x - b.x));

  const usedNameRows = new Set();

  rows.forEach((row, ri) => {
    // 2. 找本行的「等级N」
    const levels = [];
    for (let i = 0; i < row.length; i++) {
      const t = row[i].text;
      if (!t.startsWith('等')) continue;
      let j = i;
      if (t === '等' && row[i + 1]?.text.startsWith('级')) j = i + 1;
      let k = j + 1;
      while (k < row.length && !/^[1-8]$/.test(row[k].text)) {
        if (row[k].x - row[j].x > 130) break;
        k++;
      }
      if (k < row.length && /^[1-8]$/.test(row[k].text)) {
        levels.push({ level: Number(row[k].text), x: row[k].x });
        i = k;
      }
    }
    if (!levels.length) return;
    for (const L of levels) anchors.push({ x: L.x, y: row[0].y, level: L.level });

    // 3. 名字：不按「行」取，直接按几何窗口取 —— 同一行里各字的 y 会抖 2~3px，
    //    按行聚合容易把最后一个字切到下一行（实测「米歇尔的铜」少了「像」）。
    //    窗口 = 等级下方 40~170px、水平 ±420px。
    for (const L of levels) {
      const ws = sorted.filter((w) => {
        if (!/[\u4e00-\u9fff]/.test(w.text)) return false;
        const dy = w.y - row[0].y;
        if (dy < 40 || dy > 170) return false;
        // 名字在卡片里居中，而「等级」贴在卡片左缘 —— 所以窗口要**偏右**，
        // 左边只留 20px（留多了会把上一张卡名字的尾巴吃进来，实测「着足浴池」）
        const c = w.x + w.w / 2;
        const dx = c - L.x;
        return dx > -120 && dx < 560;
      }).sort((a, b) => a.x - b.x);
      const name = ws.map((w) => w.text).join('');
      const slot = matchSlotLoose(name);
      if (slot) {
        if (!(slot in items) || L.level > items[slot]) items[slot] = L.level;
        detail.push({ slot, level: L.level, name });
      } else {
        unmatched.push(`Lv${L.level} ${name || '?'}`);
      }
    }
  });

  return { items, detail, unmatched, anchors };
}

/** 最长公共子序列长度占比，用来容忍 OCR 的错字（"椿子树" vs "椰子树"） */
function sim(a, b) {
  if (!a || !b) return 0;
  const m = a.length; const n = b.length;
  let prev = new Array(n + 1).fill(0);
  for (let i = 1; i <= m; i++) {
    const cur = new Array(n + 1).fill(0);
    for (let j = 1; j <= n; j++) {
      cur[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
    }
    prev = cur;
  }
  return prev[n] / Math.max(m, n);
}

/** 道具名里出现「吉」「贝」这种被截断的乐器名时，用字头判定 */
const KIND_CHARS = ['吉', '贝', '鼓', '键', '麦', '话'];
const KIND_WORDS = ['吉他', '贝斯', '爵士鼓', '键盘', '麦克风', '话筒', '海报', '宣传单', '传单'];

/** 角色名表（来自 team.json，懒加载） */
let CHAR_NAMES = null;
function charNames() {
  if (CHAR_NAMES) return CHAR_NAMES;
  const meta = window.__BDH_STATE?.meta || {};
  const chs = meta.characters || {};
  const bands = meta.bands || {};
  CHAR_NAMES = Object.values(chs)
    .map((c) => ({
      n: (c.n || '').replace(/\s/g, ''),
      b: bands[String(c.b)]?.n || '',
    }))
    .filter((x) => x.n && x.b);
  return CHAR_NAMES;
}

/** 官方道具名表（74 条，items.json），由 items-ui 注入 */
let ITEM_NAMES = null;
export function setItemNames(list) { ITEM_NAMES = list; }

/**
 * 官方名单模糊匹配。
 *
 * OCR 出来的名字常有 1 个字错（「椿子树」vs「椰子树」、「喷累」vs「喷泉」、
 * 「内酱意大利面」vs「肉酱意大利面」），关键词表一条都命中不了。
 * 拿 74 条官方名字算相似度，1 个字之差也能对上。
 */
function matchOfficial(name) {
  if (!ITEM_NAMES?.length || !name) return null;
  const n = name.replace(/[^\u4e00-\u9fffA-Za-z]/g, '');
  if (n.length < 2) return null;
  const scored = ITEM_NAMES
    .map((it) => ({ it, s: sim(n, it.n.replace(/\s/g, '')) }))
    .sort((a, b) => b.s - a.s);
  const best = scored[0];
  const second = scored[1];
  if (!best || best.s < 0.6) return null;
  if (second && best.s - second.s < 0.08) return null;   // 两个都像 → 不猜
  return best.it.s;
}

/** 保守版槽位匹配：认不准返回 null */
export function matchSlotLoose(name) {
  const n = (name || '').replace(/[^\u4e00-\u9fffA-Za-z]/g, '');
  if (!n) return null;
  if (n.includes('杂志')) return 'Magazine';
  if (n.startsWith('大家') || n.toLowerCase().includes('circle')) return 'Everyone';

  // 乐队名直接出现（海报 / 宣传单）
  const bands = ['PoppinParty', 'Afterglow', 'PastelPalettes', 'Roselia',
    'HelloHappyWorld', 'Morfonica', 'RaiseASuilen', 'MyGO', 'AveMujica'];
  const flat = n.toLowerCase();
  for (const b of bands) {
    const key = b.toLowerCase().replace(/[^a-z]/g, '');
    if (flat.replace(/[^a-z]/g, '').includes(key.slice(0, 6))) return b;
  }

  // 乐器：<角色>的<乐器>
  const isInstrument = KIND_WORDS.some((k) => n.includes(k)) ||
    (n.includes('的') && KIND_CHARS.includes(n.split('的')[1]?.[0]));
  if (isInstrument) {
    const head = n.includes('的') ? n.split('的')[0] : n;
    let best = null; let bs = 0;
    for (const c of charNames()) {
      const s2 = sim(head, c.n);
      if (s2 > bs) { bs = s2; best = c; }
    }
    if (best && bs >= 0.6) return best.b;
    return null;
  }

  // 广场 / 菜单：只认高相似度
  const plaza = ['椰子树', '足浴池', '米歇尔的铜像', '喷泉', '盆栽套装', '长椅', '路灯', '花坛', '秋千'];
  const menu = ['肉酱意大利面', '紫薯冰霜', '马卡龙', '水果挞', '巧克力海螺包', '极上咖啡',
    '咖啡', '蛋糕', '芭菲', '松饼', '布丁', '曲奇', '沙拉', '寿司', '饭团'];
  const pick = (list, slot) => {
    for (const k of list) if (n.includes(k) || sim(n, k) >= 0.7) return slot;
    return null;
  };
  return pick(plaza, 'Plaza') || pick(menu, 'Menu') || matchOfficial(n);
}

/** 兼容旧接口：没有坐标时退回按文本行猜 */
export function parseOcrText(text) {
  return parseOcrLines((text || '').split(/\r?\n/).filter(Boolean)
    .map((t, i) => ({ text: t.trim(), x: 0, y: i * 40 })));
}

/**
 * 按列切条 OCR —— 第三路证据。
 *
 * 商店固定 4 列（2640 宽时列起点约 556/933/1310/1686），把每一列单独裁出来
 * 识别，四列的字就不会糊成一行。海报页（88/89）靠这一路能读出
 * 「Roselia 的 宣 传 单」「CiRCLE 的 海 报」这种整名。
 *
 * @returns {Promise<{level:number,name:string}[]>}
 */
export async function readItemColumns(canvas, onProgress) {
  const worker = await getWorker(onProgress);
  const W = canvas.width;
  const H = canvas.height;
  const k = W / 2640;
  const xs = [0.2106, 0.3534, 0.4962, 0.6390].map((r) => Math.round(W * r));
  const w0 = Math.round(380 * k);
  const y0 = Math.round(240 * k);
  const h0 = Math.round(860 * k);
  const out = [];
  for (let i = 0; i < xs.length; i++) {
    const c = document.createElement('canvas');
    const S = 3;
    c.width = w0 * S; c.height = h0 * S;
    const g = c.getContext('2d');
    g.imageSmoothingQuality = 'high';
    g.drawImage(canvas, xs[i], y0, w0, h0, 0, 0, c.width, c.height);
    let text = '';
    try {
      const { data } = await worker.recognize(c, { tessedit_pageseg_mode: '6' });
      text = data.text || '';
    } catch { /* 单列失败不影响其它 */ }
    // 逐行配对：等级行之后的第一条「像名字」的行
    const lines = text.split(/\n+/).map((x) => x.replace(/\s+/g, '').trim()).filter(Boolean);
    for (let j = 0; j < lines.length; j++) {
      const m = lines[j].match(/等级([1-8])/);
      if (!m) continue;
      const level = Number(m[1]);
      for (let q = j + 1; q < Math.min(j + 4, lines.length); q++) {
        const t = lines[q];
        if (/等级|提升|售完|购买|剩余|完成|^\d|^[¥￥@®]/.test(t)) continue;
        if (t.length < 2) continue;
        out.push({ level, name: t });
        break;
      }
    }
    if (onProgress) onProgress((i + 1) / xs.length);
  }
  return out;
}

/** 把 File/Blob/dataURL 画到 2 倍画布上（Tesseract 对小字更友好） *//**
 * 逐张卡读道具名 —— 这是识别率的关键。
 *
 * 整图 OCR 会把四列名字糊成一段（「Roselia的宣传单」只剩「的宣传单」），
 * 但**按卡片裁出来单独识别**就非常准：实测 4 倍放大 + 单行模式，
 * 6 张里 5 张正确（「Roselia 的 宣 传 单」「CiRCLE 的 海 报」…）。
 *
 * 名称条位置是固定的：等级文字下方 22~92px、卡片宽约 350px（2640 宽截图）。
 *
 * @param {HTMLCanvasElement} canvas 与 words 同一坐标系的画布
 * @param {{x:number,y:number,level:number}[]} anchors
 * @returns {Promise<{x:number,y:number,level:number,name:string}[]>}
 */
export async function readItemNames(canvas, anchors, onProgress) {
  if (!anchors?.length) return [];
  const worker = await getWorker(onProgress);
  const k = canvas.width / 2640;      // 相对 2640 宽截图的缩放
  const out = [];
  for (let i = 0; i < anchors.length; i++) {
    const a = anchors[i];
    const name = await readOneName(canvas, worker, a, k);
    out.push({ ...a, name, lines: name ? [name] : [] });
    if (onProgress) onProgress((i + 1) / anchors.length);
  }
  return out;
}

/**
 * 读一张卡的道具名。
 *
 * 卡片有两种版式（紧凑卡名字在等级下方 ~30px，大卡在 ~70px），
 * 所以不猜偏移：先在卡片区域里**按行找那条深色名称条**（白字深底，
 * 一整行 55% 以上是暗像素），再把这条单独裁出来 4 倍放大识别。
 */
async function readOneName(canvas, worker, anchor, k) {
  const rx = Math.max(0, Math.round((anchor.x / k) - 15));
  const ry = Math.max(0, Math.round((anchor.y / k) - 40));
  const rw = Math.round(350);
  const rh = Math.round(230);
  if (rx + rw > canvas.width / k || ry + rh > canvas.height / k) return '';

  // 1x 小图，只用来找名称条的行范围
  const tmp = document.createElement('canvas');
  tmp.width = rw; tmp.height = rh;
  const g = tmp.getContext('2d', { willReadFrequently: true });
  g.drawImage(canvas, rx * k, ry * k, rw * k, rh * k, 0, 0, rw, rh);
  const d = g.getImageData(0, 0, rw, rh).data;
  const dark = new Uint8Array(rh);
  const wide = new Uint16Array(rh);   // 该行暗像素的横向跨度
  for (let y = 0; y < rh; y++) {
    let n = 0; let x0 = -1; let x1 = -1;
    for (let x = 0; x < rw; x++) {
      const o = (y * rw + x) * 4;
      if (0.299 * d[o] + 0.587 * d[o + 1] + 0.114 * d[o + 2] < 120) {
        n++; if (x0 < 0) x0 = x; x1 = x;
      }
    }
    if (n / rw > 0.5) { dark[y] = 1; wide[y] = x1 - x0; }
  }
  // 名称条横跨整张卡（≥250px）；「等级」那个小药丸只有 ~200px，靠宽度区分
  let band = null;
  for (let y = 0; y < rh; y++) {
    if (!dark[y]) continue;
    let y2 = y; let wMax = 0;
    while (y2 < rh && dark[y2]) { wMax = Math.max(wMax, wide[y2]); y2++; }
    const h = y2 - y;
    if (h >= 22 && h <= 80 && wMax >= 250) { band = [y, y2]; break; }
    y = y2;
  }
  if (!band) return '';

  // 从原画布按 4 倍裁出来
  const c = document.createElement('canvas');
  const S = 2;
  c.width = rw * S;
  c.height = (band[1] - band[0] + 8) * S;
  const cg = c.getContext('2d');
  cg.imageSmoothingQuality = 'high';
  cg.drawImage(canvas,
    (rx) * k, (ry + band[0] - 4) * k, rw * k, (band[1] - band[0] + 8) * k,
    0, 0, c.width, c.height);

  try {
    const { data } = await worker.recognize(c, { tessedit_pageseg_mode: '7' });
    return (data.text || '')
      .replace(/[|｜…·.,，、:：;；'"“”]/g, ' ')
      .replace(/\s+/g, '')
      .trim();
  } catch {
    return '';
  }
}

/** 把 File/Blob/dataURL 画到 2 倍画布上（Tesseract 对小字更友好） *//** 把 File/Blob/dataURL 画到 2 倍画布上（Tesseract 对小字更友好） */
async function toCanvas2x(image) {
  let src = image;
  if (typeof image === 'string') {
    const img = new Image();
    img.src = image;
    await img.decode();
    src = img;
  } else if (typeof Blob !== 'undefined' && image instanceof Blob) {
    // File/Blob 不能直接 drawImage（会报 "not of type HTMLImageElement"）
    src = await createImageBitmap(image);
  }
  const w = src.width || src.naturalWidth;
  const h = src.height || src.naturalHeight;
  const c = document.createElement('canvas');
  c.width = Math.round(w * 2);
  c.height = Math.round(h * 2);
  const g = c.getContext('2d');
  g.imageSmoothingQuality = 'high';
  g.drawImage(src, 0, 0, c.width, c.height);
  return c;
}

/** 对一张图片跑 OCR。
 *
 * @param {File|Blob|string} image 图片文件或 dataURL
 * @param {(p:number)=>void} [onProgress] 0~1
 */
/**
 * 后台预热：进「道具」页时就调用，把脚本和中文模型先载好。
 * 用户真正点「识别」时就不用等那 5~10 秒的下载。
 *
 * @param {(state:string)=>void} [onState] 'loading' | 'ready' | 'failed'
 */
export function prewarm(onState) {
  if (warmState === 'ready') { if (onState) onState('ready'); return Promise.resolve(true); }
  if (workerPromise) { if (onState) onState('loading'); return workerPromise.then(() => true).catch(() => false); }
  warmState = 'loading';
  if (onState) onState('loading');
  workerPromise = (async () => {
    await loadScript();
    return Tesseract.createWorker('chi_sim+eng', 1, OCR_OPTS);
  })();
  return workerPromise
    .then((w) => { warmState = 'ready'; if (onState) onState('ready'); return w; })
    .catch((e) => { warmState = 'failed'; workerPromise = null; if (onState) onState('failed'); throw e; });
}

export function warmStatus() { return warmState; }

async function getWorker(onProgress) {
  if (workerPromise) {
    const w = await workerPromise;
    // 复用时要重新挂 logger（进度回调是每次识别不同的闭包）
    if (onProgress) {
      w._logger = onProgress;
    }
    return w;
  }
  await loadScript();
  workerPromise = Tesseract.createWorker('chi_sim+eng', 1, {
    ...OCR_OPTS,
    logger: (m) => {
      const cb = workerPromise && workerPromise._cb;
      if (m.status === 'recognizing text' && cb) cb(m.progress || 0);
    },
  });
  return workerPromise;
}

export async function recognizeImage(image, onProgress) {
  const worker = await getWorker(onProgress);
  // 把本次的进度回调挂到 worker 上（Tesseract 的 logger 在创建时固定，
  // 复用 worker 时只能这样把进度转出来）
  if (workerPromise) workerPromise._cb = onProgress;
  try {
    // 放大 2 倍再识别：商店截图是 2640×1200，正文只有 ~20px，
    // 1x 下 Tesseract 会把 4 列的名字糊成一段（实测 "ek ee ee Th en en"）。
    const canvas = await toCanvas2x(image);
    const { data } = await worker.recognize(canvas, {
      tessedit_pageseg_mode: '6',       // 单块文本：保留列间大间距
      preserve_interword_spaces: '1',
    });
    const toBox = (o) => ({
      text: (o.text || '').trim(),
      x: o.bbox?.x0 ?? 0,
      y: o.bbox?.y0 ?? 0,
      w: (o.bbox?.x1 ?? 0) - (o.bbox?.x0 ?? 0),
      h: (o.bbox?.y1 ?? 0) - (o.bbox?.y0 ?? 0),
    });
    return {
      lines: (data.lines || []).map(toBox).filter((l) => l.text),
      words: (data.words || []).map(toBox).filter((w) => w.text),
      scale: 2,
      canvas,          // 供图标模板匹配用（与 words 同一套坐标）
    };
  } finally {
    // **不要 terminate**：worker 要复用（语言包重载很贵）。
    // 只有页面卸载时才该销毁，这里不处理。
  }
}
