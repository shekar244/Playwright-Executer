// ── Dashboard JS ──────────────────────────────────────────────────────────────

const DC = { passed:'#a6e3a1', failed:'#f38ba8', broken:'#f9e2af', skipped:'#89b4fa', unknown:'#808099' };
const CHART_GRID  = '#2e2e45';
const CHART_LABEL = '#808099';

let _dashTests    = [];
let _dashFiltered = [];
let _dashSortKey  = 'suite';
let _dashSortAsc  = true;

async function loadDashboard() {
  const repo = document.getElementById('repo').value.trim() || document.getElementById('repoSelect').value.trim();
  document.getElementById('dashSubtitle').textContent = repo ? '— loading…' : '— select a repo on the Executor tab first';
  if (!repo) return;

  const url  = `/api/dashboard?repo=${encodeURIComponent(repo)}`;
  const data = await fetch(url).then(r => r.json()).catch(e => {
    console.error('Dashboard load error:', e);
    return { tests: [], trend: [], summaries: [], run_history: [] };
  });

  _dashTests = data.tests || [];
  const runHistory  = data.run_history || [];
  const summaries   = data.summaries   || [];
  const allureTrend = data.trend       || [];

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

  const lastDate = _dashTests.length ? new Date(_dashTests[0].start).toLocaleString() : (runHistory[0]?.date || 'no runs yet');
  document.getElementById('dashSubtitle').textContent = `— ${total} tests  ·  last run: ${lastDate}`;

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
}

function _renderStatusDonut(counts) {
  const id = 'chartStatusDonut';
  const ch = Chart.getChart(id); if (ch) ch.destroy();
  const vals   = [counts.passed, counts.failed, counts.broken, counts.skipped];
  const labels = ['Passed', 'Failed', 'Broken', 'Skipped'];
  const colors = [DC.passed, DC.failed, DC.broken, DC.skipped];
  new Chart(document.getElementById(id).getContext('2d'), {
    type: 'doughnut',
    data: { labels, datasets: [{ data: vals, backgroundColor: colors, borderWidth: 2, borderColor: '#1f1f2e', hoverOffset: 5 }] },
    options: { cutout: '66%', animation: { duration: 500 }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => ` ${c.label}: ${c.raw}` } } } },
  });
  const total = vals.reduce((a, b) => a + b, 0) || 1;
  document.getElementById('legendStatus').innerHTML = labels.map((l, i) =>
    `<div class="dash-legend-item"><span class="dash-legend-dot" style="background:${colors[i]}"></span><span>${l}</span><span class="dash-legend-val">${vals[i]} <span style="color:var(--text-dim);font-size:10px;">(${(vals[i]/total*100).toFixed(0)}%)</span></span></div>`
  ).join('');
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
  const N    = days.length;
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
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text-dim);padding:32px;">${_dashTests.length ? 'No tests match the filter' : 'No Allure results found — run tests with <code>--alluredir</code> enabled'}</td></tr>`;
    return;
  }
  const SI = { passed: '✓', failed: '✗', broken: '!', skipped: '⊘', unknown: '?' };
  tbody.innerHTML = tests.map(t => {
    const s     = t.status || 'unknown';
    const runAt = t.start ? new Date(t.start).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
    const suite = escHtml(t.file  || t.fullName?.split('#')[0]?.replace(/^\./, '') || t.suite || '—');
    const name  = escHtml(t.method || t.fullName?.split('#').pop() || t.name || '—');
    return `<tr>
      <td style="color:var(--text-dim);font-size:11px;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${suite}">${suite}</td>
      <td style="max-width:340px;" title="${name}"><span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--text);">${name}</span></td>
      <td><span class="tbl-status-badge ${s}">${SI[s] || '?'} ${s}</span></td>
      <td class="tbl-num" style="color:var(--text-muted);">${_fmtDur(t.duration)}</td>
      <td style="font-size:11px;color:var(--text-dim);white-space:nowrap;">${runAt}</td>
    </tr>`;
  }).join('');
}

function _renderHistoryTable(records) {
  const tbody = document.getElementById('historyTbody');
  if (!records.length) { tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-dim);padding:24px;">No runs recorded yet — run some tests first</td></tr>'; return; }
  tbody.innerHTML = records.slice(0, 50).map(r => {
    const s    = r.stats || {};
    const total = s.total || 0;
    const pct   = total ? ((s.passed || 0) / total * 100).toFixed(1) : '—';
    const statusCls   = (r.status || '').startsWith('failed') ? 'failed' : (r.status || 'cancelled');
    const statusLabel = (r.status || '').startsWith('failed') ? 'failed' : (r.status || '—');
    return `<tr>
      <td>${escHtml(r.date || '—')}</td>
      <td>${escHtml(r.repo || '—')}</td>
      <td><span class="tbl-status-badge ${statusCls}">${statusLabel}</span></td>
      <td class="tbl-num pass">${s.passed || 0}</td>
      <td class="tbl-num fail">${s.failed || 0}</td>
      <td class="tbl-num broken">${s.broken || 0}</td>
      <td class="tbl-num skip">${s.skipped || 0}</td>
      <td class="tbl-num">${total}</td>
      <td class="tbl-num ${pct !== '—' && parseFloat(pct) === 100 ? 'pass' : ''}">${pct !== '—' ? pct + '%' : '—'}</td>
    </tr>`;
  }).join('');
}

async function clearHistory() {
  if (!confirm('Clear all run history? This cannot be undone.')) return;
  await fetch('/api/report/history/clear', { method: 'POST' });
  loadDashboard();
}
