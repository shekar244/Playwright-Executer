// ── Reporting Dashboard JS ────────────────────────────────────────────────────
// Grid runs and local Executor runs in one view: filter by source / environment / browser /
// pod / time frame, drill into a run (which pod ran what), read its logs, open its HTML report.
// Server side: routes/dashboard.py (/api/dashboard/overview, /api/dashboard/run/<source>/<id>,
// /api/dashboard/report-link) and routes/grid.py (/api/grid/chunks/<id> for logs).

let DC, CHART_GRID, CHART_LABEL, CHART_TEXT, CHART_SURFACE;

// Chart.js draws on canvas, so it cannot read CSS variables — resolve them per render.
function _chartTheme() {
  const v = (n) => getComputedStyle(document.body).getPropertyValue(n).trim();
  DC = { passed: v('--green'), failed: v('--red'), broken: v('--yellow'), skipped: v('--accent'), unknown: v('--text-dim') };
  CHART_GRID    = v('--chart-grid');
  CHART_LABEL   = v('--text-dim');
  CHART_TEXT    = v('--text');
  CHART_SURFACE = v('--surface');
}
_chartTheme();

const _DASH_STORE = 'amplify-dash-filters-v1';
const _DASH_DEFAULTS = { source: 'all', env: '', browser: '', pod: '', time: '7', from: '', to: '' };
const _DASH_TIMES = ['1', '7', '30', '90', 'all', 'custom'];
const _dash = { f: { ..._DASH_DEFAULTS }, data: null, seq: 0, ready: false, timer: null, live: false,
                open: null, shownRun: '', logs: null, logSeq: 0, returnFocus: null,
                generating: new Set() };            // "source:run id" of reports being generated right now

// ── Small helpers ─────────────────────────────────────────────────────────────

const _dEsc = (v) => String(v ?? '').replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const _dEl = (id) => document.getElementById(id);

async function _dFetch(url, opts) {
  const resp = await fetch(url, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

function _dWhen(ms) {
  return ms ? new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
}

function _dAgo(ms) {
  if (!ms) return '—';
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function _dDur(s) {
  if (s == null) return '—';
  if (s < 10) return `${Number(s).toFixed(1)}s`;
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s % 60)}s`;
}

const _dReportKey = (source, id) => `${source || 'local'}:${id}`;

// "repo@1a2b3c4" — which commit of which repo a Grid run's tests came from.
const _dRepoRef = (r) => (r.repo ? (r.sha ? `${r.repo}@${String(r.sha).slice(0, 7)}` : r.repo) : '');

const _dBadge = (state) => `<span class="dbadge d-${_dEsc(state || 'pending')}">${_dEsc(state || 'pending')}</span>`;

// ── Filters ───────────────────────────────────────────────────────────────────

function _dashRestore() {
  try { Object.assign(_dash.f, JSON.parse(localStorage.getItem(_DASH_STORE) || '{}')); } catch (e) { /* use defaults */ }
  const f = _dash.f;
  if (!['all', 'grid', 'local'].includes(f.source)) f.source = 'all';
  if (!_DASH_TIMES.includes(f.time)) f.time = '7';
  ['env', 'browser', 'pod', 'from', 'to'].forEach(k => { if (typeof f[k] !== 'string') f[k] = ''; });
}

function _dashSave() {
  try { localStorage.setItem(_DASH_STORE, JSON.stringify(_dash.f)); } catch (e) { /* private mode */ }
}

function _dSelect(id, value, allLabel) {
  const sel = _dEl(id);
  if (!sel.options.length) { _dFill(id, [], value, allLabel); return; }       // before the first response: just the saved value
  if (value && ![...sel.options].some(o => o.value === value)) sel.add(new Option(value, value));
  sel.value = value || '';
}

function _dashSyncControls() {
  const f = _dash.f;
  document.querySelectorAll('#dfSource button').forEach(b => {
    const on = b.dataset.v === f.source;
    b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on));
  });
  _dSelect('dfEnv', f.env, 'All environments');       // so a saved filter is never wiped by touching another control
  _dSelect('dfBrowser', f.browser, 'All browsers');
  _dSelect('dfPod', f.pod, 'All pods');
  _dEl('dfTime').value = f.time;
  _dEl('dfFrom').value = f.from;
  _dEl('dfTo').value = f.to;
  _dEl('dfCustom').style.display = f.time === 'custom' ? '' : 'none';
}

function _dashReadControls() {
  const f = _dash.f;
  f.env = _dEl('dfEnv').value;
  f.browser = _dEl('dfBrowser').value;
  f.pod = _dEl('dfPod').value;
  f.time = _dEl('dfTime').value;
  f.from = _dEl('dfFrom').value;
  f.to = _dEl('dfTo').value;
}

function dashFilterChanged() { _dashReadControls(); _dashSave(); _dashSyncControls(); loadDashboard(); }

function dashSetSource(v) {
  _dash.f.source = v;
  if (v === 'local') _dash.f.pod = '';               // local runs have no pods
  _dashSave(); _dashSyncControls(); loadDashboard();
}

function dashResetFilters() {
  Object.assign(_dash.f, _DASH_DEFAULTS);
  _dashSave(); _dashSyncControls(); loadDashboard();
}

function dashPickPod(name) {
  _dash.f.pod = _dash.f.pod === name ? '' : name;     // clicking the chosen pod again clears it
  if (_dash.f.source === 'local') _dash.f.source = 'all';
  _dashSave(); _dashSyncControls(); loadDashboard();
}

function _dashQuery(browserAndPodOnly) {
  const f = _dash.f, q = new URLSearchParams();
  if (f.browser) q.set('browser', f.browser);
  if (f.pod) q.set('pod', f.pod);
  if (browserAndPodOnly) return q;
  if (f.source !== 'all') q.set('source', f.source);
  if (f.env) q.set('env', f.env);
  if (f.time === 'custom') { if (f.from) q.set('from', f.from); if (f.to) q.set('to', f.to); }
  else if (f.time !== 'all') q.set('days', f.time);
  q.set('tz', String(new Date().getTimezoneOffset()));
  try { q.set('tzname', Intl.DateTimeFormat().resolvedOptions().timeZone || ''); } catch (e) { /* offset alone will do */ }
  return q;
}

function _dFill(id, values, current, allLabel) {
  const sel = _dEl(id);
  const opts = [''].concat(values);
  if (current && !values.includes(current)) opts.push(current);   // never silently drop the chosen value
  sel.innerHTML = opts.map(v => `<option value="${_dEsc(v)}">${_dEsc(v || allLabel)}</option>`).join('');
  sel.value = current || '';
}

function _renderFilters(facets) {
  const f = _dash.f;
  _dFill('dfEnv', facets.environments, f.env, 'All environments');
  _dFill('dfBrowser', facets.browsers, f.browser, 'All browsers');
  _dFill('dfPod', facets.pods, f.pod, 'All pods');
  _dEl('dfPod').disabled = f.source === 'local';
  const chips = [];
  if (f.source !== 'all') chips.push(['source', `Source: ${f.source === 'grid' ? 'Grid' : 'Local'}`]);
  if (f.env) chips.push(['env', `Environment: ${f.env}`]);
  if (f.browser) chips.push(['browser', `Browser: ${f.browser}`]);
  if (f.pod) chips.push(['pod', `Pod: ${f.pod}`]);
  _dEl('dashChips').innerHTML = chips.map(([k, label]) =>
    `<button type="button" class="dc-chip" data-clear="${k}" title="Remove this filter">${_dEsc(label)} ✕</button>`).join('');
}

// ── Load ──────────────────────────────────────────────────────────────────────

async function loadDashboard() {
  if (!_dash.ready) { _dashRestore(); _dash.ready = true; _dashSyncControls(); }
  const seq = ++_dash.seq;
  clearTimeout(_dash.timer);
  const f = _dash.f;
  if (f.time === 'custom' && f.from && f.to && f.from > f.to) {      // don't ask the server for an impossible range
    _dEl('dashSubtitle').textContent = '— the start date must be on or before the end date';
    return;
  }
  _dEl('dashSubtitle').textContent = '— loading…';
  let data;
  try {
    data = await _dFetch('/api/dashboard/overview?' + _dashQuery());
  } catch (e) {
    if (seq === _dash.seq) { _dEl('dashSubtitle').textContent = `— ${e.message}`; _dashArm(); }   // keep following live runs
    return;
  }
  if (seq !== _dash.seq) return;                      // a newer request is already on its way
  _dash.data = data;

  _chartTheme();
  _renderFilters(data.facets);
  _renderBanner(data);
  _renderKpis(data.kpis);
  _renderPods(data.pods);
  _renderRuns(data.runs);
  _renderCharts(data);                                // last: the tables above never depend on Chart.js

  const k = data.kpis, g = data.sources.grid || {};
  const parts = [`${k.runs} run${k.runs === 1 ? '' : 's'}`, `${k.total} test${k.total === 1 ? '' : 's'}`];
  if (g.reachable) parts.push('Grid connected');
  _dEl('dashSubtitle').textContent = '— ' + parts.join(' · ');

  if (_dash.open) dashOpenRun(_dash.open.source, _dash.open.id, false);   // keep an open drill-down current
  _dash.live = k.running > 0;                         // follow runs that are still in flight
  _dashArm();
}

// Follow in-flight runs: poll every 5 s while any run is running, but only refresh while the tab is
// visible and no viewer is open — and always look again later, so the chain never dies.
function _dashArm() {
  clearTimeout(_dash.timer);
  if (_dash.live) _dash.timer = setTimeout(_dashTick, 5000);
}

function _dashTick() {
  const active = _dEl('page-dashboard')?.classList.contains('active');
  const modalOpen = ['dashLogModal', 'dashReportModal'].some(id => _dEl(id).style.display !== 'none');
  if (active && !document.hidden && !modalOpen) loadDashboard();
  else _dashArm();
}

function _renderBanner(d) {
  const out = [], g = d.sources.grid || {}, source = _dash.f.source;
  if (source !== 'local') {
    if (g.configured === false) out.push(['info', 'Grid results aren’t included because no Grid coordinator is set up. Ask an admin to set it in Admin Panel → Grid Coordinator.']);
    else if (g.configured && !g.reachable) out.push(['warn', `Grid results are unavailable right now (${g.error || 'unreachable'}).` + (source === 'all' ? ' Showing local runs only.' : '')]);
  }
  if (d.grid_runs_skipped) out.push(['warn', `${d.grid_runs_skipped} Grid run${d.grid_runs_skipped === 1 ? '' : 's'} couldn’t be read and ${d.grid_runs_skipped === 1 ? 'is' : 'are'} not counted.`]);
  if (d.grid_runs_capped) out.push(['warn', `Only the newest ${d.grid_runs_examined} of ${d.grid_runs_matched} matching Grid runs are included in these numbers — choose a shorter time frame to see the rest.`]);
  if (d.grid_list_incomplete) out.push(['warn', 'The Grid coordinator lists at most 200 runs, so older Grid runs in this time frame may be missing — choose a shorter time frame.']);
  if (d.truncated) out.push(['info', `Showing the newest ${d.runs.length} runs in the table — narrow the filters to see others.`]);
  _dEl('dashBanner').innerHTML = out.map(([kind, text]) => `<div class="dc-banner ${kind}">${_dEsc(text)}</div>`).join('');
}

function _dashFlash(text) {
  const el = document.createElement('div');
  el.className = 'dc-banner warn';
  el.textContent = text;
  _dEl('dashBanner').appendChild(el);
  setTimeout(() => el.remove(), 7000);
}

// ── KPIs and charts ───────────────────────────────────────────────────────────

function _renderKpis(k) {
  const failed = k.failed + k.broken;
  const cards = [
    ['Runs', k.runs, k.running ? `${k.running} in progress` : (k.failed_runs ? `${k.failed_runs} with failures` : ''), '--accent'],
    ['Tests', k.total, '', '--teal'],
    ['Passed', k.passed, '', '--green'],
    ['Failed', failed, k.broken ? `incl. ${k.broken} broken` : '', '--red'],
    ['Skipped', k.skipped, '', '--yellow'],
    ['Pass rate', k.pass_rate == null ? '—' : `${k.pass_rate}%`, '', '--purple'],
    ['Avg test time', _dDur(k.avg_test_s), '', '--teal'],
    ['Pods used', k.pods, '', '--accent'],
  ];
  _dEl('dashKpis').innerHTML = cards.map(([label, val, sub, color]) => `
    <div class="dc-kpi" style="--k:var(${color});">
      <div class="dc-kpi-label">${_dEsc(label)}</div>
      <div class="dc-kpi-val">${_dEsc(val)}</div>
      <div class="dc-kpi-sub">${_dEsc(sub)}</div>
    </div>`).join('');
}

const _D_CHARTS = [['chartStatusDonut', 'emptyDonut'], ['chartByBrowser', 'emptyBrowser'], ['chartByEnv', 'emptyEnv'], ['chartTrend', 'emptyTrend']];

function _renderCharts(data) {
  if (typeof Chart === 'undefined') {                 // the Chart.js script didn't load (offline / blocked)
    _D_CHARTS.forEach(([canvas, empty]) => {
      _dEl(canvas).parentElement.style.display = 'none';
      const note = _dEl(empty); note.style.display = 'block'; note.textContent = 'Charts unavailable — Chart.js could not be loaded';
    });
    return;
  }
  _D_CHARTS.forEach(([, empty]) => { _dEl(empty).textContent = 'No results in this view'; });
  [() => _renderDonut(data.kpis),
   () => _renderStacked('chartByBrowser', 'emptyBrowser', data.by_browser, true),
   () => _renderStacked('chartByEnv', 'emptyEnv', data.by_environment, true),
   () => _renderTrend(data.by_day)].forEach(draw => { try { draw(); } catch (e) { console.error('chart failed', e); } });
}

function _dChartSlot(canvasId, emptyId, has) {
  const old = Chart.getChart(canvasId); if (old) old.destroy();
  _dEl(emptyId).style.display = has ? 'none' : 'block';
  _dEl(canvasId).parentElement.style.display = has ? 'flex' : 'none';
  return has;
}

function _renderDonut(k) {
  if (!_dChartSlot('chartStatusDonut', 'emptyDonut', k.total > 0)) return;
  const vals = [k.passed, k.failed, k.broken, k.skipped];
  const labels = ['Passed', 'Failed', 'Broken', 'Skipped'];
  const centre = {
    id: 'donutCentre',
    afterDraw(chart) {
      const { ctx, chartArea } = chart;
      if (!chartArea) return;
      const cx = (chartArea.left + chartArea.right) / 2, cy = (chartArea.top + chartArea.bottom) / 2;
      ctx.save(); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = CHART_TEXT; ctx.font = '700 22px sans-serif'; ctx.fillText(String(k.total), cx, cy - 6);
      ctx.fillStyle = CHART_LABEL; ctx.font = '11px sans-serif'; ctx.fillText('tests', cx, cy + 12);
      ctx.restore();
    },
  };
  new Chart(_dEl('chartStatusDonut').getContext('2d'), {
    type: 'doughnut',
    data: { labels, datasets: [{ data: vals, backgroundColor: [DC.passed, DC.failed, DC.broken, DC.skipped], borderColor: CHART_SURFACE, borderWidth: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: '68%', animation: false,
      plugins: { legend: { position: 'right', labels: { color: CHART_LABEL, boxWidth: 10, font: { size: 11 } } } } },
    plugins: [centre],
  });
}

function _stackSeries(rows) {
  return [['passed', 'Passed'], ['failed', 'Failed'], ['broken', 'Broken'], ['skipped', 'Skipped']]
    .filter(([key]) => rows.some(r => r[key] > 0));
}

function _renderStacked(canvasId, emptyId, rows, horizontal) {
  if (!_dChartSlot(canvasId, emptyId, rows.some(r => r.total > 0))) return;
  const series = _stackSeries(rows);
  const axis = { stacked: true, grid: { color: CHART_GRID }, ticks: { color: CHART_LABEL, precision: 0, font: { size: 10 } } };
  new Chart(_dEl(canvasId).getContext('2d'), {
    type: 'bar',
    data: { labels: rows.map(r => r.name), datasets: series.map(([key, label]) => ({
      label, data: rows.map(r => r[key]), backgroundColor: DC[key], borderRadius: 3, maxBarThickness: 26 })) },
    options: { indexAxis: horizontal ? 'y' : 'x', responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: series.length > 1, position: 'bottom', labels: { color: CHART_LABEL, boxWidth: 10, font: { size: 10 } } } },
      scales: { x: axis, y: axis } },
  });
}

function _renderTrend(days) {
  const rows = days.map(d => ({ ...d, total: d.passed + d.failed + d.broken + d.skipped,
    name: new Date(d.date + 'T00:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' }) }));
  _renderStacked('chartTrend', 'emptyTrend', rows, false);
}

// ── Pods ──────────────────────────────────────────────────────────────────────

function _renderPods(pods) {
  _dEl('dashPodCount').textContent = `${pods.length} pod${pods.length === 1 ? '' : 's'}`;
  _dEl('dashPodBody').innerHTML = pods.length ? pods.map(p => {
    const rate = p.tests ? Math.round(100 * p.passed / p.tests) : null;
    return `<tr class="${_dash.f.pod === p.pod ? 'dc-selected' : ''}">
      <td><button class="dlink dmono" data-pod="${_dEsc(p.pod)}" title="Show only what this pod ran">${_dEsc(p.pod)}</button></td>
      <td>${_dEsc(p.browser || '—')}</td><td>${_dEsc(p.runs)}</td><td>${_dEsc(p.chunks)}</td><td>${_dEsc(p.tests)}</td>
      <td style="color:var(--green);">${_dEsc(p.passed)}</td><td style="color:${p.failed ? 'var(--red)' : 'inherit'};">${_dEsc(p.failed)}</td>
      <td>${rate == null ? '—' : `<span class="dbar"><i style="width:${_dEsc(rate)}%"></i></span> ${_dEsc(rate)}%`}</td>
      <td>${_dAgo(p.last_ms)}</td></tr>`;
  }).join('') : `<tr><td colspan="9" style="text-align:center;color:var(--text-dim);padding:22px;">
      No Grid pods in this view${_dash.f.source === 'local' ? ' (source is Local)' : ''}</td></tr>`;
}

// ── Runs ──────────────────────────────────────────────────────────────────────

function _renderRuns(runs) {
  _dEl('dashRunCount').textContent = `${runs.length} run${runs.length === 1 ? '' : 's'}`;
  const open = _dash.open;
  _dEl('dashRunBody').innerHTML = runs.length ? runs.map(r => {
    const t = r.totals, failed = t.failed + t.broken;
    const ref = _dRepoRef(r);
    const src = (r.source === 'grid' ? '<span class="dsrc dsrc-grid">Grid</span>' : '<span class="dsrc dsrc-local">Local</span>')
      + (ref ? ` <span class="dc-dim">${_dEsc(ref)}</span>` : '');
    const busy = _dash.generating.has(_dReportKey(r.source, r.id));
    const browsers = r.browsers.length ? `<div class="dpills">${r.browsers.map(b => `<span class="dpill">${_dEsc(b)}</span>`).join('')}</div>` : '—';
    const selected = open && open.source === r.source && open.id === r.id;
    return `<tr class="dc-clickable ${selected ? 'dc-selected' : ''}" data-source="${_dEsc(r.source)}" data-id="${_dEsc(r.id)}"
        tabindex="0" aria-label="Open run ${_dEsc(r.id)}">
      <td title="${_dEsc(r.id)}">${_dWhen(r.started_ms)}<div class="dc-dim">${_dAgo(r.started_ms)}</div></td>
      <td>${src}</td><td>${_dEsc(r.environment || '—')}</td><td>${browsers}</td><td>${_dBadge(r.status)}</td>
      <td style="color:var(--green);">${_dEsc(t.passed)}</td><td style="color:${failed ? 'var(--red)' : 'inherit'};">${_dEsc(failed)}</td><td>${_dEsc(t.skipped)}</td>
      <td>${_dDur(r.duration_s)}</td>
      <td>${r.pods.length ? `<span title="${_dEsc(r.pods.join(', '))}">${_dEsc(r.pods.length)}</span>` : '—'}</td>
      <td>${r.has_report ? `<button class="dlink" data-report-run="${_dEsc(r.id)}" data-report-source="${_dEsc(r.source)}"${busy ? ' disabled' : ''}>${busy ? 'Generating report…' : '📄 View'}</button>` : '—'}</td></tr>`;
  }).join('') : `<tr><td colspan="11" style="text-align:center;color:var(--text-dim);padding:30px;">
      No runs match these filters.
      ${_dash.f.time !== 'all' ? '<button class="dlink" data-widen="1">Show all time</button>' : ''}</td></tr>`;
}

// ── Drill-down: one run ───────────────────────────────────────────────────────

function dashCloseDetail() {
  _dash.open = null;
  _dEl('dashDetail').style.display = 'none';
  document.querySelectorAll('#dashRunBody tr.dc-selected').forEach(tr => tr.classList.remove('dc-selected'));
}

async function dashOpenRun(source, id, scroll = true) {
  _dash.open = { source, id };
  document.querySelectorAll('#dashRunBody tr').forEach(tr =>
    tr.classList.toggle('dc-selected', tr.dataset.source === source && tr.dataset.id === id));
  const card = _dEl('dashDetail'), note = _dEl('dashDetailNote');
  card.style.display = '';
  _dEl('dashDetailTitle').textContent = id;
  if (scroll) {                                       // a new run was chosen: don't show the previous one meanwhile
    _dash.shownRun = '';
    _dEl('dashDetailStatus').innerHTML = '';
    _dEl('dashDetailMeta').textContent = 'Loading…';
    _dEl('dashDetailReport').style.display = 'none';
    _dEl('dashChunkBody').innerHTML = '';
    note.style.display = 'none';
  }
  let d;
  try {
    d = await _dFetch(`/api/dashboard/run/${encodeURIComponent(source)}/${encodeURIComponent(id)}?${_dashQuery(true)}`);
  } catch (e) {
    if (!_dash.open || _dash.open.id !== id) return;
    _dEl('dashDetailStatus').innerHTML = '';
    _dEl('dashDetailMeta').textContent = '';
    _dEl('dashChunkBody').innerHTML = '';
    note.style.display = ''; note.textContent = e.message;
    return;
  }
  if (!_dash.open || _dash.open.id !== id) return;
  _renderDetail(d, source, id);
  if (scroll) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function _renderDetail(d, source, id) {
  const note = _dEl('dashDetailNote'), run = d.run;
  const reportBtn = _dEl('dashDetailReport');
  if (!run) {
    _dEl('dashDetailStatus').innerHTML = '';
    _dEl('dashDetailMeta').textContent = '';
    _dEl('dashChunkBody').innerHTML = '';
    reportBtn.style.display = 'none';
    note.style.display = ''; note.textContent = 'Nothing in this run matches the current browser / pod filter.';
    return;
  }
  _dEl('dashDetailStatus').innerHTML = _dBadge(run.status);
  _dEl('dashDetailMeta').textContent = [run.environment, run.requested_by && `by ${run.requested_by}`, _dRepoRef(run),
    `started ${_dWhen(run.started_ms)}`, run.duration_s != null && _dDur(run.duration_s)].filter(Boolean).join(' · ');
  const generating = _dash.generating.has(_dReportKey(run.source, run.id));
  reportBtn.style.display = run.has_report ? '' : 'none';
  reportBtn.disabled = generating;
  reportBtn.textContent = generating ? 'Generating report…' : '📄 Run report';
  reportBtn.onclick = () => dashViewReport(run.id, '', run.source);
  if (d.hidden_chunks > 0) {
    const f = _dash.f, by = [f.browser && `browser ${f.browser}`, f.pod && `pod ${f.pod}`].filter(Boolean).join(' and ');
    note.style.display = '';
    note.textContent = `Showing ${d.chunks.length} of ${d.chunks.length + d.hidden_chunks} chunks — filtered by ${by}. Reset the filters to see them all.`;
  } else {
    note.style.display = 'none';
  }
  const wasOpen = _dash.shownRun === id           // keep what the user expanded when this is a refresh of the same run
    ? new Map([...document.querySelectorAll('#dashChunkBody details[data-chunk]')].map(el => [el.dataset.chunk, el.open]))
    : new Map();
  _dEl('dashChunkBody').innerHTML = d.chunks.map(c => {
    const state = c.state || c.status;
    const waiting = state === 'pending' || state === 'running';
    const bits = [c.passed && `${c.passed} passed`, (c.failed + c.broken) && `${c.failed + c.broken} failed`, c.skipped && `${c.skipped} skipped`]
      .filter(Boolean).join(' · ') || (waiting ? `${c.tests.length} queued` : 'no results reported');
    const open = wasOpen.has(c.chunk_id) ? wasOpen.get(c.chunk_id) : state === 'failed';
    const tests = c.tests.map(t => `<li>${_dBadge(t.outcome || 'pending')} <span class="dmono">${_dEsc(t.name)}</span>
      <span class="dc-dim">${t.duration_s != null ? _dDur(t.duration_s) : ''}</span>
      ${t.has_report ? `<button class="dlink" data-report-run="${_dEsc(id)}" data-report-uid="${_dEsc(t.uid)}">📄 Report</button>` : ''}</li>`).join('');
    return `<tr>
      <td>${_dEsc(c.browser || '—')}</td>
      <td>${c.pod ? `<button class="dlink dmono" data-pod="${_dEsc(c.pod)}" title="Show only what this pod ran">${_dEsc(c.pod)}</button>` : '<span class="dc-dim">this server (local)</span>'}</td>
      <td>${_dBadge(state)}</td>
      <td class="dc-tests"><details data-chunk="${_dEsc(c.chunk_id)}" ${open ? 'open' : ''}><summary>${_dEsc(bits)}</summary><ul>${tests}</ul></details></td>
      <td>${_dDur(c.duration_s)}</td>
      <td>${c.has_log ? `<button class="btn btn-sm" data-logs="${_dEsc(c.chunk_id)}">View logs</button>` : '—'}</td></tr>`;
  }).join('');
  _dash.shownRun = id;
}

// ── Logs viewer (Grid chunks: what the pod's pytest printed) ──────────────────

function _dashModalOpen(id, focusId) {
  _dash.returnFocus = document.activeElement;
  _dEl(id).style.display = 'flex';
  _dEl('page-dashboard').style.overflow = 'hidden';   // the page scrolls inside #page-dashboard, not the body
  _dEl(focusId)?.focus();
}

function _dashModalClose(id) {
  _dEl(id).style.display = 'none';
  if (['dashLogModal', 'dashReportModal'].every(m => _dEl(m).style.display === 'none')) _dEl('page-dashboard').style.overflow = '';
  const back = _dash.returnFocus; _dash.returnFocus = null;
  if (back && document.contains(back)) back.focus();
}

async function dashShowLogs(chunkId) {
  const seq = ++_dash.logSeq;                         // answers for an earlier chunk must not land here
  _dashModalOpen('dashLogModal', 'dashLogClose');
  _dEl('dashLogTitle').textContent = chunkId;
  _dEl('dashLogMeta').textContent = 'Loading…';
  _dEl('dashLogBody').textContent = '';
  _dEl('dashLogBody').style.display = ''; _dEl('dashLogTests').style.display = 'none';
  document.querySelectorAll('#dashLogTabs button').forEach(b => b.classList.remove('active'));
  _dash.logs = null;
  let d;
  try {
    d = await _dFetch('/api/grid/chunks/' + encodeURIComponent(chunkId));
  } catch (e) {
    if (seq !== _dash.logSeq) return;
    _dEl('dashLogMeta').textContent = '';
    _dEl('dashLogBody').textContent = `Could not load the logs: ${e.message}`;
    return;
  }
  if (seq !== _dash.logSeq) return;
  const r = (d.results && d.results[0]) || {};
  _dash.logs = { stdout: r.stdout || '', stderr: r.stderr || '', tests: r.test_results || [] };
  _dEl('dashLogMeta').textContent = [d.browser, d.assigned_runner && `pod ${d.assigned_runner}`, d.environment,
    r.success === true ? 'passed' : (r.success === false ? 'failed' : d.status), r.duration_seconds != null && _dDur(r.duration_seconds)]
    .filter(Boolean).join(' · ');
  dashLogTab('stdout');
}

function dashLogTab(tab) {
  const L = _dash.logs; if (!L) return;
  document.querySelectorAll('#dashLogTabs button').forEach(b => b.classList.toggle('active', b.dataset.t === tab));
  const pre = _dEl('dashLogBody'), tests = _dEl('dashLogTests');
  _dash.logs.tab = tab;
  if (tab === 'tests') {
    pre.style.display = 'none'; tests.style.display = '';
    tests.innerHTML = L.tests.length ? `<table class="history-tbl"><thead><tr><th>Test</th><th>Outcome</th><th>Time</th><th>Message</th></tr></thead><tbody>${
      L.tests.map(t => `<tr><td class="dmono">${_dEsc(t.nodeid)}</td><td>${_dBadge(t.outcome)}</td>
        <td>${t.duration != null ? _dDur(t.duration) : '—'}</td><td>${_dEsc(t.message || '')}</td></tr>`).join('')}</tbody></table>`
      : '<div class="dc-dim" style="padding:20px;">No per-test results were reported for this chunk.</div>';
    return;
  }
  tests.style.display = 'none'; pre.style.display = '';
  pre.textContent = L[tab] || (tab === 'stderr' ? '(nothing was written to stderr)' : '(no output)');
  pre.scrollTop = 0;
}

async function dashCopyLogs() {
  const L = _dash.logs; if (!L) return;
  const text = L.tab === 'tests' ? JSON.stringify(L.tests, null, 2) : (L[L.tab || 'stdout'] || '');
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; } catch (e) { /* not a secure origin (plain http) */ }
  if (!ok) {                                          // http://amplify-qea-grid has no clipboard API: use the old way
    const box = document.createElement('textarea');
    box.value = text; box.style.cssText = 'position:fixed;opacity:0;top:0;left:0;';
    document.body.appendChild(box); box.select();
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    box.remove();
  }
  const btn = _dEl('dashLogCopy'); btn.textContent = ok ? 'Copied ✓' : 'Copy failed';
  setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
}

function dashCloseLogs() { _dash.logSeq++; _dash.logs = null; _dashModalClose('dashLogModal'); }

// ── HTML report viewer ────────────────────────────────────────────────────────

// A Grid report is built by the coordinator on first request and can take a minute: say so on the run's
// buttons (the table is re-drawn while runs are live, so the state is kept in _dash.generating too).
function _dashReportBusy(source, id, busy) {
  const key = _dReportKey(source, id);
  if (busy) _dash.generating.add(key); else _dash.generating.delete(key);
  const detail = _dEl('dashDetailReport');
  const buttons = [...document.querySelectorAll('#dashRunBody [data-report-run]')]
    .filter(b => !b.dataset.reportUid && _dReportKey(b.dataset.reportSource, b.dataset.reportRun) === key);
  if (_dash.open && _dReportKey(_dash.open.source, _dash.open.id) === key) buttons.push(detail);
  buttons.forEach(b => {
    b.disabled = busy;
    b.textContent = busy ? 'Generating report…' : (b === detail ? '📄 Run report' : '📄 View');
  });
}

async function dashViewReport(runId, uid, source) {
  source = source || 'local';
  if (!uid && _dash.generating.has(_dReportKey(source, runId))) return;      // already on its way
  if (!uid) _dashReportBusy(source, runId, true);
  let link;
  try {
    link = await _dFetch('/api/dashboard/report-link', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, run_id: runId, uid: uid || '' }),
    });
  } catch (e) { _dashFlash(`Couldn’t open that report: ${e.message}`); return; }
  finally { if (!uid) _dashReportBusy(source, runId, false); }
  _dEl('dashReportTitle').textContent = link.title;
  _dEl('dashReportNewTab').href = link.url;
  _dEl('dashReportFrame').src = link.url;
  _dashModalOpen('dashReportModal', 'dashReportClose');
}

function dashCloseReport() {
  _dEl('dashReportFrame').src = 'about:blank';       // stop the report and free its memory
  _dashModalClose('dashReportModal');
}

// ── Clear local history ───────────────────────────────────────────────────────

async function clearHistory() {
  if (!confirm('Clear your local run history? Grid runs are not affected. This cannot be undone.')) return;
  await fetch('/api/report/history/clear', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repo: '' }),
  });
  dashCloseDetail();
  loadDashboard();
}

// ── Wiring (event delegation: names and ids never go through inline JS strings) ─

document.addEventListener('click', (e) => {
  const t = e.target;
  if (!t.closest('#page-dashboard')) return;          // only react inside the dashboard
  const clear = t.closest('[data-clear]');
  if (clear) {
    const k = clear.dataset.clear;
    _dash.f[k] = k === 'source' ? 'all' : '';
    _dashSave(); _dashSyncControls(); loadDashboard(); return;
  }
  if (t.closest('[data-widen]')) { _dash.f.time = 'all'; _dashSave(); _dashSyncControls(); loadDashboard(); return; }
  const pod = t.closest('[data-pod]');
  if (pod) { dashPickPod(pod.dataset.pod); return; }
  const logs = t.closest('[data-logs]');
  if (logs) { dashShowLogs(logs.dataset.logs); return; }
  const report = t.closest('[data-report-run]');
  if (report) { dashViewReport(report.dataset.reportRun, report.dataset.reportUid, report.dataset.reportSource); return; }
  const row = t.closest('#dashRunBody tr[data-id]');
  if (row) dashOpenRun(row.dataset.source, row.dataset.id);
});

document.addEventListener('keydown', (e) => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('#dashRunBody tr.dc-clickable')) {
    e.preventDefault(); dashOpenRun(e.target.dataset.source, e.target.dataset.id); return;
  }
  if (e.key !== 'Escape') return;
  if (_dEl('dashReportModal')?.style.display !== 'none') dashCloseReport();
  else if (_dEl('dashLogModal')?.style.display !== 'none') dashCloseLogs();
});

// Close a viewer by clicking its backdrop — but not when a text selection started inside the panel and
// merely ended up over the backdrop.
[['dashLogModal', () => dashCloseLogs()], ['dashReportModal', () => dashCloseReport()]].forEach(([id, close]) => {
  const overlay = _dEl(id); if (!overlay) return;
  let pressedOnBackdrop = false;
  overlay.addEventListener('mousedown', (e) => { pressedOnBackdrop = e.target === overlay; });
  overlay.addEventListener('click', (e) => { if (e.target === overlay && pressedOnBackdrop) close(); pressedOnBackdrop = false; });
});

// The report is same-origin, so Esc pressed inside it can close the viewer too.
_dEl('dashReportFrame')?.addEventListener('load', () => {
  try {
    _dEl('dashReportFrame').contentWindow.document.addEventListener('keydown', (e) => { if (e.key === 'Escape') dashCloseReport(); });
  } catch (e) { /* about:blank or a cross-origin page: nothing to attach to */ }
});
