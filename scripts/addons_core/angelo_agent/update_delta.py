# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Update deltas: rebuild the new app bundle from the installed one and a small package.

Contract: doc/angelo/updates_and_reports.rst, "Deltas". A delta (format
``angelo-bundle-delta-1``, written by ``scripts/angelo/make_update_delta.py``)
is a ``.tar.xz`` whose first member, ``delta.json``, describes **every** entry
of the new bundle (directories, files with size, mode and SHA-256, symbolic
links) and where each file's bytes come from:

``same``   the installed bundle's file at the same path
``copy``   the installed bundle's file at another path (a move)
``blob``   the member ``blobs/<sha256>`` (new content, stored once)
``patch``  the installed file at ``path`` with the member's block records
           written over it (a re-signed library differs from its old copy in a
           few blocks; see ``write_patch``)

The result is built in a new folder (never in the installed app), then checked
entry by entry against the description, so a damaged installed app, a wrong
base or a stray file fails the delta and the updater falls back to the full
download. The package itself is trusted only because its SHA-256 is in the
signed feed; the result is then verified like a full download (bundle id,
version and code signature, ``update_stage``).

Paths in the description are relative POSIX paths with no ``..``, checked
before use; symbolic links are created last, so no write ever goes through
one. Standard library only; no bpy. Blocking: call it from a worker thread.
"""

import ctypes
import hashlib
import json
import os
import posixpath
import shutil
import stat
import struct
import sys
import tarfile
from pathlib import Path

FORMAT = "angelo-bundle-delta-1"
MANIFEST = "delta.json"
MAX_MANIFEST = 64 * 1024 * 1024
BLOCK = 64 * 1024
PATCH_MAGIC = b"ANGELOBLK1\n"
_RECORD = struct.Struct(">QI")   # offset, length; followed by ``length`` bytes
_CHUNK = 1024 * 1024
KINDS = ("same", "copy", "blob", "patch")


class DeltaError(Exception):
    """A delta that must not be used; ``code`` is stable (logs, tests)."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def digest(path):
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            sha.update(chunk)
    return sha.hexdigest()


def tree(root, hashes=True):
    """``{relative path: entry}`` of everything under ``root`` (symbolic links are not followed).

    An entry is ``{"type": "dir", "mode"}``, ``{"type": "file", "mode", "size", "sha256"}`` or
    ``{"type": "link", "target"}``; ``mode`` keeps the permission bits only.
    """
    root = Path(root)
    entries = {}
    for folder, names, files in os.walk(root):
        names.sort()
        for name in sorted(names + files):
            path = os.path.join(folder, name)
            relative = Path(path).relative_to(root).as_posix()
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                entries[relative] = {"type": "link", "target": os.readlink(path)}
            elif stat.S_ISDIR(info.st_mode):
                entries[relative] = {"type": "dir", "mode": stat.S_IMODE(info.st_mode)}
            elif stat.S_ISREG(info.st_mode):
                entry = {"type": "file", "mode": stat.S_IMODE(info.st_mode), "size": info.st_size}
                if hashes:
                    entry["sha256"] = digest(path)
                entries[relative] = entry
            else:
                entries[relative] = {"type": "other"}
        names[:] = [n for n in names if not os.path.islink(os.path.join(folder, n))]
    return entries


def safe_path(value):
    """A description path, or raises: relative, POSIX, normalized, no ``..``, nothing odd."""
    if (not isinstance(value, str) or not value or len(value) > 1024 or value.startswith("/") or "\\" in value
            or "\0" in value or posixpath.normpath(value) != value or value == "."
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise DeltaError("invalid", "Unusable path in the delta")
    return value


def _inside(root, path):
    """``path`` resolved, refusing anything that escapes ``root`` (a symbolic link on the way)."""
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    if real != real_root and not real.startswith(real_root + os.sep):
        raise DeltaError("invalid", "A delta path leaves its bundle")
    return real


def _clone(source, target):
    """Copy a file, as an APFS clone when the system offers ``clonefile`` (no space, no time)."""
    if sys.platform == "darwin":
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.clonefile(os.fsencode(source), os.fsencode(target), 0) == 0:
                return
        except (AttributeError, OSError):
            pass
    shutil.copyfile(source, target)


def write_patch(old, new, out):
    """Block records that turn ``old`` into ``new``; returns the literal bytes written.

    The file is ``PATCH_MAGIC``, the new size (8 bytes), then ``(offset, length, bytes)`` records for
    every ``BLOCK``-aligned range of ``new`` that differs from ``old`` at the same offset.
    """
    literal = 0
    size = os.path.getsize(new)
    with open(old, "rb") as a, open(new, "rb") as b:
        out.write(PATCH_MAGIC + struct.pack(">Q", size))
        offset, start, pending = 0, None, []
        while True:
            chunk = b.read(BLOCK)
            if not chunk:
                break
            same = a.read(len(chunk)) == chunk
            if not same:
                if start is None:
                    start = offset
                pending.append(chunk)
            if (same or len(pending) >= 256) and start is not None:
                data = b"".join(pending)
                out.write(_RECORD.pack(start, len(data)) + data)
                literal += len(data)
                start, pending = None, []
            offset += len(chunk)
        if start is not None:
            data = b"".join(pending)
            out.write(_RECORD.pack(start, len(data)) + data)
            literal += len(data)
    return literal


def _apply_patch(stream, target, size):
    if stream.read(len(PATCH_MAGIC)) != PATCH_MAGIC:
        raise DeltaError("invalid", "Not a block patch")
    (new_size,) = struct.unpack(">Q", stream.read(8))
    if new_size != size:
        raise DeltaError("invalid", "A patch's size differs from its description")
    with open(target, "r+b") as handle:
        handle.truncate(new_size)
        while True:
            header = stream.read(_RECORD.size)
            if not header:
                return
            if len(header) != _RECORD.size:
                raise DeltaError("invalid", "A truncated patch record")
            offset, length = _RECORD.unpack(header)
            if offset + length > new_size:
                raise DeltaError("invalid", "A patch record beyond the file")
            handle.seek(offset)
            remaining = length
            while remaining:
                data = stream.read(min(_CHUNK, remaining))
                if not data:
                    raise DeltaError("invalid", "A truncated patch record")
                handle.write(data)
                remaining -= len(data)


def _read_description(archive):
    member = archive.next()
    if member is None or member.name != MANIFEST or not member.isfile() or member.size > MAX_MANIFEST:
        raise DeltaError("invalid", "The delta does not start with its description")
    try:
        description = json.loads(archive.extractfile(member).read())
    except (ValueError, UnicodeError):
        raise DeltaError("invalid", "The delta's description is not JSON") from None
    if not isinstance(description, dict) or description.get("format") != FORMAT:
        raise DeltaError("format", "Unknown delta format")
    entries = description.get("entries")
    if not isinstance(entries, dict):
        raise DeltaError("invalid", "The delta lists no entries")
    return description, entries


def _check_entry(path, entry):
    safe_path(path)
    kind = entry.get("type") if isinstance(entry, dict) else None
    if kind == "dir" and isinstance(entry.get("mode"), int):
        return
    if kind == "link" and isinstance(entry.get("target"), str) and entry["target"] and "\0" not in entry["target"]:
        return
    source = entry.get("from") if kind == "file" else None
    if (kind == "file" and isinstance(entry.get("mode"), int) and isinstance(entry.get("size"), int)
            and entry["size"] >= 0 and isinstance(entry.get("sha256"), str) and isinstance(source, dict)
            and source.get("kind") in KINDS):
        if source["kind"] in {"same", "copy", "patch"}:
            safe_path(source.get("path"))
        if source["kind"] in {"blob", "patch"} and not isinstance(source.get("member"), str):
            raise DeltaError("invalid", "A file without its member")
        return
    raise DeltaError("invalid", "An unusable entry: %s" % path)


def apply(package, installed, out, expect_from, expect_to):
    """Build the new bundle at ``out`` (must not exist) from ``installed`` and the delta ``package``."""
    installed, out = Path(installed), Path(out)
    if out.exists():
        raise DeltaError("io", "The output folder already exists")
    try:
        with tarfile.open(package, mode="r|xz") as archive:
            description, entries = _read_description(archive)
            if description.get("from") != expect_from or description.get("to") != expect_to:
                raise DeltaError("version", "The delta is not for this pair of versions")
            for path, entry in entries.items():
                _check_entry(path, entry)
            members = _lay_out(installed, out, entries)
            while (member := archive.next()) is not None:   # Not ``for``: it would yield the description again.
                waiting = members.pop(member.name, None)
                if waiting is None or not member.isfile():
                    raise DeltaError("invalid", "An unexpected member in the delta")
                _write_member(archive.extractfile(member), installed, out, waiting, entries)
            if members:
                raise DeltaError("invalid", "The delta is missing members")
        _link_and_seal(out, entries)
    except DeltaError:
        shutil.rmtree(out, ignore_errors=True)
        raise
    except (OSError, tarfile.TarError, EOFError, ValueError) as error:
        shutil.rmtree(out, ignore_errors=True)
        raise DeltaError("io", "The delta could not be applied: %s" % type(error).__name__) from None
    problem = compare(out, entries)
    if problem:
        shutil.rmtree(out, ignore_errors=True)
        raise DeltaError("mismatch", problem)
    return out


def _lay_out(installed, out, entries):
    """Directories and the files made from the installed bundle; returns ``{member: [paths]}`` still to write."""
    out.mkdir(parents=True)
    members, kinds = {}, {}
    for path in sorted(entries, key=lambda p: (p.count("/"), p)):
        entry, target = entries[path], out / path
        if entry["type"] == "dir":
            _inside(out, target.parent)
            target.mkdir()
        elif entry["type"] == "file":
            _inside(out, target.parent)
            source = entry["from"]
            if source["kind"] in {"same", "copy", "patch"}:
                base = _inside(installed, installed / source["path"])
                if not os.path.isfile(base) or os.path.islink(installed / source["path"]):
                    raise DeltaError("base", "The installed app lacks a file the delta needs")
                _clone(base, target)
                os.chmod(target, 0o600)   # Writable until sealed: a patch writes into it.
            if source["kind"] in {"blob", "patch"}:
                member = source["member"]
                if kinds.setdefault(member, source["kind"]) != source["kind"] or (
                        source["kind"] == "patch" and member in members):
                    raise DeltaError("invalid", "A member used in two ways, or one patch for two files")
                members.setdefault(member, []).append(path)
    return members


def _write_member(stream, installed, out, paths, entries):
    first = None
    for path in paths:
        entry, target = entries[path], out / path
        if entry["from"]["kind"] == "patch":
            _apply_patch(stream, target, entry["size"])   # A patch member serves exactly one file.
        elif first is None:
            with open(target, "wb") as handle:
                shutil.copyfileobj(stream, handle, _CHUNK)
            first = target
        else:
            _clone(first, target)


def _link_and_seal(out, entries):
    for path, entry in entries.items():
        if entry["type"] == "file":
            os.chmod(out / path, entry["mode"])
    for path, entry in sorted(entries.items()):
        if entry["type"] == "link":
            _inside(out, (out / path).parent)
            os.symlink(entry["target"], out / path)
    for path, entry in sorted(entries.items(), key=lambda item: -item[0].count("/")):
        if entry["type"] == "dir":
            os.chmod(out / path, entry["mode"])


def compare(root, entries):
    """"" when ``root`` holds exactly the described entries, else the first difference (for logs)."""
    actual = tree(root)
    if set(actual) != set(entries):
        extra, missing = sorted(set(actual) - set(entries)), sorted(set(entries) - set(actual))
        return "Entries differ (extra %s, missing %s)" % (extra[:3], missing[:3])
    for path, entry in entries.items():
        found = actual[path]
        if found["type"] != entry["type"]:
            return "Type differs: " + path
        if entry["type"] == "link" and found["target"] != entry["target"]:
            return "Link differs: " + path
        if entry["type"] == "file" and (found["size"], found["sha256"], found["mode"]) != (
                entry["size"], entry["sha256"], entry["mode"]):
            return "File differs: " + path
        if entry["type"] == "dir" and found["mode"] != entry["mode"]:
            return "Folder mode differs: " + path
    return ""
