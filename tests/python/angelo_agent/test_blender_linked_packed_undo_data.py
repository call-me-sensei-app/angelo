# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Undo keeps linked objects with their own packed object data whole: an armature with its pose, a mesh with its
shape key, a mesh with its material slots.

Companion of test_blender_linked_packed_redo.py and test_blender_linked_packed_undo_chains.py, for the packed data
that other data depends on beyond a pointer. A kit file's object uses object data packed from an asset library. The
kit's collection is linked into a scene with an instancer, removed and purged, and linked again (new datablocks, the
linked object matched by name when Undo goes back past the purge); then three Undos and three Redos. Once with the
same asset, once with the asset changed on disk in between (other bone, vertex and material counts, same names),
and once with the libraries removed before the second link (new Libraries for the same files).

Undo keeps the linked object it matches by name as it is, so after every Undo and Redo the object must still use the
very data it was linked with (the content recorded after the second link, never a datablock merely of the same name
or content), and that data must be whole: every pose channel's bone is a bone of the object's armature and the pose
evaluates; the shape key belongs to the object's mesh and has one point per vertex; the object's material slots
match its mesh's. The dependency graph is evaluated and a copy of the file written after every step, and no datablock
may be listed twice under one name and library. Three cases read every Undo in full instead (the developer
preference "Undo Legacy", and Angelo's asset import steps), where the step reads the same packed data again under
the same session UID.

Each case runs in its own Blender under ``--log "*undo*"`` (packed_undo_children.py), and must show in that log
the keep paths it exists for (moved into its own archive, the step's same-content copy dropped, another archive
for a taken name, a parent library moved over, remapped to the copy read again) and none it must not take. The
add-on is disabled first. Run with ``--background --factory-startup --python-exit-code 1``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from collections import Counter
from pathlib import Path

import addon_utils
import bpy

import packed_undo_children

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-linked-packed-undo-data-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "linked-packed-undo-data.json"
report = {"success": False, "blender": bpy.app.version_string, "cases": {}, "running": None}
TYPES = ("armatures", "meshes", "shape_keys", "materials", "objects", "collections")
KINDS = ("armature", "shape_key", "materials")


def save_report():
    REPORT.write_text(json.dumps(report, indent=2))


def in_window(operator, **arguments):
    window = bpy.context.window_manager.windows[0]   # Undo's steps run in a window's screen, as a person's do.
    with bpy.context.temp_override(window=window, screen=window.screen):
        return operator(**arguments)


def make_data(kind, size):
    """The asset library's one datablock of this kind; `size` sets its content (bones, vertices, materials)."""
    if kind == "armature":
        armature = bpy.data.armatures.new("Kit Data")
        holder = bpy.data.objects.new("Holder", armature)
        bpy.context.scene.collection.objects.link(holder)
        bpy.context.view_layer.objects.active = holder
        bpy.ops.object.mode_set(mode='EDIT')
        for index in range(size):
            bone = armature.edit_bones.new("Bone %d" % index)
            bone.head, bone.tail = (index, 0, 0), (index, 0, 1)
        bpy.ops.object.mode_set(mode='OBJECT')
        return armature
    mesh = bpy.data.meshes.new("Kit Data")
    mesh.from_pydata([(index, 0, 0) for index in range(size)] + [(0, 1, 0)], [],
                     [(0, 1, size)] if size > 1 else [])
    if kind == "shape_key":
        holder = bpy.data.objects.new("Holder", mesh)
        holder.shape_key_add(name="Basis")
        holder.shape_key_add(name="Lift").data[0].co.z = 1
    else:
        for index in range(size):
            mesh.materials.append(bpy.data.materials.new("Kit Paint %d" % index))
    return mesh


def signature(kind, data):
    if kind == "armature":
        return {"bones": sorted(bone.name for bone in data.bones)}
    if kind == "shape_key":
        return {"vertices": len(data.vertices), "keys": [block.name for block in data.shape_keys.key_blocks]}
    return {"materials": [material.name if material else None for material in data.materials]}


def whole(kind, obj):
    """The object and its data agree; reading them walks every pointer that could be stale."""
    data = obj.data
    if kind == "armature":
        bones = list(data.bones)
        return (len(obj.pose.bones) == len(bones)
                and all(any(channel.bone == bone for bone in bones) for channel in obj.pose.bones)
                and all(channel.bone.name == channel.name for channel in obj.pose.bones))
    if kind == "shape_key":
        key = data.shape_keys
        return (key is not None and key.user == data and any(item == key for item in bpy.data.shape_keys)
                and all(len(block.data) == len(data.vertices) for block in key.key_blocks))
    return len(obj.material_slots) == len(data.materials)


def kit_object():
    return next((item for item in bpy.data.objects if item.name == "Own Kit" and item.library), None)


def inventory():
    found = {}
    for kind in TYPES:
        items = list(getattr(bpy.data, kind))
        found[kind] = sorted("%s|%s" % key for key, count in Counter(
            (item.name, getattr(item.library, "name", "")) for item in items).items() if count > 1)
    return found


def link(kit):
    bpy.ops.wm.link(filepath=str(kit / "Collection" / "Own Set"), directory=str(kit / "Collection") + "/",
                    filename="Own Set", instance_collections=True)


def case_name(kind, pack, change_on_disk, remove_libraries=False, full_read=None):
    return "%s, %s%s%s%s" % (kind, "packed" if pack else "linked only",
                             ", changed on disk" if change_on_disk else "",
                             ", libraries removed" if remove_libraries else "",
                             (", full read by " + full_read) if full_read else "")


def asset_import_step(folder):
    """An Angelo asset import of a small mesh: its steps (Before Asset Import, Import Asset) are read in full."""
    import _bpy
    import shutil
    source, private = folder / "import-source.blend", folder / "private" / "import-source.blend"
    private.parent.mkdir(exist_ok=True)
    shutil.copyfile(source, private)
    api = _bpy._angelo_undo
    scope = api.asset_begin(source_paths=((str(source.resolve()), str(private.resolve())),))
    with bpy.data.libraries.load(str(private), link=False) as (_source, target):
        target.meshes = ["Imported"]
    result = api.asset_finish(scope, keep=True)
    api.asset_close(scope)
    assert result["state"] == "COMMITTED", "the asset import was not committed: %s" % result


def case(kind, pack, change_on_disk, remove_libraries=False, full_read=None):
    """`full_read`: None, "legacy undo" (the developer preference) or "asset import" (Angelo's import steps)."""
    name = case_name(kind, pack, change_on_disk, remove_libraries, full_read)
    report["running"] = name
    print("ANGELO_CASE", name, flush=True)   # Marks the case in the log (e.g. under --log "*undo*").
    facts = report["cases"][name] = {"states": []}
    save_report()
    folder = OUT / name.replace(", ", "-").replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    assets, kit = folder / "asset-library.blend", folder / "kit-own.blend"
    versions = []
    for size in (2, 3) if change_on_disk else (2,):   # Both kits are made first: making one starts over.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.data.libraries.write(str(assets), {make_data(kind, size)}, fake_user=True)
        stamp = os.stat(assets).st_mtime + 60 * size   # A later edit: Blender knows a file's content by its
        os.utime(assets, (stamp, stamp))               # path and modification time (in seconds).
        bpy.ops.wm.read_factory_settings(use_empty=True)
        with bpy.data.libraries.load(str(assets), link=True, pack=pack) as (_source, target):
            setattr(target, "armatures" if kind == "armature" else "meshes", ["Kit Data"])
        data = (target.armatures if kind == "armature" else target.meshes)[0]
        obj = bpy.data.objects.new("Own Kit", data)
        collection = bpy.data.collections.new("Own Set")
        collection.use_fake_user = True
        collection.objects.link(obj)
        if kind == "armature":   # The kit's object has its pose, as a rig asset does.
            bpy.context.scene.collection.objects.link(obj)
            bpy.context.view_layer.update()
        bpy.ops.wm.save_as_mainfile(filepath=str(kit), compress=False)
        versions.append(kit.read_bytes())
    kit.write_bytes(versions[0])
    if full_read == "asset import":
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.data.libraries.write(str(folder / "import-source.blend"), {bpy.data.meshes.new("Imported")},
                                 fake_user=True)

    bpy.ops.wm.read_factory_settings()   # A window, and Undo on, as a person's session has.
    bpy.context.preferences.edit.use_global_undo = True
    if full_read == "legacy undo":   # Every Undo reads the step in full (it counts with the developer extras on).
        bpy.context.preferences.view.show_developer_ui = True
        bpy.context.preferences.experimental.use_undo_legacy = True
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "scene.blend"), compress=False)
    in_window(bpy.ops.ed.undo_push, message="start")
    link(kit)
    in_window(bpy.ops.ed.undo_push, message="linked")
    bpy.data.objects.remove(next(item for item in bpy.data.objects if item.instance_collection))
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    assert kit_object() is None, "the purge left the kit's object"
    if remove_libraries:   # The next link makes new Libraries for the same files.
        for name in [library.name for library in bpy.data.libraries]:   # A parent takes its archives along.
            if library := bpy.data.libraries.get(name):
                bpy.data.libraries.remove(library)
        assert not bpy.data.libraries, "libraries left: %s" % [library.name for library in bpy.data.libraries]
    in_window(bpy.ops.ed.undo_push, message="purged")
    kit.write_bytes(versions[-1])   # The kit as it is on disk now: the same, or with the asset changed.
    link(kit)
    in_window(bpy.ops.ed.undo_push, message="relinked")
    if full_read == "asset import":
        asset_import_step(folder)
    relinked = signature(kind, kit_object().data)
    facts["relinked"] = relinked

    def look(label):
        bpy.context.view_layer.update()
        bpy.context.evaluated_depsgraph_get()
        bpy.ops.wm.save_as_mainfile(filepath=str(folder / ("%s.blend" % label)), copy=True, compress=False)
        obj = kit_object()
        state = {"object": obj is not None, "inventory": inventory()}
        if obj is not None:
            state.update(data_listed=any(item == obj.data for item in getattr(bpy.data, (
                "armatures" if kind == "armature" else "meshes"))),
                packed=bool(obj.data.library and obj.data.is_linked_packed), whole=whole(kind, obj),
                content=signature(kind, obj.data))
        facts["states"].append([label, state])
        save_report()
        twice = {key: value for key, value in state["inventory"].items() if value}
        assert not twice, "%s: listed twice under one name and library: %s" % (label, twice)
        if obj is not None:
            assert state["data_listed"] and state["whole"], "%s: the object's data is not whole: %s" % (
                label, json.dumps(state))
            assert state["packed"] == pack, "%s: packed is %s" % (label, state["packed"])
            assert state["content"] == relinked, "%s: the object uses other content than it was linked with: %s" % (
                label, json.dumps(state["content"]))

    look("relinked")
    turns = 5 if full_read == "asset import" else 3   # Back over the import's two steps too.
    for turn in range(turns):
        in_window(bpy.ops.ed.undo)
        look("undo-%d" % turn)
    for turn in range(turns):
        in_window(bpy.ops.ed.redo)
        look("redo-%d" % turn)
    facts["passed"] = True


# Each case, and the keep paths its Undo log must show and must not show (packed_undo_children.PATHS).
CASES = [((kind, False, True), (), ("kept",)) for kind in KINDS]   # The controls: nothing packed to keep.
for kind in KINDS:
    CASES += [((kind, True, False), ("kept", "dropped copy"), ("new archive",)),
              ((kind, True, True), ("kept", "name taken", "new archive"), ("dropped copy",)),
              ((kind, True, False, False, "legacy undo"), ("archive read again", "remapped"), ())]
CASES += [(("materials", True, False, True), ("kept", "parent moved"), ()),
          (("materials", True, False, False, "asset import"), ("remapped",), ()),
          (("materials", True, False, True, "legacy undo"), ("kept",), ())]


def run():
    addon_utils.disable("angelo_agent", default_set=False)   # Blender only, as the person's own link would be.
    index = packed_undo_children.child_case()
    if index is not None:   # A child: one case, its own report.
        case(*CASES[index][0])
        report["success"] = all(facts.get("passed") for facts in report["cases"].values())
        return
    names = [case_name(*spec) for spec, _required, _forbidden in CASES]
    packed_undo_children.run_children(os.path.abspath(__file__), names, OUT, report, save_report, REPORT.name,
                                      {name: spec[1:] for name, spec in zip(names, CASES)})
    report["running"] = None
    report["success"] = all(facts.get("passed") for facts in report["cases"].values()) and len(
        report["cases"]) == len(CASES)


try:
    run()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    save_report()
    print("ANGELO_LINKED_PACKED_UNDO_DATA", json.dumps({"success": report["success"], "running": report["running"],
                                                       "error": (report.get("error") or "")[-600:]}), flush=True)
    if not report["success"]:
        sys.exit(1)
