#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No builder path and no credential in what Angelo ships (doc/angelo/releasing.rst, "No builder paths").

Scans EVERY file of an installed bundle or an unpacked installer, binaries included, byte by byte in UTF-8/ASCII
and UTF-16LE, for:

* absolute personal or machine paths: ``/Users/<name>``, ``/home/<name>``, ``/var/folders/``,
  ``/private/var/folders/``, ``<drive>:\\Users\\<name>`` (either separator);
* Windows short (8.3) paths: a drive root followed by a segment such as ``ANGELO~1`` (``C:\\ANGELO~1\\cargo``), which
  the long-path patterns and the compilers' prefix maps do not see;
* the building machine's own identity: the user name and host name it runs under, plus any ``--identity``, and its
  folders: home and temporary folders from the environment plus any ``--identity-path`` (source, build), each in
  its long and, on Windows, its 8.3 short form;
* credentials in their published (or, for GitHub's fine-grained tokens, observed) formats: private keys with key
  material, provider API keys and tokens. Not seen: a credential encoded again (base64, as in a ``Basic`` header), one
  split across lines, and pre-2021 unprefixed 40-hex GitHub tokens, which no shape tells from a SHA-1;
* build artifacts by type (``.pdb``, ``.exp``, ``.ilk``, ``.iobj``, ``.ipdb``, ``.obj``, ``.lib``): they do not belong in an
  installer at all, so they are excluded from the install, never allowed.

A ``.pyc`` is read through its code objects (each string constant on its own) when this interpreter can load it, so a
path's end is the string's end; otherwise its raw bytes are scanned.

Any hit fails, naming each file and its count. The only exceptions are listed, each with its reason, in
``builder_paths_allow.json``: third-party files Angelo ships unchanged (Blender's precompiled libraries and
embedded files, the Python standard library's documentation examples, the upstream agent-runtime helper built on a
public CI runner). Nothing of Angelo's own build may be there: the build maps its own paths away
(``build_files/cmake/angelo_build_paths.cmake``, ``build_files/utils/angelo_python_bytecode.py``).

``release_checks.py builder-paths <folder>`` runs it; ``--installer FILE --receipt OUT`` writes a receipt tying a
clean scan to that installer's SHA-256, which ``publish_installers.py`` requires for an MSI (macOS mounts the disk
image and scans it directly). Standard library only: it also runs on Windows with the bundled Python.
"""

import fnmatch
import getpass
import hashlib
import json
import os
import re
import socket
import sys
from pathlib import Path

ALLOW_FILE = Path(__file__).resolve().with_name("builder_paths_allow.json")
RECEIPT_KIND = "angelo-builder-paths"
# A path is personal only where it starts: "/input/home/click" (OpenXR) or "example.edu/home/x.pdf" (a URL) are
# not, "file:///Users/<name>" is.
_START = r"(?<![A-Za-z0-9_.\-])"
# The operating systems' own profile folders under Users name no one: Windows' shared and template profiles
# (C:\Users\Public, Default, Default User, All Users; pip's vendored platformdirs names C:\Users\Public) and
# macOS' /Users/Shared. A rule skips exactly these names (any case on Windows), never a longer one (Publicity).
_NOT_SYSTEM_PROFILE_WINDOWS = r"(?!(?:Public|Default(?: User)?|All Users)(?![A-Za-z0-9._-]))"
_NOT_SYSTEM_PROFILE_MACOS = r"(?!Shared(?![A-Za-z0-9._-]))"
PATHS = {
    "home folder (macOS)": _START + r"/Users/" + _NOT_SYSTEM_PROFILE_MACOS + r"[A-Za-z0-9._-]+",
    "home folder (Linux)": _START + r"/home/[A-Za-z0-9._-]+",
    "temporary folder (macOS)": _START + r"(?:/private)?/var/folders/[A-Za-z0-9_+-]+",
    "home folder (Windows)": r"(?<![A-Za-z0-9_])[A-Za-z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/)" + _NOT_SYSTEM_PROFILE_WINDOWS
                             + r"[A-Za-z0-9._-]+",
    # Windows 8.3 short names (C:\ANGELO~1\cargo\registry\...), under a drive root.
    "Windows short (8.3) path": r"(?<![A-Za-z0-9_])[A-Za-z]:(?:\\\\|\\|/)(?:[A-Za-z0-9 ._$()-]{1,64}(?:\\\\|\\|/)){0,12}?"
                                r"[A-Za-z0-9_$-]{1,6}~[0-9]{1,6}(?![A-Za-z0-9])",
    # Classic HFS form, as Finder aliases store it ("Macintosh HD:Users:<name>:", ":private:var:folders:").
    "home folder (HFS alias)": r"(?<![A-Za-z0-9_.\-]):?Users:" + _NOT_SYSTEM_PROFILE_MACOS + r"[A-Za-z0-9._-]+:",
    "temporary folder (HFS alias)": r"(?<![A-Za-z0-9_.\-]):?(?:private:)?var:folders:",
}
SECRETS = {
    "private key": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\s{0,4}[A-Za-z0-9+/=]{40}",
    "Anthropic API key": r"sk-ant-(?:api|admin)[0-9]{2}-[A-Za-z0-9_-]{80,}",
    # OpenAI keys carry "T3BlbkFJ" inside a long body; a table of key prefixes ("sk-proj-sk-svcacct-...") does not.
    "OpenAI API key": r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}T3BlbkFJ[A-Za-z0-9_-]{20,}",
    "AWS access key": r"(?<![A-Z0-9])AKIA(?![0-9A-Z]{9}EXAMPLE)[0-9A-Z]{16}(?![A-Z0-9])",  # AWS's documented example is none
    # Classic tokens: a prefix and at least 36 base62 characters (refresh tokens are longer). Fine-grained tokens:
    # github_pat_ and an 82-character body observed as 22 + "_" + 59 (the split is not published, and lengths may
    # grow, so the first segment is open-ended). No boundary after either: a token baked into a binary's string pool
    # has the next constant right after it. The shape alone rejects a program's own prefix constant followed by other
    # constants ("github_pat_GITHUB_TOKENGH_ENTERPRISE_TOKEN..." in the bundled Codex: 6 characters, not 22, before "_").
    "GitHub token": r"(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{36}",
    "GitHub fine-grained token": r"github_pat_[A-Za-z0-9]{22,}_[A-Za-z0-9]{59}",
    "Slack token": r"xox[abprs]-[0-9]{6,}-[0-9A-Za-z-]{10,}",
    "Google API key": r"AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])",
    "Stripe live key": r"(?:sk|rk)_live_[0-9A-Za-z]{24,}",
}
# The marker a diagnostic build embeds when it is made without the agent-script confinement (CMake
# ANGELO_GUARD_DIAG_NO_INSTALL; bpy_angelo_guard_confine_run.cc): never in what ships, never allowed. Joined at run
# time so neither this file nor its bytecode holds it.
DIAGNOSTIC_MARKER = "_".join(("ANGELO", "GUARD", "DIAGNOSTIC", "BUILD", "NOT", "FOR", "RELEASE"))
DIAGNOSTIC_KIND = "diagnostic build without the agent-script confinement"
# Files that are build output, never part of an installer: refused by type, whatever they hold.
BUILD_ARTIFACTS = (".pdb", ".exp", ".ilk", ".iobj", ".ipdb", ".obj", ".lib")

# A name this generic is no identity (shared CI and container accounts); one this short would match anything.
GENERIC_NAMES = {"runner", "runneradmin", "user", "users", "admin", "administrator", "build", "builder", "buildbot", "root",
                 "ubuntu", "vagrant", "jenkins", "localhost", "mac", "macbook", "default", "guest", "public"}


class ScanError(Exception):
    pass


def builder_identity(extra=()):
    """The user and host names of the machine running the check (plus ``extra``), generic ones left out."""
    names = set(extra)
    for value in (os.environ.get("USER"), os.environ.get("USERNAME"), os.environ.get("LOGNAME")):
        names.add(value)
    try:
        names.add(getpass.getuser())
    except Exception:
        pass
    host = socket.gethostname()
    names.update({host, host.split(".")[0], os.environ.get("COMPUTERNAME")})
    return sorted(name for name in names if name and len(name) >= 4 and name.lower() not in GENERIC_NAMES)


# A literal every match contains (lower case): the regular expression runs only on a file that holds one, so a
# bundle of large binaries is scanned at memory speed.
ANCHORS = {
    "home folder (macOS)": b"/users/", "home folder (Linux)": b"/home/", "temporary folder (macOS)": b"/var/folders/",
    "home folder (Windows)": b"users", "Windows short (8.3) path": b"~", "home folder (HFS alias)": b"users:",
    "temporary folder (HFS alias)": b"var:folders:", "private key": b"private key-----", "Anthropic API key": b"sk-ant-",
    "OpenAI API key": b"t3blbkfj", "AWS access key": b"akia", "GitHub token": b"gh",
    "GitHub fine-grained token": b"github_pat_", "Slack token": b"xox",
    "Google API key": b"aiza", "Stripe live key": b"_live_",
}


# Folders that name no one: a builder folder this generic is not an identity.
GENERIC_FOLDERS = {"/tmp", "/private/tmp", "/var/tmp", "/", "c:\\", "c:/", "c:\\windows\\temp", "c:\\temp"}


def _short_path(path):
    """Windows' 8.3 form of an existing folder (GetShortPathNameW), or None elsewhere or when it has none."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
        return buffer.value if 0 < length < len(buffer) and buffer.value != str(path) else None
    except Exception:
        return None


def builder_folders(extra=()):
    """The building machine's home and temporary folders (from the environment) and ``extra`` (source, build),
    each absolute, plus its 8.3 short form on Windows; generic folders left out."""
    found = []
    for value in [os.environ.get(name) for name in ("HOME", "USERPROFILE", "TMPDIR", "TEMP", "TMP", "LOCALAPPDATA",
                                                    "APPDATA")] + list(extra):
        if not value:
            continue
        path = os.path.abspath(str(value)).rstrip("\\/") or str(value)
        for form in (path, os.path.realpath(path), _short_path(path)):
            if not form or len(form) < 4 or form.lower() in GENERIC_FOLDERS or form in found:
                continue
            if os.path.basename(form.rstrip("\\/")).lower() not in GENERIC_NAMES:  # not a CI account's home
                found.append(form)
    return found


def _folder_pattern(folder):
    """A folder as it may be written: either separator (single or escaped), any case."""
    parts = [re.escape(part) for part in re.split(r"[\\/]+", folder) if part]
    lead = "" if re.match(r"^[A-Za-z]:", folder) else r"(?:\\\\|\\|/)"
    return (lead + r"(?:\\\\|\\|/)".join(parts) + r"(?![A-Za-z0-9._-])").encode()


def _rules(identity, folders=()):
    """``[(kind, pattern, class, anchor)]``."""
    rules = [(kind, re.compile(pattern.encode(), re.I if "Windows" in kind else 0), "path", ANCHORS[kind])
             for kind, pattern in PATHS.items()]
    rules += [("builder identity", re.compile(rb"(?<![A-Za-z0-9])" + re.escape(name.encode()) + rb"(?![A-Za-z0-9])",
                                              re.I), "identity", name.lower().encode()) for name in identity]
    rules += [("builder folder", re.compile(_folder_pattern(folder), re.I), "identity",
               max(re.split(r"[\\/]+", folder), key=len).lower().encode()) for folder in folders]
    rules += [(kind, re.compile(pattern.encode()), "secret", ANCHORS[kind]) for kind, pattern in SECRETS.items()]
    rules.append((DIAGNOSTIC_KIND, re.compile(re.escape(DIAGNOSTIC_MARKER.encode())), "diagnostic",
                  DIAGNOSTIC_MARKER.lower().encode()))
    return rules


def load_allow(path=ALLOW_FILE):
    """``[(file globs, compiled match, reason, context, upstream)]``; every entry must give files, match and reason.

    Two optional conditions narrow an entry from "this text in these files" to "this text where upstream put it":
    ``context``, a regular expression the text right after the hit must start with (for a file Angelo builds, such as
    the executable, that links upstream's static libraries), and ``upstream: true``, which excuses a hit only in a
    file whose bytes equal a file of the pinned upstream library tree (``scan(upstream=...)``, ``--upstream-root``;
    bytecode counts when its source does). Without the tree such an entry excuses nothing."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["allow"]
    allow = []
    for entry in entries:
        if not (entry.get("files") and entry.get("match") and entry.get("reason")):
            raise ScanError("%s: every entry needs files, match and reason: %r" % (path, entry))
        context = re.compile(entry["context"]) if entry.get("context") else None
        allow.append((list(entry["files"]), re.compile(entry["match"]), entry["reason"], context,
                      bool(entry.get("upstream"))))
    return allow


def _allowed(allow, relative, text, rule_class, after="", is_upstream=None):
    """The reason an allow entry excuses this hit, or None. ``after``: the text following the hit; ``is_upstream()``:
    whether the hit's file is a file of the pinned upstream library tree."""
    if rule_class in ("secret", "identity", "diagnostic"):
        return None  # never allowed: a credential, this machine's own name or a build without the confinement
    for entry in allow:
        globs, match, reason = entry[:3]
        context, upstream = (tuple(entry[3:5]) + (None, False))[:2]
        if not (match.fullmatch(text) and any(fnmatch.fnmatchcase(relative, glob) for glob in globs)):
            continue
        if context is not None and not context.match(after):
            continue
        if upstream and not (is_upstream and is_upstream()):
            continue
        return reason
    return None


def upstream_digests(roots):
    """The SHA-256 of every regular file below ``roots`` (the pinned upstream library trees)."""
    digests = set()
    for root in roots:
        for folder, _dirs, files in os.walk(root):
            if os.sep + ".git" in folder + os.sep:
                continue
            for name in files:
                path = os.path.join(folder, name)
                if not os.path.islink(path) and os.path.isfile(path):
                    digests.add(sha256_of(path))
    return digests


def homes_of(identity, folders):
    """The home folders a builder with these names and folders has, as hit texts are written."""
    homes = set(folders)
    for name in identity:
        homes.update({"/home/" + name, "/Users/" + name, "C:\\Users\\" + name, "C:/Users/" + name})
    home = os.path.expanduser("~")
    if home and home not in ("~", "/"):
        homes.add(home)
    return sorted(homes)


def allow_problems(allow, identity, folders):
    """An allow entry must never be able to excuse the scanning machine's own home: why one could (empty: none)."""
    problems = []
    for entry in allow:
        for home in homes_of(identity, folders):
            if entry[1].fullmatch(home.rstrip("\\/")):
                problems.append("the allow entry %r would excuse this machine's own home %s: refusing to scan with it"
                                % (entry[2][:60], home))
    return problems


def _pyc_strings(data):
    """A .pyc's text as Python reads it: every string and bytes constant and name of its code objects, one per line,
    so a path ends where its string ends (raw marshal bytes put a type byte right after it). None when this
    interpreter cannot read the file (another Python version, not bytecode): the raw bytes are scanned instead."""
    import importlib.util
    import marshal
    if len(data) < 16 or data[:4] != importlib.util.MAGIC_NUMBER:
        return None
    try:
        code = marshal.loads(data[16:])
    except (ValueError, EOFError, TypeError):
        return None
    found, stack, seen = [], [code], set()
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, str):
            found.append(item.encode("utf-8", "surrogatepass"))
        elif isinstance(item, bytes):
            found.append(item)
        elif isinstance(item, (tuple, list, frozenset, set)):
            stack.extend(item)
        elif hasattr(item, "co_consts"):
            stack.extend(item.co_consts)
            stack.extend(item.co_names)
            stack.extend(item.co_varnames)
            stack.extend((item.co_filename, item.co_name, getattr(item, "co_qualname", "")))
            stack.extend(getattr(item, "co_freevars", ()) + getattr(item, "co_cellvars", ()))
    return b"\n".join(found)


def _wide(anchor):
    return b"".join(bytes([c]) + b"\x00" for c in anchor)


def _views(data, anchors):
    """``(encoding, start, view)``: the file's bytes, and its UTF-16LE text from byte 0 and from byte 1, re-encoded as
    UTF-8, when it holds an anchor in UTF-16LE. ``start`` is the byte the view begins at (``_file_offset``)."""
    yield "UTF-8", 0, data
    lower = data.lower()
    if any(_wide(anchor) in lower for anchor in anchors):
        for offset in (0, 1):
            wide = data[offset:]
            text = wide[: len(wide) - len(wide) % 2].decode("utf-16-le", "replace")
            yield "UTF-16LE", offset, text.encode("utf-8", "replace")


def _file_offset(encoding, start, view, position):
    """The byte in the file where a hit at ``position`` of a ``_views`` view begins: each character of a UTF-16LE view
    is one 2-byte code unit of the file (an undecodable unit became one replacement character)."""
    if encoding != "UTF-16LE":
        return start + position
    return start + 2 * len(view[:position].decode("utf-8", "replace"))


def _listed_name(relative):
    """A folder-relative POSIX path as ``only`` holds it: in ``os.path.normcase``'s case (lower case on Windows)."""
    return os.path.normcase(relative).replace(os.sep, "/")


def listed_files(folder, manifest):
    """The files of ``manifest`` (one path per line, absolute or relative to ``folder``, as CMake's
    install_manifest.txt) that lie inside ``folder``, as folder-relative POSIX paths (``_listed_name``)."""
    root = os.path.normcase(os.path.realpath(folder))
    chosen = set()
    for line in Path(manifest).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        path = os.path.normcase(os.path.realpath(line if os.path.isabs(line) else os.path.join(folder, line)))
        if path.startswith(root + os.sep):
            chosen.add(path[len(root) + 1:].replace(os.sep, "/"))
    return chosen


def scan(folder, identity=None, allow=None, folders=None, only=None, upstream=None):
    """Scan every regular file below ``folder``. Returns ``{"files", "bytes", "hits": {path: {kind: n}},
    "examples": {path: [text]}, "allowed": {reason: n}}``. Symbolic links are not followed. ``only`` (folder-relative
    POSIX paths, compared case-insensitively on Windows) limits the scan to those files: what an install wrote.
    ``upstream``: the SHA-256 set of the pinned upstream library tree (``upstream_digests``), for ``upstream`` entries.
    ``report["allowed_files"]`` counts each excused hit per reason and file."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ScanError("%s is not a folder" % folder)
    identity = builder_identity() if identity is None else identity
    folders = builder_folders() if folders is None else folders
    allow = load_allow() if allow is None else allow
    rules = _rules(identity, folders)
    only = None if only is None else {_listed_name(name) for name in only}
    wide_anchors = sorted({anchor for _kind, _pattern, _rule_class, anchor in rules})   # credentials too
    report = {"files": 0, "bytes": 0, "identity": list(identity), "folders": list(folders), "hits": {}, "examples": {},
              "locations": {},
              "allowed": {}, "allowed_files": {}}
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            path = Path(root, name)
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(folder).as_posix()
            if only is not None and _listed_name(relative) not in only:
                continue
            data = path.read_bytes()
            verdict = []

            def is_upstream(path=path, data=data, verdict=verdict):
                if not verdict:
                    source = data
                    if path.name.endswith(".pyc") and path.parent.name == "__pycache__":
                        # Bytecode is upstream when the source it was compiled from is.
                        source_path = path.parent.parent / (path.name.split(".")[0] + ".py")
                        source = source_path.read_bytes() if source_path.is_file() else None
                    verdict.append(upstream is not None and source is not None
                                   and hashlib.sha256(source).hexdigest() in upstream)
                return verdict[0]
            raw = data
            if name.endswith(".pyc"):
                data = _pyc_strings(data) or data
            report["files"] += 1
            if name.lower().endswith(BUILD_ARTIFACTS):
                report["hits"].setdefault(relative, {})["build artifact (exclude it from the install)"] = 1
                report["examples"].setdefault(relative, []).append(name)
            report["bytes"] += len(data)
            seen = set()
            bytecode = name.endswith(".pyc") and data is not raw
            for encoding, view_start, view in _views(data, wide_anchors):
                if bytecode:
                    encoding = "bytecode string constants"   # offsets count in the constants, one per line
                lower = view.lower()
                for kind, pattern, rule_class, anchor in rules:
                    if anchor not in lower:
                        continue
                    for found in pattern.finditer(view):
                        text = found.group(0).decode("utf-8", "replace")
                        if rule_class == "path":
                            text = text.rstrip(".")  # a sentence's full stop is no part of the path
                        key = (kind, found.start(), len(view))
                        if key in seen:
                            continue
                        seen.add(key)
                        after = view[found.end():found.end() + 256].decode("utf-8", "replace")
                        reason = _allowed(allow, relative, text, rule_class, after, is_upstream)
                        if reason:
                            report["allowed"][reason] = report["allowed"].get(reason, 0) + 1
                            per_file = report["allowed_files"].setdefault(reason, {})
                            per_file[relative] = per_file.get(relative, 0) + 1
                            continue
                        counts = report["hits"].setdefault(relative, {})
                        counts[kind] = counts.get(kind, 0) + 1
                        examples = report["examples"].setdefault(relative, [])
                        if len(examples) < 3:
                            examples.append(text if rule_class != "secret" else text[:8] + "…")
                        locations = report["locations"].setdefault(relative, [])
                        if len(locations) < 3:
                            locations.append([kind, encoding, _file_offset(encoding, view_start, view, found.start())])
    if report["files"] == 0:
        raise ScanError("%s holds no files: nothing was scanned" % folder)
    return report


def diagnostic_files(folder):
    """Folder-relative paths of the files below ``folder`` that hold ``DIAGNOSTIC_MARKER``: a build made without the
    agent-script confinement. Read in overlapping blocks, so a file of any size is scanned whole."""
    marker = DIAGNOSTIC_MARKER.encode()
    found = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            path = Path(root, name)
            if path.is_symlink() or not path.is_file():
                continue
            tail = b""
            with open(path, "rb") as handle:
                while True:
                    block = handle.read(1 << 20)
                    if not block:
                        break
                    if marker in tail + block:
                        found.append(path.relative_to(folder).as_posix())
                        break
                    tail = block[-len(marker):]
    return found


def scan_disk_image(path, runner=None):
    """Mount a disk image read-only (macOS ``hdiutil``), scan everything in it, detach; the scan's report.
    ``runner(argv, check=...)`` runs a command (``subprocess.run`` by default)."""
    import subprocess
    import tempfile
    if runner is None:
        def runner(argv, check=True, timeout=None, env=None):
            return subprocess.run(argv, capture_output=True, text=True, check=check, timeout=timeout or 600)
    mount = Path(tempfile.mkdtemp(prefix="angelo-paths-"))
    try:
        runner(["hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-mountpoint", str(mount), str(path)])
        try:
            return scan(mount)
        finally:
            runner(["hdiutil", "detach", str(mount)], check=False)
    finally:
        try:
            mount.rmdir()
        except OSError:
            pass


def summary(report, limit=40):
    """Lines naming each file with hits and its count, most first."""
    rows = sorted(report["hits"].items(), key=lambda item: -sum(item[1].values()))
    lines = ["%d files, %.1f MB scanned; %d with builder paths or credentials" % (
        report["files"], report["bytes"] / 1e6, len(rows))]
    for relative, counts in rows[:limit]:
        where = report.get("locations", {}).get(relative)
        lines.append("  %6d  %s  (%s; e.g. %s%s)" % (
            sum(counts.values()), relative, ", ".join("%s %d" % item for item in sorted(counts.items())),
            " ".join(report["examples"][relative]),
            "; first in %s at byte %d" % (where[0][1], where[0][2]) if where else ""))
    if len(rows) > limit:
        lines.append("  ... and %d more files" % (len(rows) - limit))
    for reason, count in sorted(report["allowed"].items()):
        lines.append("  allowed %d: %s" % (count, reason))
    return lines


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def receipt(report, installer):
    installer = Path(installer)
    return {"kind": RECEIPT_KIND, "installer": installer.name, "sha256": sha256_of(installer),
            "files": report["files"], "hits": sum(sum(c.values()) for c in report["hits"].values()),
            "allow_sha256": sha256_of(ALLOW_FILE)}


def receipt_problem(installer, receipt_path=None):
    """None when ``installer`` has a clean receipt (``<installer>.builder-paths.json`` by default) for exactly its
    bytes; otherwise the reason."""
    installer = Path(installer)
    receipt_path = Path(receipt_path) if receipt_path else installer.with_name(installer.name + ".builder-paths.json")
    try:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "%s has no builder-path receipt (%s): scan its unpacked payload with release_checks.py builder-paths " \
               "--installer %s --receipt %s" % (installer.name, receipt_path.name, installer.name, receipt_path.name)
    if data.get("kind") != RECEIPT_KIND or data.get("installer") != installer.name:
        return "%s is not a builder-path receipt for %s" % (receipt_path.name, installer.name)
    if data.get("sha256") != sha256_of(installer):
        return "%s was written for other bytes than %s (rebuilt or re-signed since the scan?)" % (
            receipt_path.name, installer.name)
    if data.get("hits") != 0 or not data.get("files"):
        return "%s records %s builder paths or credentials in %s" % (receipt_path.name, data.get("hits"),
                                                                     installer.name)
    return None


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="refuse builder paths and credentials in a bundle or payload")
    parser.add_argument("folder", help="an installed bundle (Angelo.app) or an unpacked installer payload")
    parser.add_argument("--identity", action="append", default=[], help="another name of the building machine")
    parser.add_argument("--identity-path", action="append", default=[],
                        help="a folder of the building machine (source, build) that must not appear, long or 8.3")
    parser.add_argument("--installer", help="the installer this payload came from (with --receipt)")
    parser.add_argument("--receipt", help="write a receipt of a clean scan for --installer here")
    parser.add_argument("--report", help="write the full report as JSON here")
    parser.add_argument("--files-from", help="scan only the files this manifest lists (CMake's install_manifest.txt)")
    parser.add_argument("--upstream-root", action="append", default=[],
                        help="the pinned upstream library tree (LIBDIR): files byte-identical to one of its files may "
                             "keep upstream's build paths (allow entries with upstream: true)")
    arguments = parser.parse_args(argv)
    if bool(arguments.installer) != bool(arguments.receipt):
        parser.error("--installer and --receipt go together")
    try:
        only = listed_files(arguments.folder, arguments.files_from) if arguments.files_from else None
        identity, folders, allow = (builder_identity(arguments.identity), builder_folders(arguments.identity_path),
                                    load_allow())
        problems = allow_problems(allow, identity, folders)
        if problems:
            raise ScanError("; ".join(problems))
        upstream = upstream_digests(arguments.upstream_root) if arguments.upstream_root else None
        report = scan(arguments.folder, identity, allow, folders=folders, only=only, upstream=upstream)
    except (ScanError, OSError, ValueError) as error:
        print("builder-paths: %s" % error, file=sys.stderr)
        return 2
    for line in summary(report):
        print(line)
    if arguments.report:
        Path(arguments.report).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    if report["hits"]:
        print("builder-paths: refused: %d files embed builder paths or credentials" % len(report["hits"]),
              file=sys.stderr)
        return 1
    if arguments.receipt:
        Path(arguments.receipt).write_text(json.dumps(receipt(report, arguments.installer), indent=1) + "\n",
                                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
