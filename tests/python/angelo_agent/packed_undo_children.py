# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Runs each case of the packed linked data Undo suites in a child Blender under ``--log "*undo*"``, and checks
which of Undo's keep paths its log shows (test_blender_linked_packed_undo_data.py, _undo_chains.py).

A case passes only when its child passed it and its log shows every path the case exists for, and none it must
not take: the suites prove that each branch of ``read_undo_keep_packed_ids_used_by_kept_ids`` (readfile.cc) ran,
not only that Blender lived. The child inherits the suite's isolated environment; its QA folder is its own.
"""

import json
import os
import re
import subprocess
import sys

# What the read logs when it takes each path (readfile.cc, CLOG "undo" at debug level).
PATHS = {
    "kept": r"UNDO: keep packed linked datablock ",
    "name taken": r"\(name taken in its own\)",
    "new archive": r"UNDO: new archive library ",
    "dropped copy": r"UNDO: drop the step's ",
    "remapped": r"read again by this step: remapped to that copy",
    "archive read again": r"UNDO: archive library \S+ read again by this step",
    "parent moved": r"UNDO: parent library \S+ moved over",
    "same-file parent": r"UNDO: parent library \S+ stands for ",
    "library renamed": r"UNDO: library renamed ",
}


def child_case():
    """In a child, the index of the case to run; None in the parent."""
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return int(arguments[arguments.index("--case") + 1]) if "--case" in arguments else None


def run_children(script, names, out, report, save_report, report_name, expected, timeout=900):
    """Each case in its own Blender with Undo's log. `expected` maps a case name to (paths it must show, paths it
    must not show)."""
    import bpy
    for index, name in enumerate(names):
        folder = out / ("case-%02d" % index)
        folder.mkdir(parents=True, exist_ok=True)
        report["running"] = name
        save_report()
        command = [bpy.app.binary_path, "--background", "--factory-startup", "--python-exit-code", "1",
                   "--log", "*undo*", "--log-level", "debug", "--python", script, "--", "--case", str(index)]
        with open(folder / "log.txt", "wb") as log:
            code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                                  env=dict(os.environ, ANGELO_QA_DIR=str(folder))).returncode
        text = (folder / "log.txt").read_text(errors="replace")
        try:
            child = json.loads((folder / report_name).read_text())
        except (OSError, ValueError):
            child = {"cases": {}, "error": "no report"}
        facts = dict(child["cases"].get(name) or {"error": child.get("error")})
        facts.update(exit=code, paths=sorted(key for key, pattern in PATHS.items() if re.search(pattern, text)))
        report["cases"][name] = facts
        save_report()
        assert code == 0 and facts.get("passed"), "%s: child exit %s: %s" % (
            name, code, (facts.get("error") or child.get("error") or text[-800:]))
        required, forbidden = expected[name]
        missing = sorted(set(required) - set(facts["paths"]))
        taken = sorted(set(forbidden) & set(facts["paths"]))
        assert not missing and not taken, "%s: Undo's log does not show %s, or shows %s (it shows %s)" % (
            name, missing, taken, facts["paths"])
