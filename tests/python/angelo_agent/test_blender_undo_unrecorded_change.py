# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Undo and Redo after a change no undo step recorded leave a consistent scene and never crash (readfile.cc,
memfile_undo.cc; doc/angelo/agent_execution.rst, "Undo after an unrecorded change").

A script, a timer, a handler or an add-on can add, remove or relink data-blocks without pushing an undo step. Global
Undo then decoded the previous step by reusing every ID whose stored chunks matched the step it came from, as if the
live scene were that step. It was not: a reused ID still pointed at one the undo freed, and the next read of it
crashed. Found by learn-l3 on 2026-11-12 (lessons suite, then a no-lesson repro): SIGSEGV during the Undo in the
dependency graph, or at the outliner's first draw after it; stock Blender 5.3.0 crashed the same way.

Each case makes recorded steps (operators with undo, or ``ed.undo_push``), then an unrecorded change, presses the
person's Undo (or Redo) key and reads the whole scene: the dependency graph, the outliner drawn, every object's data
of its own type, every collection's objects alive, the view layer's objects. The recorded change must be undone and
the unrecorded one gone or restored as the step stored it; an unrecorded value edit (the frame, a location) ends as
stock Blender leaves it (the frame read back with the scene, the untouched object's location kept).
``chained_undo_reuses`` pins the latency side: a second Undo after the first keeps unchanged IDs where they are (ID
reuse). The last case is the exact
path found: Edit Mode by keys, a cylinder added from Python, Undo. Mode boundaries: test_blender_undo_unrecorded_modes.

No Undo reads a whole step for an unrecorded change: ``image_transient_no_whole_read`` (an image loaded and removed,
another kept, with no step, as an observation preview or a thumbnail cache does) and ``stored_image_removed_reuses``
(an image a step stores, removed with no step, as an evicted thumbnail) pin that unchanged IDs keep their address.
``shape_key_clear`` removes an owned ID whose owner's pointer its caller cleared itself (``global_undo_toggled``: with
global undo off for the change). ``library_reloaded`` (and ``_nested``, an indirectly linked parent) reloads a library
with no step: its linked IDs get new session UIDs, matched by name in the step. Cases whose recorded step adds
an object first deselect everything in a step of their own, so the add leaves the edited object's stored chunks as
they were and only the fix can read it again. Every Undo is timed (``undo_ms``, from Blender's undo_pre to undo_post
handlers). ``ANGELO_UNDO_CASES`` (comma separated) runs only the named cases, each alone in its own run, for a
fail-first per case on an older binary. ``heavy_scene`` runs only when named: Undo times on a scene of
``ANGELO_UNDO_HEAVY_OBJECTS`` objects (20000 by default; each with a parent and an ID property, plus a tenth as many
linked from a library), Undo and undo pushes timed, for an A/B against the stock-undo app; it asserts no whole read
and the walk's Undo within 1.3x the no-change Undo + 3 ms. ``library_replaced`` removes a library, links its file
again and links the object into the scene, all with no step: the Undo must restore the step's object.

A window is required: ``run_native_suites.py --gui --include test_blender_undo_unrecorded_change.py``.
"""

import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

import qa_boot  # noqa: E402
import qa_input  # noqa: E402
import qa_waits  # noqa: E402
import undo_unrecorded_qa as qa  # noqa: E402
from bpy_extras.view3d_utils import location_3d_to_region_2d  # noqa: E402
import qa_hit

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-undo-unrecorded"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "undo-unrecorded-change.json"
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "cases": {}}
CASES = []   # (name, generator function), in the order they run: filled by @case below.
HEAVY = set()   # Cases run only when ANGELO_UNDO_CASES names them (@heavy below).
ONLY = [n for n in os.environ.get("ANGELO_UNDO_CASES", "").split(",") if n]   # Empty: every case.
SCALE = qa_waits.timeout_scale()   # The runner's timeout scale (T4 runs 2): every wait below is scaled by it.
started = None   # When the boot gate opened (qa_boot.gated): the run's own budget counts from there.


def until(condition, what, limit=15.0):
    deadline = time.monotonic() + limit * SCALE
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for " + what)
        yield 0.1


def scene():
    """The scene as it is now: an Undo that reads the whole step replaces the Scene, and a Python reference kept from
    before it is gone ("StructRNA of type Scene has been removed")."""
    return bpy.context.scene


def settle():
    for _ in range(8):   # Draws right after the key, the outliner's among them.
        yield 0.1


UNDO_TIMES = {"pre": None, "ms": []}


def _undo_pre(*_args):
    UNDO_TIMES["pre"] = time.perf_counter()


def _undo_post(*_args):
    if UNDO_TIMES["pre"] is not None:
        UNDO_TIMES["ms"].append(round((time.perf_counter() - UNDO_TIMES["pre"]) * 1000.0, 1))
        UNDO_TIMES["pre"] = None


for _handlers, _fn in ((bpy.app.handlers.undo_pre, _undo_pre), (bpy.app.handlers.undo_post, _undo_post),
                       (bpy.app.handlers.redo_pre, _undo_pre), (bpy.app.handlers.redo_post, _undo_post)):
    _handlers.append(_fn)


def case(function):
    CASES.append((function.__name__, function))
    return function


def heavy(function):
    HEAVY.add(function.__name__)
    return case(function)


def wanted(name):
    return name in ONLY if (ONLY or name in HEAVY) else True


def deselected():
    """Everything deselected in a step of its own: an operator's add then leaves the other objects' chunks as stored."""
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    yield from settle()
    qa.push("Deselected")


def record(name, extra=None, **expect):
    """Read the scene after an Undo or Redo: ``expect`` maps a check's name to its outcome."""
    problems = qa.consistent()
    objects = sorted(o.name for o in bpy.data.objects)
    case = {"problems": problems, "objects": objects if len(objects) <= 50 else len(objects),
            "undo_ms": list(UNDO_TIMES["ms"]), **(extra or {}), **expect}
    UNDO_TIMES["ms"].clear()
    state["cases"][name] = case
    state["checks"][name] = not problems and all(expect.values())
    REPORT.write_text(json.dumps(state, indent=2))   # Written as it goes: a crash leaves the last case.


# The value cases first: they pin stock's parity on a scene no earlier case has changed. An older build of this fix read
# a whole step after some cases, which gave every ID a new address and so read every value back on the next Undo.
@case
def frame_as_stock():   # A value edit with no step, then Undo of a later move: as stock Blender 5.3.0.
    qa.recorded_move()
    before = scene().frame_current
    scene().frame_current = before + 7
    qa.press("ed.undo")
    yield from settle()
    # Stock reads the scene back here (the old app, merge-11's Mac fail-first of 74adf151565): the frame returns to
    # the step's. The fix must not change that; it changes only IDs pointing at one the step frees.
    record("frame_as_stock", frame_as_stock=bpy.context.scene.frame_current == before)


@case
def location_kept():   # An object the undone step did not change keeps it, as stock does.
    qa.recorded_move()
    light_x = round(bpy.data.objects["Light"].location.x + 2.0, 4)
    bpy.data.objects["Light"].location.x = light_x
    qa.press("ed.undo")
    yield from settle()
    record("location_kept", location_kept=round(bpy.data.objects["Light"].location.x, 4) == light_x)


@case
def image_transient_no_whole_read():   # As an observation preview or a thumbnail: loaded, removed, no step.
    qa.recorded_move()
    camera = bpy.data.objects["Camera"].as_pointer()   # Unchanged by every step: reused, its address kept.
    image = bpy.data.images.new("Transient Preview", 8, 8)
    bpy.data.images.remove(image)
    bpy.data.images.new("Kept Preview", 8, 8)   # And one left in Main, as a thumbnail cache does.
    qa.press("ed.undo")
    yield from settle()
    record("image_transient_no_whole_read",
           no_whole_read=bpy.data.objects["Camera"].as_pointer() == camera)


@case
def stored_image_removed_reuses():   # As a thumbnail evicted: an image a step stores, removed with no step.
    image = bpy.data.images.new("Stored Thumbnail", 8, 8)
    qa.push("Thumbnail stored")
    qa.recorded_move()
    camera = bpy.data.objects["Camera"].as_pointer()
    bpy.data.images.remove(image)
    qa.press("ed.undo")
    yield from settle()
    record("stored_image_removed_reuses",
           no_whole_read=bpy.data.objects["Camera"].as_pointer() == camera,
           image_from_the_step="Stored Thumbnail" in bpy.data.images)


@case
def shape_key_clear():   # An owned ID freed after its caller cleared the owner's pointer itself.
    qa.shape_keys_add("Basis", "Unrecorded Key")
    yield from settle()
    qa.push("Shape keys")
    qa.recorded_move()
    bpy.data.objects["Cube"].shape_key_clear()
    qa.press("ed.undo")
    yield from settle()
    record("shape_key_clear", **qa.shape_keys_from_the_step(("Basis", "Unrecorded Key")))
    bpy.data.objects["Cube"].shape_key_clear()   # The cases after this one start from the stock cube.
    yield from settle()
    qa.push("Shape keys cleared")


@case
def global_undo_toggled():   # The same with global undo off for the change: steps written before it stay readable.
    qa.shape_keys_add("Basis", "Toggled Key")
    yield from settle()
    qa.push("Shape keys")
    qa.recorded_move()
    edit = bpy.context.preferences.edit
    edit.use_global_undo = False
    bpy.data.objects["Cube"].shape_key_clear()
    edit.use_global_undo = True
    qa.press("ed.undo")
    yield from settle()
    record("global_undo_toggled", **qa.shape_keys_from_the_step(("Basis", "Toggled Key")))
    bpy.data.objects["Cube"].shape_key_clear()
    yield from settle()
    qa.push("Shape keys cleared")


@case
def unrecorded_add():
    x0 = qa.cube_x()
    qa.recorded_move()
    added = bpy.data.objects.new("Unrecorded", bpy.data.meshes.new("Unrecorded"))
    scene().collection.objects.link(added)
    qa.press("ed.undo")
    yield from settle()
    record("unrecorded_add", move_undone=qa.cube_x() == x0, added_gone="Unrecorded" not in bpy.data.objects)


@case
def unrecorded_remove():
    x0 = qa.cube_x()
    qa.recorded_move()
    bpy.data.objects.remove(bpy.data.objects["Light"])
    qa.press("ed.undo")
    yield from settle()
    light = bpy.data.objects.get("Light")
    record("unrecorded_remove", move_undone=qa.cube_x() == x0, removed_back=light is not None,
           removed_back_in_its_collection=light is not None and bool(light.users_collection),
           removed_back_in_the_view_layer=light is not None and light.name in bpy.context.view_layer.objects)


@case
def unrecorded_collection():
    x0 = qa.cube_x()
    qa.recorded_move()
    group = bpy.data.collections.new("Unrecorded Group")
    scene().collection.children.link(group)
    group.objects.link(bpy.data.objects.new("Grouped", bpy.data.meshes.new("Grouped")))
    qa.press("ed.undo")
    yield from settle()
    record("unrecorded_collection", move_undone=qa.cube_x() == x0,
           added_gone="Grouped" not in bpy.data.objects and "Unrecorded Group" not in bpy.data.collections)


@case
def pointer_reassigned():   # No ID added or removed unrecorded: only a pointer.
    yield from deselected()
    mesh = bpy.data.objects["Cube"].data.name
    made = qa.recorded_add("mesh.primitive_cube_add", location=(4.0, 0.0, 0.0))
    name = made.name   # Read now: the Undo frees it.
    bpy.data.objects["Cube"].data = bpy.data.meshes[made.data.name]
    qa.press("ed.undo")
    yield from settle()
    cube = bpy.data.objects["Cube"]
    record("pointer_reassigned", added_gone=name not in bpy.data.objects,
           data_restored=cube.data is not None and cube.data.name == mesh)


@case
def dict_id_property():   # A pointer no depsgraph tag reports: an ID property set dictionary-style.
    yield from deselected()
    made = qa.recorded_add("mesh.primitive_cube_add", location=(4.0, 4.0, 0.0))
    name = made.name
    bpy.data.objects["Cube"]["unrecorded_ref"] = made
    qa.press("ed.undo")
    yield from settle()
    record("dict_id_property", added_gone=name not in bpy.data.objects,
           property_from_the_step="unrecorded_ref" not in bpy.data.objects["Cube"].keys())


@case
def later_object_in_older_collection():
    yield from deselected()
    made = qa.recorded_add("mesh.primitive_uv_sphere_add", location=(-4.0, 0.0, 0.0))
    name = made.name
    scene().collection.objects.link(made)   # Into the scene's own collection too, unrecorded.
    qa.press("ed.undo")
    yield from settle()
    record("later_object_in_older_collection", added_gone=name not in bpy.data.objects)


@case
def modifier_target():
    yield from deselected()
    made = qa.recorded_add("object.empty_add", location=(0.0, 4.0, 0.0))
    name = made.name
    modifier = bpy.data.objects["Cube"].modifiers.new("Unrecorded Array", 'ARRAY')
    modifier.use_object_offset = True
    modifier.offset_object = made
    qa.press("ed.undo")
    yield from settle()
    record("modifier_target", added_gone=name not in bpy.data.objects,
           modifier_gone="Unrecorded Array" not in bpy.data.objects["Cube"].modifiers)


@case
def linked_from_a_library():
    library = qa.write_library(OUT / "undo-unrecorded-library.blend", ("Linked A", "Linked B"))
    qa.push("Library written")
    qa.link(library, "Linked A", scene())
    qa.push("Linked A")
    qa.link(library, "Linked B", scene())   # From the library already linked, unrecorded.
    qa.press("ed.undo")
    yield from settle()
    # As stock Blender 5.3.0 (the old app, Mac and Windows): Undo keeps linked objects from a library still linked.
    linked = sorted(o.name for o in bpy.data.objects if o.library is not None)
    record("linked_from_a_library", linked_kept_as_stock=linked == ["Linked A", "Linked B"])


@case
def library_replaced():   # A library removed and linked again, its object linked in, all with no step.
    record("library_replaced", **(yield from qa.library_replaced(scene(), OUT)))


@case
def library_reloaded():   # Reloaded with no step: its linked IDs' new session UIDs matched by name in the step.
    record("library_reloaded", **(yield from qa.library_reloaded(scene(), OUT, nested=False)))


@case
def library_reloaded_nested():   # The same for a library whose object's parent comes from another, indirectly.
    record("library_reloaded_nested", **(yield from qa.library_reloaded(scene(), OUT, nested=True)))


@case
def redo_after_unrecorded():
    x0 = qa.cube_x()
    qa.recorded_move()
    qa.press("ed.undo")
    yield from settle()
    scene().collection.objects.link(bpy.data.objects.new("Before Redo", bpy.data.meshes.new("Before Redo")))
    qa.press("ed.redo")
    yield from settle()
    record("redo_after_unrecorded", move_redone=qa.cube_x() == round(x0 + 1.0, 4),
           added_gone="Before Redo" not in bpy.data.objects)


@case
def chained_undo_reuses():
    qa.recorded_move()
    qa.recorded_move()
    scene().collection.objects.link(bpy.data.objects.new("Unrecorded 2", bpy.data.meshes.new("Unrecorded 2")))
    qa.press("ed.undo")
    yield from settle()
    camera = bpy.data.objects["Camera"].as_pointer()   # Unchanged in every step below.
    qa.press("ed.undo")
    yield from settle()
    record("chained_undo_reuses", second_undo_kept_the_camera=bpy.data.objects["Camera"].as_pointer() == camera)


@case
def edit_keys_then_python_add():   # The path learn-l3 found.
    cube = bpy.data.objects["Cube"]
    qa.press("object.select_all", "Object Mode", action='DESELECT')
    yield 0.6
    area, region = qa.view3d()
    point = location_3d_to_region_2d(region, area.spaces.active.region_3d, cube.matrix_world.translation)
    qa_input.press(qa.window(), *qa_hit.xy(qa_hit.RAW, region, point.x, point.y))
    yield from until(lambda: bpy.context.view_layer.objects.active == cube and cube.select_get(), "the cube selected")
    yield 0.6
    qa.key('TAB')
    yield from until(lambda: cube.mode == 'EDIT', "Edit Mode")
    yield 0.6
    qa.key('THREE')
    qa.key('A')
    yield 0.6
    qa.key('TAB')
    yield from until(lambda: cube.mode == 'OBJECT', "Object Mode")
    yield 0.6
    with bpy.context.temp_override(window=qa.window(), area=area, region=region):
        bpy.ops.mesh.primitive_cylinder_add()   # From Python: no undo step.
    yield 0.8
    qa.press("ed.undo")
    yield from settle()
    record("edit_keys_then_python_add", added_gone="Cylinder" not in bpy.data.objects)


@heavy
def heavy_scene():   # Undo and push times on a heavy scene: run against this build and the stock-undo app (A/B).
    count = int(os.environ.get("ANGELO_UNDO_HEAVY_OBJECTS") or 20000)
    extra = max(1, count // 10)
    yield from qa.build_heavy_scene(scene(), count, extra, OUT / "undo-unrecorded-heavy-library.blend")
    yield from settle()
    qa.push("Heavy scene")
    timings = {"push": []}

    def timed(label):
        yield from settle()
        timings.setdefault(label, []).extend(UNDO_TIMES["ms"])
        UNDO_TIMES["ms"].clear()

    def timed_push(message):
        start = time.perf_counter()
        qa.push(message)
        timings["push"].append(round((time.perf_counter() - start) * 1000.0, 1))

    camera_kept = []
    for _round in range(3):
        # Nothing changed with no step: every old ID is stored, so the walk is skipped.
        qa.recorded_move()
        yield from settle()
        UNDO_TIMES["ms"].clear()
        qa.press("ed.undo")
        yield from timed("recorded_move")
        # An image added with no step (an observation preview kept): the walk runs over every ID.
        qa.recorded_move()
        yield from settle()
        bpy.data.images.new("Heavy Preview", 8, 8)
        UNDO_TIMES["ms"].clear()
        qa.press("ed.undo")
        yield from timed("unrecorded_image_added")
        # An image a step stores, removed with no step (an evicted thumbnail): no whole read.
        image = bpy.data.images.new("Heavy Thumbnail", 8, 8)
        timed_push("Heavy thumbnail")
        qa.recorded_move()
        yield from settle()
        camera = bpy.data.objects["Camera"].as_pointer()
        bpy.data.images.remove(image)
        UNDO_TIMES["ms"].clear()
        qa.press("ed.undo")
        yield from timed("stored_image_removed")
        camera_kept.append(bpy.data.objects["Camera"].as_pointer() == camera)
        bpy.data.objects["Light"].location.z += 0.01   # A value edit, then a step of its own.
        timed_push("Heavy edit")

    medians = {kind: sorted(ms)[len(ms) // 2] for kind, ms in timings.items() if ms}
    spread = {kind: [min(ms), max(ms)] for kind, ms in timings.items() if ms}
    # Measured on Mac (merge-11, 2026-11-13, 20k objects, same-tree A/B): the walk's Undo ran 1.05x the no-change Undo
    # (52.1 vs 49.5 ms medians; stock 1.06x, 47.1 vs 44.4), each kind spreading about 15%. 1.3x plus 3 ms (scaled
    # for slow runners) holds that spread and fails a walk that costs a third of an Undo or more.
    walk_bounded = medians["unrecorded_image_added"] <= 1.3 * medians["recorded_move"] + 3.0 * SCALE
    local = sum(1 for o in bpy.data.objects if o.name.startswith("Heavy ") and o.name[len("Heavy "):].isdigit())
    record("heavy_scene", extra={"heavy_objects": count, "heavy_linked": extra, "undo_ms_by_kind": timings,
                                 "median_ms": medians, "spread_ms": spread},
           all_objects_kept=local == count and len(bpy.data.collections["Heavy Linked"].objects) == extra,
           stored_image_removed_no_whole_read=all(camera_kept), walk_bounded=walk_bounded)


def scenario():
    qa.ensure_outliner()   # Angelo's start-up is over: the timer is qa_boot.gated.
    yield 0.5
    for name, function in CASES:
        if not wanted(name):
            continue
        state["phase"] = name
        yield from function()


def finish(error=None):
    if error is not None:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        print(state["traceback"], flush=True)
    else:
        state["phase"] = "complete"
        ran = {n for n, _f in CASES if wanted(n)}
        state["success"] = set(state["checks"]) == ran and all(state["checks"].values())
    REPORT.write_text(json.dumps(state, indent=2))
    print("ANGELO_UNDO_UNRECORDED", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


_steps = scenario()


def tick():
    global started
    try:
        started = time.monotonic() if started is None else started
        budget = 240 + (900 if HEAVY & set(ONLY) else 0)   # Inside the manifest's 1200 s: a hang says its phase.
        assert time.monotonic() - started < budget * SCALE, "timed out in phase " + state["phase"]
        return next(_steps)
    except StopIteration:
        finish()
    except Exception as error:   # noqa: BLE001
        finish(error)
    return None


REPORT.write_text(json.dumps(state, indent=2))   # A boot that never ends leaves phase "boot" (the gate says why).
bpy.app.timers.register(qa_boot.gated(qa_input.after_clicks(tick)), first_interval=0.5)
