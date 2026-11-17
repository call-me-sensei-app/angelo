# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Undo and Redo keep valid every packed linked datablock that linked data Undo keeps still uses, whatever the path.

Companion of test_blender_linked_packed_redo.py (one chain: scene, instancer, linked collection, object, material,
one packed datablock). Undo keeps some data as it is (data it never frees, and the linked data of libraries the
loaded step names, matched by session UID or by name), while it frees packed data the loaded step does not store.
These cases reach packed data the other ways:

- relinked: link, remove and purge, link again (new datablocks, the linked ones matched by name), two Undos and two
  Redos; once with the same asset (the step's packed datablock has the same content) and once with the asset changed
  on disk in between (same name, other content), and a linked-only control first;
- nested: a packed node group using another packed node group and a packed image, and an image linked into the
  asset library from a third file (packed into an archive library of its own);
- references: a packed image used only through an object's ID property and a driver variable;
- shared archive: two packed node groups of one asset library, linked one after the other, so the step after the
  first stores their archive library but not the second node group.

After every Undo and Redo: the dependency graph is evaluated, a copy of the file is written (which reads every
pointer), the packed datablocks the linked data reaches must be listed by identity, no datablock is listed twice under
one name and library, and after the last Redo the packed counts by type and the libraries are as after the last link.
Two cases read every Undo in full (the developer preference "Undo Legacy"). Each case runs in its own Blender under
``--log "*undo*"`` (packed_undo_children.py) and must show the keep paths it exists for, and none it must not take.
The add-on is disabled first. Run with ``--background --factory-startup --python-exit-code 1``.
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

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-linked-packed-undo-chains-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "linked-packed-undo-chains.json"
report = {"success": False, "blender": bpy.app.version_string, "cases": {}, "running": None}
TYPES = ("node_groups", "images", "meshes", "materials", "objects", "collections")


def save_report():
    REPORT.write_text(json.dumps(report, indent=2))


def in_window(operator, **arguments):
    window = bpy.context.window_manager.windows[0]   # Undo's steps run in a window's screen, as a person's do.
    with bpy.context.temp_override(window=window, screen=window.screen):
        return operator(**arguments)


def listed(data):
    return data is not None and any(item == data for item in getattr(bpy.data, data_kind(data)))


def data_kind(data):
    return {"ShaderNodeTree": "node_groups", "Image": "images", "Object": "objects", "Mesh": "meshes",
            "Material": "materials", "ParticleSettings": "particles"}[type(data).__name__]


def inventory():
    found = {}
    for kind in TYPES:
        items = list(getattr(bpy.data, kind))
        found[kind] = {"packed": sum(1 for item in items if item.library and item.is_linked_packed),
                       "twice": sorted("%s|%s" % key for key, count in Counter(
                           (item.name, getattr(item.library, "name", "")) for item in items).items() if count > 1)}
    found["libraries"] = sorted(library.name for library in bpy.data.libraries)
    return found


def quad(name):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    return mesh


def kit_object(name, material=None):
    mesh = quad(name + " Mesh")
    if material:
        mesh.materials.append(material)
    return bpy.data.objects.new(name, mesh)


def material_with_group(name, group):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.new("ShaderNodeGroup").node_tree = group
    return material


def save_kit(folder, collections):
    for name, objects in collections.items():
        collection = bpy.data.collections.new(name)
        collection.use_fake_user = True
        for obj in objects:
            collection.objects.link(obj)
    kit = folder / "kit-own.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(kit), compress=False)
    return kit


def write_asset_groups(path, names, label=""):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    groups = set()
    for name in names:
        group = bpy.data.node_groups.new(name, "ShaderNodeTree")
        if label:
            group.nodes.new("NodeFrame").label = label   # Other content, same name.
        groups.add(group)
    bpy.data.libraries.write(str(path), groups, fake_user=True)
    if label:   # A later edit: Blender knows a file's content by its path and modification time (in seconds).
        stamp = os.stat(path).st_mtime + 60
        os.utime(path, (stamp, stamp))


def start_session(folder, full_read=None):
    bpy.ops.wm.read_factory_settings()   # A window, and Undo on, as a person's session has.
    bpy.context.preferences.edit.use_global_undo = True
    if full_read == "legacy undo":   # Every Undo reads the step in full (it counts with the developer extras on).
        bpy.context.preferences.view.show_developer_ui = True
        bpy.context.preferences.experimental.use_undo_legacy = True
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "scene.blend"), compress=False)
    in_window(bpy.ops.ed.undo_push, message="start")


def link(kit, name):
    bpy.ops.wm.link(filepath=str(kit / "Collection" / name), directory=str(kit / "Collection") + "/",
                    filename=name, instance_collections=True)


def linked(kind, name):
    return next((item for item in getattr(bpy.data, kind) if item.name == name and item.library), None)


def group_of(material_name):
    material = linked("materials", material_name)
    node = material and next((n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeGroup"), None)
    return node.node_tree if node else None


def look(folder, facts, label, reach):
    """One state: evaluated, written, and checked."""
    bpy.context.view_layer.update()
    bpy.context.evaluated_depsgraph_get()
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / ("%s.blend" % label)), copy=True, compress=False)
    state = {"inventory": inventory(), "reach": reach()}
    facts["states"].append([label, state])
    save_report()
    twice = {kind: found["twice"] for kind, found in state["inventory"].items()
             if kind != "libraries" and found["twice"]}
    assert not twice, "%s: listed twice under one name and library: %s" % (label, twice)
    bad = [what for what, ok in state["reach"].items() if ok is False]
    assert not bad, "%s: what the linked data uses is not listed: %s" % (label, bad)
    return state


def begin(name):
    report["running"] = name
    print("ANGELO_CASE", name, flush=True)   # Marks the case in the log (e.g. under --log "*undo*").
    facts = report["cases"][name] = {"states": []}
    save_report()
    folder = OUT / name.replace(", ", "-").replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    return folder, facts


def rounds(folder, facts, reach, undos):
    """Undo `undos` times, then Redo as many times; the end must be the last link's state again."""
    last = look(folder, facts, "linked", reach)
    for turn in range(undos):
        in_window(bpy.ops.ed.undo)
        look(folder, facts, "undo-%d" % turn, reach)
    for turn in range(undos):
        in_window(bpy.ops.ed.redo)
        end = look(folder, facts, "redo-%d" % turn, reach)
    assert end["inventory"] == last["inventory"], "after Redo the data is not as after the link: %s" % (
        json.dumps(end["inventory"]))
    facts["passed"] = True


def relinked(name, pack, change_on_disk, full_read=None):
    folder, facts = begin(name)
    assets = folder / "asset-library.blend"
    kit = folder / "kit-own.blend"
    versions = []
    for label in ("", "changed") if change_on_disk else ("",):   # Both kits are made first: making one starts over.
        write_asset_groups(assets, ["Kit Asset"], label=label)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        with bpy.data.libraries.load(str(assets), link=True, pack=pack) as (_source, target):
            target.node_groups = ["Kit Asset"]
        save_kit(folder, {"Own Set": [kit_object("Own Kit", material_with_group("Own Paint", target.node_groups[0]))]})
        versions.append(kit.read_bytes())
    kit.write_bytes(versions[0])
    start_session(folder, full_read)
    link(kit, "Own Set")
    in_window(bpy.ops.ed.undo_push, message="linked")
    bpy.data.objects.remove(next(obj for obj in bpy.data.objects if obj.instance_collection))
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    assert group_of("Own Paint") is None, "the purge left the kit's material"
    in_window(bpy.ops.ed.undo_push, message="purged")
    kit.write_bytes(versions[-1])   # The kit as it is on disk now: the same, or with the asset changed.
    link(kit, "Own Set")
    in_window(bpy.ops.ed.undo_push, message="relinked")

    def reach():
        group = group_of("Own Paint")
        return {"material's node group": listed(group) if linked("materials", "Own Paint") else None,
                "packed as linked": (group.is_linked_packed == pack) if group else None}
    rounds(folder, facts, reach, 3)


def nested(name, full_read=None):
    folder, facts = begin(name)
    far = folder / "far-images.blend"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    image = bpy.data.images.new("Far Image", 4, 4)
    image.pack()
    bpy.data.libraries.write(str(far), {image}, fake_user=True)

    assets = folder / "asset-library.blend"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(far), link=True) as (_source, target):
        target.images = ["Far Image"]
    own_image = bpy.data.images.new("Kit Image", 4, 4)
    own_image.pack()
    inner = bpy.data.node_groups.new("Kit Inner", "ShaderNodeTree")
    inner.nodes.new("ShaderNodeTexImage").image = target.images[0]
    inner.nodes.new("ShaderNodeTexImage").image = own_image
    outer = bpy.data.node_groups.new("Kit Asset", "ShaderNodeTree")
    outer.nodes.new("ShaderNodeGroup").node_tree = inner
    outer.use_fake_user = True
    bpy.ops.wm.save_as_mainfile(filepath=str(assets), compress=False)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(assets), link=True, pack=True) as (_source, target):
        target.node_groups = ["Kit Asset"]
    kit = save_kit(folder, {"Own Set": [kit_object("Own Kit",
                                                   material_with_group("Own Paint", target.node_groups[0]))]})
    start_session(folder, full_read)
    link(kit, "Own Set")
    in_window(bpy.ops.ed.undo_push, message="linked")

    def reach():
        outer = group_of("Own Paint")
        inner = outer and next((n.node_tree for n in outer.nodes if n.bl_idname == "ShaderNodeGroup"), None)
        images = [n.image for n in inner.nodes if n.bl_idname == "ShaderNodeTexImage"] if inner else []
        return {"outer group": listed(outer), "inner group": listed(inner),
                "images": len(images) == 2 and all(listed(image) for image in images),
                "all packed": bool(outer and inner and all(x.is_linked_packed for x in [outer, inner] + images))}
    rounds(folder, facts, reach, 1)


def references(name):
    folder, facts = begin(name)
    assets = folder / "asset-library.blend"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    image = bpy.data.images.new("Kit Image", 4, 4)
    image.pack()
    bpy.data.libraries.write(str(assets), {image}, fake_user=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(assets), link=True, pack=True) as (_source, target):
        target.images = ["Kit Image"]
    obj = kit_object("Own Kit")
    obj["kit_ref"] = target.images[0]
    driver = obj.driver_add("location", 0).driver
    variable = driver.variables.new()
    variable.targets[0].id_type = 'IMAGE'
    variable.targets[0].id = target.images[0]
    variable.targets[0].data_path = "size[0]"
    driver.expression = variable.name
    kit = save_kit(folder, {"Own Set": [obj]})
    start_session(folder)
    link(kit, "Own Set")
    in_window(bpy.ops.ed.undo_push, message="linked")

    def reach():
        obj = linked("objects", "Own Kit")
        if obj is None:
            return {"object": None}
        prop = obj.get("kit_ref")
        target = obj.animation_data.drivers[0].driver.variables[0].targets[0].id
        return {"ID property": listed(prop), "driver variable": listed(target),
                "one image": prop is not None and prop == target}
    rounds(folder, facts, reach, 1)


def shared_archive(name):
    folder, facts = begin(name)
    assets = folder / "asset-library.blend"
    write_asset_groups(assets, ["Kit First", "Kit Second"])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(assets), link=True, pack=True) as (_source, target):
        target.node_groups = ["Kit First", "Kit Second"]
    first, second = target.node_groups
    kit = save_kit(folder, {"Set A": [kit_object("Kit A", material_with_group("Paint A", first))],
                            "Set B": [kit_object("Kit B", material_with_group("Paint B", second))]})
    start_session(folder)
    link(kit, "Set A")
    in_window(bpy.ops.ed.undo_push, message="first")
    link(kit, "Set B")   # The step before stores the archive library, but not this node group.
    in_window(bpy.ops.ed.undo_push, message="second")

    def reach():
        return {name: listed(group_of(name)) if linked("materials", name) else None
                for name in ("Paint A", "Paint B")}
    rounds(folder, facts, reach, 2)


def packed_emitter(name):
    """The kit's collection holds a packed object with two particle systems: the step's copy of it, dropped for the
    kept one, was never linked, and freeing it followed its particle systems' settings pointers."""
    folder, facts = begin(name)
    assets = folder / "asset-library.blend"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    emitter = kit_object("Kit Emitter")
    bpy.context.scene.collection.objects.link(emitter)
    for index in range(2):
        emitter.modifiers.new("Particles %d" % index, 'PARTICLE_SYSTEM')
    bpy.data.libraries.write(str(assets), {emitter}, fake_user=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(assets), link=True, pack=True) as (_source, target):
        target.objects = ["Kit Emitter"]
    kit = save_kit(folder, {"Own Set": [target.objects[0]]})
    start_session(folder)
    relink_after_purge(kit)

    def reach():
        emitter = linked("objects", "Kit Emitter")
        if emitter is None:
            return {"emitter": None}
        systems = list(emitter.particle_systems)
        return {"emitter": listed(emitter), "packed": emitter.is_linked_packed,
                "particle systems": len(systems) == 2 and all(listed(system.settings) for system in systems)}
    rounds(folder, facts, reach, 3)


def relink_after_purge(kit, remove=()):
    """Link the kit, purge it (and `remove` first), link it again: each with its step."""
    link(kit, "Own Set")
    in_window(bpy.ops.ed.undo_push, message="linked")
    bpy.data.objects.remove(next(obj for obj in bpy.data.objects if obj.instance_collection))
    for item in remove:
        item()
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    in_window(bpy.ops.ed.undo_push, message="purged")
    link(kit, "Own Set")
    in_window(bpy.ops.ed.undo_push, message="relinked")


def local_users(name, kind, full_read=None):
    """A local datablock uses the packed data directly (a local object its mesh, a local material its node group).
    Undo past the purge reads it anew, and the step's copy of the packed data it uses is dropped for the kept one:
    it must use the kept one, the very datablock the linked kit uses."""
    folder, facts = begin(name)
    assets = folder / "asset-library.blend"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    data = quad("Kit Data") if kind == "mesh" else bpy.data.node_groups.new("Kit Data", "ShaderNodeTree")
    bpy.data.libraries.write(str(assets), {data}, fake_user=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(assets), link=True, pack=True) as (_source, target):
        setattr(target, "meshes" if kind == "mesh" else "node_groups", ["Kit Data"])
    packed = (target.meshes if kind == "mesh" else target.node_groups)[0]
    kit_user = (bpy.data.objects.new("Own Kit", packed) if kind == "mesh" else
                kit_object("Own Kit", material_with_group("Own Paint", packed)))
    kit = save_kit(folder, {"Own Set": [kit_user]})
    start_session(folder, full_read)

    def kit_data():
        if kind == "mesh":
            obj = linked("objects", "Own Kit")
            return obj.data if obj else None
        return group_of("Own Paint")

    def local_data():
        if kind == "mesh":
            obj = bpy.data.objects.get("Own Local")
            return obj.data if obj else None
        material = bpy.data.materials.get("Own Local Paint")
        node = material and next((n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeGroup"), None)
        return node.node_tree if node else None

    def add_local_user():
        if kind == "mesh":
            bpy.context.scene.collection.objects.link(bpy.data.objects.new("Own Local", kit_data()))
        else:
            material_with_group("Own Local Paint", kit_data()).use_fake_user = True

    def remove_local_user():
        if kind == "mesh":
            bpy.data.objects.remove(bpy.data.objects["Own Local"])
        else:
            bpy.data.materials.remove(bpy.data.materials["Own Local Paint"])

    link(kit, "Own Set")
    add_local_user()
    in_window(bpy.ops.ed.undo_push, message="linked")
    bpy.data.objects.remove(next(obj for obj in bpy.data.objects if obj.instance_collection))
    remove_local_user()
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    in_window(bpy.ops.ed.undo_push, message="purged")
    link(kit, "Own Set")
    in_window(bpy.ops.ed.undo_push, message="relinked")

    def reach():
        local, kept = local_data(), kit_data()
        return {"local user's packed data": listed(local) if local is not None else None,
                "the kit's own": (local == kept) if local is not None and kept is not None else None}
    rounds(folder, facts, reach, 3)


# Each case: its name, what it runs, and the keep paths its Undo log must show and must not show
# (packed_undo_children.PATHS).
CASES = [
    ("relinked, linked only", relinked, (False, False), (), ("kept",)),   # The control: nothing packed to keep.
    ("relinked, packed", relinked, (True, False), ("kept", "dropped copy"), ("new archive",)),
    ("relinked, packed, changed on disk", relinked, (True, True), ("kept", "name taken", "new archive"),
     ("dropped copy",)),
    ("relinked, packed, full read by legacy undo", relinked, (True, False, "legacy undo"),
     ("archive read again", "remapped"), ()),
    ("nested and cross-library, packed", nested, (), ("kept",), ()),
    ("nested and cross-library, packed, full read by legacy undo", nested, ("legacy undo",), ("remapped",), ()),
    ("ID property and driver, packed", references, (), ("kept",), ()),
    ("shared archive library, packed", shared_archive, (), ("kept",), ("new archive",)),
    ("packed object with particle systems", packed_emitter, (), ("kept", "dropped copy"), ()),
    ("local object with the packed mesh", local_users, ("mesh",), ("kept", "dropped copy"), ()),
    ("local material with the packed node group", local_users, ("node_group",), ("kept", "dropped copy"), ()),
    ("local object with the packed mesh, full read by legacy undo", local_users, ("mesh", "legacy undo"),
     ("kept", "dropped copy"), ()),
    ("local material with the packed node group, full read by legacy undo", local_users,
     ("node_group", "legacy undo"), ("kept", "dropped copy"), ()),
]


def run():
    addon_utils.disable("angelo_agent", default_set=False)   # Blender only, as the person's own link would be.
    index = packed_undo_children.child_case()
    if index is not None:   # A child: one case, its own report.
        name, function, arguments = CASES[index][:3]
        function(name, *arguments)
        report["success"] = all(facts.get("passed") for facts in report["cases"].values())
        return
    packed_undo_children.run_children(os.path.abspath(__file__), [case[0] for case in CASES], OUT, report,
                                      save_report, REPORT.name, {case[0]: case[3:] for case in CASES})
    report["running"] = None
    report["success"] = all(facts.get("passed") for facts in report["cases"].values()) and len(
        report["cases"]) == len(CASES)


try:
    run()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    save_report()
    print("ANGELO_LINKED_PACKED_UNDO_CHAINS", json.dumps({"success": report["success"], "running": report["running"],
                                                         "error": (report.get("error") or "")[-600:]}), flush=True)
    if not report["success"]:
        sys.exit(1)
