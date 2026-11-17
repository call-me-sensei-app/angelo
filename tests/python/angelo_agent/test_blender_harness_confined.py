# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The test harness's grant scope is the harness's alone, never an agent script's (doc/angelo/grant_operators.rst,
"Authority in C"): in an app started with --enable-event-simulate, where the scope exists, a confined script can't
enter it by any of its names, and nothing it reaches grants.

The harness (``grant_guard.stand_in`` / ``harness``, ``qa_person.as_person``) stands in for the person's click in
suites that test a form, not the click (the coordinator's 2.0.0 condition): a shipped app never has it
(``test_blender_guard_authority``: refused without the flag), and this suite pins the other half. The suite's own
unconfined harness call works first (the flag is on); then each way a script could reach it is refused and the
ledger is unchanged.

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec --enable-event-simulate``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-harness-confined-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "connections"))
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
REPORT = {"success": False, "checks": {}}

ATTEMPTS = {   # Each tries to run a grant in the harness's scope from inside an agent script.
    "guard_module": "import _bpy\n_bpy._angelo_guard.harness(_bpy._angelo_guard.set, 'always_allow', 'on', True)\n",
    "stand_in": "from angelo_agent import grant_guard\ngrant_guard.stand_in().trusted_scripts('EXEC_DEFAULT')\n",
    "harness_function": "from angelo_agent import always_allow, grant_guard\n"
                        "grant_guard.harness(always_allow.set_enabled, True)\n",
    "qa_person": "import qa_person\nfrom angelo_agent import always_allow\nqa_person.as_person(always_allow.set_enabled, True)\n",
    "operator": "bpy.ops.angelo.set_approval(mode='AUTO_BOUNDED')\n",
}


def check(name, ok, detail=None):
    REPORT["checks"][name] = bool(ok)
    if not ok and detail is not None:
        REPORT.setdefault("details", {})[name] = str(detail)[:900]


def confined(source, label):
    """``source`` as an agent script runs it (admitted, confined): its refusal, or what it returned."""
    from angelo_agent import script_confinement
    from confinement_attacks_admit import admit
    admit("harness-confined-" + label, source)
    try:
        script_confinement.run_confined(source, {"__name__": "__angelo_script__", "bpy": bpy}, None,
                                        "harness-confined-" + label)
        return {"ran": True}
    except script_confinement.ScriptConfined as error:
        return {"refused": script_confinement.reason_of(error)}
    except Exception as error:   # noqa: BLE001 - a refusal raised as the guard's error is a refusal too.
        return {"error": repr(error)[:900]}


def main():
    from angelo_agent import always_allow, grant_authority, grant_guard
    check("event_simulate_flag_on", bpy.app.use_event_simulate)
    check("unconfined_harness_works", grant_guard.harness(lambda: grant_authority.GUARD.admitted(
        "angelo.external_app_connect")) is True)
    before = (always_allow.enabled(), grant_authority.chat_in_auto("harness-confined"))
    for name, source in ATTEMPTS.items():
        outcome = confined(source, name)
        check("refused_" + name, not outcome.get("ran"), outcome)
    check("nothing_granted", (always_allow.enabled(), grant_authority.chat_in_auto("harness-confined")) == before
          and not always_allow.enabled(), before)


try:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    main()
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
(OUT / "harness-confined.json").write_text(json.dumps(REPORT, indent=2, default=str))
failed = sorted(name for name, ok in REPORT["checks"].items() if not ok)
print(json.dumps({"success": REPORT["success"], "failed": failed, "error": REPORT.get("error"),
                  "details": REPORT.get("details")}, default=str)[:6000])
sys.exit(0 if REPORT["success"] else 1)
