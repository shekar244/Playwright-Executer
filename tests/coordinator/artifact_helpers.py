"""Shared builders for the artifacts tests (archives, fake CLI scripts, report decoding)."""
import asyncio
import base64
import hashlib
import io
import json
import re
import tarfile
import time
import uuid
import os
from pathlib import Path

SHA = "a" * 40


def make_tar(entries) -> bytes:
    """entries: (kind, name, extra) with kind in file|dir|symlink|hardlink|chr|fifo."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for kind, name, extra in entries:
            ti = tarfile.TarInfo(name)
            if kind == "file":
                ti.size = len(extra)
                tar.addfile(ti, io.BytesIO(extra))
                continue
            ti.type = {"dir": tarfile.DIRTYPE, "symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                       "chr": tarfile.CHRTYPE, "fifo": tarfile.FIFOTYPE}[kind]
            ti.linkname = extra or ""
            tar.addfile(ti)
    return buf.getvalue()


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def body(data: bytes, size: int = 1000):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def result_archive(names, browser="chrome", with_env=True, attachment=False) -> bytes:
    """A pod's allure-results directory contents, as the runner uploads it."""
    entries = []
    for name in names:
        u, now = str(uuid.uuid4()), int(time.time() * 1000)
        result = {"uuid": u, "historyId": u, "name": name, "fullName": f"tests.test_x.{name}",
                  "status": "failed" if "fail" in name else "passed", "stage": "finished",
                  "start": now, "stop": now + 20,
                  "labels": ([{"name": "browser", "value": browser}] if browser else [])
                  + [{"name": "host", "value": "pod-1"}]}   # browser=None: results from before the labels existed
        if attachment:
            result["attachments"] = [{"name": "log", "source": f"{u}-attachment.txt", "type": "text/plain"}]
            entries.append(("file", f"{u}-attachment.txt", b"hello attachment"))
        entries.append(("file", f"{u}-result.json", json.dumps(result).encode()))
    if with_env and browser:
        entries.append(("file", "environment.properties", f"browser={browser}\n".encode()))
    return make_tar(entries)


def put_allure(store, run_id, chunk_id, data):
    return asyncio.run(store.put_allure(run_id, chunk_id, body(data), str(len(data))))


def report_text(path: Path) -> str:
    """Single-file reports embed their data as base64 d("<name>","<b64>") calls: decode them all."""
    html = path.read_text(encoding="utf-8")
    parts = [base64.b64decode(v).decode("utf-8", "replace") for _n, v in re.findall(r'd\("([^"]+)","([A-Za-z0-9+/=]*)"\)', html)]
    return html + "\n".join(parts)


def fake_cli(tmp_path, script: str) -> str:
    path = tmp_path / "bin" / "fake-allure"
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)
    return str(path)


FAKE_OK = '''out=""; args="$*"
while [ $# -gt 0 ]; do [ "$1" = "--output" ] && out="$2"; shift; done
mkdir -p "$out"; { echo "<html>fake"; echo "ARGS: $args"; env; } > "$out/index.html"
'''


def age(path: Path, days: float) -> Path:
    t = time.time() - days * 86400
    os.utime(path, (t, t))
    return path
