# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A folder the person typed for one writer is that writer's only (piece 10's re-read of f9f24cc6ffd, MUST-C;
doc/angelo/script_confinement.rst, "Writers that read their path from data").

Typed records come only from the interface (a person's own edit of a path field), so this suite types as a person
does: in a real window, it types a folder of the person's into a File Output node's folder field in the Compositing
tab (a click into the field, then its keys and Return). Then an agent script points the render output into that
folder and the person renders: the render's write is judged (the script set its output) and the typed folder counts
for the node's folder only, so nothing of the person's is written over. The control: after a script tags the node (its file name), the
person's render still writes the node's file into the folder they typed for it.

Synthetic events in a window; Cycles on the CPU; no provider, account or network. Run through
``run_native_suites.py --gui --include test_blender_writers_typed_gui``; ``writers-typed.json`` has ``success`` and
``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import gc
import json
import tempfile
import time
import traceback
import uuid
from pathlib import Path

import addon_utils
import bpy

assert not bpy.app.background, "The typed-folder suite needs a window"
import qa_boot  # noqa: E402
import qa_waits  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-writers-typed-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "connections"))
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
REPORT = {"success": False, "phase": "boot", "checks": {}, "details": {}}
STARTED = time.monotonic()
LIMIT = 240.0 * qa_waits.timeout_scale()
TEXT = b"the person's file"
PLACEHOLDER = "writers_typed_" + uuid.uuid4().hex[:8]
WAITING = {}
TRIES = 4   # Clicks into the field at most (``tick``, phase "typed").
STATE = {}
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False


def check(name, condition, detail=None):
    REPORT["checks"][name] = bool(condition)
    if not condition and detail is not None:
        REPORT["details"][name] = str(detail)[:1500]


def after(label, seconds):
    return time.monotonic() - WAITING.setdefault(label, time.monotonic()) >= seconds


def run(source, **names):
    """``source`` as an agent script, through the executor's confinement."""
    from angelo_agent import script_confinement, script_errors
    from confinement_attacks_admit import admit
    namespace = {"__name__": "__angelo_script__", "bpy": bpy, **names}
    admit("escape", source)
    outcome = {"refused": None}
    try:
        _, result = script_confinement.run_confined(
            source, namespace, None, "escape", finish=lambda ns: script_errors.read_back(ns.get("result")))
        outcome["result"] = json.dumps(result, default=str)[:200]
    except script_confinement.ScriptConfined as error:
        outcome["refused"] = script_confinement.reason_of(error)
    except Exception as error:   # noqa: BLE001 - the check's detail.
        outcome["error"] = repr(error)[:300]
    gc.collect()
    return outcome


def setup():
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    document = OUT / "writers-typed-document" / "project.blend"
    document.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(document))   # The person's project (its folder anchors nothing of the person's).
    person = Path(tempfile.mkdtemp(prefix="writers-typed-person-", dir=str(OUT)))
    (person / "taxes_0001.png").write_bytes(TEXT)
    STATE["person"] = str(person)
    scene = bpy.context.scene
    render, cycles = scene.render, scene.cycles
    render.engine, render.resolution_percentage = "CYCLES", 1
    render.image_settings.file_format, render.use_file_extension = "PNG", True
    cycles.device, cycles.samples = "CPU", 1
    scene.frame_start, scene.frame_end = 1, 1
    tree = bpy.data.node_groups.new("WritersTypedTree", "CompositorNodeTree")
    scene.compositing_node_group = tree
    render.use_compositing = True
    render.compositor_device = "CPU"   # Never a GPU path for the composite.
    layers = tree.nodes.new("CompositorNodeRLayers")
    tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    group_output = tree.nodes.new("NodeGroupOutput")
    tree.links.new(layers.outputs["Image"], group_output.inputs[0])
    output = tree.nodes.new("CompositorNodeOutputFile")
    output.name, output.location = "WritersTypedOutput", (300, 0)
    output.directory, output.file_name = PLACEHOLDER, "pass_"   # Set from Python: no typed value.
    output.file_output_items.new("RGBA", "Beauty")
    tree.links.new(layers.outputs["Image"], output.inputs["Beauty"])
    window = bpy.context.window_manager.windows[0]
    window.workspace = bpy.data.workspaces["Compositing"]   # The person opens the Compositing tab.
    STATE["window"], STATE["tree"], STATE["output"] = window, tree, output


def show():
    """The Compositing tab's node editor, showing the tree whole, once the tab is open; else None."""
    window = STATE["window"]
    if window.workspace.name != "Compositing":
        return None
    area = next((a for a in window.screen.areas if a.type == "NODE_EDITOR"), None)
    if area is None:
        return None
    area.ui_type = "CompositorNodeTree"
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.node.view_all()
    return region


def node_buttons():
    """The drawn buttons of the File Output node's block (``_angelo_jobs.region_buttons``; a node's block is
    ``node_<name>``)."""
    from _bpy import _angelo_jobs
    window = STATE["window"]
    region = STATE["region"]
    return [(text, x, y, w, h) for pointer, block, text, _idname, x, y, w, h in _angelo_jobs.region_buttons()
            if pointer == window.as_pointer() and block == "node_" + STATE["output"].name
            and region.x <= x and x + w <= region.x + region.width
            and region.y <= y and y + h <= region.y + region.height]


def field():
    """``(x, y, w, h)`` of the node's folder field: its fields have no label (``node_draw_buttons``), the folder is
    the first, the topmost of the full-width unlabelled buttons. The typed value checks it was that field."""
    fields = [(x, y, w, h) for text, x, y, w, h in node_buttons() if not text and w > 60 and h > 8]
    return max(fields, key=lambda rect: rect[1] + rect[3], default=None)


def render_frames():
    try:
        bpy.ops.render.render(animation=True)
        return "ran"
    except RuntimeError as error:
        return repr(error)[:200]


def tick():
    try:
        assert time.monotonic() - STARTED < LIMIT, "Timed out in " + REPORT["phase"]
        phase = REPORT["phase"]
        window = STATE.get("window")
        if phase == "boot":
            if not after("boot", 3.0):
                return 0.2
            setup()
            REPORT["phase"] = "show"
        elif phase == "show":
            STATE["region"] = show()
            if STATE["region"] is None:
                if not after("show", 10.0):
                    return 0.2
                raise AssertionError("the Compositing tab did not open")
            REPORT["phase"] = "find"
        elif phase == "find":
            # The field where it rests: View All moves the view smoothly, so a place read during the move is gone
            # by the click. Two reads half a second apart must agree.
            rect, seen = field(), STATE.get("seen")
            STATE["seen"] = rect
            if rect is None or rect != seen:
                if not after("find", 10.0):
                    return 0.5
                raise AssertionError("no resting folder field in the node's block: %r" % (node_buttons(),))
            x, y, w, h = rect
            STATE["at"] = (int(x + w / 2), int(y + h / 2))
            window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=STATE["at"][0] - 1, y=STATE["at"][1])
            REPORT["phase"] = "onto"
        elif phase == "onto":
            if not after("onto", 0.6):
                return 0.1
            x, y = STATE["at"]
            window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
            window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)   # The person clicks into the field
            window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)   # (its text all selected),
            REPORT["phase"] = "edit"
        elif phase == "edit":
            if not after("edit", 0.6):
                return 0.1
            x, y = STATE["at"]
            for char in STATE["person"]:   # types their folder over it
                window.event_simulate(type='A', value='PRESS', x=x, y=y, unicode=char)
            window.event_simulate(type='RET', value='PRESS', x=x, y=y)   # and confirms.
            window.event_simulate(type='RET', value='RELEASE', x=x, y=y)
            REPORT["phase"] = "typed"
        elif phase == "typed":
            if not after("typed", 0.6):
                return 0.1
            output = STATE["output"]
            typed = os.path.normpath(bpy.path.abspath(output.directory)) == os.path.normpath(STATE["person"])
            STATE["tries"] = STATE.get("tries", 1)
            if not typed and STATE["tries"] < TRIES:
                # The window's first synthetic click into the field started no edit on Xvfb (every run there; the
                # second click did): the person clicks and types again, after Escape ends any edit.
                STATE["tries"] += 1
                x, y = STATE["at"]
                window.event_simulate(type='ESC', value='PRESS', x=x, y=y)
                window.event_simulate(type='ESC', value='RELEASE', x=x, y=y)
                for label in ("find", "onto", "edit", "typed"):
                    WAITING.pop(label, None)
                STATE.pop("seen", None)
                REPORT["phase"] = "find"
                return 0.5
            REPORT["details"]["tries"] = STATE["tries"]
            check("the_person_typed_the_nodes_folder", typed, (output.directory, STATE["at"], node_buttons()))
            # A script points the render output into the folder the person typed for the node, then the person
            # renders: the render's write is judged (a script set its output) and the typed folder is the node's.
            STATE["set"] = run("bpy.data.scenes[NAME].render.filepath = P\n", NAME=bpy.context.scene.name,
                               P=os.path.join(STATE["person"], "taxes_"))
            STATE["render"] = render_frames()
            kept = (Path(STATE["person"]) / "taxes_0001.png").read_bytes() == TEXT
            check("a_folder_typed_for_one_writer_is_not_anothers", kept,
                  (STATE["set"], STATE["render"], sorted(os.listdir(STATE["person"]))))
            # The control: a script tags the node (its file name); the person's render still writes the node's file
            # into the folder they typed for that very node.
            STATE["named"] = run("bpy.data.node_groups['WritersTypedTree'].nodes['WritersTypedOutput'].file_name = "
                                 "'scripts_'\n")
            STATE["again"] = render_frames()
            written = sorted(name for name in os.listdir(STATE["person"]) if name.startswith("scripts_"))
            check("the_typed_folder_keeps_its_own_writer_writing", len(written) == 1,
                  (STATE["named"], STATE["again"], sorted(os.listdir(STATE["person"]))))
            return finish()
        return 0.1
    except Exception:   # noqa: BLE001 - the report says what failed.
        return finish(traceback.format_exc())


def finish(error=None):
    REPORT.update(success=error is None and bool(REPORT["checks"]) and all(REPORT["checks"].values()),
                  phase="complete" if error is None else "failed", elapsed_seconds=round(time.monotonic() - STARTED, 2))
    if error:
        REPORT["error"] = error
    (OUT / "writers-typed.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_WRITERS_TYPED", json.dumps({"success": REPORT["success"], "phase": REPORT["phase"],
                                              "checks": REPORT["checks"]}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(qa_boot.gated(tick), first_interval=1.0)   # After Angelo's first tab build.
