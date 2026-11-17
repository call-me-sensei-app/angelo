# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's writers judged by the data their paths are built from, in the real app (doc/angelo/script_confinement.rst,
"Writers that read their path from data"; piece 10's delta review and its round 3). Angelo is enabled as it starts
(the real confinement); each script runs through ``run_confined``.

The checks render, composite and bake (``escapes_writers``, ``escapes_writers_data``): a script that changes what a
writer's path is built from (an output path, a view suffix, a material or cache name, a File Output folder, an
image's or a text's file, a project it wrote and appended) never makes Angelo's unconfined writer write over a file of
the person's, and a person's render or bake of their own data writes everything while scripts run. They live here,
apart from test_blender_confinement_escapes, because rendering and compositing may take a GPU path: this suite is
``needs_opengl`` (it runs where a GPU driver is), and the escapes suite stays free of the GPU.

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import gc
import json
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-confinement-writers-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "connections"))
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
REPORT = {"success": False, "checks": {}}
PLAIN = OUT / "plain"


def check(name, ok, detail=None):
    REPORT["checks"][name] = bool(ok)
    if not ok and detail is not None:
        REPORT.setdefault("details", {})[name] = str(detail)[:600]


def run(source, **names):
    """``source`` as an agent script, through the executor's confinement, read back as the executor reads it."""
    from angelo_agent import script_confinement, script_errors
    from confinement_attacks_admit import admit
    namespace = {"__name__": "__angelo_script__", "bpy": bpy, **names}
    admit("escape", source)
    outcome = {"refused": None}
    try:
        _, result = script_confinement.run_confined(
            source, namespace, None, "escape", finish=lambda ns: script_errors.read_back(ns.get("result")))
        outcome["result"] = json.dumps(result, default=str)[:200]
    except script_confinement.ScriptConfined as error:
        outcome["refused"] = script_confinement.reason_of(error)
    except Exception as error:   # noqa: BLE001 - reported as the check's detail.
        outcome["error"] = repr(error)[:300]
    gc.collect()
    return outcome


def main():
    PLAIN.mkdir(parents=True, exist_ok=True)
    # The person's own saved project, as Blender's disk caches need one; its folder is apart from the person's other
    # folders (escapes_writers._person_folder), so the create-beside rule below it never covers them.
    document = OUT / "writers-document" / "project.blend"
    document.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(document))
    run("result = 1")   # A script region has begun: C answers by its records from here on.
    import escapes_writers   # The delta review's MUST-1/SHOULD-3: writers reading their path while a job runs.
    escapes_writers.run_all(run, check, PLAIN)
    import escapes_writers_data   # Round 3: the data a path is built from decides (names, suffixes, saves, MUST-E).
    escapes_writers_data.run_all(run, check, PLAIN)


try:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    main()
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
(OUT / "confinement-writers.json").write_text(json.dumps(REPORT, indent=2, default=str))
print(json.dumps(REPORT, default=str)[:6000])
if REPORT["success"]:
    print("ANGELO_CONFINEMENT_WRITERS_OK")
sys.exit(0 if REPORT["success"] else 1)
