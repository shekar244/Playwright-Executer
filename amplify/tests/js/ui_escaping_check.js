// Feeds hostile data (what a pod's test code, a coordinator or a git folder name could send) to the real
// rendering code of static/js/grid.js and static/js/dashboard.js, in a stub DOM, and checks the HTML it
// produces: only known tags and attributes, no event-handler attributes, no unescaped markup from the data.
// Run by test_ui_escaping.py:  node ui_escaping_check.js <path to amplify/static/js>
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const jsDir = process.argv[2];
const problems = [];
const MARK = 'window.__xss=1';
const HOSTILE_TEXT = [
  `<img src=x onerror=${MARK}>`,
  `"><img src=x onerror=${MARK}>`,
  `' onmouseover='${MARK}`,
  `');${MARK};//`,
  `</span><script>${MARK}</script>`,
  `" onmouseover="${MARK}" x="`,
  '`${MARK}`',
];
const ESCAPED_MARK = '&lt;img src=x onerror=window.__xss=1&gt;';

// ── HTML audit ────────────────────────────────────────────────────────────────

const TAGS = new Set(['tr', 'td', 'th', 'span', 'a', 'button', 'div', 'ul', 'li', 'br', 'label', 'input', 'option',
  'details', 'summary', 'table', 'thead', 'tbody', 'i']);
const ATTR_OK = /^(class|title|style|href|value|type|checked|selected|open|disabled|colspan|tabindex|aria-[a-z]+|data-[a-z-]+)$/;
const TAG_RE = /<(\/?)([a-z][a-z0-9]*)((?:\s+[a-z][a-z0-9-]*(?:="[^"<>]*")?)*)\s*\/?>/y;
const ATTR_RE = /\s+([a-z][a-z0-9-]*)(?:="([^"]*)")?/g;
const STYLE_OK = /^[\w\s:;.%(),#-]*$/;

function audit(html, label, out = problems) {
  for (let i = String(html).indexOf('<'); i !== -1; i = String(html).indexOf('<', i + 1)) {
    TAG_RE.lastIndex = i;
    const m = TAG_RE.exec(html);
    if (!m) { out.push(`${label}: unparsable or injected markup near ${JSON.stringify(html.slice(i, i + 60))}`); continue; }
    if (!TAGS.has(m[2])) out.push(`${label}: unexpected <${m[2]}>`);
    for (const a of m[3].matchAll(ATTR_RE)) {
      const [, name, value = ''] = a;
      if (!ATTR_OK.test(name)) out.push(`${label}: unexpected attribute ${name} on <${m[2]}>`);
      if (name === 'style' && (!STYLE_OK.test(value) || /url|expression/i.test(value))) out.push(`${label}: odd style ${JSON.stringify(value)}`);
      if (name === 'href' && value !== '#') out.push(`${label}: odd href ${JSON.stringify(value)}`);
    }
  }
}

// The audit must itself catch the original bug, or a pass would mean nothing.
{
  const old = `<li><span class="hint">0.1</span><img src=x onerror=${MARK}>s</span></li>`;
  const found = []; audit(old, 'selftest', found);
  if (!found.length) problems.push('selftest: the audit did not flag an injected <img onerror>');
  const handler = '<tr onclick="gridOpenRun(\'a\')"><td>x</td></tr>';
  const found2 = []; audit(handler, 'selftest', found2);
  if (!found2.length) problems.push('selftest: the audit did not flag an inline onclick');
}

const unescape = (s) => s.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&gt;/g, '>').replace(/&lt;/g, '<').replace(/&amp;/g, '&');

// ── Stub DOM ──────────────────────────────────────────────────────────────────

function makeContext(files) {
  const els = {};
  const listeners = {};
  const make = (id) => ({
    id, innerHTML: '', textContent: '', value: '', checked: false, disabled: false, hidden: false, href: '', src: '', open: false,
    style: {}, dataset: {}, options: [], className: '',
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, appendChild() {}, remove() {}, scrollIntoView() {}, focus() {}, setAttribute() {}, select() {},
    querySelectorAll: () => [], querySelector: () => null, closest: () => null, matches: () => false,
    get parentElement() { return make('parent'); }, getContext: () => ({}),
  });
  const document = {
    getElementById: (id) => (els[id] ||= make(id)), querySelectorAll: () => [], hidden: false, body: make('body'),
    createElement: () => make('created'), contains: () => true, activeElement: null, execCommand: () => false,
    addEventListener: (type, fn) => { listeners[type] = fn; },
  };
  const calls = { fetches: [] };
  const sandbox = {
    document, console, setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    getComputedStyle: () => ({ getPropertyValue: () => '' }), localStorage: { getItem: () => null, setItem() {} },
    navigator: {}, Intl, URLSearchParams, Option: function Option() {}, confirm: () => false,
    fetch: async (url) => { calls.fetches.push(url); return files.respond(url); },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  return { ctx: sandbox, els, listeners, calls };
}

const load = (ctx, file) => vm.runInContext(fs.readFileSync(path.join(jsDir, file), 'utf8'), ctx, { filename: file });
const evalIn = (ctx, code) => vm.runInContext(code, ctx);
const json = (data, ok = true, status = 200) => ({ ok, status, json: async () => data });
const mustContainEscaped = (html, label) => { if (!html.includes(ESCAPED_MARK)) problems.push(`${label}: the hostile text is not shown (escaped)`); };

// ── grid.js ───────────────────────────────────────────────────────────────────

async function checkGrid() {
  const [H0, H1, H2, H3, H4, H5, H6] = HOSTILE_TEXT;
  const hostileNum = [H0, true, null, NaN, Infinity, -1, [], {}, '12', 1e999];
  const runId = H3;                                   // a quote-breaking id, as an attribute value and as a JS argument
  const detail = {
    run_id: runId, environment: H1, browsers: [H0, H2], status: H4, outcome: H5,
    chunk_details: hostileNum.map((d, i) => ({
      chunk_id: `${runId}-${H1}-${i}`, browser: H0, status: i % 2 ? 'completed' : H4, assigned_runner: H5, tests: [H0, H6],
      results: [{ test_results: [
        { nodeid: H0, outcome: H1, duration: d, message: H2 },
        { nodeid: H3, outcome: H4, duration: `0.1</span>${H0}`, message: H5 },
        null, 'junk',
      ] }],
    })).concat([{ chunk_id: runId + '-x', status: 'completed', results: 'junk', tests: 'junk' }, null, 'junk']),
  };
  const runs = { runs: [
    { run_id: runId, created_at: H0, environment: H1, browsers: [H0], status: H4, outcome: H5, total_tests: H0, passed: H0, failed: H1,
      skipped: H2, chunks: { completed: H0, total: H1 }, requested_by: H2 },
    { run_id: H6, created_at: null, environment: null, browsers: 'junk', status: null, total_tests: null, passed: {}, failed: [], skipped: true,
      chunks: 'junk', requested_by: null },
    null, 'junk',
  ] };
  const pods = { kubernetes: { enabled: true, error: H0 }, counts: { [H0]: H1, [H2]: -3, ok: 4 },
    pods: [{ name: H0, node: H1, browser: H2, state: H3, reason: H4, run_id: runId, current_chunk: H5, environment: H6, last_seen: H0 },
      { name: null, run_id: H4 }, null, 'junk'] };
  const files = { respond: (url) => {
    if (url.startsWith('/api/grid/pods')) return json(pods);
    if (url.startsWith('/api/grid/runs?')) return json(runs);
    if (url.startsWith('/api/grid/runs/')) return json(detail);
    return json({ error: 'nope' }, false, 404);
  } };
  const { ctx, els, listeners } = makeContext(files);
  load(ctx, 'grid.js');
  await evalIn(ctx, '_gridLoadPods()');
  await evalIn(ctx, '_gridLoadRuns()');
  await evalIn(ctx, `_gridLoadRunDetail(${JSON.stringify(runId)})`);
  const parts = { pods: els.gridPodBody.innerHTML, podCounts: els.gridPodCounts.innerHTML, runs: els.gridRunBody.innerHTML,
    detailTitle: els.gridDetailTitle.innerHTML, detail: els.gridDetailBody.innerHTML };
  for (const [name, html] of Object.entries(parts)) {
    if (!html) problems.push(`grid ${name}: nothing was rendered`);
    audit(html, `grid ${name}`);
    mustContainEscaped(html, `grid ${name}`);
  }
  // No numbers from the data reach the markup as text: counts and durations are numbers or nothing.
  if (/NaN|Infinity|undefined|\[object/.test(parts.runs.replace(/&[a-z#0-9]+;/g, ''))) problems.push('grid runs: a bad value was printed');
  if (/(\d)<\/span>\s*<img|>s<\/span>/.test(parts.detail)) problems.push('grid detail: a duration was printed unescaped');

  // Ids are read back from data attributes by one delegated click handler, never through inline JS.
  const opened = [], logged = [];
  ctx.gridOpenRun = (id) => opened.push(id);
  ctx.gridShowLog = (id) => logged.push(id);
  const attr = (html, name) => [...html.matchAll(new RegExp(`${name}="([^"]*)"`, 'g'))].map((m) => unescape(m[1]));
  const clickOn = (selector, dataset) => listeners.click({
    preventDefault() {},
    target: { closest: (sel) => (sel === '#page-grid' ? {} : sel === selector ? { dataset } : null) },
  });
  const rowIds = attr(parts.runs, 'data-grid-run');
  if (!rowIds.includes(runId)) problems.push('grid runs: the run id did not survive the data attribute exactly');
  clickOn('[data-grid-run]', { gridRun: rowIds[0] });
  const logIds = attr(parts.detail, 'data-grid-log');
  if (!logIds.some((id) => id.startsWith(runId))) problems.push('grid detail: the chunk id did not survive the data attribute');
  clickOn('[data-grid-log]', { gridLog: logIds[0] });
  if (opened[0] !== runId || logged[0] !== logIds[0]) problems.push('grid: the click handler did not pass the exact id on');
  if (!listeners.click) problems.push('grid: no delegated click handler');
}

// ── dashboard.js ──────────────────────────────────────────────────────────────

function checkDashboard() {
  const [H0, H1, H2, H3, H4, H5] = HOSTILE_TEXT;
  const files = { respond: () => json({}) };
  const { ctx, els } = makeContext(files);
  load(ctx, 'dashboard.js');
  const n = H0;                                        // a number field that holds markup
  evalIn(ctx, `_renderPods(${JSON.stringify([
    { pod: H0, browser: H1, runs: n, chunks: n, tests: n, passed: n, failed: n, last_ms: n },
    { pod: H2, browser: null, runs: 1, chunks: 1, tests: 3, passed: 2, failed: 1, last_ms: 1700000000000 }])})`);
  evalIn(ctx, `_renderRuns(${JSON.stringify([
    { id: H3, source: H0, started_ms: n, environment: H1, browsers: [H0, H2], status: H4, duration_s: n, pods: [H0, H1],
      totals: { passed: n, failed: n, broken: n, skipped: n }, repo: H5, sha: H0, has_report: true },
    { id: H1, source: 'grid', started_ms: 1700000000000, environment: null, browsers: [], status: 'passed', duration_s: 3,
      pods: [], totals: { passed: 1, failed: 0, broken: 0, skipped: 0 }, repo: '', sha: '', has_report: false }])})`);
  evalIn(ctx, `_renderKpis(${JSON.stringify({ runs: n, running: n, failed_runs: n, total: n, passed: n, failed: n, broken: n, skipped: n,
    pass_rate: n, avg_test_s: n, pods: n })})`);
  evalIn(ctx, `_renderBanner(${JSON.stringify({ sources: { grid: { configured: true, reachable: false, error: H0 } },
    grid_runs_skipped: n, grid_runs_capped: n, grid_runs_examined: n, grid_runs_matched: n, runs: [] })})`);
  evalIn(ctx, `_renderFilters(${JSON.stringify({ environments: [H0], browsers: [H1], pods: [H2] })})`);
  const chunk = { chunk_id: H2, browser: H0, pod: H1, status: H4, state: H5, passed: n, failed: n, broken: n, skipped: n,
    duration_s: n, has_log: true, tests: [{ name: H0, outcome: H1, duration_s: n, uid: H2, has_report: true }] };
  evalIn(ctx, `_renderDetail(${JSON.stringify({ run: { id: H3, source: 'grid', status: H4, environment: H0, requested_by: H1, repo: H2,
    sha: H3, started_ms: n, duration_s: n, has_report: true }, chunks: [chunk], hidden_chunks: 0 })}, 'grid', ${JSON.stringify(H3)})`);
  evalIn(ctx, `_dash.logs = { stdout: '', stderr: '', tests: ${JSON.stringify([{ nodeid: H0, outcome: H1, duration: n, message: H2 },
    { nodeid: H3, outcome: H4, duration: [1], message: { a: 1 } }])} }; dashLogTab('tests')`);

  const parts = { pods: els.dashPodBody.innerHTML, runs: els.dashRunBody.innerHTML, kpis: els.dashKpis.innerHTML,
    banner: els.dashBanner.innerHTML, chips: els.dashChips.innerHTML, filters: els.dfEnv.innerHTML + els.dfBrowser.innerHTML + els.dfPod.innerHTML,
    chunks: els.dashChunkBody.innerHTML, logTests: els.dashLogTests.innerHTML };
  for (const [name, html] of Object.entries(parts)) {
    if (!html && name !== 'chips') problems.push(`dashboard ${name}: nothing was rendered`);
    audit(html, `dashboard ${name}`);
    if (name !== 'chips') mustContainEscaped(html, `dashboard ${name}`);
  }
}

(async () => {
  try {
    await checkGrid();
    checkDashboard();
  } catch (e) {
    problems.push(`the check itself crashed: ${e && e.stack || e}`);
  }
  if (problems.length) {
    console.log(`${problems.length} problem(s):\n- ${[...new Set(problems)].slice(0, 25).join('\n- ')}`);
    process.exit(1);
  }
  console.log('OK');
})();
