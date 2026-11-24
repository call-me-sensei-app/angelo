# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ripple Delete's Clear Ranges starts off in new scenes; a saved file keeps its own setting, in a real background Blender.

Only for the ruling "change the new-scene default" (the 2.1.0 release plan,
section 4.1, option a). Blender 5.3.0 starts every scene with Clear Ranges on (``seq::tool_settings_init`` in
``sequencer.cc``), so Ripple Delete cuts unselected strips that overlap the deleted range on other channels. The
report contract that holds under either ruling is ``test_blender_ripple_delete_report``; this suite is separate so
the default can be dropped without touching it.

* A scene made now (``bpy.data.scenes.new``) and the startup file's scene start with Clear Ranges off. All Channels
  and Markers keep Blender's defaults (on): only the option that deletes footage changes.
* Ripple Delete's own ``clear_ranges`` property defaults off too, so a script or the agent that runs the operator
  without naming it gets the same safe behaviour as the person.
* A file keeps what it was saved with, on or off: the default never rewrites a person's saved choice (no versioning
  for files at or above 503.26, which already carry their own setting).

Run through ``run_native_suites.py --include test_blender_ripple_delete_default``; check
``ripple-delete-default.json`` for ``success``. Expected to fail on 2.0.0 (06259fa5bb0) on the two new-scene checks and the operator default.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-ripple-delete-default-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "ripple-delete-default.json"
bpy.context.preferences.use_preferences_save = False

state = {"success": False, "checks": {}, "values": {}}


def check(name, value):
    state["checks"][name] = bool(value)


def ripple(scene):
    settings = scene.tool_settings.sequencer_tool_settings
    return {"clear_ranges": settings.ripple_clear_ranges, "all_channels": settings.ripple_all_channels,
            "markers": settings.ripple_markers}


def saved_and_reloaded(clear_ranges):
    """Save a scene with Clear Ranges set as given, open the file again, and read the setting back."""
    scene = bpy.data.scenes.new("saved_%s" % clear_ranges)
    scene.tool_settings.sequencer_tool_settings.ripple_clear_ranges = clear_ranges
    path = OUT / ("ripple-%s.blend" % ("on" if clear_ranges else "off"))
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
    return ripple(bpy.data.scenes["saved_%s" % clear_ranges])


def main():
    try:
        startup = ripple(bpy.data.scenes[0])
        made = ripple(bpy.data.scenes.new("made_now"))
        state["values"].update(startup=startup, made_now=made)
        for name, values in (("startup", startup), ("new_scene", made)):
            check("%s_clear_ranges_off" % name, values["clear_ranges"] is False)
            check("%s_keeps_all_channels_and_markers" % name, values["all_channels"] and values["markers"])
        operator_default = bpy.ops.sequencer.ripple_delete.get_rna_type().properties["clear_ranges"].default
        state["values"]["operator_default"] = operator_default
        check("operator_clear_ranges_default_off", operator_default is False)
        for wanted in (True, False):
            values = saved_and_reloaded(wanted)
            state["values"]["saved_%s" % wanted] = values
            check("saved_%s_kept" % ("on" if wanted else "off"), values["clear_ranges"] is wanted)
        state["success"] = len(state["checks"]) == 7 and all(state["checks"].values())
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_RIPPLE_DELETE_DEFAULT", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v)}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
