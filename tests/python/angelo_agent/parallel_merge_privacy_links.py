# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Parts for ``test_blender_parallel_merge_privacy``: data a worker linked and packed in its file (Blender 5's packed
linked data) from a library outside every private folder, each holding a private path in a mesh's own property
(BREAKIT-C7A-11 H1, BREAKIT-C7A-12 M1 and M2). Every one is made local, cleaned and checked, never refused:

- ``packed``: a mesh used by a local object.
- ``override``: a collection the worker made a library override hierarchy of: the overrides become plain local data,
  since an override whose reference is local data is a corrupt file.
- ``cycle``: a collection whose two objects use each other (a parent one way, a constraint's target the other), used
  by a local empty that instances it: each copied by Blender, then every local user moved to the copy.
- ``theirs``: a collection the worker linked, not packed, from a person's library whose material uses a node group
  that library packs from another (as Blender packs its Essentials): refused with nothing merged in 2.0.0, as defense
  in depth since plain Blender 5.3 crashed on Redo after an Undo of such data (BREAKIT-C7A-13 M1, fixed for 2.0.0).
- ``mixed``: the same, and the same node group packed by the worker itself, holding a private path, on a local object:
  read from the part, so made local and cut, or refused; never saved.
- ``named``: a mesh named after a private file, in a library the worker wrote into the chat's default workspace
  (which is not private), linked by the part: its name is written by every save and cannot change, so the merge
  refuses (BREAKIT-C7A-14 H1).
"""

from pathlib import Path

import bpy

PARTS = {15: "packed", 16: "override", 17: "cycle", 18: "theirs", 19: "mixed", 20: "named"}


def _start(part):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(part), compress=False)
    return bpy.context.scene


def _library(folder, name, datablocks):
    library = Path(folder) / name
    library.parent.mkdir(parents=True, exist_ok=True)
    bpy.data.libraries.write(str(library), set(datablocks), fake_user=True)
    return library


def _link(part, library, kind, name, secret):
    scene = _start(part)
    with bpy.data.libraries.load(str(library), link=True, pack=True) as (_source, target):
        setattr(target, kind, [name])
    linked = getattr(target, kind)[0]
    for mesh in bpy.data.meshes:
        mesh["note"] = str(secret)
    return scene, linked


def write(parts, quad, secret, library_folder):
    """Each part of ``parts`` (``{number: part path}`` for ``PARTS``) written; whether each packs its linked data."""
    by_name = {name: parts[number] for number, name in PARTS.items()}
    packed = {}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    library = _library(library_folder, "kit-library.blend", [quad("Linked Kit")])
    scene, linked = _link(by_name["packed"], library, "meshes", "Linked Kit", secret)
    scene.collection.objects.link(bpy.data.objects.new("Packed Kit", linked))
    packed["packed"] = linked.is_linked_packed
    bpy.ops.wm.save_mainfile(compress=False)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    collection = bpy.data.collections.new("Override Set")
    collection.objects.link(bpy.data.objects.new("Override Kit", quad("Override Mesh")))
    library = _library(library_folder, "override-library.blend", [collection])
    scene, linked = _link(by_name["override"], library, "collections", "Override Set", secret)
    packed["override"] = linked.is_linked_packed
    # In the scene first, as a person overrides it: Blender 5.3 makes no override of a collection only loaded (it is
    # still indirect then), packed or not; 5.2 did.
    scene.collection.children.link(linked)
    linked.override_hierarchy_create(scene, bpy.context.view_layer, do_fully_editable=True)
    packed["override_made"] = any(item.override_library for item in bpy.data.objects)
    bpy.ops.wm.save_mainfile(compress=False)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    first = bpy.data.objects.new("Cycle A", quad("Cycle Mesh A"))
    second = bpy.data.objects.new("Cycle B", quad("Cycle Mesh B"))
    first.parent = second
    second.constraints.new("COPY_LOCATION").target = first
    collection = bpy.data.collections.new("Cycle Set")
    collection.objects.link(first)
    collection.objects.link(second)
    library = _library(library_folder, "cycle-library.blend", [collection])
    scene, linked = _link(by_name["cycle"], library, "collections", "Cycle Set", secret)
    packed["cycle"] = linked.is_linked_packed
    empty = bpy.data.objects.new("Cycle Instancer", None)
    empty.instance_type = "COLLECTION"
    empty.instance_collection = linked
    scene.collection.objects.link(empty)
    bpy.ops.wm.save_mainfile(compress=False)
    packed.update(_write_theirs(by_name, quad, secret, library_folder))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    workspace = Path(secret).parent.parent / "workspace"   # The chat's default workspace (``Controller.workspace``).
    library = _library(workspace, "named-kit.blend", [quad(str(secret))])
    scene = _start(by_name["named"])
    with bpy.data.libraries.load(str(library), link=True) as (_source, target):
        target.meshes = [str(secret)]
    packed["named"] = bool(target.meshes and target.meshes[0].name == str(secret))
    scene.collection.objects.link(bpy.data.objects.new("Named Kit", target.meshes[0]))
    bpy.ops.wm.save_mainfile(compress=False)
    return packed


def _painted(name, group):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.new("ShaderNodeGroup").node_tree = group
    return material


def _write_theirs(by_name, quad, secret, library_folder):
    """``theirs`` and ``mixed``: a person's library ``kit-own.blend`` whose collection's material uses "Kit Bevel",
    packed from ``asset-library.blend``."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    assets = _library(library_folder, "asset-library.blend", [bpy.data.node_groups.new("Kit Bevel", "ShaderNodeTree")])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(assets), link=True, pack=True) as (_source, target):
        target.node_groups = ["Kit Bevel"]
    collection = bpy.data.collections.new("Own Set")
    collection.use_fake_user = True
    collection.objects.link(bpy.data.objects.new("Own Kit", quad("Own Mesh", [_painted("Own Paint",
                                                                                      target.node_groups[0])])))
    kit = Path(library_folder) / "kit-own.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(kit), compress=False)
    packed = {}
    for name in ("theirs", "mixed"):
        scene = _start(by_name[name])
        with bpy.data.libraries.load(str(kit), link=True) as (_source, target):
            target.collections = ["Own Set"]
        empty = bpy.data.objects.new("%s Instancer" % name.title(), None)
        empty.instance_type, empty.instance_collection = "COLLECTION", target.collections[0]
        empty["note"] = str(secret)
        scene.collection.objects.link(empty)
        group = bpy.data.node_groups.get("Kit Bevel")
        packed[name] = bool(group and group.library and group.is_linked_packed)
        if name == "mixed":   # The worker's own use, and its own text in the packed group.
            with bpy.data.libraries.load(str(assets), link=True, pack=True) as (_source, target):
                target.node_groups = ["Kit Bevel"]
            target.node_groups[0]["note"] = str(secret)
            holder = bpy.data.objects.new("Mixed Kit", quad("Mixed Mesh", [_painted("Mixed Paint",
                                                                                      target.node_groups[0])]))
            scene.collection.objects.link(holder)
            packed["mixed_own_use"] = target.node_groups[0].is_linked_packed
        bpy.ops.wm.save_mainfile(compress=False)
    return packed


def _local_and_cleared(objects):
    """Whether each object, its mesh and the objects it uses are local data with no library override, every mesh's
    note cut to the file's name."""
    for obj in objects:
        used = [obj, obj.parent] + [constraint.target for constraint in obj.constraints]
        for item in [item for item in used if item is not None] + ([obj.data] if obj.data else []):
            if item.library is not None or item.override_library is not None:
                return False
        if obj.data is not None and obj.data.get("note") != "chat.json":
            return False
    return True


def _corrupt_overrides():
    """Overrides whose reference is local data (Blender removes their override data when the file is read)."""
    return sorted(item.name for kind in ("objects", "collections", "meshes") for item in getattr(bpy.data, kind)
                  if item.override_library is not None and item.override_library.reference is not None
                  and item.override_library.reference.library is None)


def checks(check, merge, packed, saved_leaks):
    """``merge(number)`` merges that part and returns the answer; ``saved_leaks()`` saves the file and returns where
    it holds anything private, after reading it back (``open_mainfile`` raises on a corrupt file)."""
    for name in ("packed", "override", "cycle", "theirs", "mixed"):
        check("%s_link_part_packs_it" % name, packed.get(name) is True, packed)
    check("named_link_part_links_it_by_that_name", packed.get("named") is True, packed)
    check("override_link_part_overrides_it", packed.get("override_made") is True, packed)
    answer = merge(15)
    holder = bpy.data.objects.get("Packed Kit")
    check("packed_link_made_local_and_cleared", answer.get("success") is True and holder is not None
          and _local_and_cleared([holder]), (answer, holder and holder.data and holder.data.get("note")))
    answer = merge(16)
    kit = [obj for obj in bpy.data.objects if obj.name.startswith("Override Kit")]
    check("override_link_made_plain_local_data", answer.get("success") is True and len(kit) == 1
          and _local_and_cleared(kit) and not _corrupt_overrides(), (answer, [obj.name for obj in kit],
                                                                     _corrupt_overrides()))
    answer = merge(17)
    empty = bpy.data.objects.get("Cycle Instancer")
    collection = empty.instance_collection if empty else None
    members = list(collection.objects) if collection else []
    check("cycle_link_made_local_never_refused", answer.get("success") is True and collection is not None
          and collection.library is None and len(members) == 2 and _local_and_cleared(members),
          (answer, [(obj.name, obj.library) for obj in members]))
    check("no_merged_datablock_still_linked_packed", not [item.name for kind in ("objects", "meshes", "collections")
                                                          for item in getattr(bpy.data, kind)
                                                          if item.is_linked_packed], None)
    answer = merge(19)   # First: once the person's file holds the library's "Kit Bevel", a part's is never read.
    notes = [item.get("note") for item in bpy.data.node_groups if item.name.startswith("Kit Bevel")]
    check("mixed_link_workers_copy_cut_or_refused", answer.get("success") is not True
          or all(note in (None, "chat.json") for note in notes), (answer, notes))
    mixed = answer
    answer = merge(18)
    check("theirs_link_refused_with_nothing_merged", answer.get("success") is not True
          and answer.get("error_code") == "UNSUPPORTED" and "Kit Bevel" in str(answer.get("error"))
          and bpy.data.objects.get("Theirs Instancer") is None, answer)
    answer = merge(20)
    check("named_link_refused_with_nothing_merged", answer.get("success") is not True
          and answer.get("error_code") == "UNSUPPORTED" and bpy.data.objects.get("Named Kit") is None, answer)
    found = saved_leaks()
    check("linked_parts_saved_file_holds_nothing_private_and_reads", not found, (found, mixed))
