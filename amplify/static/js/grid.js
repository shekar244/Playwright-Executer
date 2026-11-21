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

async function _gridLoadConfig() {
  const cfg = await _gFetch('/api/grid/config');
  document.getElementById('gridEnv').innerHTML = _gList(cfg.environments).map(e =>
    `<option value="${_gEsc(e)}"${e === cfg.default_environment ? ' selected' : ''}>${_gEsc(e)}</option>`).join('');
  document.getElementById('gridBrowsers').innerHTML = _gList(cfg.browsers).map(b =>
    `<label><input type="checkbox" value="${_gEsc(b)}"${b === cfg.default_browser ? ' checked' : ''}/> ${_gEsc(b)}</label>`).join('');
  document.getElementById('gridChunk').value = cfg.default_chunk_size;
  document.getElementById('gridTests').addEventListener('input', _gridCountTests);
  _gridConfigLoaded = true;
}

function _gridTestList() {
  return document.getElementById('gridTests').value.split('\n').map(s => s.trim()).filter(Boolean);
}

function _gridCountTests() {
  document.getElementById('gridTestCount').textContent = `${_gridTestList().length} selected`;
}

// The repo chosen on the Executor tab — what "Load from repo" lists and what a run is snapshotted from.
function _gridRepoPath() {
  return (document.getElementById('repo').value || document.getElementById('repoSelect').value || '').trim();
}

async function gridLoadFromRepo() {
  const msg = document.getElementById('gridStartMsg');
  const repo = _gridRepoPath();
  if (!repo) { msg.className = 'grid-msg err'; msg.textContent = 'Select a repo on the Executor tab first'; return; }
  try {
    const data = await _gFetch(`/api/tests?repo=${encodeURIComponent(repo)}`);
    const prefix = repo.replace(/[\\/]+$/, '');
    const files = Object.values(data.tree || {}).flat().map(p => {
      const norm = String(p).replace(/\\/g, '/');
      const base = prefix.replace(/\\/g, '/');
      return norm.startsWith(base + '/') ? norm.slice(base.length + 1) : norm;
    });
    document.getElementById('gridTests').value = [...new Set(files)].join('\n');
    _gridCountTests();
    msg.className = 'grid-msg'; msg.textContent = `Loaded ${files.length} test files`;
  } catch (e) {
    msg.className = 'grid-msg err'; msg.textContent = e.message;
  }
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
    environment: document.getElementById('gridEnv').value,
    chunk_size: parseInt(document.getElementById('gridChunk').value, 10) || 3,
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
    </tr>`;
  }).join('');
}

async function _gridLoadPods() {
  const data = await _gFetch('/api/grid/pods');
  const k = data.kubernetes || {};
  document.getElementById('gridPodSource').textContent = k.enabled
    ? (k.error ? `· Kubernetes: ${k.error}` : '· live from OpenShift')
    : '· from runner heartbeats (not running in a cluster)';
  document.getElementById('gridPodCounts').innerHTML = _gridPodCountsHtml(data.counts);
  document.getElementById('gridPodBody').innerHTML = _gridPodRowsHtml(data.pods) ||
    '<tr><td colspan="6" style="text-align:center;color:var(--text-dim);padding:24px;">No pods yet — start a run</td></tr>';
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
      <td>${_gBadge(r.status)}${r.outcome ? ' ' + _gBadge(r.outcome) : ''}</td>
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
  return `${_gEsc(run.run_id)} · ${_gEsc(run.environment)} · ${_gEsc(_gList(run.browsers).join(', '))} ${_gBadge(run.status)}${run.outcome ? ' ' + _gBadge(run.outcome) : ''}`;
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
        <td class="g-mono">${_gEsc(c.assigned_runner || '—')}</td>
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

// ── Wiring (event delegation: ids never go through inline JS strings) ─────────

document.addEventListener('click', (e) => {
  const from = e.target && e.target.closest ? e.target : null;
  if (!from || !from.closest('#page-grid')) return;
  const run = from.closest('[data-grid-run]');
  if (run) { e.preventDefault(); gridOpenRun(run.dataset.gridRun); return; }
  const log = from.closest('[data-grid-log]');
  if (log) gridShowLog(log.dataset.gridLog);
});
