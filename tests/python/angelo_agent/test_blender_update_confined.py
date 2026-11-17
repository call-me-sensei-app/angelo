# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The self-update's files against a confined script, in the real app with the real guard (piece 10, p10c-1-update).

* An offer Angelo holds, changed in place by a script so its version names Angelo's own state folder: withdrawing
  it (the person's "not this version") removes nothing outside the updates folder. ``may_write`` alone allows
  Angelo's private folders, so the version name is what holds the removal (``update_flow.Preparer.work``).
* The updates folder (``<cache>/updates``) is Angelo's: a script writes nothing there. What is staged there is what
  the installer runs (its helper script, the Windows installer), so a script that could replace it would run
  outside its confinement at Restart to Update.
* Ordinary update paths still pass: Angelo's own write in a version's folder (``may_write``).

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec``. No network.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import shutil
import tempfile
import time
import traceback
import types
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-update-confined-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "connections"))
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
REPORT = {"success": False, "checks": {}}
OFFER = types.SimpleNamespace(release=types.SimpleNamespace(version="9.9.8"), later=None)   # Held before any script.


def check(name, ok, detail=None):
    REPORT["checks"][name] = bool(ok)
    REPORT.setdefault("details", {})[name] = str(detail)[:600]


def run(source, **names):
    from angelo_agent import script_confinement, script_errors
    from confinement_attacks_admit import admit
    namespace = {"__name__": "__angelo_script__", "bpy": bpy, **names}
    admit("update", source)
    try:
        _, result = script_confinement.run_confined(
            source, namespace, None, "update", finish=lambda ns: script_errors.read_back(ns.get("result")))
        return {"refused": None, "result": result}
    except script_confinement.ScriptConfined as error:
        return {"refused": script_confinement.reason_of(error)}
    except Exception as error:   # noqa: BLE001 - the detail says what broke.
        return {"refused": None, "error": repr(error)[:300]}


def main():
    from angelo_agent import owned_paths, ui, update_ready
    updates = update_ready.directory()
    check("updates_folder_known", updates is not None, updates)
    ours = updates / "9.9.9"
    ours.mkdir(parents=True, exist_ok=True)
    chats = Path(ui.state_directory()) / "chats" / "update-probe"
    chats.mkdir(parents=True, exist_ok=True)
    (chats / "chat.json").write_text("{}")

    # A script writes into the updates folder: what Restart to Update runs is staged there.
    for name in ("install_update_macos.sh", "update.msi"):
        target = ours / name
        target.unlink(missing_ok=True)
        outcome = run("with open(TARGET, 'w') as f:\n    f.write('script')\nresult = 'written'\n", TARGET=str(target))
        check("script_writes_nothing_in_updates_" + name.split(".")[-1],
              outcome["refused"] is not None and not target.exists(), outcome)

    # A script changes an offer Angelo holds, in place, to name Angelo's state folder; the person withdraws it.
    outcome = run("object.__setattr__(OFFER.release, 'version', CHATS)\nresult = OFFER.release.version\n",
                  OFFER=OFFER, CHATS=str(chats))
    changed = OFFER.release.version == str(chats)
    update_ready.withdraw(OFFER)
    deadline = time.monotonic() + 20
    import threading
    while any(t.name == "angelo-update-forget" for t in threading.enumerate()) and time.monotonic() < deadline:
        time.sleep(0.1)
    check("withdrawn_offer_naming_state_removes_nothing", (chats / "chat.json").is_file(),
          {"script": outcome, "changed_in_place": changed})

    # What still passes: Angelo's own write in a version's folder, after scripts ran.
    check("angelo_may_write_in_a_version_folder", owned_paths.may_write(ours / "ready.json"), ours)
    shutil.rmtree(chats, ignore_errors=True)
    shutil.rmtree(ours, ignore_errors=True)


try:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    main()
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
(OUT / "update-confined.json").write_text(json.dumps(REPORT, indent=2, default=str))
print(json.dumps(REPORT, default=str)[:6000])
if REPORT["success"]:
    print("ANGELO_UPDATE_CONFINED_OK")
sys.exit(0 if REPORT["success"] else 1)
