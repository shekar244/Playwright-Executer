"""The Grid tab and the dashboard render data that a pod's test code, the coordinator or a git folder name can
shape. Stored XSS there would run in an Amplify session (an admin's can reach the Executor), so the real
rendering code is fed hostile data and its HTML is audited. Needs node; skipped without it. The same scenario
was also checked in a real browser (see the fix report)."""
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
JS_DIR = ROOT / "src" / "amplify_server" / "static" / "js"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_hostile_grid_and_dashboard_data_comes_out_escaped():
    result = subprocess.run([NODE, str(HERE / "js" / "ui_escaping_check.js"), str(JS_DIR)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "OK"


def test_no_inline_handler_in_the_grid_script_carries_data():
    """Ids and names go through data attributes and one delegated click handler, never through onclick="f('...')"."""
    source = (JS_DIR / "grid.js").read_text()
    assert "onclick=" not in source
