"""
Playwright Test Executor — Web Server (modular)
Registers Flask Blueprints and serves the Jinja2 template UI.
"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, render_template, send_from_directory

_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from routes.executor    import bp as executor_bp
from routes.config      import bp as config_bp
from routes.git         import bp as git_bp
from routes.dashboard   import bp as dashboard_bp
from routes.zephyr      import bp as zephyr_bp
from routes.filemanager import bp as filemanager_bp

app = Flask(__name__, static_folder="static", template_folder="templates")

app.register_blueprint(executor_bp)
app.register_blueprint(config_bp)
app.register_blueprint(git_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(zephyr_bp)
app.register_blueprint(filemanager_bp)


@app.route("/")
def index():
    return render_template("base.html")


@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


# ── Hostname setup ─────────────────────────────────────────────────────────────

HOSTNAME = "amplyfy-qea"
PORT     = 7777
APP_URL  = f"http://{HOSTNAME}:{PORT}"


def _ensure_hosts_entry() -> bool:
    import platform
    hosts_path = (r"C:\Windows\System32\drivers\etc\hosts"
                  if platform.system() == "Windows"
                  else "/etc/hosts")
    try:
        with open(hosts_path, "r", encoding="utf-8") as f:
            content = f.read()
        if HOSTNAME in content:
            return True
    except OSError:
        return False

    entry = f"\n127.0.0.1  {HOSTNAME}\n"
    if platform.system() == "Windows":
        try:
            with open(hosts_path, "a", encoding="utf-8") as f:
                f.write(entry)
            return True
        except PermissionError:
            return False
    else:
        result = os.system(f'echo "127.0.0.1  {HOSTNAME}" | sudo tee -a {hosts_path} > /dev/null')
        return result == 0


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ok  = _ensure_hosts_entry()
    url = APP_URL if ok else f"http://localhost:{PORT}"

    print(f"\n  Playwright Test Executor")
    print(f"  Open: {url}\n")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
