# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A merge's per-placement material swap on parts that are not of a known-safe kind is refused by name before anything
is swapped (``worker_material_reach.unsafe``, break-it P2R6: after six rounds found one more way each time for a swap
to escape the check, a swap is accepted only where everything in its part is proven safe), through the lead chat's real
merge of part files, without workers, leaving every datablock and the scene's record as they were.

Parts are saved as a worker leaves them and merged as an Auto edit: Geometry Nodes setting the material (joined to the
object's own mesh, through the modifier's Material input, a nested group's input and a Material node), a curve and a
text in data slots, a grease pencil drawing, a node that reads the swapped material (Material Selection), a grease
pencil modifier filtering by it, a material Geometry Nodes moves out of the object's slots, a collection instance of
studs another placement owns, an object a node group instances, and instances a node group makes. The plain swap that
merges is proven in ``test_blender_parallel_merge_instances.py`` and ``test_blender_parallel_merge.py``. No provider or
paid call.

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

import bmesh
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-parallel-materials-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, worker_sessions  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import ToolDispatchBudget, pump_controller_tools  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402

report = {"checks": {}, "details": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "platform": sys.platform}
RED = {"name": "Red", "base_color": [0.8, 0.05, 0.05]}
NAMES = {1: "Turned", 2: "Picker", 3: "Sign", 4: "Sketch", 5: "Filtered", 6: "Turned Again", 7: "Scatter",
         8: "Rack", 9: "Holder"}


def check(name, passed, details=None):
    report["checks"][name] = bool(passed)   # Exactly true or false: the native runner reads nothing else.
    report["details"][name] = details


def material(name, colour, pencil=False):
    found = bpy.data.materials.new(name)
    found.diffuse_color = colour
    if pencil:
        bpy.data.materials.create_gpencil_data(found)
    return found


def cube(scene, name, materials, size=1.0):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(mesh)
    bm.free()
    for item in materials:
        mesh.materials.append(item)
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    return obj


def group(name, material_inputs=()):
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    for label in material_inputs:
        tree.interface.new_socket(label, in_out="INPUT", socket_type="NodeSocketMaterial")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    return tree, tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")


def set_material(tree, geometry, value):
    """A Set Material node on ``geometry``: its own material, or the socket ``value`` links in."""
    node = tree.nodes.new("GeometryNodeSetMaterial")
    tree.links.new(geometry, node.inputs["Geometry"])
    if isinstance(value, bpy.types.Material):
        node.inputs["Material"].default_value = value
    else:
        tree.links.new(value, node.inputs["Material"])
    return node


def build_turned(scene):
    """Three ways Geometry Nodes sets Ebony, each on a mesh whose own material is Oak."""
    ebony, oak = material("Ebony", (0.05, 0.03, 0.02, 1)), material("Oak", (0.5, 0.3, 0.1, 1))
    material("Spare", (0.2, 0.2, 0.8, 1)).use_fake_user = True   # In the part, shown by nothing.
    shell = cube(scene, "Shell", [oak])   # A Set Material node; the Join moves Oak off the mesh's own index.
    tree, inputs, output = group("Shell GN")
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    grid = tree.nodes.new("GeometryNodeMeshGrid")
    tree.links.new(inputs.outputs[0], join.inputs[0])   # A Join lists its newest input first: the grid, then Oak.
    tree.links.new(set_material(tree, grid.outputs[0], ebony).outputs[0], join.inputs[0])
    tree.links.new(join.outputs[0], output.inputs[0])
    shell.modifiers.new("GeometryNodes", "NODES").node_group = tree
    trim = cube(scene, "Trim", [oak], size=0.5)   # The modifier's own Material input.
    tree, inputs, output = group("Trim GN", ["Finish"])
    tree.links.new(set_material(tree, inputs.outputs[0], inputs.outputs[1]).outputs[0], output.inputs[0])
    modifier = trim.modifiers.new("GeometryNodes", "NODES")
    modifier.node_group = tree
    modifier.properties.inputs[tree.interface.items_tree["Finish"].identifier]["value"] = ebony
    trim.location = (0, 2, 0)
    inner, inner_in, inner_out = group("Inner GN", ["Finish"])   # Nested: the group node's input, a Material node.
    join = inner.nodes.new("GeometryNodeJoinGeometry")
    cone = inner.nodes.new("GeometryNodeMeshCone")
    picked = inner.nodes.new("GeometryNodeInputMaterial")
    picked.material = ebony
    inner.links.new(set_material(inner, cone.outputs[0], picked.outputs[0]).outputs[0], join.inputs[0])
    inner.links.new(set_material(inner, inner_in.outputs[0], inner_in.outputs[1]).outputs[0], join.inputs[0])
    inner.links.new(join.outputs[0], inner_out.inputs[0])
    outer, outer_in, outer_out = group("Outer GN")
    nested = outer.nodes.new("GeometryNodeGroup")
    nested.node_tree = inner
    nested.inputs[1].default_value = ebony
    outer.links.new(outer_in.outputs[0], nested.inputs[0])
    outer.links.new(nested.outputs[0], outer_out.inputs[0])
    knob = cube(scene, "Knob", [oak], size=0.3)
    knob.location = (0, -2, 0)
    knob.modifiers.new("GeometryNodes", "NODES").node_group = outer
    plain = cube(scene, "Plain", [ebony, oak], size=0.4)   # Slots only: the data's own.
    plain.location = (2, 0, 0)
    plain.data.polygons[0].material_index = 1


def build_picker(scene):
    """Geometry Nodes that reads Ebony: a Material Selection picks Ebony's faces to set Oak."""
    ebony, oak = material("Ebony", (0.05, 0.03, 0.02, 1)), material("Oak", (0.5, 0.3, 0.1, 1))
    obj = cube(scene, "Picker", [ebony])
    tree, inputs, output = group("Picker GN")
    select = tree.nodes.new("GeometryNodeMaterialSelection")
    select.inputs["Material"].default_value = ebony
    node = set_material(tree, inputs.outputs[0], oak)
    tree.links.new(select.outputs[0], node.inputs["Selection"])
    tree.links.new(node.outputs[0], output.inputs[0])
    obj.modifiers.new("GeometryNodes", "NODES").node_group = tree


def build_sign(scene):
    """A text in two materials, extruded, and a bevelled curve: their own slots."""
    ebony, oak = material("Ebony", (0.05, 0.03, 0.02, 1)), material("Oak", (0.5, 0.3, 0.1, 1))
    text = bpy.data.curves.new("Sign", "FONT")
    text.body, text.extrude = "AB", 0.1
    text.materials.append(ebony)
    text.materials.append(oak)
    text.body_format[1].material_index = 1
    scene.collection.objects.link(bpy.data.objects.new("Sign", text))
    rail = bpy.data.curves.new("Rail", "CURVE")
    rail.dimensions, rail.bevel_depth = "3D", 0.05
    spline = rail.splines.new("POLY")
    spline.points.add(2)
    for index, point in enumerate(spline.points):
        point.co = (index, 1, 0, 1)
    rail.materials.append(ebony)
    scene.collection.objects.link(bpy.data.objects.new("Rail", rail))


def pencil(scene, name, materials):
    data = bpy.data.grease_pencils.new(name)
    for item in materials:
        data.materials.append(item)
    drawing = data.layers.new("Lines").frames.new(scene.frame_current).drawing
    drawing.add_strokes([3, 3, 3])
    attribute = drawing.attributes.new("material_index", "INT", "CURVE")
    attribute.data.foreach_set("value", [0, 1, 0])
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    return obj


def build_sketch(scene):
    pencil(scene, "Sketch", [material("Ink", (0, 0, 0, 1), True), material("Chalk", (1, 1, 1, 1), True)])


def build_filtered(scene):
    """A grease pencil modifier that acts on Ink's strokes only: it reads Ink."""
    obj = pencil(scene, "Filtered", [material("Ink", (0, 0, 0, 1), True), material("Chalk", (1, 1, 1, 1), True)])
    modifier = obj.modifiers.new("Thick", "GREASE_PENCIL_THICKNESS")
    modifier.material_filter = obj.data.materials[0]


def build_scatter(scene):
    """Instances (never realized) of a cone a Set Material node sets to Ebony, beside the object's own Oak mesh."""
    ebony = material("Ebony", (0.05, 0.03, 0.02, 1))
    tree, inputs, output = group("Scatter GN")
    cone, scatter = tree.nodes.new("GeometryNodeMeshCone"), tree.nodes.new("GeometryNodeInstanceOnPoints")
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    tree.links.new(set_material(tree, cone.outputs[0], ebony).outputs[0], scatter.inputs["Instance"])
    tree.links.new(inputs.outputs[0], scatter.inputs["Points"])
    tree.links.new(inputs.outputs[0], join.inputs[0])
    tree.links.new(scatter.outputs[0], join.inputs[0])
    tree.links.new(join.outputs[0], output.inputs[0])
    owner = cube(scene, "Scatter", [material("Oak", (0.5, 0.3, 0.1, 1))])
    owner.modifiers.new("GeometryNodes", "NODES").node_group = tree


def build_rack(scene):
    """An empty instancing the part's own collection of Ebony studs: another placement's copy still shows those."""
    studs = bpy.data.collections.new("Studs")
    scene.collection.children.link(studs)
    stud = cube(scene, "Stud", [material("Ebony", (0.05, 0.03, 0.02, 1))], size=0.1)
    scene.collection.objects.unlink(stud)
    studs.objects.link(stud)
    rack = bpy.data.objects.new("Rack", None)
    rack.instance_type, rack.instance_collection = "COLLECTION", studs
    scene.collection.objects.link(rack)


def build_holder(scene):
    """Geometry Nodes taking another object of the part, its slot Ebony, as an instance."""
    gem = cube(scene, "Gem", [material("Ebony", (0.05, 0.03, 0.02, 1))], size=0.2)
    tree, inputs, output = group("Holder GN")
    info, join = tree.nodes.new("GeometryNodeObjectInfo"), tree.nodes.new("GeometryNodeJoinGeometry")
    info.inputs["Object"].default_value, info.inputs["As Instance"].default_value = gem, True
    tree.links.new(inputs.outputs[0], join.inputs[0])
    tree.links.new(info.outputs["Geometry"], join.inputs[0])
    tree.links.new(join.outputs[0], output.inputs[0])
    owner = cube(scene, "Holder", [material("Oak", (0.5, 0.3, 0.1, 1))])
    owner.modifiers.new("GeometryNodes", "NODES").node_group = tree


def instances(obj):
    """[(type, the materials Blender shows in its slots)] of every instance ``obj`` makes when rendered."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return sorted((item.object.type, tuple(getattr(s.material, "name", None) for s in item.object.material_slots))
                  for item in depsgraph.object_instances if item.is_instance and item.parent.original == obj)


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


def every_datablock():
    return sorted((name, item.name) for name in ("objects", "meshes", "materials", "node_groups", "collections",
                                                 "curves", "grease_pencils", "fonts", "libraries")
                  for item in getattr(bpy.data, name))


def shown(obj):
    """What each element of ``obj``'s evaluated result shows, by Blender's evaluated object (the oracle)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    copy = obj.evaluated_get(depsgraph)
    slots = [slot.material for slot in copy.material_slots]

    def at(index):
        found = slots[min(max(index, 0), len(slots) - 1)] if slots else None
        return found.name if found is not None else None

    def indices(attributes, count, domain):
        values = [0] * count
        attribute = attributes.get("material_index")
        if attribute is not None and attribute.domain == domain:
            attribute.data.foreach_get("value", values)
        return [at(i) for i in values]
    geometry = bpy.types.GeometrySet.from_evaluated_object(copy)
    found = []
    if geometry.mesh is not None:
        found += indices(geometry.mesh.attributes, len(geometry.mesh.polygons), "FACE")
    if geometry.grease_pencil is not None:
        for layer in geometry.grease_pencil.layers:
            drawing = layer.current_frame().drawing
            found += indices(drawing.attributes, len(drawing.strokes), "CURVE")
    return found


def copies(name, count):
    return [bpy.data.objects[name if index == 0 else "%s.%03d" % (name, index)] for index in range(count)]


def swapped(oracle, swap):
    return [swap.get(name, name) for name in oracle]


def run_checks():
    folder = OUT / "parts"
    folder.mkdir(parents=True, exist_ok=True)
    builds = {1: build_turned, 2: build_picker, 3: build_sign, 4: build_sketch, 5: build_filtered,
              6: build_turned, 7: build_scatter, 8: build_rack, 9: build_holder}
    paths = {number: write_part(folder / ("%s.blend" % NAMES[number].lower()), build)
             for number, build in builds.items()}

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
    brass = material("Brass", (0.8, 0.6, 0.2, 1))
    brass.use_fake_user = True
    bpy.ops.ed.undo_push(message="Before the merges")

    # Every part here holds Geometry Nodes, a Grease Pencil drawing, a modifier with a material or instancing: none is
    # of a kind a swap can be proven on (``worker_material_reach.unsafe``, break-it P2R6), so each swap is refused by
    # name before anything is swapped, and nothing of the merge stays. The plain per-placement swap is proven in
    # ``test_blender_parallel_merge_instances.py`` and ``test_blender_parallel_merge.py``.
    second = {"location": [3, 0, 0], "materials": {"Ebony": RED}}
    for label, parts, code, says in (
            ("geometry_nodes_setting_the_material", {1: [{}, second, {"location": [6, 0, 0],
                                                                      "materials": {"Ebony": "Brass"}}]},
             "UNSUPPORTED", ("can't be checked", "(Nodes)")),
            ("a_sign", {3: [{}, {"location": [0, 5, 0], "materials": {"Ebony": RED}}]}, "UNSUPPORTED",
             ("can't be checked", " has ")),
            ("a_grease_pencil_drawing", {4: [{}, {"location": [0, -5, 0], "materials": {"Ink": {
                "name": "Red Ink", "base_color": [0.8, 0, 0]}}}]}, "UNSUPPORTED", ("can't be checked", " has ")),
            ("instances_a_node_group_makes", {7: [{}, {"location": [0, 9, 0], "materials": {"Ebony": "Brass"}}]},
             "UNSUPPORTED", ("can't be checked", " has ")),
            ("a_node_reading_the_material", {2: [{}, second]}, "UNSUPPORTED", ("can't be checked", " has ")),
            ("a_modifier_filtering_by_it", {4: [{}], 5: [{"materials": {"Ink": RED}}]}, "UNSUPPORTED",
             ("can't be checked", " has ")),
            ("a_material_geometry_nodes_moves_out_of_its_slot", {6: [{}, {"location": [3, 0, 0],
                                                                          "materials": {"Oak": "Brass"}}]},
             "UNSUPPORTED", ("can't be checked", "(Nodes)")),
            ("a_collection_another_placement_owns", {8: [{}, second]}, "UNSUPPORTED", ("can't be checked", " has ")),
            ("an_object_a_node_group_instances", {9: [{}, second]}, "UNSUPPORTED", ("can't be checked", " has "))):
        before = every_datablock()
        from angelo_agent.parallel_sessions import PART_KEY
        record = dict(window.scene.get(PART_KEY) or {})   # A refusal puts back the record the append wrote (R4-L-1).
        answer = lead_merge(window, lead, parts)
        message = answer.get("error", "")
        check("refused_" + label, answer.get("error_code") == code and all(word in message for word in says)
              and answer.get("scene_changed") is False and not answer.get("recovery_required"), answer)
        check("refused_" + label + "_leaves_every_datablock", every_datablock() == before,
              sorted(set(every_datablock()) ^ set(before)))
        check("refused_" + label + "_leaves_the_record", dict(window.scene.get(PART_KEY) or {}) == record,
              sorted(window.scene.get(PART_KEY) or {}))

    report["success"] = all(report["checks"].values())


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(OUT / "parallel-merge-materials.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_PARALLEL_MERGE_MATERIALS", json.dumps({"success": report.get("success"),
                                                    "failed": [k for k, v in report["checks"].items()
                                                               if not v]}))
sys.exit(0 if report.get("success") else 1)
