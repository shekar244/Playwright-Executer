"""
Blueprint: Git command routes
  GET/POST /api/git/commands
  POST     /api/git/run
"""
from __future__ import annotations

import os
import sys

from flask import Blueprint, jsonify, request

from routes import state
from ui_launcher.config_reader import ConfigReader
from ui_launcher.runner import TestRunner

bp = Blueprint("git", __name__)


@bp.route("/api/git/commands", methods=["GET", "POST"])
def git_commands():
    reader = ConfigReader()
    cfg = reader.load()
    if request.method == "POST":
        body = request.json or {}
        cmds = body.get("git_commands")
        if not isinstance(cmds, list):
            return jsonify({"error": "git_commands must be an array"}), 400
        cfg["git_commands"] = cmds
        try:
            reader.save(cfg)
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True})
    return jsonify({"git_commands": cfg.get("git_commands", [])})


@bp.route("/api/git/run", methods=["POST"])
def run_git_command():
    with state._run_lock:
        if state._is_running:
            return jsonify({"error": "Already running"}), 409

        body = request.json or {}
        raw_cmd = body.get("command", "").strip()
        repo = body.get("repo", "").strip()

        if not raw_cmd:
            return jsonify({"error": "No command specified"}), 400

        if not repo or not os.path.isdir(repo):
            repo = ConfigReader().load().get("repo_root", "").strip()
        if not repo or not os.path.isdir(repo):
            return jsonify({"error": "Repo path not found — set repo_root in config"}), 400

        if sys.platform == "win32":
            cmd = ["cmd", "/c"] + raw_cmd.split()
        else:
            import shlex
            cmd = shlex.split(raw_cmd)

        state.broadcast("cmd", raw_cmd)
        state._is_running = True

        def on_output(line):
            state.broadcast("line", line)

        def on_finish(exit_code, cancelled):
            state._is_running = False
            if cancelled:
                state.broadcast("status", "cancelled")
            elif exit_code == 0:
                state.broadcast("status", "passed")
            else:
                state.broadcast("status", f"failed:{exit_code}")
            state.broadcast("done", "")

        state._runner = TestRunner(
            cmd=cmd, cwd=repo, env_overrides={},
            on_output=on_output, on_finish=on_finish,
        )
        state._runner.start()

    return jsonify({"ok": True, "cmd": raw_cmd})
