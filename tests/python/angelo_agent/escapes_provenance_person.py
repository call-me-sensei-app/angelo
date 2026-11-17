# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's own Forget Agent Changes in a Folder, for escapes_provenance: a child Blender that escapes_provenance
starts with --enable-event-simulate (its own flag: the suite itself never stands in for the person), arms the
confinement with one script, forgets FOLDER as the person through C's harness scope, then reports whether Angelo
loads FILE as its own.

Usage: blender --background --factory-startup --enable-event-simulate --python escapes_provenance_person.py --
FOLDER FILE
"""

import io
import json
import os
import sys

import addon_utils
import bpy

folder, path = sys.argv[sys.argv.index("--") + 1:][:2]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import grant_authority, script_confinement  # noqa: E402
from confinement_attacks_admit import admit  # noqa: E402

admit("probe", "x = 1")
script_confinement.run_confined("x = 1", {"__name__": "__angelo_script__"}, None, "probe")
report = {}
try:
    report["forget"] = sorted(grant_authority.GUARD.harness(bpy.ops.angelo.forget_agent_changes, directory=folder))
except Exception as error:   # noqa: BLE001 - the report says what failed.
    report["forget"] = repr(error)
try:
    io.open_code(path).close()
    report["load"] = "loaded"
except Exception as error:   # noqa: BLE001 - a refusal is reported by its type.
    report["load"] = type(error).__name__
print("CHILD " + json.dumps(report))
