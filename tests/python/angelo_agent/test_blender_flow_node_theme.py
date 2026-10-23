# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Generation Flow's node bodies are in the card language and every other tree is stock, in pixels (1.0.5, A3).

``node_draw.cc`` swaps the theme's widget colours and node backdrop for card_kit's tokens while a
``ANGELO_GenerationFlowTree`` draws, and restores them afterwards. In one session two Node Editors of the default
window show the Image to 3D template (its Generate 3D step with Stop for review on) and the default cube's material.
Each node's pixels are read from the capture:

* the flow's node bodies are PANEL (at the theme's backdrop alpha), their buttons PANEL_HOVER or CARD, the review toggle TEAL_SOFT over PANEL, and
  none of the person's stock widget colours (the theme's own regular, menu, number and toggle-on fills) shows;
* the shader node's body is the theme's node backdrop and shows its stock widget fills and none of the tokens.

A capture is judged only once it shows the state being judged: every node of both trees drawn where the editors'
views place it (a capture read a second early still shows the case before, 1.0.5's fix round: no tree at scale 1).

Captures in English at UI scale 1 and 2, idle, and a flow with one failed step: ``flow-node-theme-*.png``. No provider,
network or paid request: one in-memory fixture image key, and the attempt is an in-memory library record. Run through
``run_native_suites.py --gui --include test_blender_flow_node_theme``; check ``flow-node-theme.json``.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

assert not bpy.app.background and "--factory-startup" in sys.argv, "Use an isolated factory-startup GUI instance"
OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-flow-node-theme-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_flow_view
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
prefs = bpy.context.preferences
prefs.view.show_splash = False
prefs.use_preferences_save = False
prefs.view.smooth_view = 0   # View All lands at once: the framing is complete when the capture is taken.
import qa_waits  # noqa: E402
from angelo_agent import card_kit, flow_host, flow_nodes, flow_starters, flow_tree  # noqa: E402

REPORT = OUT / "flow-node-theme.json"
NEAR = 6          # Per-channel distance that counts as "this colour" (anti-aliased edges excluded).
NEUTRAL = 1       # A pixel whose channels are within this of each other is grey: never one of the tinted colours.
SHOWS = 0.004     # Share of a node's pixels above which a colour is drawn there.
DRAWN = 0.5       # A drawn node covers at least this share of its rectangle with something other than the background.
SETTLED = 0.6     # Seconds the drawn node sizes stay the same before framing (a scale change redraws them).
CURRENT_WAIT = 10.0   # Seconds (x the runner's timeout scale) for a capture to show the case being judged.
state = {"phase": "boot", "success": False, "checks": {}, "observed": {}, "screenshots": []}
checks, ctx = state["checks"], {}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def rgb(color):
    return tuple(int(round(c * 255)) for c in color[:3])


def over(top, ground):
    """``top`` (with alpha) composited over the opaque ``ground``, as 0-255 RGB."""
    a = top[3]
    return tuple(int(round((t * a + g * (1 - a)) * 255)) for t, g in zip(top[:3], ground[:3]))


TOKENS = {"PANEL": rgb(card_kit.PANEL), "PANEL_HOVER": rgb(card_kit.PANEL_HOVER), "CARD": rgb(card_kit.CARD),
          "TEAL_SOFT": over(card_kit.TEAL_SOFT, card_kit.PANEL)}


def stock():
    """The person's theme as Python reads it outside a flow's draw (restored after every one)."""
    theme = prefs.themes[0]
    tui = theme.user_interface
    back = theme.node_editor.space.back
    backdrop = theme.node_editor.node_backdrop   # Drawn with its alpha over the editor's background.
    return {"backdrop": over(backdrop, back), "flow_body": over(tuple(card_kit.PANEL[:3]) + (backdrop[3],), back),
            "regular": rgb(tui.wcol_regular.inner), "tool": rgb(tui.wcol_tool.inner),
            "menu": rgb(tui.wcol_menu.inner), "number": rgb(tui.wcol_num.inner),
            "toggle_on": rgb(tui.wcol_toggle.inner_sel)}


def areas():
    """(flow area, shader area): the window's largest area split in two side-by-side Node Editors, once (the second
    largest area can be a timeline a few rows high, where no node shows)."""
    found = [a for a in window().screen.areas if a.as_pointer() in (ctx.get("flow"), ctx.get("shader"))]
    if len(found) < 2 or ctx.get("flow") == ctx.get("shader"):
        if not ctx.get("split"):   # Once: the split shows two editors only after the screen refreshes.
            largest = max(window().screen.areas, key=lambda a: a.width * a.height)
            largest.type = 'NODE_EDITOR'
            region = next(r for r in largest.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window(), screen=window().screen, area=largest, region=region):
                bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5)
            ctx["split"] = True
        editors = sorted((a for a in window().screen.areas if a.type == 'NODE_EDITOR'), key=lambda a: a.x)
        if len(editors) < 2:
            return None   # Not refreshed yet: the caller waits a moment.
        flow, shader = editors[0], editors[-1]
        for editor in (flow, shader):   # Only the node canvas is measured: a sidebar's stock panels are not the tree's.
            space = editor.spaces.active
            space.show_region_ui = space.show_region_toolbar = False
        flow.spaces.active.tree_type = flow_nodes.TREE
        shader.spaces.active.tree_type = 'ShaderNodeTree'
        cube = bpy.data.objects.get("Cube") or shaded_cube()
        if not cube.material_slots or cube.material_slots[0].material is None:
            cube.data.materials.append(bpy.data.materials.new("Stock"))
        bpy.context.view_layer.objects.active = cube
        ctx["flow"], ctx["shader"] = flow.as_pointer(), shader.as_pointer()
        return flow, shader
    return tuple(sorted(found, key=lambda a: a.as_pointer() != ctx["flow"]))


def shaded_cube():
    """A cube with a node material for the shader editor (Angelo's start-up scene may have none)."""
    import bmesh
    mesh = bpy.data.meshes.new("Cube")
    shape = bmesh.new()
    bmesh.ops.create_cube(shape, size=2.0)
    shape.to_mesh(mesh)
    shape.free()
    cube = bpy.data.objects.new("Cube", mesh)
    bpy.context.scene.collection.objects.link(cube)
    return cube


def drawn_sizes(editors):
    """Every node's drawn size in both editors, or None before each has been drawn (a size is known only after a draw,
    and after a scale change only once it is drawn at the new scale)."""
    sizes = []
    for area in editors:
        tree = area.spaces.active.edit_tree
        if tree is None or not all(node.dimensions[0] > 0 for node in tree.nodes):
            return None
        sizes.append(tuple(tuple(node.dimensions) for node in tree.nodes))
    return tuple(sizes)


def frame(area):
    """Frame the editor's drawn tree."""
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window(), area=area, region=region, space_data=area.spaces.active):
        if bpy.ops.node.select_all.poll():
            bpy.ops.node.select_all(action='DESELECT')
        if bpy.ops.node.view_all.poll():
            bpy.ops.node.view_all()


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def pixels(path):
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    return (values.reshape(height, width, 4)[..., :3] * 255).round().astype(numpy.int32)


def shares(px, area, colours):
    """{node: {colour name: share of the node's pixels that are it (``matching``)}, plus "main": its dominant colour}."""
    import numpy
    import qa_flow_view
    region, rects = qa_flow_view.graph_bounds(area, prefs.system.ui_scale)
    result = {}
    for node, (left, bottom, right, top) in zip(area.spaces.active.node_tree.nodes, rects):
        x0, y0 = max(0, int(region.x + max(left, 0))), max(0, int(region.y + max(bottom, 0)))
        x1 = int(region.x + min(right, region.width))
        y1 = int(region.y + min(top, region.height))
        block = px[y0:max(y0, y1), x0:max(x0, x1)].reshape(-1, 3)
        if not len(block):
            continue
        entry = {name: round(float(matching(block, c).mean()), 4) for name, c in colours.items()}
        keys = block[:, 0] * 65536 + block[:, 1] * 256 + block[:, 2]
        values, counts = numpy.unique(keys, return_counts=True)
        value = int(values[counts.argmax()])
        entry["main"] = [value // 65536, value // 256 % 256, value % 256]
        result[node.name] = entry
    return result


def matching(block, colour):
    """Which pixels of ``block`` (N x 3) are ``colour``: within NEAR in every channel and, for a tinted colour (card_kit's
    tokens are blue-tinted), not grey. Blender's own neutral greys 43-44 (grid dots, widget outlines) are within NEAR
    of PANEL_HOVER per channel; they made 2033 of 2039 (scale 1) and 3518 of 3519 (scale 2) of the shader's
    "PANEL_HOVER" pixels in 1.0.5's final round."""
    import numpy
    target = numpy.array(colour)
    hits = numpy.abs(block - target).max(axis=1) <= NEAR
    if target.max() - target.min() > NEUTRAL:
        hits &= block.max(axis=1) - block.min(axis=1) > NEUTRAL
    return hits


def region_shares(px, area, colours):
    """{colour name: share of the editor's WINDOW region that is it (``matching``)}."""
    import numpy
    region = next(r for r in area.regions if r.type == 'WINDOW')
    block = px[region.y:region.y + region.height, region.x:region.x + region.width].reshape(-1, 3)
    return {name: round(float(matching(block, c).mean()), 4) for name, c in colours.items()}


def current(px, flow, shader, background):
    """The capture shows the state being judged: every node of both editors drawn at the rectangle its editor's view
    places it at (anything but the editor's background over at least DRAWN of it). Returns (ok, per-node shares)."""
    import numpy
    import qa_flow_view
    covered = {}
    for name, area in (("flow", flow), ("shader", shader)):
        region, rects = qa_flow_view.graph_bounds(area, prefs.system.ui_scale)
        for node, (left, bottom, right, top) in zip(area.spaces.active.edit_tree.nodes, rects):
            x0, y0 = max(0, int(region.x + max(left, 0))), max(0, int(region.y + max(bottom, 0)))
            x1, y1 = int(region.x + min(right, region.width)), int(region.y + min(top, region.height))
            block = px[y0:max(y0, y1), x0:max(x0, x1)].reshape(-1, 3)
            off = float((numpy.abs(block - numpy.array(background)).max(axis=1) > NEAR).mean()) if len(block) else 0.0
            covered["%s/%s" % (name, node.name)] = round(off, 4)
    return bool(covered) and all(share >= DRAWN for share in covered.values()), covered


def near(a, b, tolerance=NEAR):
    return max(abs(x - y) for x, y in zip(a, b)) <= tolerance


def judge(case):
    """Judge ``case`` from a capture that shows it; False while the capture still shows an earlier state."""
    flow, shader = areas()
    colours = dict(TOKENS, **{"stock_" + k: v for k, v in stock().items()})
    shot = capture("flow-node-theme-%s.png" % case)
    px = pixels(shot)
    back = rgb(prefs.themes[0].node_editor.space.back)
    shows, covered = current(px, flow, shader, back)
    tries = state.setdefault("capture_tries", {})
    tries[case] = tries.get(case, 0) + 1
    since = ctx.setdefault("since", {}).setdefault(case, time.monotonic())
    if not shows and time.monotonic() - since < CURRENT_WAIT * qa_waits.timeout_scale():
        return False   # Drawn a moment later: retake without changing anything.
    state["observed"].setdefault(case, {})["covered"] = covered
    checks[case + "_capture_shows_both_trees"] = shows
    # Whole editors for the bodies and leaks (each tree against the other), the flow's node rectangles for its
    # buttons: SHOWS is a share of a node's pixels, and one small node fills about 2% of an editor (failed-step).
    # The rectangles land on the drawn nodes: ``current`` checked that above.
    flow_seen, shader_seen = region_shares(px, flow, colours), region_shares(px, shader, colours)
    flow_nodes = shares(px, flow, colours)
    state["observed"][case].update(stock=stock(), tokens=TOKENS, flow=flow_seen, shader=shader_seen,
                                   nodes={"flow": flow_nodes, "shader": shares(px, shader, colours)})
    stock_names = ("stock_regular", "stock_tool", "stock_menu", "stock_number", "stock_toggle_on")
    # Only meaningful where the person's theme differs from the tokens (Angelo's own themes are close to them).
    distinct = [n for n in stock_names if all(not near(colours[n], t, 12) for t in TOKENS.values())]
    state["observed"][case]["distinct_stock"] = distinct
    # Against the shader editor beside it: one small node fills little of a whole region (the failed-step case).
    checks[case + "_flow_bodies_are_panel"] = (flow_seen["stock_flow_body"] > SHOWS
                                               and flow_seen["stock_flow_body"] > 4 * shader_seen["stock_flow_body"])
    # Every step has buttons (Pick image, Run this step, Place in scene): each node shows PANEL_HOVER fills.
    checks[case + "_flow_buttons_are_card_tokens"] = bool(flow_nodes) and all(
        node["PANEL_HOVER"] > SHOWS for node in flow_nodes.values())
    checks[case + "_no_stock_widget_fill_in_a_flow"] = all(flow_seen[n] <= SHOWS for n in distinct)
    checks[case + "_shader_body_is_stock"] = shader_seen["stock_backdrop"] > 0.03
    checks[case + "_shader_keeps_stock_widgets"] = not distinct or any(shader_seen[n] > SHOWS for n in distinct)
    # Blender's own greys never count as a token (``matching``); a leaked button fill covers more than SHOWS.
    checks[case + "_no_token_fill_in_the_shader"] = (shader_seen["TEAL_SOFT"] <= SHOWS / 4
                                                     and shader_seen["PANEL_HOVER"] <= SHOWS)
    if case.startswith("idle"):
        checks[case + "_review_on_is_teal_soft_not_blue"] = flow_seen["TEAL_SOFT"] > SHOWS / 4 and (
            "stock_toggle_on" not in distinct or flow_seen["stock_toggle_on"] <= SHOWS / 4)
    return True


def image_account():
    """An in-memory image generation key the catalog treats as verified; no provider is ever called."""
    from angelo_agent.api_connections import CheckResult
    from angelo_agent.provider_registry import SPECS
    record = ui.connections().add("GOOGLE", "API_KEY", "Fixture Gemini", group="GENAI")
    ui._memory_keys[record["id"]] = "fixture-key-not-a-real-account"
    ui._api_connections.results[record["id"]] = CheckResult("verified", "Fixture", True, SPECS[record["mode"]]["models"])
    entry = next(e for e in flow_host.catalog_entries("IMAGE") if e["ready"])
    return entry["connection"], entry["id"]


def failed_flow():
    """A flow with one Generate Image step whose last attempt failed (an in-memory library record)."""
    connection, model = image_account()
    tree = bpy.data.node_groups.new("Failed step", flow_nodes.TREE)
    look = tree.nodes.new(flow_nodes.ANGELO_FlowGenerateImageNode.bl_idname)
    look.name, look.prompt = "Look", "a brass robot, failed case"
    look.connection_id, look.model = connection, model
    flow_host.refresh(tree)
    key = flow_host.node_state(tree, "Look")["key"]
    identity = "qa-failed-%s" % tree.name
    flow_host.library().assets[identity] = {"id": identity, "kind": "IMAGE", "status": "failed", "created": time.time(),
                                            "lineage": {"source": "flow", "flow": tree.name, "node": "Look", "key": key}}
    flow_host.tree_changed(tree)
    return tree


def show(tree):
    flow, _shader = areas()   # Ready: the boot phase waited for both editors.
    flow.spaces.active.node_tree = tree
    flow_host.tree_changed(tree)
    flow_host.pump()


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(checks) and all(checks.values()))
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    prefs.view.ui_scale = 1.0
    REPORT.write_text(json.dumps(state, indent=2, default=str, ensure_ascii=False))
    print("ANGELO_FLOW_NODE_THEME", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 200, "flow node theme fixture timed out in " + state["phase"]
        phase = state["phase"]
        if phase == "boot":
            prefs.view.language = "en_US"
            tree = bpy.data.node_groups.new("Image to 3D", flow_nodes.TREE)
            flow_tree.flow_id(tree)
            flow_tree.write_flow(tree, flow_starters.instantiate("image_to_3d"))
            tree.nodes["3D model"].review = True   # Stop for review on: the toggle's "on" fill.
            ctx["idle"] = tree
            state["phase"] = "editors"
            ctx["queue"] = [("idle-scale-1", "idle", 1.0), ("idle-scale-2", "idle", 2.0), ("failed-step", "failed", 1.0)]
            return .5
        if phase == "editors":   # Two side-by-side node editors before anything is shown or measured.
            if areas() is None:
                return .3
            state["phase"] = "next"
            return .5
        if phase == "next":
            if not ctx["queue"]:
                finish()
                return None
            case, tree_key, scale = ctx["queue"][0]
            if prefs.view.ui_scale != scale:
                prefs.view.ui_scale = scale
                return .6
            if tree_key not in ctx:
                ctx[tree_key] = failed_flow()
            show(ctx[tree_key])
            state["phase"] = "frame"
            return .3
        if phase == "frame":   # View All once both trees are drawn at this scale; never in the capture's tick.
            editors = areas()
            sizes = drawn_sizes(editors)
            if sizes is None or sizes != ctx.get("sizes", (None,))[0]:
                ctx["sizes"] = (sizes, time.monotonic())
                return .2
            if time.monotonic() - ctx["sizes"][1] < SETTLED:
                return .2
            ctx.pop("sizes")
            for area in editors:
                frame(area)
            state["phase"] = "look"
            return .5
        if phase == "look":
            case = ctx["queue"][0][0]
            if not judge(case):
                return .3
            ctx["queue"].pop(0)
            state["phase"] = "next"
            return .3
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=1.0)
