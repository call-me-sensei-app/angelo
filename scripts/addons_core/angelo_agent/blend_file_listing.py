# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_blend_file`` for another .blend in the file workspace: its datablock names, without opening it.

The file is named relative to the selected file workspace and admitted by the
file tools' own rule (``files.FileWorkspace.path``: relative, not hidden, no
link on the way, no credential folders). It is then opened here, anchored and
without following a link, to check that it is one regular file with a .blend
header and a bounded size, and it stays open while Blender reads it.

The native scanner reads only that admitted descriptor; it never reopens the
path or enters Blender's library-link lifecycle. Plain, gzip and Zstandard
streams use native headers/SDNA with bounded codec buffers. Geometry payloads
are skipped or discarded rather than retained. The controller runs this owner
on a cancellable worker; no live bpy data is used here. Identity, size, mtime,
ctime and link checks reject concurrent writes. User counts, missing files
and unresolved references need the open file's data.

Every failure is an answer (``readable`` false with a stable ``code``): a path
outside the workspace, a missing file, a file that is not a .blend, a .blend
Blender cannot read. No message carries an absolute path.
"""

import os
import re
import stat

from . import blend_file_paths as paths
from .data_read_access import NATIVE_MESSAGE, NATIVE_REQUIRED, native_api
from .checkpoint_filesystem import (_open_file, _private_directory, assert_directory_identity, entry_info,
                                    require_anchored_files, same_file)

MAX_BLEND_BYTES = 2 * 1024 ** 3
SUMMARY_NAMES = 12
MAX_NAME = 128
MAX_LIBRARIES = 32
# The workspace scan's time budget (TIME_LIMIT), separate from Stop (CANCELLED) and the work budgets below
# (LIMIT_EXCEEDED). It runs on a cancellable worker, never on the main thread. Five seconds, not two: a valid
# 2 GiB-decoded fixture took 3.46 s on the Windows validation VM and over 2 s on a loaded Mac (0.99 s quiet).
DEADLINE_SECONDS = 5.0
# Explicit initial admission ceilings, validated by representative native fixtures before release.
# block_count bounds the work of walking block headers (summed over the two or three passes). Blender
# writes one block per weighted vertex, so a 250 MB rigged scene has 1.1 M blocks per pass; a 2 GiB one
# about 9.7 M, 29 M over a deep page's three passes. A buffered plain read costs about 17 ns per block
# on an M4 Pro (it was a system call per block, about 400 ns, when this limit was 2,000,000), so
# 32,000,000 blocks are about the same half second of header work as the old limit was.
SCAN_LIMITS = {"decoded_bytes": 6 * 1024 ** 3, "input_bytes": 6 * 1024 ** 3,
               "block_count": 32_000_000, "block_bytes": 2 * 1024 ** 3,
               "dna_bytes": 8 * 1024 ** 2, "dna_entries": 100_000,
               "decoder_bytes": 64 * 1024 ** 2, "zstd_window_bytes": 32 * 1024 ** 2,
               "result_names": 4096}
# A .blend and Blender's numbered backups of it (.blend1 ...).
SUFFIX = re.compile(r"\.blend\d{0,2}$", re.IGNORECASE)
# Uncompressed, Zstandard and gzip (before 3.0) files start with one of these.
MAGICS = (b"BLENDER", b"\x28\xb5\x2f\xfd", b"\x1f\x8b")
SCAN_FAILURES = {
    "INVALID": ("UNREADABLE", "This file is damaged, cut short or not a readable .blend."),
    "LIMIT_EXCEEDED": ("LIMIT_EXCEEDED", "Listing this file exceeds a bounded metadata-read budget; it was not loaded."),
    "TIME_LIMIT": ("TIME_LIMIT", "The metadata-read time budget was reached; the file was not loaded."),
    "CANCELLED": ("CANCELLED", "The metadata read was stopped; the file was not loaded."),
    "IO_ERROR": ("UNREADABLE", "The file could not be read; it was not loaded."),
}


def _refused(code, message, **fields):
    return dict(readable=False, scope="WORKSPACE_FILE", code=code, message=message, **fields)


def scanner_api():
    api = native_api()
    return api if api is not None and all(callable(getattr(api, name, None)) for name in (
        "metadata_scan", "metadata_cancel_token", "metadata_cancel")) else None


def read(_bpy, args, workspace, *, api=None, token=None, diagnostics=None):
    """One file's metadata. No bpy is used; the first argument keeps internal callers compatible."""
    relative = args["file"]
    if workspace is None:
        return _refused("NO_WORKSPACE", "No file workspace is selected for this chat.")
    try:
        require_anchored_files()
        path = workspace.path(relative)
    except ValueError as error:
        return _refused("OUTSIDE_WORKSPACE", str(error))  # The refused path is not echoed: it may be absolute.
    if not SUFFIX.search(path.name):
        return _refused("NOT_BLEND", "Name a .blend file (or a .blend1 backup) inside the file workspace.", file=relative)
    try:
        with _private_directory(path.parent, within=workspace.root) as folder:
            info = entry_info(folder, path.name)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return _refused("NOT_A_FILE", "This is not one regular file; links and shared files are not read.",
                                file=relative)
            # A regular entry can become a FIFO after stat. Nonblocking admission prevents an open
            # waiting for a writer before native cancellation/deadline checks can run; regular-file
            # reads keep their ordinary semantics. The opened descriptor remains authoritative.
            descriptor = _open_file(folder, path.name, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            try:
                opened = os.fstat(descriptor)
                if not same_file(info, opened) or opened.st_nlink != 1:
                    return _refused("NOT_A_FILE", "This is not one regular file; links and shared files are not read.",
                                    file=relative)
                if opened.st_size > MAX_BLEND_BYTES:
                    return _refused("TOO_LARGE", "This file is larger than the %d GiB that are listed without "
                                    "opening it." % (MAX_BLEND_BYTES // 1024 ** 3), file=relative, bytes=opened.st_size)
                if not os.read(descriptor, 8).startswith(MAGICS):
                    return _refused("NOT_BLEND", "This file does not start as a .blend does.", file=relative,
                                    bytes=opened.st_size)
                api = api if api is not None else scanner_api()
                if api is None:
                    return _refused(NATIVE_REQUIRED, NATIVE_MESSAGE, file=relative)
                token = token if token is not None else api.metadata_cancel_token(DEADLINE_SECONDS)
                os.lseek(descriptor, 0, os.SEEK_SET)
                request = {"list": args.get("list", ""), "offset": args["offset"], "limit": args["limit"],
                           "summary_names": SUMMARY_NAMES, "libraries_limit": MAX_LIBRARIES}
                try:
                    scanned = api.metadata_scan(descriptor, request, dict(SCAN_LIMITS), token)
                except (OSError, RuntimeError, ValueError, MemoryError):
                    return _refused("UNREADABLE", "The native metadata reader could not finish; the file was not loaded.",
                                    file=relative, bytes=opened.st_size)
                assert_directory_identity(path.parent, folder)
                after, held = entry_info(folder, path.name), os.fstat(descriptor)
                fields = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
                if (not same_file(held, after) or any(getattr(held, field) != getattr(opened, field) or
                        getattr(after, field) != getattr(opened, field) for field in fields)):
                    return _refused("CHANGED", "The file changed while it was read; nothing is reported. Read it again.",
                                    file=relative)
            finally:
                os.close(descriptor)
    except FileNotFoundError:
        return _refused("NOT_FOUND", "No such file in the file workspace.", file=relative)
    except (OSError, ValueError):
        return _refused("NOT_A_FILE", "This path is not a readable regular file inside the file workspace.", file=relative)
    if diagnostics is not None:
        diagnostics.update({key: scanned.get(key) for key in ("status", "decoded_bytes", "input_bytes", "blocks", "passes")})
    if scanned.get("status") != "OK":
        code, message = SCAN_FAILURES.get(scanned.get("status"), ("UNREADABLE", "The metadata reader returned no result."))
        return _refused(code, message, file=relative, bytes=opened.st_size)
    names, libraries, version = scanned["types"], scanned["libraries"], scanned.get("version")
    result = {"readable": True, "scope": "WORKSPACE_FILE", "file": relative, "bytes": opened.st_size}
    if version:
        result["blender_version"] = ".".join(str(part) for part in version[:3])
    listed, offset, limit = args.get("list"), args["offset"], args["limit"]
    if listed is not None:
        if listed not in names:
            return dict(result, readable=False, code="UNKNOWN_LIST",
                        message="list names a datablock type of this file; its other parts need the file open.",
                        types=sorted(kind for kind, entries in names.items() if entries["total"]))
        entries = names[listed]
        selected = [name[:MAX_NAME] for name in entries["names"]]
        more = offset + len(selected) < entries["total"]
        return dict(result, type=listed, total=entries["total"], offset=offset, items=selected,
                    next_offset=offset + len(selected) if more else None, truncated=more)
    rows, truncated = [], False
    for kind in sorted(names):
        entries = names[kind]
        if not entries["total"]:
            continue
        row = {"type": kind, "count": entries["total"], "names": [name[:MAX_NAME] for name in entries["names"]]}
        if entries["total"] > len(entries["names"]):
            row["more"] = entries["total"] - len(entries["names"])
            truncated = True
        rows.append(row)
    folder_text = str(path.parent)
    result.update(datablocks=rows, truncated=truncated,
                  libraries=[paths.public_name(raw, folder_text, str(workspace.root)) for raw in libraries[:MAX_LIBRARIES]])
    if truncated:
        result["note"] = "Types with more names than shown: page one with list (for example list=\"objects\")."
    result["scope_note"] = ("Names and counts only: user counts, missing files and unresolved references need the "
                            "file open.")
    return result
