/**
 * 浏览器端 OCR —— 读游戏「商店 / 道具」截图里的道具等级。
 *
 * ## 为什么是 tesseract.js
 *
 * GitHub Pages 是纯静态站，跑不了 Python。要在**打开网页就能用**的前提下做
 * 识别，只能把 OCR 放进浏览器。`tesseract.js` 是 wasm 版的 Tesseract：
 *
 * - **免安装、免后端**，一个 `<script>` 即可
 * - 中文模型按需加载（约 10 MB），**点按钮才下载**，不拖慢首屏
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

const CDN_LIST = [
  'https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js',
  'https://unpkg.com/tesseract.js@5/dist/tesseract.min.js',
  'https://cdnjs.cloudflare.com/ajax/libs/tesseract.js/5.1.0/tesseract.min.js',
];

let loading = null;
/** 复用的 worker（创建一次，别每次识别都新建——语言包要重新载入） */
let workerPromise = null;
let warmState = 'idle';    // idle | loading | ready | failed

/** 按顺序尝试多个 CDN，哪个通用哪个 */
function loadScript() {
  if (window.Tesseract) return Promise.resolve();
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    let i = 0;
    const tryNext = () => {
      if (i >= CDN_LIST.length) { reject(new Error('OCR 脚本加载失败（CDN 全不通）')); return; }
      const s = document.createElement('script');
      s.src = CDN_LIST[i++];
      s.onload = () => resolve();
      s.onerror = tryNext;
      document.head.appendChild(s);
    };
    tryNext();
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

/** 兼容旧接口：没有坐标时退回按文本行猜 */
export function parseOcrText(text) {
  return parseOcrLines((text || '').split(/\r?\n/).filter(Boolean)
    .map((t, i) => ({ text: t.trim(), x: 0, y: i * 40 })));
}

/**
 * 对一张图片跑 OCR。
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
    return Tesseract.createWorker('chi_sim+eng', 1, { cachePath: 'bdh-ocr' });
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
    cachePath: 'bdh-ocr',
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
    const { data } = await worker.recognize(image);
    // **必须带坐标**：Tesseract 的纯文本是按识别顺序排的，同一行的「等级」
    // 会全挤在一起、道具名又全在另一段，靠文本相邻根本配不上对。
    return (data.lines || []).map((l) => ({
      text: (l.text || '').trim(),
      x: l.bbox?.x0 ?? 0,
      y: l.bbox?.y0 ?? 0,
      w: (l.bbox?.x1 ?? 0) - (l.bbox?.x0 ?? 0),
      h: (l.bbox?.y1 ?? 0) - (l.bbox?.y0 ?? 0),
    })).filter((l) => l.text);
  } finally {
    // **不要 terminate**：worker 要复用（语言包重载很贵）。
    // 只有页面卸载时才该销毁，这里不处理。
  }
}
