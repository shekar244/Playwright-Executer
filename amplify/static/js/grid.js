// ── Grid JS (distributed run: choose tests/env/browsers, watch pods + runs) ───
//
// Everything that reaches innerHTML below comes from the coordinator, from the pods' test code or from git
// (names, ids, messages, durations, counts), so none of it is trusted: text goes through _gEsc, numbers
// through _gNum / _gDur, and ids never go into inline JS — rows carry data-grid-* attributes and one
// delegated click handler (bottom of the file) reads them. Rendering is done by the _grid*Html functions,
// which only build strings, so a test can feed them hostile data without a browser.

const GRID_REFRESH_MS = 5000;
let _gridSelectedRun = null;
let _gridConfigLoaded = false;
let _gridTimer = null;
let _gridLivePods = new Set();     // pods that serve a live view (from the last pod list)
let _gridArgDefs = [];             // extra pytest arguments configured in Admin → Grid
let _gridEnvs = { list: [], def: '' };   // environments a run may use (Admin → Grid Environments, else the coordinator's)

const _gEsc = (v) => String(v ?? '').replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const _gList = (v) => (Array.isArray(v) ? v : []);
const _gNum = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
// "1.2s" for a real duration; nothing for anything else (a pod can report any value here).
const _gDur = (v) => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? `${v.toFixed(v < 10 ? 2 : 1)}s` : '');

function _gBadge(state) {
  const s = state || 'unknown';
  return `<span class="g-badge g-${_gEsc(s)}">${_gEsc(s)}</span>`;
}

// A run can be cancelled while it is queued or running. The id travels in a data attribute (never in inline JS).
const _gCancellable = (status) => status === 'queued' || status === 'running';
const _gCancelBtn = (run) => (run && run.run_id && _gCancellable(run.status)
  ? ` <button class="btn btn-sm g-cancel" data-grid-cancel="${_gEsc(run.run_id)}" title="Cancel this run">✕ Cancel</button>` : '');

// Live view: a pod that runs its browser headed on a VNC display can be watched; the name travels in a data attribute.
const _gLiveBtn = (name) => (typeof name === 'string' && name
  ? ` <button class="btn btn-sm g-live" data-grid-live="${_gEsc(name)}" title="Watch this pod's browser live">👁 Live</button>` : '');

function _gAgo(iso) {
  if (!iso) return '—';
  const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

async function _gFetch(url, opts) {
  const resp = await fetch(url, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

// ── Entry point (called by switchTab) ─────────────────────────────────────────

async function loadGrid() {
  const sub = document.getElementById('gridSubtitle');
  let status;
  try {
    status = await _gFetch('/api/grid/status');
  } catch (e) {
    sub.textContent = `— ${e.message}`;
    return;
  }
  if (!status.configured) {
    sub.textContent = '— not configured: ask an admin to set the coordinator in Admin Panel → Grid Coordinator';
    return;
  }
  if (!status.reachable) {
    sub.textContent = `— coordinator unreachable (${status.error || 'unknown error'})`;
    return;
  }
  sub.textContent = `— connected to ${status.url}`;

  try {
    if (!_gridConfigLoaded) await _gridLoadConfig();
    gridLoadTree(false);                              // once, and again whenever the active repo changes
    await Promise.all([_gridLoadPods(), _gridLoadRuns()]);
  } catch (e) {
    sub.textContent = `— ${e.message}`;
    return;
  }
  if (_gridSelectedRun) _gridLoadRunDetail(_gridSelectedRun);

  if (!_gridTimer) _gridTimer = setInterval(_gridTick, GRID_REFRESH_MS);
}

function _gridTick() {
  const page = document.getElementById('page-grid');
  const auto = document.getElementById('gridAuto');
  if (!page || !page.classList.contains('active') || !auto.checked || document.hidden) return;
  loadGrid().catch(() => {});
}

// ── New run form ──────────────────────────────────────────────────────────────

// ── Extra pytest arguments (Admin → Grid) ─────────────────────────────────────
// The words a run's arguments become on the pod, as the runner builds them: separate → [flag, value],
// joined → [flag=value], switch → [flag]. Only for display; the server checks the choice again.
function _gridArgWords(args) {
  return _gList(args).flatMap(a => {
    if (!a || typeof a.flag !== 'string') return [];
    if (a.style === 'switch') return [a.flag];
    return a.style === 'joined' ? [`${a.flag}=${a.value}`] : [a.flag, String(a.value ?? '')];
  });
}

// The run's arguments for the current choice in the form (mirrors grid_args.resolve).
function _gridChosenArgs(defs, selection, environment) {
  return _gList(defs).flatMap(d => {
    if (!d || typeof d.flag !== 'string') return [];
    if (d.kind === 'environment') return environment ? [{ flag: d.flag, style: d.style, value: environment }] : [];
    if (d.kind === 'switch') return selection[d.flag] ? [{ flag: d.flag, style: 'switch', value: null }] : [];
    const value = selection[d.flag];
    return value ? [{ flag: d.flag, style: d.style, value }] : [];
  });
}

function _gridArgSelection() {
  const selection = {};
  document.querySelectorAll('#gridArgs [data-grid-arg]').forEach(el => {
    selection[el.dataset.gridArg] = el.type === 'checkbox' ? el.checked : el.value;
  });
  return selection;
}

// The run's environment: the "Run's environment" argument's dropdown, or the default environment without one.
function _gridRunEnvironment() {
  const el = document.querySelector('#gridArgs [data-grid-env]');
  return el ? el.value : (_gridEnvs.def || '');
}

function _gridUpdateArgPreview() {
  const env = _gridRunEnvironment();
  const words = _gridArgWords(_gridChosenArgs(_gridArgDefs, _gridArgSelection(), env));
  const args = words.length ? words.map(w => (/\s/.test(w) ? `'${w}'` : w)).join(' ') : 'none';
  const envNote = document.querySelector('#gridArgs [data-grid-env]') || !env ? '' : `Environment: ${env} (default) · `;
  document.getElementById('gridEnvArg').textContent = `${envNote}Pod arguments: ${args}`;
}

// Built with DOM calls: labels, options and values come from the Admin Panel and are never parsed as HTML.
function _gridRenderArgs(defs) {
  const box = document.getElementById('gridArgs');
  box.replaceChildren();
  _gList(defs).forEach(d => {
    if (!d || typeof d.flag !== 'string') return;
    const field = document.createElement('div');
    field.className = 'field';
    const label = document.createElement('label');
    label.textContent = `${d.label || d.flag} `;
    const hint = document.createElement('span');
    hint.className = 'hint';
    hint.textContent = d.flag;
    label.append(hint);
    let input;
    if (d.kind === 'environment') {
      // The run's environment and this option's value: one dropdown of the environments a run may use.
      input = document.createElement('select');
      _gList(_gridEnvs.list).forEach(env => {
        const o = document.createElement('option');
        o.value = String(env); o.textContent = String(env); o.selected = env === _gridEnvs.def;
        input.append(o);
      });
      input.dataset.gridEnv = '1';
      input.addEventListener('change', _gridUpdateArgPreview);
      field.append(label, input);
      box.append(field);
      return;
    }
    if (d.kind === 'switch') {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = d.default === true;
      const wrap = document.createElement('label');
      wrap.className = 'checkbox-label';
      wrap.append(input, document.createTextNode(` pass ${d.flag}`));
      field.append(label, wrap);
    } else {
      input = document.createElement('select');
      const options = (d.required ? [] : [['', '(not passed)']]).concat(_gList(d.values).map(v => [String(v), String(v)]));
      options.forEach(([value, text]) => {
        const o = document.createElement('option');
        o.value = value; o.textContent = text; o.selected = value === (d.default || '');
        input.append(o);
      });
      field.append(label, input);
    }
    input.dataset.gridArg = d.flag;
    input.addEventListener('change', _gridUpdateArgPreview);
    box.append(field);
  });
}

async function _gridLoadConfig() {
  const cfg = await _gFetch('/api/grid/config');
  _gridEnvs = { list: _gList(cfg.environments), def: cfg.default_environment || _gList(cfg.environments)[0] || '' };
  try {
    _gridArgDefs = _gList((await _gFetch('/api/grid/pytest-args')).arguments);
  } catch (e) {
    _gridArgDefs = [];
  }
  _gridRenderArgs(_gridArgDefs);
  _gridUpdateArgPreview();
  document.getElementById('gridBrowsers').innerHTML = _gList(cfg.browsers).map(b =>
    `<label><input type="checkbox" value="${_gEsc(b)}"${b === cfg.default_browser ? ' checked' : ''}/> ${_gEsc(b)}</label>`).join('');
  document.getElementById('gridChunk').value = cfg.default_chunk_size;
  document.getElementById('gridTests').addEventListener('input', _gridCountTests);
  document.getElementById('gridTests').addEventListener('change', () => { _gridSelFromText(); _gridRenderTree(); });
  document.getElementById('gridTreeFilter').addEventListener('input', _gridRenderTree);
  _gridApplyCollapsed();
  _gridConfigLoaded = true;
}

function _gridTestList() {
  return document.getElementById('gridTests').value.split('\n').map(s => s.trim()).filter(Boolean);
}

function _gridCountTests() {
  const ids = _gridTestList();
  const tests = typeof _gridTree !== 'undefined' && _gridTree ? _gridTestCountOf(ids) : ids.length;   // grid_picker.js
  document.getElementById('gridTestCount').textContent = ids.length
    ? `${ids.length} selected${tests !== ids.length ? ` (≈${tests} tests)` : ''}` : '0 selected';
}

// The active repo (Repository tab; the Executor keeps it in its #repo field) — what the picker lists and what a run
// is snapshotted from.
function _gridRepoPath() {
  const active = typeof _repoWs !== 'undefined' && _repoWs ? ((_repoWs.repos || []).find(r => r.active) || {}).path : '';
  return (document.getElementById('repo').value || document.getElementById('repoSelect').value || active || '').trim();
}

async function gridStartRun() {
  const msg = document.getElementById('gridStartMsg');
  const btn = document.getElementById('gridStartBtn');
  const browsers = [...document.querySelectorAll('#gridBrowsers input:checked')].map(i => i.value);
  const tests = _gridTestList();
  if (!tests.length) { msg.className = 'grid-msg err'; msg.textContent = 'Add at least one test'; return; }
  if (!browsers.length) { msg.className = 'grid-msg err'; msg.textContent = 'Pick at least one browser'; return; }

  const warn = document.getElementById('gridStartWarn');
  const repoPath = _gridRepoPath();
  const payload = {
    tests, browsers,
    environment: _gridRunEnvironment(),
    chunk_size: parseInt(document.getElementById('gridChunk').value, 10) || 3,
    pytest_args: _gridArgSelection(),
  };
  if (repoPath) payload.repo_path = repoPath;       // run from a snapshot of this repo's committed state

  btn.disabled = true;
  warn.style.display = 'none';
  if (repoPath) { msg.className = 'grid-msg'; msg.textContent = 'Packaging the repo and starting the run…'; }
  try {
    const run = await _gFetch('/api/grid/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const from = run.repo && run.sha ? ` from ${run.repo}@${String(run.sha).slice(0, 7)}` : '';
    msg.className = 'grid-msg';
    msg.textContent = `Started ${run.run_id}${from}: ${run.chunks_queued} chunks on ${_gList(run.browsers).join(', ')} (${run.environment})`;
    const notes = [];                                // textContent: the repo name comes from a folder name
    if (run.dirty) notes.push(`⚠ ${run.repo} has uncommitted changes. They are not included — the run uses the committed state (${String(run.sha).slice(0, 7)}).`);
    if (run.warning) notes.push(`⚠ ${run.warning}`);
    if (notes.length) { warn.textContent = notes.join(' '); warn.style.display = ''; }
    _gridSelectedRun = run.run_id;
    await Promise.all([_gridLoadRuns(), _gridLoadPods()]);
    _gridLoadRunDetail(run.run_id);
  } catch (e) {
    msg.className = 'grid-msg err'; msg.textContent = e.message;     // e.g. "select the repo's top-level folder"
  } finally {
    btn.disabled = false;
  }
}

// ── Cancel a queued or running run ────────────────────────────────────────────

async function gridCancelRun(runId) {
  const msg = document.getElementById('gridStartMsg');
  if (!confirm(`Cancel ${runId}?\n\nChunks still waiting in the queue are dropped. A chunk that is already running ` +
               'finishes on its pod, but its results are discarded. Finished chunks keep their results.')) return;
  try {
    const out = await _gFetch(`/api/grid/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
    msg.className = 'grid-msg';
    msg.textContent = `Cancelled ${runId}: ${_gNum(out.cancelled_chunks)} chunk(s) stopped`;
  } catch (e) {
    msg.className = 'grid-msg err'; msg.textContent = `Could not cancel ${runId}: ${e.message}`;
  }
  await Promise.all([_gridLoadRuns().catch(() => {}), _gridLoadPods().catch(() => {})]);
  if (_gridSelectedRun === runId) _gridLoadRunDetail(runId);
}

// ── Pods ──────────────────────────────────────────────────────────────────────

function _gridPodCountsHtml(counts) {
  return Object.entries(counts && typeof counts === 'object' ? counts : {})
    .map(([state, n]) => `<span class="g-badge g-${_gEsc(state)}">${_gEsc(state)} · ${_gNum(n)}</span>`).join('');
}

function _gridPodRowsHtml(pods) {
  return _gList(pods).map(p => {
    p = p || {};
    return `
    <tr>
      <td class="g-mono" title="${_gEsc(p.node || '')}">${_gEsc(p.name)}</td>
      <td>${_gEsc(p.browser || '—')}</td>
      <td>${_gBadge(p.state)}${p.reason && p.state !== 'busy' && p.state !== 'idle' ? ` <span class="hint">${_gEsc(p.reason)}</span>` : ''}</td>
      <td class="g-mono">${p.run_id ? `<a href="#" data-grid-run="${_gEsc(p.run_id)}">${_gEsc(p.run_id)}</a>` : '—'}${p.current_chunk ? `<br>${_gEsc(p.current_chunk)}` : ''}</td>
      <td>${_gEsc(p.environment || '—')}</td>
      <td>${_gAgo(p.last_seen)}</td>
      <td>${p.live_view === true ? _gLiveBtn(p.name) : ''}</td>
    </tr>`;
  }).join('');
}

async function _gridLoadPods() {
  const data = await _gFetch('/api/grid/pods');
  const k = data.kubernetes || {};
  document.getElementById('gridPodSource').textContent = k.enabled
    ? (k.error ? `· Kubernetes: ${k.error}` : '· live from OpenShift')
    : '· from runner heartbeats (not running in a cluster)';
  _gridLivePods = new Set(_gList(data.pods).filter(p => p && p.live_view === true && typeof p.name === 'string').map(p => p.name));
  document.getElementById('gridPodCounts').innerHTML = _gridPodCountsHtml(data.counts);
  document.getElementById('gridPodBody').innerHTML = _gridPodRowsHtml(data.pods) ||
    '<tr><td colspan="7" style="text-align:center;color:var(--text-dim);padding:24px;">No pods yet — start a run</td></tr>';
}

// ── Runs ──────────────────────────────────────────────────────────────────────

function _gridRunRowsHtml(runs, selected) {
  return _gList(runs).map(r => {
    r = r || {};
    const [passed, failed, skipped, total] = [r.passed, r.failed, r.skipped, r.total_tests].map(_gNum);
    const chunks = r.chunks || {};
    const pct = (n) => (total ? (100 * n / total).toFixed(1) : 0);
    const tip = `${passed + failed + skipped}/${total} tests, ${_gNum(chunks.completed)}/${_gNum(chunks.total)} chunks`;
    return `
    <tr class="g-clickable${r.run_id === selected ? ' g-selected' : ''}" data-grid-run="${_gEsc(r.run_id)}">
      <td class="g-mono">${_gEsc(r.run_id)}</td>
      <td>${_gAgo(r.created_at)}</td>
      <td>${_gEsc(r.environment)}</td>
      <td>${_gEsc(_gList(r.browsers).join(', '))}</td>
      <td>${_gBadge(r.status)}${r.outcome ? ' ' + _gBadge(r.outcome) : ''}${_gCancelBtn(r)}</td>
      <td title="${_gEsc(tip)}">
        <div class="g-progress">
          <span style="width:${pct(passed)}%;background:var(--green);"></span>
          <span style="width:${pct(failed)}%;background:var(--red);"></span>
          <span style="width:${pct(skipped)}%;background:var(--yellow);"></span>
        </div></td>
      <td style="color:var(--green);">${passed}</td>
      <td style="color:var(--red);">${failed}</td>
      <td>${skipped}</td>
      <td>${_gEsc(r.requested_by || '—')}</td>
    </tr>`;
  }).join('');
}

async function _gridLoadRuns() {
  const data = await _gFetch('/api/grid/runs?limit=25');
  document.getElementById('gridRunBody').innerHTML = _gridRunRowsHtml(data.runs, _gridSelectedRun) ||
    '<tr><td colspan="10" style="text-align:center;color:var(--text-dim);padding:24px;">No runs yet</td></tr>';
}

function gridOpenRun(runId) {
  _gridSelectedRun = runId;
  document.getElementById('gridLog').style.display = 'none';
  _gridLoadRuns().catch(() => {});
  _gridLoadRunDetail(runId);
}

function _gridDetailTitleHtml(run) {
  const words = _gridArgWords(run.pytest_args);
  const args = words.length ? ` <span class="hint g-mono" title="Extra pytest arguments of this run">pytest ${_gEsc(words.join(' '))}</span>` : '';
  return `${_gEsc(run.run_id)} · ${_gEsc(run.environment)} · ${_gEsc(_gList(run.browsers).join(', '))} ${_gBadge(run.status)}${run.outcome ? ' ' + _gBadge(run.outcome) : ''}${_gCancelBtn(run)}${args}`;
}

function _gridDetailRowsHtml(run) {
  return _gList(run.chunk_details).map(c => {
    c = c || {};
    const per = _gList(((_gList(c.results)[0]) || {}).test_results);
    const items = per.length
      ? per.map(t => { t = t || {}; return `<li>${_gBadge(t.outcome)} <span title="${_gEsc(t.message || '')}">${_gEsc(t.nodeid)}</span> <span class="hint">${_gEsc(_gDur(t.duration))}</span></li>`; }).join('')
      : _gList(c.tests).map(t => `<li>${_gBadge(c.status === 'completed' ? 'unknown' : c.status)} ${_gEsc(t)}</li>`).join('');
    return `
      <tr>
        <td class="g-mono">${_gEsc(String(c.chunk_id ?? '').replace(String(run.run_id ?? '') + '-', ''))}</td>
        <td>${_gEsc(c.browser)}</td>
        <td>${_gBadge(c.status)}</td>
        <td class="g-mono">${_gEsc(c.assigned_runner || '—')}${c.status === 'running' && _gridLivePods.has(c.assigned_runner) ? _gLiveBtn(c.assigned_runner) : ''}</td>
        <td><ul class="g-tests">${items}</ul></td>
        <td>${c.status === 'completed' ? `<button class="btn btn-sm" data-grid-log="${_gEsc(c.chunk_id)}">View</button>` : ''}</td>
      </tr>`;
  }).join('');
}

async function _gridLoadRunDetail(runId) {
  const card = document.getElementById('gridDetailCard');
  try {
    const run = await _gFetch(`/api/grid/runs/${encodeURIComponent(runId)}`);
    card.style.display = '';
    document.getElementById('gridDetailTitle').innerHTML = _gridDetailTitleHtml(run);
    document.getElementById('gridDetailBody').innerHTML = _gridDetailRowsHtml(run);
  } catch (e) {
    card.style.display = '';
    document.getElementById('gridDetailTitle').textContent = `${runId} — ${e.message}`;
  }
}

async function gridShowLog(chunkId) {
  const el = document.getElementById('gridLog');
  el.style.display = '';
  el.textContent = 'Loading…';
  try {
    const chunk = await _gFetch(`/api/grid/chunks/${encodeURIComponent(chunkId)}`);
    const r = (chunk.results || [])[0] || {};
    el.textContent = `# ${chunk.chunk_id} on ${chunk.assigned_runner} (${chunk.browser}, ${chunk.environment})\n\n${r.stdout || ''}${r.stderr ? '\n--- stderr ---\n' + r.stderr : ''}`;
    el.scrollIntoView({ block: 'nearest' });
  } catch (e) {
    el.textContent = e.message;
  }
}

// ── Live view (noVNC, loaded on first use) ────────────────────────────────────
//
// Amplify relays the viewer's WebSocket to the pod's VNC server (routes/grid_live.py); the browser never talks to a
// pod. View-only: a watcher cannot click or type into a running test (the pod's VNC server refuses input too).

let _gridRfb = null;

function _gridLiveStatus(text) {
  document.getElementById('gridLiveStatus').textContent = text;
}

function gridCloseLive() {
  if (_gridRfb) { try { _gridRfb.disconnect(); } catch (e) { /* already gone */ } }
  _gridRfb = null;
  document.getElementById('gridLiveScreen').replaceChildren();
  document.getElementById('gridLiveModal').style.display = 'none';
}

async function gridOpenLive(pod) {
  gridCloseLive();
  const modal = document.getElementById('gridLiveModal');
  document.getElementById('gridLiveTitle').textContent = `Live view — ${pod}`;
  _gridLiveStatus('Connecting…');
  modal.style.display = 'flex';
  let view;
  try {
    view = await _gFetch(`/api/grid/pods/${encodeURIComponent(pod)}/view`);
  } catch (e) {
    _gridLiveStatus(e.message);
    return;
  }
  if (view.chunk_id) document.getElementById('gridLiveTitle').textContent = `Live view — ${pod} · ${view.chunk_id}`;
  let RFB;
  try {
    RFB = (await import('/static/vendor/novnc/core/rfb.js')).default;
  } catch (e) {
    _gridLiveStatus('The live viewer could not be loaded');
    return;
  }
  if (document.getElementById('gridLiveModal').style.display === 'none') return;     // closed while loading
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${scheme}//${location.host}/api/grid/pods/${encodeURIComponent(pod)}/vnc`;
  const rfb = new RFB(document.getElementById('gridLiveScreen'), url,
    { credentials: { password: String(view.password || '') }, wsProtocols: ['binary'], shared: true });
  rfb.viewOnly = true;
  rfb.scaleViewport = true;
  rfb.background = '#000';
  rfb.addEventListener('connect', () => _gridLiveStatus('● Live'));
  rfb.addEventListener('securityfailure', () => _gridLiveStatus('The pod refused the viewer (password)'));
  rfb.addEventListener('disconnect', (e) => {
    if (_gridRfb !== rfb) return;
    _gridLiveStatus(e.detail && e.detail.clean ? 'Disconnected — the pod finished or stopped serving' : 'Connection lost — the pod may have finished');
  });
  _gridRfb = rfb;
}

function gridLiveFullscreen() {
  const screen = document.getElementById('gridLiveScreen');
  if (screen.requestFullscreen) screen.requestFullscreen().catch(() => {});
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _gridRfb && !document.fullscreenElement) gridCloseLive();
});

// ── Wiring (event delegation: ids never go through inline JS strings) ─────────

document.addEventListener('click', (e) => {
  const from = e.target && e.target.closest ? e.target : null;
  if (!from || !from.closest('#page-grid')) return;
  const cancel = from.closest('[data-grid-cancel]');          // before the row: the button sits inside a clickable row
  if (cancel) { e.preventDefault(); e.stopPropagation(); gridCancelRun(cancel.dataset.gridCancel); return; }
  const live = from.closest('[data-grid-live]');
  if (live) { e.preventDefault(); e.stopPropagation(); gridOpenLive(live.dataset.gridLive); return; }
  if (from.closest('[data-grid-live-close]')) { gridCloseLive(); return; }
  if (from.closest('[data-grid-live-full]')) { gridLiveFullscreen(); return; }
  const treeBtn = from.closest('[data-grid-tree]');
  if (treeBtn) { _gridTreeAction(treeBtn.dataset.gridTree); return; }
  const fold = from.closest('[data-grid-fold]');
  if (fold) { e.preventDefault(); e.stopPropagation(); _gridToggleSection(fold.closest('[data-grid-collapse]')); return; }
  const section = from.closest('[data-grid-collapse]');
  if (section && !from.closest('button, a, input, select')) { _gridToggleSection(section); return; }
  const run = from.closest('[data-grid-run]');
  if (run) { e.preventDefault(); gridOpenRun(run.dataset.gridRun); return; }
  const log = from.closest('[data-grid-log]');
  if (log) gridShowLog(log.dataset.gridLog);
});
