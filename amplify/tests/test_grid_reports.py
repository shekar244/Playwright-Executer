"""Viewing a Grid run's Allure report in the dashboard: fetched from the coordinator, cached per run and ETag,
and shown through the same signed, per-user viewer as local reports.

The coordinator is a real local HTTP server that insists on the X-API-Token header, so a wrong request, a
missing token or a leaked secret all show up.
"""
import importlib
import os
import threading
import time
from types import SimpleNamespace

import pytest

from amplify import report_viewer
from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

from amplify import grid_reports

TOKEN = "s3cret-token-value"
HTML = b"<html><body>ALLURE 3 REPORT run-1</body></html>"
DAY = 86_400


@pytest.fixture
def app(data_dir):
    flask_app = Flask(__name__)
    for name in ("auth", "dashboard"):
        flask_app.register_blueprint(importlib.import_module(f"amplify.routes.{name}").bp)
    return flask_app


@pytest.fixture
def coordinator(monkeypatch):
    """GET /api/runs/<id>/report: HTML + ETag per run, optional 304, optional canned errors."""
    fake = Flask("fake-coordinator")
    state = SimpleNamespace(reports={"run-1": (HTML, "etag-one")}, errors={}, calls=[], tokens=set(), sent=0,
                            honour_304=True, streamed=None)

    @fake.route("/api/runs/<run_id>/report")
    def report(run_id):
        state.tokens.add(request.headers.get("X-API-Token"))
        if request.headers.get("X-API-Token") != TOKEN:
            return jsonify({"detail": "Missing or invalid X-API-Token"}), 401
        state.calls.append((run_id, request.headers.get("If-None-Match")))
        if run_id in state.errors:
            status, detail = state.errors[run_id]
            return jsonify({"detail": detail}), status
        if run_id not in state.reports:
            return jsonify({"detail": "No Allure data for this run"}), 404
        body, etag = state.reports[run_id]
        headers = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}
        if etag is not None:
            headers["ETag"] = f'"{etag}"'
            if state.honour_304 and request.headers.get("If-None-Match") == headers["ETag"]:
                return Response(status=304, headers={"ETag": headers["ETag"]})
        state.sent += 1
        if state.streamed:                                              # no Content-Length: chunked
            return Response((body[i:i + 1000] for i in range(0, len(body), 1000)), headers=headers)
        return Response(body, headers=headers)

    server = make_server("127.0.0.1", 0, fake, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("GRID_COORDINATOR_URL", state.url)
    monkeypatch.setenv("GRID_API_TOKEN", TOKEN)
    yield state
    server.shutdown()
    server.server_close()


def link(client, run_id="run-1", **extra):
    return client.post("/api/dashboard/report-link", json={"source": "grid", "run_id": run_id, **extra})


def cache(data_dir):
    return data_dir / "grid_reports"


# ── success ───────────────────────────────────────────────────────────────────

def test_a_grid_runs_report_opens_inside_the_app(admin, coordinator, data_dir):
    r = link(admin)
    assert r.status_code == 200
    out = r.get_json()
    assert set(out) == {"url", "title", "file"} and out["file"] == "report.html" and "run-1" in out["title"]
    assert out["url"].startswith("/rv/") and out["url"].endswith("/report.html")
    page = admin.get(out["url"])
    assert page.status_code == 200 and page.data == HTML
    assert page.headers["Cache-Control"] == "no-store" and page.headers["X-Content-Type-Options"] == "nosniff"
    assert page.headers["Content-Security-Policy"] == report_viewer.SANDBOX_CSP          # untrusted: sandboxed
    assert (cache(data_dir) / "run-1" / "etag-one" / "report.html").read_bytes() == HTML


def test_the_token_goes_only_in_the_header(admin, coordinator, data_dir):
    r = link(admin)
    assert coordinator.tokens == {TOKEN} and TOKEN not in r.get_data(as_text=True)
    assert TOKEN not in str(list(cache(data_dir).rglob("*")))


def test_the_uid_of_a_local_test_is_ignored_for_grid_runs(admin, coordinator):
    assert link(admin, uid="whatever").status_code == 200


# ── caching ───────────────────────────────────────────────────────────────────

def test_an_unchanged_report_is_not_downloaded_again(admin, coordinator, data_dir):
    link(admin)
    second = link(admin).get_json()
    assert coordinator.sent == 1 and coordinator.calls == [("run-1", None), ("run-1", '"etag-one"')]
    assert admin.get(second["url"]).data == HTML
    assert [p.name for p in (cache(data_dir) / "run-1").iterdir()] == ["etag-one"]


def test_a_coordinator_that_ignores_if_none_match_still_gets_one_cache_entry(admin, coordinator, data_dir):
    coordinator.honour_304 = False
    link(admin), link(admin)
    assert coordinator.sent == 2
    assert [p.name for p in (cache(data_dir) / "run-1").iterdir()] == ["etag-one"]
    assert os.listdir(cache(data_dir) / "run-1" / "etag-one") == ["report.html"]


def test_more_results_mean_a_new_etag_and_a_new_folder(admin, coordinator, data_dir):
    link(admin)
    coordinator.reports["run-1"] = (b"<html>MORE CHUNKS</html>", "etag-two")
    out = link(admin).get_json()
    assert coordinator.calls[-1] == ("run-1", '"etag-one"') and admin.get(out["url"]).data == b"<html>MORE CHUNKS</html>"
    assert {p.name for p in (cache(data_dir) / "run-1").iterdir()} == {"etag-one", "etag-two"}   # old link still works for its hour


def test_versions_whose_links_have_expired_are_dropped_when_a_newer_one_arrives(admin, coordinator, data_dir):
    link(admin)
    old = cache(data_dir) / "run-1" / "etag-one" / "report.html"
    os.utime(old, (time.time() - 2 * 3600, time.time() - 2 * 3600))
    coordinator.reports["run-1"] = (b"<html>NEWER</html>", "etag-two")
    link(admin)
    assert [p.name for p in (cache(data_dir) / "run-1").iterdir()] == ["etag-two"]


def test_each_run_has_its_own_folder(admin, coordinator, data_dir):
    coordinator.reports["run-2"] = (b"<html>TWO</html>", "etag-one")        # same etag, different run
    a, b = link(admin, "run-1").get_json(), link(admin, "run-2").get_json()
    assert admin.get(a["url"]).data == HTML and admin.get(b["url"]).data == b"<html>TWO</html>"


def test_a_weak_etag_names_the_folder_without_the_marker(admin, coordinator, data_dir):
    coordinator.reports["run-1"] = (HTML, "abc123")
    link(admin)
    assert [p.name for p in (cache(data_dir) / "run-1").iterdir()] == ["abc123"]


def test_a_missing_or_odd_etag_falls_back_to_a_hash_of_the_content(admin, coordinator, data_dir):
    import hashlib
    coordinator.reports["run-1"] = (HTML, None)                       # no ETag header at all
    link(admin)
    assert [p.name for p in (cache(data_dir) / "run-1").iterdir()] == [hashlib.sha256(HTML).hexdigest()]


@pytest.mark.parametrize("raw", ["../../escape", "a/b", "x" * 200, "a b", ""])
def test_an_etag_that_is_not_a_safe_folder_name_is_never_used_as_one(raw, admin, coordinator, data_dir):
    import hashlib
    coordinator.reports["run-1"] = (HTML, raw)
    assert link(admin).status_code == 200
    names = [p.name for p in (cache(data_dir) / "run-1").iterdir()]
    assert names == [hashlib.sha256(HTML).hexdigest()] and not (data_dir / "escape").exists()


# ── errors ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("canned, status, message", [
    ((404, "No Allure data for this run"), 404, "No Allure data for this run"),
    ((404, "Not Found"), 404, "no report for this run"),
    ((503, "Allure CLI is not installed on the coordinator"), 503, "Allure CLI is not installed"),
    ((503, "Report generation is busy, try again shortly"), 503, "busy"),
    ((502, "Report generation failed: boom"), 502, "Report generation failed: boom"),
    ((504, "Report generation timed out"), 502, "timed out"),
    ((500, "Internal Server Error"), 502, "Internal Server Error"),
    ((401, "Missing or invalid X-API-Token"), 502, "X-API-Token")])
def test_coordinator_errors_map_to_json_with_sensible_statuses(admin, coordinator, data_dir, canned, status, message):
    coordinator.errors["run-1"] = canned
    r = link(admin)
    assert r.status_code == status and message in r.get_json()["error"]
    assert not cache(data_dir).exists() or not list(cache(data_dir).rglob("report.html"))


def test_a_long_failure_message_is_cut_short(admin, coordinator):
    coordinator.errors["run-1"] = (502, "Report generation failed: " + "x" * 5000)
    assert len(link(admin).get_json()["error"]) <= 300


def test_an_unreachable_coordinator_is_a_502(admin, monkeypatch):
    monkeypatch.setenv("GRID_COORDINATOR_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("GRID_API_TOKEN", TOKEN)
    r = link(admin)
    assert r.status_code == 502 and "unreachable" in r.get_json()["error"] and TOKEN not in r.get_data(as_text=True)


def test_no_coordinator_configured_is_a_503_that_says_where_to_set_it(admin, data_dir):
    r = link(admin)
    assert r.status_code == 503 and "Admin Panel" in r.get_json()["error"]


def test_a_wrong_token_is_a_502_never_a_401(admin, coordinator, monkeypatch):
    monkeypatch.setenv("GRID_API_TOKEN", "bad-token-9f3a")
    r = link(admin)
    assert r.status_code == 502 and "bad-token-9f3a" not in r.get_data(as_text=True)


def test_an_empty_report_is_refused(admin, coordinator, data_dir):
    coordinator.reports["run-1"] = (b"", "etag-one")
    r = link(admin)
    assert r.status_code == 502 and "empty" in r.get_json()["error"] and not cache(data_dir).exists()


@pytest.mark.parametrize("streamed", [False, True])
def test_a_report_over_the_size_cap_is_refused_and_not_kept(admin, coordinator, data_dir, monkeypatch, streamed):
    from amplify.routes import grid
    monkeypatch.setattr(grid, "REPORT_MAX_BYTES", 2000)
    coordinator.reports["run-1"] = (b"<html>" + b"x" * 5000 + b"</html>", "etag-one")
    coordinator.streamed = streamed
    r = link(admin)
    assert r.status_code == 502 and "larger than 2000 bytes" in r.get_json()["error"]
    assert not cache(data_dir).exists()


def test_the_download_uses_a_long_timeout_and_a_200_mib_cap():
    from amplify.routes import grid
    assert grid.REPORT_TIMEOUT == 150 and grid.REPORT_MAX_BYTES == 200 * 1024 * 1024


@pytest.mark.parametrize("run_id", ["..", ".", "a/b", "a b", "", "x" * 101, "-x", ".hidden", "run-1\n", "../../etc/passwd",
                                    "run-1/../../x", None])
def test_a_run_id_that_is_not_a_plain_name_is_not_found_and_never_reaches_the_coordinator(admin, coordinator, data_dir, run_id):
    r = admin.post("/api/dashboard/report-link", json={"source": "grid", "run_id": run_id})
    assert r.status_code == 404 and coordinator.calls == [] and not cache(data_dir).exists()


# ── request validation, login, other users ────────────────────────────────────

def test_an_unknown_source_is_a_400_and_local_is_still_the_default(admin, coordinator):
    assert admin.post("/api/dashboard/report-link", json={"source": "cloud", "run_id": "x"}).status_code == 400
    assert admin.post("/api/dashboard/report-link", json={"run_id": "local-1"}).status_code == 404    # default: local
    assert admin.post("/api/dashboard/report-link", json={"source": "local", "run_id": "local-1"}).status_code == 404
    assert coordinator.calls == []                                                                 # never asked the grid


def test_a_report_link_needs_a_login(app, coordinator):
    assert link(app.test_client()).status_code == 401 and coordinator.calls == []


def test_someone_elses_link_is_not_found_but_everyone_can_make_their_own(admin, user, coordinator):
    url = link(admin).get_json()["url"]
    assert admin.get(url).status_code == 200 and user.get(url).status_code == 404
    mine = link(user)
    assert mine.status_code == 200 and user.get(mine.get_json()["url"]).data == HTML
    assert admin.get(mine.get_json()["url"]).status_code == 404


# ── what a link can reach ─────────────────────────────────────────────────────

def test_a_link_exposes_only_its_one_file(admin, coordinator, data_dir):
    coordinator.reports["run-2"] = (b"<html>OTHER RUN</html>", "etag-other")
    link(admin, "run-2")
    run_dir = cache(data_dir) / "run-1"
    url = link(admin).get_json()["url"]
    (run_dir / "secret.json").write_text('{"secret": true}')                  # next to the version folder
    (run_dir / "etag-one" / "data").mkdir()
    os.symlink(data_dir / "amplify.db", run_dir / "etag-one" / "leak.json")  # a planted symlink
    base = url.rsplit("/", 1)[0]
    assert admin.get(url).data == HTML and admin.get(f"{base}/").data == HTML
    for path in ("../secret.json", "..%2fsecret.json", "%2e%2e/secret.json", "../etag-other/report.html",
                 "../../run-2/etag-other/report.html", "..%2f..%2frun-2%2fetag-other%2freport.html",
                 "leak.json", "data/x.json", "nothing.html"):
        assert admin.get(f"{base}/{path}").status_code == 404, path


def test_the_version_folder_holds_nothing_but_the_report(admin, coordinator, data_dir):
    link(admin)
    assert os.listdir(cache(data_dir) / "run-1" / "etag-one") == ["report.html"]     # no temp file left behind


# ── clean-up of old cache entries ─────────────────────────────────────────────

def seed(data_dir, run, etag, age_days):
    path = cache(data_dir) / run / etag / "report.html"
    path.parent.mkdir(parents=True)
    path.write_text("<html>old</html>")
    stamp = time.time() - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def test_entries_older_than_a_week_are_deleted_and_younger_ones_kept(admin, coordinator, data_dir):
    stale_run = seed(data_dir, "old-run", "e1", 8)
    stale_version = seed(data_dir, "run-1", "e-old", 7.5)
    fresh_other = seed(data_dir, "other-run", "e2", 6.5)
    link(admin)
    assert not stale_run.exists() and not stale_run.parent.parent.exists()          # its run folder went too
    assert not stale_version.exists()
    assert fresh_other.exists()


def test_using_a_report_keeps_it_alive(admin, coordinator, data_dir):
    link(admin)
    path = cache(data_dir) / "run-1" / "etag-one" / "report.html"
    old = time.time() - 6.9 * DAY
    os.utime(path, (old, old))
    link(admin)                                                              # a 304: counts as use
    assert time.time() - path.stat().st_mtime < 60


def test_prune_never_raises_and_ignores_stray_files(data_dir):
    grid_reports.prune()                                                      # no cache folder yet
    (cache(data_dir)).mkdir()
    (cache(data_dir) / "stray.txt").write_text("x")
    keep = seed(data_dir, "r", "e", 1)
    grid_reports.prune(max_age_s=7 * DAY)
    assert keep.exists() and (cache(data_dir) / "stray.txt").exists()
    grid_reports.prune(max_age_s=7 * DAY, now=time.time() + 30 * DAY)         # a month later: everything is stale
    assert not keep.exists()


# ── grid reports are sandboxed (their content comes from tests in any repo) ────

def test_a_grid_report_gets_an_opaque_origin_so_its_scripts_cannot_act_as_the_user(admin, coordinator):
    csp = admin.get(link(admin).get_json()["url"]).headers["Content-Security-Policy"]
    assert csp.startswith("sandbox ") and "allow-scripts" in csp and "allow-same-origin" not in csp
    assert "frame-ancestors 'self'" in csp


def test_local_reports_are_not_sandboxed_they_need_to_load_their_own_files(admin, tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<html>local</html>")
    local = report_viewer.mint(1, page)
    assert admin.get(f"/rv/{local}/index.html").headers["Content-Security-Policy"] == report_viewer.DEFAULT_CSP
    assert "sandbox" not in report_viewer.DEFAULT_CSP


def test_the_flag_is_part_of_the_signed_token_and_a_token_without_it_means_not_sandboxed(tmp_path):
    import time
    import jwt
    from amplify.config import settings
    page = tmp_path / "r.html"
    page.write_text("x")
    assert report_viewer.verify(report_viewer.mint(1, page, sandbox=True))["sandbox"] is True
    assert report_viewer.verify(report_viewer.mint(1, page))["sandbox"] is False
    old = jwt.encode({"uid": 1, "dir": str(tmp_path), "entry": "r.html", "aud": report_viewer.AUDIENCE,
                      "exp": int(time.time()) + 60}, settings.JWT_SECRET_KEY, algorithm="HS256")
    assert report_viewer.verify(old)["sandbox"] is False


def test_the_sandbox_flag_cannot_be_switched_off_by_editing_the_token(admin, coordinator):
    import base64, json
    token = link(admin).get_json()["url"].split("/")[2]
    head, payload, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sbx"] = False
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    assert admin.get(f"/rv/{head}.{forged}.{sig}/report.html").status_code == 404


# ── the sandboxed frame blocks storage, and Allure's report reads it at start-up ──

PAGE = b"<!doctype html><html><head><meta charset='utf-8'><title>Allure</title></head><body>report</body></html>"


def test_the_storage_fallback_goes_first_in_head_and_nothing_else_changes():
    out = grid_reports.with_storage_shim(PAGE)
    assert out.replace(grid_reports.STORAGE_SHIM, b"") == PAGE                                    # the page itself is untouched
    assert out.index(grid_reports.STORAGE_SHIM) == PAGE.index(b"<head>") + len(b"<head>")         # right after <head>
    assert out.startswith(b"<!doctype html>")


def test_a_head_tag_with_attributes_or_any_case_is_found():
    for tag in (b"<HEAD>", b"<head lang='en'>"):
        page = b"<!DOCTYPE html><html>" + tag + b"<title>x</title></head><body></body></html>"
        out = grid_reports.with_storage_shim(page)
        assert out.index(grid_reports.STORAGE_SHIM) == page.index(tag) + len(tag)


def test_a_page_with_no_head_near_the_top_is_left_alone():
    assert grid_reports.with_storage_shim(b"<html><body>no head</body></html>") == b"<html><body>no head</body></html>"
    far = b"<!doctype html>" + b" " * 5000 + b"<head></head>"
    assert grid_reports.with_storage_shim(far) == far


def test_the_fallback_is_valid_javascript():
    import shutil, subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = grid_reports.STORAGE_SHIM.removeprefix(b"<script>").removesuffix(b"</script>")
    check = subprocess.run([node, "--check", "-"], input=script, capture_output=True)
    assert check.returncode == 0, check.stderr.decode()


def test_the_fallback_only_replaces_storage_that_is_blocked():
    import shutil, subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = grid_reports.STORAGE_SHIM.removeprefix(b"<script>").removesuffix(b"</script>").decode()
    probe = f"""
    function run(blocked) {{
      const store = {{ real: true, getItem() {{ if (blocked) throw new Error('SecurityError'); return null; }} }};
      const win = {{}}; Object.defineProperty(win, 'localStorage', {{ get() {{ if (blocked) throw new Error('SecurityError'); return store; }}, configurable: true }});
      Object.defineProperty(win, 'sessionStorage', {{ get() {{ if (blocked) throw new Error('SecurityError'); return store; }}, configurable: true }});
      const doc = {{}}; Object.defineProperty(doc, 'cookie', {{ get() {{ if (blocked) throw new Error('SecurityError'); return 'a=b'; }}, set(v) {{}}, configurable: true }});
      new Function('window', 'document', {script!r})(win, doc);
      return {{ win, doc }};
    }}
    const open = run(false); if (!open.win.localStorage.real) throw new Error('replaced working storage');
    const shut = run(true);
    shut.win.localStorage.setItem('theme', 'dark');
    if (shut.win.localStorage.getItem('theme') !== 'dark') throw new Error('fallback does not store');
    if (shut.win.localStorage.getItem('missing') !== null) throw new Error('missing key should be null');
    if (shut.win.sessionStorage.length !== 1 && shut.win.sessionStorage.length !== 0) throw new Error('length');
    shut.doc.cookie = 'k=v'; if (shut.doc.cookie !== 'k=v') throw new Error('cookie fallback');
    console.log('ok');
    """
    done = subprocess.run([node, "-e", probe], capture_output=True, text=True)
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr


def test_the_copy_kept_on_disk_carries_the_fallback(admin, data_dir, monkeypatch):
    reply = SimpleNamespace(ok=True, status=200, headers={"ETag": '"shimmed"'}, body=PAGE, error=None)
    monkeypatch.setattr(grid_reports, "coordinator_download", lambda *a, **k: reply)
    path = grid_reports.fetch_report("run-shim")
    assert path.read_bytes() == grid_reports.with_storage_shim(PAGE) and grid_reports.STORAGE_SHIM in path.read_bytes()
