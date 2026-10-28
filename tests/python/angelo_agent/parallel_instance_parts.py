# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The parts ``test_blender_parallel_merge_instances.py`` merges, each built in an empty scene and saved as a worker's
part file (``BUILDS``: part number -> builder). Runs inside Blender only."""

import bmesh
import bpy

from angelo_agent.worker_material_check import DEPTH

LEVELS = DEPTH + 1   # Collection instances nested in the Tower part: one more than the check reads.
BRANCH = 4           # Empties per level of the Kit part (break-it P2 M4's kit-bash shape, small).


def material(name, colour):
    found = bpy.data.materials.new(name)
    found.diffuse_color = colour
    return found


def cube(name, slots, collection, size=0.5):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(mesh)
    bm.free()
    for item in slots:
        mesh.materials.append(item)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def tree(name):
    found = bpy.data.node_groups.new(name, "GeometryNodeTree")
    found.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    found.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    return found, found.nodes.new("NodeGroupInput"), found.nodes.new("NodeGroupOutput")


def gn_mesh(name, own, shows, collection):
    """A cube whose own slot is ``own`` and whose Geometry Nodes set ``shows`` on all of it."""
    obj = cube(name, [own], collection)
    group, inputs, output = tree(name + " GN")
    node = group.nodes.new("GeometryNodeSetMaterial")
    node.inputs["Material"].default_value = shows
    group.links.new(inputs.outputs[0], node.inputs["Geometry"])
    group.links.new(node.outputs[0], output.inputs[0])
    obj.modifiers.new("GeometryNodes", "NODES").node_group = group
    return obj


def woods():
    return material("Ebony", (0.05, 0.03, 0.02, 1)), material("Oak", (0.5, 0.3, 0.1, 1))


def holder(scene, name):
    found = bpy.data.collections.new(name)
    scene.collection.children.link(found)
    return found


def instancer(name, collection, home):
    obj = bpy.data.objects.new(name, None)
    obj.instance_type, obj.instance_collection = "COLLECTION", collection
    home.objects.link(obj)
    return obj


def build_display(scene, hidden=False, slot=False):
    """The audit's part: an empty instancing the part's collection of Geometry Nodes geometry (``slot``: the
    material in the instanced object's own slot instead; ``hidden``: the empty disabled in viewports)."""
    ebony, oak = woods()
    source = holder(scene, "Source")
    cube("Tile", [ebony], source) if slot else gn_mesh("Tile", oak, ebony, source)
    showcase = instancer("Showcase", source, scene.collection)
    showcase.location, showcase.hide_viewport = (2, 0, 0), hidden


def build_frame(scene):
    """An empty instancing Outer, whose empty instances Inner, whose mesh Geometry Nodes sets to Ebony."""
    ebony, oak = woods()
    gn_mesh("Bolt", oak, ebony, holder(scene, "Inner"))
    instancer("Mount", bpy.data.collections["Inner"], holder(scene, "Outer"))
    instancer("Frame", bpy.data.collections["Outer"], scene.collection)


def build_tower(scene):
    """Collection instances nested ``LEVELS`` deep, the last holding a mesh Geometry Nodes sets to Ebony."""
    ebony, oak = woods()
    levels = [holder(scene, "Level %d" % number) for number in range(1, LEVELS + 1)]
    for number, level in enumerate(levels[:-1], start=1):
        instancer("Step %d" % number, levels[number], level)
    gn_mesh("Core", oak, ebony, levels[-1])
    instancer("Tower", levels[0], scene.collection)


def hair(field, blade, count=4, display="RENDER"):
    field.modifiers.new("Hair", "PARTICLE_SYSTEM")
    settings = field.particle_systems[-1].settings
    settings.type, settings.count, settings.render_type, settings.instance_object = "HAIR", count, "OBJECT", blade
    settings.display_method = display


def build_field(scene):
    """A hair particle system, shown as strands in viewports, instancing another object Geometry Nodes sets to Ebony."""
    ebony, oak = woods()
    hair(cube("Field", [oak], scene.collection), gn_mesh("Blade", oak, ebony, scene.collection), display="PATH")


def build_meadow(scene):
    """A particle system with no particles, instancing another object whose own slot is Ebony: it shows nothing."""
    ebony, oak = woods()
    hair(cube("Field", [oak], scene.collection), cube("Blade", [ebony], scene.collection), count=0)


def build_prop(scene, render_only):
    """Source's Tile (Geometry Nodes set Ebony), and Prop, whose Geometry Nodes show Source in renders only: a
    switch on Is Viewport, or (``render_only``) a modifier off in viewports."""
    ebony, oak = woods()
    source = holder(scene, "Source")
    gn_mesh("Tile", oak, ebony, source)
    prop = cube("Prop", [oak], scene.collection)
    prop.location = (3, 0, 0)
    group, inputs, output = tree("Prop GN")
    info = group.nodes.new("GeometryNodeCollectionInfo")
    info.inputs["Collection"].default_value = source
    if render_only:
        group.links.new(info.outputs[0], output.inputs[0])
    else:
        switch = group.nodes.new("GeometryNodeSwitch")
        switch.input_type = "GEOMETRY"
        group.links.new(group.nodes.new("GeometryNodeIsViewport").outputs[0], switch.inputs[0])
        group.links.new(info.outputs[0], switch.inputs[1])     # Off: a render shows the collection.
        group.links.new(inputs.outputs[0], switch.inputs[2])   # On: the viewport shows the cube.
        group.links.new(switch.outputs[0], output.inputs[0])
    modifier = prop.modifiers.new("Detail", "NODES")
    modifier.node_group, modifier.show_viewport = group, not render_only


def build_body(scene):
    """Body (slots Oak, Ebony) cut by a Boolean whose Collection operand holds an Ebony cutter (materials by index)."""
    ebony, oak = woods()
    cutters = holder(scene, "Cutters")
    cutter = cube("Cutter", [ebony], cutters, size=0.8)
    cutter.location, cutter.scale = (0.5, 0, 0.5), (1, 40, 1)   # A groove along Y, through every placement.
    body = cube("Body", [oak, ebony], scene.collection, size=1.0)
    modifier = body.modifiers.new("Cut", "BOOLEAN")
    modifier.operation, modifier.operand_type, modifier.collection = "DIFFERENCE", "COLLECTION", cutters
    modifier.solver, modifier.material_mode = "EXACT", "INDEX"


def build_plank(scene):
    """The audit's shape in slots: Plank (Ebony) and Showcase, an empty instancing Source, whose Tile is Ebony."""
    ebony, _oak = woods()
    cube("Plank", [ebony], scene.collection).location = (-2, 0, 0)
    cube("Tile", [ebony], holder(scene, "Source"))
    instancer("Showcase", bpy.data.collections["Source"], scene.collection).location = (2, 0, 0)


def build_offset(scene):
    """Body (Oak), whose Geometry Nodes move it by Plate's location (Object Info), and Plate (Ebony): a use of another
    object that never shows its materials."""
    ebony, oak = woods()
    body = cube("Body", [oak], scene.collection)
    plate = cube("Plate", [ebony], scene.collection)
    plate.location = (3, 0, 0)
    group, inputs, output = tree("Offset GN")
    info, move = group.nodes.new("GeometryNodeObjectInfo"), group.nodes.new("GeometryNodeTransform")
    info.inputs["Object"].default_value = plate
    group.links.new(inputs.outputs[0], move.inputs["Geometry"])
    group.links.new(info.outputs["Location"], move.inputs["Translation"])
    group.links.new(move.outputs[0], output.inputs[0])
    body.modifiers.new("Offset", "NODES").node_group = group


def build_unused(scene):
    """The Body part with its Boolean off in viewports and renders: its operand changes nothing shown."""
    build_body(scene)
    modifier = bpy.data.objects["Body"].modifiers["Cut"]
    modifier.show_viewport = modifier.show_render = False


def build_lawn(scene, whole):
    """A hair system instancing Members (A in Oak, B in Ebony) by counts, B's count zero; ``whole``: the whole
    collection per particle, which ignores counts."""
    ebony, oak = woods()
    members = holder(scene, "Members")
    cube("A", [oak], members, size=0.2)
    cube("B", [ebony], members, size=0.2).location = (0, 2, 0)
    (field := cube("Field", [oak], scene.collection)).modifiers.new("Hair", "PARTICLE_SYSTEM")
    settings = field.particle_systems[-1].settings
    settings.type, settings.count, settings.render_type = "HAIR", 6, "COLLECTION"
    settings.instance_collection, settings.use_collection_count, settings.use_whole_collection = members, True, whole
    bpy.context.view_layer.update()   # Blender lists a count per member.
    for weight in settings.instance_weights:
        weight.count = 0 if weight.name.startswith("B:") else 1


def build_slab(scene):
    """Body (its own slot Ebony), whose Geometry Nodes show Plate (Oak) in renders only (Is Viewport): the viewport
    evaluates Ebony in Body's slot, a render Oak."""
    ebony, oak = woods()
    body = cube("Body", [ebony], scene.collection)
    plate = cube("Plate", [oak], scene.collection)
    plate.location = (3, 0, 0)
    group, inputs, output = tree("Slab GN")
    info, switch = group.nodes.new("GeometryNodeObjectInfo"), group.nodes.new("GeometryNodeSwitch")
    info.inputs["Object"].default_value = plate
    switch.input_type = "GEOMETRY"
    group.links.new(group.nodes.new("GeometryNodeIsViewport").outputs[0], switch.inputs[0])
    group.links.new(info.outputs["Geometry"], switch.inputs[1])   # Off: a render shows Plate's mesh.
    group.links.new(inputs.outputs[0], switch.inputs[2])
    group.links.new(switch.outputs[0], output.inputs[0])
    body.modifiers.new("Slab", "NODES").node_group = group


def build_kit(scene):
    """Kit-bash nesting: one Geometry Nodes mesh; three levels of ``BRANCH`` empties each instancing the next."""
    ebony, oak = woods()
    levels = [holder(scene, "L%d" % number) for number in range(4)]
    gn_mesh("Stud", oak, ebony, levels[3])
    for number in range(3):
        for index in range(BRANCH):
            instancer("E%d_%d" % (number, index), levels[number + 1], levels[number]).location = (index, number, 0)
    instancer("Top", levels[0], scene.collection)


def switched(tree_, inputs, output, viewport_geometry, render_geometry):
    """``tree_``'s output: ``viewport_geometry`` in viewports, ``render_geometry`` in renders (Is Viewport)."""
    switch = tree_.nodes.new("GeometryNodeSwitch")
    switch.input_type = "GEOMETRY"
    tree_.links.new(tree_.nodes.new("GeometryNodeIsViewport").outputs[0], switch.inputs[0])
    tree_.links.new(render_geometry, switch.inputs[1])
    tree_.links.new(viewport_geometry, switch.inputs[2])
    tree_.links.new(switch.outputs[0], output.inputs[0])


def build_counted(scene, driven=False):
    """SourceMesh (a cube, no slots) under Subdivision of level 0 in viewports and 1 in renders, whose Geometry Nodes
    output an Oak cube for six input faces and an Ebony cube for more (break-it P2R5 H-1; ``driven``: the switch is a
    driver on the depsgraph's mode instead, P2R5 H-2); Showcase instances Source."""
    ebony, oak = woods()
    source = cube("SourceMesh", [], holder(scene, "Source"))
    subdivision = source.modifiers.new("Subdivision", "SUBSURF")
    subdivision.levels, subdivision.render_levels = 0, 1
    group, inputs, output = tree("Counted material")
    primitive, shown = group.nodes.new("GeometryNodeMeshCube"), []
    for wood in (oak, ebony):
        node = group.nodes.new("GeometryNodeSetMaterial")
        node.inputs["Material"].default_value = wood
        group.links.new(primitive.outputs["Mesh"], node.inputs["Geometry"])
        shown.append(node.outputs[0])
    switch = group.nodes.new("GeometryNodeSwitch")
    switch.input_type = "GEOMETRY"
    if driven:
        curve = group.driver_add('nodes["%s"].inputs[0].default_value' % switch.name)
        curve.driver.expression = "depsgraph.mode != 'VIEWPORT'"
    else:
        size, compare = group.nodes.new("GeometryNodeAttributeDomainSize"), group.nodes.new("ShaderNodeMath")
        compare.operation = "GREATER_THAN"
        group.links.new(inputs.outputs[0], size.inputs[0])
        group.links.new(size.outputs["Face Count"], compare.inputs[0])
        compare.inputs[1].default_value = 6
        group.links.new(compare.outputs[0], switch.inputs[0])
    group.links.new(shown[0], switch.inputs[1])   # Six faces (the viewport): Oak.
    group.links.new(shown[1], switch.inputs[2])   # More (a render): Ebony.
    group.links.new(switch.outputs[0], output.inputs[0])
    source.modifiers.new("Materials", "NODES").node_group = group
    instancer("Showcase", bpy.data.collections["Source"], scene.collection).location = (2, 0, 0)


def build_leak(scene, given=False, reader=False, showcase=True):
    """Source's SourceMesh, with no slots of its own: Geometry Nodes make a cube, Oak in viewports and Ebony in
    renders (Is Viewport); Showcase instances Source (break-it P2R3 H-1). ``given``: Ebony comes through the group's
    Material input, set on the modifier (P2R4 H-1); ``reader``: Reader shows SourceMesh realized through Object Info,
    and nothing instances it (P2R4 H-2)."""
    ebony, oak = woods()
    source = bpy.data.objects.new("SourceMesh", bpy.data.meshes.new("SourceMesh"))
    holder(scene, "Source").objects.link(source)
    group, inputs, output = tree("Render material")
    finish = group.interface.new_socket("Finish", in_out="INPUT", socket_type="NodeSocketMaterial") if given else None
    primitive, shown = group.nodes.new("GeometryNodeMeshCube"), []
    for wood in (oak, ebony):
        node = group.nodes.new("GeometryNodeSetMaterial")
        node.inputs["Material"].default_value = wood
        if wood is ebony and given:
            group.links.new(inputs.outputs["Finish"], node.inputs["Material"])
        group.links.new(primitive.outputs["Mesh"], node.inputs["Geometry"])
        shown.append(node.outputs[0])
    switched(group, inputs, output, *shown)
    modifier = source.modifiers.new("Materials", "NODES")
    modifier.node_group = group
    if given:
        modifier.properties.inputs[finish.identifier]["value"] = ebony   # As worker_materials reads it.
    if reader:
        read = bpy.data.objects.new("Reader", bpy.data.meshes.new("Reader"))
        scene.collection.objects.link(read)
        read.location = (0, 2, 0)
        group, inputs, output = tree("Read source")
        info = group.nodes.new("GeometryNodeObjectInfo")   # As Instance off: SourceMesh becomes Reader's own mesh.
        info.inputs["Object"].default_value = source
        group.links.new(info.outputs["Geometry"], output.inputs[0])
        read.modifiers.new("Read", "NODES").node_group = group
    if showcase:
        instancer("Showcase", bpy.data.collections["Source"], scene.collection).location = (2, 0, 0)


def build_driven(scene):
    """Body (its own slot Ebony) whose Bevel's render switch a driver sets: what renders show is decided per mode."""
    ebony, _oak = woods()
    body = cube("Body", [ebony], scene.collection)
    body.modifiers.new("Edge", "BEVEL")
    body.modifiers["Edge"].driver_add("show_render").driver.expression = "1"


def build_inactive(scene, render):
    """Body (its own slot Ebony) with a Geometry Nodes modifier off in viewports, its group passing its input through
    untouched; off in renders too and reading Ebony (Material Selection) unless ``render`` (break-it P2R3 M-1, M-2)."""
    ebony, _oak = woods()
    body = cube("Body", [ebony], scene.collection)
    group, inputs, output = tree("Pass through")
    group.links.new(inputs.outputs[0], output.inputs[0])
    group.nodes.new("GeometryNodeIsViewport")   # Unlinked: nothing it decides reaches the output.
    if not render:
        group.nodes.new("GeometryNodeMaterialSelection").inputs["Material"].default_value = ebony
    modifier = body.modifiers.new("Pass", "NODES")
    modifier.node_group, modifier.show_viewport, modifier.show_render = group, False, render


def build_plain(scene):
    """A plain part: Body (slots Oak, Ebony; half its faces Ebony) under Bevel, Subdivision and Mirror, parented to an
    empty; Panel (Ebony); a light."""
    ebony, oak = woods()
    rig = bpy.data.objects.new("Rig", None)
    scene.collection.objects.link(rig)
    body = cube("Body", [oak, ebony], scene.collection)
    for polygon in body.data.polygons[:3]:
        polygon.material_index = 1
    for kind in ("BEVEL", "SUBSURF", "MIRROR"):
        body.modifiers.new(kind.title(), kind)
    body.parent = rig
    cube("Panel", [ebony], scene.collection).location = (2, 0, 0)
    lamp = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT"))
    scene.collection.objects.link(lamp)


def build_observer(scene):
    """Source (Ebony) and Observer (Oak, Gold) under Solidify, whose material offset a driver sets from Source's
    material colour (break-it P2R6 H-1: driver variables are dependencies no reach walk saw)."""
    ebony, oak = woods()
    cube("Source", [ebony], scene.collection)
    observer = cube("Observer", [oak, material("Gold", (0.8, 0.6, 0.1, 1))], scene.collection)
    observer.location = (2, 0, 0)
    observer.modifiers.new("Shell", "SOLIDIFY")
    driver = observer.driver_add('modifiers["Shell"].material_offset').driver
    variable = driver.variables.new()
    target = variable.targets[0]
    target.id, target.data_path = bpy.data.objects["Source"], "active_material.diffuse_color[0]"
    driver.expression = "0"


def build_sown(scene):
    """A hair system instancing Blade (Ebony), its modifier off in both modes and its render switch driven by the
    depsgraph's mode (break-it P2R6 H-2)."""
    ebony, oak = woods()
    field = cube("Field", [oak], scene.collection)
    hair(field, cube("Blade", [ebony], scene.collection))
    modifier = field.modifiers["Hair"]
    modifier.show_viewport = modifier.show_render = False
    modifier.driver_add("show_render").driver.expression = "depsgraph.mode != 'VIEWPORT'"


def build_escaped(scene):
    """Body (Ebony) with Geometry Nodes named with a quotation mark, off in viewports, its render switch driven
    (break-it P2R6 H-3: an escaped name hid the driver)."""
    ebony, oak = woods()
    body = cube("Body", [ebony], scene.collection)
    group, inputs, output = tree("Oak cube")
    node = group.nodes.new("GeometryNodeSetMaterial")
    node.inputs["Material"].default_value = oak
    group.links.new(group.nodes.new("GeometryNodeMeshCube").outputs["Mesh"], node.inputs["Geometry"])
    group.links.new(node.outputs[0], output.inputs[0])
    modifier = body.modifiers.new('Build"Mat', "NODES")
    modifier.node_group, modifier.show_viewport = group, False
    modifier.driver_add("show_render").driver.expression = "depsgraph.mode != 'VIEWPORT'"


def build_reactive(scene):
    """Body (Ebony) and an unused material Reactive kept by a fake user, whose base colour a driver reads from Body's
    material: a swap destination with a dependency (break-it P2R7 H-1)."""
    ebony, _oak = woods()
    body = cube("Body", [ebony], scene.collection)
    reactive = material("Reactive", (0.5, 0.5, 0.5, 1))
    reactive.use_nodes, reactive.use_fake_user = True, True
    principled = next(node for node in reactive.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
    driver = reactive.node_tree.driver_add('nodes["%s"].inputs["Base Color"].default_value' % principled.name, 0).driver
    variable = driver.variables.new()
    variable.targets[0].id, variable.targets[0].data_path = body, "active_material.diffuse_color[0]"
    driver.expression = variable.name


BUILDS = {1: build_display, 2: build_frame, 3: build_tower, 4: build_field, 5: lambda s: build_prop(s, False),
          6: lambda s: build_prop(s, True), 7: lambda s: build_display(s, hidden=True), 8: build_body,
          9: lambda s: build_display(s, slot=True), 10: build_kit, 11: build_meadow, 12: build_plank, 13: build_offset,
          14: build_unused, 15: lambda s: build_lawn(s, False), 16: lambda s: build_lawn(s, True), 17: build_slab,
          18: build_leak, 19: lambda s: build_inactive(s, False), 20: lambda s: build_inactive(s, True),
          21: lambda s: build_leak(s, given=True), 22: lambda s: build_leak(s, reader=True, showcase=False),
          23: build_driven, 24: build_counted, 25: lambda s: build_counted(s, driven=True), 26: build_plain,
          27: build_observer, 28: build_sown, 29: build_escaped, 30: build_reactive}
