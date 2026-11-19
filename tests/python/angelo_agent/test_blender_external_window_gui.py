# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An app's looks and window picture in a real window, through the host's own path, change nothing of the person's.

``external_visibility``'s read guard, validation, per-app throttle and capture budget (``AgentVisibility``) against
Blender's own window (``VisibilityUi``), with a stand-in conversation: no provider, account or network.

* **Cold, then warm.** A material made fresh for this run (so EEVEE has not compiled it) is looked at in MATERIAL and
  in LIT: the first look answers ``busy`` while the shaders compile; asked again on later ticks, a look draws once
  they are ready (re-review MUST A: a cancelled look keeps compiling).
* **The window**, with the viewport in Solid and in Material Preview: its picture is window-sized and draws nothing,
  every editor (the 3D viewport too, in 2.0.1) flat grey and outlined, each saying why in the layout.
* **Unchanged.** Before and after every call, refused, busy or drawn: every data-block collection's names, the unsaved
  flag, the Undo stack's length, each 3D viewport's shading, the selection and the mode.
* **The exception path.** A look forced to fail while it captures answers ``not_applied`` and leaves everything as
  it was.

Run through ``run_native_suites.py --gui --include test_blender_external_window_gui``; check
``external-window.json`` for ``success`` and ``phase == "complete"``. Contract: doc/angelo/external_mcp.rst, "Looks".
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import random
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import addon_utils
import bpy

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-external-window-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

import rendered_view_fixtures as fx  # noqa: E402
import test_native_view_fixture_boot as fixture_boot  # noqa: E402
from angelo_agent import external_visibility, external_window, live_windows, view_capture  # noqa: E402
from angelo_agent.external_look import AgentLooks  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402

REPORT = OUT / "external-window.json"
CAP_S = 120.0
state = {"phase": "boot", "success": False, "checks": {}, "seconds": {}, "busy": {}, "paid_requests": 0}
controller = fx.conversation(None)
controller.references = ReferenceStore(OUT, "4c0d3a5e-7a52-4c55-9d0e-2b1a3f8e6d10")


def check(name, condition):
    state["checks"][name] = bool(condition)
    assert condition, name


def window():
    return bpy.context.window_manager.windows[0]


class Env(external_visibility.VisibilityUi):
    ui = SimpleNamespace(main_window=window)

    def describe(self):
        return {"blender_version": bpy.app.version_string}


class Host(AgentLooks, external_visibility.AgentVisibility):
    """The host's read path (guard, validation, throttle, budget, pruning) with replies kept here."""

    def __init__(self):
        self.env, self._looks, self._views, self._tasks, self.replies = Env(), {}, {}, {}, []

    def _reply(self, work, controller, code, message, **fields):
        self.replies.append(dict(fields, code=code, message=message))

    def _state(self, controller, label):
        return {}

    def ask(self, action, conversation=True, **payload):
        """``conversation=False``: as before the app's first message (no conversation), as a state read may be."""
        work = SimpleNamespace(client=SimpleNamespace(label="suite-app"))
        self._read_only(work, controller if conversation else None, action, dict(payload, action=action))
        return self.replies[-1]


HOST = Host()


def snapshot():
    layer = window().view_layer
    return {"ids": fx.id_names(), "dirty": bpy.data.is_dirty,
            "undo": len(list(bpy.context.window_manager.undo_stack.steps)),
            "shading": [area.spaces.active.shading.type for area in live_windows.areas(window())
                        if area.type == "VIEW_3D"],
            "selection": sorted(obj.name for obj in layer.objects.selected),
            "active": layer.objects.active and layer.objects.active.name, "mode": bpy.context.mode}


def unchanged(label, before):
    after = snapshot()
    for key in before:
        check("%s_%s_unchanged" % (label, key), after[key] == before[key])


def until_drawn(label, action, **payload):
    """Ask on later ticks until a picture comes: busy (throttle, budget, compiling shaders) is asked again; generator.
    Returns the reply through ``state``."""
    started, busy = time.monotonic(), []
    while True:
        before = snapshot()
        reply = HOST.ask(action, **payload)
        unchanged("%s_%s" % (label, reply["code"]), before)
        if reply["code"] == "ok":
            state["busy"][label] = busy
            state["seconds"][label] = round(time.monotonic() - started, 2)
            state[label] = reply
            return
        check("%s_answered_ok_or_busy" % label, reply["code"] in ("ok", "busy"))
        busy.append(reply["message"][:80])
        check("%s_drawn_in_time" % label, time.monotonic() - started < CAP_S)
        yield


def window_pixels(reply):
    import imbuf
    import numpy
    path = controller.references.attached_image(reply["result"]["views"][0])
    check("window_picture_written", path.is_file() and path.stat().st_size > 1000)
    picture = imbuf.load(str(path))
    try:
        with picture.with_buffer() as view:
            return numpy.array(view, dtype=numpy.uint8)
    finally:
        picture.free()


def check_window(label, reply):
    import numpy
    rows, layout = window_pixels(reply), reply["result"]["layout"]
    check("%s_nothing_drawn" % label, not any(entry["shown"] for entry in layout["editors"]))
    check("%s_every_editor_says_why" % label, all(entry.get("why") for entry in layout["editors"]))
    check("%s_viewports_point_to_the_looks" % label, all(
        "angelo_look" in entry["why"] for entry in layout["editors"] if entry["type"] == "VIEW_3D"))
    check("%s_only_grey_and_outlines" % label, set(numpy.unique(rows[..., :3]).tolist()) <= {
        external_window.GREY, external_window.OUTLINE})
    state["%s_layout" % label] = layout


def cold_material(name):
    material = bpy.data.materials.new("%s %08x" % (name, random.getrandbits(32)))   # Never compiled before this run.
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (random.random(), random.random(), random.random(), 1.0)
    noise = material.node_tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 1.0 + random.random() * 50.0
    material.node_tree.links.new(noise.outputs["Fac"], shader.inputs["Roughness"])
    return material


def scenario():
    check("checkout_modules_loaded", str(ROOT) in external_visibility.__file__)
    cube = bpy.data.objects["Cube"]
    cube.data.materials.clear()
    cube.data.materials.append(cold_material("Cold look"))
    bpy.ops.ed.undo_push(message="Suite setup")   # The person's last edit, before any look.
    yield

    state["phase"] = "cold_material_look"
    yield from until_drawn("material_look", "look", look={"views": ["front", "perspective"], "objects": ["Cube"],
                                                          "shading": "MATERIAL", "resolution": 256})
    state["phase"] = "cold_lit_look"
    yield from until_drawn("lit_look", "look", look={"views": ["front"], "shading": "LIT", "resolution": 256})
    check("a_look_disclosed_scene_changed_false", state["lit_look"]["result"].get("scene_changed") is False)

    state["phase"] = "exception_path"
    # The window's state on real bpy. The stand-in conversation has no chat settings (a real Controller's are read by
    # the host tests), so the state is read as before an app's first message: the window, no chat.
    state_reply = HOST.ask("state", conversation=False)
    check("state_reads_the_real_window", state_reply["code"] == "ok"
          and "unavailable" not in str(state_reply["result"]["window"]["viewport"]))
    before = snapshot()
    with mock.patch.object(view_capture, "_write_views", side_effect=RuntimeError("forced by the suite")):
        for _ in range(int(CAP_S * 10)):
            reply = HOST.ask("look", look={"views": ["front"], "shading": "SOLID"})
            if reply["code"] != "busy":
                break
            yield
    check("a_failed_capture_answers_not_applied", reply["code"] == "not_applied")
    unchanged("exception_path", before)
    yield

    state["phase"] = "window_solid"
    yield from until_drawn("window_solid", "window")
    check_window("solid", state["window_solid"])

    state["phase"] = "window_material_preview"
    cube.data.materials.clear()
    cube.data.materials.append(cold_material("Cold window"))   # The person's edit: a fresh material, never compiled.
    viewport = next(area for area in live_windows.areas(window()) if area.type == "VIEW_3D")
    viewport.spaces.active.shading.type = "MATERIAL"   # The person's own viewport in Material Preview.
    yield
    started = time.perf_counter()
    yield from until_drawn("window_material", "window")
    state["seconds"]["window_material_call"] = round(time.perf_counter() - started, 3)
    check_window("material", state["window_material"])
    state["phase"] = "complete"
    state["success"] = all(state["checks"].values())


fixture_boot.drive(scenario, state, REPORT, "ANGELO_EXTERNAL_WINDOW")
