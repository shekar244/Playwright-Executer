"""
Blueprint: Zephyr / Jira routes
  All /api/zephyr/* and /api/jira/* endpoints
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import ssl as _ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as _uuid
from collections import OrderedDict, defaultdict
from pathlib import Path

from flask import Blueprint, jsonify, request

from ui_launcher.config_reader import ConfigReader

bp = Blueprint("zephyr", __name__)

ZAPI_BASE        = "https://prod-api.zephyr4jiracloud.com/connect"
JIRA_REST        = "/rest/api/2"
ZEPHYR_CONFIG_KEY = "zephyr"

STATUS_MAP = {
    "pass": 1, "passed": 1, "p": 1,
    "fail": 2, "failed": 2, "f": 2,
    "wip": 3, "in progress": 3,
    "blocked": 4,
    "unexecuted": -1, "skip": -1, "skipped": -1,
}


# ── SSL / HTTP helpers ─────────────────────────────────────────────────────────

def _ssl_ctx(verify: bool = True) -> _ssl.SSLContext:
    if not verify:
        ctx = _ssl._create_unverified_context()
        ctx.check_hostname = False
        ctx.verify_mode    = _ssl.CERT_NONE
        if hasattr(_ssl, "OP_LEGACY_SERVER_CONNECT"):
            ctx.options |= _ssl.OP_LEGACY_SERVER_CONNECT
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=0")
        except _ssl.SSLError:
            pass
        return ctx
    try:
        import certifi
        return _ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    return _ssl.create_default_context()


def _urlopen(req: urllib.request.Request, timeout: int = 20):
    verify = _z_cfg().get("verify_ssl", False)
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(verify))


def _parse_response(raw: bytes, status: int) -> tuple:
    if not raw or not raw.strip():
        return {"ok": True}, status
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        return json.loads(text), status
    except (json.JSONDecodeError, ValueError):
        return {"raw": text, "ok": True}, status


def _parse_error(e: urllib.error.HTTPError) -> tuple:
    raw_err = e.read()
    try:    err = json.loads(raw_err.decode("utf-8", errors="replace"))
    except: err = {"message": raw_err.decode("utf-8", errors="replace") or str(e)}
    return {"error": err}, e.code


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _canonical_qs(params: dict | None) -> str:
    if not params:
        return ""
    items: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for v in value:
                items.append((str(key), str(v)))
        else:
            items.append((str(key), str(value)))
    items.sort(key=lambda x: (x[0], x[1]))
    safe = "~._-"
    return "&".join(
        f"{urllib.parse.quote(k, safe=safe)}={urllib.parse.quote(v, safe=safe)}"
        for k, v in items
    )


def _build_qsh(method: str, api_path: str, query_params: dict | None = None) -> str:
    path  = api_path if api_path.startswith("/") else f"/{api_path}"
    query = _canonical_qs(query_params or {})
    canonical = f"{method.upper().strip()}&{path}&{query}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _zephyr_jwt(access_key: str, secret_key: str, account_id: str,
                method: str, path: str, query_params: dict | None = None,
                expires_in: int = 3600) -> str:
    now   = int(time.time())
    nonce = _uuid.uuid4().hex
    qsh = _build_qsh(method, path, query_params)
    header  = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload = _b64url(json.dumps({
        "sub": account_id, "qsh": qsh, "iss": access_key,
        "iat": now, "exp": now + expires_in, "nonce": nonce,
    }, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def _z_cfg() -> dict:
    return ConfigReader().load().get(ZEPHYR_CONFIG_KEY, {})


def _z_call(method: str, path: str, params: dict | None = None, body=None) -> tuple:
    cfg = _z_cfg()
    ak = cfg.get("access_key", "").strip()
    sk = cfg.get("secret_key", "").strip()
    ai = cfg.get("account_id", "").strip()
    if not (ak and sk and ai):
        return {"error": "Zephyr not configured — add Access Key, Secret Key and Account ID"}, 400
    token = _zephyr_jwt(ak, sk, ai, method, path, params, expires_in=60)
    hdrs = {"Authorization": f"JWT {token}", "zapiAccessKey": ak, "Accept": "application/json"}
    if method.upper() in ("POST", "PUT", "PATCH"):
        hdrs["Content-Type"] = "application/json"
    url = ZAPI_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    try:
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        with _urlopen(req, timeout=20) as resp:
            return _parse_response(resp.read(), resp.status)
    except urllib.error.HTTPError as e:
        return _parse_error(e)
    except Exception as ex:
        return {"error": str(ex)}, 500


def _jira_call(method: str, path: str, params: dict | None = None, body=None) -> tuple:
    cfg = _z_cfg()
    jira_url = cfg.get("jira_url", "").rstrip("/")
    if not jira_url:
        return {"error": "Jira URL not configured"}, 400
    creds = base64.b64encode(f"{cfg.get('username','')}:{cfg.get('api_token','')}".encode()).decode()
    hdrs = {"Authorization": f"Basic {creds}", "Content-Type": "application/json", "Accept": "application/json"}
    url = jira_url + JIRA_REST + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    try:
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        with _urlopen(req, timeout=20) as resp:
            return _parse_response(resp.read(), resp.status)
    except urllib.error.HTTPError as e:
        return _parse_error(e)
    except Exception as ex:
        return {"error": str(ex)}, 500


def _multipart(fields: dict, file_field: str, filename: str, file_bytes: bytes,
               mime: str = "application/octet-stream") -> tuple[bytes, str]:
    boundary = b"----ZephyrBound7MA4YW"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(b"--" + boundary + b"\r\n"
                     b'Content-Disposition: form-data; name="' + name.encode() + b'"\r\n\r\n'
                     + str(value).encode() + b"\r\n")
    parts.append(b"--" + boundary + b"\r\n"
                 + f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
                 + f"Content-Type: {mime}\r\n\r\n".encode()
                 + file_bytes + b"\r\n")
    parts.append(b"--" + boundary + b"--\r\n")
    return b"".join(parts), f"multipart/form-data; boundary={boundary.decode()}"


def _jira_search_jql(jql: str, max_results: int = 200) -> tuple:
    cfg      = _z_cfg()
    jira_url = cfg.get("jira_url", "").rstrip("/")
    if not jira_url:
        return {"error": "Jira URL not configured"}, 400
    creds = base64.b64encode(f"{cfg.get('username','')}:{cfg.get('api_token','')}".encode()).decode()
    hdrs = {"Authorization": f"Basic {creds}", "Content-Type": "application/json", "Accept": "application/json"}
    fields = "summary,issuetype,status,priority,assignee,key,id"
    qs  = urllib.parse.urlencode({"jql": jql, "maxResults": max_results, "fields": fields})
    url = f"{jira_url}/rest/api/latest/search/jql?{qs}"
    try:
        req = urllib.request.Request(url, headers=hdrs, method="GET")
        with _urlopen(req, timeout=20) as resp:
            data, code = _parse_response(resp.read(), resp.status)
            if isinstance(data, dict) and "issues" in data:
                return data, code
    except urllib.error.HTTPError as e:
        if e.code not in (404, 405, 410):
            return _parse_error(e)
    except Exception:
        pass
    return _jira_call("GET", "/search", {"jql": jql, "maxResults": max_results, "fields": fields})


def _z_get_all_executions(path: str, base_params: dict, page_size: int = 50) -> list[dict]:
    all_execs: list[dict] = []
    offset = 0
    while True:
        params = {**base_params, "size": str(page_size), "offset": str(offset)}
        data, code = _z_call("GET", path, params)
        if code != 200:
            break
        page = data.get("searchObjectList", data.get("executions", []))
        all_execs.extend(page)
        total = data.get("totalCount", data.get("total", len(page)))
        if len(page) < page_size or offset + page_size >= int(total):
            break
        offset += page_size
    return all_execs


def _z_get_execution_id(issue_id: str, project_id: str, version_id: str,
                        folder_id: str = "", cycle_id: str = "") -> str | None:
    offset, limit = 0, 50
    while True:
        params: dict = {"issueId": issue_id, "projectId": project_id,
                        "versionId": version_id, "offset": offset, "limit": limit}
        data, code = _z_call("GET", "/public/rest/api/1.0/executions", params)
        if code != 200:
            break
        executions = data.get("executions", [])
        if not executions:
            break
        for wrapper in executions:
            exec_obj       = wrapper.get("execution", wrapper)
            current_folder = str(exec_obj.get("folderId", "") or "")
            if folder_id:
                if current_folder == str(folder_id):
                    return str(exec_obj.get("id", ""))
            else:
                return str(exec_obj.get("id", ""))
        total = int(data.get("totalCount", 0))
        offset += limit
        if offset >= total:
            break
    return None


def _z_update_execution(exec_id: str, issue_id: str, project_id: str,
                        status_id: int, cycle_id: str, version_id: str,
                        comment: str = "") -> tuple:
    api_path   = f"/public/rest/api/1.0/execution/{exec_id}"
    url_params = {"projectId": project_id, "issueId": issue_id}
    body = {
        "status":    {"id": str(status_id)},
        "issueId":   issue_id, "projectId": project_id,
        "cycleId":   cycle_id,
        "versionId": int(version_id) if str(version_id).lstrip("-").isdigit() else -1,
        "comment":   comment,
        "testStepStatusChangeFlag": False,
    }
    cfg    = _z_cfg()
    ak     = cfg.get("access_key",""); sk = cfg.get("secret_key",""); ai = cfg.get("account_id","")
    verify = cfg.get("verify_ssl", False)
    token  = _zephyr_jwt(ak, sk, ai, "PUT", api_path, url_params, expires_in=60)
    hdrs   = {"Authorization": f"JWT {token}", "zapiAccessKey": ak,
              "Content-Type": "application/json", "Accept": "application/json"}
    url = ZAPI_BASE + api_path + "?" + urllib.parse.urlencode(url_params)
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdrs, method="PUT")
        with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx(verify)) as resp:
            return _parse_response(resp.read(), resp.status)
    except urllib.error.HTTPError as e:
        return _parse_error(e)
    except Exception as ex:
        return {"error": str(ex)}, 500


def _update_exec_steps(exec_id: str, issue_id, status_id: int) -> list[dict]:
    sr_data, sr_code = _z_call("GET", "/public/rest/api/1.0/stepresult/search",
                                {"executionId": exec_id, "issueId": str(issue_id)})
    steps_updated: list[dict] = []
    if sr_code != 200:
        return steps_updated
    for sr in sr_data.get("stepResults", []):
        sr_id   = sr.get("id", "")
        step_id = sr.get("stepId", "")
        if not sr_id:
            continue
        upd_body = {"status": {"id": status_id}, "issueId": issue_id,
                    "stepId": step_id, "executionId": exec_id}
        _, code = _z_call("PUT", f"/public/rest/api/1.0/stepresult/{sr_id}", body=upd_body)
        steps_updated.append({"stepResultId": sr_id, "orderId": sr.get("orderId", ""),
                               "updated": code in (200, 201), "status": status_id})
    return steps_updated


def _attach_file_to_exec(exec_id: str, issue_id, cycle_id: str,
                          project_id: str, version_id: str,
                          file_bytes: bytes, filename: str) -> bool:
    cfg = _z_cfg()
    path_att = "/public/rest/api/1.0/attachment"
    params = {
        "comment": "test result", "cycleId": cycle_id,
        "entityId": exec_id, "entityName": "execution",
        "entityType": "execution", "executionId": exec_id,
        "issueId": str(issue_id), "projectId": project_id, "versionId": version_id,
    }
    tok = _zephyr_jwt(cfg.get("access_key",""), cfg.get("secret_key",""),
                      cfg.get("account_id",""), "POST", path_att, params)
    bb, ct = _multipart({}, "file", filename, file_bytes)
    url_att = ZAPI_BASE + path_att + "?" + urllib.parse.urlencode(params)
    hdrs_att = {"Authorization": f"JWT {tok}",
                "zapiAccessKey": cfg.get("access_key",""), "Content-Type": ct}
    try:
        req = urllib.request.Request(url_att, data=bb, headers=hdrs_att, method="POST")
        _urlopen(req, timeout=30).close()
        return True
    except Exception:
        return False


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/api/zephyr/debug")
def zephyr_debug():
    cfg = _z_cfg()
    path   = "/public/rest/api/1.0/cycles/search"
    params = {"projectId": "10000", "versionId": "-1"}
    ak = cfg.get("access_key", ""); sk = cfg.get("secret_key", ""); ai = cfg.get("account_id", "")
    missing = [k for k, v in {"access_key": ak, "secret_key": sk, "account_id": ai}.items() if not v]
    if missing:
        return jsonify({"error": f"Missing config fields: {', '.join(missing)}"}), 400
    try:
        token = _zephyr_jwt(ak, sk, ai, "GET", path, params)
    except Exception as ex:
        return jsonify({"error": f"JWT generation failed: {ex}"}), 500
    jira_url  = cfg.get("jira_url", "").rstrip("/")
    jira_user = cfg.get("username", "")
    jira_tok  = cfg.get("api_token", "")
    data, code = _z_call("GET", "/public/rest/api/1.0/cycles/search", {"projectId": "DUMMY", "versionId": "-1"})
    return jsonify({
        "config_present": {"jira_url": bool(jira_url), "username": bool(jira_user),
                           "api_token": bool(jira_tok), "access_key": bool(ak),
                           "secret_key": bool(sk), "account_id": bool(ai)},
        "jwt_generated": bool(token), "jwt_preview": token,
        "zapi_base": ZAPI_BASE, "zapi_test_status": code, "zapi_test_response": data,
        "jira_url": jira_url or "(not set)",
    })


@bp.route("/api/zephyr/test-jira")
def test_jira():
    data, code = _jira_call("GET", "/myself")
    return jsonify({"status": code, "response": data})


@bp.route("/api/zephyr/config", methods=["GET", "POST"])
def zephyr_config():
    reader = ConfigReader()
    cfg = reader.load()
    if request.method == "POST":
        body = request.json or {}
        cfg[ZEPHYR_CONFIG_KEY] = body
        try:
            reader.save(cfg)
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True})
    return jsonify(cfg.get(ZEPHYR_CONFIG_KEY, {}))


@bp.route("/api/zephyr/projects")
def zephyr_projects():
    data, code = _jira_call("GET", "/project")
    return jsonify(data), code


@bp.route("/api/zephyr/versions")
def zephyr_versions():
    pid = request.args.get("projectKey", "").strip()
    if not pid:
        return jsonify({"error": "projectKey required"}), 400
    data, code = _jira_call("GET", f"/project/{pid}/versions")
    return jsonify(data), code


@bp.route("/api/zephyr/cycles")
def zephyr_cycles():
    path   = "/public/rest/api/1.0/cycles/search"
    params = {k: request.args.get(k) for k in ("projectId", "versionId") if request.args.get(k)}
    data, code = _z_call("GET", path, params)
    return jsonify(data), code


@bp.route("/api/zephyr/cycle", methods=["POST"])
def create_cycle():
    data, code = _z_call("POST", "/public/rest/api/1.0/cycle", body=request.json or {})
    return jsonify(data), code


@bp.route("/api/zephyr/folders")
def zephyr_folders():
    path   = "/public/rest/api/1.0/folders"
    params = {k: request.args.get(k) for k in ("projectId", "versionId", "cycleId") if request.args.get(k)}
    data, code = _z_call("GET", path, params)
    return jsonify(data), code


@bp.route("/api/zephyr/folder", methods=["POST"])
def create_folder():
    data, code = _z_call("POST", "/public/rest/api/1.0/folder", body=request.json or {})
    return jsonify(data), code


@bp.route("/api/zephyr/executions/add", methods=["POST"])
def add_tests_to_cycle():
    body = request.json or {}
    cycle_id  = body.pop("cycleId", "")
    folder_id = body.pop("folderId", None)
    if folder_id:
        path = f"/public/rest/api/1.0/executions/add/folder/{folder_id}"
        body["cycleId"] = cycle_id
    else:
        path = f"/public/rest/api/1.0/executions/add/cycle/{cycle_id}"
    data, code = _z_call("POST", path, body=body)
    return jsonify(data), code


@bp.route("/api/zephyr/executions")
def list_executions():
    cycle_id  = request.args.get("cycleId", "")
    folder_id = request.args.get("folderId", "")
    base: dict = {k: request.args.get(k) for k in ("projectId", "versionId") if request.args.get(k)}
    if folder_id:
        path = f"/public/rest/api/1.0/executions/search/folder/{folder_id}"
        base["cycleId"] = cycle_id
    else:
        path = f"/public/rest/api/1.0/executions/search/cycle/{cycle_id}"
    all_execs = _z_get_all_executions(path, base, page_size=50)
    return jsonify({"searchObjectList": all_execs, "totalCount": len(all_execs)})


@bp.route("/api/zephyr/execution/<exec_id>", methods=["PUT"])
def update_execution(exec_id: str):
    data, code = _z_call("PUT", f"/public/rest/api/1.0/execution/{exec_id}", body=request.json or {})
    return jsonify(data), code


@bp.route("/api/zephyr/executions/bulk", methods=["POST"])
def bulk_update_executions():
    data, code = _z_call("POST", "/public/rest/api/1.0/executions", body=request.json or {})
    return jsonify(data), code


@bp.route("/api/zephyr/stepresults")
def get_step_results():
    params = {k: request.args.get(k) for k in ("executionId", "issueId") if request.args.get(k)}
    data, code = _z_call("GET", "/public/rest/api/1.0/stepresult/search", params)
    return jsonify(data), code


@bp.route("/api/zephyr/stepresult/<sr_id>", methods=["PUT"])
def update_step_result(sr_id: str):
    data, code = _z_call("PUT", f"/public/rest/api/1.0/stepresult/{sr_id}", body=request.json or {})
    return jsonify(data), code


@bp.route("/api/zephyr/attach", methods=["POST"])
def zephyr_attach():
    cfg = _z_cfg()
    if not cfg.get("access_key"):
        return jsonify({"error": "Zephyr not configured"}), 400
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400
    file_bytes = file.read()
    filename   = file.filename or "attachment"
    path       = "/public/rest/api/1.0/attachment"
    params     = {k: request.form.get(k) for k in
                  ("projectId", "issueId", "versionId", "cycleId", "entityId", "entityName")
                  if request.form.get(k)}
    token = _zephyr_jwt(cfg.get("access_key",""), cfg.get("secret_key",""),
                        cfg.get("account_id",""), "POST", path, params)
    body_bytes, ct = _multipart({}, "file", filename, file_bytes)
    url = ZAPI_BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    hdrs = {"Authorization": f"JWT {token}", "zapiAccessKey": cfg.get("access_key",""), "Content-Type": ct}
    try:
        req = urllib.request.Request(url, data=body_bytes, headers=hdrs, method="POST")
        with _urlopen(req, timeout=30) as resp:
            return jsonify(json.loads(resp.read().decode()))
    except urllib.error.HTTPError as e:
        try:    err = json.loads(e.read().decode())
        except: err = {"message": str(e)}
        return jsonify({"error": err}), e.code
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@bp.route("/api/jira/search")
def jira_search():
    jql         = request.args.get("jql", "").strip()
    filter_id   = request.args.get("filterId", "").strip()
    max_results = min(int(request.args.get("maxResults", 200)), 500)
    if filter_id:
        f_data, f_code = _jira_call("GET", f"/filter/{filter_id}")
        if f_code != 200:
            return jsonify({"error": f"Filter lookup failed ({f_code}): {f_data}"}), f_code
        jql = f_data.get("jql", "")
    if not jql:
        return jsonify({"error": "jql or filterId required"}), 400
    data, code = _jira_search_jql(jql, max_results)
    return jsonify(data), code


@bp.route("/api/jira/project-info")
def jira_project_info():
    key = request.args.get("key", "").strip() or _z_cfg().get("project_key","")
    if not key:
        return jsonify({"error": "key param required"}), 400
    data, code = _jira_call("GET", f"/project/{key}")
    if code != 200:
        return jsonify({"error": data}), code
    return jsonify({
        "id": data.get("id"), "key": data.get("key"), "name": data.get("name"),
        "issueTypes": [{"id": t["id"], "name": t["name"]} for t in data.get("issueTypes", [])],
    })


@bp.route("/api/jira/link-types")
def jira_link_types():
    data, code = _jira_call("GET", "/issueLinkType")
    return jsonify(data), code


@bp.route("/api/jira/link", methods=["POST"])
def jira_link():
    body = request.json or {}
    data, code = _jira_call("POST", "/issueLink", body=body)
    return jsonify(data if data else {"ok": True}), code if code != 204 else 200


@bp.route("/api/zephyr/import-testcases-grouped", methods=["POST"])
def import_testcases_grouped():
    file        = request.files.get("file")
    cycle_id    = request.form.get("cycleId", "")
    folder_id   = request.form.get("folderId", "")
    project_key = request.form.get("projectKey", "")
    version_id  = request.form.get("versionId", "-1")
    link_type   = request.form.get("linkType", "Tests")
    if not file or not project_key:
        return jsonify({"error": "file and projectKey are required"}), 400

    app_cfg = ConfigReader().load()
    mapping = app_cfg.get("zephyr_tc_mapping", app_cfg.get("zephyr_mapping", {}))
    col_story   = mapping.get("story_id",            "Story ID")
    col_summary = mapping.get("summary",             "Summary")
    col_desc    = mapping.get("description",         "")
    col_priority= mapping.get("priority",            "")
    col_labels  = mapping.get("labels",              "")
    issue_type_name    = mapping.get("issue_type_name", "Test")
    issue_type_id_cfg  = mapping.get("issue_type_id", "").strip()
    col_type           = issue_type_name
    custom_fields = mapping.get("custom_fields", [])
    steps_fmt = mapping.get("steps_format", "rows")
    step_act  = mapping.get("step_action_prefix",   "Step")
    step_dat  = mapping.get("step_data_prefix",     "Data")
    step_exp  = mapping.get("step_expected_prefix", "Expected")

    content = file.read().decode("utf-8-sig")
    rows    = list(csv.DictReader(io.StringIO(content)))
    vi      = int(version_id) if str(version_id).lstrip("-").isdigit() else -1

    numeric_pid   = project_key
    issue_type_id = None
    proj_data, proj_code = _jira_call("GET", f"/project/{project_key}")
    _proj_debug = {"proj_code": proj_code, "proj_id": None, "issueTypes": [], "issue_type_id": None}
    if proj_code == 200:
        if proj_data.get("id"):
            numeric_pid = str(proj_data["id"])
            _proj_debug["proj_id"] = numeric_pid
        its = proj_data.get("issueTypes", [])
        _proj_debug["issueTypes"] = [{"id": t.get("id"), "name": t.get("name")} for t in its]
        target_name = (issue_type_name or "Test").strip().lower()
        for it in its:
            if it.get("name", "").strip().lower() == target_name:
                issue_type_id = str(it["id"]); break
        _proj_debug["issue_type_id"] = issue_type_id

    def _row_step(row):
        def _get(col, *aliases):
            v = row.get(col, "")
            if not v:
                for a in aliases:
                    v = row.get(a, "")
                    if v: break
            return (v or "").strip()
        act = _get(step_act, "Test Step", "Step", "Action", "Step Action")
        if not act: return None
        return {"step": act, "data": _get(step_dat, "Test Data", "Data", "Step Data"),
                "result": _get(step_exp, "Test Result", "Result", "Expected Result")}

    def _upload_steps(issue_id, steps):
        uploaded = []
        path = f"/public/rest/api/1.0/teststep/{issue_id}"
        params = {"projectId": numeric_pid}
        for order, step in enumerate(steps, start=1):
            if not step.get("step"): continue
            resp_data, code = _z_call("POST", path, params=params,
                body={"step": step["step"], "data": step.get("data",""), "result": step.get("result","")})
            ok = code in (200, 201)
            uploaded.append({"order": order, "step": step["step"][:80], "ok": ok,
                             "code": code, "error": str(resp_data) if not ok else None})
        return uploaded

    existing_folders: dict[str, str] = {}
    if cycle_id:
        fd, _ = _z_call("GET", "/public/rest/api/1.0/folders",
                         {"cycleId": cycle_id, "versionId": version_id, "projectId": numeric_pid})
        for f in (fd if isinstance(fd, list) else fd.get("folders", [])):
            existing_folders[f.get("name", "")] = str(f.get("id", ""))

    story_tests: dict[str, dict] = defaultdict(OrderedDict)
    for row in rows:
        story   = row.get(col_story, "").strip()
        summary = row.get(col_summary, "").strip()
        if not summary: continue
        if summary not in story_tests[story]:
            story_tests[story][summary] = []
        story_tests[story][summary].append(row)

    results: dict = {"created": [], "errors": [], "skipped": [], "folders": {}, "_debug": _proj_debug}

    for story_id, test_cases in story_tests.items():
        target_folder_id = existing_folders.get(story_id, "")
        if story_id and not target_folder_id and cycle_id:
            fd_body = {"name": story_id, "cycleId": cycle_id, "projectId": numeric_pid, "versionId": vi}
            new_fd, fd_code = _z_call("POST", "/public/rest/api/1.0/folder", body=fd_body)
            if fd_code in (200, 201):
                target_folder_id = str(new_fd.get("id", ""))
                existing_folders[story_id] = target_folder_id
                results["folders"][story_id] = {"id": target_folder_id, "created": True}
            else:
                results["errors"].append({"story": story_id,
                                          "error": f"folder creation failed ({fd_code}): {new_fd}"}); continue
        elif story_id and target_folder_id:
            results["folders"][story_id] = {"id": target_folder_id, "created": False}
        use_folder = target_folder_id or folder_id

        for test_name, test_rows in test_cases.items():
            primary = test_rows[0]
            effective_id = issue_type_id_cfg or issue_type_id
            fields: dict = {
                "project":   {"key": project_key},
                "issuetype": {"id": effective_id} if effective_id else {"name": col_type or "Test"},
                "summary":   test_name,
            }
            desc_val = (primary.get(col_desc) or primary.get("Description") or primary.get("DESCRIPTION") or "").strip()
            if desc_val: fields["description"] = desc_val
            if col_priority and primary.get(col_priority):
                fields["priority"] = {"name": primary[col_priority].strip()}
            if col_labels and primary.get(col_labels):
                fields["labels"] = [l.strip() for l in primary[col_labels].split(",") if l.strip()]
            for cf in custom_fields:
                csv_col = cf.get("csv_col","").strip(); jira_field = cf.get("jira_field","").strip()
                field_type = cf.get("field_type","text")
                if not csv_col or not jira_field: continue
                raw = primary.get(csv_col, "")
                if raw is None or str(raw).strip() == "": continue
                val = str(raw).strip()
                if field_type == "list": fields[jira_field] = [v.strip() for v in val.split(",") if v.strip()]
                elif field_type == "object": fields[jira_field] = {"name": val}
                elif field_type == "number":
                    try: fields[jira_field] = float(val)
                    except: pass
                else: fields[jira_field] = val

            issue_data, issue_code = _jira_call("POST", "/issue", body={"fields": fields})
            if issue_code not in (200, 201):
                results["errors"].append({"summary": test_name, "story": story_id, "error": issue_data,
                    "sent_fields": {k: v for k, v in fields.items() if k in ("project","issuetype","summary")}}); continue

            issue_key = issue_data.get("key", ""); issue_id = issue_data.get("id", "")
            steps = [s for row in test_rows for s in [_row_step(row)] if s]
            step_results = _upload_steps(issue_id, steps)

            link_ok = False
            if story_id:
                _, lc = _jira_call("POST", "/issueLink", body={
                    "type": {"name": link_type},
                    "inwardIssue": {"key": issue_key}, "outwardIssue": {"key": story_id},
                })
                link_ok = lc in (200, 201, 204)

            enrol = {"issues": [issue_key], "method": 1, "projectId": numeric_pid,
                     "versionId": vi, "assigneeType": "currentUser"}
            enrol_ok = False
            if use_folder and cycle_id:
                enrol["cycleId"] = cycle_id
                _, ec = _z_call("POST", f"/public/rest/api/1.0/executions/add/folder/{use_folder}", body=enrol)
                enrol_ok = ec in (200, 201)
            elif cycle_id:
                _, ec = _z_call("POST", f"/public/rest/api/1.0/executions/add/cycle/{cycle_id}", body=enrol)
                enrol_ok = ec in (200, 201)

            results["created"].append({
                "key": issue_key, "summary": test_name, "story": story_id, "folder": use_folder,
                "steps": len(step_results), "steps_ok": sum(1 for s in step_results if s["ok"]),
                "step_details": step_results, "linked": link_ok, "enrolled": enrol_ok,
            })

    return jsonify({"created": len(results["created"]), "errors": len(results["errors"]),
                    "skipped": len(results["skipped"]), "folders": results["folders"], "details": results})


@bp.route("/api/zephyr/proxy-raw", methods=["POST"])
def zephyr_proxy_raw():
    body   = request.json or {}
    method = body.get("method", "GET").upper()
    path   = body.get("path", "")
    params = body.get("params") or {}
    b      = body.get("body")
    data, code = _z_call(method, path, params if params else None, b)
    return jsonify(data), code


@bp.route("/api/zephyr/mapping/testcase", methods=["GET", "POST"])
def zephyr_mapping_testcase():
    reader = ConfigReader(); cfg = reader.load()
    if request.method == "POST":
        cfg["zephyr_tc_mapping"] = request.json or {}
        try: reader.save(cfg)
        except OSError as exc: return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True})
    return jsonify(cfg.get("zephyr_tc_mapping", {}))


@bp.route("/api/zephyr/mapping/results", methods=["GET", "POST"])
def zephyr_mapping_results():
    reader = ConfigReader(); cfg = reader.load()
    if request.method == "POST":
        cfg["zephyr_results_mapping"] = request.json or {}
        try: reader.save(cfg)
        except OSError as exc: return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True})
    return jsonify(cfg.get("zephyr_results_mapping", {}))


@bp.route("/api/zephyr/mapping", methods=["GET", "POST"])
def zephyr_mapping():
    reader = ConfigReader(); cfg = reader.load()
    if request.method == "POST":
        body = request.json or {}
        cfg["zephyr_tc_mapping"]      = {k: v for k, v in body.items() if not k.startswith("results_")}
        cfg["zephyr_results_mapping"] = {k.replace("results_","",1): v for k, v in body.items() if k.startswith("results_")}
        try: reader.save(cfg)
        except OSError as exc: return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True})
    tc  = cfg.get("zephyr_tc_mapping", {})
    res = cfg.get("zephyr_results_mapping", {})
    return jsonify({**tc, **{"results_" + k: v for k, v in res.items()}})


@bp.route("/api/zephyr/csv-preview", methods=["POST"])
def csv_preview():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400
    content = file.read().decode("utf-8-sig")
    reader  = csv.DictReader(io.StringIO(content))
    headers = list(reader.fieldnames or [])
    preview: list[dict] = []
    for i, row in enumerate(reader):
        if i >= 5: break
        preview.append(dict(row))
    return jsonify({"headers": headers, "preview": preview})


@bp.route("/api/zephyr/import-testcases", methods=["POST"])
def import_testcases():
    file        = request.files.get("file")
    cycle_id    = request.form.get("cycleId", "")
    folder_id   = request.form.get("folderId", "")
    project_key = request.form.get("projectKey", "")
    version_id  = request.form.get("versionId", "-1")
    if not file or not project_key:
        return jsonify({"error": "file and projectKey are required"}), 400

    app_cfg = ConfigReader().load()
    mapping = app_cfg.get("zephyr_tc_mapping", app_cfg.get("zephyr_mapping", {}))
    col_summary    = mapping.get("summary", "Summary")
    col_desc       = mapping.get("description", "")
    col_priority   = mapping.get("priority", "")
    col_labels     = mapping.get("labels", "")
    col_components = mapping.get("components", "")
    col_issue_type = mapping.get("issue_type_name", "Test")
    steps_format   = mapping.get("steps_format", "columns")
    step_act_pfx   = mapping.get("step_action_prefix", "Step Action")
    step_dat_pfx   = mapping.get("step_data_prefix", "Step Data")
    step_exp_pfx   = mapping.get("step_expected_prefix", "Expected Result")
    col_step_all   = mapping.get("step_single_column", "Steps")

    content = file.read().decode("utf-8-sig")
    rows    = list(csv.DictReader(io.StringIO(content)))

    def _jira_fields(row):
        fields: dict = {"project": {"key": project_key}, "issuetype": {"name": col_issue_type or "Test"},
                        "summary": row.get(col_summary, "").strip()}
        if col_desc and row.get(col_desc): fields["description"] = row[col_desc].strip()
        if col_priority and row.get(col_priority): fields["priority"] = {"name": row[col_priority].strip()}
        if col_labels and row.get(col_labels):
            fields["labels"] = [l.strip() for l in row[col_labels].split(",") if l.strip()]
        if col_components and row.get(col_components):
            fields["components"] = [{"name": c.strip()} for c in row[col_components].split(",") if c.strip()]
        return fields

    def _extract_steps(row, headers):
        steps: list[dict] = []
        if steps_format == "columns":
            i = 1
            while True:
                act = row.get(f"{step_act_pfx} {i}", row.get(f"{step_act_pfx}{i}", "")).strip()
                if not act: break
                steps.append({"step": act,
                    "data":   row.get(f"{step_dat_pfx} {i}", row.get(f"{step_dat_pfx}{i}", "")).strip(),
                    "result": row.get(f"{step_exp_pfx} {i}", row.get(f"{step_exp_pfx}{i}", "")).strip()})
                i += 1
        elif steps_format == "single_col" and col_step_all:
            for line in row.get(col_step_all, "").split("\n"):
                if line.strip(): steps.append({"step": line.strip(), "data": "", "result": ""})
        return steps

    headers = list(rows[0].keys()) if rows else []
    results: dict = {"created": [], "errors": [], "skipped": []}

    if steps_format == "rows":
        grouped: dict[str, list] = {}
        for row in rows:
            key = row.get(col_summary, "").strip()
            if not key: continue
            if key not in grouped: grouped[key] = []
            grouped[key].append(row)
        work_items = list(grouped.values())
    else:
        work_items = [[r] for r in rows]

    for group in work_items:
        primary = group[0]
        summary = primary.get(col_summary, "").strip()
        if not summary:
            results["skipped"].append({"row": primary, "reason": "empty summary"}); continue
        fields = _jira_fields(primary)
        jira_data, jira_code = _jira_call("POST", "/issue", body={"fields": fields})
        if jira_code not in (200, 201):
            results["errors"].append({"summary": summary, "error": jira_data}); continue
        issue_key = jira_data.get("key", ""); issue_id = jira_data.get("id", "")
        results["created"].append({"key": issue_key, "summary": summary})

        if steps_format == "rows":
            step_rows = group
        else:
            step_rows = [primary]

        for order, step_row in enumerate(step_rows, start=1):
            steps = _extract_steps(step_row, headers) if steps_format != "rows" else [{
                "step":   step_row.get(step_act_pfx, step_row.get("Step", "")).strip(),
                "data":   step_row.get(step_dat_pfx, step_row.get("Data", "")).strip(),
                "result": step_row.get(step_exp_pfx, step_row.get("Expected", "")).strip(),
            }]
            for step in steps:
                if step.get("step"):
                    path = f"/public/rest/api/1.0/teststep/{issue_id}"
                    _z_call("POST", path, body={"step": step["step"], "data": step.get("data",""), "result": step.get("result","")})

        enrol_body = {"issues": [issue_key], "method": 1, "projectId": project_key,
                      "versionId": int(version_id) if str(version_id).lstrip("-").isdigit() else -1,
                      "assigneeType": "currentUser"}
        if folder_id:
            enrol_body["cycleId"] = cycle_id
            _z_call("POST", f"/public/rest/api/1.0/executions/add/folder/{folder_id}", body=enrol_body)
        elif cycle_id:
            _z_call("POST", f"/public/rest/api/1.0/executions/add/cycle/{cycle_id}", body=enrol_body)

    return jsonify({"created": len(results["created"]), "errors": len(results["errors"]),
                    "skipped": len(results["skipped"]), "details": results})


@bp.route("/api/zephyr/bulk-results", methods=["POST"])
def bulk_results_upload():
    file       = request.files.get("file")
    cycle_id   = request.form.get("cycleId", "")
    folder_id  = request.form.get("folderId", "")
    project_id = request.form.get("projectId", "")
    version_id = request.form.get("versionId", "-1")
    global_attachment    = request.files.get("attachment")
    bulk_status_override = request.form.get("bulkStatus", "")
    if not file:
        return jsonify({"error": "No CSV file provided"}), 400
    if not cycle_id:
        return jsonify({"error": "cycleId is required"}), 400

    content = file.read().decode("utf-8-sig")
    reader  = csv.DictReader(io.StringIO(content))
    rows    = list(reader)

    app_cfg     = ConfigReader().load()
    res_map     = app_cfg.get("zephyr_results_mapping", {})
    col_key     = res_map.get("issue_key",     "Issue Key")
    col_status  = res_map.get("status",        "Status")
    col_comment = res_map.get("comment",       "Comment")
    col_attach  = res_map.get("attachment_path","Attachment Path")
    update_steps = res_map.get("update_steps", True)

    all_keys = [(row.get(col_key) or row.get("Issue Key") or row.get("Test ID")
                 or row.get("Jira ID") or row.get("issueKey") or "").strip() for row in rows]
    all_keys = [k for k in all_keys if k]
    jira_id_map: dict[str, str] = {}
    if all_keys:
        jql_resp, _ = _jira_search_jql(f"issueKey in ({','.join(all_keys)})", max_results=len(all_keys))
        for iss in jql_resp.get("issues", []):
            jira_id_map[iss.get("key","")] = str(iss.get("id",""))

    results: dict = {"success": [], "errors": [], "not_found": []}

    for row_num, row in enumerate(rows, start=2):
        issue_key = (row.get(col_key) or row.get("Issue Key") or row.get("Test ID")
                     or row.get("Jira ID") or row.get("issueKey") or "").strip()
        if not issue_key: continue

        if bulk_status_override:
            status_id  = int(bulk_status_override)
            status_str = {1:"pass",2:"fail",3:"wip",4:"blocked","-1":"unexecuted"}.get(int(bulk_status_override),"pass")
        else:
            status_str = (row.get(col_status) or row.get("Status") or row.get("Result") or "pass").strip().lower()
            status_id  = STATUS_MAP.get(status_str, 1)

        comment         = (row.get(col_comment) or row.get("Comment") or "").strip()
        row_attach_path = (row.get(col_attach) or row.get("Attachment Path") or "").strip()

        issue_id = jira_id_map.get(issue_key, "")
        if not issue_id:
            results["not_found"].append({"row": row_num, "issue": issue_key, "error": "Jira issue not found"}); continue

        exec_id = _z_get_execution_id(issue_id=issue_id, project_id=project_id,
                                       version_id=version_id, folder_id=folder_id, cycle_id=cycle_id)
        if not exec_id:
            results["not_found"].append({"row": row_num, "issue": issue_key,
                                          "error": f"Execution not found in cycle/folder for {issue_key}"}); continue

        upd, upd_code = _z_update_execution(exec_id=exec_id, issue_id=issue_id, project_id=project_id,
                                              status_id=status_id, cycle_id=cycle_id, version_id=version_id, comment=comment)
        if upd_code not in (200, 201):
            results["errors"].append({"row": row_num, "issue": issue_key,
                                       "error": f"Execution update failed ({upd_code}): {upd}"}); continue

        row_result: dict = {"row": row_num, "issue": issue_key, "status": status_str,
                            "execId": exec_id, "steps": [], "attached": []}

        if update_steps:
            row_result["steps"] = _update_exec_steps(exec_id, issue_id, status_id)

        if global_attachment:
            global_attachment.seek(0)
            fb = global_attachment.read()
            fname = global_attachment.filename or "report.html"
            ok = _attach_file_to_exec(exec_id, issue_id, cycle_id, project_id, version_id, fb, fname)
            if ok: row_result["attached"].append(fname)

        if row_attach_path:
            p = Path(row_attach_path)
            if p.exists() and p.is_file():
                try:
                    fb = p.read_bytes()
                    ok = _attach_file_to_exec(exec_id, issue_id, cycle_id, project_id, version_id, fb, p.name)
                    if ok: row_result["attached"].append(str(p.name))
                    else:  row_result["attach_error"] = f"Upload failed for {p.name}"
                except Exception as e:
                    row_result["attach_error"] = str(e)
            else:
                row_result["attach_error"] = f"File not found: {row_attach_path}"

        results["success"].append(row_result)

    return jsonify({"processed": len(rows), "success": len(results["success"]),
                    "errors": len(results["errors"]), "not_found": len(results["not_found"]),
                    "details": results})
