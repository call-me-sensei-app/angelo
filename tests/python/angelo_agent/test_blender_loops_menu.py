# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Loops menu in the running app, in Director, the view people start in (doc/angelo/features.rst, "Loops").

Real default startup (Director layout and chat), real window events through ``Window.event_simulate``. Director hides
the 3D view's header, so the menus are reached as a person reaches them there: the right-click menu in Edit Mode, the
Edge menu opened by name (``wm.call_menu``, as its shortcut does), and F3, whose menu search reads the 3D view's
header menus even while Director hides them. Menus are driven with the keyboard, never by calling the operators:
Home (or End) first, because Blender opens a menu with an item already under the pointer and highlighted (the first,
or the one last used), so arrows alone count from wherever that is (the 2026-11-15 run ran "Extrude Only Vertices"
instead of Loops); then Down to the item and Return. Each step records the operator that actually ran. The scenario:

* every item of Loops, from the right-click menu, on a fresh selection of a real loop, changes the geometry the way
  that tool does: Bridge adds faces between two loops; Circle makes a loop round; Curve moves the unselected loops
  between two selected ones; Flatten makes a loop planar; Gstretch to Annotation moves a path onto an annotation
  stroke; Relax smooths a jagged loop; Space evens a loop's spacing;
* the Edge menu's last entry is Loops, and its Relax runs from there;
* F3, "gstretch", Return runs Gstretch to Annotation: no other menu entry carries that word (stock Blender calls it
  "Fit Edge Loops To Annotation"), so the result proves search found the Loops entry.

Stand-in provider CLIs (``fake_provider_cli.py``) answer the start-up sign-in checks; no account or inference. Run
through ``run_native_suites.py --gui --include test_blender_loops_menu``; check ``loops-menu.json`` for ``success``
and ``phase == "complete"``. Screenshots after each visible step.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import hashlib
import json
import math
import random
import statistics
import tempfile
import time
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Euler, Vector

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-loops-menu-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.

os.environ["ANGELO_FAKE_CLI_STATE"] = str(OUT / "fake-cli")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "loops-menu.json"
RNG = random.Random(20261114)
state = {"phase": "boot", "success": False, "checks": {}, "steps": {}, "screenshots": {},
         "fixture": "Default Director startup, stand-in Claude connected, one mesh rebuilt per step in Edit Mode, "
                    "synthetic window events; no provider, account or inference"}
started = time.monotonic()

# The Loops menu's items in the order it draws them (LoopTools' names, alphabetical); Down presses after Home.
ORDER = ("Bridge", "Circle", "Curve", "Flatten", "Gstretch", "Relax", "Space")


def window():
    return bpy.context.window_manager.windows[0]


def view():
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return area, next(r for r in area.regions if r.type == 'WINDOW'), area.spaces.active


def send(kind, value='NOTHING', x=None, y=None, **extra):
    _, region, _ = view()
    window().event_simulate(type=kind, value=value, x=region.x + region.width // 2 if x is None else int(x),
                            y=region.y + region.height // 2 if y is None else int(y), **extra)


def tap(kind, **extra):
    send(kind, 'PRESS', **extra)
    send(kind, 'RELEASE')


SUBMENU_OPENS = 0.6   # Seconds after Right for a submenu to open: keys sent sooner reach its parent.


def keys(sequence, first=.5, gap=.12):
    """Press each key of ``sequence`` (event names, or (name, unicode)) one event-loop pass apart; after Right (which
    opens a submenu) wait SUBMENU_OPENS, so the next key reaches the submenu (the 2026-11-15 Linux run: the Edge menu's
    Home reached its parent and the Relax step ran Space)."""
    queue = list(sequence)

    def step():
        item = queue.pop(0)
        kind, char = item if isinstance(item, tuple) else (item, None)
        tap(kind, **({"unicode": char} if char else {}))
        return (SUBMENU_OPENS if kind == 'RIGHT_ARROW' else gap) if queue else None
    bpy.app.timers.register(step, first_interval=first)


def obj():
    return bpy.data.objects["LoopsFixture"]


# Fixtures: each rebuilds the fixture mesh in Edit Mode, selects a real loop and returns the measure before.

def ring(bm, count, radius, z=0.0, jitter=0.0, uneven=False):
    """A closed edge loop of ``count`` vertices around the Z axis; jitter moves them off the circle and plane."""
    verts = []
    for index in range(count):
        angle = 2 * math.pi * (index + (0.35 * math.sin(index * 1.7) if uneven else 0)) / count
        r = radius * (1 + jitter * (RNG.random() - 0.5))
        verts.append(bm.verts.new((r * math.cos(angle), r * math.sin(angle), z + jitter * (RNG.random() - 0.5))))
    for a, b in zip(verts, verts[1:] + verts[:1]):
        bm.edges.new((a, b))
    return verts


def tube(bm, rings, count, radius, length):
    """An open tube of ``rings`` loops with quad faces; returns the rings' vertex lists."""
    layers = [ring(bm, count, radius, z=length * i / (rings - 1)) for i in range(rings)]
    for lower, upper in zip(layers, layers[1:]):
        for i in range(count):
            bm.faces.new((lower[i], lower[(i + 1) % count], upper[(i + 1) % count], upper[i]))
    return layers


def rebuild(build):
    """Clear the fixture's edit mesh, build new geometry, select what ``build`` returns, deselect the rest.

    Never while a modal operator runs: a transform holds the edit mesh's vertices, so clearing them under it left the
    next draw reading freed edges (the intermittent SIGSEGV in extract_points_bm, 2 runs in 9 on the Linux lane VM,
    2026-11-18: a stray R had started Rotate)."""
    # Angelo's own resident modals (the chat bar's input) run all along and hold no mesh: only ones started since.
    running = [op.bl_idname for op in window().modal_operators if op.bl_idname not in state.get("resident", ())]
    assert not running, "a modal operator is still running before the fixture is rebuilt: %s" % running
    mesh = obj().data
    bm = bmesh.from_edit_mesh(mesh)
    bm.clear()
    # clear() leaves the BMesh with no select mode, and a flush in no mode selects no edge: every edge-loop tool then
    # finds no loop (the 2026-11-15 runs). The fixture selects vertices, so flush as Vertex select mode does.
    bm.select_mode = {'VERT'}
    selected = build(bm)
    for element in list(bm.verts) + list(bm.edges) + list(bm.faces):
        element.select_set(False)
    for vert in selected:
        vert.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh)


def coords(selected_only=True):
    bm = bmesh.from_edit_mesh(obj().data)
    return [v.co.copy() for v in bm.verts if v.select or not selected_only]


def radius_spread(points):
    centre = sum(points, Vector()) / len(points)
    radii = [(p - centre).length for p in points]
    return statistics.pstdev(radii) / statistics.mean(radii)


def plane_spread(points):
    """Spread off the loop's own best-fitting plane (Flatten fits that plane, which need not be horizontal)."""
    import numpy as np
    centred = np.array([tuple(p) for p in points]) - np.mean([tuple(p) for p in points], axis=0)
    return float(np.linalg.svd(centred, compute_uv=False)[-1]) / math.sqrt(len(points))


def ordered_ring(points):
    centre = sum(points, Vector()) / len(points)
    return sorted(points, key=lambda p: math.atan2(p.y - centre.y, p.x - centre.x))


def spacing_spread(points):
    loop = ordered_ring(points)
    lengths = [(a - b).length for a, b in zip(loop, loop[1:] + loop[:1])]
    return statistics.pstdev(lengths) / statistics.mean(lengths)


def roughness(points):
    loop = ordered_ring(points)
    return sum(((a + c) / 2 - b).length for a, b, c in zip(loop[-1:] + loop[:-1], loop, loop[1:] + loop[:1]))


def faces():
    return len(bmesh.from_edit_mesh(obj().data).faces)


def every_vertex():
    """Every vertex's position and whether it is selected, in index order."""
    return [(v.co.copy(), v.select) for v in bmesh.from_edit_mesh(obj().data).verts]


def unselected_motion(before, after):
    return sum((a - b).length for (a, selected), (b, _) in zip(after, before) if not selected)


STROKE_Z = 1.5


def stroke_distance(points):
    """Mean distance of the points to the annotation stroke (a straight line at z = STROKE_Z over x in [-2, 2])."""
    return statistics.mean(abs(p.z - STROKE_Z) + abs(p.y) for p in points)


def annotation_stroke():
    scene = bpy.context.scene
    notes = scene.annotation or bpy.data.annotations.new("LoopsNotes")
    scene.annotation = notes
    layer = notes.layers.get("Note") or notes.layers.new("Note", set_active=True)
    # The operator reads the active layer; a layer from an earlier run may not be it (5.3: layers.active_index).
    notes.layers.active_index = list(notes.layers).index(layer)
    # active=True sets the layer's active frame at once; Gstretch reads that frame, and Director may never draw it.
    frame = layer.frames[0] if len(layer.frames) else layer.frames.new(scene.frame_current, active=True)
    for stroke in list(frame.strokes):
        frame.strokes.remove(stroke)
    stroke = frame.strokes.new()
    stroke.points.add(41)
    for index, point in enumerate(stroke.points):
        point.co = (-2 + 4 * index / 40, 0.0, STROKE_Z)


# Each case: (LoopTools name, fixture -> selection, measure before/after -> passed). Fixtures run in Edit Mode.
def case(name):
    def path_line(bm):
        annotation_stroke()   # A fresh stroke for every Gstretch run, whatever an earlier run did with the last one.
        verts = [bm.verts.new((-1.5 + 3 * i / 12, 0.0, 0.3 * math.sin(i))) for i in range(13)]
        for a, b in zip(verts, verts[1:]):
            bm.edges.new((a, b))
        return verts

    if name == "Bridge":
        return (lambda bm: ring(bm, 12, 1.0) + ring(bm, 12, 1.0, z=1.5)), faces, lambda b, a: a > b
    if name == "Circle":
        return (lambda bm: ring(bm, 16, 1.0, jitter=0.6)), lambda: radius_spread(coords()), lambda b, a: a < b * 0.2
    if name == "Curve":
        def build(bm):
            layers = tube(bm, 5, 12, 1.0, 2.0)
            for vert in layers[4]:
                vert.co.x += 1.2   # Bend the far loop away, so the loops between have a curve to follow.
            return layers[0] + layers[4]
        return build, every_vertex, lambda b, a: unselected_motion(b, a) > 0.05
    if name == "Flatten":
        return (lambda bm: ring(bm, 16, 1.0, jitter=0.5)), lambda: plane_spread(coords()), lambda b, a: a < b * 0.2
    if name == "Gstretch":
        return path_line, lambda: stroke_distance(coords()), lambda b, a: a < b * 0.5
    if name == "Relax":
        return (lambda bm: ring(bm, 24, 1.0, jitter=0.5)), lambda: roughness(coords()), lambda b, a: a < b * 0.8
    if name == "Space":
        return (lambda bm: ring(bm, 16, 1.0, uneven=True)), lambda: spacing_spread(coords()), lambda b, a: a < b * 0.5
    raise KeyError(name)


def in_loops(name):
    """Inside the open Loops menu: Home to its first item, Down to ``name``, Return."""
    return ['HOME'] + ['DOWN_ARROW'] * ORDER.index(name) + ['RET']


def from_context_menu(name):
    """Right-click; Home to Loops (the menu's first entry), Right to open it; then the item."""
    def run():
        send('MOUSEMOVE')
        tap('RIGHTMOUSE')
        keys(['HOME', 'RIGHT_ARROW'] + in_loops(name), first=.6)
    return run


def from_edge_menu(name):
    """The Edge menu by name; End to its last entry (Loops), Right to open it; then the item by its accelerator letter
    (the first letter of its name, unique in Loops), as a keyboard user picks it. Counting arrows from Home landed one
    item too far in this path, in two Linux runs (2026-11-15), while the same keys worked from the right-click menu."""
    letter = name[0].upper()
    assert sum(text[0].upper() == letter for text in ORDER) == 1, "the accelerator must be unique in Loops"

    def open_menu():
        area, region, _ = view()
        with bpy.context.temp_override(window=window(), area=area, region=region):
            bpy.ops.wm.call_menu(name="VIEW3D_MT_edit_mesh_edges")
        keys(['END', 'RIGHT_ARROW', (letter, letter.lower())], first=.6)
        return None

    def run():
        # The previous step's Esc is still queued as an event: a menu opened now, in this tick, is closed by it, and
        # the keys then reach the 3D view (R starts Rotate; the 2026-11-18 VM runs). Open it once that has been read.
        send('MOUSEMOVE')
        bpy.app.timers.register(open_menu, first_interval=.4)
    return run


def control(key, name, build, measure, test):
    """Diagnostic, not a criterion: the same operator called directly on the same fixture in this session, so a miss
    shows whether the menu path or the fixture and its measure are at fault."""
    from angelo_agent import loops_menu
    idname = next(op for op, text, *_ in loops_menu.ITEMS if text.startswith(name))   # (idname, text, context)
    rebuild(build)
    before = measure()
    area, region, _ = view()
    try:
        with bpy.context.temp_override(window=window(), area=area, region=region):
            result = sorted(getattr(bpy.ops.mesh, idname.split(".")[1])())
    except RuntimeError as error:
        result = "error: %s" % error
    try:
        after = measure()
        passed = bool(test(before, after))
    except (ZeroDivisionError, statistics.StatisticsError) as error:
        after, passed = "unmeasurable: %s" % error, False
    summary = (lambda v: round(v, 4) if isinstance(v, float) else (len(v) if isinstance(v, list) else v))
    state.setdefault("control", {})[key] = {"result": result, "passed": passed, "before": summary(before),
                                            "after": summary(after)}


def last_operator():
    operators = bpy.context.window_manager.operators
    return operators[-1].bl_idname if len(operators) else None


def annotation_state():
    notes = bpy.context.scene.annotation
    layer = notes.layers[notes.layers.active_index] if notes and len(notes.layers) else None
    frame = layer.active_frame if layer else None
    return {"layer": layer.info if layer else None, "active_frame": frame is not None,
            "strokes": [(len(s.points), s.display_mode) for s in frame.strokes] if frame else None}


def from_search(words):
    def run():
        send('MOUSEMOVE')
        tap('F3')
        typed = [("SPACE", " ") if c == " " else (c.upper(), c) for c in words]
        keys(typed + ['RET'], first=.6)
    return run


def loops_menu_registered():
    return any(getattr(cls, "bl_label", None) == "Loops" for cls in bpy.types.Menu.__subclasses__())


STEPS = tuple(("context_menu_" + name.lower(), name, from_context_menu(name)) for name in ORDER) + (
    ("edge_menu_relax", "Relax", from_edge_menu("Relax")),
    ("f3_gstretch", "Gstretch", from_search("gstretch")),
)


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def capture(shot):
    path = OUT / shot
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"][shot] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state["phase"] = "complete"
        state["success"] = (len(state["steps"]) == len(STEPS) and all(state["checks"].values())
                            and all(step["passed"] for step in state["steps"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_LOOPS_MENU", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def enter_edit_mode():
    """A click on an empty spot takes the keyboard from the chat's composer; then the fixture alone is selected and
    Tab, with the pointer over the view, enters Edit Mode."""
    mesh = bpy.data.meshes.new("LoopsFixture")
    fixture = bpy.data.objects.new("LoopsFixture", mesh)
    bpy.context.scene.collection.objects.link(fixture)
    _, region, space = view()
    rv3d = space.region_3d
    rv3d.view_perspective = 'ORTHO'
    rv3d.view_rotation = Euler((math.radians(70), 0, math.radians(20))).to_quaternion()
    rv3d.view_location, rv3d.view_distance = (0, 0, 0.8), 7
    x, y = region.x + 40, region.y + 40
    send('MOUSEMOVE', x=x, y=y)
    send('LEFTMOUSE', 'PRESS', x=x, y=y)
    send('LEFTMOUSE', 'RELEASE', x=x, y=y)

    def select_and_tab():
        for other in bpy.context.view_layer.objects:
            other.select_set(other is fixture)
        bpy.context.view_layer.objects.active = fixture
        send('MOUSEMOVE')
        tap('TAB')
    bpy.app.timers.register(select_and_tab, first_interval=.5)


def tick():
    try:
        assert time.monotonic() - started < 420, "Loops menu fixture timed out in " + state["phase"]
        from angelo_agent import director, ui
        if state["phase"] == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            # A grant: the suite stands in for the person through C's harness scope (2.0.0; manifest harness=True).
            __import__("angelo_agent.grant_guard").grant_guard.stand_in().quick_connect(mode="claude_subscription")
            state["phase"] = "connect"
            return .2
        if state["phase"] == "connect":
            if not ui.connection_available():
                return .2
            state["checks"]["director_is_default"] = director.preferred_mode() == "DIRECTOR"
            state["checks"]["loops_menu_registered"] = loops_menu_registered()
            enter_edit_mode()
            state["phase"], state["since"] = "edit", time.monotonic()
            return .3
        if state["phase"] == "edit":
            if bpy.context.mode != 'EDIT_MESH':
                assert time.monotonic() - state["since"] < 10, "Tab did not enter Edit Mode"
                return .2
            state["checks"]["edit_mode_in_director"] = director.is_director(window())
            state["resident"] = sorted(op.bl_idname for op in window().modal_operators)
            capture("loops-start.png")
            state["phase"], state["index"], state["pending"] = "step", 0, None
            save()
            return .3
        if state["phase"] == "step":
            if state["pending"]:
                key, name, measure, test, before, sent = state["pending"]
                try:
                    after = measure()
                    passed = bool(test(before, after))
                except (ZeroDivisionError, statistics.StatisticsError) as error:
                    # The selection is gone (a wrong menu entry deleted it): this step failed; the rest still run.
                    after, passed = "unmeasurable: %s" % error, False
                if not passed and time.monotonic() - sent < 6.0:
                    return .2  # Arrow keys and a submenu take a few event-loop passes.
                summary = (lambda v: round(v, 4) if isinstance(v, float) else (len(v) if isinstance(v, list) else v))
                state["steps"][key] = {"item": name, "passed": passed, "before": summary(before),
                                       "after": summary(after), "mode": bpy.context.mode, "ran": last_operator()}
                if name == "Gstretch":
                    state["steps"][key]["annotation"] = annotation_state()
                state["pending"] = None
                state["index"] += 1
                tap('ESC')   # Close anything left open by a step that failed.
                capture(key + ".png")
                save()
            if state["index"] >= len(STEPS):
                finish()
                return None
            key, name, run = STEPS[state["index"]]
            build, measure, test = case(name)
            control(key, name, build, measure, test)
            rebuild(build)
            before = measure()
            run()
            state["pending"] = (key, name, measure, test, before, time.monotonic())
            return .3
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
