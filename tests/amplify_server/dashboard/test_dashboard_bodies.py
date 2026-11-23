"""POST /api/dashboard/report-link: a malformed body or a wrong field type is a JSON 400 (never an HTML 500),
and a corrupt run-history file cannot make the local branch fail."""
import importlib
import time

import pytest
from flask import Flask

from amplify_server.routes.history import save_history


@pytest.fixture
def app(data_dir):
    flask_app = Flask(__name__)
    for name in ("auth", "dashboard"):
        flask_app.register_blueprint(importlib.import_module(f"amplify_server.routes.{name}").bp)
    return flask_app


def post(client, data, content_type="application/json"):
    return client.post("/api/dashboard/report-link", data=data, headers={"Content-Type": content_type})


def test_a_body_that_is_not_a_json_object_is_a_400(admin):
    for data, content_type in [("[1]", "application/json"), ('"grid"', "application/json"), ("7", "application/json"),
                               ("null", "application/json"), ("{broken", "application/json"),
                               ('{"source": "grid"}', "text/plain"), (b"\xff\xfe", "application/json")]:
        r = post(admin, data, content_type)
        assert r.status_code == 400 and r.is_json and "JSON object" in r.get_json()["error"], data


def test_wrong_field_types_are_a_400(admin):
    for body in ({"source": 5, "run_id": "x"}, {"source": ["grid"], "run_id": "x"}, {"source": "grid", "run_id": 5},
                 {"source": "grid", "run_id": ["run-1"]}, {"source": "grid", "run_id": {"a": 1}},
                 {"source": "local", "run_id": "local-1", "uid": 5}, {"source": "local", "run_id": "local-1", "uid": ["u"]},
                 {"run_id": True}, {"source": True}):
        r = admin.post("/api/dashboard/report-link", json=body)
        assert r.status_code == 400 and r.is_json and "must be text" in r.get_json()["error"], body


def test_an_unknown_source_is_a_400_and_an_unknown_run_is_a_404(admin):
    assert admin.post("/api/dashboard/report-link", json={"source": "cloud", "run_id": "x"}).status_code == 400
    assert admin.post("/api/dashboard/report-link", json={"source": "local", "run_id": "local-1"}).status_code == 404
    assert admin.post("/api/dashboard/report-link", json={"source": "grid", "run_id": "../x"}).status_code == 404
    assert admin.post("/api/dashboard/report-link", json={}).status_code == 404            # an empty object: no such local run
    assert post(admin, b"").status_code == 404


def test_a_corrupt_history_file_cannot_break_the_local_branch(admin):
    ts = int(time.time() * 1000)
    save_history(["junk", 5, None, {"ts": ts, "repo": ["x"], "report_path": {"a": 1}, "tests": 5},
                  {"ts": ts + 1, "repo": 7, "report_path": 9, "tests": ["x", None, {"uid": "u1", "name": {}, "report_path": []}]}],
                 user_id=1)
    for run, uid in ((f"local-{ts}", ""), (f"local-{ts + 1}", ""), (f"local-{ts + 1}", "u1"), (f"local-{ts + 1}", "nope"),
                     ("local-0", "")):
        r = admin.post("/api/dashboard/report-link", json={"source": "local", "run_id": run, "uid": uid})
        assert r.status_code in (404,) and r.is_json, (run, uid, r.status_code)
