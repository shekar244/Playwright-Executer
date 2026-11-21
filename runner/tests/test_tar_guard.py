"""safe_extract_tar must not buffer or decompress what the member loop never sees (W2).

The bombs are written by streaming zeros through gzip, so building them never holds more than
1 MiB; extracting them is refused after reading one header block.
"""
import gzip
import io
import tarfile
import time
import tracemalloc

import pytest

import tar_guard
from runner_fakes import make_tar

MIB = 1 << 20
FORMAT = (tarfile.GNU_FORMAT, "utf-8", "surrogateescape")


def _block(info) -> bytes:
    return info.tobuf(*FORMAT)


def _padded(n: int) -> int:
    return -(-n // 512) * 512


def _write_extension_bomb(path, ext_type, ext_size):
    """A .tar.gz whose first member is a `ext_type` header claiming ext_size bytes of zeros."""
    zeros = bytes(MIB)
    ext = tarfile.TarInfo("././@LongLink")
    ext.type, ext.size = ext_type, ext_size
    last = tarfile.TarInfo("a.txt")
    last.size = 2
    with gzip.open(path, "wb", compresslevel=1) as gz:
        gz.write(_block(ext))
        left = _padded(ext_size)
        while left:
            n = min(left, MIB)
            gz.write(zeros[:n])
            left -= n
        gz.write(_block(last) + b"hi" + bytes(510) + bytes(1024))


@pytest.fixture(scope="module")
def bombs(tmp_path_factory):
    d = tmp_path_factory.mktemp("bombs")
    made = {
        "1 GiB GNU long link": (tarfile.GNUTYPE_LONGLINK, 1 << 30),
        "512 MiB PAX header": (tarfile.XHDTYPE, 512 * MIB),
    }
    for name, (ext_type, size) in made.items():
        _write_extension_bomb(d / f"{name}.tar.gz", ext_type, size)
    return d


def _extract_measured(archive, dest, **kwargs):
    tracemalloc.start()
    started = time.perf_counter()
    try:
        tar_guard.safe_extract_tar(archive, dest, **kwargs)
    except ValueError as e:
        error = e
    else:
        error = None
    finally:
        elapsed = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return error, elapsed, peak


@pytest.mark.parametrize("name", ["1 GiB GNU long link", "512 MiB PAX header"])
def test_a_gigabyte_extension_header_is_refused_quickly_with_bounded_memory(bombs, tmp_path, name):
    archive = bombs / f"{name}.tar.gz"
    assert archive.stat().st_size < 8 * MIB  # the bomb is small on the wire
    out = tmp_path / "out"
    out.mkdir()

    error, elapsed, peak = _extract_measured(archive, out / "snap")

    assert error is not None and "extension header" in str(error)
    assert elapsed < 2.0
    assert peak < 8 * MIB  # tarfile would have read the whole claimed header into one bytes object
    assert list(out.iterdir()) == []  # nothing extracted, no temp dir left behind


@pytest.mark.parametrize("ext_type", [tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK, tarfile.XHDTYPE,
                                      tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE])
def test_every_extension_header_type_has_the_same_limit(tmp_path, ext_type):
    archive = tmp_path / "x.tar.gz"
    _write_extension_bomb(archive, ext_type, tar_guard.MAX_EXTENSION_HEADER_BYTES + 1)

    error, _, peak = _extract_measured(archive, tmp_path / "snap")

    assert error is not None and "extension header" in str(error)
    assert peak < 4 * MIB


def test_an_extension_header_at_the_limit_is_not_refused_by_the_size_check(tmp_path):
    archive = tmp_path / "x.tar.gz"
    _write_extension_bomb(archive, tarfile.XGLTYPE, tar_guard.MAX_EXTENSION_HEADER_BYTES)  # zeros: no records

    dest = tar_guard.safe_extract_tar(archive, tmp_path / "snap")

    assert (dest / "a.txt").read_bytes() == b"hi"


def _chain_of_sparse_blocks(path, blocks):
    """A GNU sparse member whose extended-header chain is `blocks` blocks long, then a plain file."""
    head = bytearray(_block(_sparse_member()))
    head[482] = 1  # isextended: extension blocks follow
    _set_checksum(head)
    last = tarfile.TarInfo("a.txt")
    last.size = 2
    with gzip.open(path, "wb") as gz:
        gz.write(bytes(head))
        for i in range(blocks):
            ext = bytearray(512)
            ext[504] = 1 if i < blocks - 1 else 0
            gz.write(bytes(ext))
        gz.write(_block(last) + b"hi" + bytes(510) + bytes(1024))


def _sparse_member():
    info = tarfile.TarInfo("sparse.bin")
    info.type = tarfile.GNUTYPE_SPARSE
    info.size = 0
    return info


def _set_checksum(block: bytearray):
    block[148:156] = b" " * 8
    block[148:156] = b"%06o\0 " % sum(block)


def test_an_endless_chain_of_sparse_extension_blocks_is_refused(tmp_path):
    archive = tmp_path / "sparse.tar.gz"
    _chain_of_sparse_blocks(archive, tar_guard.MAX_EXTENSION_HEADER_BYTES // 512 + 50)

    error, _, _ = _extract_measured(archive, tmp_path / "snap")

    assert error is not None and "extension header" in str(error)
    assert not (tmp_path / "snap").exists()


def test_a_short_chain_of_sparse_extension_blocks_is_not_refused_by_the_guard(tmp_path):
    archive = tmp_path / "sparse.tar.gz"
    _chain_of_sparse_blocks(archive, 3)

    with gzip.open(archive, "rb") as gz, tarfile.open(fileobj=gz, mode="r:", tarinfo=tar_guard.GuardedTarInfo) as tar:
        assert [m.name for m in tar] == ["sparse.bin", "a.txt"]


def _member_with_pax_header(tar, name, comment_bytes):
    info = tarfile.TarInfo(name)
    info.size = 1
    info.pax_headers = {"comment": "x" * comment_bytes}
    tar.addfile(info, io.BytesIO(b"v"))


def _many_pax_members(path, count, comment_bytes):
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        for i in range(count):
            _member_with_pax_header(tar, f"f{i}.txt", comment_bytes)


def test_ordinary_long_names_and_pax_headers_still_extract(tmp_path):
    long_name = "/".join(["d" * 90] * 4) + "/file.txt"  # needs a PAX (or GNU long-name) header
    for fmt in (tarfile.PAX_FORMAT, tarfile.GNU_FORMAT):
        archive = tmp_path / f"{fmt}.tar.gz"
        with tarfile.open(archive, "w:gz", format=fmt) as tar:
            info = tarfile.TarInfo(long_name)
            info.size = 2
            tar.addfile(info, io.BytesIO(b"ok"))
            if fmt == tarfile.PAX_FORMAT:
                _member_with_pax_header(tar, "tagged.txt", 60 * 1024)  # big, but under the limit

        dest = tar_guard.safe_extract_tar(archive, tmp_path / f"snap{fmt}")

        assert (dest / long_name).read_bytes() == b"ok"


def test_a_pax_header_just_over_the_limit_is_refused(tmp_path):
    archive = tmp_path / "pax.tar.gz"
    _many_pax_members(archive, 1, tar_guard.MAX_EXTENSION_HEADER_BYTES + 100)

    with pytest.raises(ValueError, match="extension header"):
        tar_guard.safe_extract_tar(archive, tmp_path / "snap")


def test_the_decompressed_stream_has_a_hard_cap_of_its_own(tmp_path):
    # Every member is tiny and every extension header is under 64 KiB, so each per-header and
    # per-member check passes; only the total amount decompressed gives it away.
    archive = tmp_path / "many.tar.gz"
    _many_pax_members(archive, 3, 60 * 1024)

    with pytest.raises(ValueError, match="too large"):
        tar_guard.safe_extract_tar(archive, tmp_path / "small", max_members=3, max_bytes=100)
    assert not (tmp_path / "small").exists()

    dest = tar_guard.safe_extract_tar(archive, tmp_path / "default")  # the default limits are generous
    assert sorted(p.name for p in dest.iterdir()) == ["f0.txt", "f1.txt", "f2.txt"]


def test_a_forward_seek_past_the_cap_is_refused_before_anything_is_decompressed(tmp_path):
    make_tar(tmp_path / "x.tar.gz", {"a.txt": b"x"})
    with gzip.open(tmp_path / "x.tar.gz", "rb") as gz:
        capped = tar_guard._CappedGzip(gz, cap=1000)
        with pytest.raises(ValueError, match="too large"):
            capped.seek(10**12)
        assert gz.tell() == 0


def test_a_binary_file_object_can_be_extracted_like_a_path(tmp_path):
    data = make_tar(tmp_path / "x.tar.gz", {"a/b.txt": b"data"})

    dest = tar_guard.safe_extract_tar(io.BytesIO(data), tmp_path / "snap")

    assert (dest / "a" / "b.txt").read_bytes() == b"data"
