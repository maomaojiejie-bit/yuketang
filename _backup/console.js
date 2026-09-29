
const $ = (id) => document.getElementById(id);
const stream = $('stream');
const empty = $('stream-empty');
const seenSeq = new Set();

// ---------------------------------------------------------------- 工具
function esc(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[c]);
}
function time(ts) {
  return new Date((ts || Date.now() / 1000) * 1000).toLocaleTimeString('zh-CN', { hour12: false });
}
function atBottom() {
  return window.scrollY + window.innerHeight > document.body.scrollHeight - 120;
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  };
  const response = await fetch(path, options);
  try { return await response.json(); }
  catch { return { ok: false, error: `HTTP ${response.status}` }; }
}

function push(node) {
  if (empty) empty.style.display = 'none';
  const stick = atBottom();
  stream.appendChild(node);
  while (stream.children.length > 400) stream.removeChild(stream.firstChild);
  if (stick) window.scrollTo(0, document.body.scrollHeight);
}

function toast(message, kind = 'error') {
  const div = document.createElement('div');
  div.className = 'logline ' + kind;
  div.textContent = `[${new Date().toLocaleTimeString('zh-CN', { hour12: false })}] ${message}`;
  push(div);
}

// ---------------------------------------------------------------- 自定义背景
const BG_KEY = 'cjsolver.background.v1';
const BG_DEFAULT = {
  enabled: false, kind: 'color',
  color: '#0b1020',
  gradA: '#0b1020', gradB: '#14304a', gradDir: '160deg',
  url: '', file: '', fileName: '',
  veil: 0, blur: 0
};
const BG_KINDS = ['color', 'gradient', 'url', 'file'];
const bgState = Object.assign({}, BG_DEFAULT, loadBg());

function loadBg() {
  try {
    const merged = Object.assign({}, BG_DEFAULT, JSON.parse(localStorage.getItem(BG_KEY) || '{}'));
    if (!BG_KINDS.includes(merged.kind)) merged.kind = 'color';
    merged.veil = Number(merged.veil) || 0;
    merged.blur = Number(merged.blur) || 0;
    return merged;
  } catch (error) {
    return {};
  }
}

function saveBg() {
  try {
    localStorage.setItem(BG_KEY, JSON.stringify(bgState));
  } catch (error) {
    toast('背景设置没能存到本地（多半是图片太大），刷新后会回到上一次的设置');
  }
}

// 拼进 CSS url() 之前先转义，避免引号/括号把 background-image 写坏
function cssUrl(value) {
  return String(value || '').replace(/["'\\\n\r()]/g, (c) => ({
    '"': '%22', "'": '%27', '\\': '%5C', '(': '%28', ')': '%29', '\n': '', '\r': ''
  })[c]);
}

function bgImage() {
  if (bgState.kind === 'gradient') {
    return `linear-gradient(${bgState.gradDir || '160deg'}, ${bgState.gradA}, ${bgState.gradB})`;
  }
  if (bgState.kind === 'url' && bgState.url) return `url("${cssUrl(bgState.url)}")`;
  if (bgState.kind === 'file' && bgState.file) return `url("${cssUrl(bgState.file)}")`;
  return 'none';
}

function applyBg() {
  const layer = $('bg-layer');
  const veil = $('bg-veil');
  const on = !!bgState.enabled;
  document.documentElement.classList.toggle('custom-bg', on);
  if (!on) {
    layer.style.backgroundImage = 'none';
    layer.style.backgroundColor = 'var(--bg)';
    layer.style.filter = 'none';
    layer.style.transform = 'none';
    veil.style.opacity = '0';
    return;
  }
  if (bgState.kind === 'color') {
    layer.style.backgroundImage = 'none';
    layer.style.backgroundColor = bgState.color || '#0b1020';
  } else {
    layer.style.backgroundColor = '#070b16';
    layer.style.backgroundImage = bgImage();
  }
  const blur = Number(bgState.blur) || 0;
  layer.style.filter = blur ? `blur(${blur}px)` : 'none';
  // 模糊会让固定层的边缘发虚，稍微放大一点盖住
  layer.style.transform = blur ? 'scale(1.06)' : 'none';
  veil.style.opacity = String((Number(bgState.veil) || 0) / 100);
}

function refreshBgLabels() {
  $('bg-veil-value').textContent = `${Number(bgState.veil) || 0}%`;
  $('bg-blur-value').textContent = `${Number(bgState.blur) || 0}px`;
  $('bg-file-hint').textContent = bgState.file
    ? `已选：${bgState.fileName || '本地图片'}（换设备后需要重新选择）`
    : '图片以 Base64 存在浏览器本地，建议小于 2 MB。';
  document.querySelectorAll('[data-bg]').forEach((node) => {
    node.style.display = node.getAttribute('data-bg') === bgState.kind ? '' : 'none';
  });
}

// 把已保存的状态回填到表单（只在加载和恢复默认时调用，避免打断正在输入的地址）
function syncBgForm() {
  $('bg-enabled').checked = !!bgState.enabled;
  $('bg-kind').value = bgState.kind;
  $('bg-color').value = bgState.color;
  $('bg-grad-a').value = bgState.gradA;
  $('bg-grad-b').value = bgState.gradB;
  $('bg-grad-dir').value = bgState.gradDir;
  $('bg-url').value = bgState.url;
  $('bg-veil').value = bgState.veil;
  $('bg-blur').value = bgState.blur;
  refreshBgLabels();
}

function readBgForm() {
  bgState.enabled = $('bg-enabled').checked;
  bgState.kind = $('bg-kind').value;
  bgState.color = $('bg-color').value;
  bgState.gradA = $('bg-grad-a').value;
  bgState.gradB = $('bg-grad-b').value;
  bgState.gradDir = $('bg-grad-dir').value;
  bgState.url = $('bg-url').value.trim();
  bgState.veil = Number($('bg-veil').value) || 0;
  bgState.blur = Number($('bg-blur').value) || 0;
}

function onBgInput() {
  readBgForm();
  refreshBgLabels();
  applyBg();
  saveBg();
}

for (const id of ['bg-enabled', 'bg-kind', 'bg-color', 'bg-grad-a', 'bg-grad-b',
                  'bg-grad-dir', 'bg-url', 'bg-veil', 'bg-blur']) {
  $(id).addEventListener('input', onBgInput);
  $(id).addEventListener('change', onBgInput);
}

$('bg-file').addEventListener('change', (event) => {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  if (file.size > 4 * 1024 * 1024) {
    toast('图片超过 4 MB，浏览器本地存储放不下，换一张小一点的吧');
    event.target.value = '';
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    bgState.file = String(reader.result || '');
    bgState.fileName = file.name;
    readBgForm();
    refreshBgLabels();
    applyBg();
    saveBg();
  };
  reader.onerror = () => toast('读取本地图片失败');
  reader.readAsDataURL(file);
});

$('btn-bg-reset').addEventListener('click', () => {
  Object.assign(bgState, BG_DEFAULT);
  try { localStorage.removeItem(BG_KEY); } catch (error) { /* 忽略 */ }
  syncBgForm();
  applyBg();
  toast('已恢复默认背景', 'info');
});

// 顶栏设置浮层的开合
function toggleSettingsPanel(force) {
  const panel = $('settings-panel');
  const open = force === undefined ? panel.hidden : !!force;
  panel.hidden = !open;
  $('btn-settings-toggle').classList.toggle('active', open);
}

$('btn-settings-toggle').addEventListener('click', (event) => {
  event.stopPropagation();
  toggleSettingsPanel();
});
$('btn-settings-open').addEventListener('click', () => toggleSettingsPanel(true));
$('btn-settings-close').addEventListener('click', () => toggleSettingsPanel(false));
document.addEventListener('click', (event) => {
  const panel = $('settings-panel');
  if (panel.hidden) return;
  if (panel.contains(event.target) || $('btn-settings-toggle').contains(event.target)) return;
  toggleSettingsPanel(false);
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') toggleSettingsPanel(false);
});

// ------------------------------------------------ 控制台设置（含自动进课堂）
// 面板里所有可调项，改动后防抖保存到服务端（console-settings.json）
const SETTINGS_INPUTS = [
  'mode', 'dryrun', 'auto-submit', 'min-confidence', 'click-delay',
  'dom-fallback', 'poll-interval', 'auto-enter-lesson', 'enter-lesson-interval',
  'window-width', 'window-height'
];

const settingsState = {
  mode: 'manual', dry_run: false, auto_submit: true, min_confidence: 0, click_delay: 0.8,
  dom_fallback: true, poll_interval: 2, auto_enter_lesson: false, enter_lesson_interval: 20,
  window_width: 800, window_height: 600
};

const MODE_TEXT = {
  manual: 'manual 只提示',
  dom: 'dom 自动作答',
  api: 'api 直接调接口'
};

function numberOr(id, fallback) {
  const value = Number($(id).value);
  return Number.isFinite(value) ? value : fallback;
}

function readSettingsForm() {
  settingsState.mode = $('mode').value;
  settingsState.dry_run = $('dryrun').checked;
  settingsState.auto_submit = $('auto-submit').checked;
  settingsState.min_confidence = numberOr('min-confidence', 0) / 100;
  settingsState.click_delay = numberOr('click-delay', 0.8);
  settingsState.dom_fallback = $('dom-fallback').checked;
  settingsState.poll_interval = numberOr('poll-interval', 2);
  settingsState.auto_enter_lesson = $('auto-enter-lesson').checked;
  settingsState.enter_lesson_interval = numberOr('enter-lesson-interval', 20);
  settingsState.window_width = numberOr('window-width', 800);
  settingsState.window_height = numberOr('window-height', 600);
  $('min-confidence-value').textContent = $('min-confidence').value + '%';
  return settingsState;
}

function updateRunHint() {
  const bits = [MODE_TEXT[settingsState.mode] || settingsState.mode];
  if (settingsState.dry_run) bits.push('预演');
  if (settingsState.auto_enter_lesson) bits.push('自动进课堂');
  if (settingsState.window_width > 0 && settingsState.window_height > 0) {
    bits.push(`窗口 ${settingsState.window_width}×${settingsState.window_height}`);
  }
  $('run-hint').textContent = `当前设置：${bits.join(' · ')}`;
}

function fillSettingsForm(values) {
  const answer = values.answer || {};
  const solver = values.solver || {};
  const browser = values.browser || {};

  $('mode').value = answer.mode || 'manual';
  $('dryrun').checked = !!answer.dry_run;
  $('auto-submit').checked = answer.auto_submit !== false;
  $('min-confidence').value = Math.round((answer.min_confidence || 0) * 100);
  $('click-delay').value = answer.click_delay ?? 0.8;
  $('dom-fallback').checked = solver.dom_fallback !== false;
  $('poll-interval').value = solver.poll_interval ?? 2;
  $('auto-enter-lesson').checked = !!solver.auto_enter_lesson;
  $('enter-lesson-interval').value = solver.enter_lesson_interval ?? 20;
  $('window-width').value = browser.window_width ?? 800;
  $('window-height').value = browser.window_height ?? 600;
  readSettingsForm();
  updateRunHint();
}

function settingsPayload() {
  return {
    answer: {
      mode: settingsState.mode,
      dry_run: settingsState.dry_run,
      auto_submit: settingsState.auto_submit,
      min_confidence: settingsState.min_confidence,
      click_delay: settingsState.click_delay
    },
    solver: {
      dom_fallback: settingsState.dom_fallback,
      poll_interval: settingsState.poll_interval,
      auto_enter_lesson: settingsState.auto_enter_lesson,
      enter_lesson_interval: settingsState.enter_lesson_interval
    },
    browser: {
      window_width: settingsState.window_width,
      window_height: settingsState.window_height
    }
  };
}

// 服务端会把越界数字夹到合法区间，把最终值写回输入框（正在编辑的除外）
function setIfIdle(id, value) {
  const el = $(id);
  if (!el || document.activeElement === el) return;
  if (value === undefined || value === null) return;
  el.value = value;
}

function writeBackSettings(values) {
  const answer = values.answer || {};
  const solver = values.solver || {};
  const browser = values.browser || {};
  setIfIdle('enter-lesson-interval', solver.enter_lesson_interval);
  setIfIdle('poll-interval', solver.poll_interval);
  setIfIdle('click-delay', answer.click_delay);
  setIfIdle('min-confidence', Math.round((answer.min_confidence || 0) * 100));
  setIfIdle('window-width', browser.window_width);
  setIfIdle('window-height', browser.window_height);
}

async function saveSettings({ quiet = false } = {}) {
  readSettingsForm();
  updateRunHint();
  const result = await api('/api/settings', settingsPayload());
  if (!result.ok) {
    toast(result.error || '设置保存失败');
    await loadSettings();
    return null;
  }
  if (result.values) {
    writeBackSettings(result.values);
    readSettingsForm();
    updateRunHint();
  }
  if (!quiet) toast('设置已保存', 'info');
  return result;
}

let settingsTimer = null;
function scheduleSettingsSave() {
  readSettingsForm();
  updateRunHint();
  if (settingsTimer) clearTimeout(settingsTimer);
  // 拖滑杆时不必每格都发请求
  settingsTimer = setTimeout(() => saveSettings({ quiet: true }), 400);
}

async function loadSettings() {
  const result = await api('/api/settings');
  if (!result.ok) {
    toast(result.error || '读取设置失败');
    return;
  }
  fillSettingsForm(result.values || {});
}

for (const id of SETTINGS_INPUTS) {
  $(id).addEventListener('input', scheduleSettingsSave);
  $(id).addEventListener('change', scheduleSettingsSave);
}

// ---------------------------------------------------------------- 状态
async function refresh() {
  const state = await api('/api/state');
  if (state.error) return;

  const browser = state.browser || {};
  $('txt-browser').textContent = browser.attached ? '已接管' : '未接管';
  $('dot-browser').className = 'dot ' + (browser.attached ? 'ok' : 'off');

  const model = state.model || {};
  $('txt-model').textContent = model.configured ? model.name : '未配置 Key';
  $('dot-model').className = 'dot ' + (model.configured ? 'ok' : 'warn');

  $('txt-watch').textContent = state.watching ? state.answer.mode : '未启动';
  $('dot-watch').className = 'dot ' + (state.watching ? 'ok' : 'off');

  const solver = state.solver || {};
  const autoLesson = !!solver.auto_enter_lesson;
  $('txt-lesson').textContent = autoLesson ? '开' : '关';
  $('dot-lesson').className = 'dot ' + (autoLesson ? 'ok' : 'off');

  const stats = state.stats || { problems: 0, answered: 0, skipped: 0, failed: 0 };
  $('s-problems').textContent = stats.problems ?? 0;
  $('s-answered').textContent = stats.answered ?? 0;
  $('s-skipped').textContent = stats.skipped ?? 0;
  $('s-failed').textContent = stats.failed ?? 0;
  $('txt-stats').textContent = `${stats.problems ?? 0} / ${stats.answered ?? 0}`;

  $('btn-start').disabled = state.watching || state.busy;
  $('btn-stop').disabled = !state.watching;
}

// ---------------------------------------------------------------- 渲染
function problemCard(data) {
  const p = data.problem || {};
  const el = document.createElement('div');
  el.className = 'ev problem';
  const options = (p.options || []).map((o, i) =>
    `<div class="opt"><i>${esc(o.letter)}</i>${esc(o.text)}</div>`).join('');
  const blanks = (p.blanks || []).length
    ? `<div class="hint">空位：${esc((p.blanks || []).join(' / '))}</div>` : '';
  el.innerHTML = `
    <div class="hd"><span class="tag">第 ${data.index ?? '-'} 题</span>
      <span class="tag">${esc(p.type_label || p.type || '')}</span>
      <span class="tag">${esc(p.source || '')}</span>
      <span style="margin-left:auto">${time()}</span></div>
    <div class="bd">
      <div class="stem">${esc(p.prompt) || '<span style="color:var(--muted)">（题干在课件图片中）</span>'}</div>
      ${options}${blanks}
    </div>`;
  return el;
}

function answerCard(data) {
  const s = data.suggestion || {};
  const submit = data.submit || {};
  const failed = !data.ok;
  const el = document.createElement('div');
  el.className = 'ev answer' + (failed ? ' fail' : '');

  const confidence = (s.confidence === null || s.confidence === undefined)
    ? '—' : Math.round(s.confidence * 100) + '%';
  const status = submit.mode
    ? `<span class="tag">${esc(submit.detail || submit.mode)}</span>` : '';

  el.innerHTML = `
    <div class="hd"><span class="tag">${failed ? '未能作答' : 'AI 答案'}</span>${status}
      <span style="margin-left:auto">${time()}</span></div>
    <div class="bd">
      ${failed
        ? `<div class="answer-line" style="color:var(--warn)">${esc(s.failure_reason || data.reason || '无可用答案')}</div>`
        : `<div class="answer-line">${esc(s.display || '')}</div>`}
      ${s.explanation ? `<div class="explain">${esc(s.explanation)}</div>` : ''}
      <div class="meta">
        <span>置信度 ${confidence}</span>
        <span>模型 ${esc(s.model || '—')}</span>
        <span>耗时 ${esc(s.elapsed ?? '—')}s</span>
      </div>
    </div>`;
  return el;
}

function simulationCard(report) {
  const el = document.createElement('div');
  el.className = 'ev sim';
  const rows = (report.checks || []).map((check) => {
    const mark = check.skipped ? '○' : (check.ok ? '✓' : '✗');
    const cls = check.skipped ? 'skip' : (check.ok ? 'ok' : 'no');
    return `<div class="check"><div class="mark ${cls}">${mark}</div>
      <div class="txt"><b>${esc(check.label)}</b><small>${esc(check.detail)}</small></div></div>`;
  }).join('');
  el.innerHTML = `
    <div class="hd"><span class="tag">${esc(report.title || '模拟检测')}</span>
      <span class="tag" style="color:${report.ok ? 'var(--ok)' : 'var(--err)'}">
        ${report.ok ? '全部通过' : '存在未通过项'}</span>
      <span class="tag">${esc(report.elapsed)}s</span>
      <span style="margin-left:auto">${time()}</span></div>
    <div class="bd">
      ${report.error ? `<div class="logline error">${esc(report.error)}</div>` : ''}
      ${rows}
      <div class="hint" style="margin-top:10px">
        本题集识别出 ${(report.problems || []).length} 道题目。
      </div>
    </div>`;
  return el;
}

function render(event) {
  if (event.seq !== undefined) {
    if (seenSeq.has(event.seq)) return;
    seenSeq.add(event.seq);
    if (seenSeq.size > 2000) seenSeq.clear();
  }
  const data = event.data || {};
  switch (event.type) {
    case 'hello':
      refresh();
      break;
    case 'problem':
      push(problemCard(data));
      break;
    case 'thinking':
      push(Object.assign(document.createElement('div'), {
        className: 'logline', textContent: `… 正在推理 ${data.problem_id ?? ''}`
      }));
      break;
    case 'answer':
      push(answerCard(data));
      break;
    case 'simulation':
      push(simulationCard(data));
      renderSimSummary(data);
      break;
    case 'sim-log':
      push(Object.assign(document.createElement('div'), {
        className: 'logline', textContent: `  ${data.message}`
      }));
      break;
    case 'log':
      push(Object.assign(document.createElement('div'), {
        className: 'logline ' + (data.level || 'info'), textContent: data.message
      }));
      break;
    case 'warn':
      push(Object.assign(document.createElement('div'), {
        className: 'logline warn', textContent: `⚠ ${data.message}`
      }));
      break;
    case 'error':
      push(Object.assign(document.createElement('div'), {
        className: 'logline error', textContent: `✗ ${data.message}`
      }));
      break;
    case 'skip':
      push(Object.assign(document.createElement('div'), {
        className: 'logline', textContent: `跳过 ${data.problem_id}：${data.reason}`
      }));
      break;
    case 'browser':
      refresh();
      break;
    case 'watching':
      refresh();
      break;
    default:
      break;
  }
}

function renderSimSummary(report) {
  const rows = (report.checks || []).map((check) => {
    const mark = check.skipped ? '○' : (check.ok ? '✓' : '✗');
    const cls = check.skipped ? 'skip' : (check.ok ? 'ok' : 'no');
    return `<div class="check"><div class="mark ${cls}">${mark}</div>
      <div class="txt"><b>${esc(check.label)}</b><small>${esc(check.detail)}</small></div></div>`;
  }).join('');
  $('sim-result').innerHTML =
    `<div class="hint" style="margin-top:4px;color:${report.ok ? 'var(--ok)' : 'var(--err)'}">
       ${esc(report.title)}：${report.ok ? '全部通过' : '存在未通过项'}（${esc(report.elapsed)}s）
     </div>${rows}`;
}

// ---------------------------------------------------------------- SSE
function connect() {
  const source = new EventSource('/api/events');
  source.onmessage = (message) => {
    try { render(JSON.parse(message.data)); }
    catch (error) { console.error('解析事件失败', error, message.data); }
  };
  source.onerror = () => {
    $('dot-watch').className = 'dot warn';
  };
  return source;
}

// ---------------------------------------------------------------- 交互
async function withBusy(button, task) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '执行中…';
  try { await task(); }
  finally { button.disabled = false; button.textContent = original; refresh(); }
}

$('btn-start').addEventListener('click', () => withBusy($('btn-start'), async () => {
  // 先把面板里的设置落盘，避免「刚改完就点启动」用到旧配置
  await saveSettings({ quiet: true });
  const { mode, dry_run: dryRun, auto_enter_lesson: autoEnter } = settingsState;
  if (mode !== 'manual' && !dryRun) {
    const extra = autoEnter ? '，并会自动进入上课中的课堂' : '';
    if (!confirm(`即将以 ${mode} 模式自动作答，会真实操作页面${extra}。确认继续？`)) return;
  }
  const result = await api('/api/watch/start', {});
  if (!result.ok) toast(result.error || '启动失败');
  else toast('监听已启动', 'info');
}));

$('btn-stop').addEventListener('click', () => withBusy($('btn-stop'), async () => {
  const result = await api('/api/watch/stop', {});
  if (!result.ok) toast(result.error || '停止失败');
  else toast('监听已停止', 'info');
}));

$('btn-attach').addEventListener('click', () => withBusy($('btn-attach'), async () => {
  const result = await api('/api/browser/attach', {});
  if (!result.ok) toast(result.error || '接管失败');
  else toast(`已接管：${result.url}`, 'info');
}));

$('btn-dump').addEventListener('click', () => withBusy($('btn-dump'), async () => {
  const result = await api('/api/dump', {});
  toast(result.ok ? `页面结构已导出到 ${result.path}` : (result.error || '导出失败'),
        result.ok ? 'info' : 'error');
}));

for (const [id, kind] of [['btn-sim-offline', 'offline'],
                          ['btn-sim-browser', 'browser'],
                          ['btn-sim-live', 'live']]) {
  $(id).addEventListener('click', () => withBusy($(id), async () => {
    const result = await api('/api/simulate', { kind });
    if (!result.ok) toast(result.error || '模拟检测失败');
  }));
}

$('btn-ask').addEventListener('click', () => withBusy($('btn-ask'), async () => {
  const text = $('ask-text').value.trim();
  if (!text) { toast('请先输入题目'); return; }
  const result = await api('/api/ask', { text });
  if (!result.ok) toast(result.error || '提问失败');
}));

$('btn-clear').addEventListener('click', () => {
  stream.querySelectorAll('.ev, .logline').forEach((node) => node.remove());
  empty.style.display = '';
});

// ---------------------------------------------------------------- 启动
syncBgForm();
applyBg();
loadSettings();
connect();
refresh();
setInterval(refresh, 4000);
