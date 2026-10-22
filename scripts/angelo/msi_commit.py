# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo MSI on a Windows build machine that is the build of the commit under test, for the install checks.

An install check took the newest ``*.msi`` it could find. When a run's build was skipped (the release stage's
``source-clean`` check refused the tree), the newest MSI was one an earlier run had built from another commit, and
the check installed it and passed. An MSI is accepted here only with proof that it is the commit's build: a build
manifest in its own folder (a JSON object, as ``angelo-release-build.json`` from build_release_windows.ps1 or
``angelo-identity-build.json`` from windows_validate.ps1) that names the file (``msi.name``), records its bytes
(``msi.sha256``) and says ``source_commit`` is the commit under test; and records ``build_hash`` (the hash the built
program reports), which must be that commit's too. Every other MSI is refused, with the reason.

    msi_commit.py --commit SHA [--pattern GLOB] (--msi PATH | --search FOLDER)...

The chosen MSI (the newest proven one) is printed as the last line; each refusal is printed before it. Exit 1 when
none is proven. ``--search`` looks two folders deep at most (a build folder's root, ``results`` and
``results\\release`` below ``C:\\angelo``), never into a source tree. Standard library only.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HEX = re.compile(r"[0-9a-f]{7,64}")
MANIFEST_BYTES = 4 * 1024 * 1024   # A build manifest is a few KB; larger JSON files beside an MSI are reports.


def same_commit(recorded, commit):
    """True when ``recorded`` names ``commit`` (full hashes equal, or one an abbreviation of the other)."""
    recorded, commit = str(recorded or "").strip().lower(), commit.strip().lower()
    if not HEX.fullmatch(recorded) or not HEX.fullmatch(commit):
        return False
    return recorded.startswith(commit) or commit.startswith(recorded)


def manifests(folder):
    """``[(path, data)]``: the JSON objects in ``folder`` that describe an MSI and the commit it was built from."""
    found = []
    for path in sorted(Path(folder).glob("*.json")):
        try:
            if path.stat().st_size > MANIFEST_BYTES:
                continue
            data = json.loads(path.read_bytes().decode("utf-8-sig"))
        except (OSError, UnicodeError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("msi"), dict) and "source_commit" in data:
            found.append((path, data))
    return found


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def refusal(msi, commit):
    """Why ``msi`` is not proven to be ``commit``'s build; None when it is."""
    msi = Path(msi)
    named = [(path, data) for path, data in manifests(msi.parent)
             if str(data["msi"].get("name") or "").lower() == msi.name.lower()]   # NTFS names ignore case
    if not named:
        return "no build manifest in its folder names it (another run's MSI, or a packaging copy)"
    digest, reasons = sha256(msi), []
    for path, data in named:
        recorded, hash_built = data.get("source_commit"), str(data.get("build_hash") or "")
        if not same_commit(recorded, commit):
            reasons.append("%s says it was built from %s, not the commit under test" % (path.name, recorded or "no commit"))
        elif str(data["msi"].get("sha256") or "").lower() != digest:
            reasons.append("its SHA-256 is %s but %s records %s: the file is another build's" % (
                digest[:16], path.name, str(data["msi"].get("sha256") or "none")[:16]))
        elif not hash_built:
            reasons.append("%s records no build hash (the hash the built program reports): unproven" % path.name)
        elif not same_commit(hash_built, commit):
            reasons.append("%s says the built program reports hash %s, not this commit's" % (path.name, hash_built))
        else:
            return None
    return "; ".join(reasons)


def candidates(msis, folders, pattern, depth=2):
    """Every MSI named or found (at most ``depth`` folders below each of ``folders``) whose name matches ``pattern``."""
    found = [Path(path) for path in msis]
    for folder in folders:
        base = len(Path(folder).parts)
        for top, dirs, files in os.walk(folder):
            if len(Path(top).parts) - base >= depth:
                dirs[:] = []
            found += [Path(top, name) for name in files if name.lower().endswith(".msi")]
    return [path for path in found if fnmatch.fnmatch(path.name.lower(), pattern.lower())]


def choose(commit, msis=(), folders=(), pattern="*.msi"):
    """``(chosen path or None, [refusal lines])`` for the MSIs named in ``msis`` or found in ``folders``."""
    if not HEX.fullmatch(str(commit or "").strip().lower()):
        return None, ["the commit under test is unknown or not a hash (%r): pass the commit that was sent" % commit]
    proven, lines = [], []
    for path in candidates(msis, folders, pattern):
        if not path.is_file():
            lines.append("refused %s: no such file" % path)
            continue
        why = refusal(path, commit)
        if why:
            lines.append("refused %s: %s" % (path, why))
        else:
            proven.append(path)
    if not proven:
        lines.append("no MSI is proven to be the build of %s (%d refused)" % (commit, len(lines)))
        return None, lines
    return max(proven, key=lambda path: path.stat().st_mtime), lines


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", required=True, help="the commit under test (the one sent to the machine)")
    parser.add_argument("--msi", action="append", default=[], help="an MSI to check (repeatable)")
    parser.add_argument("--search", action="append", default=[], help="a folder to look in, two levels deep")
    parser.add_argument("--pattern", default="*.msi", help="file names to consider, e.g. Angelo-*-windows-x64.msi")
    arguments = parser.parse_args(argv)
    if not arguments.msi and not arguments.search:
        parser.error("name an MSI (--msi) or a folder to search (--search)")
    chosen, lines = choose(arguments.commit, arguments.msi, arguments.search, arguments.pattern)
    for line in lines:
        print(line)
    if chosen is None:
        return 1
    print(chosen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
