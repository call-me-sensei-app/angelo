# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The final runs' candidate: one frozen copy of the built app and what it is (final_runs.py ``freeze``).

The copy is made once (a clone on APFS), never overwritten, and checked
against the source file by file; ``RECEIPT.json`` records its executable's
digest, a digest of the whole inventory and ``--version``. Every step checks
the executable again and a billed run the whole inventory, so a rebuild during
the campaign cannot mix conditions.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

RECEIPT = "RECEIPT.json"


def digest(path, block=8 << 20):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(block), b""):
            value.update(chunk)
    return value.hexdigest()


def executable(app):
    """The app's executable: ``Angelo.app/Contents/MacOS/Angelo`` on macOS, the folder's ``angelo.exe`` on Windows."""
    app = Path(app)
    for candidate in (app / "Contents/MacOS/Angelo", app / "angelo.exe", app / "blender.exe"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("No Angelo executable in %s" % app)


def inventory(root):
    """``[(relative path, bytes, sha256)]`` of every file (the frozen app's identity)."""
    rows = []
    for folder, _dirs, files in os.walk(root):
        for name in files:
            path = Path(folder) / name
            if not path.is_symlink():
                rows.append((str(path.relative_to(root)), path.stat().st_size, digest(path)))
    return sorted(rows)


def freeze(source, into):
    """Copy the candidate app once (a clone on APFS) and record what it is; never overwrite a freeze."""
    source, into = Path(source).resolve(strict=True), Path(into).resolve()
    if into.exists():
        raise FileExistsError("Never overwrite a freeze: %s" % into)
    into.mkdir(parents=True)
    target = into / source.name
    if sys.platform == "darwin":
        subprocess.run(["/bin/cp", "-cRp", str(source), str(target)], check=True)
    else:
        shutil.copytree(source, target, symlinks=True)
    rows = inventory(target)
    if rows != inventory(source):
        raise RuntimeError("The copy differs from the source (was it rebuilt during the freeze?)")
    exe = executable(target)
    version = subprocess.run([str(exe), "--factory-startup", "--version"], capture_output=True, text=True, timeout=120)
    receipt = {"source": str(source), "app": str(target), "executable": str(exe), "executable_sha256": digest(exe),
               "inventory_sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest(), "files": len(rows),
               "version": [line.strip() for line in version.stdout.splitlines() if line.strip()][:8],
               "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (into / RECEIPT).write_text(json.dumps(receipt, indent=2))
    return receipt


def verify_candidate(config, full=False):
    """The frozen app is unchanged: its executable's digest (each step) or its whole inventory (``full``)."""
    receipt = json.loads((Path(config["candidate"]) / RECEIPT).read_text())
    if digest(receipt["executable"]) != receipt["executable_sha256"]:
        raise RuntimeError("The frozen candidate's executable changed: refusing to mix conditions")
    if full and hashlib.sha256(json.dumps(inventory(receipt["app"])).encode()).hexdigest() != receipt["inventory_sha256"]:
        raise RuntimeError("The frozen candidate's files changed: refusing to mix conditions")
    return receipt
