"""tar_limits: crafted archives that used to make tarfile buffer GiBs are refused quickly, in bounded memory.

Memory is measured with tracemalloc around the call (bytes objects are tracked). Attacks that need real
data use 32 MiB of zeros (a ~32 KiB gzip), enough to tell "bounded" from "buffered" without allocating GiBs;
the 1 GiB / 512 MiB cases only declare the size in the header, which is all the refusal needs to see.
"""
import gzip
import io
import tarfile
import time
import tracemalloc
from pathlib import Path

import pytest

from coordinator import artifacts
from coordinator import tar_limits
from artifact_helpers import put_allure, write
from coordinator.artifacts import ArtifactError, ReportService
from coordinator.tar_limits import BoundedTarInfo, CappedReader, TarLimitError, walk_tar

MIB = 1 << 20
LIMITS = dict(max_members=100, max_bytes=100_000)
BOUNDED = 4 * MIB      # peak allocation allowed while refusing an attack (a bomb needs 32 MiB or more)
QUICK = 5.0            # seconds; a refusal is immediate, 32 MiB of zeros takes well under this to inflate


# --- builders ------------------------------------------------------------------------
def pad(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % 512)


def header(name: str, kind: bytes, size: int) -> bytes:
    info = tarfile.TarInfo(name)
    info.type, info.size = kind, size
    return info.tobuf(tarfile.GNU_FORMAT)


def regular(name: str, data: bytes = b"hello") -> bytes:
    return header(name, tarfile.REGTYPE, len(data)) + pad(data)


def pax_record(key: str, value: str) -> bytes:
    tail = f" {key}={value}\n".encode()
    n = len(tail)
    while n != len(str(n)) + len(tail):
        n = len(str(n)) + len(tail)
    return f"{n}".encode() + tail


def zeros(total: int, step: int = MIB):
    for _ in range(total // step):
        yield b"\0" * step


def gz(path: Path, *parts) -> Path:
    """Write the parts (bytes or iterables of bytes) as one gzip stream, never holding them all."""
    with gzip.open(path, "wb", compresslevel=9) as out:
        for part in parts:
            for piece in ([part] if isinstance(part, bytes) else part):
                out.write(piece)
        out.write(b"\0" * 1024)  # end-of-archive marker
    return path


def measured(fn, *args, **kwargs):
    """(result, exception, peak bytes, seconds) of fn(*args)."""
    tracemalloc.start()
    started = time.monotonic()
    try:
        result, error = fn(*args, **kwargs), None
    except Exception as exc:  # noqa: BLE001 - the tests assert on the type
        result, error = None, exc
    seconds = time.monotonic() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return result, error, peak, seconds


def refused(path: Path, match: str, **limits):
    _result, error, peak, seconds = measured(walk_tar, path, **(limits or LIMITS))
    assert isinstance(error, ValueError), f"not refused: {error!r}"
    assert match in str(error), str(error)
    assert peak < BOUNDED and seconds < QUICK, (peak, seconds)


# --- the hooks we rely on ------------------------------------------------------------
@pytest.mark.parametrize("hook", tar_limits._HOOKS)
def test_the_tarfile_hooks_we_override_exist_in_this_python(hook):
    """If a Python upgrade renames one, the limits would silently stop applying: fail the build instead."""
    assert hasattr(tarfile.TarInfo, hook), f"tarfile.TarInfo has no {hook}: update tar_limits.py"
    assert getattr(BoundedTarInfo, hook) is not getattr(tarfile.TarInfo, hook)


# --- oversized extension headers: refused before tarfile reads the payload ------------
@pytest.mark.parametrize("kind,name,declared", [
    (tarfile.GNUTYPE_LONGNAME, "././@LongLink", 1 << 30),
    (tarfile.GNUTYPE_LONGLINK, "././@LongLink", 1 << 30),
    (tarfile.XHDTYPE, "PaxHeader/a", 512 * MIB),
    (tarfile.XGLTYPE, "pax_global_header", 512 * MIB),
    (tarfile.GNUTYPE_LONGNAME, "././@LongLink", tar_limits.MAX_EXTENSION_BYTES + 1),
], ids=["gnu-longname-1GiB", "gnu-longlink-1GiB", "pax-x-512MiB", "pax-g-512MiB", "longname-just-over"])
def test_declared_oversized_extension_header_is_refused_before_it_is_read(tmp_path, kind, name, declared):
    archive = gz(tmp_path / "a.tar.gz", header(name, kind, declared), pad(b"a.txt\0"), regular("a.txt"))
    refused(archive, "oversized tar extension header")


def test_extension_header_with_a_real_32_mib_payload_does_not_buffer_it(tmp_path):
    """The actual attack: a tiny gzip whose long-name payload inflates to 32 MiB of zeros."""
    for kind, name in ((tarfile.GNUTYPE_LONGNAME, "././@LongLink"), (tarfile.XHDTYPE, "PaxHeader/a")):
        archive = gz(tmp_path / f"{kind.decode()}.tar.gz", header(name, kind, 32 * MIB), zeros(32 * MIB),
                     regular("a.txt"))
        assert archive.stat().st_size < 200_000
        refused(archive, "oversized tar extension header")


def test_negative_extension_size_is_refused(tmp_path):
    info = tarfile.TarInfo("././@LongLink")
    info.type = tarfile.GNUTYPE_LONGNAME
    raw = bytearray(info.tobuf(tarfile.GNU_FORMAT))
    raw[124:136] = b"\xff" + (256 ** 11 - 600).to_bytes(11, "big")  # base-256 negative size
    raw[148:156] = b" " * 8
    raw[148:156] = f"{sum(raw):06o}\0 ".encode()
    refused(gz(tmp_path / "n.tar.gz", bytes(raw), regular("a.txt")), "oversized tar extension header")


def test_extension_headers_up_to_the_limit_still_work(tmp_path):
    exact = pax_record("comment", "a" * (tar_limits.MAX_EXTENSION_BYTES - 15))
    assert len(exact) == tar_limits.MAX_EXTENSION_BYTES
    archive = gz(tmp_path / "ok.tar.gz", header("PaxHeader/a", tarfile.XHDTYPE, len(exact)), pad(exact),
                 regular("a.txt"))
    assert walk_tar(archive, **LIMITS) == (1, 5)
    over = pax_record("comment", "a" * (tar_limits.MAX_EXTENSION_BYTES - 14))
    assert len(over) == tar_limits.MAX_EXTENSION_BYTES + 1
    refused(gz(tmp_path / "over.tar.gz", header("PaxHeader/a", tarfile.XHDTYPE, len(over)), pad(over),
               regular("a.txt")), "oversized tar extension header")


def test_legitimate_long_names_and_global_headers_still_extract(tmp_path):
    long_name = "/".join(["d" * 40] * 8) + "/file.txt"          # > 100 chars: needs a long-name/pax header
    for fmt in (tarfile.GNU_FORMAT, tarfile.PAX_FORMAT):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz", format=fmt, pax_headers={"comment": "git"} if fmt == tarfile.PAX_FORMAT else None) as tar:
            info = tarfile.TarInfo(long_name)
            info.size = 2
            tar.addfile(info, io.BytesIO(b"ok"))
        archive = write(tmp_path / f"{fmt}.tar.gz", buf.getvalue())
        dest = tmp_path / f"out-{fmt}"
        assert tar_limits.safe_extract_tar(archive, dest, max_members=10, max_bytes=100) == (1, 2)
        assert (dest / long_name).read_bytes() == b"ok"


# --- PAX records ------------------------------------------------------------------------
def test_pax_record_claiming_a_huge_length_is_rejected_without_allocating(tmp_path):
    payload = b"99999999999999 path=" + b"a" * 100 + b"\n"
    archive = gz(tmp_path / "r.tar.gz", header("PaxHeader/a", tarfile.XHDTYPE, len(payload)), pad(payload),
                 regular("a.txt"))
    refused(archive, "corrupt")


def test_pax_record_nested_inside_a_value_is_just_a_value(tmp_path):
    inner = pax_record("path", "../../etc/passwd")
    payload = pax_record("comment", inner.decode())
    archive = gz(tmp_path / "n.tar.gz", header("PaxHeader/a", tarfile.XHDTYPE, len(payload)), pad(payload),
                 regular("a.txt"))
    dest = tmp_path / "out"
    assert tar_limits.safe_extract_tar(archive, dest, max_members=10, max_bytes=100) == (1, 5)
    assert [p.name for p in dest.rglob("*")] == ["a.txt"]


def test_pax_size_record_cannot_hide_a_bomb_from_the_unpacked_size_limit(tmp_path):
    payload = pax_record("size", str(1 << 40))
    archive = gz(tmp_path / "s.tar.gz", header("PaxHeader/a", tarfile.XHDTYPE, len(payload)), pad(payload),
                 regular("a.txt"))
    refused(archive, "unpacks to more than")


# --- chained extension headers ------------------------------------------------------------
def chain(count: int) -> bytes:
    payload = pax_record("comment", "x")
    return (header("PaxHeader/a", tarfile.XHDTYPE, len(payload)) + pad(payload)) * count + regular("a.txt")


def test_too_many_chained_extension_headers_are_refused_not_a_500(tmp_path):
    refused(gz(tmp_path / "c.tar.gz", chain(tar_limits.MAX_EXTENSION_CHAIN + 1)), "chains too many")
    assert walk_tar(gz(tmp_path / "ok.tar.gz", chain(tar_limits.MAX_EXTENSION_CHAIN)), **LIMITS) == (1, 5)


def test_recursion_error_inside_tarfile_is_a_refusal_too(tmp_path, monkeypatch):
    monkeypatch.setattr(tar_limits, "MAX_EXTENSION_CHAIN", 10 ** 6)  # take the chain guard out of the picture
    refused(gz(tmp_path / "deep.tar.gz", chain(5000)), "corrupt")


# --- GNU sparse members ---------------------------------------------------------------------
def test_pax_sparse_1_0_map_of_millions_of_entries_is_refused_unread(tmp_path):
    payload = b"".join(pax_record(k, v) for k, v in (
        ("GNU.sparse.major", "1"), ("GNU.sparse.minor", "0"), ("GNU.sparse.name", "a.bin"),
        ("GNU.sparse.realsize", "10")))
    fields = 1_000_000                      # the map is "<fields>\n" then 2*fields lines, here "0\n" each
    data = f"{fields}\n".encode() + b"0\n" * (2 * fields)
    archive = gz(tmp_path / "s.tar.gz", header("PaxHeader/a", tarfile.XHDTYPE, len(payload)), pad(payload),
                 header("a.bin", tarfile.REGTYPE, len(data)), pad(data))
    refused(archive, "sparse member")


def test_old_gnu_sparse_header_with_a_chain_of_extension_blocks_is_refused(tmp_path):
    info = tarfile.TarInfo("a.bin")
    raw = bytearray(info.tobuf(tarfile.GNU_FORMAT))
    raw[156:157], raw[482] = tarfile.GNUTYPE_SPARSE, 1       # type 'S', "an extension block follows"
    raw[148:156] = b" " * 8
    raw[148:156] = f"{sum(raw):06o}\0 ".encode()
    block = b"00000000001\0" * 42 + b"\x01" + b"\0" * 7      # 21 (offset, numbytes) structs, then isextended=1
    assert len(block) == 512
    refused(gz(tmp_path / "s.tar.gz", bytes(raw), block * 4000), "sparse member")


# --- the decompressed-size backstop ---------------------------------------------------------------
def test_stream_limit_follows_the_member_and_byte_limits():
    assert tar_limits.stream_limit(20000, 524288000) == 20000 * 2048 + 524288000 + MIB
    assert tar_limits.stream_limit(0, 0) == MIB and tar_limits.stream_limit(-5, -5) == MIB


def test_capped_reader_cuts_a_gzip_bomb_of_zeros_short(tmp_path):
    archive = gz(tmp_path / "z.gz", zeros(64 * MIB))
    assert archive.stat().st_size < 100_000

    def drain():
        reader, seen = CappedReader(gzip.open(archive, "rb"), 2 * MIB), 0
        while chunk := reader.read(10240):
            seen += len(chunk)
        return seen

    _seen, error, peak, seconds = measured(drain)
    assert isinstance(error, TarLimitError) and isinstance(error, ValueError)
    assert peak < BOUNDED and seconds < QUICK


def test_capped_reader_allows_exactly_its_limit_and_bounds_unsized_reads():
    assert CappedReader(io.BytesIO(b"x" * 100), 100).read() == b"x" * 100
    with pytest.raises(TarLimitError):
        CappedReader(io.BytesIO(b"x" * 101), 100).read()
    reader = CappedReader(io.BytesIO(b"x" * 10 ** 6), 100)
    with pytest.raises(TarLimitError):
        reader.read(None)
    assert reader._left == -1                       # it never pulled more than limit + 1 bytes


def test_many_individually_small_extension_headers_hit_the_stream_cap(tmp_path):
    """Each header is under 64 KiB, the member count and byte totals are fine: only the cap sees the total."""
    payload = pax_record("comment", "a" * 60_000)
    member = header("PaxHeader/a", tarfile.XHDTYPE, len(payload)) + pad(payload) + regular("a.txt")
    members = 40
    archive = gz(tmp_path / "m.tar.gz", member * members)
    refused(archive, "expands to more than its limits allow", max_members=members, max_bytes=1000)
    assert walk_tar(archive, max_members=members, max_bytes=10 * MIB) == (members, members * 5)  # bigger limits, bigger cap


def test_zero_filled_bomb_declaring_a_huge_file_is_refused_by_the_declared_size(tmp_path):
    archive = gz(tmp_path / "b.tar.gz", header("big.bin", tarfile.REGTYPE, 1 << 30), zeros(64 * MIB))
    refused(archive, "unpacks to more than")


# --- wired into everything that reads an archive ---------------------------------------------------
BOMB = (header("././@LongLink", tarfile.GNUTYPE_LONGNAME, 1 << 30), pad(b"a.txt\0"), regular("a.txt"))


def test_put_allure_answers_400_and_stores_nothing(store, settings, tmp_path):
    data = gz(tmp_path / "bomb.tar.gz", *BOMB, zeros(MIB)).read_bytes()
    assert len(data) < 5000
    _r, error, peak, seconds = measured(put_allure, store, "run-1", "c1", data)
    assert isinstance(error, ArtifactError) and error.status_code == 400
    assert "Invalid Allure archive" in error.detail and "oversized" in error.detail
    assert peak < BOUNDED and seconds < QUICK
    assert store.list_allure("run-1") == [] and not any(settings.allure_dir.rglob(".upload-*"))


def test_a_stored_bomb_cannot_spike_memory_when_a_report_is_built(store, settings, tmp_path):
    """Archives stored before this fix are re-read by every report build."""
    stored = write(store.allure_path("run-1", "c1"), gz(tmp_path / "bomb.tar.gz", *BOMB, zeros(MIB)).read_bytes())
    archives = store.list_allure("run-1")
    assert archives and archives[0][1] == stored

    _r, error, peak, _s = measured(artifacts.assemble_results, archives, tmp_path / "results",
                                   max_members=100, max_bytes=100_000)
    assert isinstance(error, ValueError) and "oversized" in str(error) and peak < BOUNDED
    assert not list(tmp_path.glob(".*part-*")) and not (tmp_path / "results-parts" / "c1").exists()

    reports = ReportService(settings, store)
    with pytest.raises(ArtifactError) as exc:
        reports._build("run-1", ["true"])
    assert exc.value.status_code == 502 and "oversized" in exc.value.detail
    assert list((settings.report_dir / ".work").iterdir()) == []

