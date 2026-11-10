# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every Python wrapper and iterator of freed data raises ``ReferenceError``, not only the shared wrapper.

Blender invalidates one wrapper when it frees an ID: the shared one its ``py_instance`` holds. Stock Blender 5.2
(and 5.3, main) leaves every other wrapper and iterator over that memory pointing at it, and reading or writing
through one crashes (SIGSEGV) or silently reads whatever took the memory. ``dir()`` on such a wrapper crashed a
1.0.8 build in ``pyrna_struct_dir`` (``ID_rna_properties_next`` read the freed ID's custom properties). Angelo links
these dependents to the shared wrapper and invalidates them with it (``bpy_rna.cc``, "Dependent Wrappers"):

* aliases, other wrappers of the same ID: one kept across a type change that refines to another RNA type (a light's
  ``type``), ``type_recast()``, and ``Sub(obj)`` or ``bpy.types.ID(obj)``;
* views, wrappers of another type over the ID itself (``mesh.attributes``, ``object.display``, a
  ``depsgraph.updates`` item, whose ID is the evaluated copy it reports);
* collection iterators (``iter(obj.modifiers)``, ``iter(depsgraph.object_instances)``), ended when their owner ID or
  dependency graph is freed; the graph now has one shared wrapper, released when it is freed;
* a depsgraph instance's ``object``, a temporary copy the iterator overwrites at a later step and frees at its end
  (``rna_depsgraph.cc``): its wrapper is invalidated before either, where stock read another instance's data or
  freed memory, and the instance itself once its step is over;
* every other ``bpy_struct``, ``bpy_prop``, ``bpy_prop_array``, ``bpy_func``, ``IDPropertyGroup``,
  ``IDPropertyArray`` and ``IDPropertyUIManager`` of the data (and mathutils values reading through them).

Each case runs in its own child process of this binary, so a crash fails that case and the others still report.
Checks: every kept wrapper or iterator raises ``ReferenceError`` once its data is gone, on read, write, ``dir()``
(also from a Python ``__init__``) and in an ID-taking helper (``bpy.data.user_map``); while the data lives every
alias, view and iterator still works, lookups keep returning one shared wrapper, and an instance's ``object`` reads
its own instance through its loop step. Since the break-it review, also every property, array, collection, function,
sub-struct and custom-property wrapper (and the mathutils values reading through them) of a removed ID; a graph
reached through ``view_layer.depsgraph``; an instance kept into the next step; owned evaluated data freed or
replaced by a re-evaluation; views kept across an undo; and ``a.__init__(b)`` on a shared wrapper is refused.
Since the first 1.0.10 build: key-maps kept across file reads stay valid (the window manager moves them, so no
dependent is rooted on it or on a screen), and an override reset, which swaps an ID's contents with a temporary ID
it then frees, invalidates the wrappers into the old contents while the override stays valid.

Background only. No add-on, provider, account or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import bpy

REMOVED = "has been removed"
CHILD_FLAG = "--angelo-alias-case"


def _new_light(name="Light", kind="POINT"):
    return bpy.data.lights.new(name, kind)


def _alias_by_type_change(new_type):
    """A light's wrapper left behind when a type change makes the next lookup replace the shared one."""
    old = _new_light()
    old.type = new_type
    shared = bpy.data.lights[old.name]
    assert type(shared) is not type(old), (type(shared), type(old))
    return old, shared


class _Probe:
    """``dir()`` from a Python ``__init__``, the frame the 1.0.8 crash came through."""

    def __init__(self, wrapper):
        self.names = wrapper.__dir__()


def _stale_checks(stale, type_name):
    """Every access to a wrapper of a freed ID raises ReferenceError naming its type."""
    results = {}
    attempts = {
        "read": lambda: stale.name,
        "write": lambda: setattr(stale, "name", "x" * 60),
        "dir": lambda: dir(stale),
        "dir_in_init": lambda: _Probe(stale),
        "custom_property": lambda: stale.get("custom"),
        "user_map": lambda: bpy.data.user_map(subset=[stale]),
    }
    for label, attempt in attempts.items():
        try:
            attempt()
            results[label] = "no error"
        except ReferenceError as error:
            results[label] = "ok" if (type_name in str(error) and REMOVED in str(error)) else str(error)
        except Exception as error:  # A wrong error is a failed check, not a crash.
            results[label] = "%s: %s" % (type(error).__name__, error)
    return results


def case_light_type_change_remove_shared():
    old, shared = _alias_by_type_change("SPOT")
    old["custom"] = 1.0
    bpy.data.lights.remove(shared)
    return _stale_checks(old, "PointLight")


def case_light_type_change_batch_remove():
    old, shared = _alias_by_type_change("AREA")
    bpy.data.batch_remove([shared])
    return _stale_checks(old, "PointLight")


def case_light_type_change_orphans_purge():
    old, _shared = _alias_by_type_change("SUN")
    del _shared
    bpy.data.orphans_purge()
    return _stale_checks(old, "PointLight")


def case_light_several_type_changes():
    """Each wrapper left by a chain of type changes goes with the light."""
    first = _new_light()
    kept = [first]
    for kind in ("SPOT", "AREA", "SUN"):
        first.type = kind
        kept.append(bpy.data.lights[first.name])
    bpy.data.lights.remove(kept[-1])
    results = {}
    for wrapper, name in zip(kept[:-1], ("PointLight", "SpotLight", "AreaLight")):
        for label, value in _stale_checks(wrapper, name).items():
            results["%s_%s" % (name, label)] = value
    return results


def case_texture_type_recast_keep_old():
    tex = bpy.data.textures.new("Tex", "NONE")
    tex.type = "CLOUDS"
    recast = tex.type_recast()
    assert type(recast) is not type(tex)
    bpy.data.textures.remove(recast)
    return _stale_checks(tex, "Texture")


def case_texture_type_recast_keep_recast():
    """The wrapper passed to remove() raised as in stock; the recast one must too."""
    tex = bpy.data.textures.new("Tex", "NONE")
    tex.type = "CLOUDS"
    recast = tex.type_recast()
    bpy.data.textures.remove(tex)
    return _stale_checks(recast, "CloudsTexture")


def case_subclass_wrapper():
    class Sub(bpy.types.Object):
        __slots__ = ()

    obj = bpy.data.objects.new("Obj", None)
    obj["custom"] = 1.0
    sub = Sub(obj)
    bpy.data.objects.remove(obj)
    return _stale_checks(sub, "Sub")


def case_base_class_wrapper():
    obj = bpy.data.objects.new("Obj", None)
    base = bpy.types.ID(obj)
    bpy.data.objects.remove(obj)
    return _stale_checks(base, "ID")


def case_subclass_of_alias():
    """``Sub(old)`` made from a wrapper a type change left behind goes with the light too."""
    class SubLight(bpy.types.Light):
        __slots__ = ()

    old, shared = _alias_by_type_change("SPOT")
    sub = SubLight(old)
    bpy.data.lights.remove(shared)
    results = {"old_%s" % k: v for k, v in _stale_checks(old, "PointLight").items()}
    results.update({"sub_%s" % k: v for k, v in _stale_checks(sub, "SubLight").items()})
    return results


def case_live_aliases_still_work():
    """While the ID lives, every alias reads and writes it and lookups keep one shared wrapper."""
    results = {}
    old, shared = _alias_by_type_change("SPOT")
    old.energy = 42.0
    results["alias_writes_the_light"] = "ok" if shared.energy == 42.0 else "energy %r" % shared.energy
    results["lookup_returns_the_shared_wrapper"] = "ok" if bpy.data.lights[old.name] is shared else "new wrapper"
    recast = shared.type_recast()
    results["recast_of_current_type_is_shared"] = "ok" if recast is shared else "new wrapper"

    class Sub(bpy.types.Object):
        __slots__ = ()

    obj = bpy.data.objects.new("Live", None)
    sub = Sub(obj)
    sub.location = (1.0, 2.0, 3.0)
    results["subclass_writes_the_object"] = "ok" if tuple(obj.location) == (1.0, 2.0, 3.0) else "not written"
    results["object_lookup_still_shared"] = "ok" if bpy.data.objects["Live"] is obj else "new wrapper"
    return results


def _raises_reference_error(attempt):
    try:
        attempt()
        return "no error"
    except ReferenceError:
        return "ok"
    except Exception as error:  # A wrong error is a failed check, not a crash.
        return "%s: %s" % (type(error).__name__, error)


def _instancing_scene(count=4):
    """A parent instancing a child on each of its vertices, and the evaluated graph."""
    mesh = bpy.data.meshes.new("Points")
    mesh.from_pydata([(float(i), 0.0, 0.0) for i in range(count)], [], [])
    parent = bpy.data.objects.new("Instancer", mesh)
    parent.instance_type = "VERTS"
    child = bpy.data.objects.new("Instanced", bpy.data.meshes.new("Child"))
    child["custom"] = 1.0
    child.parent = parent
    for obj in (parent, child):
        bpy.context.scene.collection.objects.link(obj)
    return bpy.context.evaluated_depsgraph_get()


def case_instance_object_kept_after_iteration():
    """B14's second path: stock crashed reading an instance's ``object`` kept past the loop."""
    depsgraph = _instancing_scene()
    kept = [inst.object for inst in depsgraph.object_instances if inst.is_instance]
    results = {"instances_found": "ok" if kept else "none"}
    if kept:
        results.update({"kept_%s" % k: v for k, v in _stale_checks(kept[0], "Object").items()})
    return results


def case_instance_object_kept_across_a_step():
    """Kept into the next loop step, stock read the next instance's data; it raises instead."""
    depsgraph = _instancing_scene()
    first, results = None, {}
    for inst in depsgraph.object_instances:
        if not inst.is_instance:
            continue
        if first is None:
            first = inst.object
            results["own_step_reads_its_instance"] = "ok" if first.name == "Instanced" else first.name
            continue
        results["next_step_raises"] = _raises_reference_error(lambda: first.matrix_world)
        break
    results["original_still_usable"] = "ok" if bpy.data.objects["Instanced"].name == "Instanced" else "lost"
    return results


def case_iterator_kept_across_graph_free():
    depsgraph = _instancing_scene()
    instances = iter(depsgraph.object_instances)
    objects = iter(depsgraph.objects)
    next(instances)
    next(objects)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return {"instances_iterator": _raises_reference_error(lambda: next(instances)),
            "objects_iterator": _raises_reference_error(lambda: next(objects)),
            "graph_wrapper": _raises_reference_error(lambda: depsgraph.mode)}


def case_iterator_kept_across_owner_removal():
    obj = bpy.data.objects.new("Owner", bpy.data.meshes.new("OwnerMesh"))
    for kind in ("SUBSURF", "BEVEL", "SOLIDIFY"):
        obj.modifiers.new(kind.title(), kind)
    modifiers = iter(obj.modifiers)
    next(modifiers)
    mesh = bpy.data.meshes.new("Verts")
    mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [], [(0, 1, 2)])
    vertices = iter(mesh.vertices)
    next(vertices)
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)
    return {"modifiers_iterator": _raises_reference_error(lambda: next(modifiers)),
            "vertices_iterator": _raises_reference_error(lambda: next(vertices))}


def case_views_kept_after_removal():
    mesh = bpy.data.meshes.new("Viewed")
    mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("Viewed", mesh)
    attributes, display = mesh.attributes, obj.display
    results = {"views_work_while_live": "ok" if len(attributes) > 0 and display.show_shadows in (True, False)
               else "wrong"}
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)
    results["attributes"] = _raises_reference_error(lambda: len(attributes))
    results["display"] = _raises_reference_error(lambda: display.show_shadows)
    return results


def case_depsgraph_update_kept_after_its_id():
    _instancing_scene()
    kept = []

    def collect(_scene, depsgraph):
        kept.extend(depsgraph.updates)

    bpy.app.handlers.depsgraph_update_post.append(collect)
    try:
        bpy.data.objects["Instanced"].location.x += 1.0
        bpy.context.view_layer.update()
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(collect)
    results = {"updates_seen": "ok" if kept else "none",
               "updates_read_in_handler_scope": "ok" if all(u.id is not None for u in kept) else "no id"}
    for name in ("Instanced", "Instancer"):
        bpy.data.objects.remove(bpy.data.objects[name])
    bpy.context.view_layer.update()
    outcomes = []
    for update in kept:
        try:
            update.id.name
            outcomes.append("live")
        except ReferenceError:
            outcomes.append("raised")
    results["removed_ids_raise"] = "ok" if "raised" in outcomes else repr(outcomes)
    return results


def _object_with_data():
    mesh = bpy.data.meshes.new("Owned")
    mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("Owned", mesh)
    obj.modifiers.new("Subdivision", "SUBSURF")
    obj["group"] = {"a": 1, "b": 2}
    obj["values"] = [1.0, 2.0, 3.0]
    obj["number"] = 3
    return obj, mesh


def case_sub_data_kept_after_owner_removal():
    """Property, array, collection, function, sub-struct and custom-property wrappers of a removed
    ID raise; stock 5.2 read freed memory or crashed (the review's t_prop_wrapper.py)."""
    obj, mesh = _object_with_data()
    kept = {
        "modifiers_len": (obj.modifiers, len),
        "modifiers_iter": (obj.modifiers, lambda m: list(iter(m))),
        "modifiers_keys": (obj.modifiers, lambda m: m.keys()),
        "location_vector": (obj.location, tuple),
        "modifier_struct": (obj.modifiers[0], lambda m: m.name),
        "bound_function": (obj.modifiers.new, lambda f: f("x", "BEVEL")),
        "vertex_struct": (mesh.vertices[0], lambda v: tuple(v.co)),
        "vertex_co_vector": (mesh.vertices[0].co, tuple),
        "id_group": (obj["group"], lambda g: list(g.keys())),
        "id_group_keys_view": (obj["group"].keys(), list),
        "id_array": (obj["values"], list),
        "ui_data": (obj.id_properties_ui("number"), lambda u: u.as_dict()),
    }
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)
    _churn = [bpy.data.objects.new("Churn%d" % i, None) for i in range(50)]
    return {name: _raises_reference_error(lambda w=wrapper, use=use: use(w)) for name, (wrapper, use) in kept.items()}


def case_sub_data_live_still_works():
    obj, mesh = _object_with_data()
    location = obj.location
    location.x = 2.0
    group = obj["group"]
    ui = obj.id_properties_ui("number")
    ui.update(min=0, max=10)
    return {"vector_writes": "ok" if obj.location.x == 2.0 else "not written",
            "group_reads": "ok" if group.to_dict() == {"a": 1, "b": 2} else "wrong",
            "array_reads": "ok" if list(obj["values"]) == [1.0, 2.0, 3.0] else "wrong",
            "ui_reads": "ok" if ui.as_dict()["max"] == 10 else "wrong",
            "function_calls": "ok" if obj.modifiers.new("Bevel", "BEVEL").name == "Bevel" else "wrong",
            "vertex_reads": "ok" if tuple(mesh.vertices[1].co) == (1.0, 0.0, 0.0) else "wrong"}


def case_view_layer_graph_iterator_after_layer_removal():
    """The review's t_vl_iter2.py: a graph reached through `view_layer.depsgraph` (whose pointer
    names the scene as owner) frees with its view layer; stock 5.2 crashed at the next step."""
    scene = bpy.context.scene
    for i in range(3):
        scene.collection.objects.link(bpy.data.objects.new("Layered%d" % i, None))
    layer = scene.view_layers.new("Extra")
    layer.update()
    depsgraph = layer.depsgraph
    results = {"graph_has_no_owner": "ok" if depsgraph.id_data is None else repr(depsgraph.id_data),
               "graph_has_no_ancestors": "ok" if list(depsgraph.rna_ancestors()) == [] else "ancestors"}
    objects = iter(depsgraph.objects)
    instances = iter(depsgraph.object_instances)
    next(objects)
    next(instances)
    scene.view_layers.remove(layer)
    results["objects_iterator"] = _raises_reference_error(lambda: next(objects))
    results["instances_iterator"] = _raises_reference_error(lambda: next(instances))
    results["graph_wrapper"] = _raises_reference_error(lambda: depsgraph.mode)
    return results


def case_instance_kept_across_a_step():
    """A DepsgraphObjectInstance kept into the next step read another instance in stock."""
    depsgraph = _instancing_scene()
    first, results = None, {}
    for inst in depsgraph.object_instances:
        if first is None:
            first = inst
            results["own_step_reads"] = "ok" if first.matrix_world is not None else "none"
            continue
        results["next_step_raises"] = _raises_reference_error(lambda: first.matrix_world)
        break
    return results


def case_evaluated_data_kept_across_reevaluation():
    """The review's t_data_eval.py: an owned evaluated point cloud is freed on re-evaluation, and
    an evaluated object's sub-data is replaced in place; kept wrappers of either raise."""
    cloud = bpy.data.pointclouds.new("Cloud")
    obj = bpy.data.objects.new("Cloud", cloud)
    bpy.context.scene.collection.objects.link(obj)
    group = bpy.data.node_groups.new("Points", "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    points = group.nodes.new("GeometryNodePoints")
    points.inputs["Count"].default_value = 100
    group.links.new(points.outputs[0], group.nodes.new("NodeGroupOutput").inputs[0])
    obj.modifiers.new("Nodes", "NODES").node_group = group
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    data = evaluated.data
    modifier = evaluated.modifiers[0]
    results = {"live_reads": "ok" if len(data.points) == 100 and modifier.name == "Nodes" else "wrong"}
    points.inputs["Count"].default_value = 7
    obj.update_tag()  # The evaluated object is copied again in place, its modifiers with it.
    depsgraph.update()
    results["evaluated_data"] = _raises_reference_error(lambda: len(data.points))
    results["evaluated_sub_struct"] = _raises_reference_error(lambda: modifier.name)
    fresh = obj.evaluated_get(depsgraph)
    results["evaluated_object_still_reads"] = "ok" if len(fresh.data.points) == 7 and \
        fresh.modifiers[0].name == "Nodes" else "wrong"
    return results


def case_view_kept_across_undo():
    """Undo re-reads a changed ID; its kept views raise like the ID wrapper itself (stock 5.2 read
    the old memory through `mesh.attributes`)."""
    mesh = bpy.data.meshes.new("Undone")
    mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [], [(0, 1, 2)])
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("Undone", mesh))
    bpy.ops.ed.undo_push(message="before")
    mesh.vertices[0].co.x = 5.0
    bpy.ops.ed.undo_push(message="after")
    attributes, vertices = mesh.attributes, mesh.vertices
    bpy.ops.ed.undo()
    return {"mesh": _raises_reference_error(lambda: mesh.name),
            "attributes_view": _raises_reference_error(lambda: len(attributes)),
            "vertices_collection": _raises_reference_error(lambda: len(vertices)),
            "undone_value": "ok" if bpy.data.meshes["Undone"].vertices[0].co.x == 0.0 else "not undone"}


def case_depsgraph_update_ancestors():
    _instancing_scene()
    kept = []

    def collect(_scene, depsgraph):
        for update in depsgraph.updates:
            kept.append([type(a).__name__ for a in update.rna_ancestors()])

    bpy.app.handlers.depsgraph_update_post.append(collect)
    try:
        bpy.data.objects["Instanced"].location.x += 1.0
        bpy.context.view_layer.update()
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(collect)
    bad = [names for names in kept if "ViewLayer" in names or "Depsgraph" in names]
    return {"updates_seen": "ok" if kept else "none", "no_view_layer_ancestor": "ok" if not bad else repr(bad)}


def case_shared_wrapper_reinit_refused():
    first = bpy.data.objects.new("First", None)
    second = bpy.data.objects.new("Second", None)
    try:
        first.__init__(second)
        refused = "accepted"
    except TypeError:
        refused = "ok"
    return {"refused": refused, "unchanged": "ok" if first.name == "First" else first.name}


def case_live_iteration_still_works():
    depsgraph = _instancing_scene()
    instances = [(inst.is_instance, inst.object.name, tuple(inst.matrix_world.translation))
                 for inst in depsgraph.object_instances]
    positions = sorted(t[2][0] for t in instances if t[0])
    mesh = bpy.data.meshes["Points"]
    obj = bpy.data.objects["Instancer"]
    obj.modifiers.new("Weld", "WELD")
    return {"instances_listed": "ok" if positions == [0.0, 1.0, 2.0, 3.0] else repr(positions),
            "instance_names": "ok" if {t[1] for t in instances if t[0]} == {"Instanced"} else repr(instances),
            "vertices_iterate": "ok" if [v.index for v in mesh.vertices] == [0, 1, 2, 3] else "wrong",
            "modifiers_iterate": "ok" if [m.name for m in obj.modifiers] == ["Weld"] else "wrong",
            "graph_shared_wrapper": "ok" if bpy.context.evaluated_depsgraph_get() is depsgraph else "new wrapper"}


def case_alias_dropped_before_removal():
    """An alias freed before its ID is (unlinked on deallocation) leaves the removal clean."""
    import gc

    class Sub(bpy.types.Object):
        __slots__ = ()

    old, shared = _alias_by_type_change("SPOT")
    obj = bpy.data.objects.new("Obj", None)
    for _ in range(50):
        Sub(obj)
        bpy.types.ID(obj)
    del old
    gc.collect()
    bpy.data.lights.remove(shared)
    bpy.data.objects.remove(obj)
    junk = [bpy.data.meshes.new("junk%d" % i) for i in range(50)]
    return {"removal_after_dropped_aliases": "ok" if len(junk) == 50 and len(bpy.data.lights) == 0 else "state"}


def case_keymaps_kept_across_file_reads():
    """An add-on keeps `(km, kmi)` from register to unregister. A file read moves the key-maps into
    the window manager it keeps and frees the other one, so their RNA owner does not own them:
    they stay valid (a 1.0.10 candidate raised in every add-on's unregister, pose_library's first)."""
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:
        return {"addon_keyconfig": "missing"}
    keymap = keyconfig.keymaps.new(name="Object Mode", space_type="EMPTY")
    item = keymap.keymap_items.new("object.select_all", "A", "PRESS", ctrl=True, shift=True, alt=True)
    item.properties.action = "TOGGLE"
    properties = item.properties
    saved = os.path.join(tempfile.mkdtemp(prefix="angelo-keymaps-"), "kept.blend")
    bpy.ops.wm.save_as_mainfile(filepath=saved)
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.wm.open_mainfile(filepath=saved, load_ui=True)
    bpy.ops.wm.open_mainfile(filepath=saved, load_ui=False)
    results = {"keymap_reads": "ok" if keymap.name == "Object Mode" else keymap.name,
               "item_reads": "ok" if item.idname == "object.select_all" and item.ctrl else "wrong",
               "properties_read": "ok" if properties.action == "TOGGLE" else properties.action}
    count = len(keymap.keymap_items)
    keymap.keymap_items.remove(item)
    results["item_removed"] = "ok" if len(keymap.keymap_items) == count - 1 else "not removed"
    keyconfig.keymaps.remove(keymap)
    return results


def _overridden_object():
    """A local override of a linked object with a modifier, its location overridden."""
    source = bpy.data.objects.new("Source", bpy.data.meshes.new("SourceMesh"))
    source.modifiers.new("Bevel", "BEVEL")
    library = os.path.join(tempfile.mkdtemp(prefix="angelo-override-"), "library.blend")
    bpy.data.libraries.write(library, {source})
    bpy.data.objects.remove(source)
    with bpy.data.libraries.load(library, link=True) as (data_from, data_to):
        data_to.objects = ["Source"]
    override = data_to.objects[0].override_create(remap_local_usages=True)
    bpy.context.scene.collection.objects.link(override)
    override.location.x = 3.0
    override.override_library.operations_update()
    return override


def case_override_reset_sub_data():
    """Resetting an override re-applies it into a temporary ID and swaps contents with it, keeping
    the ID; the old contents are freed with the temporary ID. Wrappers into them raise (stock 5.2
    read freed memory); the override, its properties and collections stay valid and read anew."""
    override = _overridden_object()
    modifier, modifiers, location = override.modifiers[0], override.modifiers, override.location
    walk = iter(override.modifiers)
    override.override_library.reset()
    _churn = [bpy.data.objects.new("Churn%d" % i, None) for i in range(50)]
    return {"modifier": _raises_reference_error(lambda: modifier.name),
            "modifiers_iterator": _raises_reference_error(lambda: next(walk)),
            "modifiers_collection": "ok" if [m.name for m in modifiers] == ["Bevel"] else "wrong",
            "location_vector": "ok" if tuple(location)[0] == 0.0 else repr(tuple(location)),
            "override_reads": "ok" if override.location.x == 0.0 else repr(override.location.x)}


CASES = {name[len("case_"):]: fn for name, fn in globals().items() if name.startswith("case_")}


def run_child(name):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        results = CASES[name]()
    except Exception as error:
        results = {"setup": "%s: %s" % (type(error).__name__, error)}
    # Allocations after the checks: a stale wrapper that slipped through would be read from reused memory.
    _churn = [bpy.data.meshes.new("churn%d" % i) for i in range(100)]
    print("ANGELO_ALIAS_CASE " + json.dumps({"case": name, "results": results}), flush=True)


def run_parent():
    assert "--factory-startup" in sys.argv, "Use a disposable factory-startup Blender instance"
    assert bpy.app.background, "Run with --background"
    out = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-instance-alias-wrappers"))
    out.mkdir(parents=True, exist_ok=True)
    report = {"success": False, "cases": {}, "failed": [], "binary": bpy.app.binary_path, "paid_requests": 0}
    for name in CASES:
        command = [bpy.app.binary_path, "--background", "--factory-startup", "--python-exit-code", "3",
                   "--python", __file__, "--", CHILD_FLAG, name]
        try:
            child = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            report["cases"][name] = {"exit": "timeout"}
            report["failed"].append(name)
            continue
        lines = [line for line in child.stdout.splitlines() if line.startswith("ANGELO_ALIAS_CASE ")]
        results = json.loads(lines[-1].split(" ", 1)[1])["results"] if lines else {}
        ok = child.returncode == 0 and bool(results) and all(value == "ok" for value in results.values())
        report["cases"][name] = {"exit": child.returncode, "results": results,
                                 "stderr_tail": child.stderr[-600:] if not ok else ""}
        if not ok:
            report["failed"].append(name)
    report["success"] = bool(report["cases"]) and not report["failed"]
    (out / "instance-alias-wrappers.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_INSTANCE_ALIAS_WRAPPERS", json.dumps({"success": report["success"], "failed": report["failed"]}),
          flush=True)


if CHILD_FLAG in sys.argv:
    run_child(sys.argv[sys.argv.index(CHILD_FLAG) + 1])
else:
    run_parent()
