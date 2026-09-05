// ── Executor + Config JS ──────────────────────────────────────────────────────

// ── Extra Options ─────────────────────────────────────────────────────────────
let _extraOptions = [];

function renderExtraOptions(options) {
  _extraOptions = options;
  const section   = document.getElementById('extraOptionsSection');
  const container = document.getElementById('extraOptionsContainer');
  container.innerHTML = '';
  if (!options.length) { section.style.display = 'none'; return; }
  section.style.display = '';
  options.forEach(opt => {
    const flag  = opt.flag;
    const label = opt.label || flag;
    const type  = opt.type || 'checkbox';
    const id    = 'eo_' + flag.replace(/[^a-zA-Z0-9]/g, '_');
    const wrap  = document.createElement('div');
    wrap.className = 'field';
    if (type === 'checkbox') {
      wrap.innerHTML = `<label class="checkbox-label"><input type="checkbox" id="${id}" data-flag="${flag}" ${opt.default ? 'checked' : ''} /> ${label} <code style="font-size:10px;margin-left:4px;">${flag}</code></label>`;
    } else if (type === 'dropdown') {
      const opts = (opt.values || []).map(v => `<option value="${v}"${v === opt.default ? ' selected' : ''}>${v}</option>`).join('');
      wrap.innerHTML = `<label>${label} <code style="font-size:10px;margin-left:4px;">${flag}</code></label><select id="${id}" data-flag="${flag}"><option value="">(none)</option>${opts}</select>`;
    } else if (type === 'text') {
      wrap.innerHTML = `<label>${label} <code style="font-size:10px;margin-left:4px;">${flag}</code></label><input type="text" id="${id}" data-flag="${flag}" placeholder="${opt.placeholder || ''}" value="${opt.default || ''}" />`;
    }
    container.appendChild(wrap);
  });
}

function collectExtraOptionValues() {
  const values = {};
  _extraOptions.forEach(opt => {
    const flag = opt.flag;
    const id   = 'eo_' + flag.replace(/[^a-zA-Z0-9]/g, '_');
    const el   = document.getElementById(id);
    if (!el) return;
    if (opt.type === 'checkbox') {
      values[flag] = el.checked;
    } else if (opt.type === 'text') {
      const v = el.value.trim(); if (v) values[flag] = v;
    } else {
      const v = el.value.trim();
      if (v && v.toLowerCase() !== 'none' && v !== '(none)') values[flag] = v;
    }
  });
  return values;
}

// ── Repo scanner ───────────────────────────────────────────────────────────────
async function scanRepos(preferredPath) {
  const data  = await fetch('/api/repos').then(r => r.json()).catch(() => ({ repos: [] }));
  const repos = data.repos || [];
  // Keep repoSelect populated for backward compat (hidden element)
  const sel = document.getElementById('repoSelect');
  if (sel) sel.innerHTML = repos.map(r => `<option value="${r.path}">${r.name}</option>`).join('');
  if (preferredPath) {
    applyRepo(preferredPath, false);
    discoverTests();
    return;
  }
  if (repos.length === 1) { applyRepo(repos[0].path, false); discoverTests(); }
  await loadPinnedRepos();
}

function onRepoSelect() {
  const path = document.getElementById('repoSelect').value;
  if (path) applyRepo(path);
}

function applyRepo(path, andDiscover = true) {
  document.getElementById('repo').value = path;
  const name = path.split(/[/\\]/).filter(Boolean).pop() || path;
  document.getElementById('repoSubtitle').textContent = '📁 ' + name;
  document.title = 'Amplyf QEA — ' + name;

  // Update active banner
  const banner = document.getElementById('activeRepoBanner');
  const nameEl = document.getElementById('activeRepoName');
  const pathEl = document.getElementById('activeRepoPath');
  if (banner) banner.style.display = '';
  if (nameEl) nameEl.textContent   = name;
  if (pathEl) pathEl.textContent   = path;

  // Highlight active repo in pinned list
  document.querySelectorAll('.pinned-repo-card').forEach(c => {
    c.style.borderColor = c.dataset.path === path ? 'rgba(137,180,250,0.5)' : 'var(--border)';
    c.style.background  = c.dataset.path === path ? 'rgba(137,180,250,0.06)' : 'var(--surface2)';
  });

  // Persist repo_root
  fetch('/api/config/repo-root', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_root: path }),
  }).catch(() => {});

  if (andDiscover) discoverTests();
}

// ── Pinned repos ───────────────────────────────────────────────────────────────
let _pinnedRepos = [];

async function loadPinnedRepos() {
  const cfg = await fetch('/api/config').then(r => r.json()).catch(() => ({}));
  _pinnedRepos = cfg.pinned_repos || [];
  renderPinnedRepos();
}

function renderPinnedRepos() {
  const section = document.getElementById('pinnedReposSection');
  const list    = document.getElementById('pinnedReposList');
  if (!section || !list) return;
  if (!_pinnedRepos.length) { section.style.display = 'none'; return; }

  const currentRepo = document.getElementById('repo').value.trim();
  section.style.display = 'flex';
  list.innerHTML = '';
  _pinnedRepos.forEach(p => {
    const name     = p.split(/[/\\]/).filter(Boolean).pop() || p;
    const isActive = p === currentRepo;
    const card     = document.createElement('div');
    card.className    = 'pinned-repo-card';
    card.dataset.path = p;
    card.style.cssText = `display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:var(--radius);
      border:1px solid ${isActive ? 'rgba(137,180,250,0.5)' : 'var(--border)'};
      background:${isActive ? 'rgba(137,180,250,0.06)' : 'var(--surface2)'};
      cursor:pointer;transition:all 0.12s;`;
    card.title   = p;
    card.onclick = () => applyRepo(p);
    card.innerHTML = `
      <span style="font-size:13px;flex-shrink:0;">📁</span>
      <span style="flex:1;font-size:12px;font-weight:600;color:${isActive ? 'var(--accent)' : 'var(--text-muted)'};
             overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escHtml(name)}</span>
      <button onclick="event.stopPropagation();unpinRepo('${escHtml(p)}')"
              title="Unpin" style="background:none;border:none;color:var(--text-dim);cursor:pointer;
              font-size:12px;padding:2px 4px;border-radius:3px;opacity:0.5;transition:all 0.1s;"
              onmouseover="this.style.opacity='1';this.style.color='var(--red)'"
              onmouseout="this.style.opacity='0.5';this.style.color='var(--text-dim)'">✕</button>`;
    list.appendChild(card);
  });
}

async function pinCurrentRepo() {
  const path = document.getElementById('repo').value.trim();
  if (!path || _pinnedRepos.includes(path)) return;
  _pinnedRepos.push(path);
  await _savePinnedRepos();
  renderPinnedRepos();
}

async function unpinRepo(path) {
  _pinnedRepos = _pinnedRepos.filter(p => p !== path);
  await _savePinnedRepos();
  renderPinnedRepos();
}

async function _savePinnedRepos() {
  await fetch('/api/config/pinned-repos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pinned_repos: _pinnedRepos }),
  }).catch(() => {});
}

// ── Test discovery ─────────────────────────────────────────────────────────────
async function discoverTests() {
  const repo = document.getElementById('repo').value.trim();
  if (!repo) return;
  appendLine('⟳  Discovering tests…', 'info');
  const res = await fetch(`/api/tests?repo=${encodeURIComponent(repo)}`);
  if (!res.ok) {
    const err = await res.json();
    appendLine('✗  ' + (err.error || 'Discovery failed'), 'failed');
    return;
  }
  const data = await res.json();
  testTree = data.tree || {};
  const suites = ['All Tests', ...Object.keys(testTree).sort()];
  const suiteEl = document.getElementById('suite');
  suiteEl.innerHTML = suites.map(s => `<option>${s}</option>`).join('');
  onSuiteChange();
  if (data.markers?.length) populateMarkers(data.markers);
  const total = Object.values(testTree).reduce((a, b) => a + b.length, 0);
  appendLine(`✓  Discovered ${total} test files in ${Object.keys(testTree).length} suite(s).`, 'passed');
}

function onSuiteChange() {
  const suite = document.getElementById('suite').value;
  const files = suite === 'All Tests' ? [] : (testTree[suite] || []);
  const fileEl = document.getElementById('file');
  fileEl.innerHTML = ['All in Suite', ...files.map(f => f.split('/').pop())].map(f => `<option>${f}</option>`).join('');
  clearTestNames();
}

async function onFileChange() {
  clearTestNames();
  const file = document.getElementById('file').value;
  if (!file || file === 'All in Suite') return;
  const repo = document.getElementById('repo').value.trim();
  if (!repo) return;
  const data = await fetch(`/api/tests/names?repo=${encodeURIComponent(repo)}&file=${encodeURIComponent(file)}`).then(r => r.json()).catch(() => ({ names: [] }));
  const names  = data.names || [];
  const list   = document.getElementById('testNameList');
  const hint   = document.getElementById('kHint');
  const manual = document.getElementById('manualKToggle').checked;
  if (names.length === 0 || manual) return;
  list.innerHTML = names.map(n => `<div class="test-name-item"><input type="checkbox" id="tn_${n}" value="${n}" onchange="syncKFilter()" /><label for="tn_${n}">${n}</label></div>`).join('');
  list.style.display = 'flex';
  document.getElementById('k_filter').style.display = 'none';
  hint.textContent = `${names.length} test${names.length !== 1 ? 's' : ''} — check to select, leave all unchecked to run all`;
}

function clearTestNames() {
  const list = document.getElementById('testNameList');
  list.innerHTML = ''; list.style.display = 'none';
  if (!document.getElementById('manualKToggle').checked) {
    document.getElementById('k_filter').style.display = 'none';
    document.getElementById('k_filter').value = '';
    document.getElementById('kHint').textContent = 'Select a specific test file to pick individual tests';
  }
}

function syncKFilter() {
  const checked = Array.from(document.querySelectorAll('#testNameList input:checked')).map(c => c.value);
  document.getElementById('k_filter').value = checked.join(' or ');
}

function toggleKMode() {
  const manual = document.getElementById('manualKToggle').checked;
  const list   = document.getElementById('testNameList');
  const input  = document.getElementById('k_filter');
  const hint   = document.getElementById('kHint');
  if (manual) {
    list.style.display  = 'none';
    input.style.display = '';
    hint.textContent    = 'Type a pytest -k expression (e.g. test_login or test_checkout)';
  } else {
    input.style.display = 'none';
    const file = document.getElementById('file').value;
    if (file && file !== 'All in Suite') onFileChange();
    else hint.textContent = 'Select a specific test file to pick individual tests';
  }
}

function getKFilter() {
  const manual = document.getElementById('manualKToggle').checked;
  if (manual) return document.getElementById('k_filter').value.trim();
  return Array.from(document.querySelectorAll('#testNameList input:checked')).map(c => c.value).join(' or ');
}

function populateMarkers(markers) {
  const sel      = document.getElementById('marker');
  const existing = Array.from(sel.options).map(o => o.value).filter(Boolean);
  markers.forEach(m => { if (!existing.includes(m)) sel.add(new Option(m, m)); });
}

// ── Run / Stop ────────────────────────────────────────────────────────────────
async function runTests() {
  const repo = document.getElementById('repo').value.trim();
  if (!repo) { alert('Please set the Framework Repository Root first.'); return; }
  clearLog();
  _runContext = 'executor'; _activeLogEl = document.getElementById('log');
  setRunning(true);
  const body = {
    repo,
    suite:    document.getElementById('suite').value,
    file_sel: document.getElementById('file').value,
    k_filter: getKFilter(),
    browser:  document.getElementById('browser').value,
    marker:   document.getElementById('marker').value || null,
    workers:  parseInt(document.getElementById('workers').value) || 1,
    verbose:  document.getElementById('verbose').checked,
    headed:   document.getElementById('headed').checked,
    extra:    document.getElementById('extra').value,
    extra_option_values: collectExtraOptionValues(),
  };
  const res = await fetch('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) {
    const err = await res.json();
    appendLine('✗  ' + (err.error || 'Failed to start'), 'failed');
    setRunning(false);
  }
}

async function stopTests() { await fetch('/api/stop', { method: 'POST' }); }

// ── Config file info ──────────────────────────────────────────────────────────

async function loadConfigInfo() {
  const info = await fetch('/api/config/info').then(r => r.json()).catch(() => ({}));

  const pathEl   = document.getElementById('cfgActivePath');
  const badgeEl  = document.getElementById('cfgSourceBadge');
  const overInfo = document.getElementById('cfgOverrideInfo');
  const clearBtn = document.getElementById('clearCfgOverrideBtn');

  const source = info.active_config_source || 'tool';
  const path   = info.active_config_path   || '—';

  // Show full path with word-wrap so the entire path is readable
  if (pathEl)  { pathEl.textContent = path; }
  if (badgeEl) {
    const colors = { tool: 'rgba(137,180,250,0.15)', repo: 'rgba(166,227,161,0.15)', override: 'rgba(249,226,175,0.15)' };
    const text   = { tool: 'tool', repo: 'repo', override: 'override' };
    const txtCls = { tool: 'var(--accent)', repo: 'var(--green)', override: 'var(--yellow)' };
    badgeEl.textContent       = text[source]   || source;
    badgeEl.style.background  = colors[source] || 'rgba(128,128,153,0.15)';
    badgeEl.style.color       = txtCls[source] || 'var(--text-dim)';
    badgeEl.style.borderColor = txtCls[source] || 'var(--border)';
  }
  const hasOverride = !!info.config_override_path;
  if (overInfo) overInfo.style.display = hasOverride ? '' : 'none';
  if (clearBtn) clearBtn.style.display = hasOverride ? '' : 'none';
}

async function refreshConfigAndInit() {
  const cfg = await fetch('/api/config').then(r => r.json()).catch(() => ({}));

  // Re-apply browser list & default
  if (cfg.browsers) {
    const sel = document.getElementById('browser');
    if (sel) {
      sel.innerHTML = cfg.browsers.map(b => `<option value="${b}">${b}</option>`).join('');
      if (cfg.default_browser) sel.value = cfg.default_browser;
    }
  } else if (cfg.default_browser) {
    const sel = document.getElementById('browser');
    if (sel) sel.value = cfg.default_browser;
  }
  if (cfg.default_workers) { const w = document.getElementById('workers'); if (w) w.value = cfg.default_workers; }
  if (cfg.auto_open_report !== undefined) { const a = document.getElementById('auto_open'); if (a) a.checked = !!cfg.auto_open_report; }
  if (cfg.markers?.length) populateMarkers(cfg.markers);
  renderExtraOptions(cfg.extra_options || []);
  applyUiTabs(cfg.ui_tabs || {});

  // Reload venv path input
  const venvEl = document.getElementById('venvPathInput');
  if (venvEl && cfg.venv_path !== undefined) venvEl.value = cfg.venv_path || '';

  // Refresh repo list, keeping current repo if set
  const currentRepo = document.getElementById('repo')?.value.trim() || cfg.repo_root || '';
  await scanRepos(currentRepo);

  // Refresh config info panel (badge + content)
  await loadConfigInfo();
  if (typeof checkVenvStatus === 'function') checkVenvStatus();
}

async function browseCfgOverride() {
  const current = document.getElementById('cfgActivePath')?.title || '';
  const res  = await fetch('/api/browse-folder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ start: current, file: true, filter: '*.json' }) }).catch(() => null);
  if (!res) return;
  const data = await res.json().catch(() => ({}));
  if (data.cancelled || !data.path) return;
  const res2 = await fetch('/api/config/override', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: data.path }) });
  const d2   = await res2.json().catch(() => ({}));
  if (d2.ok) await refreshConfigAndInit();
  else        alert('Could not set override: ' + (d2.error || 'unknown error'));
}

async function clearCfgOverride() {
  const res = await fetch('/api/config/override', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: '' }) });
  const d   = await res.json().catch(() => ({}));
  if (d.ok) await refreshConfigAndInit();
  else       alert('Could not clear override: ' + (d.error || 'unknown error'));
}

// ── Venv management ───────────────────────────────────────────────────────────

async function checkVenvStatus() {
  const repo    = document.getElementById('repo').value.trim();
  const badgeEl = document.getElementById('venvStatusBadge');
  const pathEl  = document.getElementById('venvPythonPath');
  const reqSec  = document.getElementById('venvReqSection');
  const reqFile = document.getElementById('venvReqFile');

  if (badgeEl) badgeEl.textContent = '⟳ checking…';
  const data = await fetch(`/api/venv/status${repo ? '?repo=' + encodeURIComponent(repo) : ''}`).then(r => r.json()).catch(() => ({}));

  const found = data.venv_found;
  if (badgeEl) {
    badgeEl.textContent       = found ? '✓ venv active' : '⚠ system python';
    badgeEl.style.background  = found ? 'rgba(166,227,161,0.15)' : 'rgba(249,226,175,0.15)';
    badgeEl.style.color       = found ? 'var(--green)'  : 'var(--yellow)';
    badgeEl.style.borderColor = found ? 'rgba(166,227,161,0.3)' : 'rgba(249,226,175,0.3)';
  }
  if (pathEl) {
    const short = (data.python || '—').split(/[/\\]/).slice(-4).join('/');
    pathEl.textContent = short; pathEl.title = data.python || '';
  }
  const reqs = data.requirements || [];
  if (reqSec) reqSec.style.display = reqs.length ? 'flex' : 'none';
  if (reqFile && reqs.length) {
    const short = reqs[0].split(/[/\\]/).slice(-2).join('/');
    reqFile.textContent  = short; reqFile.title = reqs[0];
    reqFile.dataset.full = reqs[0];
  }
}

async function saveVenvPath() {
  const venv = document.getElementById('venvPathInput').value.trim();
  const res  = await fetch('/api/config/venv-path', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ venv_path: venv }) });
  const d    = await res.json().catch(() => ({}));
  if (d.ok) checkVenvStatus();
  else       alert('Save failed: ' + (d.error || 'unknown error'));
}

async function venvInstallReqs() {
  const repo    = document.getElementById('repo').value.trim();
  const reqFile = document.getElementById('venvReqFile')?.dataset.full || '';
  const btn     = document.getElementById('venvInstallBtn');
  if (btn) { btn.disabled = true; btn.textContent = '⟳ Installing…'; }
  _runContext = 'executor'; _activeLogEl = document.getElementById('log');
  clearLog(); setRunning(true);
  const res = await fetch('/api/venv/install', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo, requirements_file: reqFile }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    appendLine('✗  ' + (err.error || 'Install failed'), 'failed');
    setRunning(false);
  }
  if (btn) { btn.disabled = false; btn.textContent = '▶ pip install -r requirements.txt'; }
}

// ── Reports ───────────────────────────────────────────────────────────────────
let _reports = { individual: null, consolidated: null };

async function refreshReport() {
  const repo = document.getElementById('repo').value.trim();
  if (!repo) return;
  const data = await fetch(`/api/report?repo=${encodeURIComponent(repo)}`).then(r => r.json());
  _reports = { individual: data.individual || null, consolidated: data.consolidated || null };
  _updateReportCard('individual',   _reports.individual,   repo);
  _updateReportCard('consolidated', _reports.consolidated, repo);
}

function _updateReportCard(type, path, repo) {
  const pathEl = document.getElementById(`report${_cap(type)}Path`);
  const btn    = document.getElementById(`open${_cap(type)}Btn`);
  if (path) {
    const rel = path.replace(repo.replace(/\\/g, '/') + '/', '').replace(/\\/g, '/');
    pathEl.textContent = '✓  ' + rel;
    pathEl.className   = 'report-path found';
    btn.disabled       = false;
  } else {
    pathEl.textContent = 'No report found';
    pathEl.className   = 'report-path';
    btn.disabled       = true;
  }
}

function _cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

async function openReport(type) {
  const path = _reports[type];
  if (!path) await refreshReport();
  const p = _reports[type];
  if (!p) { alert('No ' + type + ' report found.'); return; }
  const res = await fetch('/api/report/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: p }) });
  if (!res.ok) { const err = await res.json(); alert('Could not open report: ' + (err.error || 'unknown error')); }
}

async function browseRepo() {
  const btn  = document.querySelector('button[onclick="browseRepo()"]');
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.textContent = '…'; btn.disabled = true; }
  try {
    const current = document.getElementById('repo').value.trim();
    const res  = await fetch('/api/browse-folder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ start: current || '' }) });
    const data = await res.json();
    if (data.cancelled || !data.path) return;
    const p = data.path;
    document.getElementById('repo').value = p;
    const sel = document.getElementById('repoSelect');
    if (!Array.from(sel.options).some(o => o.value === p)) sel.add(new Option('📁 ' + p.split(/[/\\]/).pop(), p));
    sel.value = p;
    applyRepo(p);
  } catch (err) {
    appendLine('✗  Folder picker error: ' + err.message, 'failed');
  } finally {
    if (btn) { btn.textContent = orig; btn.disabled = false; }
  }
}

// ── Features management ────────────────────────────────────────────────────────
let _features = [];

async function loadFeatures() {
  const cfg = await fetch('/api/config').then(r => r.json()).catch(() => ({}));
  _features = cfg.features || [];
  renderFeatures();
}

function renderFeatures() {
  const list = document.getElementById('featList');
  if (!_features.length) { list.innerHTML = '<div class="empty-state">No features yet — click "+ Add Feature" to create one.</div>'; return; }
  const runtimeBadge = { python: 'badge-python', node: 'badge-node', shell: 'badge-shell' };
  list.innerHTML = _features.map((f, i) => {
    const selected = _selectedFeat === i;
    const bCls = runtimeBadge[f.runtime] || 'badge-checkbox';
    return `<div class="feature-card" id="feat-card-${i}" style="border-color:${selected ? 'var(--accent)' : 'var(--border)'};cursor:pointer;" onclick="selectFeat(${i})">
      <div class="feature-card-top"><span class="feature-card-name">${escHtml(f.name || 'Unnamed')}</span><span class="badge ${bCls}">${escHtml(f.runtime || 'python')}</span></div>
      ${f.description ? `<div class="feature-card-desc">${escHtml(f.description)}</div>` : ''}
      <div class="feature-card-script">${escHtml(f.script || '')}</div>
      <div class="feature-card-actions">
        <button type="button" class="btn btn-sm" onclick="event.stopPropagation();editFeat(${i})">Edit</button>
        <button type="button" class="btn btn-sm" style="color:var(--red);border-color:var(--red);" onclick="event.stopPropagation();deleteFeat(${i})">Delete</button>
      </div></div>`;
  }).join('');
}

function selectFeat(i) { _selectedFeat = i; renderFeatures(); const fr = document.getElementById('featRunBtn'); if (fr) fr.disabled = false; }

function showFeatAddForm() {
  document.getElementById('ff_index').value = '-1';
  document.getElementById('featFormTitle').textContent = 'Add Feature';
  ['ff_name','ff_desc','ff_script','ff_cwd','ff_args'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('ff_runtime').value = 'python';
  document.getElementById('featForm').style.display = '';
  document.getElementById('ff_name').focus();
}

function editFeat(i) {
  const f = _features[i];
  document.getElementById('ff_index').value = i;
  document.getElementById('featFormTitle').textContent = 'Edit Feature';
  document.getElementById('ff_name').value    = f.name        || '';
  document.getElementById('ff_desc').value    = f.description || '';
  document.getElementById('ff_runtime').value = f.runtime     || 'python';
  document.getElementById('ff_script').value  = f.script      || '';
  document.getElementById('ff_cwd').value     = f.cwd         || '';
  document.getElementById('ff_args').value    = f.args        || '';
  document.getElementById('featForm').style.display = '';
  document.getElementById('ff_name').focus();
}

function cancelFeatForm() { document.getElementById('featForm').style.display = 'none'; }

async function saveFeatForm() {
  const name   = document.getElementById('ff_name').value.trim();
  const script = document.getElementById('ff_script').value.trim();
  if (!name)   { alert('Name is required.'); return; }
  if (!script) { alert('Script Path is required.'); return; }
  const feat = { name, description: document.getElementById('ff_desc').value.trim(), runtime: document.getElementById('ff_runtime').value, script, cwd: document.getElementById('ff_cwd').value.trim(), args: document.getElementById('ff_args').value.trim() };
  const idx = parseInt(document.getElementById('ff_index').value, 10);
  if (idx >= 0) _features[idx] = feat; else _features.push(feat);
  await persistFeatures(); cancelFeatForm(); renderFeatures();
}

async function deleteFeat(i) {
  if (!confirm(`Delete "${_features[i].name}"?`)) return;
  if (_selectedFeat === i) _selectedFeat = null;
  else if (_selectedFeat > i) _selectedFeat--;
  _features.splice(i, 1);
  await persistFeatures(); renderFeatures();
  const fr = document.getElementById('featRunBtn'); if (fr) fr.disabled = _selectedFeat === null;
}

async function persistFeatures() {
  const res = await fetch('/api/config/features', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ features: _features }) });
  if (!res.ok) { const err = await res.json().catch(() => ({})); alert('Save failed: ' + (err.error || 'unknown error')); }
}

async function runFeature() {
  if (_selectedFeat === null || _selectedFeat === undefined) return;
  const feat = _features[_selectedFeat]; if (!feat) return;
  clearFeatLog(); _runContext = 'features'; _activeLogEl = document.getElementById('features-log'); setRunning(true);
  const res = await fetch('/api/features/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ feature: feat }) });
  if (!res.ok) { const err = await res.json().catch(() => ({})); appendLine('✗  ' + (err.error || 'Failed to start'), 'failed'); _runContext = 'executor'; setRunning(false); }
}

// ── Git Commands ───────────────────────────────────────────────────────────────
let _gitCmds = [];
let _gitRepo  = '';

async function loadGitCommands() {
  const cfg = await fetch('/api/config').then(r => r.json()).catch(() => ({}));
  _gitCmds  = cfg.git_commands || [];
  _gitRepo  = cfg.repo_root    || '';
  const lbl = document.getElementById('gitRepoLabel');
  if (lbl) lbl.textContent = _gitRepo ? `Repo: ${_gitRepo.split(/[\\/]/).pop()}` : 'No repo configured';
  renderGitCommands();
}

function renderGitCommands() {
  const list = document.getElementById('gitCmdList');
  if (!_gitCmds.length) { list.innerHTML = '<div class="empty-state">No git commands yet — click "+ Add Command" to create one.</div>'; return; }
  list.innerHTML = _gitCmds.map((g, i) => `
    <div class="feature-card" style="border-color:var(--border);">
      <div class="feature-card-top">
        <span class="feature-card-name">${escHtml(g.name || 'Unnamed')}</span>
        <span class="badge" style="background:rgba(137,180,250,0.15);color:#89b4fa;border:1px solid rgba(137,180,250,0.3);">git</span>
      </div>
      ${g.description ? `<div class="feature-card-desc">${escHtml(g.description)}</div>` : ''}
      <div class="feature-card-script">${escHtml(g.command || '')}</div>
      <div class="feature-card-actions">
        <button type="button" class="btn btn-run" style="padding:4px 14px;font-size:11px;" onclick="runGitCmd(${i})">▶ Run</button>
        <button type="button" class="btn btn-sm" onclick="editGitCmd(${i})">Edit</button>
        <button type="button" class="btn btn-sm" style="color:var(--red);border-color:var(--red);" onclick="deleteGitCmd(${i})">Delete</button>
      </div></div>`).join('');
}

function showGitAddForm() {
  document.getElementById('gf_index').value = '-1';
  document.getElementById('gitFormTitle').textContent = 'Add Git Command';
  ['gf_name','gf_desc','gf_cmd'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('gitForm').style.display = '';
  document.getElementById('gf_name').focus();
}

function editGitCmd(i) {
  const g = _gitCmds[i];
  document.getElementById('gf_index').value = i;
  document.getElementById('gitFormTitle').textContent = 'Edit Git Command';
  document.getElementById('gf_name').value = g.name         || '';
  document.getElementById('gf_desc').value = g.description  || '';
  document.getElementById('gf_cmd').value  = g.command      || '';
  document.getElementById('gitForm').style.display = '';
  document.getElementById('gf_name').focus();
}

function cancelGitForm() { document.getElementById('gitForm').style.display = 'none'; }

async function saveGitForm() {
  const name = document.getElementById('gf_name').value.trim();
  const cmd  = document.getElementById('gf_cmd').value.trim();
  if (!name) { alert('Name is required.'); return; }
  if (!cmd)  { alert('Git command is required.'); return; }
  const entry = { name, description: document.getElementById('gf_desc').value.trim(), command: cmd };
  const idx   = parseInt(document.getElementById('gf_index').value, 10);
  if (idx >= 0) _gitCmds[idx] = entry; else _gitCmds.push(entry);
  await persistGitCommands(); cancelGitForm(); renderGitCommands();
}

async function deleteGitCmd(i) {
  if (!confirm(`Delete "${_gitCmds[i].name}"?`)) return;
  _gitCmds.splice(i, 1); await persistGitCommands(); renderGitCommands();
}

async function persistGitCommands() {
  const res = await fetch('/api/git/commands', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ git_commands: _gitCmds }) });
  if (!res.ok) { const err = await res.json().catch(() => ({})); alert('Save failed: ' + (err.error || 'unknown error')); }
}

async function runGitCmd(i) {
  const g = _gitCmds[i]; if (!g) return;
  const repo = _gitRepo || (await fetch('/api/config').then(r => r.json()).catch(() => ({}))).repo_root || '';
  // Clear git log and use dedicated 'git' context so output goes to git-log panel
  const gitLogEl = document.getElementById('git-log');
  if (gitLogEl) gitLogEl.innerHTML = '';
  _runContext = 'git';
  _activeLogEl = gitLogEl;  // Pin BEFORE the async fetch so SSE events always find the right div
  setRunning(true);
  const res = await fetch('/api/git/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: g.command, repo }) });
  if (!res.ok) { const err = await res.json().catch(() => ({})); appendLine('✗  ' + (err.error || 'Failed to start'), 'failed'); _runContext = 'executor'; setRunning(false); }
}

// ── Tools management ───────────────────────────────────────────────────────────
let _tools = [];

async function loadTools() {
  const cfg = await fetch('/api/config').then(r => r.json()).catch(() => ({}));
  _tools = cfg.extra_options || [];
  renderTools();
}

function renderTools() {
  const list = document.getElementById('toolsList');
  if (!_tools.length) { list.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🔧</div>No custom tools yet — click "+ Add Tool" to create one.</div>'; return; }
  list.innerHTML = _tools.map((t, i) => {
    const badge = t.type === 'dropdown' ? '<span class="badge badge-dropdown">dropdown</span>' : t.type === 'text' ? '<span class="badge" style="background:#313244;color:#89b4fa;">text</span>' : '<span class="badge badge-checkbox">checkbox</span>';
    const meta  = t.type === 'dropdown' ? `Values: ${(t.values || []).join(', ')} — Default: ${t.default || '(none)'}` : t.type === 'text' ? `Default: "${t.default || ''}"${t.placeholder ? ' — Placeholder: ' + t.placeholder : ''}` : `Default: ${t.default ? 'checked' : 'unchecked'}`;
    const iconMap = { checkbox: '☑', dropdown: '▾', text: '✏' };
    return `<div class="tool-card"><div class="tool-card-icon">${iconMap[t.type] || '⚙'}</div><div class="tool-card-info"><div class="tool-card-label">${escHtml(t.label || '')}${badge}</div><div class="tool-card-flag">${escHtml(t.flag || '')}</div><div class="tool-card-meta">${escHtml(meta)}</div></div><div class="tool-card-actions"><button type="button" class="btn btn-sm" onclick="editTool(${i})">Edit</button><button type="button" class="btn btn-sm" style="color:var(--red);border-color:var(--red);" onclick="deleteTool(${i})">Delete</button></div></div>`;
  }).join('');
}

function showAddForm() {
  document.getElementById('toolFormIndex').value = '-1';
  document.getElementById('toolFormTitle').textContent = 'Add New Tool';
  ['tf_label','tf_flag','tf_default','tf_values','tf_placeholder'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('tf_type').value = 'checkbox';
  onToolTypeChange();
  document.getElementById('toolForm').style.display = '';
  document.getElementById('tf_label').focus();
}

function editTool(i) {
  const t = _tools[i];
  document.getElementById('toolFormIndex').value = i;
  document.getElementById('toolFormTitle').textContent = 'Edit Tool';
  document.getElementById('tf_label').value       = t.label  || '';
  document.getElementById('tf_flag').value        = t.flag   || '';
  document.getElementById('tf_type').value        = t.type   || 'checkbox';
  document.getElementById('tf_default').value     = t.default != null ? String(t.default) : '';
  document.getElementById('tf_values').value      = (t.values || []).join(', ');
  document.getElementById('tf_placeholder').value = t.placeholder || '';
  onToolTypeChange();
  document.getElementById('toolForm').style.display = '';
  document.getElementById('tf_label').focus();
}

function cancelToolForm() { document.getElementById('toolForm').style.display = 'none'; }

function onToolTypeChange() {
  const type = document.getElementById('tf_type').value;
  document.getElementById('tf_values_wrap').style.display      = type === 'dropdown' ? '' : 'none';
  document.getElementById('tf_placeholder_wrap').style.display = type === 'text'     ? '' : 'none';
  const defWrap = document.getElementById('tf_default_wrap');
  defWrap.querySelector('label').textContent = type === 'text' ? 'Default Value' : type === 'checkbox' ? 'Default (true/false)' : 'Default';
}

async function saveToolForm() {
  const label  = document.getElementById('tf_label').value.trim();
  const flag   = document.getElementById('tf_flag').value.trim();
  const type   = document.getElementById('tf_type').value;
  const defRaw = document.getElementById('tf_default').value.trim();
  if (!label) { alert('Label is required.'); return; }
  if (!flag)  { alert('CLI Flag is required.'); return; }
  const tool = { label, flag, type };
  if (type === 'dropdown') {
    tool.values  = document.getElementById('tf_values').value.split(',').map(v => v.trim()).filter(Boolean);
    tool.default = defRaw || '';
  } else if (type === 'text') {
    tool.default     = defRaw || '';
    tool.placeholder = document.getElementById('tf_placeholder').value.trim();
  } else {
    tool.default = defRaw === 'true' || defRaw === '1' || defRaw === 'checked';
  }
  const idx = parseInt(document.getElementById('toolFormIndex').value, 10);
  if (idx >= 0) _tools[idx] = tool; else _tools.push(tool);
  await persistTools(); cancelToolForm(); renderTools();
}

async function deleteTool(i) {
  if (!confirm(`Delete "${_tools[i].label}"?`)) return;
  _tools.splice(i, 1); await persistTools(); renderTools();
}

async function persistTools() {
  const res = await fetch('/api/config/tools', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ extra_options: _tools }) });
  if (!res.ok) { const err = await res.json().catch(() => ({})); alert('Save failed: ' + (err.error || 'unknown error')); }
}

// ── Config sub-tabs ────────────────────────────────────────────────────────────
function switchCfgTab(tab) {
  const panels = { features: 'cfgFeatures', git: 'cfgGit', tools: 'cfgTools', mapping: 'cfgMapping', zephyr: 'cfgZephyr', uitabs: 'cfgUiTabs' };
  Object.entries(panels).forEach(([key, id]) => {
    const panel = document.getElementById(id);
    if (panel) panel.style.display = key === tab ? 'flex' : 'none';
    document.getElementById('ctab-' + key)?.classList.toggle('active', key === tab);
  });
  // Toggle right-side log panel: git tab gets Git Output, everything else gets Feature Output
  const featPanel = document.getElementById('feat-log-panel');
  const gitPanel  = document.getElementById('git-log-panel');
  if (featPanel) featPanel.style.display = tab === 'git' ? 'none' : 'flex';
  if (gitPanel)  gitPanel.style.display  = tab === 'git' ? 'flex' : 'none';

  if (tab === 'mapping') onMappingStepsFormatChange();
  if (tab === 'git')     loadGitCommands();
  if (tab === 'uitabs')  loadUiTabs();
}

// ── UI Tab kill-switch ────────────────────────────────────────────────────────
const _UI_TAB_KEYS = ['dashboard', 'zephyr', 'cfg_git', 'cfg_tools', 'cfg_mapping', 'cfg_zephyr'];

async function loadUiTabs() {
  const cfg  = await fetch('/api/config').then(r => r.json()).catch(() => ({}));
  const tabs = cfg.ui_tabs || {};
  const defaults = { dashboard: true, zephyr: true, cfg_git: true, cfg_tools: true, cfg_mapping: true, cfg_zephyr: true };
  _UI_TAB_KEYS.forEach(key => {
    const el = document.getElementById('uitab_' + key);
    if (el) el.checked = key in tabs ? !!tabs[key] : !!defaults[key];
  });
}

async function saveUiTabs() {
  const tabs = {};
  _UI_TAB_KEYS.forEach(key => {
    const el = document.getElementById('uitab_' + key);
    if (el) tabs[key] = el.checked;
  });
  await fetch('/api/config/ui-tabs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ui_tabs: tabs }),
  });
  // Apply immediately without page reload
  if (typeof applyUiTabs === 'function') applyUiTabs(tabs);
}

function toggleConfigSection(bodyId, arrowId) {
  const body  = document.getElementById(bodyId);
  const arrow = document.getElementById(arrowId);
  if (!body) return;
  if (!body.style.display || body.style.display !== 'none') body.style.display = 'none';
  else body.style.display = '';
  if (arrow) {
    const label = arrow.textContent.replace(/^[▼▶]\s*/, '').replace(/Expand|Configure|Hide/g, '').trim() || 'Expand';
    arrow.textContent = (body.style.display !== 'none') ? '▼ ' + label : '▶ ' + label;
  }
}

function switchMappingTab(tab) {
  ['tc','res'].forEach(t => {
    document.getElementById('vtab-' + t)?.classList.toggle('active', t === tab);
    const panel = document.getElementById('vtab-panel-' + t);
    if (panel) panel.classList.toggle('hidden', t !== tab);
  });
}

// ── Field Mapping Config ───────────────────────────────────────────────────────
let _customFieldRows = [];

function onMappingStepsFormatChange() {
  const fmt = document.getElementById('m_steps_format').value;
  document.getElementById('m_columns_fields').style.display    = fmt !== 'single_col' && fmt !== 'none' ? 'grid'  : 'none';
  document.getElementById('m_single_col_fields').style.display = fmt === 'single_col'                   ? 'block' : 'none';
}

async function loadMappingConfig() {
  const [tc, res] = await Promise.all([
    fetch('/api/zephyr/mapping/testcase').then(r => r.json()).catch(() => ({})),
    fetch('/api/zephyr/mapping/results').then(r => r.json()).catch(() => ({})),
  ]);
  _csvMapping = { ...tc, results: res };
  const s = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ''; };
  s('m_story_id',      tc.story_id            || '');
  s('m_issue_type_id', tc.issue_type_id       || '');
  s('m_summary',       tc.summary             || '');
  s('m_description',   tc.description         || '');
  s('m_priority',      tc.priority            || '');
  s('m_labels',        tc.labels              || '');
  s('m_components',    tc.components          || '');
  s('m_issue_type',    tc.issue_type_name     || 'Test');
  s('m_step_action',   tc.step_action_prefix  || 'Step Action');
  s('m_step_data',     tc.step_data_prefix    || 'Step Data');
  s('m_step_expected', tc.step_expected_prefix|| 'Expected Result');
  s('m_step_single',   tc.step_single_column  || 'Steps');
  const fmtEl = document.getElementById('m_steps_format');
  if (fmtEl) { fmtEl.value = tc.steps_format || 'columns'; onMappingStepsFormatChange(); }
  _customFieldRows = (tc.custom_fields || []).map(r => ({ ...r }));
  _renderCustomFieldRows();
  s('rm_issue_key',   res.issue_key        || 'Issue Key');
  s('rm_status',      res.status           || 'Status');
  s('rm_comment',     res.comment          || 'Comment');
  s('rm_attach_path', res.attachment_path  || 'Attachment Path');
  const stepsEl = document.getElementById('rm_update_steps');
  if (stepsEl) stepsEl.value = res.update_steps !== false ? 'true' : 'false';
}

function _showSaveStatus(id, ok) {
  const el = document.getElementById(id);
  if (el) { el.textContent = ok ? '✓ Saved' : '✗ Failed'; setTimeout(() => { if (el) el.textContent = ''; }, 3000); }
}

function addCustomFieldRow(data = {}) {
  _customFieldRows.push({ csv_col: data.csv_col || '', jira_field: data.jira_field || '', field_type: data.field_type || 'text' });
  _renderCustomFieldRows();
}

function removeCustomFieldRow(idx) { _customFieldRows.splice(idx, 1); _renderCustomFieldRows(); }

function _renderCustomFieldRows() {
  const container = document.getElementById('customFieldRows');
  if (!container) return;
  container.innerHTML = _customFieldRows.map((row, i) => `
    <div style="display:grid;grid-template-columns:1fr 1fr auto auto;gap:6px;align-items:center;">
      <input type="text" placeholder="CSV column header" value="${escHtml(row.csv_col)}" oninput="_customFieldRows[${i}].csv_col=this.value" style="font-size:11px;font-family:'JetBrains Mono',monospace;" />
      <input type="text" placeholder="Jira field (e.g. customfield_10001)" value="${escHtml(row.jira_field)}" oninput="_customFieldRows[${i}].jira_field=this.value" style="font-size:11px;font-family:'JetBrains Mono',monospace;" />
      <select style="font-size:11px;" onchange="_customFieldRows[${i}].field_type=this.value">
        <option value="text"   ${row.field_type==='text'   ?'selected':''}>Text</option>
        <option value="object" ${row.field_type==='object' ?'selected':''}>Select List</option>
        <option value="list"   ${row.field_type==='list'   ?'selected':''}>Multi-value</option>
        <option value="number" ${row.field_type==='number' ?'selected':''}>Number</option>
      </select>
      <button class="btn btn-sm" style="color:var(--red);padding:4px 8px;" onclick="removeCustomFieldRow(${i})">✕</button>
    </div>`).join('');
}

async function saveTcMapping() {
  const v = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const mapping = {
    story_id: v('m_story_id'), summary: v('m_summary') || 'Summary', description: v('m_description'),
    priority: v('m_priority'), labels: v('m_labels'), components: v('m_components'),
    issue_type_name: v('m_issue_type') || 'Test', issue_type_id: v('m_issue_type_id'),
    steps_format: document.getElementById('m_steps_format')?.value || 'columns',
    step_action_prefix: v('m_step_action') || 'Step Action',
    step_data_prefix: v('m_step_data') || 'Step Data',
    step_expected_prefix: v('m_step_expected') || 'Expected Result',
    step_single_column: v('m_step_single') || 'Steps',
    custom_fields: _customFieldRows.filter(r => r.csv_col && r.jira_field),
  };
  _csvMapping = { ..._csvMapping, ...mapping };
  const res = await fetch('/api/zephyr/mapping/testcase', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(mapping) });
  _showSaveStatus('mappingSaveStatus', res.ok);
}

async function saveResultsMapping() {
  const v = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const mapping = {
    issue_key: v('rm_issue_key') || 'Issue Key', status: v('rm_status') || 'Status',
    comment: v('rm_comment') || 'Comment', attachment_path: v('rm_attach_path') || 'Attachment Path',
    update_steps: document.getElementById('rm_update_steps')?.value === 'true',
  };
  const res = await fetch('/api/zephyr/mapping/results', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(mapping) });
  _showSaveStatus('mappingSaveStatus2', res.ok);
}

async function saveMappingConfig() { await saveTcMapping(); await saveResultsMapping(); }

// ── Zephyr config (Config tab panel) ──────────────────────────────────────────
async function loadZephyrConfig() {
  const cfg = await fetch('/api/zephyr/config').then(r => r.json()).catch(() => ({}));
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ''; };
  set('z_jira_url',     cfg.jira_url     || '');
  set('z_username',     cfg.username     || '');
  set('z_api_token',    cfg.api_token    || '');
  set('z_access_key',   cfg.access_key   || '');
  set('z_secret_key',   cfg.secret_key   || '');
  set('z_account_id',   cfg.account_id   || '');
  set('z_project_key',  cfg.project_key  || '');
  set('z_project_name', cfg.project_name || '');
  _zProjectName = cfg.project_name || '';
  const sslEl = document.getElementById('z_verify_ssl');
  if (sslEl) sslEl.checked = cfg.verify_ssl === true;
  _syncProjectDisplay(cfg.project_key || '');
  if (cfg.access_key) {
    const dot = document.getElementById('zConnectStatus');
    if (dot) dot.innerHTML = '<span class="connect-dot" style="background:var(--green);width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:3px;"></span>Configured';
  }
}

async function saveZephyrConfig() {
  const val        = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const projectKey = val('z_project_key').toUpperCase();
  const sslEl      = document.getElementById('z_verify_ssl');
  const cfg = {
    jira_url: val('z_jira_url'), username: val('z_username'), api_token: val('z_api_token'),
    access_key: val('z_access_key'), secret_key: val('z_secret_key'), account_id: val('z_account_id'),
    project_key: projectKey, project_name: val('z_project_name'),
    verify_ssl: sslEl ? sslEl.checked : false,
  };
  try {
    const res  = await fetch('/api/zephyr/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) });
    const data = await res.json();
    if (res.ok && data.ok) { zLog(`✓  Config saved — project: ${projectKey || '(none)'}`, 'passed'); _zProjectName = cfg.project_name; _syncProjectDisplay(projectKey); }
    else zLog('✗  Save failed: ' + JSON.stringify(data), 'failed');
  } catch (err) { zLog('✗  Save error: ' + err.message, 'failed'); }
}

async function testZephyrConnection() {
  const dot = document.getElementById('zConnectStatus');
  if (dot) dot.innerHTML = '<span class="connect-dot" style="background:var(--yellow);width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:3px;"></span>Testing…';
  const res = await fetch('/api/zephyr/projects');
  if (res.ok) {
    const data  = await res.json();
    const count = Array.isArray(data) ? data.length : '?';
    if (dot) dot.innerHTML = '<span class="connect-dot ok"></span> Connected · ' + count + ' projects';
    zLog(`✓  Connection successful — ${count} project(s) found.`, 'passed');
  } else {
    const err = await res.json().catch(() => ({}));
    if (dot) dot.innerHTML = '<span class="connect-dot err"></span> Failed';
    zLog('✗  Connection failed: ' + JSON.stringify(err.error || err), 'failed');
  }
}
