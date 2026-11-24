# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The signed file manifest of a Linux release: what the release contains, and the check that a tree is exactly it.

Contract: doc/angelo/updates_and_reports.rst, "Linux release signing". Linux has no operating-system code signing,
so the release signs its own files. Its top folder (``Angelo-<version>-linux-x64``) holds two files beside the app:

* ``angelo-release-manifest.json``: every entry of the tree, sorted by path. A regular file has its size, SHA-256
  and whether it is executable; a symbolic link has its target, which must stay inside the tree; a folder is listed
  so empty ones are covered too. The two manifest files themselves are not listed.
* ``angelo-release-manifest.sig``: ``{"format", "version", "key_id", "signature"}``, the Ed25519 signature of
  ``SIGNING_CONTEXT`` followed by the SHA-512 of the manifest's exact bytes. Cloud KMS signs at most 64 KiB, and a
  manifest of a whole Blender tree is larger, so the key signs the manifest's digest.

The key is the Linux release key (Cloud KMS ``linux-release``), never the update feed's: its public keys ship in
``linux_release_keys.json``, in ``update_keys.json``'s format, so either key can be rotated or revoked alone.

``check_tree`` is the updater's acceptance check for an unpacked release (the Linux counterpart of the macOS code
requirement and Windows's same-publisher check), and the release tools' check before anything is published. It is
fail closed: no trusted key, an unknown key, a bad signature, another version, platform or top folder than the one
expected, any entry missing, added or different (a partial extraction included), a link that leaves the tree, a
special file, a set-id bit or a file anyone else may write is an error, never a partial acceptance.

Standard library only; no bpy. Runs on the update worker thread.
"""

import base64
import binascii
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from . import ed25519, update_feed

KEYS_FILE = Path(__file__).resolve().parent / "linux_release_keys.json"
FORMAT = "angelo-release-manifest"
SIGNATURE_FORMAT = "angelo-release-manifest-signature"
SIGNING_CONTEXT = b"angelo-release-manifest-v1\n"
MANIFEST_NAME = "angelo-release-manifest.json"
SIGNATURE_NAME = "angelo-release-manifest.sig"
OWN_FILES = (MANIFEST_NAME, SIGNATURE_NAME)
MAX_MANIFEST = 16 * 1024 * 1024
MAX_SIGNATURE = 4 * 1024
MAX_ENTRIES = 200000
_HEX64 = re.compile(r"[0-9a-f]{64}")
_UNSAFE = re.compile(r"[\x00-\x1f\x7f\\]")
_CHUNK = 1024 * 1024


class ManifestError(Exception):
    """A tree or manifest that must not be used. ``code`` is stable (logs, tests); the message is for logs only."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def load_keys(path=None):
    """``{key_id: public key}`` of the usable Linux release keys (revoked ones left out); empty when none. ``path``
    defaults to ``KEYS_FILE`` as it is when called."""
    return update_feed.load_keys(KEYS_FILE if path is None else path)


def root_name(version, platform="linux-x64"):
    """The release's top folder: ``Angelo-<version>-<platform>``."""
    return "Angelo-%s-%s" % (version, platform)


def check_path(path):
    """``path`` when it is a relative POSIX path inside the tree with no empty, ``.`` or ``..`` part."""
    if not isinstance(path, str) or not path or len(path) > 4096 or path.startswith("/") or _UNSAFE.search(path):
        raise ManifestError("bad_path", "Not a path inside the release: %r" % (path,))
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise ManifestError("bad_path", "Not a path inside the release: %r" % (path,))
    return path


def link_inside(path, target):
    """True when the link at ``path`` (relative) points at ``target`` without leaving the tree."""
    if not isinstance(target, str) or not target or target.startswith("/") or _UNSAFE.search(target):
        return False
    depth = path.count("/")
    for part in target.split("/"):
        if part in ("", "."):
            continue
        depth = depth - 1 if part == ".." else depth + 1
        if depth < 0:
            return False
    return True


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _refuse_mode(path, mode):
    if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
        raise ManifestError("bad_mode", "%s carries a set-id or sticky bit" % path)
    if not stat.S_ISLNK(mode) and mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ManifestError("bad_mode", "%s may be written by other users" % path)


def describe_tree(root, check_modes=True):
    """``{path: entry}`` of everything under ``root`` (the two manifest files at its top left out), as the manifest
    lists it. Never follows a link. Raises ``ManifestError`` for a special file, a name that is not UTF-8, a link that
    leaves the tree and (``check_modes``) a set-id bit or a file others may write."""
    root = Path(root)
    if check_modes:   # The top folder too: whether an install can update itself is decided by its mode.
        _refuse_mode(root.name, os.lstat(root).st_mode)
    entries = {}
    pending = [("", root)]
    while pending:
        prefix, folder = pending.pop()
        with os.scandir(folder) as listing:
            for item in listing:
                name = item.name
                try:
                    name.encode("utf-8")   # A name that is not UTF-8 arrives with surrogate escapes.
                except UnicodeError:
                    raise ManifestError("bad_path", "A name in %s is not UTF-8" % folder) from None
                path = check_path(prefix + name)
                if not prefix and name in OWN_FILES:
                    continue
                info = item.stat(follow_symlinks=False)
                if check_modes:
                    _refuse_mode(path, info.st_mode)
                if stat.S_ISLNK(info.st_mode):
                    target = os.readlink(item.path)
                    if not link_inside(path, target):
                        raise ManifestError("bad_link", "%s points outside the release" % path)
                    entries[path] = {"type": "link", "target": target}
                elif stat.S_ISDIR(info.st_mode):
                    entries[path] = {"type": "dir"}
                    pending.append((path + "/", Path(item.path)))
                elif stat.S_ISREG(info.st_mode):
                    entries[path] = {"type": "file", "size": info.st_size, "sha256": sha256_file(item.path),
                                     "exec": bool(info.st_mode & stat.S_IXUSR)}
                else:
                    raise ManifestError("bad_type", "%s is not a file, folder or link" % path)
                if len(entries) > MAX_ENTRIES:
                    raise ManifestError("too_large", "More than %d entries" % MAX_ENTRIES)
    return entries


def manifest_bytes(entries, version, platform="linux-x64"):
    """The manifest's exact bytes for ``describe_tree``'s entries."""
    document = {"format": FORMAT, "schema": 1, "version": version, "platform": platform,
                "root": root_name(version, platform),
                "entries": [dict(path=path, **entries[path]) for path in sorted(entries)]}
    return (json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def signed_message(manifest):
    """What the release key signs: the context, then the SHA-512 of the manifest's bytes."""
    return SIGNING_CONTEXT + hashlib.sha512(manifest).digest()


def signature_bytes(key_id, signature):
    document = {"format": SIGNATURE_FORMAT, "version": 1, "key_id": key_id,
                "signature": base64.b64encode(signature).decode("ascii")}
    return (json.dumps(document, indent=1) + "\n").encode("utf-8")


def verify_signature(manifest, signature_file, keys):
    """The key id when ``signature_file`` is a valid signature of ``manifest`` by a trusted key; raises."""
    if not keys:
        raise ManifestError("no_keys", "No Linux release key is configured in this build")
    if not isinstance(signature_file, bytes) or len(signature_file) > MAX_SIGNATURE:
        raise ManifestError("malformed", "The signature file is not a signature")
    try:
        document = json.loads(signature_file)
    except (ValueError, UnicodeError):
        raise ManifestError("malformed", "The signature file is not JSON") from None
    if (not isinstance(document, dict) or document.get("format") != SIGNATURE_FORMAT
            or type(document.get("version")) is not int or document.get("version") != 1):
        raise ManifestError("malformed", "Not an Angelo release manifest signature (version 1)")
    key_id = document.get("key_id")
    key = keys.get(key_id) if isinstance(key_id, str) else None
    if key is None:
        raise ManifestError("unknown_key", "The manifest names a key this build does not trust")
    try:
        signature = base64.b64decode(document.get("signature"), validate=True)
    except (TypeError, ValueError, binascii.Error):
        raise ManifestError("malformed", "The signature is not base64") from None
    if not ed25519.verify(key, signed_message(manifest), signature):
        raise ManifestError("bad_signature", "The manifest signature does not match")
    return key_id


def _entry(value):
    """``(path, entry)`` of one manifest entry, checked; raises ``ManifestError``."""
    if not isinstance(value, dict):
        raise ManifestError("malformed", "A manifest entry is not an object")
    path, kind = check_path(value.get("path")), value.get("type")
    if kind == "dir" and set(value) == {"path", "type"}:
        return path, {"type": "dir"}
    if kind == "link" and set(value) == {"path", "type", "target"} and link_inside(path, value["target"]):
        return path, {"type": "link", "target": value["target"]}
    if (kind == "file" and set(value) == {"path", "type", "size", "sha256", "exec"}
            and type(value["size"]) is int and value["size"] >= 0 and isinstance(value["sha256"], str)
            and _HEX64.fullmatch(value["sha256"]) and type(value["exec"]) is bool):
        return path, {"type": "file", "size": value["size"], "sha256": value["sha256"], "exec": value["exec"]}
    raise ManifestError("malformed", "Manifest entry %r is not a file, folder or link inside the release" % path)


def parse_manifest(manifest, version, platform="linux-x64"):
    """``{path: entry}`` of a manifest for exactly ``version`` and ``platform``; raises ``ManifestError``."""
    if not isinstance(manifest, bytes) or len(manifest) > MAX_MANIFEST:
        raise ManifestError("too_large", "The manifest is larger than %d bytes" % MAX_MANIFEST)
    try:
        document = json.loads(manifest)
    except (ValueError, UnicodeError):
        raise ManifestError("malformed", "The manifest is not JSON") from None
    if (not isinstance(document, dict) or document.get("format") != FORMAT
            or type(document.get("schema")) is not int or document.get("schema") != 1):
        raise ManifestError("malformed", "Not an Angelo release manifest (schema 1)")
    if document.get("version") != version or document.get("platform") != platform:
        raise ManifestError("wrong_release", "The manifest is for %r %r, not %s %s" % (
            document.get("version"), document.get("platform"), version, platform))
    if document.get("root") != root_name(version, platform):
        raise ManifestError("wrong_release", "The manifest's top folder is not %s" % root_name(version, platform))
    listed = document.get("entries")
    if not isinstance(listed, list) or not listed or len(listed) > MAX_ENTRIES:
        raise ManifestError("malformed", "The manifest lists no entries")
    entries = {}
    for value in listed:
        path, entry = _entry(value)
        if path in entries:
            raise ManifestError("malformed", "%s is listed twice" % path)
        entries[path] = entry
    for path in entries:   # Every entry's folder is listed as a folder: nothing hangs below a file or a link.
        parent = path.rpartition("/")[0]
        if parent and entries.get(parent, {}).get("type") != "dir":
            raise ManifestError("malformed", "%s is listed without its folder" % path)
        if entries[path]["type"] == "link" and _escapes(path, entries):
            raise ManifestError("bad_link", "%s leaves the release through its links" % path)
    return entries


def _escapes(path, entries, limit=40):
    """True when the link at ``path`` resolves outside the release once the listed links it passes through are
    followed (``a -> ..`` then ``b -> a/../..`` passes the lexical check of each link alone), or loops."""
    stack, pending, hops = path.split("/")[:-1], entries[path]["target"].split("/"), 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                return True
            stack.pop()
            continue
        stack.append(part)
        entry = entries.get("/".join(stack))
        if entry is not None and entry["type"] == "link":
            hops += 1
            if hops > limit:
                return True
            stack.pop()
            pending = entry["target"].split("/") + pending
    return False


# -- the download's minisign signature ------------------------------------------------------------------------

PREHASHED = b"ED"          # minisign's default form: Ed25519 over the file's BLAKE2b-512. The only form accepted.
MAX_MINISIG = 4 * 1024


def keynum(public):
    """minisign's 8-byte key number of a Linux release key, derived from its public key."""
    return hashlib.sha256(b"angelo-minisign-keynum\n" + public).digest()[:8]


def read_minisig(text, name="the download"):
    """``(key number, signature, trusted comment, global signature)`` of a ``.minisig``'s text, prehashed form only."""
    lines = text.split("\n") if isinstance(text, str) else []
    if len(text or "") > MAX_MINISIG or len(lines) < 4 or not lines[0].startswith("untrusted comment:") \
            or not lines[2].startswith("trusted comment: "):
        raise ManifestError("bad_minisig", "%s: not a minisign signature" % name)
    try:
        raw, global_signature = base64.b64decode(lines[1], validate=True), base64.b64decode(lines[3], validate=True)
    except (ValueError, binascii.Error):
        raise ManifestError("bad_minisig", "%s: the signature is not base64" % name) from None
    if len(raw) != 74 or raw[:2] != PREHASHED:
        raise ManifestError("bad_minisig", "%s: not a prehashed Ed25519 minisign signature" % name)
    return raw[2:10], raw[10:], lines[2][len("trusted comment: "):], global_signature


def blake2b_file(source):
    """The BLAKE2b-512 of a file, by path or from an open binary handle (read from where it stands)."""
    digest = hashlib.blake2b(digest_size=64)
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as handle:
            return blake2b_file(handle)
    for chunk in iter(lambda: source.read(_CHUNK), b""):
        digest.update(chunk)
    return digest.digest()


def check_minisig(path, text, public, key_number, name=None, handle=None):
    """Raises ``ManifestError`` unless ``text`` is minisign's signature of the file at ``path`` (or the open
    ``handle``, read once from where it stands) by ``public`` (key number ``key_number``), its trusted comment naming
    ``name``: the name the file is published under (default: the file's own name)."""
    name = name or Path(path).name
    number, signature, comment, global_signature = read_minisig(text, name)
    if number != key_number:
        raise ManifestError("unknown_key", "%s: signed by another key" % name)
    if not ed25519.verify(public, signature + comment.encode("utf-8"), global_signature):
        raise ManifestError("bad_minisig", "%s: the trusted comment's signature does not match" % name)
    if "file:%s" % name not in comment.split("\t"):
        raise ManifestError("bad_minisig", "%s: the signature was made for another file (%s)" % (name, comment))
    if not ed25519.verify(public, blake2b_file(path if handle is None else handle), signature):
        raise ManifestError("bad_signature", "%s: the signature does not match the file" % name)


def verify_minisig(path, text, keys, name=None, handle=None):
    """The key id when ``text`` is a valid minisign signature of the file at ``path`` (or ``handle``), published as
    ``name``, by one of ``keys``; raises ``ManifestError``."""
    name = name or Path(path).name
    if not keys:
        raise ManifestError("no_keys", "No Linux release key is configured in this build")
    number = read_minisig(text, name)[0]
    matching = [(key_id, public) for key_id, public in keys.items() if keynum(public) == number]
    if not matching:
        raise ManifestError("unknown_key", "%s: signed by a key this build does not trust" % name)
    check_minisig(path, text, matching[0][1], number, name, handle)
    return matching[0][0]


def extraction_filter(max_bytes, max_entries=MAX_ENTRIES):
    """A ``tarfile`` extraction filter: ``data``'s rules (no absolute paths, no link out of the folder, no device
    or set-id), every mode given explicitly (folders 0755, files 0755 or 0644 by their owner-execute bit) so the
    result never depends on the process umask, and at most ``max_bytes`` and ``max_entries`` unpacked."""
    import tarfile
    totals = {"bytes": 0, "entries": 0}

    def check(member, destination):
        member = tarfile.data_filter(member, destination)
        totals["entries"] += 1
        totals["bytes"] += max(member.size, 0)
        if totals["entries"] > max_entries or totals["bytes"] > max_bytes:
            raise ManifestError("too_large", "The update unpacks to more than its allowed size")
        if member.isdir():
            return member.replace(mode=0o755, deep=False)
        if member.isreg():
            return member.replace(mode=0o755 if member.mode & 0o100 else 0o644, deep=False)
        return member
    return check


def compare(expected, actual):
    """Raises ``ManifestError`` naming the first difference between two ``{path: entry}`` tables."""
    for path in sorted(set(expected) | set(actual)):
        if path not in actual:
            raise ManifestError("missing", "%s is missing" % path)
        if path not in expected:
            raise ManifestError("unexpected", "%s is not part of the release" % path)
        if expected[path] != actual[path]:
            raise ManifestError("changed", "%s is not the released %s" % (path, expected[path]["type"]))


def read_own_file(root, name, limit):
    """One of the manifest files at the top of ``root``: a regular file, never through a link. Whatever else is at
    the name (a folder, a FIFO, a device) is refused from the raw descriptor before a byte is read, and opening it
    never blocks (``O_NONBLOCK``); only ``ManifestError`` comes out, never an ``OSError``."""
    path = os.path.join(root, name)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise ManifestError("unsigned", "The release has no %s" % name) from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ManifestError("unsigned", "%s is not a file" % name)
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(descriptor, min(1 << 20, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError as error:
        raise ManifestError("unsigned", "%s could not be read: %s" % (name, error.strerror)) from None
    finally:
        os.close(descriptor)
    if size > limit:
        raise ManifestError("too_large", "%s is larger than %d bytes" % (name, limit))
    return b"".join(chunks)


def check_tree(root, version, keys=None, platform="linux-x64"):
    """The key id when the folder ``root`` is exactly the signed release ``version``; raises ``ManifestError``.

    ``keys`` defaults to the shipped ``linux_release_keys.json``. The signature is checked before anything in the
    manifest is believed, and the manifest before any file is read.
    """
    keys = load_keys() if keys is None else keys
    if Path(root).name != root_name(version, platform):
        raise ManifestError("wrong_release", "The folder is not %s" % root_name(version, platform))
    manifest = read_own_file(root, MANIFEST_NAME, MAX_MANIFEST)
    key_id = verify_signature(manifest, read_own_file(root, SIGNATURE_NAME, MAX_SIGNATURE), keys)
    expected = parse_manifest(manifest, version, platform)
    compare(expected, describe_tree(root))
    return key_id
