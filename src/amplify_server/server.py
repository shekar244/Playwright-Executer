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

_PKG = Path(__file__).parent            # src/amplify_server/
_SRC = _PKG.parent                      # src/
_ROOT = _SRC.parent                     # repository root
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from amplify_server.routes.executor    import bp as executor_bp
from amplify_server.routes.config      import bp as config_bp
from amplify_server.routes.git         import bp as git_bp
from amplify_server.routes.dashboard   import bp as dashboard_bp
from amplify_server.routes.zephyr      import bp as zephyr_bp
from amplify_server.routes.filemanager import bp as filemanager_bp
from amplify_server.routes.insights    import bp as insights_bp
from amplify_server.routes.auth        import bp as auth_bp
from amplify_server.routes.admin       import bp as admin_bp
from amplify_server.routes.admin_bootstrap import bp as bootstrap_bp
from amplify_server.routes.repo        import bp as repo_bp
from amplify_server.routes.grid        import bp as grid_bp
from amplify_server.routes.grid_live   import bp as grid_live_bp
from amplify_server.routes.testdata    import bp as testdata_bp
from amplify_server.auth_utils import verify_jwt
from amplify_server.models.db import init_db
from amplify_server.models.user import User
from amplify_server.config import settings

app = Flask(__name__, static_folder=str(_PKG / "static"), template_folder=str(_PKG / "templates"))
app.secret_key = os.environ.get("AMPLIFY_SECRET_KEY") or "amplify-qea-grid-local-secret-change-me"

app.register_blueprint(executor_bp)
app.register_blueprint(config_bp)
app.register_blueprint(git_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(zephyr_bp)
app.register_blueprint(filemanager_bp)
app.register_blueprint(insights_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(bootstrap_bp)
app.register_blueprint(repo_bp)
app.register_blueprint(grid_bp)
app.register_blueprint(grid_live_bp)
app.register_blueprint(testdata_bp)

@app.before_request
def before_request():
    """Check authentication for protected routes."""
    public_routes = {"/login", "/register", "/api/auth/register", "/api/auth/login", "/api/admin/bootstrap"}
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
    if g.user.must_change_password:                  # an admin reset their password: choose a new one first
        return redirect("/change-password")
    return render_template("base.html", user=g.user)

@app.route("/change-password")
def change_password_page():
    if not hasattr(g, "user"):
        return redirect("/login")
    return render_template("change_password.html", user=g.user, forced=bool(g.user.must_change_password))

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/register")
def register():
    return render_template("register.html")

@app.route("/admin")
def admin_page():
    if not hasattr(g, "user"):
        return redirect("/login")
    if g.user.must_change_password:
        return redirect("/change-password")
    if not g.user.is_admin:
        return redirect("/")
    return render_template("admin.html", user=g.user)

@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory(_PKG / "static", path)


# ── Hostname / port setup ──────────────────────────────────────────────────────

HOSTNAME   = "amplify-qea-grid"
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
