# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Below the bar, parallel edit does not exist: a background Angelo, unforced, is a machine that fails the check.

The maintainer's rule (2026-10-24): parallel edit is for a capable machine, and below the bar nothing of it runs, so
nothing of it can crash there (build4, a VM without a display adapter, crashed only in suites that forced it on).
Checked here with ``ANGELO_ENABLE_PARALLEL_AGENTS`` unset:

* the machine check fails (no GPU in a background Angelo) and the feature is off;
* no ``angelo_workers_*`` tool is in the tool list, so no request declares one;
* no rail, live view, input hold or timer is registered, no live part or picture exists, and no GPU object is made
  (nothing here calls into ``gpu``);
* Settings says what the machine needs, in one line.

Runs everywhere, a GPU-less VM included.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

os.environ.pop("ANGELO_ENABLE_PARALLEL_AGENTS", None)
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-parallel-below-bar"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
report = {"checks": {}}


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault("details", {})[name] = detail


def run():
    import addon_utils
    loaded_gpu = "gpu" in sys.modules
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    from angelo_agent import (live_view, live_view_draw, parallel_access, parallel_capability, parallel_sessions,
                              providers_page_model, rail_pictures, session_rail_input, worker_spec)
    from angelo_agent.tool_specs import TOOL_MAP
    machine = parallel_capability.current()
    check("a_background_angelo_is_below_the_bar", not machine.capable and "gpu" in machine.missing, str(machine))
    check("the_feature_is_off", not parallel_access.enabled() and parallel_access.worker_limit() == 0)
    check("no_worker_tool_is_declared", not set(TOOL_MAP) & worker_spec.FAMILY_NAMES)
    check("no_rail_or_live_view_is_registered", not session_rail_input._registered[0]
          and not bpy.app.timers.is_registered(session_rail_input._watch) and not session_rail_input._handlers)
    check("nothing_gpu_related_was_made", not live_view.sessions() and not live_view_draw._offscreens
          and not rail_pictures._offscreens and not parallel_sessions.sessions(), loaded_gpu)
    section = providers_page_model._parallel_edit(None)   # Below the bar no choice is drawn, prefs or not.
    line = section["rows"][0]["name"]
    check("settings_say_what_it_needs", line.startswith("Parallel edit needs ") and "GPU" in line, line)
    addon_utils.disable("angelo_agent", default_set=False)


try:
    run()
except Exception:
    report["error"] = traceback.format_exc()
report["success"] = "error" not in report and all(report["checks"].values())
(OUT / "parallel-below-bar.json").write_text(json.dumps(report, indent=2))
print("ANGELO_PARALLEL_BELOW_BAR", json.dumps({"success": report["success"]}), flush=True)
sys.exit(0 if report["success"] else 1)
