# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which Codex a trial's app runs, by file digest: the plan every live-eval report carries.

The agent runtime keeps the Codex version string of the release it is built from, so two builds
(the 1.0.5 runtime and one with a later patch, both ``codex-cli 0.163.0``) cannot be told apart by
version. ``identity`` names the file the app will start, following the app's own rule
(``agent_runtime.executable``): ``ANGELO_AGENT_RUNTIME=off`` is the person's own Codex, a path there
is that file, and otherwise the runtime bundled beside the binary
(``Angelo.app/Contents/Helpers/codex``, ``<install folder>/agent-runtime/codex.exe``). It hashes that
file once, before any trial, so no timing includes it. Standard library only.
"""

import hashlib
import sys
from pathlib import Path

from angelo_agent import product_env  # (standard library only)

OVERRIDE = "AGENT_RUNTIME"  # ANGELO_AGENT_RUNTIME, as agent_runtime.OVERRIDE
OFF = "off"


def bundled(binary, platform=None):
    """The runtime bundled with the app whose executable is ``binary``."""
    binary = Path(binary)
    if (platform or sys.platform) == "darwin":
        return binary.parent.parent / "Helpers" / "codex"   # Angelo.app/Contents/MacOS/Angelo
    return binary.parent / "agent-runtime" / "codex.exe"


def digest(path, block=8 << 20):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(block), b""):
            value.update(chunk)
    return value.hexdigest()


def identity(binary, environ=None, platform=None):
    """``{"kind", "path", "sha256"}`` of the Codex the app at ``binary`` runs; ``kind`` is ``own`` (the
    person's Codex: no file to name), ``override`` or ``bundled``; a missing file has ``sha256`` None."""
    value = product_env.current(OVERRIDE, environ)
    if value is not None and value.strip().lower() == OFF:
        return {"kind": "own", "path": None, "sha256": None}
    if value is not None and value.strip():
        kind, path = "override", Path(value.strip()).expanduser()
    elif binary:
        kind, path = "bundled", bundled(binary, platform)
    else:
        return {"kind": "unknown", "path": None, "sha256": None}
    return {"kind": kind, "path": str(path), "sha256": digest(path) if path.is_file() else None}


def plan(binary, environ=None):
    """The run plan's part, built before any trial (no timing includes it): ``agent_runtime`` when there is an app or
    an override to name, else nothing."""
    if binary or product_env.current(OVERRIDE, environ) is not None:
        return {"agent_runtime": identity(binary, environ)}
    return {}
