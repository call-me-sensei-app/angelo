# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Is the engine link's background child (write-back, preflight) confined? Run inside ``Angelo --background
--python`` with ``ANGELO_QA_DIR`` set. It starts a child the way ``engine_link_writeback.WriteBack._run`` does (this
binary, ``--background --factory-startup``, ``_child_environment()``), with a probe in place of ``CHILD``, and writes
``child-confinement.json``: whether C's confinement is installed in the child, whether its thread is confined, and
what ``may_write`` answers there for a path nothing vouched for. ``child_spec`` asks C about the child's targets in
the parent because the answer in the child is expected to be yes (doc/angelo/engine_link.rst)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ["ANGELO_QA_DIR"])
if not addon_utils.check("angelo_agent")[1]:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import engine_link_writeback, owned_paths  # noqa: E402

PROBE = r"""
import json, os, sys
from angelo_agent import owned_paths, script_confinement, script_confinement_blender
guard = script_confinement._guard
unvouched = os.path.join(os.path.expanduser("~"), "angelo-probe-never-vouched.txt")
print("PROBE" + json.dumps({
    "native_guard": guard is not None,
    "installed": bool(script_confinement_blender._native_installed),
    "confined": bool(guard.confined()) if guard is not None and hasattr(guard, "confined") else None,
    "may_write_unvouched": owned_paths.may_write(unvouched),
}))
"""

unvouched = os.path.join(os.path.expanduser("~"), "angelo-probe-never-vouched.txt")
command = [bpy.app.binary_path, "--background", "--factory-startup", "--python-exit-code", "1",
           "--python-expr", PROBE, "--", "unused"]
completed = subprocess.run(command, capture_output=True, timeout=300, env=engine_link_writeback._child_environment())
text = completed.stdout.decode("utf-8", "replace")
lines = [line[len("PROBE"):] for line in text.splitlines() if line.startswith("PROBE")]
result = {"parent_may_write_unvouched": owned_paths.may_write(unvouched), "returncode": completed.returncode,
          "child": json.loads(lines[-1]) if lines else None,
          "stderr_tail": completed.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "child-confinement.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print("child-confinement:", json.dumps(result))
sys.exit(0 if result["child"] is not None else 1)
