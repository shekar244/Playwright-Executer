// ── Jira Insights JS ──────────────────────────────────────────────────────────
// The Streamlit app runs beside Flask (see routes/insights.py); this tab starts
// it on demand, waits for its health check, then embeds it in an iframe.

let _insightsUrl      = '';
let _insightsStarting = false;

function _insightsBaseUrl(s) {
  return s.public_url || `${location.protocol}//${location.hostname}:${s.port}`;
}

function _insightsSetState(dot, text) {
  document.getElementById('insightsDot').className = 'connect-dot' + (dot ? ' ' + dot : '');
  document.getElementById('insightsStatus').textContent = text;
}

function _insightsShowFrame(show) {
  document.getElementById('insightsFrame').style.display = show ? 'block' : 'none';
  document.getElementById('insightsEmpty').style.display = show ? 'none' : 'flex';
}

function _insightsEmbed(s) {
  const url   = _insightsBaseUrl(s);
  const frame = document.getElementById('insightsFrame');
  if (_insightsUrl !== url || !frame.src) {
    frame.src    = url + '/?embed=true&embed_options=dark_theme';
    _insightsUrl = url;
  }
  _insightsShowFrame(true);
  _insightsSetState('ok', s.managed ? `Running on port ${s.port}` : `Connected · ${url}`);
}

function _insightsFail(message, logTail) {
  _insightsShowFrame(false);
  _insightsSetState('err', 'Not running');
  document.getElementById('insightsEmptyMsg').textContent = message;
  const log = document.getElementById('insightsLog');
  log.textContent   = logTail || '';
  log.style.display = logTail ? 'block' : 'none';
}

async function _insightsStatus() {
  return fetch('/api/insights/status').then(r => r.json()).catch(() => null);
}

async function loadInsights() {
  const s = await _insightsStatus();
  if (!s) return _insightsFail('Cannot reach the Amplify QEA server.', '');
  if (s.healthy) return _insightsEmbed(s);
  insightsStart();
}

async function insightsStart() {
  if (_insightsStarting) return;
  _insightsStarting = true;
  const btn = document.getElementById('insightsStartBtn');
  btn.disabled = true;
  _insightsSetState('', 'Starting…');
  document.getElementById('insightsEmptyMsg').textContent = 'Starting Jira Insights…';
  document.getElementById('insightsLog').style.display = 'none';
  try {
    const res = await fetch('/api/insights/start', { method: 'POST' })
      .then(r => r.json()).catch(e => ({ error: String(e) }));
    if (res.error) return _insightsFail(res.error, res.log_tail);
    // Streamlit takes a few seconds to boot — poll its health check.
    for (let i = 0; i < 60; i++) {
      const s = await _insightsStatus();
      if (s?.healthy) return _insightsEmbed(s);
      if (s?.exited)  return _insightsFail('Jira Insights stopped during start-up.', s.log_tail);
      await new Promise(r => setTimeout(r, 750));
    }
    _insightsFail('Timed out waiting for Jira Insights to start.', '');
  } finally {
    _insightsStarting = false;
    btn.disabled = false;
  }
}

async function insightsStop() {
  await fetch('/api/insights/stop', { method: 'POST' }).catch(() => {});
  document.getElementById('insightsFrame').removeAttribute('src');
  _insightsUrl = '';
  _insightsFail('Jira Insights is stopped.', '');
}

function insightsReload() {
  const frame = document.getElementById('insightsFrame');
  if (frame.src) frame.src = frame.src;
  else loadInsights();
}

async function insightsOpenNewTab() {
  const s = await _insightsStatus();
  if (s?.healthy) window.open(_insightsBaseUrl(s), '_blank', 'noopener');
}
