"""Tests for the report generator in artifacts.py: real Allure 3 CLI, fake CLIs, concurrency."""
import asyncio
import os
import re
import shutil
import time
from dataclasses import replace

import pytest

import artifacts
from artifact_helpers import FAKE_OK, age, fake_cli, make_tar, put_allure, report_text, result_archive
from artifacts import ArtifactError, ReportService

REAL_ALLURE = shutil.which("allure")


# --- report generation: real Allure 3 CLI ------------------------------------------
@pytest.mark.skipif(REAL_ALLURE is None, reason="allure CLI is not on PATH")
def test_real_allure_cli_builds_one_single_file_report(store, settings):
    reports = ReportService(replace(settings, allure3_bin=REAL_ALLURE), store)
    put_allure(store, "run-1", "run-1-chrome-001", result_archive(["test_login_works", "test_cart_fails"], "chrome", attachment=True))
    put_allure(store, "run-1", "run-1-firefox-001", result_archive(["test_search_works"], "firefox"))

    report = asyncio.run(reports.render("run-1"))

    assert report.path == settings.report_dir / "run-1" / f"{report.key}.html"
    text = report_text(report.path)
    assert text.lstrip().lower().startswith("<!doctype html")
    for name in ("test_login_works", "test_cart_fails", "test_search_works", "chrome", "firefox"):
        assert name in text, name
    assert '"environment":"chrome"' in text and '"environment":"firefox"' in text   # one Allure environment per browser
    assert "googletagmanager" not in text and "gtag(" not in text   # Allure 3's built-in analytics is stripped
    assert list((settings.report_dir / ".work").iterdir()) == []  # temp results are cleaned up
    assert reports.info("run-1") == {"available": True, "chunks_with_allure": 2, "cached": True}

    # Cached: same file, CLI not run again; a matching If-None-Match is a 304 (no body).
    mtime = report.path.stat().st_mtime_ns
    again = asyncio.run(reports.render("run-1"))
    assert again.path == report.path and report.path.stat().st_mtime_ns == mtime
    assert asyncio.run(reports.render("run-1", f'"{report.key}"')).path is None

    # More chunks arriving invalidate it.
    put_allure(store, "run-1", "run-1-edge-001", result_archive(["test_extra_check"], "edge"))
    assert reports.info("run-1")["cached"] is False
    newer = asyncio.run(reports.render("run-1", f'"{report.key}"'))
    assert newer.key != report.key and "test_extra_check" in report_text(newer.path)
    assert '"environment":"edge"' in report_text(newer.path)   # the new browser got its environment too


# --- report generation: fake CLI (error handling, env, concurrency) ----------------
def service(tmp_path, settings, store, script, **overrides) -> ReportService:
    return ReportService(replace(settings, allure3_bin=fake_cli(tmp_path, script), **overrides), store)


def seed(store, run_id="run-1", chunks=("c1",)):
    for c in chunks:
        put_allure(store, run_id, c, result_archive(["test_a"]))


def render_error(reports, run_id="run-1", **kwargs) -> ArtifactError:
    with pytest.raises(ArtifactError) as exc:
        asyncio.run(reports.render(run_id, **kwargs))
    return exc.value


def test_cli_gets_the_documented_arguments_and_a_clean_environment(tmp_path, settings, store, monkeypatch):
    for name, value in (("API_TOKEN", "tok-1"), ("GRID_API_TOKEN", "tok-2"), ("DATABASE_URL", "postgresql://u:pw@h/d"),
                        ("NODE_OPTIONS", "--max-old-space-size=300"), ("AWS_SECRET_ACCESS_KEY", "aws-1")):
        monkeypatch.setenv(name, value)
    seed(store)
    text = asyncio.run(service(tmp_path, settings, store, FAKE_OK).render("run-1")).path.read_text()
    assert "--single-file" in text and "--report-name run-1" in text
    assert re.search(r"--config \S+/environments\.config\.mjs", text) and "--group-by" not in text  # environments per browser
    assert re.search(r"awesome \S+/results --output \S+/out ", text)
    for leaked in ("tok-1", "tok-2", "pw@h", "aws-1", "API_TOKEN", "DATABASE_URL", "AWS_SECRET"):
        assert leaked not in text, leaked
    assert "NODE_OPTIONS=--max-old-space-size=300" in text and "\nPATH=" in text
    assert (settings.report_dir / ".work").is_dir() and list((settings.report_dir / ".work").iterdir()) == []


def test_a_rejected_environments_config_falls_back_to_grouping_by_browser(tmp_path, settings, store):
    seed(store)
    script = 'case "$*" in *--config*) echo "unsupported field" >&2; exit 2;; esac\n' + FAKE_OK
    text = asyncio.run(service(tmp_path, settings, store, script).render("run-1")).path.read_text()
    assert "--group-by browser" in text and "--config" not in text and "--single-file" in text


def test_when_neither_is_accepted_the_report_is_a_plain_run(tmp_path, settings, store):
    seed(store)
    script = 'case "$*" in *--config*|*--group-by*) echo "unknown option" >&2; exit 2;; esac\n' + FAKE_OK
    text = asyncio.run(service(tmp_path, settings, store, script).render("run-1")).path.read_text()
    assert "--group-by" not in text and "--config" not in text and "--single-file" in text


def test_results_without_a_browser_label_are_grouped_not_given_environments(tmp_path, settings, store):
    put_allure(store, "run-1", "c1", result_archive(["test_a"], browser=None))   # from before the labels existed
    text = asyncio.run(service(tmp_path, settings, store, FAKE_OK).render("run-1")).path.read_text()
    assert "--group-by browser" in text and "--config" not in text


def test_the_environments_config_is_not_left_where_the_cli_would_load_it_by_itself(tmp_path, settings, store):
    """The CLI runs inside the work folder and loads any allurerc.* there unasked, which would defeat the fallback."""
    seen = tmp_path / "seen.txt"
    script = f'ls >> {seen}\n' + FAKE_OK
    seed(store)
    asyncio.run(service(tmp_path, settings, store, script).render("run-1"))
    assert not any(line.startswith("allurerc") for line in seen.read_text().split())


def test_failed_generation_is_a_502_with_a_path_free_stderr_tail(tmp_path, settings, store):
    seed(store)
    boom = 'echo "boom: cannot read /srv/secret/place/results.json" >&2; exit 3\n'
    err = render_error(service(tmp_path, settings, store, boom))
    assert err.status_code == 502
    assert err.detail == "Report generation failed: boom: cannot read results.json"
    long_err = render_error(service(tmp_path, settings, store, 'printf "%0900d" 0 >&2; exit 1\n'))
    assert len(long_err.detail) == len("Report generation failed: ") + 300
    assert render_error(service(tmp_path, settings, store, "exit 0\n")).status_code == 502  # no index.html
    assert list((settings.report_dir / ".work").iterdir()) == [] and not (settings.report_dir / "run-1").exists()


def test_timeout_is_a_504_and_kills_the_cli_process_group(tmp_path, settings, store):
    seed(store)
    pidfile = tmp_path / "child.pid"
    reports = service(tmp_path, settings, store, f"sleep 30 &\necho $! > {pidfile}\nwait\n", report_timeout_seconds=1.0)
    started = time.monotonic()
    err = render_error(reports)
    assert err.status_code == 504 and time.monotonic() - started < 10
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the CLI's child process survived the timeout")
    assert list((settings.report_dir / ".work").iterdir()) == [] and not (settings.report_dir / "run-1").exists()


def test_missing_cli_is_a_503_but_a_matching_etag_still_answers_304(tmp_path, settings, store):
    seed(store)
    reports = ReportService(replace(settings, allure3_bin="no-such-allure-cli-xyz"), store)
    err = render_error(reports)
    assert (err.status_code, err.detail) == (503, "Allure CLI is not installed on the coordinator")
    key = artifacts.archive_key(store.list_allure("run-1"))
    assert asyncio.run(reports.render("run-1", f'"{key}"')).path is None
    assert reports.info("run-1") == {"available": True, "chunks_with_allure": 1, "cached": False}


def test_no_allure_data_is_a_404(tmp_path, settings, store):
    reports = service(tmp_path, settings, store, FAKE_OK)
    err = render_error(reports, "run-nothing")
    assert (err.status_code, err.detail) == (404, "No Allure data for this run")
    assert reports.info("run-nothing") == {"available": False, "chunks_with_allure": 0, "cached": False}
    put_allure(store, "run-empty", "c1", make_tar([]))  # a valid but empty archive
    assert render_error(reports, "run-empty").status_code == 404


def test_oversized_results_are_a_502_without_server_paths(tmp_path, settings, store):
    seed(store, chunks=("c1", "c2"))
    err = render_error(service(tmp_path, settings, store, FAKE_OK, max_allure_files=3))
    assert err.status_code == 502 and str(tmp_path) not in err.detail


def test_npx_means_npx_allure(tmp_path, settings, store, monkeypatch):
    fake_cli(tmp_path, "exit 0\n")
    (tmp_path / "bin" / "npx").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "bin" / "npx").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    for value, argv in (("npx", ["npx", "allure"]), ("npx allure", ["npx", "allure"]),
                        ("npx allure@3.16.0", ["npx", "allure@3.16.0"]), ("allure", ["allure"])):
        reports = ReportService(replace(settings, allure3_bin=value), store)
        if value == "allure" and REAL_ALLURE is None:
            continue
        assert reports._cli_argv() == argv


def test_the_npm_bin_folder_means_the_allure_inside_it(tmp_path, settings, store):
    """ALLURE3_BIN may be what `echo $(npm config get prefix)/bin` prints (where `npm install -g allure` puts it)."""
    folder = tmp_path / "npm-bin"
    folder.mkdir()
    (folder / "allure").write_text("#!/bin/sh\nexit 0\n")
    (folder / "allure").chmod(0o755)
    assert ReportService(replace(settings, allure3_bin=str(folder)), store)._cli_argv() == [str(folder / "allure")]

    (tmp_path / "empty").mkdir()
    with pytest.raises(artifacts.ArtifactError) as err:  # a folder without the CLI is the same 503 as a missing CLI
        ReportService(replace(settings, allure3_bin=str(tmp_path / "empty")), store)._cli_argv()
    assert err.value.status_code == 503


def test_report_concurrency_is_bounded_by_the_semaphore(tmp_path, settings, store):
    log = tmp_path / "log.txt"
    script = FAKE_OK + f'echo start >> {log}; sleep 0.6; echo end >> {log}\n'
    for run in ("run-a", "run-b"):
        seed(store, run)

    async def both(reports):
        return await asyncio.gather(reports.render("run-a"), reports.render("run-b"))

    asyncio.run(both(service(tmp_path, settings, store, script, report_concurrency=1)))
    assert log.read_text().split() == ["start", "end", "start", "end"]
    log.unlink()
    for run in ("run-a", "run-b"):
        shutil.rmtree(settings.report_dir / run)
    asyncio.run(both(service(tmp_path, settings, store, script, report_concurrency=2)))
    assert log.read_text().split()[:2] == ["start", "start"]


def test_a_request_that_cannot_get_a_slot_in_time_is_a_503_with_retry_after(tmp_path, settings, store, monkeypatch):
    seed(store, "run-a")
    seed(store, "run-b")

    def slow_build(self, run_id, argv):  # stands in for a long unpack + generation holding the slot
        time.sleep(1.0)
        return artifacts.Report("k", None)

    monkeypatch.setattr(ReportService, "_build", slow_build)
    reports = service(tmp_path, settings, store, FAKE_OK, report_timeout_seconds=0.3)

    async def scenario():
        first = asyncio.ensure_future(reports.render("run-a"))
        await asyncio.sleep(0.1)
        with pytest.raises(ArtifactError) as exc:
            await reports.render("run-b")
        assert (await first).key == "k"
        return exc.value

    err = asyncio.run(scenario())
    assert err.status_code == 503 and err.headers == {"Retry-After": "5"}


def test_cancelled_request_does_not_free_the_slot_early(tmp_path, settings, store, monkeypatch):
    seed(store, "run-a")
    seed(store, "run-b")
    events = []

    def slow_build(self, run_id, argv):
        events.append(f"start-{run_id}")
        time.sleep(0.5)
        events.append(f"end-{run_id}")
        return artifacts.Report("k", None)

    monkeypatch.setattr(ReportService, "_build", slow_build)
    reports = service(tmp_path, settings, store, FAKE_OK, report_timeout_seconds=10)

    async def scenario():
        first = asyncio.ensure_future(reports.render("run-a"))
        await asyncio.sleep(0.1)
        first.cancel()  # e.g. the HTTP client gave up
        await reports.render("run-b")

    asyncio.run(scenario())
    assert events == ["start-run-a", "end-run-a", "start-run-b", "end-run-b"]


def test_superseded_cached_reports_are_pruned_once_stale(tmp_path, settings, store):
    seed(store)
    reports = service(tmp_path, settings, store, FAKE_OK)
    first = asyncio.run(reports.render("run-1"))
    put_allure(store, "run-1", "c2", result_archive(["test_b"]))
    second = asyncio.run(reports.render("run-1"))
    assert first.path.exists() and second.path.exists()  # fresh: a reader may still hold the old one
    age(first.path, 0.01)
    put_allure(store, "run-1", "c3", result_archive(["test_c"]))
    third = asyncio.run(reports.render("run-1"))
    assert not first.path.exists() and second.path.exists() and third.path.exists()
