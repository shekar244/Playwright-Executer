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

// Insights is always entered through QEA: /api/insights/open checks the QEA session, signs the
// user in with a short-lived single-use token and redirects. Every load (first, reload, theme
// change, new tab) therefore gets a fresh token, and the user's identity never sits in a URL
// the user could edit.
function _insightsOpenUrl(embed) {
  // Several themes (Dark, Midnight, Nord, Rosé, Forest) are dark; each has its own body class.
  const active = (typeof _THEMES !== 'undefined' ? _THEMES : []).find(x => x.cls && document.body.classList.contains(x.cls));
  const t = active && active.dark ? 'dark' : 'light';
  return `/api/insights/open?theme=${t}${embed ? '&embed=1' : ''}`;
}

// Streamlit reads its theme at session start, so a theme change reloads the embed.
function _insightsApplyTheme() {
  const frame = document.getElementById('insightsFrame');
  if (_insightsUrl && frame && frame.getAttribute('src')) frame.src = _insightsOpenUrl(true);
}

function _insightsEmbed(s) {
  const url   = _insightsBaseUrl(s);
  const frame = document.getElementById('insightsFrame');
  if (_insightsUrl !== url || !frame.src) {
    frame.src    = _insightsOpenUrl(true);
    _insightsUrl = url;
  }
  _insightsShowFrame(true);
  _insightsSetState('ok', s.managed ? `Running on port ${s.port}` : `Connected · ${url}`);
  // Its own pod (INSIGHTS_INTERNAL_URL): QEA cannot start or stop it, so no Stop button.
  const stop = document.getElementById('insightsStopBtn');
  if (stop) stop.style.display = s.external ? 'none' : '';
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

// Opens synchronously (no await first) so popup blockers allow it. _insightsUrl is set only while
// Insights is up and embedded, which is exactly when the button should do something.
function insightsOpenNewTab() {
  if (_insightsUrl) window.open(_insightsOpenUrl(false), '_blank', 'noopener');
}
