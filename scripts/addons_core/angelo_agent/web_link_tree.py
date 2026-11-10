# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web link's live export, inside Blender's glTF exporter (``web_link_live`` makes the frames; doc/angelo/web_link.rst,
"Live frames"). The hooks act only on the web link's own export id (``web_export.WebExportExtension``):

* ``filter_tree`` (``gather_tree_filter_tag_hook``) records what a full export holds, then keeps the changed objects,
  their ancestors as empties, the armatures their skins need, and the bodies and shapes they write into each other.
* ``mark_nodes`` (``gather_scene_hook``) gives every node its key and role; ``owners_of`` says which objects a node's
  content depends on: its own, and each instancer it is a copy under.
* ``image`` (``gather_image_hook``) names each image by its bytes and writes one every receiving page has as a stub.
"""

import hashlib

from .web_link_digest import KEY, ROLE

CONTENT, PARENT = "content", "parent"
STUB, DIGEST = "angelo_stub", "angelo_digest"


def stub_png(digest):
    """The picture an image every receiving page already has is sent as: a valid 1 x 1 transparent RGBA PNG whose
    text chunk names the image, so no two stubs have the same bytes. The exporter shares one buffer view among equal
    bytes, and three.js's loader caches a picture by buffer view, so equal stubs would become one texture (the npm
    side's e2e found both: the first stub's pixels didn't inflate to a whole row)."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"tEXt", b"angelo\x00" + digest.encode("ascii"))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")) + chunk(b"IEND", b""))


# The export being written (read by the glTF hooks): which objects are content (None: all), what the full tree
# holds, the images every receiving page has, and what the hooks found.
_export = {"active": False, "content": None, "known_images": frozenset(), "objects": set(), "owners": {},
           "physics_changed": frozenset(),
           "filtered": False, "marked": False}


# The exporter's hooks (web_export.WebExportExtension, only for this export id) ---------------------------------------

def _object_uid(vnode):
    return vnode.blender_object.session_uid if vnode.blender_object is not None else None


def _armature_of(vtree, vnode):
    """The armature object whose bones a mesh's skin needs (its Armature modifier), or None."""
    obj = vnode.blender_object
    for modifier in getattr(obj, "modifiers", ()):
        if modifier.type == "ARMATURE" and modifier.object is not None:
            return modifier.object.session_uid
    return None


def filter_tree(vtree):
    """Record what a full export holds, then keep only the content objects, their ancestors (as empties) and the
    armatures their skins need (``gather_tree_filter_tag_hook``)."""
    if not _export["active"]:
        return
    kept = {uuid for uuid, vnode in vtree.nodes.items() if vnode.keep_tag}
    _export["objects"] = {_object_uid(vtree.nodes[uuid]) for uuid in kept} - {None}
    content = _export["content"]
    _export["bodies"] = {}
    if content is None:
        _export["filtered"] = True
        return
    owners = {uuid: owners_of(vtree, uuid) for uuid in kept}
    armatures = set()
    for uuid in kept:
        vnode = vtree.nodes[uuid]
        if owners[uuid] & content and vnode.blender_type != _bone_type(vnode):
            armature = _armature_of(vtree, vnode)
            if armature is not None:
                armatures.add(armature)
    wanted = set()
    for uuid in kept:
        vnode = vtree.nodes[uuid]
        if owners[uuid] & content or _object_uid(vnode) in armatures:
            wanted.add(uuid)
    walked = set()
    for uuid in list(wanted):   # A body's shapes on its descendants are written with it (break-it round 5, M2),
        _with_shapes(vtree, uuid, kept, wanted, content, walked)
        _with_body(vtree, uuid, kept, wanted, content)   # and a shape changes the body that lists it (round 6, M2).
    keep = set(wanted)
    for uuid in wanted:   # Every ancestor, so transforms stay local to their real parents.
        parent = vtree.nodes[uuid].parent_uuid
        while parent is not None and parent not in keep:
            keep.add(parent)
            parent = vtree.nodes[parent].parent_uuid
    for uuid in kept:
        vnode = vtree.nodes[uuid]
        vnode.keep_tag = uuid in keep
        if uuid in keep and not owners[uuid] & content and _object_uid(vnode) not in armatures:
            vnode.force_as_empty = True   # An ancestor carries no mesh of its own here.
    _export["filtered"] = True


def _with_body(vtree, uuid, kept, wanted, content):
    """Add the nearest body above a changed object that carries game physics: a body lists its children's trigger
    shapes, so its own content changes with them."""
    try:
        from io_scene_gltf2.angelo_physics.props import marked, stored
    except ImportError:
        return
    obj = vtree.nodes[uuid].blender_object
    if obj is None or not (marked(obj) or obj.session_uid in _export["physics_changed"]):   # Also a shape just gone.
        return
    bodies, walked = _export.setdefault("bodies", {}), []   # Each node's nearest body, filled along each walk.
    parent, body = vtree.nodes[uuid].parent_uuid, None
    while parent is not None and parent in vtree.nodes:
        if parent in bodies:
            body = bodies[parent]
            break
        walked.append(parent)
        above = vtree.nodes[parent].blender_object
        if above is not None and (stored(above) or {}).get("body", 0):
            body = parent
            break
        parent = vtree.nodes[parent].parent_uuid
    for node in walked:
        bodies[node] = body
    if body is not None and body in kept:
        wanted.add(body)
        content.add(vtree.nodes[body].blender_object.session_uid)


def _with_shapes(vtree, uuid, kept, wanted, content, walked):
    """Add the descendants of a changed object that carry game physics: the physics export writes their shapes into
    the body above them, so they belong to its frame (as content: their colliders change with it). ``walked``: the
    nodes already looked at in this export, so each subtree is walked once."""
    try:
        from io_scene_gltf2.angelo_physics.props import marked
    except ImportError:
        return
    pending = list(vtree.nodes[uuid].children)
    while pending:
        child = pending.pop()
        if child in walked or child not in kept or child not in vtree.nodes:
            continue
        walked.add(child)
        obj = vtree.nodes[child].blender_object
        if obj is not None and marked(obj) and child not in wanted:
            wanted.add(child)
            content.add(obj.session_uid)
        pending.extend(vtree.nodes[child].children)


def _bone_type(vnode):
    return getattr(type(vnode), "BONE", 3)


def _starts_scope(vtree, vnode):
    """Whether ``vnode`` is one of an instancer's copies, so it and everything under it belong to that instancer
    (keyed inside its key, sent when it changes): a collection instance's object, a geometry-nodes instance, any
    child of a vertex- or face-instancing object, or a particle copy (an object below an instancer that isn't that
    instancer's own child)."""
    parent = vtree.nodes.get(vnode.parent_uuid) if vnode.parent_uuid is not None else None
    if parent is None or vnode.blender_type == _bone_type(vnode):
        return False
    if getattr(vnode, "blender_type", None) == getattr(type(vnode), "INSTANCE", 7):
        return True
    in_collection = getattr(type(vnode), "CHILDREN_IS_IN_COLLECTION", 91)
    if parent.children_type.get(vnode.uuid) == in_collection:
        return True
    instancer, obj = parent.blender_object, vnode.blender_object
    if instancer is None or obj is None or not getattr(instancer, "is_instancer", False):
        return False
    if getattr(instancer, "instance_type", "") in {"VERTS", "FACES"}:
        return True   # Vertex and face instancing copy the instancer's own children (break-it round 5, H1).
    return getattr(obj, "parent", None) is not instancer


def owners_of(vtree, uuid):
    """Every object a node's content depends on: its own (a bone's armature) and each instancer it is a copy under,
    at any depth (break-it round 4, H1: a particle system's seed moves its copies)."""
    found, vnode = set(), vtree.nodes[uuid]
    own = _object_uid(vnode)
    if own is not None:
        found.add(own)
    while vnode.parent_uuid is not None and vnode.parent_uuid in vtree.nodes:
        parent = vtree.nodes[vnode.parent_uuid]
        if _starts_scope(vtree, vnode) and _object_uid(parent) is not None:
            found.add(_object_uid(parent))
        vnode = parent
    return frozenset(found)


def _scope(vtree, uuid, keys, scopes):
    """The key every node under ``uuid`` is keyed inside: its nearest instancer's key, or "" outside instances."""
    if uuid in scopes:
        return scopes[uuid]
    vnode = vtree.nodes[uuid]
    if vnode.parent_uuid is None or vnode.parent_uuid not in vtree.nodes:
        found = ""
    elif _starts_scope(vtree, vnode):
        found = node_key(vtree, vnode.parent_uuid, keys, scopes)
    else:
        found = _scope(vtree, vnode.parent_uuid, keys, scopes)
    scopes[uuid] = found
    return found


def _instance_own(vtree, vnode):
    """A node with no object of its own (a geometry-nodes instance): its data's uid and its place among its
    siblings with the same data, never the exporter's per-export uuid."""
    data = getattr(getattr(vnode, "data", None), "session_uid", 0)
    parent = vtree.nodes.get(vnode.parent_uuid)
    siblings = parent.children if parent is not None else [vnode.uuid]
    same = [child for child in siblings if child in vtree.nodes
            and getattr(getattr(vtree.nodes[child], "data", None), "session_uid", 0) == data
            and vtree.nodes[child].blender_object is None]
    return "d%d#%d" % (data, same.index(vnode.uuid) if vnode.uuid in same else 0)


def node_key(vtree, uuid, keys, scopes=None):
    """``extras.angelo_uid`` of a node: unique in the file and the same in every export of this process. An object's
    ``session_uid``; a bone's armature key, ``/`` and its name (``#leaf`` for the exporter's leaf node); anything
    inside a collection or geometry-nodes instance, at any depth, behind its instancer's key and ``>``."""
    scopes = {} if scopes is None else scopes
    if uuid in keys:
        return keys[uuid]
    vnode = vtree.nodes[uuid]
    if vnode.blender_type == _bone_type(vnode) and vnode.armature in vtree.nodes:
        bone = vnode.blender_bone.name if vnode.blender_bone is not None else ""
        found = "%s/%s%s" % (node_key(vtree, vnode.armature, keys, scopes), bone,
                             "#leaf" if vnode.leaf_reference is not None else "")
    else:
        own = str(_object_uid(vnode) or _instance_own(vtree, vnode))
        scope = _scope(vtree, uuid, keys, scopes)
        found = scope + ">" + own if scope else own
    keys[uuid] = found
    return found


def mark_nodes(vtree):
    """Give every written node its key and role, and remember which object each key belongs to
    (``gather_scene_hook``)."""
    if not _export["active"]:
        return
    content, keys, scopes, owners = _export["content"], {}, {}, _export["owners"]
    for uuid, vnode in vtree.nodes.items():
        node = getattr(vnode, "node", None)
        if node is None:
            continue
        chain = owners_of(vtree, uuid)   # A bone's is its armature's.
        key = node_key(vtree, uuid, keys, scopes)
        role = CONTENT if content is None or chain & content else PARENT
        if role == CONTENT:
            owners[key] = chain
        node.extras = dict(node.extras or {}, **{KEY: key, ROLE: role})
    _export["marked"] = True


def image(gltf_image):
    """Name an image by its bytes' digest, and send one every receiving page already has as a stub
    (``gather_image_hook``)."""
    if not _export["active"]:
        return
    view = getattr(gltf_image, "buffer_view", None)
    data = getattr(view, "data", None)
    if not isinstance(data, bytes):
        return
    digest = hashlib.sha256(data).hexdigest()
    extras = dict(gltf_image.extras or {}, **{DIGEST: digest})
    if digest in _export["known_images"]:
        view.data = stub_png(digest)
        gltf_image.mime_type = "image/png"
        extras[STUB] = True
    gltf_image.extras = extras
