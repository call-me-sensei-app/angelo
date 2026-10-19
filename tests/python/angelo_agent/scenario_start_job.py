# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background job: a scenario's start file (``START``), made before its references (``scenario_references``).

    Blender -b --factory-startup --python-exit-code 1 --python scenario_start_job.py -- --scenario ID --out DIR

Runs the scenario's ``make_start`` from the factory scene, which saves
``DIR/<START>`` and appends it into a fresh factory scene as a trial does, and
writes what that took to ``DIR/start.json``. The reference job then opens the
start file as it begins (``scenario_references.run_job(..., blend=...)``), so
``scenario_job.py``, shared by every scenario's reference digest, is unchanged.
"""

import argparse
import json
import sys
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

import scenario_specs as specs  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=specs.SCENARIO_IDS + specs.ONESHOT_NAMES, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    scenario = specs.module(args.scenario)
    if not getattr(scenario, "START", None):
        print("SCENARIO_START_NONE", args.scenario, flush=True)
        return 1
    start = scenario.make_start(bpy, out)
    (out / "start.json").write_text(json.dumps(start, indent=2))
    print("SCENARIO_START", json.dumps(start), flush=True)
    return 0


sys.exit(main())
