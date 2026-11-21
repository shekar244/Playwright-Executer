let _tdFiles = [];
let _tdInitialized = false;
let _tdSelectedFolder = '.';
let _tdExpandedFolders = new Set(['.']);

function _tdFormatBytes(size) {
  if (!size) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = size;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index++;
  }
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

function _tdSetStatus(message, isError) {
  const el = document.getElementById('tdStatus');
  if (!el) return;
  el.textContent = message;
  el.style.color = isError ? 'var(--red)' : 'var(--green)';
}

function _tdSetSelected(files) {
  _tdFiles = Array.from(files || []);
  const selected = document.getElementById('tdSelected');
  const button = document.getElementById('tdUploadBtn');
  if (!selected || !button) return;
  selected.textContent = _tdFiles.length
    ? _tdFiles.map(file => file.name).join(', ')
    : 'Drop files here or choose files from your computer';
  button.disabled = _tdFiles.length === 0;
}

async function _tdLoad() {
  const folder = _tdSelectedFolder || '.';
  const response = await fetch(`/api/testdata/tree?folder=${encodeURIComponent(folder)}`);
  const data = await response.json().catch(() => ({ error: 'Could not load Test Data' }));
  if (!response.ok) {
    _tdSetStatus(data.error || 'Could not load Test Data', true);
    return;
  }

  _tdSelectedFolder = data.folder || '.';
  _tdRenderFolderTree(data.folders || []);
  const current = document.getElementById('tdCurrentFolder');
  if (current) current.textContent = _tdSelectedFolder === '.' ? 'Repository root' : _tdSelectedFolder;

  const rows = document.getElementById('tdFileRows');
  if (!rows) return;
  const files = data.files || [];
  rows.innerHTML = files.length ? files.map(file => `
    <tr>
      <td style="font-family:var(--mono);">${escHtml(file.name)}</td>
      <td>${escHtml(_tdFormatBytes(file.size))}</td>
      <td>${escHtml(file.modified ? new Date(file.modified * 1000).toLocaleString() : '')}</td>
      <td><button type="button" class="btn btn-sm td-delete" data-name="${escHtml(file.name)}">Delete</button></td>
    </tr>
  `).join('') : '<tr><td colspan="4" class="hint">This folder has no files.</td></tr>';
  _tdSetStatus(`${data.repo || 'Repository'}: ${files.length} file(s)`, false);
}

function _tdRenderFolderTree(folders) {
  const tree = document.getElementById('tdFolderTree');
  if (!tree) return;
  tree.innerHTML = '';

  const root = { path: '.', name: 'Repository root', children: [] };
  const nodes = new Map([[ '.', root ]]);

  (folders || [])
    .filter(folderEntry => folderEntry.path !== '.')
    .sort((a, b) => a.path.localeCompare(b.path))
    .forEach(folderEntry => {
      const parts = folderEntry.path.split('/').filter(Boolean);
      let parentPath = '.';
      let parent = root;

      parts.forEach((part, index) => {
        const path = index === 0 ? part : `${parts.slice(0, index + 1).join('/')}`;
        let node = nodes.get(path);
        if (!node) {
          node = { path, name: part, children: [] };
          nodes.set(path, node);
          parent.children.push(node);
        }
        parent = node;
        parentPath = path;
      });
    });

  function addNode(node, depth) {
    const row = document.createElement('div');
    row.className = 'td-folder-row';
    row.style.paddingLeft = `${depth * 14}px`;

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'td-folder-toggle';
    if (!node.children.length) toggle.classList.add('empty');
    toggle.textContent = _tdExpandedFolders.has(node.path) ? '▾' : '▸';
    toggle.setAttribute('aria-label', _tdExpandedFolders.has(node.path) ? 'Collapse folder' : 'Expand folder');
    toggle.onclick = event => {
      event.stopPropagation();
      if (_tdExpandedFolders.has(node.path)) _tdExpandedFolders.delete(node.path);
      else _tdExpandedFolders.add(node.path);
      _tdRenderFolderTree(folders);
    };

    const folder = document.createElement('button');
    folder.type = 'button';
    folder.className = 'td-folder';
    if (node.path === _tdSelectedFolder) folder.classList.add('selected');
    folder.style.flex = '1';

    const icon = document.createElement('span');
    icon.textContent = '📁';
    const name = document.createElement('span');
    name.className = 'td-folder-name';
    name.textContent = node.name;
    name.title = node.path;
    folder.appendChild(icon);
    folder.appendChild(name);
    folder.onclick = () => {
      _tdSelectedFolder = node.path;
      _tdLoad();
    };

    row.appendChild(toggle);
    row.appendChild(folder);
    tree.appendChild(row);

    if (_tdExpandedFolders.has(node.path)) {
      node.children
        .sort((a, b) => a.name.localeCompare(b.name))
        .forEach(child => addNode(child, depth + 1));
    }
  }

  root.children.sort((a, b) => a.name.localeCompare(b.name));
  addNode(root, 0);
}

async function _tdUpload() {
  if (!_tdFiles.length) return;
  const folder = _tdSelectedFolder || '.';
  const form = new FormData();
  form.append('folder', folder);
  _tdFiles.forEach(file => form.append('files', file));

  const button = document.getElementById('tdUploadBtn');
  if (button) button.disabled = true;
  const response = await fetch('/api/testdata/upload', { method: 'POST', body: form });
  const data = await response.json().catch(() => ({ error: 'Upload failed' }));
  if (!response.ok) {
    _tdSetStatus(data.error || 'Upload failed', true);
  } else {
    const parts = [];
    if (data.uploaded?.length) parts.push(`${data.uploaded.length} uploaded`);
    if (data.replaced?.length) parts.push(`${data.replaced.length} replaced`);
    _tdSetStatus(parts.join(', ') || 'Upload complete', false);
  }
  _tdSetSelected([]);
  await _tdLoad();
  if (button) button.disabled = _tdFiles.length === 0;
}

async function _tdDelete(name) {
  const folder = _tdSelectedFolder || '.';
  const response = await fetch('/api/testdata/file', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder, name }),
  });
  const data = await response.json().catch(() => ({ error: 'Delete failed' }));
  if (!response.ok) {
    _tdSetStatus(data.error || 'Delete failed', true);
  } else {
    _tdSetStatus(`Deleted ${name}`, false);
  }
  await _tdLoad();
}

function initTestData() {
  const input = document.getElementById('tdFiles');
  const drop = document.getElementById('tdDrop');
  const upload = document.getElementById('tdUploadBtn');
  const refresh = document.getElementById('tdRefresh');

  if (input) input.onchange = event => _tdSetSelected(event.target.files);
  if (upload) upload.onclick = _tdUpload;
  if (refresh) refresh.onclick = _tdLoad;

  if (drop) {
    drop.ondragover = event => {
      event.preventDefault();
      drop.classList.add('dragging');
    };
    drop.ondragleave = () => drop.classList.remove('dragging');
    drop.ondrop = event => {
      event.preventDefault();
      drop.classList.remove('dragging');
      _tdSetSelected(event.dataTransfer?.files);
    };
  }

  const rows = document.getElementById('tdFileRows');
  if (rows) rows.onclick = event => {
    const button = event.target.closest('.td-delete');
    if (button) _tdDelete(button.dataset.name);
  };

  if (!_tdInitialized) _tdInitialized = true;
  _tdLoad();
}
