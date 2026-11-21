// ── Repository tab ────────────────────────────────────────────────────────────
//
// The active repo (repo_root: what the Executor runs and the Grid snapshots), the user's other clones, cloning.
// Everything shown comes from git (names, URLs, commit messages, authors), so it is set with textContent / DOM
// calls only, never innerHTML. Access tokens go to the server and never come back (has_token only).

let _repoWs = null;

async function _repoFetch(url, opts) {
  const resp = await fetch(url, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

const _repoPost = (url, body) => _repoFetch(url, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
});

function _repoEl(tag, props, children) {
  const el = Object.assign(document.createElement(tag), props || {});
  (children || []).forEach(c => el.append(c));
  return el;
}

function _repoMsg(id, text, kind) {
  const el = document.getElementById(id);
  el.textContent = text || '';
  el.className = 'repo-msg' + (kind ? ' ' + kind : '');
}

function _repoWhen(iso) {
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d) ? d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '';
}

function _repoTokenBadge(has) {
  return _repoEl('span', { className: 'repo-badge' + (has ? ' on' : ''), textContent: has ? 'saved' : 'none' });
}

// Other tabs read the active repo from the Executor's (hidden) #repo field: keep it in step.
function _repoPublishActive(path, name) {
  const field = document.getElementById('repo');
  if (field) field.value = path || '';
  const sub = document.getElementById('repoSubtitle');
  if (sub) sub.textContent = name ? '📁 ' + name : '';
  if (typeof discoverTests === 'function' && path) discoverTests();
}

function _repoRenderActive(ws) {
  const active = (ws.repos || []).find(r => r.active);
  document.getElementById('repoActiveEmpty').style.display = active ? 'none' : '';
  document.getElementById('repoActive').style.display = active ? '' : 'none';
  document.getElementById('repoActButtons').style.display = active ? '' : 'none';
  if (!active) return;
  document.getElementById('repoActName').textContent = active.name;
  const dirty = document.getElementById('repoActDirty');
  dirty.replaceChildren(active.dirty ? _repoEl('span', { className: 'repo-badge warn', textContent: 'uncommitted changes',
    title: 'The Grid runs the committed state only' }) : '');
  document.getElementById('repoActRemote').textContent = active.remote || '(no remote)';
  const tok = document.getElementById('repoActToken');
  tok.replaceChildren(_repoTokenBadge(active.has_token), ' ',
    _repoEl('button', { className: 'btn btn-sm', textContent: active.has_token ? 'Replace' : 'Set',
      onclick: () => repoSetToken(active.name) }));
  if (active.has_token) {
    tok.append(' ', _repoEl('button', { className: 'btn btn-sm', textContent: 'Remove', onclick: () => repoClearToken(active.name) }));
  }
  const h = active.head || {};
  document.getElementById('repoActSha').textContent = (h.sha || '').slice(0, 7);
  document.getElementById('repoActSubject').textContent = h.subject || '';
  document.getElementById('repoActWho').textContent = [h.author, _repoWhen(h.date)].filter(Boolean).join(' · ');
  document.getElementById('repoActPath').textContent = active.path;
  const sel = document.getElementById('repoBranchSelect');
  if (!sel.options.length || sel.dataset.repo !== active.name) {
    sel.replaceChildren(_repoEl('option', { value: active.branch, textContent: active.branch || '(detached)' }));
    sel.dataset.repo = active.name;
  }
  _repoPublishActive(active.path, active.name);
}

function _repoRenderList(ws) {
  const body = document.getElementById('repoTableBody');
  body.replaceChildren();
  (ws.repos || []).forEach(r => {
    const h = r.head || {};
    const actions = _repoEl('div', { style: 'display:flex;gap:6px;justify-content:flex-end;' });
    if (!r.active) actions.append(_repoEl('button', { className: 'btn btn-sm btn-accent', textContent: 'Use', onclick: () => repoUse(r.name) }));
    actions.append(
      _repoEl('button', { className: 'btn btn-sm', textContent: 'Pull', onclick: () => repoPull(r.name) }),
      _repoEl('button', { className: 'btn btn-sm', textContent: r.has_token ? 'Token…' : 'Set token', onclick: () => repoSetToken(r.name) }),
      _repoEl('button', { className: 'btn btn-sm', textContent: 'Remove', title: 'Delete this clone from your workspace', onclick: () => repoRemove(r.name) }));
    const name = _repoEl('div', {}, [_repoEl('strong', { textContent: r.name })]);
    if (r.active) name.append(' ', _repoEl('span', { className: 'repo-badge on', textContent: 'active' }));
    if (r.dirty) name.append(' ', _repoEl('span', { className: 'repo-badge warn', textContent: 'changes' }));
    body.append(_repoEl('tr', {}, [
      _repoEl('td', {}, [name]),
      _repoEl('td', { className: 'repo-mono', textContent: r.branch || '—' }),
      _repoEl('td', { className: 'repo-mono', textContent: r.remote || '—' }),
      _repoEl('td', { title: [h.author, _repoWhen(h.date)].filter(Boolean).join(' · ') }, [
        _repoEl('span', { className: 'repo-mono', textContent: (h.sha || '').slice(0, 7) }), ' ', _repoEl('span', { textContent: h.subject || '' })]),
      _repoEl('td', {}, [_repoTokenBadge(r.has_token)]),
      _repoEl('td', {}, [actions]),
    ]));
  });
  (ws.saved || []).filter(s => !s.cloned).forEach(s => {
    body.append(_repoEl('tr', {}, [
      _repoEl('td', {}, [_repoEl('strong', { textContent: s.name }), ' ', _repoEl('span', { className: 'repo-badge', textContent: 'saved, not cloned' })]),
      _repoEl('td', { className: 'repo-mono', textContent: s.branch || 'default' }),
      _repoEl('td', { className: 'repo-mono', textContent: s.url }),
      _repoEl('td', { textContent: '—' }),
      _repoEl('td', {}, [_repoTokenBadge(s.has_token)]),
      _repoEl('td', {}, [_repoEl('button', { className: 'btn btn-sm btn-accent', textContent: 'Clone', onclick: () => repoCloneSaved(s.name) })]),
    ]));
  });
  if (!body.children.length) {
    body.append(_repoEl('tr', {}, [_repoEl('td', { colSpan: 6, className: 'repo-msg', textContent: 'No repositories yet — clone one above.' })]));
  }
  const loc = ws.location || {};
  document.getElementById('repoLocation').textContent = loc.path
    ? `Stored per user in ${loc.path} — ${loc.storage || ''}` : '';
}

async function loadRepository() {
  try {
    _repoWs = await _repoFetch('/api/repo/workspace');
  } catch (e) {
    _repoMsg('repoListMsg', e.message, 'err');
    return;
  }
  _repoMsg('repoListMsg', '');
  _repoRenderActive(_repoWs);
  _repoRenderList(_repoWs);
  if ((_repoWs.repos || []).some(r => r.active)) repoLoadBranches(false);
}

async function repoLoadBranches(announce) {
  const sel = document.getElementById('repoBranchSelect');
  if (announce) _repoMsg('repoActMsg', 'Fetching branches…');
  try {
    const d = await _repoFetch('/api/repo/branches');
    sel.replaceChildren(...(d.branches || []).map(b => _repoEl('option', { value: b, textContent: b, selected: b === d.current })));
    if (announce) _repoMsg('repoActMsg', `${(d.branches || []).length} branches`, 'ok');
  } catch (e) {
    if (announce) _repoMsg('repoActMsg', e.message, 'err');
  }
}

async function repoSwitchBranch() {
  const branch = document.getElementById('repoBranchSelect').value;
  if (!branch) return;
  _repoMsg('repoActMsg', `Switching to ${branch}…`);
  try {
    await _repoPost('/api/repo/checkout', { branch });
    _repoMsg('repoActMsg', `On ${branch}`, 'ok');
    loadRepository();
    if (typeof loadRepoStatus === 'function') loadRepoStatus();
  } catch (e) {
    _repoMsg('repoActMsg', e.message, 'err');
  }
}

async function repoPull(name) {
  const msgId = name ? 'repoListMsg' : 'repoActMsg';
  _repoMsg(msgId, 'Pulling…');
  try {
    const d = await _repoPost('/api/repo/pull', name ? { name } : {});
    _repoMsg(msgId, (d.output || 'Up to date').split('\n').pop(), 'ok');
    loadRepository();
  } catch (e) {
    _repoMsg(msgId, e.message, 'err');
  }
}

async function repoUse(name) {
  try {
    await _repoPost('/api/repo/use', { name });
    _repoMsg('repoListMsg', `${name} is now the active repository`, 'ok');
    document.getElementById('repoBranchSelect').replaceChildren();
    await loadRepository();
    if (typeof loadRepoStatus === 'function') loadRepoStatus();
  } catch (e) {
    _repoMsg('repoListMsg', e.message, 'err');
  }
}

async function repoSetToken(name) {
  const token = prompt(`Access token (PAT) for ${name}.\nIt is saved for this repository and never shown again.`);
  if (!token || !token.trim()) return;
  try {
    await _repoPost('/api/repo/token', { name, token: token.trim() });
    _repoMsg('repoListMsg', `Access token saved for ${name}`, 'ok');
    loadRepository();
  } catch (e) {
    _repoMsg('repoListMsg', e.message, 'err');
  }
}

async function repoClearToken(name) {
  if (!confirm(`Remove the saved access token of ${name}?`)) return;
  try {
    await _repoPost('/api/repo/token', { name, clear_token: true });
    loadRepository();
  } catch (e) {
    _repoMsg('repoListMsg', e.message, 'err');
  }
}

async function repoRemove(name) {
  if (!confirm(`Delete the clone of ${name} from your workspace?\nUncommitted changes in it are lost. Its saved URL and token stay.`)) return;
  try {
    const d = await _repoPost('/api/repo/remove', { name });
    _repoMsg('repoListMsg', `Removed ${name}` + (d.was_active ? ' — choose another active repository' : ''), 'ok');
    loadRepository();
  } catch (e) {
    _repoMsg('repoListMsg', e.message, 'err');
  }
}

async function repoFetchRemoteBranches() {
  const url = document.getElementById('repoCloneUrl').value.trim();
  if (!url) { _repoMsg('repoCloneMsg', 'Enter the Git URL first', 'err'); return; }
  const sel = document.getElementById('repoCloneBranch');
  _repoMsg('repoCloneMsg', 'Listing branches…');
  try {
    const d = await _repoPost('/api/repo/branches', { url, token: document.getElementById('repoCloneToken').value.trim() });
    sel.replaceChildren(_repoEl('option', { value: '', textContent: 'default' }),
      ...(d.branches || []).map(b => _repoEl('option', { value: b, textContent: b })));
    _repoMsg('repoCloneMsg', `${(d.branches || []).length} branches`, 'ok');
  } catch (e) {
    _repoMsg('repoCloneMsg', e.message, 'err');
  }
}

async function _repoDoClone(body) {
  const btn = document.getElementById('repoCloneBtn');
  btn.disabled = true;
  _repoMsg('repoCloneMsg', 'Cloning…');
  try {
    const d = await _repoPost('/api/repo/clone', body);
    _repoMsg('repoCloneMsg', d.action === 'cloned' ? `Cloned ${d.name}` : `Pulled ${d.name}`, 'ok');
    document.getElementById('repoCloneToken').value = '';
    document.getElementById('repoBranchSelect').replaceChildren();
    await loadRepository();
    if (typeof loadRepoStatus === 'function') loadRepoStatus();
  } catch (e) {
    _repoMsg('repoCloneMsg', e.message, 'err');
  } finally {
    btn.disabled = false;
  }
}

function repoClone() {
  const url = document.getElementById('repoCloneUrl').value.trim();
  if (!url) { _repoMsg('repoCloneMsg', 'Enter the Git URL', 'err'); return; }
  _repoDoClone({ url, branch: document.getElementById('repoCloneBranch').value,
    token: document.getElementById('repoCloneToken').value.trim() });
}

function repoCloneSaved(name) {
  _repoDoClone({ name });
}
