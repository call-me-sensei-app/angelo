# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Descriptor metadata parity and resource admission in an isolated background app.

Run with run_native_suites.py --include test_blender_metadata_scan. This serial
suite deliberately creates 100,013 Blender mesh IDs and streams a 2 GiB zero
payload into a compressed fixture. It never holds that payload in memory or
writes its uncompressed form. Normal files are written by Blender; the decoded
work fixture inserts unreferenced DATA blocks before their original DNA.

The unsafe legacy library listing is used only as an oracle on private scratch
files, never on the 2 GiB expansion. Production deadlines run first, followed
by a 300-second diagnostic token. A production refusal remains a failed check
even if the diagnostic run succeeds; both receipts are retained.
No network, model, provider, account, existing document or native build.
"""

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import threading
import time
import traceback

import bpy
from _bpy import _angelo_read as api

assert bpy.app.background and "--factory-startup" in sys.argv, "Use an isolated factory-startup process"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import blend_file_listing as listing  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

OUT = Path(os.environ["ANGELO_QA_DIR"]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
CHUNK = 1024 ** 2
GIB = 1024 ** 3
LARGE_IDS = 100_013
PREFIX = "Metadata shared prefix " + "p" * 72 + " "
report = {"checks": {}, "samples": [], "fixtures": {}, "paid_requests": 0, "success": False,
          "binary": bpy.app.binary_path, "production_limits": dict(listing.SCAN_LIMITS),
          "production_deadline_seconds": listing.DEADLINE_SECONDS,
          "timing_method": "Production token first; diagnostic follows on the same file and may use a warm filesystem cache"}


def check(name, condition, *, defer=False):
    report["checks"][name] = bool(condition)
    if not condition and not defer:
        raise AssertionError(name)


def request(kind="", offset=0, limit=32):
    return {"list": kind, "offset": offset, "limit": limit,
            "summary_names": listing.SUMMARY_NAMES, "libraries_limit": listing.MAX_LIBRARIES}


def native(path, *, asked=None, limits=None, seconds=300, token=None, label="scan"):
    """No bpy access: this helper is also safe to execute on a worker."""
    asked = asked if asked is not None else request()
    token = token if token is not None else api.metadata_cancel_token(seconds)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        os.lseek(descriptor, 3, os.SEEK_SET)
        started = time.perf_counter()
        result = api.metadata_scan(descriptor, asked, dict(limits or listing.SCAN_LIMITS), token)
        elapsed = time.perf_counter() - started
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 3, "Borrowed descriptor cursor changed"
        os.fstat(descriptor)  # Native code must not close the caller's descriptor.
    finally:
        os.close(descriptor)
    retained = sum(len(row["names"]) for row in result["types"].values()) + len(result["libraries"])
    assert retained <= listing.SCAN_LIMITS["result_names"], "Unbounded metadata result"
    if result["status"] != "OK":
        assert not result["types"] and not result["libraries"] and "version" not in result
    sample = {key: result[key] for key in ("status", "decoded_bytes", "input_bytes", "blocks", "passes")}
    sample.update(label=label, seconds=elapsed, file_bytes=path.stat().st_size,
                  retained_names=retained, request=asked, deadline_seconds=seconds)
    report["samples"].append(sample)
    return result


def oracle(path):
    """Detached names only, with no link/append selection, on the private fixture."""
    with bpy.data.libraries.load(str(path), link=False) as (source, _target):
        names = {kind: list(getattr(source, kind)) for kind in dir(source)
                 if not kind.startswith("_") and kind not in {"libraries", "version"}
                 and isinstance(getattr(source, kind), list)}
        libraries = [entry.filepath for entry in source.libraries]
        version = tuple(source.version)
    return names, libraries, version


def matches(result, expected, asked):
    names, libraries, version = expected
    if result["status"] != "OK" or result["version"] != version or set(result["types"]) != set(names):
        return False
    for kind, entries in names.items():
        row = result["types"][kind]
        selected = (entries[asked["offset"]:asked["offset"] + asked["limit"]]
                    if kind == asked["list"] else [] if asked["list"] else entries[:asked["summary_names"]])
        if row != {"total": len(entries), "names": selected}:
            return False
    return result["libraries"] == libraries[:asked["libraries_limit"]]


def state_digest():
    """Bounded digest of relevant live IDs; do not retain the 100k-row snapshot."""
    digest = hashlib.sha256()
    for prop in bpy.data.bl_rna.properties:
        if prop.type != "COLLECTION" or prop.identifier == "rna_type":
            continue
        for item in getattr(bpy.data, prop.identifier):
            if isinstance(item, bpy.types.ID):
                row = (prop.identifier, item.as_pointer(), item.name, item.users, item.use_fake_user)
                digest.update(repr(row).encode("utf8"))
    digest.update(repr((bpy.data.filepath, bpy.data.is_dirty,
                        [(image.as_pointer(), image.has_data) for image in bpy.data.images])).encode("utf8"))
    return digest.hexdigest()


def gzip_copy(source, destination):
    with source.open("rb") as raw, destination.open("wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=1, mtime=0) as compressed:
            shutil.copyfileobj(raw, compressed, CHUNK)


def without_seek_table(source, destination):
    """Keep Blender's genuine Zstd frames, removing only its documented skippable seek table."""
    with source.open("rb") as stream:
        stream.seek(-9, os.SEEK_END)
        frames, flags, magic = struct.unpack("<IBI", stream.read(9))
        assert magic == 0x8F92EAB1 and not flags & 0x60
        table_size = frames * (12 if flags & 0x80 else 8) + 9
        table_start = source.stat().st_size - table_size - 8
        stream.seek(table_start)
        assert struct.unpack("<II", stream.read(8)) == (0x184D2A5E, table_size)
        stream.seek(0)
        with destination.open("wb") as output:
            remaining = table_start
            while remaining:
                chunk = stream.read(min(CHUNK, remaining))
                assert chunk
                output.write(chunk)
                remaining -= len(chunk)


def stress_gzip(source, destination):
    """Fixture-only framing; every original Blender block is copied unchanged."""
    with source.open("rb") as raw:
        header = raw.read(17)
        assert header[:7] == b"BLENDER"
        if header[7:9] == b"17":
            header_bytes, block = 17, struct.Struct("<4siQqq")
            length_at = 3
            padding = lambda size: block.pack(b"DATA", 0, 0, size, size)
        else:
            assert header[8:9] == b"v" and header[7:8] in (b"-", b"_")
            header_bytes = 12
            block = struct.Struct("<4siQii" if header[7:8] == b"-" else "<4siIii")
            length_at = 1
            padding = lambda size: block.pack(b"DATA", size, 0, 0, size)
        raw.seek(header_bytes)
        while True:
            insertion = raw.tell()
            fields = block.unpack(raw.read(block.size))
            if fields[0] == b"DNA1":
                break
            assert fields[0] != b"ENDB" and fields[length_at] >= 0
            raw.seek(fields[length_at], os.SEEK_CUR)
        raw.seek(0)
        with destination.open("wb") as output:
            with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=1, mtime=0) as compressed:
                remaining = insertion
                while remaining:
                    chunk = raw.read(min(CHUNK, remaining))
                    assert chunk
                    compressed.write(chunk)
                    remaining -= len(chunk)
                zeros = bytes(CHUNK)
                for _ in range(16):
                    compressed.write(padding(128 * CHUNK))
                    for _ in range(128):
                        compressed.write(zeros)
                shutil.copyfileobj(raw, compressed, CHUNK)
    report["fixtures"]["decoded_work"] = {"payload_bytes": 2 * GIB, "largest_payload_buffer": CHUNK,
                                            "compressed_bytes": destination.stat().st_size,
                                            "kind": "genuine Blender container with unreferenced DATA padding"}


def small_files(folder):
    dependency = bpy.data.materials.new("Metadata linked material")
    bpy.data.libraries.write(str(folder / "dependency.blend"), {dependency}, fake_user=True)
    bpy.data.materials.remove(dependency)
    with bpy.data.libraries.load(str(folder / "dependency.blend"), link=True) as (source, target):
        target.materials = source.materials
    linked = target.materials[0]
    meshes = [bpy.data.meshes.new("Metadata mesh " + suffix) for suffix in ("Z", "A", "Ω", 'quote"', "10", "2")]
    meshes[0].materials.append(linked)
    obj = bpy.data.objects.new("Metadata object", meshes[0])
    blocks = set(meshes) | {obj, bpy.data.worlds.new("Metadata world")}
    plain, compressed = folder / "plain.blend", folder / "zstd.blend"
    bpy.data.libraries.write(str(plain), blocks, fake_user=True, compress=False, path_remap="RELATIVE_ALL")
    bpy.data.libraries.write(str(compressed), blocks, fake_user=True, compress=True, path_remap="RELATIVE_ALL")
    with compressed.open("rb") as stream:
        assert stream.read(4) == b"\x28\xb5\x2f\xfd", "Expected Blender's native Zstandard writer"
    gzip_copy(plain, folder / "gzip.blend")
    without_seek_table(compressed, folder / "zstd-stream.blend")
    return plain, (plain, folder / "gzip.blend", compressed, folder / "zstd-stream.blend")


def small_parity(folder, files):
    workspace = FileWorkspace(folder)
    for path in files:
        expected = oracle(path)
        before = state_digest()
        for asked in (request(), request("meshes", 0, 2), request("meshes", 2, 3), request("meshes", 100, 2)):
            result = native(path, asked=asked, label=path.stem + "-parity")
            check(path.stem + "-native-" + str(asked), matches(result, expected, asked))
            arguments = {"file": path.name, "offset": asked["offset"], "limit": asked["limit"]}
            if asked["list"]:
                arguments["list"] = asked["list"]
            answer = listing.read(None, validate_call("angelo_blend_file", arguments), workspace)
            check(path.stem + "-adapter-readable-" + str(asked), answer["readable"])
            if asked["list"]:
                entries = expected[0][asked["list"]]
                check(path.stem + "-adapter-page-" + str(asked), answer["total"] == len(entries)
                      and answer["items"] == [name[:listing.MAX_NAME] for name in entries[asked["offset"]:asked["offset"] + asked["limit"]]])
            else:
                check(path.stem + "-adapter-summary",
                      {row["type"]: (row["count"], row["names"]) for row in answer["datablocks"]}
                      == {kind: (len(entries), [name[:listing.MAX_NAME] for name in entries[:listing.SUMMARY_NAMES]])
                          for kind, entries in expected[0].items() if entries})
        check(path.stem + "-live-state-unchanged", state_digest() == before)


def large_names(folder):
    """A genuine whole-file save of exactly 100,013 similarly named meshes, created in name order.

    Not a partial write: ``bpy.data.libraries.write`` re-inserts each ID of its
    unordered set into a sorted list (``id_sort_by_name`` walks back from the
    end), quadratic in the ID count. It took 251 s on a quiet machine and kept
    the suite past its timeout under load, before any scan ran.
    """
    started = time.perf_counter()
    bpy.ops.wm.read_factory_settings(use_empty=True)  # No other meshes; the earlier fixtures are on disk.
    meshes = [bpy.data.meshes.new(PREFIX + f"{index:06d}") for index in range(LARGE_IDS)]
    for mesh in meshes:
        mesh.use_fake_user = True
    path = folder / "many-names.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, compress=False, check_existing=False)
    expected = oracle(path)
    check("genuine-large-id-count", len(expected[0]["meshes"]) == LARGE_IDS)
    report["fixtures"]["many_names"] = {"ids": LARGE_IDS, "bytes": path.stat().st_size,
                                         "creation_and_oracle_seconds": time.perf_counter() - started}
    before = state_digest()
    for asked in (request(), request("meshes", 0, 64), request("meshes", 100_000, 7),
                  request("meshes", LARGE_IDS - 2, 64), request("meshes", LARGE_IDS, 64)):
        production = native(path, asked=asked, seconds=listing.DEADLINE_SECONDS,
                            label="many-names-production-" + str(asked["offset"]))
        check("many-names-production-" + str(asked), matches(production, expected, asked), defer=True)
        diagnostic = native(path, asked=asked, label="many-names-diagnostic-" + str(asked["offset"]))
        check("many-names-diagnostic-" + str(asked), matches(diagnostic, expected, asked))
        check("many-names-retained-" + str(asked), len(diagnostic["types"]["meshes"]["names"])
              <= (asked["limit"] if asked["list"] else asked["summary_names"]))
    check("many-names-live-state-unchanged", state_digest() == before)
    bpy.data.batch_remove(meshes)


def decoded_work(folder, plain):
    expected = oracle(plain)
    path = folder / "decoded-2gib.blend"
    stress_gzip(plain, path)
    check("decoded-work-within-physical-file-budget", path.stat().st_size < listing.MAX_BLEND_BYTES)
    before = state_digest()
    production = native(path, seconds=listing.DEADLINE_SECONDS, label="2gib-production")
    check("2gib-production-preserves-valid-metadata", matches(production, expected, request()), defer=True)
    diagnostic = native(path, label="2gib-diagnostic")
    check("2gib-diagnostic-parity", matches(diagnostic, expected, request()))
    check("2gib-real-decoded-work", diagnostic["decoded_bytes"] >= 4 * GIB)
    limited = native(path, limits={**listing.SCAN_LIMITS, "decoded_bytes": 2 * GIB}, label="2gib-work-limit")
    check("2gib-distinct-work-limit", limited["status"] == "LIMIT_EXCEEDED"
          and limited["decoded_bytes"] <= 2 * GIB + 1)
    token = api.metadata_cancel_token(300)
    api.metadata_cancel(token)
    check("pre-cancelled-token", native(path, token=token, label="pre-cancelled")["status"] == "CANCELLED")
    token, started = api.metadata_cancel_token(300), threading.Event()
    finished, errors = [], []

    def worker():
        try:
            started.set()
            finished.append(native(path, token=token, label="2gib-in-flight-cancel"))
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert started.wait(5)
    time.sleep(0.05)  # Let the native worker start consuming the deliberately large stream.
    running = thread.is_alive()
    cancelled_at = time.perf_counter()
    api.metadata_cancel(token)
    thread.join(10)
    check("in-flight-cancel-completes", running and not thread.is_alive() and not errors and len(finished) == 1)
    check("in-flight-cancel-status", finished[0]["status"] == "CANCELLED" and finished[0]["input_bytes"] > 0)
    report["cancel_latency_seconds"] = time.perf_counter() - cancelled_at
    timed = native(plain, seconds=1e-9, label="expired-deadline")
    check("deadline-distinct-from-cancel-and-work-limit", timed["status"] == "TIME_LIMIT")
    check("decoded-work-live-state-unchanged", state_digest() == before)


try:
    with tempfile.TemporaryDirectory(prefix="metadata-", dir=OUT) as temporary:
        folder = Path(temporary)
        plain, files = small_files(folder)
        small_parity(folder, files)
        large_names(folder)
        decoded_work(folder, plain)
    report["success"] = bool(report["checks"]) and all(report["checks"].values())
    if not report["success"]:
        raise AssertionError("Production admission rejected a valid fixture; inspect diagnostic samples")
except BaseException:
    report["error"] = traceback.format_exc()
    raise
finally:
    (OUT / "metadata-scan.json").write_text(json.dumps(report, indent=2), encoding="utf8")
