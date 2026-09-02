// ── Test Management (Zephyr) JS ───────────────────────────────────────────────

let _zProject     = '';
let _zProjectId   = '';
let _zProjectName = '';
let _zVersionId   = '-1';
let _zCycleId     = '';
let _zFolderId    = '';
let _zFile        = null;
let _csvMapping   = {};
let _csvHeaders   = [];
let _createFile   = null;
let _importKeys   = [];
let _filterIssues = [];
let _executions   = [];
let _resultsCsvRows    = [];
let _resultsCsvHeaders = [];
const _zFiles = {};

// ── Zephyr sub-tabs ────────────────────────────────────────────────────────────
function switchZTab(tab) {
  const tabs = ['import', 'results', 'executions'];
  tabs.forEach(t => {
    const btn   = document.getElementById('ztab-' + t);
    const panel = document.getElementById('zpanel-' + t);
    const active = t === tab;
    if (btn)   btn.classList.toggle('active', active);
    if (panel) panel.style.display = active ? 'flex' : 'none';
  });
  _updateZephyrContextBanners();
}

function _syncProjectDisplay(key) {
  _zProject = (key || '').trim().toUpperCase();
  const el = document.getElementById('z_project_display');
  if (el) { el.textContent = _zProject || '— not configured —'; el.style.color = _zProject ? 'var(--accent)' : 'var(--text-dim)'; }
  updateZephyrBreadcrumb();
}

async function onZephyrProjectChange() {}

async function loadZephyrVersionsAndCycles() {
  if (!_zProject) { zLog('✗  No project key — set it in Config → Zephyr.', 'failed'); return; }
  zLog(`⟳  Loading versions for ${_zProject}…`, 'info');
  const vRes = await fetch(`/api/zephyr/versions?projectKey=${encodeURIComponent(_zProject)}`);
  if (vRes.ok) {
    const versions = await vRes.json();
    const arr = Array.isArray(versions) ? versions : (versions.values || []);
    if (arr.length && arr[0].projectId) _zProjectId = String(arr[0].projectId);
    const sel = document.getElementById('z_version');
    const sortedVersions = [...arr].reverse();
    sel.innerHTML = '<option value="-1">Unscheduled</option>' + sortedVersions.map(v => `<option value="${escHtml(String(v.id))}">${escHtml(v.name)}</option>`).join('');
    zLog(`✓  Loaded ${arr.length} version(s) — newest first.`, 'passed');
  } else { zLog('⚠  Could not load versions — will use Unscheduled.', 'warning'); }
  await loadZephyrCycles();
}

async function onZephyrVersionChange() {
  _zVersionId = document.getElementById('z_version').value || '-1';
  _zCycleId = ''; _zFolderId = '';
  updateZephyrBreadcrumb(); await loadZephyrCycles();
}

function onZephyrCycleChange() {
  _zCycleId  = document.getElementById('z_cycle').value;
  _zFolderId = '';
  updateZephyrBreadcrumb(); _updateZephyrContextBanners();
}

async function loadZephyrCycles() {
  if (!_zProject) { zLog('✗  No project key — set it in Config → Zephyr.', 'failed'); return; }
  const versionId = (document.getElementById('z_version')?.value) || _zVersionId || '-1';
  zLog(`⟳  Loading cycles for ${_zProject} / version ${versionId}…`, 'info');
  const pRes = await fetch(`/api/zephyr/versions?projectKey=${encodeURIComponent(_zProject)}`);
  let projectId = '';
  if (pRes.ok) { const pData = await pRes.json(); const arr = Array.isArray(pData) ? pData : (pData.values || []); if (arr.length) projectId = String(arr[0].projectId || ''); }
  const params = new URLSearchParams({ versionId });
  if (projectId) params.set('projectId', projectId);
  const res = await fetch(`/api/zephyr/cycles?${params}`);
  if (!res.ok) { const e = await res.json().catch(() => ({})); zLog('✗  Cycles error: ' + JSON.stringify(e.error || e), 'failed'); return; }
  const data = await res.json();
  let cycles = [];
  if (Array.isArray(data)) { cycles = data; }
  else if (data && typeof data === 'object') {
    cycles = Object.entries(data).filter(([k]) => k !== 'recordsCount' && k !== 'offset' && !isNaN(Number(k)) === false).map(([id, c]) => ({ id, name: c.name || id, ...c })).filter(c => c.name);
  }
  const sel = document.getElementById('z_cycle');
  sel.innerHTML = '<option value="">— select cycle —</option>' + cycles.map(c => `<option value="${escHtml(String(c.id))}">${escHtml(c.name)}</option>`).join('');
  zLog(`✓  Loaded ${cycles.length} cycle(s).`, 'passed');
  document.getElementById('zFolderTree').innerHTML = '<div style="color:var(--text-dim);font-size:11px;padding:6px;">Select a cycle then click ⟳</div>';
}

async function loadZephyrFolders() {
  if (!_zCycleId) { zLog('✗  Select a test cycle first.', 'failed'); return; }
  const versionId = (document.getElementById('z_version')?.value) || _zVersionId || '-1';
  const params    = new URLSearchParams({ cycleId: _zCycleId, versionId });
  if (_zProjectId) params.set('projectId', _zProjectId);
  zLog('⟳  Loading folders…', 'info');
  const res = await fetch(`/api/zephyr/folders?${params}`);
  if (!res.ok) { const e = await res.json().catch(() => ({})); zLog('✗  Folders error: ' + JSON.stringify(e.error || e), 'failed'); return; }
  const data    = await res.json();
  const folders = Array.isArray(data) ? data : (data.values || []);
  renderFolderTree(folders);
  zLog(`✓  Loaded ${folders.length} folder(s).`, 'passed');
}

function renderFolderTree(folders) {
  const tree = document.getElementById('zFolderTree');
  if (!folders.length) { tree.innerHTML = '<div style="color:var(--text-dim);font-size:11px;padding:4px;">No folders found</div>'; return; }
  const byId = {}, roots = [];
  folders.forEach(f => { byId[f.id] = { ...f, children: [] }; });
  folders.forEach(f => { if (f.parentId && byId[f.parentId]) byId[f.parentId].children.push(byId[f.id]); else roots.push(byId[f.id]); });
  tree.innerHTML = roots.map(f => renderFolderNode(f, 0)).join('');
}

function renderFolderNode(f, depth) {
  const indent   = depth * 14;
  const children = f.children.map(c => renderFolderNode(c, depth + 1)).join('');
  return `<div class="folder-item" id="zf_${f.id}" onclick="selectZephyrFolder('${f.id}','${escHtml(f.name)}')" style="padding-left:${8+indent}px;">
    <span class="folder-icon">${f.children.length ? '📂' : '📁'}</span><span>${escHtml(f.name)}</span></div>
    ${children ? '<div class="folder-children">' + children + '</div>' : ''}`;
}

function selectZephyrFolder(id, name) {
  _zFolderId = id;
  document.querySelectorAll('.folder-item').forEach(el => el.classList.remove('selected'));
  const el = document.getElementById('zf_' + id); if (el) el.classList.add('selected');
  updateZephyrBreadcrumb(); _updateZephyrContextBanners();
  zLog(`✓  Selected folder: ${name} (id: ${id})`, 'passed');
}

async function createZephyrCycle() {
  if (!_zProject) { zLog('✗  No project configured — set Project Key in Config.', 'failed'); return; }
  const name = document.getElementById('z_new_cycle').value.trim();
  if (!name) { zLog('✗  Enter a cycle name.', 'failed'); return; }
  const body = { name, projectId: _zProjectId ? parseInt(_zProjectId) : _zProject, versionId: parseInt(_zVersionId) || -1, description: '' };
  zLog(`⟳  Creating cycle "${name}"…`, 'info');
  const res  = await fetch('/api/zephyr/cycle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { zLog('✗  Create cycle failed: ' + JSON.stringify(data.error || data), 'failed'); return; }
  zLog(`✓  Cycle created: ${data.name || name} (id: ${data.id || '?'})`, 'passed');
  document.getElementById('z_new_cycle').value = '';
  await loadZephyrCycles();
  if (data.id) { const sel = document.getElementById('z_cycle'); if (sel) { sel.value = String(data.id); onZephyrCycleChange(); } }
}

async function createZephyrFolder() {
  if (!_zProject) { zLog('✗  No project configured.', 'failed'); return; }
  if (!_zCycleId)  { zLog('✗  Select a test cycle first (Step 3).', 'failed'); return; }
  const name = document.getElementById('z_new_folder').value.trim();
  if (!name) { zLog('✗  Enter a folder name.', 'failed'); return; }
  const body = { name, cycleId: _zCycleId, projectId: _zProjectId ? parseInt(_zProjectId) : _zProject, versionId: parseInt(_zVersionId) || -1, description: '' };
  if (_zFolderId) body.clonedFolderId = String(_zFolderId);
  zLog(`⟳  Creating folder "${name}"…`, 'info');
  const res  = await fetch('/api/zephyr/folder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { zLog('✗  Create folder failed: ' + JSON.stringify(data.error || data), 'failed'); return; }
  zLog(`✓  Folder created: ${data.name || name} (id: ${data.id || '?'})`, 'passed');
  document.getElementById('z_new_folder').value = '';
  await loadZephyrFolders();
  if (data.id) selectZephyrFolder(String(data.id), data.name || name);
}

function updateZephyrBreadcrumb() {
  _updateResultsContext();
  const bc = document.getElementById('zBreadcrumb');
  const parts = [];
  if (_zProject) parts.push(`<span class="crumb">Project: ${escHtml(_zProject)}</span>`);
  if (_zCycleId) {
    const cycleEl   = document.getElementById('z_cycle');
    const cycleName = cycleEl.options[cycleEl.selectedIndex]?.text || _zCycleId;
    parts.push('<span>›</span>', `<span class="crumb">Cycle: ${escHtml(cycleName)}</span>`);
  }
  if (_zFolderId) {
    const folderEl   = document.getElementById('zf_' + _zFolderId);
    const folderName = folderEl ? folderEl.querySelector('span:last-child').textContent : _zFolderId;
    parts.push('<span>›</span>', `<span class="crumb">Folder: ${escHtml(folderName)}</span>`);
  }
  if (bc) bc.innerHTML = parts.length ? parts.join(' ') : 'Select project, cycle and folder above';
}

function _updateZephyrContextBanners() {
  const vSel = document.getElementById('z_version');
  const cSel = document.getElementById('z_cycle');
  const fEl  = _zFolderId ? document.getElementById('zf_' + _zFolderId) : null;
  const vName = (vSel && vSel.selectedIndex >= 0 && vSel.options[vSel.selectedIndex]?.value !== '') ? vSel.options[vSel.selectedIndex].text : '';
  const cName = (_zCycleId && cSel && cSel.selectedIndex >= 0) ? cSel.options[cSel.selectedIndex]?.text || _zCycleId : '';
  const fName = _zFolderId ? (fEl?.querySelector('span:last-child')?.textContent || _zFolderId) : '';
  const projDisplay = _zProjectName ? `${_zProjectName} (${_zProject || ''})` : (_zProject || '');
  const chip = (label, value, color) => `<span style="font-size:10px;color:var(--text-dim);margin-right:2px;">${label}:</span><span style="font-weight:700;color:${color};margin-right:8px;">${escHtml(value)}</span>`;
  const warn = (msg) => `<span style="color:var(--yellow);font-size:11px;">⚠ ${msg}</span>`;
  let html;
  if (!_zCycleId) {
    html = (projDisplay ? chip('Project', projDisplay, 'var(--purple)') : '') + warn('No cycle selected — use Steps 2 &amp; 3 on the left: Version → Cycle → Folder');
  } else {
    html = '<span style="color:var(--text-dim);font-size:10px;margin-right:6px;">TARGET</span>' +
           (projDisplay ? chip('Project', projDisplay, 'var(--purple)') : '') +
           (vName ? chip('Version', vName, 'var(--text-muted)') : '') +
           chip('Cycle', cName || _zCycleId, 'var(--accent)') +
           (fName ? chip('Folder', fName, 'var(--teal)') : warn('No folder — tests go to cycle root'));
  }
  ['zImportContext', 'zResultsContext', 'zExecContext'].forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = html; });
}

function _updateResultsContext() { _updateZephyrContextBanners(); }

// ── Import mode helpers ────────────────────────────────────────────────────────
function setImportMode(mode) {
  ['keys','create','filter'].forEach(m => {
    document.getElementById('import-mode-' + m).style.display = m === mode ? 'flex' : 'none';
    document.getElementById('imode-' + m)?.classList.toggle('active', m === mode);
  });
}

function setFilterSubMode(sub) {
  document.getElementById('filter-sub-filter').style.display = sub === 'filter' ? '' : 'none';
  document.getElementById('filter-sub-jql').style.display    = sub === 'jql'    ? '' : 'none';
  document.getElementById('fsub-filter')?.classList.toggle('active', sub === 'filter');
  document.getElementById('fsub-jql')?.classList.toggle('active', sub === 'jql');
}

function onIssueKeysChange() {
  const raw  = document.getElementById('z_issue_keys').value;
  const keys = raw.split('\n').map(k => k.trim()).filter(Boolean);
  _importKeys = keys.map(k => ({ key: k.toUpperCase(), summary: '' }));
  const btn = document.getElementById('zAddTestsBtn'); if (btn) btn.disabled = keys.length === 0;
  renderImportPreview(); if (keys.length) fetchImportSummaries();
}

async function onImportCsvLoaded(file) {
  const text = await file.text();
  const lines = text.split('\n').filter(l => l.trim());
  const headers = _parseCsvLine(lines[0]);
  const keyCol = headers.findIndex(h => /issue.?key|test.?id|jira.?id|key/i.test(h));
  if (keyCol < 0) { zLog('⚠  No Issue Key column found in CSV', 'warning'); return; }
  _importKeys = lines.slice(1).map(l => _parseCsvLine(l)[keyCol]?.trim()).filter(Boolean).map(k => ({ key: k, summary: '' }));
  document.getElementById('z_issue_keys').value = _importKeys.map(r => r.key).join('\n');
  renderImportPreview(); fetchImportSummaries();
}

async function fetchImportSummaries() {
  if (!_importKeys.length) return;
  const countEl = document.getElementById('zKeysCount');
  if (countEl) countEl.textContent = `${_importKeys.length} issue(s) — fetching summaries…`;
  const jql = `issueKey in (${_importKeys.map(r => r.key).join(',')})`;
  try {
    const res  = await fetch(`/api/jira/search?jql=${encodeURIComponent(jql)}&maxResults=${_importKeys.length}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.issues) { const byKey = {}; data.issues.forEach(i => { byKey[i.key] = i.fields?.summary || ''; }); _importKeys.forEach(r => { if (byKey[r.key]) r.summary = byKey[r.key]; }); }
  } catch (_) {}
  renderImportPreview();
}

function renderImportPreview() {
  const preview = document.getElementById('zKeysPreview');
  const tbody   = document.getElementById('zKeysTbody');
  const btn     = document.getElementById('zAddTestsBtn');
  if (!_importKeys.length) { preview.style.display = 'none'; btn.disabled = true; return; }
  document.getElementById('zKeysCount').textContent = `${_importKeys.length} issue(s)`;
  tbody.innerHTML = _importKeys.map((r, i) => `<tr><td style="color:var(--text-dim);font-size:10px;">${i+1}</td><td style="font-family:'JetBrains Mono',monospace;font-weight:600;color:var(--accent);">${escHtml(r.key)}</td><td style="font-size:11px;color:var(--text-muted);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(r.summary)}">${escHtml(r.summary||'—')}</td></tr>`).join('');
  preview.style.display = 'flex';
  btn.disabled = false;
  btn.title    = _zCycleId ? '' : 'Select a Test Cycle (Step 3) before adding';
}

async function zCreateTestcasesGrouped() {
  if (!_createFile || !_zProject) return;
  if (!_zCycleId) { zLog('✗  Select a test cycle first.', 'failed'); return; }
  const btn = document.getElementById('zCreateTestsBtn');
  btn.disabled = true; btn.textContent = 'Creating…';
  const form = new FormData();
  form.append('file', _createFile); form.append('projectKey', _zProject);
  form.append('cycleId', _zCycleId); form.append('folderId', _zFolderId || '');
  form.append('versionId', _zVersionId || '-1');
  form.append('linkType', document.getElementById('z_link_type')?.value.trim() || 'Tests');
  zLog(`⟳  Creating test cases from "${_createFile.name}"…`, 'info');
  try {
    const res  = await fetch('/api/zephyr/import-testcases-grouped', { method: 'POST', body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { zLog(`✗  Server error (${res.status}): ${JSON.stringify(data?.error || data)}`, 'failed'); btn.disabled = false; btn.textContent = 'Create Test Cases by Story → Add to Cycle'; return; }
    if (data._debug) { const d = data._debug; zLog(`  ℹ  Project resolved: id=${d.proj_id}  IssueType id=${d.issue_type_id||'NOT FOUND'}`, 'info'); if (d.issueTypes?.length) zLog(`  ℹ  Available types: ${d.issueTypes.map(t=>t.name).join(', ')}`, 'info'); }
    zLog(`✓  Created: ${data.created}  Errors: ${data.errors}  Skipped: ${data.skipped}`, data.errors ? 'warning' : 'passed');
    Object.entries(data.folders || {}).forEach(([story, f]) => zLog(`  📁 ${story} → folder id=${f.id} (${f.created ? 'NEW' : 'existing'})`, 'info'));
    (data.details?.created || []).slice(0, 30).forEach(t => {
      const stepStatus = t.steps_ok === t.steps ? '✓' : '⚠';
      zLog(`  ✓ ${t.key}  "${t.summary}"  steps=${t.steps_ok}/${t.steps} ${stepStatus}  enrolled=${t.enrolled}`, t.steps_ok < t.steps ? 'warning' : 'passed');
      if (t.step_details) t.step_details.filter(s => !s.ok).forEach(s => zLog(`     ✗ Step ${s.order} (${s.code}): ${s.error?.slice(0,200)}`, 'failed'));
    });
    (data.details?.errors || []).forEach(e => {
      const errDetail = e.error?.errorMessages?.join(' ') || JSON.stringify(e.error?.errors || e.error).slice(0, 400);
      zLog(`  ✗ [${e.story||''}] ${e.summary||'folder'}: ${errDetail}`, 'failed');
      if (e.sent_fields) zLog(`     Sent to Jira: ${JSON.stringify(e.sent_fields)}`, 'warning');
    });
    if (data.errors > 0) zLog('  ℹ  Check: correct Issue Type name in Field Mapping? Correct project permissions?', 'warning');
  } catch (err) { zLog('✗  Network error: ' + err.message, 'failed'); }
  btn.disabled = false; btn.textContent = 'Create Test Cases by Story → Add to Cycle';
}

async function zFetchFilterIssues() {
  const filterId = document.getElementById('z_filter_id').value.trim();
  const jql      = document.getElementById('z_jql').value.trim();
  const maxRes   = document.getElementById('z_filter_max').value || '100';
  const statusEl = document.getElementById('zFilterStatus');
  if (!filterId && !jql) { zLog('✗  Enter a Filter ID or JQL query.', 'failed'); return; }
  statusEl.textContent = '⟳ Loading…';
  const params = new URLSearchParams({ maxResults: maxRes });
  if (filterId) params.set('filterId', filterId); else params.set('jql', jql);
  const res  = await fetch('/api/jira/search?' + params);
  const data = await res.json();
  if (!res.ok) { statusEl.textContent = '✗ Failed'; zLog(`✗  Jira search failed (${res.status}): ${data?.error?.errorMessages?.join(' ') || JSON.stringify(data?.error || data).slice(0,200)}`, 'failed'); return; }
  _filterIssues = data.issues || [];
  statusEl.textContent = `✓ ${_filterIssues.length} issue(s) found`;
  zLog(`✓  Fetched ${_filterIssues.length} issue(s) from Jira.`, 'passed');
  _renderFilterTable();
  document.getElementById('zAddFilterBtn').disabled = !_filterIssues.length;
}

function _renderFilterTable() {
  const tbody = document.getElementById('zFilterTbody');
  document.getElementById('zFilterCount').textContent = `${_filterIssues.length} issues`;
  document.getElementById('zFilterSelectAll').checked = false;
  const TYPE_COLOR = { Bug:'var(--red)', Story:'var(--green)', Task:'var(--accent)', Test:'var(--purple)' };
  tbody.innerHTML = _filterIssues.map((issue, i) => {
    const type = issue.fields?.issuetype?.name || '—'; const status = issue.fields?.status?.name || '—'; const col = TYPE_COLOR[type] || 'var(--text-muted)';
    return `<tr><td style="width:28px;"><input type="checkbox" class="z-filter-cb" data-idx="${i}" checked onchange="zFilterUpdateBtn()" /></td><td style="font-family:'JetBrains Mono',monospace;font-weight:600;color:var(--accent);font-size:11px;">${escHtml(issue.key)}</td><td style="font-size:11px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(issue.fields?.summary||'')}">${escHtml(issue.fields?.summary||'')}</td><td style="font-size:10px;color:${col};font-weight:600;">${escHtml(type)}</td><td style="font-size:10px;color:var(--text-dim);">${escHtml(status)}</td></tr>`;
  }).join('');
  document.getElementById('zFilterPreview').style.display = 'flex';
}

function zFilterToggleAll(cb) { document.querySelectorAll('.z-filter-cb').forEach(c => c.checked = cb.checked); zFilterUpdateBtn(); }

function zFilterUpdateBtn() {
  const checked = document.querySelectorAll('.z-filter-cb:checked').length;
  const btn = document.getElementById('zAddFilterBtn');
  btn.disabled  = !checked || !_zCycleId;
  btn.textContent = checked ? `Add ${checked} Selected to Cycle` : 'Add Selected to Cycle';
}

async function zAddFilterTests() {
  const checked = [...document.querySelectorAll('.z-filter-cb:checked')];
  const keys = checked.map(c => _filterIssues[parseInt(c.dataset.idx)]?.key).filter(Boolean);
  if (!keys.length || !_zCycleId) return;
  const btn = document.getElementById('zAddFilterBtn'); btn.disabled = true; btn.textContent = 'Adding…';
  zLog(`⟳  Adding ${keys.length} issue(s) from Jira filter in chunks of 50…`, 'info');
  const baseBody = { method: 1, cycleId: _zCycleId, projectId: _zProjectId || _zProject, versionId: parseInt(_zVersionId) || -1, assigneeType: 'currentUser' };
  const { added, errors } = await _addKeysToTarget(keys, baseBody);
  zLog(`✓  Done — ${added} added, ${errors} errors`, errors ? 'warning' : 'passed');
  btn.disabled = false; zFilterUpdateBtn();
}

// ── File helpers ───────────────────────────────────────────────────────────────
function onZFileSelect(e, infoId, callback) {
  const f = e.target.files[0]; if (!f) return;
  _zFiles[e.target.id] = f; _showZFileInfo(infoId, e.target.id, f); if (callback) callback(f);
}

function onZDrop(e, fileId, infoId, callback) {
  e.preventDefault(); e.currentTarget.classList.remove('drag-over');
  const f = e.dataTransfer.files[0]; if (!f) return;
  _zFiles[fileId] = f; document.getElementById(fileId).value = '';
  _showZFileInfo(infoId, fileId, f); if (callback) callback(f);
}

function _showZFileInfo(infoId, fileId, file) {
  const info = document.getElementById(infoId); if (!info) return;
  const nameEl = info.querySelector('span:nth-child(2)');
  if (nameEl) nameEl.textContent = `${file.name}  (${(file.size/1024).toFixed(1)} KB)`;
  info.classList.add('visible');
  const dz = document.getElementById(fileId)?.closest('.drop-zone'); if (dz) dz.style.display = 'none';
}

function clearZFile(fileId, infoId) {
  delete _zFiles[fileId];
  const el = document.getElementById(fileId); if (el) el.value = '';
  const info = document.getElementById(infoId); if (info) info.classList.remove('visible');
  const dz = document.getElementById(fileId)?.closest('.drop-zone'); if (dz) dz.style.display = '';
}

function onZCreateDrop(e) { e.preventDefault(); document.getElementById('zCreateDropZone').classList.remove('drag-over'); const f = e.dataTransfer.files[0]; if (f) { _createFile = f; _showCreateFile(); previewCreateCsv(f); } }
function onZCreateFileSelect(e) { const f = e.target.files[0]; if (f) { _createFile = f; _showCreateFile(); previewCreateCsv(f); } }
function _showCreateFile() { if (!_createFile) return; document.getElementById('zCreateFileName').textContent = `${_createFile.name}  (${(_createFile.size/1024).toFixed(1)} KB)`; document.getElementById('zCreateFileInfo').classList.add('visible'); document.getElementById('zCreateDropZone').style.display = 'none'; }
function clearZCreateFile() { _createFile = null; document.getElementById('zCreateFile').value = ''; document.getElementById('zCreateFileInfo').classList.remove('visible'); document.getElementById('zCreateDropZone').style.display = ''; document.getElementById('zMappingPreview').style.display = 'none'; document.getElementById('zCreateTestsBtn').disabled = true; }

async function previewCreateCsv(file) {
  const form = new FormData(); form.append('file', file);
  const res  = await fetch('/api/zephyr/csv-preview', { method: 'POST', body: form });
  if (!res.ok) { zLog('✗  Could not parse CSV', 'failed'); return; }
  const data = await res.json(); _csvHeaders = data.headers || []; const preview = data.preview || [];
  const m = _csvMapping;
  const mappedFields = new Set([m.summary, m.description, m.priority, m.labels, m.components].filter(Boolean));
  const stepPfxs = [m.step_action_prefix, m.step_data_prefix, m.step_expected_prefix, m.step_single_column].filter(Boolean);
  const isStep = h => stepPfxs.some(p => h.startsWith(p));
  const thead = document.getElementById('zPreviewHead');
  thead.innerHTML = _csvHeaders.map(h => { const bg = mappedFields.has(h) ? 'background:rgba(137,180,250,0.2);color:var(--accent);' : isStep(h) ? 'background:rgba(249,226,175,0.15);color:var(--yellow);' : ''; return `<th style="font-size:10px;padding:5px 8px;${bg}">${escHtml(h)}</th>`; }).join('');
  document.getElementById('zPreviewBody').innerHTML = preview.map(row => '<tr>' + _csvHeaders.map(h => { const bg = mappedFields.has(h) ? 'background:rgba(137,180,250,0.06);' : isStep(h) ? 'background:rgba(249,226,175,0.05);' : ''; return `<td style="${bg}font-size:11px;padding:4px 8px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(row[h]||'')}">${escHtml(row[h]||'')}</td>`; }).join('') + '</tr>').join('');
  const mapped = _csvHeaders.filter(h => mappedFields.has(h) || isStep(h)).length;
  document.getElementById('zMappingStatus').textContent = `${_csvHeaders.length} columns, ${mapped} mapped, ${preview.length} preview rows`;
  document.getElementById('zMappingPreview').style.display = 'flex';
  document.getElementById('zCreateTestsBtn').disabled = !_zProject;
}

// ── Results mode ───────────────────────────────────────────────────────────────
function setResultsMode(mode) {
  document.getElementById('results-mode-csv').style.display    = mode === 'csv'    ? 'flex' : 'none';
  document.getElementById('results-mode-single').style.display = mode === 'single' ? 'flex' : 'none';
  document.querySelectorAll('.import-mode-btn[id^="rmode"]').forEach(b => b.classList.remove('active'));
  document.getElementById('rmode-' + mode)?.classList.add('active');
  _updateResultsContext();
}

async function onResultsCsvSelected(file) {
  const form = new FormData(); form.append('file', file);
  const res  = await fetch('/api/zephyr/csv-preview', { method: 'POST', body: form });
  if (!res.ok) { zLog('✗  Could not parse results CSV', 'failed'); return; }
  const data = await res.json(); _resultsCsvHeaders = data.headers || [];
  const text  = await file.text(); const lines = text.split('\n').filter(l => l.trim());
  const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g,''));
  _resultsCsvHeaders = headers;
  _resultsCsvRows = lines.slice(1).map(line => { const vals = _parseCsvLine(line); const row = {}; headers.forEach((h, i) => row[h] = vals[i] || ''); return row; }).filter(r => Object.values(r).some(v => v));
  _renderResultsCsvPreview();
  document.getElementById('zResultsPreview').style.display = 'flex';
  document.getElementById('zUploadResultsBtn').disabled = !_zCycleId;
  document.getElementById('zResultsPreviewInfo').textContent = `${_resultsCsvRows.length} rows · ${_resultsCsvHeaders.length} columns`;
}

function _parseCsvLine(line) {
  const result = []; let cur = ''; let inQ = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') { inQ = !inQ; } else if (c === ',' && !inQ) { result.push(cur.trim()); cur = ''; } else { cur += c; }
  }
  result.push(cur.trim()); return result;
}

function _renderResultsCsvPreview() {
  const resCfg = { issue_key: 'Issue Key', status: 'Status', comment: 'Comment' };
  const colKey = resCfg.issue_key, colStat = resCfg.status, colCom = resCfg.comment;
  const STAT_COLOR = { pass:'var(--green)', passed:'var(--green)', fail:'var(--red)', failed:'var(--red)', wip:'var(--yellow)', blocked:'var(--accent)', unexecuted:'var(--text-dim)' };
  const head = document.getElementById('zResultsCsvHead'); const body = document.getElementById('zResultsCsvBody');
  head.innerHTML = _resultsCsvHeaders.map(h => { const hi = [colKey,colStat,colCom].includes(h) ? 'background:rgba(137,180,250,0.15);color:var(--accent);' : h.toLowerCase().includes('attach') ? 'background:rgba(148,226,213,0.12);color:var(--teal);' : ''; return `<th style="font-size:10px;padding:5px 10px;${hi}">${escHtml(h)}</th>`; }).join('');
  body.innerHTML = _resultsCsvRows.map(row => {
    const status = (row[colStat] || '').toLowerCase(); const color = STAT_COLOR[status] || 'var(--text-muted)';
    return '<tr>' + _resultsCsvHeaders.map(h => { const v = row[h] || ''; if (h === colStat) return `<td style="padding:4px 10px;"><span style="font-size:10px;font-weight:700;color:${color};">${escHtml(v)}</span></td>`; if (h === colKey) return `<td style="padding:4px 10px;font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:600;color:var(--accent);">${escHtml(v)}</td>`; return `<td style="padding:4px 10px;font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(v)}">${escHtml(v)}</td>`; }).join('') + '</tr>';
  }).join('');
}

function clearZResultsCsv() {
  _resultsCsvRows = []; _resultsCsvHeaders = [];
  clearZFile('zResultsFile','zResultsFileInfo');
  document.getElementById('zResultsPreview').style.display = 'none';
  document.getElementById('zUploadResultsBtn').disabled = true;
}

async function zUploadResults() {
  if (!_zCycleId) { zLog('✗  Select a test cycle first.', 'failed'); return; }
  const csvFile = _zFiles['zResultsFile']; if (!csvFile) { zLog('✗  Upload a results CSV first.', 'failed'); return; }
  const btn = document.getElementById('zUploadResultsBtn'); btn.disabled = true; btn.textContent = 'Uploading…';
  const form = new FormData();
  form.append('file', csvFile); form.append('cycleId', _zCycleId); form.append('folderId', _zFolderId || '');
  form.append('projectId', _zProjectId || _zProject || ''); form.append('versionId', _zVersionId || '-1');
  const bulkStatus = document.getElementById('z_bulk_status')?.value; if (bulkStatus) form.append('bulkStatus', bulkStatus);
  const attachFile = _zFiles['zAttachFile']; if (attachFile) form.append('attachment', attachFile);
  zLog(`⟳  Uploading results from "${csvFile.name}" to cycle (${_resultsCsvRows.length} rows)…`, 'info');
  try {
    const res  = await fetch('/api/zephyr/bulk-results', { method: 'POST', body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { zLog(`✗  API error (${res.status}): ${JSON.stringify(data.error || data)}`, 'failed'); btn.disabled = false; btn.textContent = 'Upload Results to Cycle'; return; }
    const s = data.success || 0, e = data.errors || 0, nf = data.not_found || 0;
    zLog(`✓  Processed ${data.processed} rows — Success: ${s}  Errors: ${e}  Not found: ${nf}`, (e || nf) ? 'warning' : 'passed');
    (data.details?.success || []).forEach(r => {
      const stepMsg = r.steps?.length ? `  📝 ${r.steps.length} step(s) → ${r.steps.filter(s=>s.updated).length} updated` : '';
      const attachMsg = r.attached?.length ? `  📎 ${r.attached.join(', ')}` : '';
      zLog(`  ✓ Row ${r.row}: ${r.issue} → ${r.status}${stepMsg}${attachMsg}${r.attach_error ? '  ⚠ attach: ' + r.attach_error : ''}`, 'passed');
    });
    (data.details?.errors    || []).forEach(r => zLog(`  ✗ Row ${r.row}: ${r.issue} — ${r.error}`, 'failed'));
    (data.details?.not_found || []).forEach(r => zLog(`  ⚠ Row ${r.row}: ${r.issue} — ${r.error}`, 'warning'));
  } catch (err) { zLog('✗  ' + err.message, 'failed'); }
  btn.disabled = false; btn.textContent = 'Upload Results to Cycle';
}

async function zUploadSingle() {
  const issueKey = document.getElementById('z_single_key').value.trim();
  const statusId = parseInt(document.getElementById('z_single_status').value);
  const comment  = document.getElementById('z_single_comment').value.trim();
  const statusNames = {1:'Pass',2:'Fail',3:'WIP',4:'Blocked','-1':'Unexecuted'};
  if (!issueKey)  { zLog('✗  Enter a Test ID / Issue Key.', 'failed'); return; }
  if (!_zCycleId) { zLog('✗  Select a test cycle first.', 'failed'); return; }
  zLog(`⟳  Updating ${issueKey}…`, 'info');
  const statusStr = {1:'pass',2:'fail',3:'wip',4:'blocked','-1':'unexecuted'}[statusId] || 'pass';
  const csvContent = `Issue Key,Status,Comment\n${issueKey},${statusStr},"${comment.replace(/"/g,'""')}"`;
  const form = new FormData();
  form.append('file', new Blob([csvContent], {type:'text/csv'}), 'single.csv');
  form.append('cycleId', _zCycleId); form.append('folderId', _zFolderId || '');
  form.append('projectId', _zProjectId || _zProject || ''); form.append('versionId', _zVersionId || '-1');
  const attachFile = _zFiles['zSingleAttach']; if (attachFile) form.append('attachment', attachFile);
  try {
    const res  = await fetch('/api/zephyr/bulk-results', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) { zLog('✗  ' + JSON.stringify(data.error || data), 'failed'); return; }
    const r = data.details?.success?.[0];
    if (r) {
      zLog(`✓  ${issueKey} → ${statusNames[statusId] || statusId}`, 'passed');
      if (r.steps?.length) { zLog(`  📝 ${r.steps.length} test step(s) found — ${r.steps.filter(s=>s.updated).length} updated to ${statusNames[statusId]}:`, 'info'); r.steps.forEach(step => zLog(`     ${step.updated ? '✓' : '✗'} Step ${step.orderId || ''}`, step.updated ? 'passed' : 'warning')); }
      else zLog('  ℹ  No Zephyr test steps found for this test case.', 'info');
      if (r.attached?.length) zLog(`  📎 Attached: ${r.attached.join(', ')}`, 'passed');
      if (r.attach_error)     zLog(`  ⚠  Attachment error: ${r.attach_error}`, 'warning');
    } else if (data.details?.not_found?.length) { zLog(`✗  "${issueKey}" not found in the selected cycle/folder.`, 'failed'); }
    else if (data.details?.errors?.length)     { zLog(`✗  ${data.details.errors[0].error}`, 'failed'); }
  } catch (err) { zLog('✗  ' + err.message, 'failed'); }
}

// ── Executions ─────────────────────────────────────────────────────────────────
async function zLoadExecutions() {
  if (!_zCycleId) { zLog('✗  Select a test cycle first.', 'failed'); return; }
  _updateZephyrContextBanners(); zLog('⟳  Loading executions…', 'info');
  const params = { versionId: _zVersionId || '-1' }; if (_zProjectId) params.projectId = _zProjectId;
  const url = _zFolderId ? `/api/zephyr/executions?folderId=${_zFolderId}&cycleId=${_zCycleId}&${new URLSearchParams(params)}` : `/api/zephyr/executions?cycleId=${_zCycleId}&${new URLSearchParams(params)}`;
  const res  = await fetch(url); const data = await res.json().catch(() => ({}));
  if (!res.ok) { zLog('✗  ' + JSON.stringify(data.error || data), 'failed'); return; }
  _executions = data.searchObjectList || data.executions || [];
  const countEl = document.getElementById('zExecCount'); if (countEl) countEl.textContent = `${_executions.length} execution(s)`;
  zLog(`✓  Loaded ${_executions.length} execution(s).`, 'passed');
  _renderExecRows(_executions);
  const activeFilter = document.getElementById('z_exec_status_filter')?.value; if (activeFilter) zFilterExecTable();
}

function zFilterExecTable() {
  const filter = document.getElementById('z_exec_status_filter')?.value || '';
  _renderExecRows(filter ? _executions.filter(e => { const execObj = e.execution || {}; const statusObj = execObj.status || e.status || {}; return String(statusObj.id ?? e.executionStatus ?? -1) === filter; }) : _executions);
}

function _renderExecRows(list) {
  const STAT_CLS  = { '1':'passed','2':'failed','3':'broken','4':'cancelled','-1':'' };
  const STAT_ICON = { '1':'✓','2':'✗','3':'⋯','4':'⊘','-1':'—' };
  document.getElementById('zExecTbody').innerHTML = list.length
    ? list.map((e, i) => {
        const execObj = e.execution || {}; const statusObj = execObj.status || e.status || {};
        const sName = statusObj.name || e.executionStatus || '—'; const sid = String(statusObj.id ?? e.executionStatus ?? -1);
        const summary = e.issueSummary || e.label || e.summary || '';
        const execId  = execObj.id || e.id || e.executionId || '';
        const issueId = e.issueId || execObj.issueId || 0;
        const origIdx = _executions.indexOf(e);
        return `<tr><td style="width:32px;"><input type="checkbox" class="z-exec-cb" data-idx="${origIdx}" /></td><td style="font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:600;color:var(--accent);">${escHtml(e.issueKey||'')}</td><td style="font-size:11px;max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(summary)}">${escHtml(summary||'—')}</td><td><span class="tbl-status-badge ${STAT_CLS[sid]||''}" style="font-size:10px;">${STAT_ICON[sid]||''} ${sName}</span></td><td style="white-space:nowrap;"><button class="btn btn-sm" style="font-size:10px;padding:3px 8px;" onclick="zQuickMark('${execId}',${issueId})">Mark</button></td></tr>`;
      }).join('')
    : '<tr><td colspan="5" style="text-align:center;color:var(--text-dim);padding:16px;">No executions match the filter</td></tr>';
  document.getElementById('zBulkMarkBtn').disabled = !list.length;
  document.getElementById('zSelectAll').checked    = false;
}

function zToggleAll(cb) { document.querySelectorAll('.z-exec-cb').forEach(c => c.checked = cb.checked); }

async function _markExecsByKey(issueKeys) {
  if (!issueKeys.length || !_zCycleId) return;
  const statusId  = parseInt(document.getElementById('z_mark_status').value) || 1;
  const comment   = document.getElementById('z_mark_comment')?.value.trim() || '';
  const statusStr = {1:'pass',2:'fail',3:'wip',4:'blocked','-1':'unexecuted'}[statusId] || 'pass';
  const csv = ['Issue Key,Status,Comment', ...issueKeys.map(k => `${k},${statusStr},"${comment.replace(/"/g,'""')}"`)].join('\n');
  const form = new FormData();
  form.append('file', new Blob([csv], { type: 'text/csv' }), 'mark.csv');
  form.append('cycleId', _zCycleId); form.append('folderId', _zFolderId || '');
  form.append('projectId', _zProjectId || _zProject || ''); form.append('versionId', _zVersionId || '-1');
  zLog(`⟳  Marking ${issueKeys.length} test(s) as ${statusStr}…`, 'info');
  try {
    const res  = await fetch('/api/zephyr/bulk-results', { method: 'POST', body: form });
    const data = await res.json();
    if (res.ok) {
      zLog(`✓  ${data.success}/${data.processed} updated${data.not_found ? `  ⚠ ${data.not_found} not found` : ''}`, (data.errors || data.not_found) ? 'warning' : 'passed');
      (data.details?.success    || []).forEach(r => { const stepsOk = (r.steps||[]).filter(s=>s.updated).length; if (stepsOk) zLog(`  📝 ${r.issue}: ${stepsOk} step(s) → ${statusStr}`, 'passed'); });
      (data.details?.errors     || []).forEach(r => zLog(`  ✗ ${r.issue}: ${r.error}`, 'failed'));
      (data.details?.not_found  || []).forEach(r => zLog(`  ⚠  ${r.issue}: ${r.error}`, 'warning'));
    } else { zLog('✗  Mark failed: ' + JSON.stringify(data.error || data), 'failed'); }
  } catch (err) { zLog('✗  ' + err.message, 'failed'); }
  await zLoadExecutions();
}

async function zQuickMark(execId, issueId) {
  const exec = _executions.find(e => (e.execution?.id || e.id || e.executionId) === execId || e.issueId == issueId);
  await _markExecsByKey([exec?.issueKey || String(issueId)]);
}

async function zBulkMark() {
  const checked = [...document.querySelectorAll('.z-exec-cb:checked')];
  if (!checked.length) { zLog('✗  Select at least one execution.', 'failed'); return; }
  const keys = checked.map(c => _executions[parseInt(c.dataset.idx)]?.issueKey || '').filter(Boolean);
  const btn  = document.getElementById('zBulkMarkBtn'); btn.disabled = true; btn.textContent = 'Marking…';
  await _markExecsByKey(keys);
  btn.disabled = false; btn.textContent = 'Mark Selected Executions';
}

// ── Zephyr log ─────────────────────────────────────────────────────────────────
function zLog(msg, cls) {
  const log  = document.getElementById('zephyrLog');
  const span = document.createElement('span'); span.className = 'log-line log-' + (cls || 'default'); span.textContent = msg;
  log.appendChild(span); log.scrollTop = log.scrollHeight;
}
function clearZephyrLog() { document.getElementById('zephyrLog').innerHTML = ''; }

// ── Chunk / add helpers ────────────────────────────────────────────────────────
function _chunks(arr, size = 50) { const out = []; for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size)); return out; }

async function _addKeysToTarget(keys, baseBody) {
  const chunks = _chunks(keys, 50); let added = 0, errors = 0;
  for (const chunk of chunks) {
    const body = { ...baseBody, issues: chunk };
    if (_zFolderId) body.folderId = _zFolderId;
    const res  = await fetch('/api/zephyr/executions/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (res.ok || data?.ok) { added += chunk.length; zLog(`  ✓ Chunk ${chunks.indexOf(chunk)+1}/${chunks.length}: added ${chunk.length} test(s)`, 'passed'); }
    else { errors += chunk.length; zLog(`  ✗ Chunk ${chunks.indexOf(chunk)+1}/${chunks.length} failed: ${data?.error?.message || data?.error || data?.raw || JSON.stringify(data)}`, 'failed'); }
  }
  return { added, errors };
}

async function zAddTests() {
  if (!_zProject) { zLog('✗  No project key configured — go to Config → Zephyr Config and set Project Key.', 'failed'); return; }
  if (!_zCycleId) { zLog('✗  No test cycle selected — use Step 3 on the left panel.', 'failed'); switchZTab('import'); return; }
  const btn = document.getElementById('zAddTestsBtn'); btn.disabled = true; btn.textContent = 'Adding…';
  let keys = _importKeys.length ? _importKeys.map(r => r.key) : document.getElementById('z_issue_keys').value.trim().split('\n').map(k => k.trim()).filter(Boolean);
  if (!keys.length) { zLog('✗  No issue keys found.', 'failed'); btn.disabled = false; btn.textContent = 'Add Tests to Cycle'; return; }
  zLog(`⟳  Adding ${keys.length} test(s) in chunks of 50…`, 'info');
  const baseBody = { method: 1, cycleId: _zCycleId, projectId: _zProjectId || _zProject, versionId: parseInt(_zVersionId) || -1, assigneeType: document.getElementById('z_assignee_type')?.value || 'currentUser' };
  const { added, errors } = await _addKeysToTarget(keys, baseBody);
  zLog(`✓  Done — ${added} added, ${errors} errors`, errors ? 'warning' : 'passed');
  btn.disabled = false; btn.textContent = 'Add Tests to Cycle';
}

async function _zApiCall(method, path, params = {}, body = null) {
  const qs = Object.keys(params).length ? '?' + new URLSearchParams(params) : '';
  return fetch('/api/zephyr/proxy-raw', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ method, path, params, body }) });
}
