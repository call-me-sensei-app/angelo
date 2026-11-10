# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A wrapper of sub-data its owner frees while the owner lives raises ``ReferenceError``, whatever freed it.

Blender's API docs list it as a gotcha: a Python wrapper keeps pointing at memory its owner has since freed or
reallocated. Stock Blender 5.2 (and 5.3, main) then reads or writes freed memory: a SIGSEGV, or whatever took the
memory. Angelo watches the memory each sub-data wrapper points at and invalidates the wrapper when that memory is
freed (``bpy_rna.cc``, "Freed-Memory Watch"; the allocator reports each free), so the next access raises the usual
``ReferenceError``. A ``memoryview`` of a custom-property array, which Python reads without asking the wrapper, keeps
the memory it was made from instead (it reads and writes that detached copy until it is released).

The cases cover many kinds of sub-data and many ways to free it, each through a path other than the wrapper kept:
removal through another wrapper, an operator, ``clear()``, a mode switch, an unregistered class; reallocation by
``*.add()``, ``foreach_set`` and attribute changes; custom properties deleted, replaced and nested; collection and
custom-property iterators. The live cases check that wrappers of memory that is still allocated keep working after
unrelated frees and reallocations of the same owner.

Each case runs in its own child process of this binary, so a crash fails that case and the others still report.
Background only. No add-on, provider, account or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import gc
import json
import subprocess
import tempfile
from pathlib import Path

import bpy

CHILD_FLAG = "--angelo-sub-data-case"
BIG = 200_000  # Vertices: a freed array this size is returned to the system, so stock reads unmapped memory.


def _raises(attempt):
    """``ok`` when ``attempt`` raises ReferenceError, else what happened instead."""
    try:
        attempt()
    except ReferenceError:
        return "ok"
    except Exception as error:  # A wrong error is a failed check, not a crash.
        return "%s: %s" % (type(error).__name__, error)
    return "no error"


def _works(attempt):
    """``ok`` when ``attempt`` runs without an error."""
    try:
        attempt()
    except Exception as error:
        return "%s: %s" % (type(error).__name__, error)
    return "ok"


def _stale(results, prefix, wrapper, attribute, value=None):
    """Read, write and ``dir()`` of a stale wrapper all raise."""
    results[prefix + "_read"] = _raises(lambda: getattr(wrapper, attribute))
    if value is not None:
        results[prefix + "_write"] = _raises(lambda: setattr(wrapper, attribute, value))
    results[prefix + "_dir"] = _raises(lambda: dir(wrapper))
    return results


def _mesh_object(name="Obj", verts=8):
    side = max(2, int(verts ** 0.5))
    me = bpy.data.meshes.new(name)
    me.from_pydata([(x, y, 0.0) for y in range(side) for x in range(side)], [],
                   [(y * side + x, y * side + x + 1, (y + 1) * side + x + 1, (y + 1) * side + x)
                    for y in range(side - 1) for x in range(side - 1)])
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _action_with_keys(obj, count=4):
    for frame in range(1, count + 1):
        obj.location.x = frame
        obj.keyframe_insert("location", index=0, frame=frame)
    channelbag = obj.animation_data.action.layers[0].strips[0].channelbag(obj.animation_data.action_slot)
    return channelbag, channelbag.fcurves[0]


# --- Structs removed from a list through another path -----------------------------------------------------------

def case_modifier_removed_through_other_wrapper():
    obj = _mesh_object()
    kept = obj.modifiers.new("Sub", 'SUBSURF')
    prop = kept.path_resolve("levels", False)
    obj.modifiers.remove(obj.modifiers["Sub"])
    results = _stale({}, "modifier", kept, "levels", 2)
    results["property"] = _raises(lambda: prop.data)
    results["property_owner"] = _raises(lambda: prop.id_data)
    results["property_type"] = _raises(lambda: prop.rna_type)
    return results


def case_modifier_removed_by_operator():
    obj = _mesh_object()
    kept = obj.modifiers.new("Bevel", 'BEVEL')
    with bpy.context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.modifier_remove(modifier="Bevel")
    return _stale({}, "modifier", kept, "width", 0.5)


def case_modifiers_cleared():
    obj = _mesh_object()
    kept = [obj.modifiers.new("M%d" % i, 'ARRAY') for i in range(3)]
    obj.modifiers.clear()
    results = {}
    for index, wrapper in enumerate(kept):
        _stale(results, "modifier%d" % index, wrapper, "count", 3)
    return results


def case_constraint_removed():
    obj = _mesh_object()
    kept = obj.constraints.new('COPY_LOCATION')
    obj.constraints.remove(obj.constraints[0])
    return _stale({}, "constraint", kept, "influence", 0.5)


def case_node_and_socket_removed():
    tree = bpy.data.node_groups.new("Tree", 'ShaderNodeTree')
    node = tree.nodes.new('ShaderNodeMath')
    socket = node.inputs[0]
    tree.nodes.remove(tree.nodes[node.name])
    results = _stale({}, "node", node, "label", "x")
    return _stale(results, "socket", socket, "name")


def case_grease_pencil_layer_removed():
    gp = bpy.data.grease_pencils.new("GP")
    kept = gp.layers.new("Layer")
    gp.layers.new("Other")
    gp.layers.remove(gp.layers["Layer"])
    return _stale({}, "layer", kept, "opacity", 0.5)


def case_edit_bones_after_edit_mode():
    arm = bpy.data.armatures.new("Arm")
    obj = bpy.data.objects.new("Arm", arm)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bone = arm.edit_bones.new("Bone")
    bone.tail = (0, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    return _stale({}, "edit_bone", bone, "head", (0, 0, 0))


def case_shape_key_point_after_key_removed():
    obj = _mesh_object()
    obj.shape_key_add(name="Basis")
    block = obj.shape_key_add(name="Key")
    point = block.data[0]
    obj.shape_key_remove(obj.data.shape_keys.key_blocks["Key"])
    results = _stale({}, "key_block", block, "value", 0.5)
    return _stale(results, "point", point, "co", (1, 2, 3))


def case_unregistered_class_type():
    class AngeloProbeGroup(bpy.types.PropertyGroup):
        value: bpy.props.IntProperty()

    bpy.utils.register_class(AngeloProbeGroup)
    rna = AngeloProbeGroup.bl_rna
    prop = rna.properties["value"]
    bpy.utils.unregister_class(AngeloProbeGroup)
    results = {"type_read": _raises(lambda: rna.identifier)}
    results["property_read"] = _raises(lambda: prop.identifier)
    return results


# --- Arrays reallocated or freed --------------------------------------------------------------------------------

def case_vertices_add():
    me = _mesh_object(verts=BIG).data
    vertex = me.vertices[5]
    co = vertex.co
    me.vertices.add(10)
    results = _stale({}, "vertex", vertex, "co", (1, 2, 3))
    results["vector_read"] = _raises(lambda: co.x)
    results["vector_write"] = _raises(lambda: setattr(co, "x", 1.0))
    return results


def case_edges_loops_polygons_add():
    me = _mesh_object(verts=BIG).data
    edge, loop, polygon = me.edges[3], me.loops[3], me.polygons[3]
    me.edges.add(4)
    me.loops.add(4)
    me.polygons.add(1)
    results = _stale({}, "edge", edge, "vertices")
    _stale(results, "loop", loop, "vertex_index", 0)
    return _stale(results, "polygon", polygon, "loop_start", 0)


def case_vertex_kept_across_foreach_set_unshare():
    """``foreach_set`` gives the mesh its own copy of a shared array; the other user then frees the old one."""
    me = _mesh_object(verts=BIG).data
    vertex = me.vertices[5]
    other = me.copy()  # Shares the vertex positions until one of them writes.
    flat = [0.0] * (len(me.vertices) * 3)
    me.vertices.foreach_set("co", flat)
    bpy.data.meshes.remove(other)
    return _stale({}, "vertex", vertex, "co", (1, 2, 3))


def case_attribute_kept_across_layer_changes():
    """Object-mode attributes have individual allocations; removing a sibling leaves this one live."""
    me = _mesh_object(verts=64).data
    kept = me.attributes.new("kept", 'FLOAT', 'POINT')
    for index in range(40):
        me.attributes.new("extra%d" % index, 'FLOAT', 'POINT')
    me.attributes.remove(me.attributes["extra0"])
    kept.name = "renamed"
    return {"identity": "ok" if kept == me.attributes["renamed"] else "different attribute"}


def case_attribute_removed():
    me = _mesh_object().data
    held = me.attributes.new("held", 'FLOAT', 'POINT')
    me.attributes.remove(me.attributes["held"])
    return _stale({}, "attribute", held, "name", "renamed")


def case_keyframe_points_add():
    obj = _mesh_object()
    _channelbag, fcurve = _action_with_keys(obj)
    point = fcurve.keyframe_points[1]
    fcurve.keyframe_points.add(200)
    return _stale({}, "keyframe", point, "co", (1, 1))


def case_fcurve_removed_with_function_kept():
    obj = _mesh_object()
    channelbag, fcurve = _action_with_keys(obj)
    evaluate = fcurve.evaluate
    channelbag.fcurves.remove(channelbag.fcurves[0])
    results = _stale({}, "fcurve", fcurve, "mute", True)
    results["function_call"] = _raises(lambda: evaluate(1.0))
    return results


def case_vertex_group_weight_kept():
    obj = _mesh_object(verts=16)
    group = obj.vertex_groups.new(name="G")
    group.add([0], 0.5, 'REPLACE')
    weight = obj.data.vertices[0].groups[0]
    obj.vertex_groups.remove(group)
    return _stale({}, "weight", weight, "weight", 0.25)


# --- Custom properties --------------------------------------------------------------------------------------------

def case_custom_property_deleted():
    obj = _mesh_object()
    obj["group"] = {"a": 1, "child": {"b": 2}}
    obj["array"] = [1.0, 2.0, 3.0]
    group, again, child, array = obj["group"], obj["group"], obj["group"]["child"], obj["array"]
    ui = obj.id_properties_ui("array")
    del obj["group"]
    del obj["array"]
    return {"group": _raises(lambda: group["a"]), "other_wrapper": _raises(lambda: again.keys()),
            "nested": _raises(lambda: child["b"]), "array": _raises(lambda: array[0]),
            "array_write": _raises(lambda: array.__setitem__(0, 5.0)), "ui_data": _raises(lambda: ui.as_dict())}


def case_custom_property_replaced():
    obj = _mesh_object()
    obj["array"] = [1.0, 2.0, 3.0]
    array = obj["array"]
    obj["array"] = "now a string"
    return {"array": _raises(lambda: array[0])}


def case_custom_property_group_iterator():
    obj = _mesh_object()
    obj["group"] = {"a": 1, "b": 2, "c": 3}
    items = iter(obj["group"].items())
    next(items)
    del obj["group"]
    return {"iterator": _raises(lambda: next(items))}


def case_property_group_instance_removed():
    class AngeloProbeSettings(bpy.types.PropertyGroup):
        value: bpy.props.FloatProperty()

    bpy.utils.register_class(AngeloProbeSettings)
    bpy.types.Object.angelo_probe = bpy.props.PointerProperty(type=AngeloProbeSettings)
    obj = _mesh_object()
    obj.angelo_probe.value = 2.0
    settings = obj.angelo_probe
    obj.property_unset("angelo_probe")
    return _stale({}, "settings", settings, "value", 3.0)


def case_memoryview_outlives_resize_and_delete():
    """Python reads a memoryview without asking its exporter: the memory must stay valid instead."""
    obj = _mesh_object()
    obj["array"] = [1.5] * BIG
    view = memoryview(obj["array"])
    second = memoryview(obj["array"])
    obj["array"] = [2.5] * (BIG * 2)
    resized = (view[10], view.nbytes)
    view[11] = 7.0
    del obj["array"]
    junk = [bytearray(BIG * 8) for _ in range(8)]
    after = view[BIG - 1]
    view.release()
    shared = second[11]
    second.release()
    return {"second_export": "ok" if shared == 7.0 else repr(shared),
            "read_after_resize": "ok" if resized[0] == 1.5 else repr(resized),
            "read_after_delete": "ok" if after == 1.5 and len(junk) == 8 else repr(after)}


def case_idproperty_cursor_deleted_same_size():
    obj = _mesh_object()
    obj["group"] = {"a": 1, "b": 2, "c": 3}
    group = obj["group"]
    iterator = iter(group.items())
    first = next(iterator)
    del group["b"]
    group["d"] = 4
    return {"setup": "ok" if first[0] == "a" else repr(first),
            "cursor": _raises(lambda: next(iterator))}


def case_array_assignment_reenters_python():
    obj = _mesh_object()
    obj["array"] = [1.0, 2.0]
    held = obj["array"]

    class Remove:
        def __float__(self):
            del obj["array"]
            return 2.5

    return {"write": _raises(lambda: held.__setitem__(0, Remove()))}


def case_dynamic_descriptor_removed():
    bpy.types.Object.angelo_values = bpy.props.FloatVectorProperty(size=3)
    obj = _mesh_object()
    held = obj.path_resolve("angelo_values", False)
    del bpy.types.Object.angelo_values
    return {"get": _raises(lambda: held[0]), "repr": _raises(lambda: repr(held))}


def case_shared_mesh_array_detached():
    me = _mesh_object(verts=16).data
    vertex = me.vertices[2]
    before = tuple(vertex.co)
    other = me.copy()
    me.vertices.foreach_set("co", [0.0] * (len(me.vertices) * 3))
    stale = _stale({}, "vertex", vertex, "co", (1, 2, 3))
    stale["other_mesh"] = "ok" if tuple(other.vertices[2].co) == before else "modified"
    return stale


def case_shared_face_offsets_detached():
    me = _mesh_object(verts=16).data
    polygon = me.polygons[1]
    other = me.copy()
    me.polygons.add(1)
    return {"polygon": _raises(lambda: polygon.loop_start),
            "other_mesh": _works(lambda: other.polygons[1].loop_start)}


def _overridden_mesh():
    source = _mesh_object("SwapMesh", verts=16).data
    source["probe"] = 1
    source.property_overridable_library_set('["probe"]', True)
    library = str(Path(tempfile.mkdtemp(prefix="angelo-mesh-swap-")) / "mesh.blend")
    bpy.data.libraries.write(library, {source})
    with bpy.data.libraries.load(library, link=True) as (_data_from, data_to):
        data_to.meshes = [source.name]
    mesh = data_to.meshes[0].override_create(remap_local_usages=True)
    holder = bpy.data.objects.new("Override mesh", mesh)
    bpy.context.scene.collection.objects.link(holder)
    mesh["probe"] = 2
    mesh.override_library.operations_update()
    return mesh


def case_mesh_scope_after_override_swap():
    mesh = _overridden_mesh()
    mesh.override_library.reset()
    assert mesh["probe"] == 1, "The fixture must actually reset overridden contents"
    vertex = mesh.vertices[2]
    before = tuple(vertex.co)
    other = mesh.copy()
    mesh.vertices.foreach_set("co", [0.0] * (len(mesh.vertices) * 3))
    results = _stale({}, "vertex", vertex, "co", (1.0, 2.0, 3.0))
    results["other_mesh"] = "ok" if tuple(other.vertices[2].co) == before else "modified"
    return results


def case_polygon_conversion_resets_owner():
    mesh = _overridden_mesh()
    polygon = mesh.polygons[0]

    class Reset:
        def __index__(self):
            mesh.override_library.reset()
            return 0

    return {"assignment": _raises(lambda: setattr(polygon, "material_index", Reset()))}


def case_index_offset_material_slot_removed():
    obj = _mesh_object()
    obj.data.materials.append(bpy.data.materials.new("A"))
    obj.data.materials.append(bpy.data.materials.new("B"))
    held = obj.material_slots[1]
    obj.data.materials.pop(index=1)
    return _stale({}, "slot", held, "link", "OBJECT")


def case_slice_index_reenters_python():
    obj = _mesh_object()
    obj["array"] = [1.0, 2.0]
    held = obj["array"]

    class Remove:
        def __index__(self):
            del obj["array"]
            return 0

    return {"slice": _raises(lambda: held[Remove():])}


def case_mapping_conversion_removes_destination():
    obj = _mesh_object()
    obj["group"] = {"x": 1}
    group = obj["group"]

    class Remove(dict):
        def keys(self):
            del obj["group"]
            return super().keys()

    return {"assign": _raises(lambda: group.__setitem__("x", Remove(a=4)))}


def case_update_from_own_child():
    obj = _mesh_object()
    obj["group"] = {"child": {"child": 1, "next": 2}}
    group = obj["group"]
    group.update(group["child"])
    return {"contents": "ok" if group.to_dict() == {"child": 1, "next": 2} else "wrong values"}


# --- Iterators ----------------------------------------------------------------------------------------------------

def case_attribute_iterator_future_item_removed():
    me = _mesh_object().data
    for name in ("first", "middle", "last"):
        me.attributes.new(name, 'FLOAT', 'POINT')
    iterator = iter(me.attributes)
    next(iterator)
    me.attributes.remove(me.attributes["last"])
    # The RNA iterator resolves its remaining snapshot on each advance.
    names = [attribute.name for attribute in iterator]
    return {"walk": "ok" if "middle" in names and "last" not in names else repr(names)}


def case_modifier_iterator_current_removed():
    obj = _mesh_object()
    for index in range(3):
        obj.modifiers.new("M%d" % index, 'ARRAY')
    iterator = iter(obj.modifiers)
    current = next(iterator)
    obj.modifiers.remove(current)
    return {"iterator": _raises(lambda: next(iterator))}


def case_vertex_iterator_across_add():
    me = _mesh_object(verts=BIG).data
    iterator = iter(me.vertices)
    next(iterator)
    me.vertices.add(10)
    return {"iterator": _raises(lambda: next(iterator))}


# --- Live wrappers keep working -----------------------------------------------------------------------------------

def case_live_wrappers_still_work():
    obj = _mesh_object(verts=64)
    me = obj.data
    kept = obj.modifiers.new("Kept", 'SUBSURF')
    obj.modifiers.new("Gone", 'BEVEL')
    obj.modifiers.remove(obj.modifiers["Gone"])
    vertex = me.vertices[3]
    co = vertex.co
    me.attributes.new("extra", 'FLOAT', 'POINT')  # Reallocates the layer array, not the positions.
    vertices, attributes = me.vertices, me.attributes
    me.vertices.add(4)
    obj["a"], obj["b"] = 1, {"x": 2}
    a_group = obj["b"]
    del obj["a"]
    obj["c"] = [1.0, 2.0]
    view = memoryview(obj["c"])
    view[0] = 9.0
    render = bpy.context.scene.render
    for index in range(50):
        bpy.data.meshes.remove(bpy.data.meshes.new("churn%d" % index))
    walked = [m.name for m in obj.modifiers]
    obj.modifiers.new("Added", 'ARRAY')
    results = {
        "kept_modifier": _works(lambda: setattr(kept, "levels", 2) or kept.levels),
        "id_collection_after_add": _works(lambda: vertices[len(me.vertices) - 1].co.x),
        "view_after_add": _works(lambda: attributes["position"].data[0].vector),
        "idprop_sibling": _works(lambda: a_group["x"]),
        "memoryview_live": "ok" if obj["c"][0] == 9.0 else repr(obj["c"][0]),
        "embedded": _works(lambda: setattr(render, "resolution_x", 640)),
        "iteration": "ok" if walked == ["Kept"] else repr(walked),
    }
    # The vertex array moved with vertices.add(); a fresh lookup still works.
    results["fresh_vertex"] = _works(lambda: me.vertices[3].co.x)
    view.release()
    del co, vertex
    return results


def case_live_iteration_while_changing():
    """Iterating while each item is edited, and while items are added, runs without an error."""
    obj = _mesh_object(verts=64)
    for index in range(4):
        obj.modifiers.new("M%d" % index, 'ARRAY')

    def edit_each():
        for modifier in obj.modifiers:
            modifier.count = 2
            modifier.name = modifier.name + "x"

    def add_while_iterating():
        for modifier in obj.modifiers:
            if len(obj.modifiers) < 8:
                obj.modifiers.new("Added", 'ARRAY')

    def vertices_each():
        for vertex in obj.data.vertices:
            vertex.co.z += 1.0

    results = {"edit_each": _works(edit_each)}
    names = [m.name for m in obj.modifiers]
    results["renamed"] = "ok" if names == ["M0x", "M1x", "M2x", "M3x"] else repr(names)
    results["add_while_iterating"] = _works(add_while_iterating)
    results["vertices_each"] = _works(vertices_each)
    return results


def case_exhausted_iterators_stay_exhausted():
    obj = _mesh_object()
    graph = bpy.context.evaluated_depsgraph_get()
    instances = iter(graph.object_instances)
    list(instances)
    modifiers = iter(obj.modifiers)
    list(modifiers)
    bpy.data.objects.remove(obj)
    return {"instances": "ok" if next(instances, None) is None else "not exhausted",
            "modifiers_after_owner_removed": "ok" if next(modifiers, None) is None else "not exhausted"}


def case_material_slot_shift_and_reuse():
    obj = _mesh_object()
    for name in ("A", "B", "C"):
        obj.data.materials.append(bpy.data.materials.new(name))
    first, middle, last = tuple(obj.material_slots)
    obj.data.materials.pop(index=1)
    results = {"unchanged_slot": _works(lambda: first.link),
               "removed_slot": _raises(lambda: middle.link),
               "shifted_slot": _raises(lambda: last.link)}
    obj.data.materials.append(bpy.data.materials.new("Replacement"))
    results["no_revival"] = _raises(lambda: middle.link)
    return results


def case_persistent_data_wrapper_after_reload():
    data, objects = bpy.data, bpy.data.objects
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return {"same_wrapper": "ok" if data is bpy.data else "replaced",
            "new_target": _works(lambda: data.objects.new("After reload", None)),
            "old_collection": _raises(lambda: len(objects))}


def case_live_matrix_assignment_keeps_orientation():
    from mathutils import Matrix
    obj = _mesh_object()
    matrix = Matrix.Translation((1.0, 2.0, 3.0))
    obj.matrix_world = matrix
    results = {"matrix": "ok" if obj.matrix_world == matrix else repr(obj.matrix_world),
               "translation": "ok" if tuple(obj.location) == (1.0, 2.0, 3.0) else repr(obj.location)}
    array = obj.path_resolve("matrix_world", False)
    results["matrix_slice"] = _works(lambda: array.__setitem__(slice(None), matrix))
    return results


def _rna_conversion_removes_attribute(operation):
    me = _mesh_object().data
    is_scalar = operation in ("scalar", "collection_foreach")
    attr = me.attributes.new("Conversion", 'FLOAT' if is_scalar else 'FLOAT_VECTOR', 'POINT')
    held = attr.data[0]
    array = None if is_scalar else held.path_resolve("vector", False)
    collection = attr.data

    class Remove:
        def __float__(self):
            me.attributes.remove(attr)
            return 1.0

    operations = {
        "scalar": lambda: setattr(held, "value", Remove()),
        "vector": lambda: setattr(held, "vector", [Remove(), 1.0, 2.0]),
        "index": lambda: array.__setitem__(0, Remove()),
        "slice": lambda: array.__setitem__(slice(None), [Remove(), 1.0, 2.0]),
        "array_foreach": lambda: array.foreach_set([Remove(), 1.0, 2.0]),
        "collection_foreach": lambda: collection.foreach_set("value", [Remove()] + [0.0] * (len(me.vertices) - 1)),
    }
    return {"assignment": _raises(operations[operation])}


def case_rna_scalar_conversion_removes_target():
    return _rna_conversion_removes_attribute("scalar")


def case_rna_vector_conversion_removes_target():
    return _rna_conversion_removes_attribute("vector")


def case_rna_index_conversion_removes_target():
    return _rna_conversion_removes_attribute("index")


def case_rna_slice_conversion_removes_target():
    return _rna_conversion_removes_attribute("slice")


def case_rna_array_foreach_conversion_removes_target():
    return _rna_conversion_removes_attribute("array_foreach")


def case_rna_collection_foreach_conversion_removes_target():
    return _rna_conversion_removes_attribute("collection_foreach")


def case_bound_function_conversion_removes_target():
    obj = _mesh_object()
    group = obj.vertex_groups.new(name="Conversion")
    add = group.add

    class Remove:
        def __float__(self):
            obj.vertex_groups.remove(group)
            return 0.5

    return {"call": _raises(lambda: add([0], Remove(), 'REPLACE'))}


def case_ui_metadata_conversion_removes_target():
    obj = _mesh_object()
    obj["value"] = 1.0
    ui = obj.id_properties_ui("value")

    class Remove:
        def __float__(self):
            del obj["value"]
            return 0.5

    return {"update": _raises(lambda: ui.update(min=Remove()))}


def case_live_ui_metadata_updates():
    obj = _mesh_object()
    obj["value"] = 1.0
    ui = obj.id_properties_ui("value")
    ui.update(min=0.5, max=3.0, description="Live metadata")
    data = ui.as_dict()
    obj["array"] = [1.0, 2.0]
    array_ui = obj.id_properties_ui("array")
    array_ui.update(default=[3.0, 4.0])
    return {"scalar": "ok" if data["min"] == 0.5 and data["max"] == 3.0 else repr(data),
            "description": "ok" if data["description"] == "Live metadata" else repr(data),
            "array": "ok" if list(array_ui.as_dict()["default"]) == [3.0, 4.0] else "wrong default"}


def _edit_attribute_retirement(grow):
    import bmesh

    obj = _mesh_object()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        bm = bmesh.from_edit_mesh(obj.data)
        layer = bm.verts.layers.float.new("Held edit attribute")
        held = obj.data.attributes["Held edit attribute"]
        if grow:
            for index in range(16):
                bm.verts.layers.float.new("Additional edit attribute %d" % index)
        else:
            bm.verts.layers.float.remove(layer)
        result = {"retired_layer_descriptor": _raises(lambda: held.name)}
        if grow:
            result["fresh_lookup"] = ("ok" if obj.data.attributes["Held edit attribute"].name ==
                                      "Held edit attribute" else "wrong name")
        return result
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')


def case_edit_attribute_removed_through_bmesh():
    return _edit_attribute_retirement(False)


def case_edit_attribute_layer_array_grows():
    return _edit_attribute_retirement(True)


def _attribute_mode_transition(start_edit, iterator):
    obj = _mesh_object()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.data.attributes.new("Mode attribute", 'FLOAT', 'POINT')
    collection = obj.data.attributes
    if start_edit:
        bpy.ops.object.mode_set(mode='EDIT')
    try:
        held = iter(collection) if iterator else collection["Mode attribute"]
        if iterator:
            next(held)
        bpy.ops.object.mode_set(mode='OBJECT' if start_edit else 'EDIT')
        return {"old_backing": _raises(lambda: next(held) if iterator else held.name),
                "live_collection": "ok" if collection["Mode attribute"].name == "Mode attribute"
                else "fresh lookup changed"}
    finally:
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')


def case_attribute_enter_edit_mode():
    return _attribute_mode_transition(False, False)


def case_attribute_leave_edit_mode():
    return _attribute_mode_transition(True, False)


def case_attribute_iterator_enter_edit_mode():
    return _attribute_mode_transition(False, True)


def case_attribute_iterator_leave_edit_mode():
    return _attribute_mode_transition(True, True)


def _constructor_retires_input(before_init=False, read_self=False):
    result = {}

    def retire(cls):
        del bpy.types.Scene.lifetime_constructor_probe
        bpy.utils.unregister_class(cls)

    class LifetimeConstructorProbe(bpy.types.PropertyGroup):
        value: bpy.props.IntProperty(default=17)

        def __new__(cls, *args, **kwargs):
            value = super().__new__(cls, *args, **kwargs)
            if before_init:
                retire(cls)
            return value

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            retire(type(self))
            if read_self:
                result["inside_constructor"] = _raises(lambda: self.value)

    bpy.utils.register_class(LifetimeConstructorProbe)
    bpy.types.Scene.lifetime_constructor_probe = bpy.props.PointerProperty(type=LifetimeConstructorProbe)
    result["factory"] = _raises(lambda: bpy.context.scene.lifetime_constructor_probe)
    return result


def case_constructor_retires_before_base_init():
    return _constructor_retires_input(before_init=True)


def case_constructor_retires_then_reads_self():
    return _constructor_retires_input(read_self=True)


def case_constructor_retires_then_returns():
    return _constructor_retires_input()


def case_iterator_constructor_clears_collection():
    armed = False

    class LifetimeCollectionProbe(bpy.types.PropertyGroup):
        value: bpy.props.IntProperty(default=17)

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if armed:
                bpy.context.scene.lifetime_collection_probe.clear()

    bpy.utils.register_class(LifetimeCollectionProbe)
    bpy.types.Scene.lifetime_collection_probe = bpy.props.CollectionProperty(type=LifetimeCollectionProbe)
    try:
        collection = bpy.context.scene.lifetime_collection_probe
        collection.add()
        collection.add()
        iterator = iter(collection)
        armed = True
        return {"constructor_clears_cursor": _raises(lambda: next(iterator)),
                "retired_cursor": _raises(lambda: next(iterator))}
    finally:
        del bpy.types.Scene.lifetime_collection_probe
        bpy.utils.unregister_class(LifetimeCollectionProbe)


def _iterator_constructor_reenters(drain):
    armed = False
    entered = False
    iterator = None

    class LifetimeReentrantProbe(bpy.types.PropertyGroup):
        value: bpy.props.IntProperty(default=17)

        def __init__(self, *args, **kwargs):
            nonlocal entered
            super().__init__(*args, **kwargs)
            if armed and not entered:
                entered = True
                if drain:
                    list(iterator)
                else:
                    next(iterator)

    bpy.utils.register_class(LifetimeReentrantProbe)
    bpy.types.Scene.lifetime_reentrant_probe = bpy.props.CollectionProperty(type=LifetimeReentrantProbe)
    try:
        collection = bpy.context.scene.lifetime_reentrant_probe
        for _ in range(3):
            collection.add()
        iterator = iter(collection)
        armed = True
        return {"constructor_reenters_cursor": _raises(lambda: next(iterator)),
                "retired_cursor": _raises(lambda: next(iterator)),
                "live_collection": "ok" if len(collection) == 3 else "collection changed"}
    finally:
        del bpy.types.Scene.lifetime_reentrant_probe
        bpy.utils.unregister_class(LifetimeReentrantProbe)


def case_iterator_constructor_advances_cursor():
    return _iterator_constructor_reenters(False)


def case_iterator_constructor_exhausts_cursor():
    return _iterator_constructor_reenters(True)


def case_subtype_hook_retires_returned_data():
    mesh = _mesh_object().data
    collection = mesh.attributes
    calls = []

    def initialize(cls, **kwargs):
        calls.append(cls.__name__)
        mesh.clear_geometry()

    bpy.types.Attribute.__init_subclass__ = classmethod(initialize)
    try:
        result = {"factory": _raises(lambda: collection.new("Retired during subtype", 'FLOAT', 'POINT'))}
    finally:
        del bpy.types.Attribute.__init_subclass__
    result["callback"] = "ok" if calls else "lazy subtype callback did not run"
    result["live_collection"] = "ok" if collection["position"].name == "position" else "fresh lookup changed"
    return result


def case_retained_constructor_capsule_checks_lifetime():
    capsules = []

    class LifetimeCapsuleProbe(bpy.types.PropertyGroup):
        value: bpy.props.IntProperty(default=17)

        def __init__(self, *args, **kwargs):
            capsules.append(args[0])
            super().__init__(*args, **kwargs)

    bpy.utils.register_class(LifetimeCapsuleProbe)
    bpy.types.Scene.lifetime_capsule_probe = bpy.props.PointerProperty(type=LifetimeCapsuleProbe)
    held = bpy.context.scene.lifetime_capsule_probe
    result = {}
    try:
        bpy.context.scene.__init__(capsules[0])
    except TypeError:
        result["shared_root"] = "ok"
    else:
        result["shared_root"] = "shared root was retargeted"
    del bpy.types.Scene.lifetime_capsule_probe
    bpy.utils.unregister_class(LifetimeCapsuleProbe)
    result["retained_capsule"] = _raises(
        lambda: super(LifetimeCapsuleProbe, held).__init__(capsules[0]))
    return result


def case_live_unicode_and_string_subclass_keys():
    class Key(str):
        def encode(self, *args, **kwargs):
            raise AssertionError("Key lookup must not call a Python encoder")

    obj = _mesh_object()
    obj["plain"] = {"naïve": [3.0, 4.0]}
    obj["café"] = {"plain": [5.0, 6.0]}
    return {"ascii_subclass": "ok" if obj[Key("plain")][Key("naïve")][0] == 3.0 else "wrong value",
            "unicode_subclass": "ok" if obj[Key("café")][Key("plain")][1] == 6.0 else "wrong value",
            "unicode_attribute": "ok" if getattr(obj, "missing_λ", None) is None else "unexpected attribute"}


def case_idproperty_wrapper_reuse_keeps_retired_views_invalid():
    obj = _mesh_object()
    obj["held"] = {"array": [7.0, 8.0]}
    group = obj["held"]
    array = group["array"]
    view = memoryview(array)
    del obj["held"]
    for index in range(200):
        other = _mesh_object("Reuse owner")
        other["fresh"] = {"array": [float(index), 2.0]}
        assert other["fresh"]["array"][0] == index
        bpy.data.objects.remove(other, do_unlink=True)
    result = {"group": _raises(lambda: group["array"]),
              "array": _raises(lambda: array[0]),
              "export": "ok" if view[0] == 7.0 else "changed detached storage"}
    view.release()
    return result


def case_live_custom_rna_constructors():
    calls = []

    class LifetimeConstructorProbe(bpy.types.PropertyGroup):
        value: bpy.props.IntProperty(default=1)

        def __new__(cls, *args, **kwargs):
            calls.append("new")
            return super().__new__(cls, *args, **kwargs)

        def __init__(self, *args, **kwargs):
            calls.append("init")
            super().__init__(*args, **kwargs)

    bpy.utils.register_class(LifetimeConstructorProbe)
    bpy.types.Scene.lifetime_constructor_probe = bpy.props.PointerProperty(type=LifetimeConstructorProbe)
    try:
        view = bpy.context.scene.lifetime_constructor_probe
        view.value = 27
        return {"constructors": "ok" if "new" in calls and "init" in calls else repr(calls),
                "value": "ok" if view.value == 27 else "wrong value"}
    finally:
        del bpy.types.Scene.lifetime_constructor_probe
        bpy.utils.unregister_class(LifetimeConstructorProbe)


CASES = {name[len("case_"):]: fn for name, fn in globals().items() if name.startswith("case_")}


def run_child(name):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        results = CASES[name]()
    except Exception as error:
        results = {"setup": "%s: %s" % (type(error).__name__, error)}
    # Allocations after the checks: a stale wrapper that slipped through would be read from reused memory.
    _churn = [bpy.data.meshes.new("churn%d" % i) for i in range(100)]
    gc.collect()
    print("ANGELO_SUB_DATA_CASE " + json.dumps({"case": name, "results": results}), flush=True)


def run_parent():
    assert "--factory-startup" in sys.argv, "Use a disposable factory-startup Blender instance"
    assert bpy.app.background, "Run with --background"
    out = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-sub-data-wrappers"))
    out.mkdir(parents=True, exist_ok=True)
    extra = [arg for arg in sys.argv[sys.argv.index("--") + 1:]] if "--" in sys.argv else []
    report = {"success": False, "cases": {}, "failed": [], "binary": bpy.app.binary_path, "paid_requests": 0,
              "blender_args": extra}
    for name in CASES:
        command = [bpy.app.binary_path, "--background", "--factory-startup", "--python-exit-code", "3",
                   *extra, "--python", __file__, "--", CHILD_FLAG, name]
        try:
            child = subprocess.run(command, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            report["cases"][name] = {"exit": "timeout"}
            report["failed"].append(name)
            continue
        lines = [line for line in child.stdout.splitlines() if line.startswith("ANGELO_SUB_DATA_CASE ")]
        results = json.loads(lines[-1].split(" ", 1)[1])["results"] if lines else {}
        ok = child.returncode == 0 and bool(results) and all(value == "ok" for value in results.values())
        report["cases"][name] = {"exit": child.returncode, "results": results,
                                 "stderr_tail": child.stderr[-600:] if not ok else ""}
        if not ok:
            report["failed"].append(name)
    report["success"] = bool(report["cases"]) and not report["failed"]
    (out / "sub-data-wrappers.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_SUB_DATA_WRAPPERS", json.dumps({"success": report["success"], "failed": report["failed"]}),
          flush=True)


if CHILD_FLAG in sys.argv:
    run_child(sys.argv[sys.argv.index(CHILD_FLAG) + 1])
else:
    run_parent()
