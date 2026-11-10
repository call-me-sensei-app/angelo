# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a change reaches, for the web link's live frames (``web_link_live``; doc/angelo/web_link.rst, "Live frames").
Main thread.

* ``evaluated_users``: the objects whose evaluated data or materials come from changed data (a material geometry
  nodes or a modifier sets, an object of a collection that is instanced but not in the scene), each evaluated object
  judged once from ``depsgraph.ids`` (and the originals of what the scene instances), then every object that shows
  one of them through instancing (``Shown``), found from the original objects: a collection instance, geometry
  nodes that reference the object or its collection (Blender 5.2 keeps a modifier's inputs in
  ``modifier.properties.inputs``), a particle system's instance object or collection, a vertex- or face-instancing
  parent. Its cost follows the scene's objects and references, never how many copies they make (break-it round 9,
  H1: walking ``depsgraph.object_instances`` cost 1.25 s a delta at a million geometry-nodes instances; round 10,
  M2: no work per candidate grows with the others).
* ``file_stamp``: every file an image reads, as Blender names it (``blend_file_paths.read_files``: each UDIM tile's,
  the frame's of a sequence, each view's), each keyed by its stat once the file system's clock has passed it, or by
  its bytes when first seen before that, in one form per file while it is unchanged (``change_keys``). Break-it
  round 9, M2 (tiles, frames, movies were never stamped) and L2 (a same-size file replaced by a rename with the old
  modification time).
"""

import os

from . import change_keys
from .blend_file_paths import frame_number, located, read_files, view_suffixes

FILED = frozenset({"FILE", "SEQUENCE", "MOVIE", "TILED"})   # Image sources Blender reads from files.
INSTANCING = frozenset({"VERTS", "FACES"})   # Instance types that copy the instancer's own children.


# Instancing --------------------------------------------------------------------------------------------------------

def evaluated_users(depsgraph, data, uses, objects):
    """Original session uids of the objects whose data (by its original's uid) is in ``data`` or whose materials
    ``uses`` (a material -> bool), judged on what the depsgraph evaluated (each object of ``depsgraph.ids`` once) and
    on the originals of the collections and objects ``objects`` (the scene's) instance, which the depsgraph may not
    hold (a collection only geometry nodes instance); and of every object that shows one of them through
    instancing."""
    import bpy
    hits = set()
    for block in depsgraph.ids:
        if isinstance(block, bpy.types.Object) and block.original.session_uid not in hits and _uses_data(
                block, data, uses, evaluated=True):
            hits.add(block.original.session_uid)
    shown = Shown(objects)
    hits |= {obj.session_uid for obj in shown.instanced if obj.session_uid not in hits and _uses_data(
        obj, data, uses, evaluated=False)}
    return hits | shown.instancers(hits) if hits else hits


def _uses_data(obj, data, uses, evaluated):
    block = obj.data
    if block is not None and (block.original if evaluated else block).session_uid in data:
        return True
    materials = list(getattr(block, "materials", ())) + [slot.material for slot in obj.material_slots]
    return any(material is not None and uses(material.original if evaluated else material) for material in materials)


class Shown:
    """Who shows whom through instancing among ``objects`` (the scene's) and every object of the collections they
    instance, at any depth, read once: a collection instance, geometry nodes that name an object or a collection (a
    node socket or a modifier input), a particle system's instance object or collection, a vertex- or
    face-instancing parent. Building it and asking it cost what the scene's objects and references cost, never what
    the copies do (break-it round 10, M2: a set rebuilt per candidate made 10,000 instancers cost 0.4 s)."""

    def __init__(self, objects):
        self.members, self.trees = {}, {}
        self.by_object, self.by_collection = {}, {}   # uid -> instancer objects
        self.instanced, seen, pending = [], set(), list(objects)
        scene = {obj.session_uid for obj in objects}
        while pending:
            obj = pending.pop()
            if obj.session_uid in seen:
                continue
            seen.add(obj.session_uid)
            if obj.session_uid not in scene:
                self.instanced.append(obj)
            for kind, block in self._shows(obj):
                if kind == "object":
                    self.by_object.setdefault(block.session_uid, []).append(obj)
                    pending.append(block)
                else:
                    self.by_collection.setdefault(block.session_uid, []).append(obj)
                    if block.session_uid not in self.members:
                        objects_in = list(block.all_objects)
                        self.members[block.session_uid] = [member.session_uid for member in objects_in]
                        pending.extend(objects_in)
        self.in_collections = {}
        for collection, uids in self.members.items():
            for uid in uids:
                self.in_collections.setdefault(uid, []).append(collection)

    def instancers(self, marked):
        """The session uids of the objects that show one of ``marked`` through instancing, to any depth (an
        instancer inside an instanced collection marks that collection's instancers too)."""
        found, pending = set(), list(marked)
        while pending:
            uid = pending.pop()
            showing = list(self.by_object.get(uid, ()))
            for collection in self.in_collections.get(uid, ()):
                showing.extend(self.by_collection.get(collection, ()))
            for obj in showing:
                if obj.session_uid not in found and obj.session_uid not in marked:
                    found.add(obj.session_uid)
                    pending.append(obj.session_uid)
        return found

    def _shows(self, obj):
        """("object" | "collection", block) for each object or collection ``obj`` makes copies of or geometry
        from."""
        kind = getattr(obj, "instance_type", "NONE")
        if kind == "COLLECTION" and obj.instance_collection is not None:
            yield "collection", obj.instance_collection
        if kind in INSTANCING:
            yield from (("object", child) for child in obj.children)
        for system in getattr(obj, "particle_systems", ()):
            settings = system.settings
            block = {"OBJECT": settings.instance_object, "COLLECTION": settings.instance_collection}.get(
                settings.render_type)
            if block is not None:
                yield _kind(block), block
        for modifier in getattr(obj, "modifiers", ()):
            if modifier.type == "NODES":
                for block in _modifier_inputs(modifier) + self._tree(modifier.node_group):
                    yield _kind(block), block

    def _tree(self, tree, walking=None):
        """Every object and collection a node tree's sockets name, nested groups included (each tree once)."""
        if tree is None:
            return []
        if tree.session_uid in self.trees:
            return self.trees[tree.session_uid]
        walking = set() if walking is None else walking
        if tree.session_uid in walking:
            return []
        walking.add(tree.session_uid)
        named = []
        for node in tree.nodes:
            for socket in node.inputs:
                value = getattr(socket, "default_value", None)
                if _is_block(value):
                    named.append(value)
            named.extend(self._tree(getattr(node, "node_tree", None), walking))
        self.trees[tree.session_uid] = named
        return named


def _modifier_inputs(modifier):
    """The objects and collections a geometry-nodes modifier's inputs hold. Blender 5.2 keeps them in generated
    property groups (``modifier.properties.inputs.<identifier>.value``), never as ID properties of the modifier."""
    inputs = getattr(getattr(modifier, "properties", None), "inputs", None)
    if inputs is None:
        return []
    found = []
    for prop in inputs.bl_rna.properties:
        if prop.type == "POINTER" and prop.identifier != "rna_type":
            value = getattr(getattr(inputs, prop.identifier, None), "value", None)
            if _is_block(value):
                found.append(value)
    return found


def _kind(block):
    """"object" or "collection", told apart without reading a collection's objects."""
    return "object" if hasattr(block, "instance_type") else "collection"


def _is_block(value):
    """An object or a collection (what can be instanced), never a number, a string or another kind of datablock."""
    return (hasattr(value, "session_uid") and hasattr(value, "instance_type")) or hasattr(value, "all_objects")


# Image files -------------------------------------------------------------------------------------------------------

def sequence_frames(scene):
    """{image session uid: frames} that the Image Texture nodes of materials and node groups read from each image
    sequence at the scene's current frame (``blend_file_paths.frame_number``)."""
    import bpy
    frames = {}
    trees = [material.node_tree for material in bpy.data.materials if material.node_tree is not None]
    for tree in trees + list(bpy.data.node_groups):
        for node in tree.nodes:
            image, user = getattr(node, "image", None), getattr(node, "image_user", None)
            if image is not None and user is not None and image.source == "SEQUENCE":
                frames.setdefault(image.session_uid, set()).add(frame_number(
                    scene.frame_current, user.frame_duration, user.frame_start, user.frame_offset, user.use_cyclic))
    return frames


Deferred = change_keys.Deferred   # The bytes a call may hash are spent: the image is stamped in a later call.
KEYS = change_keys.ChangeKeys()   # The live link's own: one form per file while it is unchanged.
WHOLE = 1 << 62   # An image's own bound once it has begun: none.


def file_stamp(image, render=None, frames=(), budget=None):
    """A key of every file ``image`` reads (each tile's, ``frames``' of a sequence, each view's), or None for an image
    with no file of its own (generated or packed). ``budget``: a one-item list of the bytes this call may still hash
    (None: no bound); past it, ``Deferred``."""
    import bpy
    if image.source not in FILED or image.packed_file is not None or len(image.packed_files) or not image.filepath_raw:
        return None
    tiles = [tile.number for tile in image.tiles if not tile.is_generated_tile] if image.source == "TILED" else ()
    suffixes = view_suffixes(image, render) if render is not None else ()
    paths = []
    for frame in (sorted(frames) or [0]) if image.source == "SEQUENCE" else [0]:
        paths.extend(read_files(image.filepath_raw, image.source, tiles=tiles, frame=frame, suffixes=suffixes))
    # An image begun within the bound is read whole (the one that crosses it), so each one's turn completes: an
    # image deferred at every poll would miss every write while its files stay unsettled (break-it, change_keys).
    own = budget if budget is None or budget[0] <= 0 else [WHOLE]
    keys = []
    # Each file is keyed by its identity (device, inode), never a spelling of its path: Make Paths Relative, a case
    # or a symbolic link respelling the same file keeps every key (bake-buffer review; break-it round 3, L4).
    for full in dict.fromkeys(os.path.normpath(located(path, image.library, bpy.path.abspath)) for path in paths):
        try:
            info = os.stat(full)
            identity = (info.st_dev, info.st_ino) if info.st_ino else os.path.normcase(os.path.realpath(full))
            keys.append((identity, change_keys.key(info, full, keys=KEYS, budget=own, name=identity)))
        except (OSError, ValueError):
            keys.append((full, None))
    if own is not budget:
        budget[0] -= WHOLE - own[0]
    return tuple(keys)
