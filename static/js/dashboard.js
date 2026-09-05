// ── Dashboard JS ──────────────────────────────────────────────────────────────

const DC = { passed:'#a6e3a1', failed:'#f38ba8', broken:'#f9e2af', skipped:'#89b4fa', unknown:'#808099' };
const CHART_GRID  = '#2e2e45';
const CHART_LABEL = '#808099';
const HISTORY_PAGE_SIZE = 10;

let _dashTests         = [];
let _dashFiltered      = [];
let _dashSortKey       = 'suite';
let _dashSortAsc       = true;
let _historyRecords    = [];
let _historyPage       = 0;
let _historySelected   = -1;
let _currentReportPath = '';

// Snapshot of the live (latest) run so we can restore it
let _liveSnapshot      = null;  // { tests, counts, avgDur, reportPath }

// ── Load ───────────────────────────────────────────────────────────────────────

async function loadDashboard() {
  const repo = document.getElementById('repo').value.trim() || document.getElementById('repoSelect').value.trim();
  document.getElementById('dashSubtitle').textContent = repo ? '— loading…' : '— select a repo on the Executor tab first';
  if (!repo) return;

  const data = await fetch(`/api/dashboard?repo=${encodeURIComponent(repo)}`)
    .then(r => r.json())
    .catch(e => { console.error('Dashboard load error:', e); return { tests: [], trend: [], summaries: [], run_history: [] }; });

  _dashTests = data.tests || [];
  const runHistory  = data.run_history  || [];
  const summaries   = data.summaries    || [];
  const allureTrend = data.trend        || [];

  // Stats
  const counts = { passed: 0, failed: 0, broken: 0, skipped: 0 };
  let totalDur = 0;
  _dashTests.forEach(t => {
    const s = t.status || 'unknown';
    if (s in counts) counts[s]++;
    totalDur += t.duration || 0;
  });
  const total    = _dashTests.length;
  const passRate = total ? ((counts.passed / total) * 100).toFixed(1) : '—';
  const avgDur   = total ? _fmtDur(totalDur / total) : '—';

  document.getElementById('dashTotal').textContent    = total;
  document.getElementById('dashPassed').textContent   = counts.passed;
  document.getElementById('dashFailed').textContent   = counts.failed;
  document.getElementById('dashBroken').textContent   = counts.broken;
  document.getElementById('dashSkipped').textContent  = counts.skipped;
  document.getElementById('dashPassRate').textContent = passRate !== '—' ? passRate + '%' : '—';
  document.getElementById('dashAvgDur').textContent   = avgDur;
  document.getElementById('dashRecordCount').textContent = `${runHistory.length} records`;

  const lastDate = _dashTests.length
    ? new Date(_dashTests[0].start).toLocaleString()
    : (runHistory[0]?.date || 'no runs yet');
  document.getElementById('dashSubtitle').textContent = `— ${total} tests  ·  last run: ${lastDate}`;

  // Suite-level report: prefer the live report_path from API, fall back to most recent history record
  _currentReportPath = data.report_path || (runHistory.find(r => r.report_path) || {}).report_path || '';
  _updateCurrentReportLink(_currentReportPath);

  _renderStatusDonut(counts);
  _renderDailyChart(_dashTests, summaries, runHistory);
  _renderRunTrend(summaries, allureTrend, runHistory);

  window._testHistory = {};
  summaries.slice(-10).forEach(s => {
    (s.results || []).forEach(r => {
      const id = r.testId || '';
      if (!window._testHistory[id]) window._testHistory[id] = [];
      let st = (r.status || 'unknown').toLowerCase();
      if (st === 'expected' || st === 'pass') st = 'passed';
      else if (st === 'unexpected' || st === 'fail') st = 'failed';
      window._testHistory[id].push(st);
    });
  });

  filterTests();
  _renderHistoryTable(runHistory);

  // Save live snapshot so "Back to Live Run" can restore without a network call
  _liveSnapshot = {
    tests:      _dashTests,
    counts,
    totalDur,
    reportPath: _currentReportPath,
    subtitle:   document.getElementById('dashSubtitle').textContent,
  };
}

// ── Report link helpers ────────────────────────────────────────────────────────

function _updateCurrentReportLink(path) {
  const link = document.getElementById('dashCurrentReportLink');
  const na   = document.getElementById('dashCurrentReportNA');
  if (path) {
    link.dataset.reportPath = path;
    link.style.display = '';
    na.style.display   = 'none';
  } else {
    link.style.display = 'none';
    na.style.display   = '';
  }
}

function dashOpenReport(event) {
  event.preventDefault();
  const path = document.getElementById('dashCurrentReportLink').dataset.reportPath || '';
  _openReportPath(path);
}

function dashOpenDetailReport(event) {
  event.preventDefault();
  const path = document.getElementById('dash-detail-report-link').dataset.reportPath || '';
  _openReportPath(path);
}

async function _openReportPath(path) {
  if (!path) return;
  const res = await fetch('/api/report/open', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  }).then(r => r.json()).catch(() => ({}));
  if (res.error) alert('Could not open report: ' + res.error);
}

// ── Charts ─────────────────────────────────────────────────────────────────────

function _renderStatusDonut(counts) {
  const donutId = 'chartStatusDonut';
  const barId   = 'chartStatusBar';
  [donutId, barId].forEach(id => { const c = Chart.getChart(id); if (c) c.destroy(); });

  const vals   = [counts.passed, counts.failed, counts.broken, counts.skipped];
  const labels = ['Passed', 'Failed', 'Broken', 'Skipped'];
  const colors = [DC.passed, DC.failed, DC.broken, DC.skipped];
  const total  = vals.reduce((a, b) => a + b, 0) || 0;

  // ── Plugin: draw total (default) or % (on hover) in centre ───────────────
  const centerTextPlugin = {
    id: 'donutCenter',
    afterDraw(chart) {
      const { ctx, chartArea } = chart;
      if (!chartArea) return;
      const cx = (chartArea.left + chartArea.right)  / 2;
      const cy = (chartArea.top  + chartArea.bottom) / 2;
      const active = chart.getActiveElements();

      let line1, line2, line1Color;
      if (active.length) {
        const idx  = active[0].index;
        const val  = chart.data.datasets[0].data[idx];
        const tot  = chart.data.datasets[0].data.reduce((a, b) => a + b, 0) || 1;
        line1      = ((val / tot) * 100).toFixed(1) + '%';
        line2      = labels[idx];
        line1Color = colors[idx];
      } else {
        line1      = total.toString();
        line2      = 'Total';
        line1Color = '#cdd6f4';
      }

      ctx.save();
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.font         = `bold 17px Inter, -apple-system, sans-serif`;
      ctx.fillStyle    = line1Color;
      ctx.fillText(line1, cx, cy - 8);
      ctx.font      = `10px Inter, -apple-system, sans-serif`;
      ctx.fillStyle = '#808099';
      ctx.fillText(line2, cx, cy + 9);
      ctx.restore();
    },
  };

  // ── Donut ─────────────────────────────────────────────────────────────────
  new Chart(document.getElementById(donutId).getContext('2d'), {
    type: 'doughnut',
    plugins: [centerTextPlugin],
    data: {
      labels,
      datasets: [{
        data:            vals,
        backgroundColor: colors,
        borderWidth:     2,
        borderColor:     '#1f1f2e',
        hoverOffset:     6,
        hoverBorderColor:'#ffffff22',
      }],
    },
    options: {
      cutout: '68%',
      animation: { duration: 400 },
      onHover: (evt, els) => {
        const canvas = document.getElementById(donutId);
        if (canvas) canvas.style.cursor = els.length ? 'pointer' : 'default';
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: c => {
              const tot = c.dataset.data.reduce((a, b) => a + b, 0) || 1;
              return ` ${c.label}: ${c.raw}  (${((c.raw / tot) * 100).toFixed(1)}%)`;
            },
          },
        },
      },
    },
  });

  // ── Horizontal bar chart ──────────────────────────────────────────────────
  new Chart(document.getElementById(barId).getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data:            vals,
        backgroundColor: colors.map(c => c + '55'),
        borderColor:     colors,
        borderWidth:     1.5,
        borderRadius:    4,
        borderSkipped:   false,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: c => {
              const tot = c.dataset.data.reduce((a, b) => a + b, 0) || 1;
              return ` ${c.raw}  (${((c.raw / tot) * 100).toFixed(1)}%)`;
            },
          },
        },
      },
      scales: {
        x: {
          beginAtZero: true,
          ticks: { color: CHART_LABEL, font: { size: 9 }, maxTicksLimit: 5 },
          grid:  { color: CHART_GRID },
        },
        y: {
          ticks: {
            color: (ctx) => colors[ctx.index] || CHART_LABEL,
            font:  { size: 10, weight: '600' },
          },
          grid: { display: false },
        },
      },
    },
  });
}

function _renderDailyChart(tests, summaries, runHistory) {
  const id = 'chartDailyRuns';
  const ch = Chart.getChart(id); if (ch) ch.destroy();

  const allTs = [];
  runHistory.forEach(r => { if (r.ts) allTs.push(r.ts); });
  if (!allTs.length) {
    if (summaries.length) summaries.forEach(s => { if (s.startTime) allTs.push(s.startTime); });
    else tests.forEach(t => { if (t.start) allTs.push(t.start); });
  }

  const now      = Date.now();
  const oldestTs = allTs.length ? Math.min(...allTs) : now;
  const daysDiff = Math.ceil((now - oldestTs) / 86400000);
  const windowDays = Math.min(60, Math.max(7, daysDiff + 1));

  const days = [], labels = [];
  for (let i = windowDays - 1; i >= 0; i--) {
    const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - i);
    days.push(d);
    const step = windowDays <= 14 ? 1 : windowDays <= 30 ? 2 : 5;
    labels.push(i % step === 0 ? d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '');
  }
  const N = days.length;
  const byDay = { passed: new Array(N).fill(0), failed: new Array(N).fill(0), broken: new Array(N).fill(0), skipped: new Array(N).fill(0) };

  const bucket = (ts, status) => {
    const date = new Date(ts); date.setHours(0, 0, 0, 0);
    const idx  = days.findIndex(d => d.getTime() === date.getTime());
    if (idx >= 0 && status in byDay) byDay[status][idx]++;
  };
  const normalise = st => {
    st = (st || 'unknown').toLowerCase();
    if (st === 'expected' || st === 'pass') return 'passed';
    if (st === 'unexpected' || st === 'fail') return 'failed';
    return st;
  };

  if (runHistory.length) {
    runHistory.forEach(r => {
      const ts = r.ts || 0; if (!ts) return;
      const date = new Date(ts); date.setHours(0, 0, 0, 0);
      const idx  = days.findIndex(d => d.getTime() === date.getTime());
      if (idx < 0) return;
      const s = r.stats || {};
      byDay.passed[idx]  += s.passed  || 0;
      byDay.failed[idx]  += s.failed  || 0;
      byDay.broken[idx]  += s.broken  || 0;
      byDay.skipped[idx] += s.skipped || 0;
    });
  } else if (summaries.length) {
    summaries.forEach(s => { const ts = s.startTime || 0; (s.results || []).forEach(r => bucket(ts, normalise(r.status))); });
  } else {
    tests.forEach(t => { if (t.start) bucket(t.start, t.status || 'unknown'); });
  }

  const bar = c => ({ borderColor: c, backgroundColor: c + '44', borderWidth: 2, borderRadius: 3, borderSkipped: false });
  const titleEl = document.getElementById('dailyChartTitle');
  if (titleEl) titleEl.textContent = `Tests Run by Day — Last ${windowDays} Days`;

  new Chart(document.getElementById(id).getContext('2d'), {
    type: 'bar',
    data: { labels, datasets: [
      { label: 'Passed',  data: byDay.passed,  ...bar(DC.passed)  },
      { label: 'Failed',  data: byDay.failed,  ...bar(DC.failed)  },
      { label: 'Broken',  data: byDay.broken,  ...bar(DC.broken)  },
      { label: 'Skipped', data: byDay.skipped, ...bar(DC.skipped) },
    ]},
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: { stacked: true, ticks: { color: CHART_LABEL, font: { size: 10 }, maxRotation: 40 }, grid: { color: CHART_GRID } },
        y: { stacked: true, ticks: { color: CHART_LABEL, font: { size: 10 } }, grid: { color: CHART_GRID }, beginAtZero: true },
      },
      plugins: { legend: { labels: { color: '#a6adc8', font: { size: 11 }, boxWidth: 11, padding: 12 }, position: 'bottom' }, tooltip: { mode: 'index', intersect: false } },
      animation: { duration: 500 },
    },
  });
}

function _renderRunTrend(summaries, allureTrend, runHistory) {
  const id = 'chartRunTrend';
  const ch = Chart.getChart(id); if (ch) ch.destroy();
  let labels, passed, failed, broken, skipped;

  if (runHistory.length) {
    const slice = [...runHistory].reverse().slice(-20);
    labels  = slice.map(r => r.date?.slice(5, 16) || '');
    passed  = slice.map(r => r.stats?.passed  || 0);
    failed  = slice.map(r => r.stats?.failed  || 0);
    broken  = slice.map(r => r.stats?.broken  || 0);
    skipped = slice.map(r => r.stats?.skipped || 0);
  } else if (summaries.length) {
    const slice = summaries.slice(-20);
    labels  = slice.map(s => { const d = new Date(s.startTime || 0); return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + '\n' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }); });
    passed = []; failed = []; broken = []; skipped = [];
    slice.forEach(s => {
      const c = { passed: 0, failed: 0, broken: 0, skipped: 0 };
      (s.results || []).forEach(r => { let st = (r.status || 'unknown').toLowerCase(); if (st === 'expected' || st === 'pass') st = 'passed'; else if (st === 'unexpected' || st === 'fail') st = 'failed'; if (st in c) c[st]++; });
      passed.push(c.passed); failed.push(c.failed); broken.push(c.broken); skipped.push(c.skipped);
    });
  } else if (allureTrend.length) {
    const slice = [...allureTrend].reverse().slice(-20);
    labels  = slice.map((_, i) => `Run #${i + 1}`);
    passed  = slice.map(b => b.data?.passed  ?? b.passed  ?? 0);
    failed  = slice.map(b => b.data?.failed  ?? b.failed  ?? 0);
    broken  = slice.map(b => b.data?.broken  ?? b.broken  ?? 0);
    skipped = slice.map(b => b.data?.skipped ?? b.skipped ?? 0);
  } else {
    labels = []; passed = []; failed = []; broken = []; skipped = [];
  }

  new Chart(document.getElementById(id).getContext('2d'), {
    type: 'line',
    data: { labels, datasets: [
      { label: 'Passed',  data: passed,  borderColor: DC.passed,  backgroundColor: DC.passed  + '18', tension: 0.35, fill: true, pointRadius: 4, borderWidth: 2 },
      { label: 'Failed',  data: failed,  borderColor: DC.failed,  backgroundColor: DC.failed  + '18', tension: 0.35, fill: true, pointRadius: 4, borderWidth: 2 },
      { label: 'Broken',  data: broken,  borderColor: DC.broken,  backgroundColor: DC.broken  + '10', tension: 0.35, pointRadius: 3, borderWidth: 1.5 },
      { label: 'Skipped', data: skipped, borderColor: DC.skipped, backgroundColor: DC.skipped + '10', tension: 0.35, pointRadius: 3, borderWidth: 1.5 },
    ]},
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: CHART_LABEL, font: { size: 9 }, maxRotation: 40 }, grid: { color: CHART_GRID } },
        y: { ticks: { color: CHART_LABEL, font: { size: 10 } }, grid: { color: CHART_GRID }, beginAtZero: true },
      },
      plugins: { legend: { labels: { color: '#a6adc8', font: { size: 11 }, boxWidth: 11, padding: 12 }, position: 'bottom' }, tooltip: { mode: 'index', intersect: false } },
      animation: { duration: 500 },
    },
  });
}

// ── Test table (Current Run) ───────────────────────────────────────────────────

function filterTests() {
  const q      = (document.getElementById('dashSearch').value || '').toLowerCase();
  const status = document.getElementById('dashStatusFilter').value;
  _dashFiltered = _dashTests.filter(t =>
    (!q      || t.name.toLowerCase().includes(q) || (t.suite || '').toLowerCase().includes(q)) &&
    (!status || t.status === status)
  );
  _sortAndRenderTests();
}

function sortTests(key) {
  if (_dashSortKey === key) _dashSortAsc = !_dashSortAsc;
  else { _dashSortKey = key; _dashSortAsc = true; }
  _sortAndRenderTests();
}

function _sortAndRenderTests() {
  const key = _dashSortKey, asc = _dashSortAsc;
  _dashFiltered.sort((a, b) => {
    let av = a[key] ?? '', bv = b[key] ?? '';
    if (key === 'duration') { av = a.duration; bv = b.duration; }
    if (av < bv) return asc ? -1 : 1;
    if (av > bv) return asc ? 1  : -1;
    return 0;
  });
  _renderTestTable(_dashFiltered);
}

function _renderTestTable(tests) {
  const tbody = document.getElementById('dashTestTbody');
  document.getElementById('dashTestCount').textContent = `${tests.length} tests`;
  if (!tests.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-dim);padding:32px;">${_dashTests.length ? 'No tests match the filter' : 'No Allure results found — run tests with <code>--alluredir</code> enabled'}</td></tr>`;
    return;
  }
  const SI = { passed: '✓', failed: '✗', broken: '!', skipped: '⊘', unknown: '?' };

  tbody.innerHTML = tests.map(t => {
    const s        = t.status || 'unknown';
    const runAt    = t.start ? new Date(t.start).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
    const suite    = escHtml(t.file  || t.fullName?.split('#')[0]?.replace(/^\./, '') || t.suite || '—');
    const name     = escHtml(t.method || t.fullName?.split('#').pop() || t.name || '—');
    // Per-test deep link: use t.report_path / t.report_relpath first, then construct from uid, fall back to suite report
    const testPath = t.report_path || t.report_relpath
      || (t.uid && _currentReportPath ? _currentReportPath + '#testresult/' + t.uid : '')
      || _currentReportPath;
    const reportCell = testPath
      ? `<a href="#" onclick="event.preventDefault();_openReportPath('${escHtml(testPath)}')" style="font-size:10px;font-weight:600;color:var(--accent);text-decoration:none;border:1px solid rgba(137,180,250,0.25);border-radius:4px;padding:2px 7px;background:var(--accent-glow);">📊 View</a>`
      : `<span style="font-size:10px;color:var(--text-dim);">N/A</span>`;
    return `<tr>
      <td style="color:var(--text-dim);font-size:11px;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${suite}">${suite}</td>
      <td style="max-width:340px;" title="${name}"><span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--text);">${name}</span></td>
      <td><span class="tbl-status-badge ${s}">${SI[s] || '?'} ${s}</span></td>
      <td class="tbl-num" style="color:var(--text-muted);">${_fmtDur(t.duration)}</td>
      <td style="font-size:11px;color:var(--text-dim);white-space:nowrap;">${runAt}</td>
      <td style="text-align:center;">${reportCell}</td>
    </tr>`;
  }).join('');
}

// ── Run History table + pagination ────────────────────────────────────────────

function _renderHistoryTable(records) {
  _historyRecords  = records;
  _historyPage     = 0;
  _historySelected = -1;
  document.getElementById('dashRecordCount').textContent = `${records.length} records`;
  _renderHistoryPage();
}

function _renderHistoryPage() {
  const records = _historyRecords;
  const total   = records.length;
  const pages   = Math.ceil(total / HISTORY_PAGE_SIZE);
  const start   = _historyPage * HISTORY_PAGE_SIZE;
  const slice   = records.slice(start, start + HISTORY_PAGE_SIZE);
  const tbody   = document.getElementById('historyTbody');

  if (!total) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-dim);padding:24px;">No runs recorded yet — run some tests first</td></tr>';
    document.getElementById('historyPagination').innerHTML = '';
    return;
  }

  const SI = { passed: '✓', failed: '✗', broken: '!', cancelled: '⊘' };
  tbody.innerHTML = slice.map((r, i) => {
    const absIdx     = start + i;
    const s          = r.stats || {};
    const rowTotal   = s.total || 0;
    const pct        = rowTotal ? ((s.passed || 0) / rowTotal * 100).toFixed(1) : '—';
    const statusKey  = (r.status || '').startsWith('failed') ? 'failed' : (r.status || 'cancelled');
    const statusLbl  = statusKey;
    const isSelected = absIdx === _historySelected;

    const reportCell = r.report_path
      ? `<button class="btn btn-sm" onclick="event.stopPropagation();_openReportPath('${escHtml(r.report_path)}')" style="padding:3px 10px;font-size:10px;" title="${escHtml(r.report_path)}">📊 View</button>`
      : `<span style="font-size:10px;color:var(--text-dim);">N/A</span>`;

    return `<tr onclick="showRunDetail(${absIdx})" style="cursor:pointer;" class="${isSelected ? 'hist-row-selected' : ''}">
      <td style="white-space:nowrap;">${escHtml(r.date || '—')}</td>
      <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(r.repo||'')}">${escHtml(r.repo || '—')}</td>
      <td><span class="tbl-status-badge ${statusKey}">${statusLbl}</span></td>
      <td class="tbl-num pass">${s.passed  || 0}</td>
      <td class="tbl-num fail">${s.failed  || 0}</td>
      <td class="tbl-num broken">${s.broken || 0}</td>
      <td class="tbl-num skip">${s.skipped || 0}</td>
      <td class="tbl-num">${rowTotal}</td>
      <td class="tbl-num ${pct !== '—' && parseFloat(pct) === 100 ? 'pass' : ''}">${pct !== '—' ? pct + '%' : '—'}</td>
      <td style="text-align:center;">${reportCell}</td>
    </tr>`;
  }).join('');

  _renderPagination(pages);
}

function _renderPagination(pages) {
  const el = document.getElementById('historyPagination');
  if (pages <= 1) { el.innerHTML = ''; return; }

  const cur  = _historyPage;
  const btns = [];

  const btn = (label, page, disabled, active, title) =>
    `<button class="pg-btn${active ? ' pg-active' : ''}" onclick="historyGoPage(${page})" ${disabled ? 'disabled' : ''} title="${title || ''}">${label}</button>`;

  btns.push(btn('← Prev', cur - 1, cur === 0, false, 'Previous page'));

  // Smart page window: always show first, last, and ±2 around current
  const shown = new Set();
  const add = p => { if (p >= 0 && p < pages) shown.add(p); };
  add(0); add(pages - 1);
  for (let d = -2; d <= 2; d++) add(cur + d);

  [...shown].sort((a, b) => a - b).forEach((p, i, arr) => {
    if (i > 0 && p > arr[i - 1] + 1) btns.push(`<span class="pg-ellipsis">…</span>`);
    btns.push(btn(p + 1, p, false, p === cur, `Go to page ${p + 1}`));
  });

  btns.push(btn('Next →', cur + 1, cur === pages - 1, false, 'Next page'));
  btns.push(`<span style="color:var(--text-dim);font-size:11px;margin-left:6px;white-space:nowrap;">Page ${cur + 1} of ${pages} · ${_historyRecords.length} runs</span>`);

  el.innerHTML = btns.join('');
}

function historyGoPage(page) {
  const pages = Math.ceil(_historyRecords.length / HISTORY_PAGE_SIZE);
  if (page < 0 || page >= pages) return;
  _historyPage = page;
  _renderHistoryPage();
}

// ── Load history row into the Current Run view ────────────────────────────────

function showRunDetail(absIdx) {
  const record = _historyRecords[absIdx];
  if (!record) return;

  _historySelected = absIdx;
  _renderHistoryPage();  // highlight selected row

  const s      = record.stats || {};
  const total  = s.total   || 0;
  const counts = {
    passed:  s.passed  || 0,
    failed:  s.failed  || 0,
    broken:  s.broken  || 0,
    skipped: s.skipped || 0,
  };

  // Convert history test records to dashboard test format
  const rawTests  = record.tests || [];
  const mappedTests = rawTests.map(t => {
    let start = 0;
    if (t.date && t.time) {
      try { start = new Date(`${t.date}T${t.time}`).getTime(); } catch(e) {}
    }
    return {
      name:        t.name        || t.method || '',
      method:      t.method      || t.name   || '',
      file:        t.suite       || '',
      suite:       t.suite       || '',
      status:      t.status      || 'unknown',
      fullName:    t.fullName    || '',
      uid:         t.uid         || '',
      report_path: t.report_path || '',
      start,
      duration: t.duration_ms || Math.round((t.duration_s || 0) * 1000),
    };
  });

  const totalDur  = mappedTests.reduce((s, t) => s + (t.duration || 0), 0);
  const passRate  = total ? ((counts.passed / total) * 100).toFixed(1) : '—';
  const avgDur    = mappedTests.length ? _fmtDur(totalDur / mappedTests.length) : '—';
  const statusStr = (record.status || '').startsWith('failed') ? 'failed' : (record.status || '—');

  // ── Push into top stats ──────────────────────────────────────────────────
  document.getElementById('dashTotal').textContent    = total;
  document.getElementById('dashPassed').textContent   = counts.passed;
  document.getElementById('dashFailed').textContent   = counts.failed;
  document.getElementById('dashBroken').textContent   = counts.broken;
  document.getElementById('dashSkipped').textContent  = counts.skipped;
  document.getElementById('dashPassRate').textContent = passRate !== '—' ? passRate + '%' : '—';
  document.getElementById('dashAvgDur').textContent   = avgDur;
  document.getElementById('dashSubtitle').textContent =
    `— ${total} tests · ${statusStr} · ${record.date || '?'} · ${record.repo || '?'}`;

  // ── Redraw donut ─────────────────────────────────────────────────────────
  _renderStatusDonut(counts);

  // ── Load into test table ─────────────────────────────────────────────────
  _dashTests    = mappedTests;
  _dashFiltered = mappedTests.slice();
  _currentReportPath = record.report_path || '';

  document.getElementById('dashTestTableTitle').textContent =
    `Test Cases — ${record.date || '?'} · ${record.repo || '?'}`;

  _updateCurrentReportLink(_currentReportPath);
  _sortAndRenderTests();

  // ── Show banner ───────────────────────────────────────────────────────────
  const banner = document.getElementById('dash-history-banner');
  document.getElementById('dash-history-banner-label').textContent =
    `${record.date || '?'} · ${record.repo || '?'} · ${total} tests · ${statusStr}`;

  const bannerReport = document.getElementById('dash-history-banner-report');
  if (_currentReportPath) {
    bannerReport.dataset.reportPath = _currentReportPath;
    bannerReport.style.display = '';
  } else {
    bannerReport.style.display = 'none';
  }
  banner.style.display = 'flex';

  // Scroll to top of dashboard smoothly
  document.getElementById('page-dashboard').scrollTo({ top: 0, behavior: 'smooth' });
}

function backToLiveRun() {
  if (!_liveSnapshot) { loadDashboard(); return; }

  _historySelected = -1;
  _renderHistoryPage();

  const { tests, counts, totalDur, reportPath, subtitle } = _liveSnapshot;
  const total   = tests.length;
  const passRate = total ? ((counts.passed / total) * 100).toFixed(1) : '—';
  const avgDur   = total ? _fmtDur(totalDur / total) : '—';

  document.getElementById('dashTotal').textContent    = total;
  document.getElementById('dashPassed').textContent   = counts.passed;
  document.getElementById('dashFailed').textContent   = counts.failed;
  document.getElementById('dashBroken').textContent   = counts.broken;
  document.getElementById('dashSkipped').textContent  = counts.skipped;
  document.getElementById('dashPassRate').textContent = passRate !== '—' ? passRate + '%' : '—';
  document.getElementById('dashAvgDur').textContent   = avgDur;
  document.getElementById('dashSubtitle').textContent = subtitle;
  document.getElementById('dashTestTableTitle').textContent = 'Test Cases — Current Run';

  _renderStatusDonut(counts);

  _dashTests    = tests;
  _dashFiltered = tests.slice();
  _currentReportPath = reportPath;
  _updateCurrentReportLink(reportPath);
  _sortAndRenderTests();

  // Hide banner
  document.getElementById('dash-history-banner').style.display = 'none';
  document.getElementById('page-dashboard').scrollTo({ top: 0, behavior: 'smooth' });
}

// Keep dashOpenDetailReport working for the banner report link
function dashOpenDetailReport(event) {
  event.preventDefault();
  const path = event.currentTarget.dataset.reportPath || '';
  _openReportPath(path);
}

// ── Clear history ──────────────────────────────────────────────────────────────

async function clearHistory() {
  if (!confirm('Clear all run history? This cannot be undone.')) return;
  const repo = document.getElementById('repo')?.value.trim() || '';
  await fetch('/api/report/history/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo }),
  });
  loadDashboard();
}
