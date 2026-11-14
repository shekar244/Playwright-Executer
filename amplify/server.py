"""
Amplify QEA — Web Server (modular)
Registers Flask Blueprints and serves the Jinja2 template UI.
Multi-tenant with user authentication.
"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, render_template, send_from_directory, request, redirect, g

_PKG  = Path(__file__).parent           # amplify/
_ROOT = _PKG.parent                      # repo root — holds the amplify package
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from amplify.routes.executor    import bp as executor_bp
from amplify.routes.config      import bp as config_bp
from amplify.routes.git         import bp as git_bp
from amplify.routes.dashboard   import bp as dashboard_bp
from amplify.routes.zephyr      import bp as zephyr_bp
from amplify.routes.filemanager import bp as filemanager_bp
from amplify.routes.studio      import bp as studio_bp
from amplify.routes.insights    import bp as insights_bp
from amplify.routes.auth        import bp as auth_bp
from amplify.auth_utils import verify_jwt
from amplify.models.db import init_db
from amplify.models.user import User
from amplify.config import settings

app = Flask(__name__, static_folder=str(_PKG / "static"), template_folder=str(_PKG / "templates"))
app.secret_key = os.environ.get("AMPLIFY_SECRET_KEY") or "amplify-qea-local-secret-change-me"

app.register_blueprint(executor_bp)
app.register_blueprint(config_bp)
app.register_blueprint(git_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(zephyr_bp)
app.register_blueprint(filemanager_bp)
app.register_blueprint(studio_bp)
app.register_blueprint(insights_bp)
app.register_blueprint(auth_bp)

@app.before_request
def before_request():
    """Check authentication for protected routes."""
    public_routes = {"/login", "/register", "/api/auth/register", "/api/auth/login"}
    if request.path not in public_routes and not request.path.startswith("/static"):
        token = request.cookies.get("access_token")
        if token:
            user_id = verify_jwt(token)
            if user_id:
                user = User.get_by_id(user_id)
                if user:
                    g.user = user
                    return
        if request.path.startswith("/api/"):
            return
        return redirect("/login")

@app.route("/")
def index():
    if not hasattr(g, "user"):
        return redirect("/login")
    return render_template("base.html", user=g.user)

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/register")
def register():
    return render_template("register.html")

@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory(_PKG / "static", path)


# ── Hostname / port setup ──────────────────────────────────────────────────────

HOSTNAME   = "amplify-qea"
PORT       = int(os.environ.get("PORT", 7777))          # 8080 in container, 7777 locally
_CONTAINER = os.environ.get("CONTAINER", "0") == "1"    # set to "1" in Dockerfile
APP_URL    = f"http://{HOSTNAME}:{PORT}"


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
    init_db()

    if _CONTAINER:
        # Running inside Docker / OpenShift — skip hosts entry and browser launch
        print(f"\n  Amplify QEA — container mode (multi-tenant)")
        print(f"  Listening on 0.0.0.0:{PORT}\n")
    else:
        # Running locally — open browser as usual
        ok  = _ensure_hosts_entry()
        url = APP_URL if ok else f"http://localhost:{PORT}"
        print(f"\n  Amplify QEA — multi-tenant")
        print(f"  Open: {url}\n")
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
