# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A merge's per-placement material swap is checked on everything a placement shows, its instances included
(``worker_material_check``, maintainer audit P2: the second placement's own object turned red while its collection
instance kept the part's material, and the merge said it succeeded), through the lead chat's real merge of part files,
without workers. The neighbour ``test_blender_parallel_merge_materials.py`` covers slots, modifiers and node groups.

Parts (``parallel_instance_parts``), most with Geometry Nodes setting the part's material over a mesh whose own slot
holds another: an empty instancing the part's collection (also with the material in the instanced object's own slot, and
the empty disabled in viewports); an empty instancing a collection whose empty instances another (both collections
excluded in the worker's view layer, so only the instancer is evaluated); a kit-bash part of empties three levels deep;
collection instances nested deeper than Blender's limit; a hair particle system shown as strands in viewports (so only
the check's own particle reader sees its instances), and one with no particles; Geometry Nodes that bring the part's
collection in for renders only (Is Viewport, a modifier off in viewports); a Boolean whose Collection operand holds the
part's cutter (also off in viewports and renders); a mesh and an instancer both showing Ebony in slots (finding 5);
Geometry Nodes reading another object's location; particle members picked by count; a slot renders evaluate otherwise; a
mesh whose Geometry Nodes show Oak in viewports and Ebony in renders, instanced by a collection; a modifier off in both
that reads Ebony; a render-only group passing its input through; the render-only source again through a Material input
and realized through Object Info; a driven render switch; Subdivision levels by mode feeding a material switch, and a
driver on the depsgraph mode (P2R5). Merged: a swap every placement makes (Geometry Nodes or slot), a kit-bash one, a
nested one, one placement owning its instancer, and every placement swapping a render-only node material show the swap
in the instances (read here from Blender's own instance list, independently of the merge's code); a particle system with
no particles, a member weighted zero, a disabled Boolean operand (break-it P2R2 M-1, L-1), a material read off in both
modes, a render-only pass-through (P2R3 M-1, M-2) and a driven switch within its own placement refuse nothing. Refused,
naming what shows the part's material, and leaving every datablock and the scene's record as they were: a second
placement's collection instance of the first placement's objects (the audit; finding 5 in slots), the same nested two
collections deep, a first placement's swap reaching an unswapped placement (an instance, a render-only instancer, a
Boolean operand: break-it P2 M2, M3), a particle instance of the first placement's object, content only renders show
(M1), a render-only instancer of another placement (M2), a shared collection its placements swap differently, a use of a
swapped object by an unswapped placement, and every shared source whose renders differ (P2R3 H-1, P2R4 H-1, H-2, P2R5
H-1, H-2): any swap reaching, or reached by, another placement that does not make the same swap; a slot swap on a copy
whose material list evaluation builds; and instancing deeper than the check reads (unchecked, so never passed). No
provider or paid call.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-parallel-instances-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, worker_sessions  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import ToolDispatchBudget, pump_controller_tools  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from parallel_instance_parts import BUILDS, cube, material  # noqa: E402

report = {"checks": {}, "details": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "platform": sys.platform}
RED = {"name": "Red", "base_color": [0.8, 0.05, 0.05]}
NAMES = {1: "Display", 2: "Frame", 3: "Tower", 4: "Field", 5: "Prop", 6: "Lamp", 7: "Hidden", 8: "Body", 9: "Studs",
         10: "Kit", 11: "Meadow", 12: "Plank", 13: "Offset", 14: "Unused", 15: "Lawn", 16: "Grove", 17: "Slab",
         18: "Leak", 19: "Reader", 20: "Pass", 21: "Inlet", 22: "Realized", 23: "Driven",
         24: "Counted", 25: "Moded", 26: "Plain", 27: "Observer", 28: "Sown", 29: "Escaped", 30: "Reactive"}
BLUE = {"name": "Blue", "base_color": [0.05, 0.05, 0.8]}


def check(name, passed, details=None):
    report["checks"][name] = bool(passed)   # Exactly true or false: the native runner reads nothing else.
    report["details"][name] = details


def faces(evaluated):
    """The material each face of an evaluated object shows, by Blender's own evaluated slots (the oracle)."""
    slots = [slot.material for slot in evaluated.material_slots]
    geometry = bpy.types.GeometrySet.from_evaluated_object(evaluated)
    if geometry.mesh is None:
        return []
    values = [0] * len(geometry.mesh.polygons)
    attribute = geometry.mesh.attributes.get("material_index")
    if attribute is not None and attribute.domain == "FACE":
        attribute.data.foreach_get("value", values)
    return [getattr(slots[min(max(i, 0), len(slots) - 1)] if slots else None, "name", None) for i in values]


def instance_faces(obj):
    """What every instance ``obj`` makes shows, face by face, from Blender's own instance list (nested included)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    found = []
    for item in depsgraph.object_instances:
        if item.is_instance and item.parent is not None and item.parent.original == obj and item.object.type == "MESH":
            found += faces(item.object)
    return found


def write_part(path, build):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    build(bpy.context.scene)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    return path


def lead_merge(window, lead, parts, timeout=120):
    """The lead merges ``parts`` ({number: placements}) as an Auto edit; its answer."""
    answers = []
    arguments = {"parts": [{"worker": number, "placements": placements} for number, placements in parts.items()]}
    lead.request_tool("call-%d" % time.monotonic_ns(), "angelo_workers_merge", arguments, answers.append)

    def apply_mutation(target, call):
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])
    started = time.perf_counter()
    while not answers and time.perf_counter() - started < timeout:
        lead.pump_events()
        pump_controller_tools([lead], 0, BlenderExecutor(window), ui.document_key(), apply_mutation,
                              budget=ToolDispatchBudget(seconds=60))
    assert answers, "the merge was not answered"
    lead.close_external_read(lead.fence)
    lead.fence = lead.open_external_read()
    return answers[0]


def merged_faces(window, lead, parts):
    """Merge ``parts``; (answer, {merged object: its own faces then its instances' faces}), taken back if merged."""
    answer = lead_merge(window, lead, parts)
    depsgraph, shown = bpy.context.evaluated_depsgraph_get(), {}
    if answer.get("success"):
        for obj in bpy.context.scene.collection.children[-1].all_objects:
            shown[obj.name] = (faces(obj.evaluated_get(depsgraph)) if obj.type == "MESH" else []) + instance_faces(obj)
        bpy.ops.ed.undo()
    return answer, shown


def every_datablock():
    return sorted((name, item.name) for name in ("objects", "meshes", "materials", "node_groups", "collections",
                                                 "particles", "libraries")
                  for item in getattr(bpy.data, name))


def merged_and_undone(window, lead, parts):
    """Merge ``parts``, then take it back with one Undo; (answer, {top object: its instances' faces} as merged)."""
    names = sorted(obj.name for obj in bpy.context.scene.objects)
    answer = lead_merge(window, lead, parts)
    shown = {obj.name: instance_faces(obj) for obj in bpy.context.scene.objects
             if obj.instance_type == "COLLECTION" and obj.users_collection
             and obj.users_collection[0] == bpy.context.scene.collection.children[-1]}
    bpy.ops.ed.undo()
    return answer, shown, sorted(obj.name for obj in bpy.context.scene.objects) == names


def run_checks():
    folder = OUT / "parts"
    folder.mkdir(parents=True, exist_ok=True)
    paths = {number: write_part(folder / ("%s.blend" % NAMES[number].lower()), build)
             for number, build in BUILDS.items()}

    bpy.ops.wm.read_factory_settings()
    bpy.context.preferences.edit.use_global_undo = True
    _register(bpy, ui)
    window = bpy.context.window_manager.windows[0]
    lead = ui.get_controller()
    lead.state["options"] = dict(lead.state.get("options") or {}, approval="AUTO_BOUNDED")
    lead.set_document(ui.document_key())
    lead.fence = lead.open_external_read()
    worker_sessions._started[lead.state["id"]] = []
    for number, path in paths.items():
        worker_sessions._parts["qa-%d" % number] = (number, NAMES[number], Path(path))
        worker_sessions._started[lead.state["id"]].append("qa-%d" % number)
    # The Frame and Kit workers excluded their instanced collections in their view layers, as a worker reports.
    worker_sessions._layouts["qa-2"] = {"collections": ["Outer", "Inner"], "objects": ["Frame"], "truncated": False,
                                        "view": {"excluded": ["Outer", "Inner"], "hidden": [], "objects": []}}
    kit = ["L0", "L1", "L2", "L3"]
    worker_sessions._layouts["qa-10"] = {"collections": kit, "objects": ["Top"], "truncated": False,
                                         "view": {"excluded": kit, "hidden": [], "objects": []}}
    bpy.ops.ed.undo_push(message="Before the merges")

    from angelo_agent.parallel_sessions import PART_KEY
    second = {"location": [0, 4, 0], "materials": {"Ebony": RED}}
    first = [{"materials": {"Ebony": RED}}, {"location": [0, 4, 0]}]
    both = [{"materials": {"Ebony": RED}}, {"location": [0, 4, 0], "materials": {"Ebony": RED}}]
    # Merged: a plain part (meshes with material-keeping modifiers, an empty parent, a light) swaps per placement: the
    # swapping placement's copies show the swap where Ebony was, the other's keep Ebony; every placement alike too.
    # {object: (materials its faces show, at least this many faces)}; "*" is the swap's variant.
    plain = {"Body": ({"Oak", "*"}, 6), "Panel": ({"*"}, 6)}
    kept = {"Body.001": ({"Oak", "Ebony"}, 6), "Panel.001": ({"Ebony"}, 6)}
    for label, placements, keeps in (
            ("a_plain_part_swapped_by_its_first_placement", first, dict(plain, **kept)),
            ("a_plain_part_swapped_by_its_second_placement", [{}, second],
             {"Body": ({"Oak", "Ebony"}, 6), "Panel": ({"Ebony"}, 6), "Body.001": ({"Oak", "*"}, 6),
              "Panel.001": ({"*"}, 6)}),
            ("a_plain_part_every_placement_swaps", both, dict(plain, **{"Body.001": ({"Oak", "*"}, 6),
                                                                      "Panel.001": ({"*"}, 6)}))):
        answer, shown = merged_faces(window, lead, {26: placements})
        red = [variant["material"] for entry in answer.get("merged") or () for variant in entry.get("variants", ())]
        check(label + "_merges", answer.get("success") is True and len(red) == 1, answer)
        check(label + "_and_each_placement_shows_what_it_should", bool(red) and all(
            len(shown.get(name) or ()) >= least and set(shown[name]) == {red[0] if wood == "*" else wood
                                                                         for wood in woods}
            for name, (woods, least) in keeps.items()), shown)

    # Refused by name before anything is swapped, nothing of the merge staying: every part holding anything not of a
    # known-safe kind (break-it P2 through P2R6: instancing, particles, Geometry Nodes, a Boolean, drivers, a driven
    # particle system, a driver variable reading the swapped object, an escaped modifier name).
    swapped = [{"materials": {"Ebony": RED}}]
    NEW = {"27", "28", "29", "30"}   # The P2R6 and P2R7 fixtures (by case order), named by what refuses them.
    cases = [("case_%02d_%s" % (order, NAMES[number].lower()), {number: placements}, says)
             for order, (number, placements, says) in enumerate((
                 (1, [{}, second], "instancing"), (1, both, "instancing"), (2, swapped, "instancing"),
                 (3, swapped, "instancing"), (4, [{}, second], "particle system"), (5, first, "modifier"),
                 (6, [{}, second], "modifier"), (7, first, "instancing"), (8, first, "modifier 'Cut' (Boolean)"),
                 (9, both, "instancing"), (10, both, "instancing"), (11, [{}, second], "particle system"),
                 (12, [{}, second], "instancing"), (12, swapped, "instancing"), (13, first, "modifier"),
                 (14, first, "modifier 'Cut' (Boolean)"), (15, [{}, second], "particle system"),
                 (16, [{}, second], "particle system"), (17, swapped, "modifier"), (18, both, "modifier"),
                 (19, swapped, "modifier"), (20, swapped, "modifier"), (21, [{}, second], "modifier"),
                 (22, [{}, second], "modifier"), (23, swapped, "drivers or animation"), (24, first, "modifier"),
                 (25, first, "modifier"), (27, first, "drivers on its object"),
                 (28, first, "particle system 'Hair'"), (29, swapped, "drivers on its object"),
                 (30, [{"materials": {"Ebony": RED}}, {"location": [0, 4, 0], "materials": {"Ebony": "Reactive"}}],
                  "drivers on material 'Reactive'")))]
    for label, parts, says in cases:
        says = ("can't be checked", " has ", says) if label.split("_")[1] in NEW else ("can't be checked", " has ")
        code = "UNSUPPORTED"
        before = every_datablock()
        record = dict(window.scene.get(PART_KEY) or {})   # A refusal puts back the record the append wrote.
        answer = lead_merge(window, lead, parts)
        message = answer.get("error", "")
        check("refused_" + label, answer.get("error_code") == code and all(word in message for word in says)
              and answer.get("scene_changed") is False and not answer.get("recovery_required"), answer)
        check("refused_" + label + "_leaves_every_datablock", every_datablock() == before,
              sorted(set(every_datablock()) ^ set(before)))
        check("refused_" + label + "_leaves_the_record", dict(window.scene.get(PART_KEY) or {}) == record,
              sorted(window.scene.get(PART_KEY) or {}))
        if answer.get("success"):   # Wrongly merged: taken back, so each case is judged on the lead's own scene.
            bpy.ops.ed.undo()

    # A driver already in the lead's scene reads the incoming Body by path through the Scene (break-it P2R7 H-2): the
    # plain part's swap is refused by name, and nothing of the merge stays; without that driver it merges (above).
    scene = window.scene
    observer = cube("Observer", [material("Oak", (0.5, 0.3, 0.1, 1)), material("Gold", (0.8, 0.6, 0.1, 1))],
                    scene.collection)
    observer.modifiers.new("Shell", "SOLIDIFY")
    driver = observer.driver_add('modifiers["Shell"].material_offset').driver
    target = driver.variables.new().targets[0]
    target.id_type, target.id, target.data_path = "SCENE", scene, 'objects["Body"].active_material.diffuse_color[0]'
    driver.expression = "0"
    before, record = every_datablock(), dict(scene.get(PART_KEY) or {})
    answer = lead_merge(window, lead, {26: [{"materials": {"Ebony": RED}}]})
    check("refused_a_scene_driver_reading_the_incoming_object", answer.get("error_code") == "UNSUPPORTED" and all(
        word in answer.get("error", "") for word in ("can't be checked", "Observer", "objects[\"Body\"]"))
          and answer.get("scene_changed") is False, answer)
    check("refused_a_scene_driver_reading_the_incoming_object_leaves_every_datablock", every_datablock() == before,
          sorted(set(every_datablock()) ^ set(before)))
    check("refused_a_scene_driver_reading_the_incoming_object_leaves_the_record",
          dict(scene.get(PART_KEY) or {}) == record, sorted(scene.get(PART_KEY) or {}))
    if answer.get("success"):
        bpy.ops.ed.undo()
    if bpy.data.objects.get("Observer") is not None:   # By name: an Undo reloads every datablock.
        bpy.data.objects.remove(bpy.data.objects["Observer"])

    # A Line Art already in the scene reads every mesh's materials by scope (break-it P2R8 H-1: its strokes changed
    # once an incoming mesh's material did): the plain part's swap is refused by name, nothing of the merge staying.
    pencil = bpy.data.objects.new("Lines", bpy.data.grease_pencils.new("Lines"))
    scene.collection.objects.link(pencil)
    layer = pencil.data.layers.new("Lines")
    layer.frames.new(scene.frame_current)
    ink = material("Ink", (0, 0, 0, 1))
    bpy.data.materials.create_gpencil_data(ink)
    pencil.data.materials.append(ink)
    lines = pencil.modifiers.new("Line Art", "LINEART")   # The probe saw its strokes change with the swap (2 to 3).
    lines.source_type, lines.target_layer, lines.target_material = "SCENE", layer.name, ink
    before, record = every_datablock(), dict(scene.get(PART_KEY) or {})
    answer = lead_merge(window, lead, {26: [{"materials": {"Ebony": RED}}]})
    check("refused_a_scene_line_art", answer.get("error_code") == "UNSUPPORTED" and all(
        word in answer.get("error", "") for word in ("can't be checked", "Lines", "Line Art"))
          and answer.get("scene_changed") is False, answer)
    check("refused_a_scene_line_art_leaves_every_datablock", every_datablock() == before,
          sorted(set(every_datablock()) ^ set(before)))
    check("refused_a_scene_line_art_leaves_the_record", dict(scene.get(PART_KEY) or {}) == record,
          sorted(scene.get(PART_KEY) or {}))
    if answer.get("success"):
        bpy.ops.ed.undo()
    if bpy.data.objects.get("Lines") is not None:
        bpy.data.objects.remove(bpy.data.objects["Lines"])

    # A consumer in another scene of the lead scene's root collection, or the lead scene as another scene's
    # background set, shows whatever the merge links there (break-it P2R9 H-1: the probe saw it turn Red): refused by
    # name, walking Blender's user map up the containment chain.
    for label, how in (("an_instancer_of_the_root_collection", "instancer"), ("a_background_set", "background")):
        for name in ("Observer", "Lines"):   # An Undo above may bring back the cases' objects.
            if bpy.data.objects.get(name) is not None:
                bpy.data.objects.remove(bpy.data.objects[name])
        scene = window.scene
        other = bpy.data.scenes.new("Other")
        if how == "instancer":
            viewer = bpy.data.objects.new("Viewer", None)
            other.collection.objects.link(viewer)
            viewer.instance_type, viewer.instance_collection = "COLLECTION", scene.collection
        else:
            other.background_set = scene
        before, record = every_datablock(), dict(scene.get(PART_KEY) or {})
        answer = lead_merge(window, lead, {26: [{"materials": {"Ebony": RED}}]})
        check("refused_" + label, answer.get("error_code") == "UNSUPPORTED" and all(
            word in answer.get("error", "") for word in ("can't be checked", "Viewer" if how == "instancer" else
                                                         "Scene 'Other'")) and answer.get("scene_changed") is False,
              answer)
        check("refused_%s_leaves_every_datablock" % label, every_datablock() == before,
              sorted(set(every_datablock()) ^ set(before)))
        check("refused_%s_leaves_the_record" % label, dict(window.scene.get(PART_KEY) or {}) == record,
              sorted(window.scene.get(PART_KEY) or {}))
        if answer.get("success"):
            bpy.ops.ed.undo()
        for name in ("Viewer",):
            if bpy.data.objects.get(name) is not None:
                bpy.data.objects.remove(bpy.data.objects[name])
        if bpy.data.scenes.get("Other") is not None:
            bpy.data.scenes.remove(bpy.data.scenes["Other"])

    # Renders of the scene that do not show each slot's material the standard way (break-it P2R10, P2R11): a view
    # layer's material override, a Material Index pass, Workbench's Single color, an image-wide compositor node. Each
    # refuses by name; nothing of the merge stays; the plain swaps above ran in the default EEVEE scene.
    for label, words in (("a_view_layer_material_override", "material override"),
                         ("a_material_index_pass_masked_in_the_compositor", "Material Index pass"),
                         ("workbench_single_color", "Workbench coloured by single"),
                         ("an_image_wide_compositor_node", "Normalize")):
        for name in ("Observer", "Lines", "Viewer"):   # An Undo above may bring back the cases' objects and scenes.
            if bpy.data.objects.get(name) is not None:
                bpy.data.objects.remove(bpy.data.objects[name])
        if bpy.data.scenes.get("Other") is not None:
            bpy.data.scenes.remove(bpy.data.scenes["Other"])
        scene = window.scene
        layer = window.view_layer
        engine = scene.render.engine   # The default scene's, put back below.
        if label.startswith("a_view_layer"):
            layer.material_override = material("Clay", (0.6, 0.6, 0.6, 1))
        elif label.startswith("a_material_index"):
            layer.use_pass_material_index = True
        elif label.startswith("workbench"):   # Renders show one grey, never a material (break-it P2R11 M-1).
            scene.render.engine, scene.display.shading.color_type = "BLENDER_WORKBENCH", "SINGLE"
        else:   # Normalize spreads an incoming mesh's brightness over every pixel (P2R11 H-1).
            tree = bpy.data.node_groups.new("Compositing", "CompositorNodeTree")
            tree.nodes.new("CompositorNodeRLayers").scene = None   # The current scene, implicitly: no reference.
            tree.nodes.new("CompositorNodeNormalize")
            scene.compositing_node_group, scene.render.use_compositing = tree, True
        before, record = every_datablock(), dict(scene.get(PART_KEY) or {})
        answer = lead_merge(window, lead, {26: [{"materials": {"Ebony": RED}}]})
        check("refused_" + label, answer.get("error_code") == "UNSUPPORTED" and all(
            word in answer.get("error", "") for word in ("can't be checked", "renders that use", words))
              and answer.get("scene_changed") is False, answer)
        check("refused_%s_leaves_every_datablock" % label, every_datablock() == before,
              sorted(set(every_datablock()) ^ set(before)))
        check("refused_%s_leaves_the_record" % label, dict(window.scene.get(PART_KEY) or {}) == record,
              sorted(window.scene.get(PART_KEY) or {}))
        if answer.get("success"):
            bpy.ops.ed.undo()
        window.view_layer.material_override, window.view_layer.use_pass_material_index = None, False
        window.scene.render.engine, window.scene.compositing_node_group = engine, None

    # A compositor of only pixel-wise nodes (Render Layers of this scene, Color Balance, the output) is the standard
    # way: the plain swap merges.
    tree = bpy.data.node_groups.new("Compositing", "CompositorNodeTree")
    tree.nodes.new("CompositorNodeRLayers").scene = window.scene
    tree.nodes.new("CompositorNodeColorBalance")
    window.scene.compositing_node_group, window.scene.render.use_compositing = tree, True
    answer = lead_merge(window, lead, {26: [{"materials": {"Ebony": RED}}]})
    check("a_pixel_wise_compositor_merges", answer.get("success") is True, answer)
    if answer.get("success"):
        bpy.ops.ed.undo()
    window.scene.compositing_node_group = None

    # The same pixel-wise compositor shared with another scene (break-it P2R12 H-1), and a Sequencer strip the render
    # goes through (P2R12 M-1): each refuses by name, nothing of the merge staying.
    for label, words in (("a_compositor_shared_with_another_scene", "CompositorNodeTree"),
                         ("a_sequencer_strip", "Sequencer with strips")):
        scene = window.scene
        if label.startswith("a_compositor"):
            tree = bpy.data.node_groups.new("Shared", "CompositorNodeTree")
            tree.nodes.new("CompositorNodeRLayers").scene = scene
            tree.nodes.new("CompositorNodeColorBalance")
            scene.compositing_node_group, scene.render.use_compositing = tree, True
            bpy.data.scenes.new("Other").compositing_node_group = tree
        else:
            scene.sequence_editor_create()
            strips = scene.sequence_editor.strips
            strips.new_effect(name="Color", type="COLOR", channel=1, frame_start=1, length=10)
            scene.render.use_sequencer = True
        before, record = every_datablock(), dict(scene.get(PART_KEY) or {})
        answer = lead_merge(window, lead, {26: [{"materials": {"Ebony": RED}}]})
        check("refused_" + label, answer.get("error_code") == "UNSUPPORTED" and all(
            word in answer.get("error", "") for word in ("can't be checked", words))
              and answer.get("scene_changed") is False, answer)
        check("refused_%s_leaves_every_datablock" % label, every_datablock() == before,
              sorted(set(every_datablock()) ^ set(before)))
        check("refused_%s_leaves_the_record" % label, dict(window.scene.get(PART_KEY) or {}) == record,
              sorted(window.scene.get(PART_KEY) or {}))
        if answer.get("success"):
            bpy.ops.ed.undo()
        window.scene.compositing_node_group = None
        if bpy.data.scenes.get("Other") is not None:
            bpy.data.scenes.remove(bpy.data.scenes["Other"])
        if window.scene.sequence_editor is not None:
            window.scene.sequence_editor_clear()

    # An object already in the file using an incoming one (an Array offset on the incoming Body): Blender's user map
    # finds it, and the swap is refused by name (the swap pipeline directly: no merge can make such a user before its
    # own append, short of a name resolved at run time, so the file's "before" is set by hand here).
    from angelo_agent.refusal_codes import RefusalError
    from angelo_agent.worker_material_check import ids
    from angelo_agent.worker_material_swaps import Swaps
    ebony, red = material("Ebony", (0.05, 0.03, 0.02, 1)), material("Red", (0.8, 0.05, 0.05, 1))
    scene = window.scene   # Again: an Undo above reloads every datablock (and may bring back the cases' objects).
    for name in ("Observer", "Lines"):
        if bpy.data.objects.get(name) is not None:
            bpy.data.objects.remove(bpy.data.objects[name])
    incoming = bpy.data.collections.new("Incoming")   # As a merge links a part: into a collection of its own.
    scene.collection.children.link(incoming)
    body = cube("Body", [ebony], incoming)
    added = {block.as_pointer() for block in (body, body.data, ebony, red, incoming)}
    user = cube("User", [], scene.collection)
    user.modifiers.new("Repeat", "ARRAY").offset_object = body
    item = {"renamed": {}, "part": {"worker": 31, "placements": [{"materials": {"Ebony": "Red"}}]},
            "instances": [{body: body}], "materials": {"Ebony": ebony, "Red": red}}
    try:
        Swaps([item], window.view_layer, ids() - added)
        found = None
    except RefusalError as error:
        found = error
    check("refused_an_existing_object_using_an_incoming_one", found is not None and found.code == "UNSUPPORTED"
          and all(word in str(found) for word in ("can't be checked", "User", "Body")), str(found))
    for leftover in [user] + [obj for obj in (bpy.data.objects.get("Body"),) if obj is not None]:
        bpy.data.objects.remove(leftover)
    if bpy.data.collections.get("Incoming") is not None:
        bpy.data.collections.remove(bpy.data.collections["Incoming"])

    report["success"] = all(report["checks"].values())


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(OUT / "parallel-merge-instances.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_PARALLEL_MERGE_INSTANCES", json.dumps({"success": report.get("success"),
                                                    "failed": [k for k, v in report["checks"].items()
                                                               if not v]}))
sys.exit(0 if report.get("success") else 1)
