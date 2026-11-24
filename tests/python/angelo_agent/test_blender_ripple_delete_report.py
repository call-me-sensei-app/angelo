# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ripple Delete never removes footage the person did not select without saying so, in a real background Blender.

Blender 5.3's Ripple Delete (``SEQUENCER_OT_ripple_delete``, ``sequencer_edit.cc``) with Clear Ranges on deletes the
contents of each deleted strip's time range on every rippled channel before closing the gap: an unselected strip there
is removed, trimmed or split (``ripple_range_clear``). 5.3.0 turns Clear Ranges on by default, with All Channels
(``seq::tool_settings_init``; versioning 503.26 sets the same on older files), so deleting a clip on one channel cut
a clip on another channel that overlapped it in time, and nothing said so (Blender 5.3 launch thread, 2026-11-10,
recorded in the Blender 5.3 launch feedback notes of 2026-11-14, item 9).

The contract this suite holds, whatever the default turns out to be (the 2.1.0 release plan,
section 4.1):

* When Ripple Delete removes, trims or splits a strip that was not selected, it says so in a WARNING report that names
  every such strip. A warning, so it prints even under ``--quiet`` and shows in the status bar.
* A strip that only moves to close the gap is not lost footage and is not named; a delete that loses nothing
  unselected reports nothing (no noise on the everyday case).
* With Clear Ranges off, nothing unselected loses a frame (Blender 5.3's behaviour, kept).
* The person's path (the operator's properties read from the scene's tool settings, as its invoke does) on a new
  scene either leaves every unselected strip whole or names each one it cut. This holds under either default.
* Ripple Trim (``SEQUENCER_OT_ripple_trim``) clears the trimmed range with the same Clear Ranges code, so it holds the
  same contract: an unselected strip it cuts is named, one it only moves is not. Found when the Ripple Delete fix
  (ac3d1b154ff) changed ``ripple_range_clear`` and left Ripple Trim's call to it unbuilt (2026-11-15).

Strips are colour strips, so no media file is read. The operator runs through ``bpy.ops`` with the scene given as
``sequencer_scene``; its reports reach ``sys.stdout`` through ``BPy_reports_write_stdout``. Run through
``run_native_suites.py --include test_blender_ripple_delete_report``; check ``ripple-delete-report.json`` for
``success``. Fails on 2.0.0 (06259fa5bb0): the cut strips are removed, trimmed or split with no report.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import contextlib
import io
import json
import tempfile
import traceback
from pathlib import Path

import bpy  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-ripple-delete-report-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "ripple-delete-report.json"
bpy.context.preferences.use_preferences_save = False

state = {"success": False, "checks": {}, "cases": {}}


def check(name, value, detail=None):
    state["checks"][name] = bool(value)
    if detail is not None:
        state["cases"].setdefault(name, detail)
    return bool(value)


def fresh_scene(name):
    scene = bpy.data.scenes.new(name)
    scene.sequence_editor_create()
    return scene


def add(scene, name, channel, start, end, selected=False):
    """A colour strip covering frames [start, end) on ``channel``."""
    strip = scene.sequence_editor.strips.new_effect(
        name=name, type="COLOR", channel=channel, frame_start=start, length=end - start)
    strip.select = selected
    return strip


def footage(scene):
    """Every strip as (channel, first frame, end frame), keyed by name, after the edit."""
    return {s.name: (s.channel, s.frame_final_start, s.frame_final_end) for s in scene.sequence_editor.strips_all}


def ripple_delete(scene, operator="ripple_delete", **properties):
    """Run Ripple Delete (or ``operator``, e.g. Ripple Trim) on the scene's selected strips; return (result, the
    WARNING lines it printed)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), bpy.context.temp_override(scene=scene, sequencer_scene=scene):
        result = getattr(bpy.ops.sequencer, operator)(**properties)
    lines = buffer.getvalue().splitlines()
    return result, [line for line in lines if line.startswith("Warning:")]


def kept(after, name):
    """Frames a strip still covers, its split pieces (``name.001``...) included."""
    return sum(end - start for key, (_, start, end) in after.items() if key == name or key.startswith(name + "."))


def named(warnings, name):
    return any(name in line for line in warnings)


CLEAR = dict(all_channels=True, markers=True, clear_ranges=True)


def case_removed():
    """A strip on another channel wholly inside the deleted range is removed: the warning names it."""
    scene = fresh_scene("removed")
    add(scene, "Cut", 1, 10, 20, selected=True)
    add(scene, "Inside", 2, 12, 18)
    result, warnings = ripple_delete(scene, **CLEAR)
    after = footage(scene)
    lost = kept(after, "Inside") < 6
    check("removed_strip_is_named", result == {"FINISHED"} and (not lost or named(warnings, "Inside")),
          {"after": after, "warnings": warnings})


def case_trimmed():
    """A strip on another channel that overlaps the range's start loses the overlapped frames: named."""
    scene = fresh_scene("trimmed")
    add(scene, "Cut", 1, 10, 20, selected=True)
    add(scene, "Tail", 2, 5, 15)
    result, warnings = ripple_delete(scene, **CLEAR)
    after = footage(scene)
    lost = kept(after, "Tail") < 10
    check("trimmed_strip_is_named", result == {"FINISHED"} and (not lost or named(warnings, "Tail")),
          {"after": after, "warnings": warnings})


def case_split():
    """A strip on another channel spanning the whole range is split around it: the original is named."""
    scene = fresh_scene("split")
    add(scene, "Cut", 1, 10, 20, selected=True)
    add(scene, "Span", 2, 0, 40)
    result, warnings = ripple_delete(scene, **CLEAR)
    after = footage(scene)
    lost = kept(after, "Span") < 40
    check("split_strip_is_named", result == {"FINISHED"} and (not lost or named(warnings, "Span")),
          {"after": after, "warnings": warnings})


def case_two_ranges():
    """Two deleted ranges: the strip cut inside the second is named; one that only moves left is not."""
    scene = fresh_scene("two_ranges")
    add(scene, "CutA", 1, 10, 20, selected=True)
    add(scene, "CutB", 1, 50, 60, selected=True)
    add(scene, "InB", 3, 55, 58)
    add(scene, "Mover", 3, 30, 40)
    result, warnings = ripple_delete(scene, **CLEAR)
    after = footage(scene)
    lost = kept(after, "InB") < 3
    mover = after.get("Mover")
    check("second_range_cut_is_named", result == {"FINISHED"} and (not lost or named(warnings, "InB")),
          {"after": after, "warnings": warnings})
    check("moved_strip_is_not_named", mover is not None and mover[2] - mover[1] == 10 and not named(warnings, "Mover"),
          {"after": after, "warnings": warnings})


def case_nothing_lost():
    """A delete that cuts nothing unselected reports nothing."""
    scene = fresh_scene("nothing_lost")
    add(scene, "Cut", 1, 10, 20, selected=True)
    add(scene, "Later", 2, 30, 40)
    result, warnings = ripple_delete(scene, **CLEAR)
    after = footage(scene)
    check("no_warning_when_nothing_lost", result == {"FINISHED"} and not warnings and after.get("Later") == (2, 20, 30),
          {"after": after, "warnings": warnings})


def case_clear_ranges_off():
    """Clear Ranges off: an overlapping strip on another channel keeps every frame and nothing is reported."""
    scene = fresh_scene("clear_off")
    add(scene, "Cut", 1, 10, 20, selected=True)
    add(scene, "Inside", 2, 12, 18)
    add(scene, "Tail", 3, 5, 15)
    result, warnings = ripple_delete(scene, all_channels=True, markers=True, clear_ranges=False)
    after = footage(scene)
    whole = kept(after, "Inside") == 6 and kept(after, "Tail") == 10
    check("clear_ranges_off_keeps_footage", result == {"FINISHED"} and whole and not warnings,
          {"after": after, "warnings": warnings})


def case_person_path():
    """The person's path on a new scene: the tool settings decide; nothing unselected is cut without a warning."""
    scene = fresh_scene("person_path")
    settings = scene.tool_settings.sequencer_tool_settings
    flags = dict(all_channels=settings.ripple_all_channels, markers=settings.ripple_markers,
                 clear_ranges=settings.ripple_clear_ranges)
    add(scene, "Cut", 1, 10, 20, selected=True)
    add(scene, "Inside", 2, 12, 18)
    add(scene, "Tail", 3, 5, 15)
    add(scene, "Span", 4, 0, 40)
    result, warnings = ripple_delete(scene, **flags)
    after = footage(scene)
    before = {"Inside": 6, "Tail": 10, "Span": 40}
    silent = []
    for name, length in before.items():
        if kept(after, name) < length and not named(warnings, name):
            silent.append(name)
    check("person_path_never_cuts_silently", result == {"FINISHED"} and not silent,
          {"flags": flags, "after": after, "warnings": warnings, "silently_cut": silent})


def case_ripple_trim():
    """Ripple Trim to the playhead clears the trimmed range on every channel: the cut strip is named, the one that
    only moves is not."""
    scene = fresh_scene("ripple_trim")
    scene.frame_current = 20
    trimmed = add(scene, "Trimmed", 1, 10, 30, selected=True)
    scene.sequence_editor.active_strip = trimmed
    add(scene, "Inside", 2, 12, 18)
    add(scene, "Later", 3, 40, 50)
    result, warnings = ripple_delete(scene, operator="ripple_trim", side='LEFT', **CLEAR)
    after = footage(scene)
    lost = kept(after, "Inside") < 6
    later = after.get("Later")
    check("ripple_trim_cut_is_named", result == {"FINISHED"} and lost and named(warnings, "Inside"),
          {"after": after, "warnings": warnings})
    check("ripple_trim_moved_strip_is_not_named",
          later is not None and later[2] - later[1] == 10 and not named(warnings, "Later"),
          {"after": after, "warnings": warnings})


def main():
    try:
        for case in (case_removed, case_trimmed, case_split, case_two_ranges, case_nothing_lost,
                     case_clear_ranges_off, case_person_path, case_ripple_trim):
            case()
        # Eight cases, ten checks; a run that checked less proves nothing.
        state["success"] = len(state["checks"]) == 10 and all(state["checks"].values())
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_RIPPLE_DELETE_REPORT", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v)}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
