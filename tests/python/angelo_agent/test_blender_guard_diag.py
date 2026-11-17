# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A diagnostic build without the agent-script confinement (CMake ``ANGELO_GUARD_DIAG_NO_INSTALL``) fails closed: it
refuses every agent script, admitted or not, through every runner, with a text that says why (guard piece 7b; the
reviewer's finding on 8fefa49f222: it ran them unconfined). In any other build the confinement is installed and an
admitted script runs. Run in the diagnostic build and in a normal one.

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import traceback

import addon_utils

REPORT = {"success": False}
NAME = "ANGELO_GUARD_DIAG_NO_INSTALL"


def main():
    import _bpy
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    from angelo_agent import script_confinement
    from confinement_attacks_admit import admit
    diagnostic = bool(getattr(_bpy._angelo_guard, "DIAG_NO_INSTALL", 0))
    REPORT["diagnostic"] = diagnostic
    source = "import os\nresult = os.getcwd()\n"
    admit("diag", source)
    try:
        script_confinement.run_confined(source, {"__name__": "__angelo_script__"}, None, "diag")
        REPORT["ran"] = True
    except PermissionError as error:
        REPORT["ran"], REPORT["refusal"] = False, str(error)
    if diagnostic:
        REPORT["success"] = REPORT["ran"] is False and "agent-script confinement" in REPORT.get("refusal", "")
    else:
        REPORT["success"] = REPORT["ran"] is True


try:
    main()
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
print(json.dumps(REPORT, default=str)[:4000])
if REPORT.get("success"):
    print("ANGELO_GUARD_DIAG_OK")
sys.exit(0 if REPORT.get("success") else 1)
