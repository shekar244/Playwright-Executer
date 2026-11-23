// ── Grid tab: the New run test picker and the collapsible sections ────────────────
// Loaded after grid.js (uses its _gEsc/_gList/_gFetch helpers and _gridRepoPath/_gridTestList/_gridCountTests).

// ── Test picker: folders → files → tests ──────────────────────────────────────
// The tree comes from /api/grid/test-tree (read with ast on the server). A file whose every test is ticked is sent as
// the file path; otherwise each ticked test as its node id. Lines typed in the text box that are not in the tree are
// kept as they are. Built with DOM calls: names come from the repo and are never parsed as HTML.

let _gridTree = null;                 // {name, path, type, children | tests}
let _gridTreeRepo = '';               // the repo path the tree was read from
const _gridSel = new Map();           // file path → Set of ticked test names ('*' = the whole file, when it lists none)
const _gridOpen = new Set();          // expanded folder / file paths
let _gridExtra = [];                  // typed lines that are not in the tree

function _gridFiles(node) {
  if (!node) return [];
  return node.type === 'file' ? [node] : _gList(node.children).flatMap(_gridFiles);
}

function _gridFileState(file) {
  const sel = _gridSel.get(file.path);
  if (!sel || !sel.size) return 'none';
  const tests = _gList(file.tests);
  if (sel.has('*') || (tests.length && tests.every(t => sel.has(t)))) return 'all';
  return 'some';
}

function _gridNodeState(node) {
  const states = _gridFiles(node).map(_gridFileState);
  if (!states.length || states.every(x => x === 'none')) return 'none';
  return states.every(x => x === 'all') ? 'all' : 'some';
}

function _gridSetFile(file, on) {
  if (!on) { _gridSel.delete(file.path); return; }
  const tests = _gList(file.tests);
  _gridSel.set(file.path, new Set(tests.length ? tests : ['*']));
}

function _gridSelectedIds() {
  const ids = _gridFiles(_gridTree).flatMap(f => {
    const state = _gridFileState(f);
    if (state === 'all') return [f.path];
    if (state === 'some') return _gList(f.tests).filter(t => _gridSel.get(f.path).has(t)).map(t => `${f.path}::${t}`);
    return [];
  });
  return ids.concat(_gridExtra.filter(x => !ids.includes(x)));
}

function _gridTestCountOf(ids) {
  const files = new Map(_gridFiles(_gridTree).map(f => [f.path, f]));
  return ids.reduce((n, id) => n + (files.has(id) ? Math.max(1, _gList(files.get(id).tests).length) : 1), 0);
}

function _gridSyncText() {
  const ids = _gridSelectedIds();
  document.getElementById('gridTests').value = ids.join('\n');
  _gridCountTests();
}

// Typed lines → the tree's ticks (and the rest kept as extra lines).
function _gridSelFromText() {
  const files = new Map(_gridFiles(_gridTree).map(f => [f.path, f]));
  _gridSel.clear();
  _gridExtra = [];
  _gridTestList().forEach(line => {
    const [path, ...rest] = line.split('::');
    const file = files.get(path);
    const name = rest.join('::');
    if (file && !name) _gridSetFile(file, true);
    else if (file && _gList(file.tests).includes(name)) {
      if (!_gridSel.has(path)) _gridSel.set(path, new Set());
      _gridSel.get(path).add(name);
    } else _gridExtra.push(line);
  });
}

function _gridMatches(node, filter) {
  if (!filter) return true;
  if (node.path.toLowerCase().includes(filter)) return true;
  if (node.type === 'file') return _gList(node.tests).some(t => t.toLowerCase().includes(filter));
  return _gList(node.children).some(c => _gridMatches(c, filter));
}

function _gridCheckbox(state, onchange) {
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.checked = state === 'all';
  box.indeterminate = state === 'some';
  box.addEventListener('change', onchange);
  return box;
}

function _gridTreeNode(node, filter) {
  const li = document.createElement('li');
  const row = document.createElement('div');
  row.className = 'g-node';
  const open = filter ? true : _gridOpen.has(node.path);
  const caret = document.createElement('span');
  caret.className = 'g-caret';
  const hasKids = node.type === 'dir' || _gList(node.tests).length > 0;
  caret.textContent = hasKids ? (open ? '▾' : '▸') : '';
  caret.addEventListener('click', () => { _gridOpen.has(node.path) ? _gridOpen.delete(node.path) : _gridOpen.add(node.path); _gridRenderTree(); });
  const box = _gridCheckbox(node.type === 'file' ? _gridFileState(node) : _gridNodeState(node), (e) => {
    (node.type === 'file' ? [node] : _gridFiles(node)).forEach(f => _gridSetFile(f, e.target.checked));
    _gridSyncText(); _gridRenderTree();
  });
  const name = document.createElement('span');
  name.className = 'g-name';
  name.textContent = node.type === 'dir' ? `📁 ${node.name}` : `🧪 ${node.name}`;
  const count = document.createElement('span');
  count.className = 'g-count';
  const files = _gridFiles(node);
  const tests = files.reduce((n, f) => n + _gList(f.tests).length, 0);
  count.textContent = node.type === 'dir' ? `${files.length} files · ${tests} tests` : `${_gList(node.tests).length}`;
  row.append(caret, box, name, count);
  li.append(row);
  if (open && hasKids) {
    const ul = document.createElement('ul');
    if (node.type === 'dir') {
      _gList(node.children).filter(c => _gridMatches(c, filter)).forEach(c => ul.append(_gridTreeNode(c, filter)));
    } else {
      const sel = _gridSel.get(node.path);
      _gList(node.tests).filter(t => !filter || node.path.toLowerCase().includes(filter) || t.toLowerCase().includes(filter)).forEach(t => {
        const tli = document.createElement('li');
        const trow = document.createElement('div');
        trow.className = 'g-node g-test';
        const spacer = document.createElement('span');
        spacer.className = 'g-caret';
        const tbox = _gridCheckbox(sel && (sel.has(t) || sel.has('*')) ? 'all' : 'none', (e) => {
          if (!_gridSel.has(node.path) || _gridSel.get(node.path).has('*')) _gridSel.set(node.path, new Set());
          const s2 = _gridSel.get(node.path);
          e.target.checked ? s2.add(t) : s2.delete(t);
          if (!s2.size) _gridSel.delete(node.path);
          _gridSyncText(); _gridRenderTree();
        });
        const tname = document.createElement('span');
        tname.className = 'g-name';
        tname.textContent = t;
        trow.append(spacer, tbox, tname);
        tli.append(trow);
        ul.append(tli);
      });
    }
    li.append(ul);
  }
  return li;
}

function _gridRenderTree() {
  const box = document.getElementById('gridTree');
  if (!_gridTree) return;
  const filter = document.getElementById('gridTreeFilter').value.trim().toLowerCase();
  const scroll = box.scrollTop;
  const ul = document.createElement('ul');
  if (_gridMatches(_gridTree, filter)) ul.append(_gridTreeNode(_gridTree, filter));
  box.replaceChildren(ul);
  box.scrollTop = scroll;
}

async function gridLoadTree(force) {
  const repo = _gridRepoPath();
  if (!force && _gridTree && repo === _gridTreeRepo) return;
  const box = document.getElementById('gridTree');
  const label = document.getElementById('gridTreeRepo');
  box.textContent = 'Loading the repository’s tests…';
  try {
    const data = await _gFetch('/api/grid/test-tree' + (repo ? `?repo=${encodeURIComponent(repo)}` : ''));
    _gridTree = data.tree;
    _gridTreeRepo = repo;
    label.textContent = `— ${data.repo}@${String(data.sha || '').slice(0, 7)}${data.dirty ? ' (uncommitted changes are not sent)' : ''}`;
    if (!_gridOpen.size) _gridOpen.add(_gridTree.path);      // the top folder starts open
    _gridSelFromText();                                       // keep what was already chosen
    _gridRenderTree();
  } catch (e) {
    _gridTree = null;
    label.textContent = '';
    box.textContent = `${e.message}`;
  }
}

function _gridTreeAction(action) {
  if (action === 'reload') { gridLoadTree(true); return; }
  if (!_gridTree) return;
  if (action === 'expand') {
    const walk = (n) => { if (n.type === 'dir' || _gList(n.tests).length) _gridOpen.add(n.path); _gList(n.children).forEach(walk); };
    walk(_gridTree);
  } else if (action === 'collapse') {
    _gridOpen.clear();
    _gridOpen.add(_gridTree.path);
  } else if (action === 'clear') {
    _gridSel.clear();
    _gridExtra = [];
    _gridSyncText();
  }
  _gridRenderTree();
}

// Kept for older bookmarks / callers: the tree replaces the old "Load from repo" list.
function gridLoadFromRepo() { gridLoadTree(true); }

// ── Collapsible sections (expanded by default; remembered in this browser) ─────
const _GRID_COLLAPSED_KEY = 'amplify.grid.collapsed';

function _gridCollapsed() {
  try { return new Set(JSON.parse(localStorage.getItem(_GRID_COLLAPSED_KEY) || '[]')); } catch (e) { return new Set(); }
}

function _gridToggleSection(title) {
  const card = title.closest('.dash-card');
  if (!card) return;
  const collapsed = card.classList.toggle('g-collapsed');
  _gridFoldButton(title);
  const saved = _gridCollapsed();
  collapsed ? saved.add(title.dataset.gridCollapse) : saved.delete(title.dataset.gridCollapse);
  try { localStorage.setItem(_GRID_COLLAPSED_KEY, JSON.stringify([...saved])); } catch (e) { /* private mode */ }
}

function _gridFoldButton(title) {
  let btn = title.querySelector(':scope > [data-grid-fold]');
  if (!btn) {
    btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-sm g-fold';
    btn.dataset.gridFold = '';
    title.append(btn);
  }
  const collapsed = title.closest('.dash-card').classList.contains('g-collapsed');
  btn.textContent = collapsed ? '+' : '−';
  btn.title = collapsed ? 'Expand' : 'Collapse';
}

function _gridApplyCollapsed() {
  const saved = _gridCollapsed();
  document.querySelectorAll('#page-grid [data-grid-collapse]').forEach(t => {
    const card = t.closest('.dash-card');
    if (!card) return;
    card.classList.toggle('g-collapsed', saved.has(t.dataset.gridCollapse));
    _gridFoldButton(t);
  });
}
