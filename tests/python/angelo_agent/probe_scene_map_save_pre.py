# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual probe: does a checkpoint's scene map describe the scenes its file holds when a save_pre handler renames them?

A ``save_pre`` handler swaps two scenes' names during the capture. The map must name each scene as the captured file
does, so a chat bound to the first scene is rebound to it after reading the file. Run with the build's own add-on in a
disposable app copy and an isolated home::

    <app>/Contents/MacOS/Angelo --background --factory-startup --disable-autoexec --python-exit-code 1 \
        --python probe_scene_map_save_pre.py -- /absolute/disposable/folder

One ``SAVE-PRE-PROBE {json}`` line; a non-zero exit on a failed assertion. Not collected by any suite.
"""

import json
import os
import sys
from pathlib import Path

import addon_utils
import bpy

assert bpy.app.background and "--factory-startup" in sys.argv, "Use a disposable headless factory-startup instance"
ARGS = sys.argv[sys.argv.index("--") + 1:]
ROOT = Path(ARGS[0]).resolve()
LATE = "late" in ARGS[1:] or "reverse" in ARGS[1:]   # The swap is registered by a save_pre handler during dispatch.
REVERSE = "reverse" in ARGS[1:]   # That handler also puts a save_post callback first that swaps the names back.
ROOT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_STATE_DIR"] = str(ROOT / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = str(ROOT / "accounts")
from angelo_agent import ui
from angelo_agent import checkpoint_scene_map as scene_map
from angelo_agent.controller_recovery import restored_document

ui._authentication.start = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
controller = ui.get_controller()
report = {"addon": ui.__file__}
try:
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "project.blend"))
    first = bpy.context.scene
    first.name = "Atelier"
    first["probe_identity"] = "first"
    second = bpy.data.scenes.new("Harbor")
    second["probe_identity"] = "second"
    before = (ui.document_key()[0], first.session_uid)

    def swap_names(*_):
        first.name = "Temporary probe name"
        second.name = "Atelier"
        first.name = "Harbor"

    ran = []

    def late_swap(*arguments):
        ran.append(True)
        swap_names(*arguments)

    def swap_back(*_):
        second.name = "Temporary probe name"
        first.name = "Atelier"
        second.name = "Harbor"

    def register_late_swap(*_):
        if late_swap not in bpy.app.handlers.save_pre:
            bpy.app.handlers.save_pre.append(late_swap)
            if REVERSE:
                bpy.app.handlers.save_post.insert(0, swap_back)
    bpy.app.handlers.save_pre.append(register_late_swap if LATE else swap_names)
    try:
        try:
            point = ui.capture_checkpoint(controller)
        except Exception as error:   # A native refusal of the handler's effect is a finding, reported as such.
            report["capture_refused"] = repr(error)
            raise
    finally:
        for handler in (register_late_swap, late_swap, swap_names, swap_back):
            for handlers in (bpy.app.handlers.save_pre, bpy.app.handlers.save_post):
                if handler in handlers:
                    handlers.remove(handler)
    report["late"], report["late_swap_ran_in_that_save"] = LATE, bool(ran)
    captured = scene_map.read(controller.checkpoints, point["id"])
    report["captured_map"] = captured
    report["names_after_capture"] = {scene["probe_identity"]: scene.name_full for scene in (first, second)}
    recorded = (first.name_full, before)
    _, recovery = controller.checkpoints.recovery_copy(point["id"])
    assert bpy.ops.wm.open_mainfile(filepath=str(recovery), load_ui=False, use_scripts=False) == {"FINISHED"}
    report["names_in_file"] = {scene.get("probe_identity"): scene.name_full for scene in bpy.data.scenes}
    key = restored_document(recorded, ui.document_key(), bpy.data.scenes, chat_id="handler-probe", captured=captured)
    bound = next((scene for scene in bpy.data.scenes if scene.session_uid == key[1]), None)
    report["bound"] = bound.get("probe_identity") if bound is not None else None
    # An add-on's save handler (this probe's) keeps no map: no map can disagree with the file. Restore then rebinds by
    # the name read before the load; with names swapped back after the write (REVERSE) that is 1.0.5's limit, recorded.
    assert captured is None, "A map was kept although an add-on save handler ran"
    if not REVERSE:
        assert report["bound"] == "first", "Rebinding by name selected another scene"
    report["passed"] = True
finally:
    print("SAVE-PRE-PROBE " + json.dumps(report, default=str), flush=True)
