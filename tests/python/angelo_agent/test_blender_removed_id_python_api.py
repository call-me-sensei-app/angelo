# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A removed data-block passed to an ID-taking ``bpy`` helper raises ``ReferenceError``.

``bpy.data.objects.remove(obj)`` clears ``obj``'s RNA pointer, and every
ordinary ``bpy`` access then raises ``ReferenceError``. The C helpers that read
an ID straight from a Python object (``pyrna_id_FromPyObject`` and
``pyrna_id_CheckPyObject``) did not check, and dereferenced the cleared type:
stock Blender 5.2 crashes (SIGSEGV) on ``bpy.data.user_map(subset=[obj])``
after ``bpy.data.objects.remove(obj)``, as it does on ``file_path_map``,
``file_path_foreach``, ``batch_remove``, ``libraries.write``, a custom
property set to the removed ID, ``GeometrySet.from_evaluated_object``,
``InlineShaderNodes.from_material/light/world`` and a USD hook's
``export_texture``. An agent script crashed the app this way. Angelo raises
the usual ``ReferenceError`` in each (``bpy_rna.cc``) for a wrapper Blender
invalidated: the one passed to ``remove()``, or the ID's current shared wrapper
when ``remove()``, ``batch_remove`` or ``orphans_purge`` frees it. The same
holds for removed non-ID data (a modifier), for custom properties set through
lists, nested groups, ``update()`` and a statically typed (``bpy.props``) ID
property, and ``file_path_foreach`` checks its whole subset before visiting
and visits a copy, so a callback that grows or clears the caller's list is safe.
Each helper still works for live IDs and still raises ``TypeError`` for a
value that is not an ID.

Not covered, as in stock Blender: a wrapper Blender never invalidates, kept
from before the ID's type changed (a light's ``type``, ``type_recast()``) or
made by a Python subclass constructor (``doc/angelo/agent_execution.rst``).

Background only. No add-on, provider, account or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv, "Use a disposable factory-startup Blender instance"
assert bpy.app.background, "Run with --background"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-removed-id-python-api"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "checks": {}, "errors": {}, "paid_requests": 0, "binary": bpy.app.binary_path}
REMOVED_TEXT = "has been removed"


def check(name, value):
    REPORT["checks"][name] = bool(value)  # Every case runs; the suite fails at the end.


def raises(name, error_type, call, *expected):
    """``call()`` raises ``error_type`` whose text has every ``expected`` part; returns whether it did."""
    try:
        call()
        error = None
    except Exception as caught:
        error = caught
    REPORT["errors"][name] = None if error is None else f"{type(error).__name__}: {error}"
    ok = type(error) is error_type and all(text in str(error) for text in expected)
    check(name, ok)
    return ok


def refuses_removed(name, call, type_name):
    return raises(name + "_raises_reference_error", ReferenceError, call,
                  f"StructRNA of type {type_name} {REMOVED_TEXT}")


def new_object(name):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    return obj


def removed_object(name="Removed"):
    obj = new_object(name)
    bpy.data.objects.remove(obj, do_unlink=True)
    return obj


def removed_modifier():
    """A removed modifier: removed data that is not an ID."""
    modifier = new_object("Modifier Owner").modifiers.new("Removed Modifier", "SUBSURF")
    modifier.id_data.modifiers.remove(modifier)
    return modifier


def node_material(name):
    material = bpy.data.materials.new(name)
    if material.node_tree is None:  # Materials have nodes by default since 5.0; older API kept for clarity.
        material.use_nodes = True
    return material


def file_image():
    """A loaded image with a real file path, for the path helpers."""
    path = OUT / "probe_texture.png"
    generated = bpy.data.images.new("Probe Source", 4, 4)
    generated.filepath_raw = str(path)
    generated.file_format = "PNG"
    generated.save()
    bpy.data.images.remove(generated)
    return bpy.data.images.load(str(path), check_existing=False), str(path)


def user_map_cases():
    refuses_removed("user_map_subset", lambda: bpy.data.user_map(subset=[removed_object()]), "Object")
    live = new_object("Live Map")
    refuses_removed("user_map_mixed_subset", lambda: bpy.data.user_map(subset=[live, removed_object()]), "Object")
    refuses_removed("user_map_subset_set", lambda: bpy.data.user_map(subset={removed_object()}), "Object")
    scene, mesh = bpy.context.scene, live.data
    everything = bpy.data.user_map()
    check("user_map_without_subset_maps_live_ids", scene in everything.get(live, ()) and live in everything[mesh])
    subset = bpy.data.user_map(subset=[live, mesh])
    check("user_map_with_subset_maps_only_the_subset", set(subset) == {live, mesh} and scene in subset[live]
          and subset[mesh] == {live})
    check("user_map_value_types_still_filter",
          bpy.data.user_map(subset=[mesh], value_types={"OBJECT"}) == {mesh: {live}})
    check("user_map_key_types_still_filter",
          all(isinstance(key, bpy.types.Mesh) for key in bpy.data.user_map(key_types={"MESH"})))
    raises("user_map_non_id_still_type_error", TypeError, lambda: bpy.data.user_map(subset=[1]),
           "Expected an ID type in `subset` iterable, not int")


def path_cases():
    image, path = file_image()
    removed_image = bpy.data.images.new("Removed Image", 4, 4)
    bpy.data.images.remove(removed_image)
    refuses_removed("file_path_map_subset", lambda: bpy.data.file_path_map(subset=[removed_image]), "Image")
    refuses_removed("file_path_map_mixed_subset",
                    lambda: bpy.data.file_path_map(subset=[image, removed_image]), "Image")
    check("file_path_map_with_subset_maps_live_paths", bpy.data.file_path_map(subset=[image]) == {image: {path}})
    check("file_path_map_without_subset_maps_live_paths", path in bpy.data.file_path_map().get(image, ()))
    raises("file_path_map_non_id_still_type_error", TypeError, lambda: bpy.data.file_path_map(subset=["x"]),
           "Expected an ID type in `subset` iterable, not str")

    visited = []

    def visit(owner, file_path, _meta):
        visited.append((owner, file_path))

    def rewrite(owner, file_path, meta):
        visit(owner, file_path, meta)
        return file_path + ".rewritten"

    refuses_removed("file_path_foreach_subset", lambda: bpy.data.file_path_foreach(visit, subset=[removed_image]),
                    "Image")
    refuses_removed("file_path_foreach_live_then_removed",
                    lambda: bpy.data.file_path_foreach(rewrite, subset=[image, removed_image]), "Image")
    raises("file_path_foreach_live_then_non_id_still_type_error", TypeError,
           lambda: bpy.data.file_path_foreach(rewrite, subset=[image, 1]), "not int")
    check("file_path_foreach_refusal_visits_and_rewrites_nothing", visited == [] and image.filepath == path)
    bpy.data.file_path_foreach(visit, subset=[image])
    check("file_path_foreach_with_subset_visits_live_paths", visited == [(image, path)])

    # The callback may grow or clear the caller's list (stock crashed on growth): the visit uses a copy.
    second, _same_path = file_image()
    subset, visited[:] = [image], []

    def grow(owner, file_path, meta):
        visit(owner, file_path, meta)
        subset.extend([image] * 1000)

    bpy.data.file_path_foreach(grow, subset=subset)
    check("file_path_foreach_callback_growing_the_subset", visited == [(image, path)] and len(subset) == 1001)
    subset, visited[:] = [image, second], []

    def clear(owner, file_path, meta):
        visit(owner, file_path, meta)
        subset.clear()

    bpy.data.file_path_foreach(clear, subset=subset)
    check("file_path_foreach_callback_clearing_the_subset", visited == [(image, path), (second, path)])


def batch_remove_cases():
    kept = new_object("Kept By Refusal")
    refuses_removed("batch_remove", lambda: bpy.data.batch_remove([removed_object()]), "Object")
    refuses_removed("batch_remove_mixed", lambda: bpy.data.batch_remove(ids=[kept, removed_object()]), "Object")
    check("batch_remove_refusal_removes_nothing", "Kept By Refusal" in bpy.data.objects)
    raises("batch_remove_non_id_still_type_error", TypeError, lambda: bpy.data.batch_remove([None]),
           "Expected an ID type, not NoneType")
    first, second = new_object("Batch A"), new_object("Batch B")
    bpy.data.batch_remove([first, second, kept])
    check("batch_remove_removes_live_ids", not {"Batch A", "Batch B", "Kept By Refusal"} & set(bpy.data.objects.keys()))
    # Freeing an ID any way invalidates its current shared wrapper, so the helpers refuse that too.
    refuses_removed("user_map_after_batch_remove", lambda: bpy.data.user_map(subset=[first]), "Object")
    orphan = bpy.data.meshes.new("Orphan Probe")
    check("orphans_purge_takes_no_ids_and_still_runs", bpy.data.orphans_purge() >= 1)
    refuses_removed("user_map_after_orphans_purge", lambda: bpy.data.user_map(subset=[orphan]), "Mesh")


def library_write_cases():
    refused, written = OUT / "removed.blend", OUT / "live.blend"
    refuses_removed("libraries_write", lambda: bpy.data.libraries.write(str(refused), {removed_object()}), "Object")
    check("libraries_write_refusal_writes_no_file", not refused.exists())
    bpy.data.libraries.write(str(written), {new_object("Written")})
    check("libraries_write_writes_live_ids", written.is_file() and written.stat().st_size > 0)


def custom_property_cases():
    holder, target = new_object("Holder"), new_object("Target")
    refuses_removed("custom_property_set", lambda: holder.__setitem__("ref", removed_object()), "Object")
    refuses_removed("custom_property_nested", lambda: holder.__setitem__("group", {"a": removed_object()}), "Object")
    check("custom_property_refusals_add_nothing", "ref" not in holder and "group" not in holder)
    holder["ref"] = target
    holder["group"] = {"a": target, "n": 1}
    check("custom_property_live_id_still_assigns", holder["ref"] == target and holder["group"]["a"] == target)
    raises("custom_property_non_id_struct_still_type_error", TypeError,
           lambda: holder.__setitem__("bad", bpy.context.scene.render))
    refuses_removed("custom_property_list", lambda: holder.__setitem__("list", [removed_object()]), "Object")
    refuses_removed("custom_property_list_of_dicts",
                    lambda: holder.__setitem__("list", [{"a": 1}, {"a": removed_object()}]), "Object")
    refuses_removed("custom_property_group_item", lambda: holder["group"].__setitem__("a", removed_object()), "Object")
    refuses_removed("custom_property_group_new_item",
                    lambda: holder["group"].__setitem__("new", removed_object()), "Object")
    refuses_removed("custom_property_group_update", lambda: holder["group"].update({"b": removed_object()}), "Object")
    check("custom_property_route_refusals_change_nothing", "list" not in holder and holder["group"]["a"] == target
          and set(holder["group"].keys()) == {"a", "n"})
    holder["list"] = [target]
    check("custom_property_live_id_list_still_assigns", holder["list"][0] == target)

    # A bpy.props pointer is a statically typed system ID property: its own TypeError must not hide the cause.
    bpy.types.Object.removed_id_probe_pointer = bpy.props.PointerProperty(type=bpy.types.Object)
    bpy.types.Object.removed_id_probe_int = bpy.props.IntProperty()
    try:
        holder.removed_id_probe_pointer = target
        system = holder.bl_system_properties_get()
        refuses_removed("static_type_id_property",
                        lambda: system.__setitem__("removed_id_probe_pointer", removed_object()), "Object")
        refuses_removed("rna_pointer_property",
                        lambda: setattr(holder, "removed_id_probe_pointer", removed_object()), "Object")
        check("static_type_id_property_refusals_keep_the_value", holder.removed_id_probe_pointer == target)
        raises("static_type_id_property_non_id_still_type_error", TypeError,
               lambda: system.__setitem__("removed_id_probe_pointer", 1), "Cannot assign a 'int' value")
        # Only a removed ID's ReferenceError is kept: other failures keep stock's TypeError.
        holder.removed_id_probe_int = 5
        raises("static_type_int_property_overflow_still_type_error", TypeError,
               lambda: system.__setitem__("removed_id_probe_int", 2 ** 40),
               "Cannot assign a 'int' value to the existing 'removed_id_probe_int' Int IDProperty")
        check("static_type_int_property_overflow_keeps_the_value", holder.removed_id_probe_int == 5)
    finally:
        del bpy.types.Object.removed_id_probe_pointer
        del bpy.types.Object.removed_id_probe_int


def native_type_cases():
    refuses_removed("geometry_set_from_evaluated_object",
                    lambda: bpy.types.GeometrySet.from_evaluated_object(removed_object()), "Object")
    live = new_object("Evaluated")
    evaluated = live.evaluated_get(bpy.context.evaluated_depsgraph_get())
    check("geometry_set_from_live_object", bpy.types.GeometrySet.from_evaluated_object(evaluated) is not None)
    raises("geometry_set_non_id_still_type_error", TypeError,
           lambda: bpy.types.GeometrySet.from_evaluated_object(1), "Expected an Object, not int")
    material = node_material("Removed Material")
    bpy.data.materials.remove(material)
    refuses_removed("inline_shader_nodes_from_material",
                    lambda: bpy.types.InlineShaderNodes.from_material(material), "Material")
    check("inline_shader_nodes_from_live_material",
          bpy.types.InlineShaderNodes.from_material(node_material("Live Material")) is not None)
    light = bpy.data.lights.new("Removed Light", "POINT")
    bpy.data.lights.remove(light)
    refuses_removed("inline_shader_nodes_from_light",
                    lambda: bpy.types.InlineShaderNodes.from_light(light), "PointLight")
    world = bpy.data.worlds.new("Removed World")
    bpy.data.worlds.remove(world)
    refuses_removed("inline_shader_nodes_from_world", lambda: bpy.types.InlineShaderNodes.from_world(world), "World")


def non_id_struct_cases():
    """Removed data that is not an ID raises the same ReferenceError in every helper."""
    calls = {
        "user_map": lambda m: bpy.data.user_map(subset=[m]),
        "file_path_map": lambda m: bpy.data.file_path_map(subset=[m]),
        "file_path_foreach": lambda m: bpy.data.file_path_foreach(lambda *_args: None, subset=[m]),
        "batch_remove": lambda m: bpy.data.batch_remove([m]),
        "libraries_write": lambda m: bpy.data.libraries.write(str(OUT / "removed-struct.blend"), {m}),
        "custom_property": lambda m: new_object("Struct Holder").__setitem__("ref", m),
        "geometry_set": lambda m: bpy.types.GeometrySet.from_evaluated_object(m),
        "inline_shader_nodes_from_material": lambda m: bpy.types.InlineShaderNodes.from_material(m),
        "inline_shader_nodes_from_light": lambda m: bpy.types.InlineShaderNodes.from_light(m),
        "inline_shader_nodes_from_world": lambda m: bpy.types.InlineShaderNodes.from_world(m),
    }
    for name, call in calls.items():
        modifier = removed_modifier()
        refuses_removed(name + "_removed_non_id_struct", lambda: call(modifier), "SubsurfModifier")


def rna_function_cases():
    """RNA function parameters were already checked; pinned so the whole family stays covered."""
    refuses_removed("rna_remove_already_checked", lambda: bpy.data.objects.remove(removed_object()), "Object")


class RemovedTextureHook(bpy.types.USDHook):
    bl_idname = "angelo_removed_texture_hook"
    bl_label = "Angelo Removed Texture Hook"
    removed = None
    removed_struct = None
    live = None
    results = {}

    @staticmethod
    def on_material_export(export_context, _bl_material, _usd_material):
        hook = RemovedTextureHook
        for key, image in (("removed", hook.removed), ("removed_struct", hook.removed_struct), ("live", hook.live)):
            try:
                RemovedTextureHook.results[key] = ("returned", export_context.export_texture(image))
            except Exception as error:
                RemovedTextureHook.results[key] = (type(error).__name__, str(error))
        return True


def usd_hook_cases():
    if not bpy.app.build_options.usd:
        REPORT["usd"] = "not built"
        return
    live_image, _path = file_image()
    removed_image = bpy.data.images.new("Removed Hook Image", 4, 4)
    bpy.data.images.remove(removed_image)
    RemovedTextureHook.removed, RemovedTextureHook.live = removed_image, live_image
    RemovedTextureHook.removed_struct = removed_modifier()
    holder = new_object("USD Holder")
    holder.data.materials.append(node_material("USD Material"))
    holder.data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    bpy.utils.register_class(RemovedTextureHook)
    try:
        bpy.ops.wm.usd_export(filepath=str(OUT / "hook.usda"), export_materials=True, export_textures_mode="KEEP")
    finally:
        bpy.utils.unregister_class(RemovedTextureHook)
    results = RemovedTextureHook.results
    REPORT["usd"] = {key: list(value) for key, value in results.items()}
    removed = results.get("removed", ("", ""))
    check("usd_hook_export_texture_raises_reference_error",
          removed[0] == "ReferenceError" and f"StructRNA of type Image {REMOVED_TEXT}" in removed[1])
    removed_struct = results.get("removed_struct", ("", ""))
    check("usd_hook_export_texture_removed_non_id_struct_raises_reference_error",
          removed_struct[0] == "ReferenceError"
          and f"StructRNA of type SubsurfModifier {REMOVED_TEXT}" in removed_struct[1])
    check("usd_hook_export_texture_live_image_returns_a_path",
          results.get("live", ("", ""))[0] == "returned" and isinstance(results["live"][1], str))


def run():
    for step in (user_map_cases, path_cases, batch_remove_cases, library_write_cases, custom_property_cases,
                 native_type_cases, non_id_struct_cases, rna_function_cases, usd_hook_cases):
        try:
            step()
        except Exception:
            REPORT.setdefault("error", {})[step.__name__] = traceback.format_exc()


def finish():
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values()) and "error" not in REPORT
    (OUT / "removed-id-python-api.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_REMOVED_ID_PYTHON_API", json.dumps({"success": REPORT["success"],
                                                      "checks": len(REPORT["checks"])}), flush=True)


run()
finish()
