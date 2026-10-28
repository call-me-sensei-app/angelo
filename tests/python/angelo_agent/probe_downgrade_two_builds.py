# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual two-build downgrade probe: does a RELEASED Angelo build list and Restore checkpoints that THIS build wrote?

Unlike ``probe_downgrade_checkpoint_formats.py`` (one build, a map folder added by hand), the producer and the
consumer are different builds and the consumer sees the producer's profile unchanged. Run the same file twice, each
in a disposable app copy with its own bundled add-on (never a source tree on ``sys.path``) and its own isolated home::

    <newer app>/Contents/MacOS/Angelo --background --factory-startup --disable-autoexec --python-exit-code 1 \
        --python probe_downgrade_two_builds.py -- produce /absolute/disposable/folder
    <released app>/Contents/MacOS/Angelo ... --python probe_downgrade_two_builds.py -- consume /absolute/disposable/folder

``produce`` saves a project, captures an ordinary checkpoint (record version 3) and then one with a native runtime
owner (a Multires object in Sculpt Mode: record version 4) through the build's own ``ui.capture_checkpoint``, and
asserts both versions and both scene maps: a run that cannot make a version-4 capture fails rather than passing on
ordinary checkpoints alone. ``consume`` asserts that the released build's inventory has no issue and that a Restore
of each checkpoint FINISHES and removes an edit made after it. Each prints one ``TWO-BUILD-PROBE {json}`` line and
exits non-zero on a failed assertion (``--python-exit-code 1``). Not collected by any suite. The recorded results are
in ``doc/angelo/checkpoint_policy.rst`` (Downgrade).
"""

import json
import os
import sys
from pathlib import Path

import addon_utils
import bpy

assert bpy.app.background and "--factory-startup" in sys.argv, "Use a disposable headless factory-startup instance"
MODE, SUPPLIED = sys.argv[sys.argv.index("--") + 1:][:2]
ROOT = Path(SUPPLIED).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_STATE_DIR"] = str(ROOT / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = str(ROOT / "accounts")
from angelo_agent import ui   # The app's bundled add-on.
from angelo_agent.checkpoints import CheckpointStore

ui._authentication.start = lambda *args, **kwargs: None   # No sign-in.
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
controller = ui.get_controller()
report = {"mode": MODE, "binary": bpy.app.binary_path, "addon": ui.__file__}


def emit():
    print("TWO-BUILD-PROBE " + json.dumps(report, default=str), flush=True)


try:
    if MODE == "produce":
        bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "project.blend"))
        ordinary = ui.capture_checkpoint(controller)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
        target = bpy.context.object
        modifier = target.modifiers.new("Probe Multires", "MULTIRES")
        assert bpy.ops.object.multires_subdivide(modifier=modifier.name, mode="CATMULL_CLARK") == {"FINISHED"}
        modifier.levels = modifier.sculpt_levels = 1
        assert bpy.ops.object.mode_set(mode="SCULPT") == {"FINISHED"}
        bpy.context.view_layer.update()
        sculpt = ui.capture_checkpoint(controller)
        report.update(ordinary_version=ordinary.get("version"), sculpt_version=sculpt.get("version"),
                      session=controller.checkpoints.session_id, ordinary=ordinary["id"], sculpt=sculpt["id"])
        report["maps"] = {kind: (ROOT / "state" / "checkpoint-scenes" / report["session"]
                                 / (report[kind] + ".json")).is_file() for kind in ("ordinary", "sculpt")}
        (ROOT / "probe.json").write_text(json.dumps(report), encoding="utf-8")
        assert ordinary.get("version") == 3, "The ordinary capture is not version 3"
        assert sculpt.get("version") == 4, "No runtime-owner capture: the probe is invalid"
        assert all(report["maps"].values()), "A scene map is missing"
    elif MODE == "consume":
        produced = json.loads((ROOT / "probe.json").read_text(encoding="utf-8"))
        controller.checkpoints = CheckpointStore(ROOT / "state", produced["session"])
        inventory = controller.checkpoints.inspect()
        report["inventory_issues"] = [issue.get("error") for issue in inventory["issues"]]
        report["listed"] = sorted(item["id"] for item in inventory["checkpoints"])
        outcomes = []
        for kind in ("ordinary", "sculpt"):
            identity = produced[kind]
            controller.state["messages"].append({"role": "user", "text": kind, "checkpoint": identity,
                                                 "checkpoint_scope": "before_request"})
            if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.mesh.primitive_cube_add()
            bpy.context.object.name = "AfterCheckpoint"
            try:
                outcome = sorted(bpy.ops.angelo.restore_checkpoint(checkpoint_id=identity))
            except RuntimeError as error:   # A background operator's ERROR report raises.
                outcome = ["RAISED", str(error)]
            outcomes.append([kind, outcome, "AfterCheckpoint" not in bpy.data.objects])
        report["restores"] = outcomes
        assert not inventory["issues"], "Downgrade inventory failed"
        assert {produced["ordinary"], produced["sculpt"]} <= set(report["listed"]), "A checkpoint is not listed"
        assert all(outcome == ["FINISHED"] and gone for _, outcome, gone in outcomes), "A checkpoint did not restore"
    else:
        raise AssertionError(MODE)
    report["passed"] = True
finally:
    emit()
