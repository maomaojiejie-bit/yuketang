// 控制台前端行为测试：设置面板 + 背景 + 设置读写（用 DOM 桩，不依赖浏览器）
const fs = require('fs');
const vm = require('vm');
const consoleSrc = fs.readFileSync(process.argv[2], 'utf8');

class El {
  constructor(id) {
    this.id = id; this.style = {}; this.value = ''; this.checked = false;
    this.textContent = ''; this.className = ''; this.children = []; this.files = [];
    this.hidden = false; this._attrs = {}; this._ev = {};
    this.classList = {
      _s: new Set(),
      add: (c) => this.classList._s.add(c),
      remove: (c) => this.classList._s.delete(c),
      toggle: (c, on) => { if (on) this.classList._s.add(c); else this.classList._s.delete(c); },
      contains: (c) => this.classList._s.has(c),
    };
  }
  addEventListener(t, fn) { this._ev[t] = fn; }
  fire(t, event) { if (this._ev[t]) this._ev[t](event || { target: this, stopPropagation() {}, key: '' }); }
  setAttribute(k, v) { this._attrs[k] = v; }
  getAttribute(k) { return this._attrs[k] === undefined ? null : this._attrs[k]; }
  appendChild(c) { this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); }
  querySelectorAll() { return []; }
  contains(node) { return node === this; }
}

const store = new Map();
const localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

const SERVER_SETTINGS = {
  answer: { mode: 'dom', dry_run: false, auto_submit: true, min_confidence: 0.25, click_delay: 1.2 },
  solver: { dom_fallback: true, poll_interval: 3, auto_enter_lesson: true, enter_lesson_interval: 30 },
  browser: { window_width: 1024, window_height: 768 },
};
let fetchCalls = [];

function makeSandbox(els, dataBgNodes) {
  const documentElement = new El('html');
  const docListeners = {};
  const document = {
    documentElement, body: { scrollHeight: 1000 },
    getElementById: (id) => (els[id] = els[id] || new El(id)),
    createElement: (tag) => new El(tag),
    querySelectorAll: (sel) => (sel === '[data-bg]' ? dataBgNodes : []),
    addEventListener: (t, fn) => { docListeners[t] = fn; },
  };
  const sandbox = {
    document, localStorage, console,
    window: { scrollY: 0, innerHeight: 900, scrollTo: () => {} },
    fetch: async (url, options) => {
      fetchCalls.push({ url, options });
      if (url === '/api/state') {
        return { json: async () => ({
          watching: false, browser: { attached: false },
          answer: { mode: 'dom', dry_run: false }, solver: { auto_enter_lesson: true },
          stats: null
        }) };
      }
      if (url === '/api/settings') {
        if (options && options.method === 'POST') {
          const sent = JSON.parse(options.body);
          if (sent.solver.poll_interval > 120) sent.solver.poll_interval = 120;  // 模拟服务端夹取
          return { json: async () => ({ ok: true, values: sent }) };
        }
        return { json: async () => ({ ok: true, values: SERVER_SETTINGS }) };
      }
      return { json: async () => ({}) };
    },
    EventSource: function () { this.onmessage = null; this.onerror = null; },
    setInterval: () => 1, setTimeout: () => 1, clearTimeout: () => {}, confirm: () => true,
    FileReader: function () { this.readAsDataURL = () => {}; },
    Date, JSON, Math, Object, String, Number, Array, Set, RegExp, Promise,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  const epilogue = '\nglobalThis.__ui = { bgState, applyBg, cssUrl, bgImage, syncBgForm, BG_KEY,'
    + ' settingsState, readSettingsForm, fillSettingsForm, saveSettings, loadSettings,'
    + ' toggleSettingsPanel, updateRunHint };\n';
  new vm.Script(consoleSrc + epilogue).runInContext(sandbox);
  return { ui: sandbox.__ui, docListeners };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

let pass = 0, fail = 0;
function check(label, actual, expected) {
  if (JSON.stringify(actual) === JSON.stringify(expected)) { pass++; console.log('  OK   ' + label); }
  else { fail++; console.log('  FAIL ' + label + ' | 期望 ' + JSON.stringify(expected) + ' | 实际 ' + JSON.stringify(actual)); }
}

(async () => {
  const els = {};
  els['settings-panel'] = new El('settings-panel'); els['settings-panel'].hidden = true;
  const dataBgNodes = ['color', 'gradient', 'url', 'file'].map((k) => {
    const n = new El(k); n.setAttribute('data-bg', k); return n;
  });

  console.log('[1] 加载：设置从服务端回填');
  const ctx = makeSandbox(els, dataBgNodes);
  const ui = ctx.ui;
  await tick(); await tick(); await tick();
  check('模式回填', els['mode'].value, 'dom');
  check('自动进课堂回填', els['auto-enter-lesson'].checked, true);
  check('课堂检查间隔回填', els['enter-lesson-interval'].value, 30);
  check('窗口尺寸回填', [els['window-width'].value, els['window-height'].value], [1024, 768]);
  check('最低置信度百分比', els['min-confidence'].value, 25);
  check('运行提示包含自动进课堂', els['run-hint'].textContent.includes('自动进课堂'), true);
  check('运行提示包含窗口尺寸', els['run-hint'].textContent.includes('1024×768'), true);

  console.log('[2] 顶栏设置浮层开合');
  check('默认收起', els['settings-panel'].hidden, true);
  els['btn-settings-toggle'].fire('click');
  check('点按钮展开', els['settings-panel'].hidden, false);
  check('按钮高亮', els['btn-settings-toggle'].classList.contains('active'), true);
  els['btn-settings-close'].fire('click');
  check('✕ 收起', els['settings-panel'].hidden, true);
  els['btn-settings-open'].fire('click');
  check('侧栏「打开设置」也能展开', els['settings-panel'].hidden, false);
  ctx.docListeners['click']({ target: new El('outside') });
  check('点空白处收起', els['settings-panel'].hidden, true);
  els['btn-settings-toggle'].fire('click');
  ctx.docListeners['keydown']({ key: 'Escape' });
  check('Esc 收起', els['settings-panel'].hidden, true);

  console.log('[3] 改动设置后提交给服务端');
  fetchCalls = [];
  els['auto-enter-lesson'].checked = false;
  els['enter-lesson-interval'].value = '45';
  els['window-width'].value = '800';
  els['dryrun'].checked = true;
  ui.readSettingsForm();
  await ui.saveSettings({ quiet: true });
  const post = fetchCalls.filter((c) => c.options && c.options.method === 'POST').pop();
  const body = post ? JSON.parse(post.options.body) : {};
  check('POST 到 /api/settings', post && post.url, '/api/settings');
  check('提交自动进课堂=false', body.solver.auto_enter_lesson, false);
  check('提交检查间隔=45', body.solver.enter_lesson_interval, 45);
  check('提交窗口宽=800', body.browser.window_width, 800);
  check('提交预演=true', body.answer.dry_run, true);
  check('提交模式沿用 dom', body.answer.mode, 'dom');
  check('本地状态同步', [ui.settingsState.auto_enter_lesson, ui.settingsState.enter_lesson_interval], [false, 45]);

  console.log('[4] 背景功能仍然可用');
  els['bg-enabled'].checked = true;
  els['bg-kind'].value = 'gradient';
  els['bg-grad-a'].value = '#101820'; els['bg-grad-b'].value = '#3b1f4d'; els['bg-grad-dir'].value = '90deg';
  els['bg-kind'].fire('change');
  check('梯度语法', ui.bgImage(), 'linear-gradient(90deg, #101820, #3b1f4d)');
  check('背景已应用', els['bg-layer'].style.backgroundImage, 'linear-gradient(90deg, #101820, #3b1f4d)');
  els['bg-veil'].value = '50'; els['bg-veil'].fire('input');
  check('遮罩 opacity', els['bg-veil'].style.opacity, '0.5');
  check('cssUrl 转义', ui.cssUrl('a"b)c'), 'a%22b%29c');
  check('背景设置已落 localStorage', JSON.parse(store.get('cjsolver.background.v1')).enabled, true);

  console.log('[5] 顶栏「自动进课堂」指示');
  await tick(); await tick();
  check('状态显示为开', els['txt-lesson'].textContent, '开');
  check('指示灯高亮', els['dot-lesson'].className, 'dot ok');

  console.log('[6] 越界数值被服务端夹取后回写输入框');
  els['poll-interval'].value = '999';
  ui.readSettingsForm();
  await ui.saveSettings({ quiet: true });
  check('输入框回写为 120', els['poll-interval'].value, 120);
  check('本地状态同步为 120', ui.settingsState.poll_interval, 120);

  console.log('\n通过 ' + pass + ' 项，失败 ' + fail + ' 项');
  process.exit(fail ? 1 : 0);
})();