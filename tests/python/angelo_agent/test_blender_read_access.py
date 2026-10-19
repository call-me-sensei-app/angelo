# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native read-purity and feature-parity fixture; full-feature factory-startup only.

Also the compiled half of read-only break #6: delegating Python overrides installed on
``bpy.types.Curve.__getattribute__`` and ``bpy.types.EnumProperty.__init__`` are reached by
ordinary Python access (the controls) and never by an automatic ``angelo_read_data`` read.

Uses the real executor, real RNA, cached image buffers and system IDProperties.
The DEBUG ONLY storage API is used solely to plant malformed fixture storage
and compare it; production reads never call it. Explicit ordinary getters used
to warm image caches and the deliberately reviewed script are outside read-only
snapshots. No provider, account, network or user document is accessed.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
from _bpy import _angelo_read as native

assert bpy.app.background and "--factory-startup" in sys.argv, "Use an isolated factory-startup process"
HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-read-access"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_DISABLE_KEYCHAIN="1",
                  ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_STATE_DIR=str(OUT / "state"))
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from read_tools_fixtures import png  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "user_projects_opened": 0,
          "binary_path": bpy.app.binary_path, "cycles": bool(bpy.app.build_options.cycles)}
window = bpy.context.window_manager.windows[0]
scene = window.scene
executor = BlenderExecutor(window)
calls = {"get": 0, "transform": 0, "enum": 0}
updates = [0]
registered = []
scene_properties = []


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if not condition:
        raise AssertionError(name + (": " + json.dumps(detail, default=str)[:1800] if detail is not None else ""))


def plain(value):
    """Copy raw fixture storage without resolving registered RNA properties."""
    if isinstance(value, bpy.types.ID):
        return {"id": value.session_uid, "type": value.id_type}
    if hasattr(value, "items"):
        return {key: plain(item) for key, item in value.items()}
    if hasattr(value, "to_list"):
        return [plain(item) for item in value.to_list()]
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def raw_storage():
    group = scene.bl_system_properties_get(do_create=False)
    return None if group is None else plain(group)


def snapshot():
    return {"storage": raw_storage(), "dirty": bpy.data.is_dirty, "updates": updates[0],
            "calls": dict(calls), "frame": scene.frame_current,
            "ids": {name: [(item.session_uid, item.users) for item in getattr(bpy.data, name)]
                    for name in ("objects", "meshes", "materials", "images", "node_groups")}}


def on_update(_scene, _depsgraph):
    updates[0] += 1


def read(path, **arguments):
    before = snapshot()
    result = executor.execute("angelo_read_data", validate_call("angelo_read_data", dict(path=path, **arguments)))
    check("pure_" + path, before == snapshot(), result)
    return result


def row(path):
    result = read(path)
    check("found_" + path, result.get("found") is True and "property" in result, result)
    return result["property"]


def stable(result):
    return json.dumps({key: value for key, value in result.items() if key != "native_exec_ms"},
                      sort_keys=True, separators=(",", ":"))


def property_named(rows, name):
    return next(item for item in rows if item["name"] == name)


def groups():
    mesh = bpy.data.meshes.new("Read Weights")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("Read Weights", mesh)
    scene.collection.objects.link(obj)
    prefix = 'data.meshes["Read Weights"].vertices'
    check("fresh_mesh_has_no_deform_storage", native.vertex_groups(mesh, 0, 0, 0)["storage_present"] is False)
    empty = read(prefix + "[0].groups")
    check("empty_groups_shape", empty["length"] == 0 and empty["items"] == [], empty)
    check("empty_read_did_not_allocate_deforms", native.vertex_groups(mesh, 0, 0, 0)["storage_present"] is False)
    for index, weight in enumerate((.25, .5, .75)):
        obj.vertex_groups.new(name="Group %d" % index).add([1], weight, "REPLACE")
    expected = [(item.group, item.weight) for item in mesh.vertices[1].groups]
    before = native.vertex_groups(mesh, 1, 0, 64)
    page = read(prefix + "[1].groups", depth=2)
    actual = [(property_named(item["properties"], "group")["value"],
               property_named(item["properties"], "weight")["value"]) for item in page["items"]]
    check("populated_groups_match_rna", actual == expected and page["length"] == len(expected), page)
    check("populated_storage_unchanged", native.vertex_groups(mesh, 1, 0, 64) == before)
    small = read(prefix + "[1].groups", offset=1, limit=1, depth=2)
    check("groups_page_and_negative_index", len(small["items"]) == 1 and small["next_offset"] == 2
          and row(prefix + "[1].groups[-1].weight")["value"] == .75, small)
    check("ordinary_answer_byte_stability", stable(page) == stable(read(prefix + "[1].groups", depth=2)))
    obj.vertex_groups[2].add([1], .125, "REPLACE")
    check("second_read_reflects_weight_edit", row(prefix + "[1].groups[-1].weight")["value"] == .125)
    duplicate = mesh.copy()
    duplicate.name = "Read Weights Copy"
    prior = native.vertex_groups(mesh, 1, 0, 64)
    read('data.meshes["Read Weights Copy"].vertices[1].groups', depth=2)
    check("copied_mesh_reads_preserve_both_weights", native.vertex_groups(mesh, 1, 0, 64) == prior
          and native.vertex_groups(duplicate, 1, 0, 64)["items"] == prior["items"])
    return obj


def image_fields(image):
    """Warm only this image outside read snapshots, then compare each ordinary RNA value."""
    expected = {name: getattr(image, name) for name in ("size", "channels", "depth", "is_float", "resolution", "file_format")}
    if "frame_duration" in native.image_metadata(image):
        expected["frame_duration"] = image.frame_duration
    expected["size"] = list(expected["size"])
    # The public read encoding rounds numeric components, including RNA's float32 resolution.
    expected["resolution"] = [round(float(component), 6) for component in expected["resolution"]]
    for name, value in expected.items():
        answer = row('data.images[%s].%s' % (json.dumps(image.name), name))
        check("image_field_" + image.name + "_" + name, answer.get("value") == value
              and "computed_getter" not in answer and "value_source" not in answer, answer)


def images():
    png(OUT / "tile.1001.png", 8, 4)
    png(OUT / "tile.1002.png", 3, 6)
    image = bpy.data.images.new("Read UDIM", 8, 4, tiled=True)
    image.filepath = str(OUT / "tile.<UDIM>.png")
    image.tiles.new(tile_number=1002)
    image.tiles.active_index = 1
    first = tuple(image.tiles[0].size)  # Explicit fixture warm-up of tile 1001 only.
    check("udim_fixture_first_cached", first == (8, 4) and native.image_tile_metadata(image, 1001)["size"] == [8, 4])
    check("udim_fixture_second_uncached", native.image_tile_metadata(image, 1002) is None)
    prefix = 'data.images["Read UDIM"]'
    check("udim_collection_traversable", read(prefix + ".tiles", depth=2)["length"] == 2)
    check("exact_cached_tile_read", row(prefix + ".tiles[0].size")["value"] == [8, 4])
    check("uncached_tile_withheld", row(prefix + ".tiles[1].size").get("withheld") == "LOADS_FILE")
    check("active_uncached_tile_withheld", row(prefix + ".tiles.active.channels").get("withheld") == "LOADS_FILE")
    check("image_default_uses_first_not_active_tile", row(prefix + ".size")["value"] == [8, 4])
    check("udim_read_never_loaded_second", native.image_tile_metadata(image, 1002) is None)
    check("invalid_tile_and_view_do_not_alias", native.image_tile_metadata(image, 1003) is None
          and native.image_tile_metadata(image, 1001, 0, 1) is None
          and native.image_tile_metadata(image, 1001, 0, -1) is None)
    second = tuple(image.tiles[1].size)  # Approved fixture setup now loads tile 1002.
    check("udim_fixture_second_now_cached", second == (3, 6), second)
    check("tile_second_read_reflects_cache_load", row(prefix + ".tiles[1].size")["value"] == [3, 6])
    check("first_tile_still_distinct", row(prefix + ".tiles[0].size")["value"] == [8, 4])
    image_fields(image)

    png(OUT / "frame0000.png", 6, 3)
    png(OUT / "frame0001.png", 9, 2)
    sequence = bpy.data.images.load(str(OUT / "frame0001.png"), check_existing=False)
    sequence.name, sequence.source = "Read Sequence", "SEQUENCE"
    sequence.buffers_free()
    warm = tuple(sequence.tiles[0].size)  # ImageTile's default ImageUser has frame zero.
    check("sequence_fixture_frame_zero_cached", warm == (6, 3), warm)
    check("exact_sequence_frame_zero", native.image_tile_metadata(sequence, 1001, 0, 0)["size"] == [6, 3])
    check("other_frame_does_not_alias", native.image_tile_metadata(sequence, 1001, 1, 0) is None)
    check("uncached_query_preserves_lastframe", native.image_metadata(sequence)["size"] == [6, 3])
    check("sequence_default_image_read", row('data.images["Read Sequence"].size')["value"] == [6, 3])
    check("sequence_tile_default_read", row('data.images["Read Sequence"].tiles[0].size')["value"] == [6, 3])
    check("sequence_read_did_not_load_frame_one", native.image_tile_metadata(sequence, 1001, 1, 0) is None)
    image_fields(sequence)


def stored_absent(name):
    """No system storage under the scene's name ``name``, asked without creating the group."""
    root = scene.bl_system_properties_get(do_create=False)
    return root is None or name not in root


def cycles():
    check("full_build_has_real_cycles", report["cycles"] and "cycles" in scene.bl_rna.properties)
    root = scene.bl_system_properties_get(do_create=False)
    if root is not None and "cycles" in root:
        del root["cycles"]
    before = raw_storage()
    check("cycles_starts_absent", stored_absent("cycles"))
    default = scene.bl_rna.properties["cycles"].fixed_type.properties["samples"].default
    check("cycles_defaults_readable", row("scene.cycles.samples")["value"] == default)
    check("cycles_group_readable_without_creation", read("scene.cycles")["struct"] == "CyclesRenderSettings")
    check("cycles_absence_preserved", raw_storage() == before
          and stored_absent("cycles"))
    root = scene.bl_system_properties_get(do_create=True)
    root["cycles"] = 13
    refused = read("scene.cycles.samples")
    check("malformed_cycles_group_not_repaired", refused.get("code") == "INVALID_STORAGE" and root["cycles"] == 13, refused)
    root["cycles"] = {"samples": "malformed"}
    invalid = row("scene.cycles.samples")
    check("malformed_cycles_scalar_not_repaired", invalid.get("withheld") == "INVALID_STORAGE"
          and root["cycles"]["samples"] == "malformed", invalid)
    del root["cycles"]


def counted_get(_owner):
    calls["get"] += 1
    return 91.5


def counted_transform(_owner, value, _is_set):
    calls["transform"] += 1
    return value * 10.0


def counted_enum(_owner, _context):
    calls["enum"] += 1
    return [("SEVEN", "Seven", "", 7), ("NINE", "Nine", "", 9)]


class ReadAccessEntry(bpy.types.PropertyGroup):
    amount: bpy.props.IntProperty(default=5)


class ReadAccessGroup(bpy.types.PropertyGroup):
    target: bpy.props.PointerProperty(type=bpy.types.Object)
    entries: bpy.props.CollectionProperty(type=ReadAccessEntry)
    amount: bpy.props.IntProperty(default=11)


def callbacks_and_pointers(obj):
    for cls in (ReadAccessEntry, ReadAccessGroup):
        bpy.utils.register_class(cls)
        registered.append(cls)
    definitions = {
        "read_computed": bpy.props.FloatProperty(default=3.25, get=counted_get),
        "read_transformed": bpy.props.FloatProperty(default=2.0, get_transform=counted_transform),
        "read_dynamic_enum": bpy.props.EnumProperty(items=counted_enum, default=7),
        "read_probe": bpy.props.PointerProperty(type=ReadAccessGroup),
    }
    for name, definition in definitions.items():
        setattr(bpy.types.Scene, name, definition)
        scene_properties.append(name)
    before_calls = dict(calls)
    for name, expected in (("read_computed", 3.25), ("read_transformed", 2.0)):
        answer = row("scene." + name)
        check(name + "_default_annotated", (answer.get("value"), answer.get("value_source"), answer.get("computed_getter"))
              == (expected, "default", "not_run"), answer)
    answer = row("scene.read_dynamic_enum")
    check("dynamic_enum_default_not_computed", answer.get("withheld") == "COMPUTED_VALUE"
          and answer.get("computed_getter") == "not_run" and answer.get("value_source") == "default", answer)
    root = scene.bl_system_properties_get(do_create=True)
    root["read_computed"], root["read_transformed"], root["read_dynamic_enum"] = 1.5, 4.0, 9
    for name, expected in (("read_computed", 1.5), ("read_transformed", 4.0)):
        answer = row("scene." + name)
        check(name + "_storage_annotated", (answer.get("value"), answer.get("value_source"), answer.get("computed_getter"))
              == (expected, "stored", "not_run"), answer)
    answer = row("scene.read_dynamic_enum")
    check("dynamic_enum_storage_not_computed", answer.get("withheld") == "COMPUTED_VALUE"
          and answer.get("value_source") == "stored", answer)
    root["read_computed"] = 2.75
    check("computed_second_read_reflects_storage_edit", row("scene.read_computed")["value"] == 2.75)
    check("nested_absent_id_pointer_stays_none", row("scene.read_probe.target")["value"] is None)
    check("absent_collection_is_empty", read("scene.read_probe.entries")["length"] == 0)
    check("nested_default_does_not_materialize_group", stored_absent("read_probe"))
    probe = scene.read_probe  # Explicit fixture construction, outside observation snapshots.
    probe.target = obj
    for name, amount in (("First", 17), ("Second", 29)):
        entry = probe.entries.add()
        entry.name, entry.amount = name, amount
    check("nested_id_pointer_reference_preserved", property_named(read("scene.read_probe")["properties"], "target")["value"]["read"]
          == 'data.objects["Read Weights"]')
    check("nested_id_pointer_traversable", row("scene.read_probe.target.name")["value"] == obj.name)
    page = read("scene.read_probe.entries", offset=1, limit=1, depth=2)
    check("stored_collection_page_preserved", page["length"] == 2 and page["items"][0]["name"] == "Second", page)
    check("stored_collection_name_lookup", row('scene.read_probe.entries["Second"].amount')["value"] == 29)
    check("all_computed_callbacks_still_unrun", calls == before_calls, calls)
    # Exercise the post-approval executor route explicitly; this fixture owns that approval.
    result = executor.execute("angelo_execute_python", {
        "description": "Read the fixture's explicitly reviewed computed property",
        "code": "result = {'computed': scene.read_computed, 'transformed': scene.read_transformed, 'enum': scene.read_dynamic_enum}",
    })
    check("reviewed_script_keeps_computed_access", result.get("success") is True
          and result["result"] == {"computed": 91.5, "transformed": 40.0, "enum": "NINE"}, result)
    check("only_explicit_script_called_getters", calls["get"] == before_calls["get"] + 1
          and calls["transform"] == before_calls["transform"] + 1 and calls["enum"] > before_calls["enum"], calls)


def python_overrides():
    """Read-only break #6: Python overrides on document classes never run during automatic reads.

    A delegating ``Curve.__getattribute__`` and ``EnumProperty.__init__`` count their calls.
    Ordinary Python access is the positive control; every automatic read must add zero.
    """
    curve = bpy.data.curves.new("Read Override Curve", "CURVE")
    curve.dimensions = "3D"  # A new curve is 2D; the read must report the stored, non-default value.
    curve.splines.new("BEZIER")
    hits = {"Curve.__getattribute__": 0, "EnumProperty.__init__": 0}
    bpy_struct = bpy.types.bpy_struct

    def counting_getattribute(self, name):
        hits["Curve.__getattribute__"] += 1
        return bpy_struct.__getattribute__(self, name)

    own_init = bpy.types.EnumProperty.__dict__.get("__init__")
    original_init = bpy.types.EnumProperty.__init__

    def counting_init(self, *arguments, **keywords):
        hits["EnumProperty.__init__"] += 1
        original_init(self, *arguments, **keywords)

    bpy.types.Curve.__getattribute__ = counting_getattribute
    bpy.types.EnumProperty.__init__ = counting_init
    try:
        _ = curve.dimensions  # Positive controls: ordinary Python reaches both overrides.
        _ = [prop.enum_items for prop in curve.bl_rna.properties if prop.identifier == "dimensions"]
        controls = dict(hits)
        check("override_controls_reached", controls["Curve.__getattribute__"] >= 1
              and controls["EnumProperty.__init__"] >= 1, controls)
        prefix = 'data.curves["Read Override Curve"]'
        answers = [read(prefix), read(prefix + ".dimensions"), read(prefix + ".splines", depth=2),
                   read(prefix + ".splines[0].bezier_points[0].co"), read(prefix, depth=2, offset=10)]
        check("override_reads_answered", answers[1].get("property", {}).get("value") == "3D"
              and all(answer.get("found") for answer in answers), answers)
        check("override_never_ran_during_automatic_reads", hits == controls, {"controls": controls, "after": hits})
        report["observed"] = dict(report.get("observed", {}), override_hits={"controls": controls, "after": dict(hits)})
    finally:
        del bpy.types.Curve.__getattribute__
        if own_init is None:
            del bpy.types.EnumProperty.__init__
        else:
            bpy.types.EnumProperty.__init__ = own_init
        bpy.data.curves.remove(curve)
    check("override_removed", "__getattribute__" not in bpy.types.Curve.__dict__)


def main():
    for name in ("vertex_groups", "image_tile_metadata", "image_metadata", "property_info"):
        check("native_helper_" + name, callable(getattr(native, name, None)))
    # Retired in 1.0: nothing called it; automatic reads use the address-read ABI's stored members.
    check("native_helper_stored_property_retired", not hasattr(native, "stored_property"))
    check("address_read_abi_active", getattr(native, "ADDRESS_READ_VERSION", None) == 2)
    bpy.app.handlers.depsgraph_update_post.append(on_update)
    obj = groups()
    images()
    cycles()
    callbacks_and_pointers(obj)
    python_overrides()
    report["success"] = True


try:
    main()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    if on_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(on_update)
    for name in reversed(scene_properties):
        delattr(bpy.types.Scene, name)
    for cls in reversed(registered):
        bpy.utils.unregister_class(cls)
    (OUT / "read-access.json").write_text(json.dumps(report, indent=2))
print("ANGELO_READ_ACCESS", json.dumps({"success": report.get("success"), "checks": len(report["checks"])}), flush=True)
if not report.get("success"):
    raise RuntimeError(report["error"])
