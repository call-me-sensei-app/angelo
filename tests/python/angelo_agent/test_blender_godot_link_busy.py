# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's saves while Godot is busy importing, with a real Angelo and a real Godot editor, both without windows
(doc/angelo/godot_link.rst, "Saving while Godot imports"). A host-Python driver for run_native_suites.py, or by hand:

    python3 tests/python/angelo_agent/test_blender_godot_link_busy.py --binary /abs/Angelo.app/Contents/MacOS/Angelo \\
        --out DIR [--godot GODOT]

Found by the Godot showcase (A3, 2026-11-15): the editor once kept showing the pre-save asset until Reload Saved
Scene. Godot's ``reimport_files`` refuses while Godot is importing already, and the plugin now keeps such a re-import
owed and retries it (``REIMPORT_RETRY``) until it runs or ``REIMPORT_GIVE_UP``, telling the person then. This run
proves the outcome a person sees, in one session, through ``godot_link_e2e/busy_driver`` (Godot's side, with a test
importer that keeps Godot importing on demand) and ``godot_link_e2e/angelo_side_busy.py`` (Angelo's side, saving
when the driver asks):

* several saves in a row: the asset ends holding the newest save, nothing owed, and Angelo hears it re-imported;
* a save during a slow import: the asset ends holding it, nothing owed;
* switching links: a save of one asset during an import, then the link switched to another asset and that saved too:
  both end holding their own newest save;
* timeout recovery: a save during an import longer than the give-up, then another save: that one shows, nothing
  owed, and the person was told at most once.

Each case also records whether the save reached Godot while it was importing (Godot's own log counts the refused
re-imports). The same throwaway project, private HOME and link record as test_blender_godot_link_e2e.py. Standard
library only.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import argparse
import json
import subprocess
import time
from pathlib import Path

import test_blender_godot_link_e2e as e2e  # noqa: E402  The project, the environment and the processes.

HERE = Path(__file__).resolve().parent / "godot_link_e2e"
DRIVER = HERE / "busy_driver/angelo_link_busy"
SECONDS = 330
REFUSED = "reimport_files() recursively"   # Godot's error when a re-import is asked for while it imports.


def run(angelo, godot, out, windowed=False, pump=""):
    folder = out / ("busy-windowed" if windowed else "busy")
    folder.mkdir(parents=True)
    home = folder / "home"
    home.mkdir()
    record = folder / "link-record.json"
    project, version = e2e.build_project(folder, angelo, godot, home, record, driver=DRIVER)
    env = e2e._environment(home, record, {"ANGELO_QA_DIR": str(folder), "ANGELO_E2E_SECONDS": str(SECONDS - 20),
                                           "ANGELO_E2E_PUMP": pump})
    side = subprocess.Popen([angelo, "--background", "--factory-startup", "--python-exit-code", "1",
                             "--python", str(HERE / "angelo_side_busy.py")], env=env,
                            stdout=(folder / "angelo.log").open("w"), stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 60
    while not record.exists() and side.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    editor = subprocess.Popen([godot] + ([] if windowed else ["--headless"]) + ["--editor", "--path", str(project)], env=env,
                              stdout=(folder / "godot.log").open("w"), stderr=subprocess.STDOUT)
    limit = deadline + SECONDS
    codes = {}
    for label, process in (("godot", editor), ("angelo", side)):
        try:
            codes[label] = process.wait(timeout=max(1, limit - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            codes[label] = "timeout"
    reports = {}
    for label in ("angelo", "godot"):
        path = folder / (label + ".json")
        reports[label] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"missing": True}
    checks = {"%s.%s" % (label, key): value for label, report in reports.items()
              for key, value in report.get("checks", {}).items()}
    log = (folder / "godot.log").read_text(errors="replace").splitlines()
    errors = [line for line in log if "SCRIPT ERROR" in line or "Parse Error" in line]
    checks["godot.no_script_errors"] = not errors
    saves = reports["angelo"].get("saves", [])
    refused = sum(REFUSED in line for line in log)
    checks["godot_refused_a_reimport_while_importing"] = refused > 0   # The race itself ran, not only its outcome.
    ok = codes == {"godot": 0, "angelo": 0} and checks and all(checks.values())
    return {"run": folder.name, "godot": version, "windowed": windowed, "pump": pump, "ok": bool(ok), "exit": codes, "checks": checks,
            "script_errors": errors[:5], "refused_reimports_in_godot_log": refused,
            "problem": reports["godot"].get("problem"), "godot_notes": reports["godot"].get("notes"),
            "godot_steps": reports["godot"].get("steps"), "angelo_saves": saves,
            "angelo_reports": reports["angelo"].get("reports"),
            "angelo_error": reports["angelo"].get("error") or reports["angelo"].get("traceback")}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, help="the Angelo under test")
    parser.add_argument("--out", required=True)
    parser.add_argument("--godot", default=e2e.GODOT, help="a Godot 4.4+ editor (default: ANGELO_QA_GODOT)")
    parser.add_argument("--windowed", action="store_true",
                        help="a windowed editor (A3 was seen in one), its own loop delivering messages mid-import")
    parser.add_argument("--pump", choices=("", "0", "1"), default="",
                        help="poll the link mid-import: 1 always, 0 never, default only when headless")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not os.path.isfile(args.godot):
        report = {"success": False, "error": "Godot not found; set ANGELO_QA_GODOT to a Godot 4.4+ editor binary"}
    else:
        result = run(args.binary, args.godot, out / "runs", args.windowed, args.pump)
        report = {"success": result["ok"], "runs": [result]}
        print(("PASS" if result["ok"] else "FAIL"), json.dumps(result)[:4000])
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Report:", out / "report.json")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
