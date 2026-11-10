# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a World's environment picture depends on, as a cache key (``web_world``, ``web_environment``). Main thread.

``key(world, engine)`` reads every input that can change what the World renders, generically through Blender's
RNA, so no node type, setting or nested data has to be listed: each node's properties (a Color Ramp's elements, a
curve's points, an image user, a texture mapping and any other data a node owns, read recursively), every socket's
value (inputs, and outputs where constant nodes such as RGB and Value keep theirs), its links, node groups (each
read once), the World's own settings (camera visibility, for one), each image's identity and contents (its file's
time and size, a digest of the packed bytes it holds, its colour space) and each text's contents (a Script node's).
Only what a node editor shows the person (location, size, colour, selection, labels, collapsed panels, a list's
active row, nodes with no sockets such as frames) is left out, so moving a node never makes the picture again;
``test_blender_web_world_key`` holds that to Blender's own rule: every setting that updates the depsgraph moves the
key, and a node's own setting that updates none leaves it (data a node owns and the World's settings stay in either
way: a render may still read them).

Some inputs can't be read reliably here: a datablock other than an image, a group or a text (a Texture Coordinate's
object), a Script node (its includes), a node type Blender doesn't know (a Python node, a newer file's), an image
painted in memory or one that changes per frame, a driver or animation, more than the bounds. Then the key also holds
``changes()`` and the frame. ``changes()`` counts the depsgraph updates of the datablocks such Worlds use (the World,
its node trees, images, texts and objects; an object only when it moves or changes shape or shading, never on a
selection), outside exports (``quiet``: the exporter steps frames and evaluates the scene, which edits nothing). So
such a picture is never reused after an edit of what it reads, and is reused while nothing it reads changes (the
live preview exports again when a picture lands and must not start another). The handler counting them is
registered only once such a World has been keyed (``_watch``), so an ordinary session's edits never run it.

Duck-typed: anything without ``bl_rna`` is read through its attributes, as the host tests' stand-ins are.
"""

import os
import zlib

from . import change_keys
from .blend_file_paths import image_files, located, node_file_places

MAX_NODES = 256          # Nodes per tree; more and the key holds the change count too.
MAX_GROUP_DEPTH = 4      # Node groups inside node groups.
MAX_DEPTH = 6            # Nested data inside one node (a curve mapping's curves' points: 3).
MAX_ITEMS = 1024         # Items of one collection (a ramp's elements, a curve's points).
MAX_VALUES = 200000      # Values read for one key; more and the key holds the change count too.
# What only the node editor shows of a node (or the World): never in a rendered result. Data a node owns is read
# whole but for ``NESTED_EDITOR_ONLY``: a ramp element's ``color`` is what it renders.
EDITOR_ONLY = frozenset({
    "rna_type", "name", "label", "location", "location_absolute", "width", "height", "dimensions", "select",
    "show_options", "show_preview", "show_texture", "hide", "color", "use_custom_color", "parent", "inputs",
    "outputs", "internal_links", "type", "bl_idname", "bl_label", "bl_description", "bl_icon", "bl_static_type",
    "bl_width_default", "bl_width_min", "bl_width_max", "bl_height_default", "bl_height_min", "bl_height_max",
    "color_tag", "warning_propagation", "is_ui_hidden", "show_expanded", "is_icon_visible", "enabled_inputs",
    "panel_states", "active_index", "use_auto_update"})
NESTED_EDITOR_ONLY = frozenset({"rna_type", "select"})
# Datablocks read here; any other a node points to can't be.
READ_IDS = frozenset({"Image", "ShaderNodeTree", "Text"})

_count = [0]
_quiet = [0]
WATCHED_WORLDS = 4        # The latest Worlds keyed with the count whose datablocks are watched (scenes, an undo).
_recent = []              # Their datablocks' session_uid sets, newest last.
_watched = set()          # Their union.
_handler = []             # The registered depsgraph handler, once anything is watched.


def changes():
    return _count[0]


class quiet:
    """``with quiet():`` an export, whose exporter steps frames and evaluates the scene without editing it."""

    def __enter__(self):
        _quiet[0] += 1

    def __exit__(self, *_exc):
        _quiet[0] -= 1


def _on_update(_scene, depsgraph):
    if _quiet[0] or not _watched:
        return
    for update in depsgraph.updates:
        block = getattr(update.id, "original", None) or update.id   # An update names the evaluated copy (uid 0).
        if getattr(block, "session_uid", None) not in _watched:
            continue
        if _rna_name(block) == "Object" and not (update.is_updated_transform or update.is_updated_geometry
                                                     or update.is_updated_shading):
            continue   # A selection click.
        _count[0] += 1
        return


def _watch(identities):
    """Count updates of ``identities`` from now on, with those of the last few Worlds keyed with the count."""
    identities = frozenset(identities)
    if identities in _recent:
        _recent.remove(identities)
    _recent.append(identities)
    del _recent[:-WATCHED_WORLDS]
    _watched.clear()
    _watched.update(*_recent)
    try:
        import bpy
    except ImportError:   # No Blender: the host tests.
        return
    if not _handler:
        handler = bpy.app.handlers.persistent(_on_update)   # Kept across File > Open.
        bpy.app.handlers.depsgraph_update_post.append(handler)
        _handler.append(handler)


def stop():
    """Forget what is watched and remove the handler (the add-on unloads)."""
    _watched.clear()
    _recent.clear()
    if _handler:
        import bpy
        if _handler[0] in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(_handler[0])
        _handler.clear()


class _Digest:
    def __init__(self, frame=None):
        self.frame = frame      # The scene frame a bake renders at: None for a picture read from the stored file.
        self.unreliable = False
        self.ids = set()        # session_uid of the datablocks read.
        self.groups = {}        # A group's parts, read once per key.
        self.values = 0

    def uses(self, block):
        identity = getattr(block, "session_uid", None)
        if identity is not None:
            self.ids.add(identity)


def _is_id(value):
    return hasattr(value, "session_uid") and hasattr(value, "users")


def _rna_name(value):
    rna = getattr(value, "bl_rna", None)
    return getattr(rna, "identifier", type(value).__name__)


def _plain(value):
    """A hashable copy of a property's value: numbers and text as they are, arrays as tuples."""
    if isinstance(value, float) and value != value:
        return "nan"   # Equal to itself, so a key holding it still matches (NaN never equals NaN).
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, (set, frozenset)):   # An enum flag: its items in a fixed order.
        return tuple(sorted(value))
    try:
        return tuple(_plain(item) for item in value)
    except TypeError:
        return repr(value)


KEYS = change_keys.ChangeKeys()   # The World's files: one key while a file is unchanged (``change_keys``).


def _file(path):
    try:   # A miss bakes again: settled, else its bytes (``change_keys``); a file it can't read is missing.
        stat = os.stat(path)
        return change_keys.key(stat, path, (path, stat.st_mtime_ns, stat.st_size), keys=KEYS, change=False)
    except (OSError, ValueError):
        return (path, None)


def _blender_abspath():
    try:
        import bpy
        return bpy.path.abspath
    except ImportError:   # No Blender: the host tests' stand-ins hold absolute paths.
        return lambda path, library=None: path


def _abspath(path, library=None):
    return located(path, library, _blender_abspath())


def _normal(path):
    return os.path.normpath(path) if path else ""   # One spelling per file; normpath("") is ".", the working folder.


def image_stamp(image, digest=None, user=None):
    """An image's identity and contents: the files it is read from (time and size), its packed bytes, its colour
    space and size.

    Each picture is keyed on the files its maker opens (the rule ``export_baked_images`` follows for a bake): an
    ``IMAGE`` picture reads the stored file itself (``web_environment``), while a ``BAKE`` (``digest.frame`` set)
    renders the World in another Blender, which opens what Blender names for the node's image ``user`` at that frame
    (``blend_file_paths.image_files``: a sequence's frame, each tile, a movie). Both read the packed bytes when there
    are any, and neither reads pixels held in this Blender, so a file saved over without a reload is what both make
    their picture from (measured, 1.0.10)."""
    if image is None:
        return None
    if digest is not None:
        digest.uses(image)
    path = None
    if image.source in {"FILE", "SEQUENCE", "MOVIE", "TILED"} and not image.packed_file:
        frame = getattr(digest, "frame", None)
        if frame is None:
            path = _file(_normal(_abspath(image.filepath, getattr(image, "library", None))))
        else:
            path = tuple(_file(each) for each in image_files(image, user, frame, _blender_abspath()))
    packed = image.packed_file
    if packed:   # The bytes themselves: a repack of another file of the same size is another picture.
        data = getattr(packed, "data", None)
        packed = (packed.size, zlib.crc32(data) if isinstance(data, (bytes, bytearray)) else None)
    generated = tuple(_plain(getattr(image, name, None)) for name in (
        "generated_type", "generated_width", "generated_height", "generated_color", "use_generated_float"))
    if digest is not None and (image.is_dirty or image.source not in {"FILE", "GENERATED"}):
        digest.unreliable = True   # Painted in memory, a sequence, a movie or tiles: not what the file holds now.
    # Where it reads from, resolved (Make Paths Relative changes no picture); none for packed bytes, read wherever
    # the project is.
    stored = _abspath(image.filepath, getattr(image, "library", None)) if getattr(image, "filepath", "") else ""
    stored = "" if packed else stored
    return (image.name, image.source, _normal(stored), path, packed, generated,
            image.colorspace_settings.name, tuple(int(v) for v in image.size[:2]), bool(image.is_dirty))


def _properties(value, hidden):
    """(identifier, kind, property) of what ``value`` holds but ``hidden``: its RNA, or a stand-in's attributes."""
    rna = getattr(value, "bl_rna", None)
    if rna is None:
        return [(name, None, None) for name in sorted(vars(value)) if name not in hidden]
    inherited = set()
    base = getattr(rna, "base", None)
    if base is not None and base.identifier == "ID":   # A datablock's ID-level fields: users, library, preview.
        inherited = set(base.properties.keys())
    return [(prop.identifier, prop.type, prop) for prop in rna.properties
            if prop.identifier not in hidden and prop.identifier not in inherited]


def _struct(value, digest, depth, skip=()):
    """The digest's parts for ``value``'s properties, nested data read to ``MAX_DEPTH``."""
    if depth > MAX_DEPTH:
        digest.unreliable = True
        return ()
    parts = []
    for name, kind, prop in _properties(value, EDITOR_ONLY if depth == 0 else NESTED_EDITOR_ONLY):
        if name in skip:
            continue
        digest.values += 1
        if digest.values > MAX_VALUES:
            digest.unreliable = True
            break
        try:
            item = getattr(value, name)
        except (AttributeError, RuntimeError):
            continue
        if kind == "STRING" and getattr(prop, "subtype", "") == "FILE_PATH" and item:
            library = getattr(getattr(value, "id_data", None), "library", None)   # An external file: what it holds
            parts.append((name, tuple(_file(path) for path in node_file_places(item, library, _blender_abspath()))))
        elif kind == "POINTER" or (kind is None and hasattr(item, "bl_rna")):
            parts.append((name, _pointer(item, digest, depth, getattr(value, "image_user", None))))
        elif kind == "COLLECTION":
            items = list(item)
            if len(items) > MAX_ITEMS:
                digest.unreliable = True
            parts.append((name, tuple(_pointer(element, digest, depth) for element in items[:MAX_ITEMS])))
        elif callable(item):
            continue
        else:
            parts.append((name, _plain(item)))
    return tuple(parts)


def _pointer(item, digest, depth, user=None):
    """``item`` read for the digest; ``user``: the image user of the node or struct holding it (an image's)."""
    if item is None:
        return None
    if _is_id(item):
        kind = _rna_name(item)
        digest.uses(item)
        if kind == "Image":
            return image_stamp(item, digest, user)
        if kind == "Text":   # A Script node's source: editing it updates no depsgraph, so its words count.
            return (kind, item.name, zlib.crc32(item.as_string().encode("utf-8")))
        if kind not in READ_IDS:
            digest.unreliable = True   # An object or another datablock: what it holds isn't read here.
        return (kind, item.name)       # A node group's nodes are read where the group node is (``_tree``).
    return _struct(item, digest, depth + 1)


def _socket(socket):
    value = getattr(socket, "default_value", None)
    if _is_id(value):
        return (socket.identifier, _rna_name(value), value.name)
    return (socket.identifier, _plain(value) if not hasattr(value, "bl_rna") else repr(value),
            bool(getattr(socket, "is_unavailable", False)))


def _known(node):
    """Whether Blender itself defines ``node``'s type (not a Python add-on's, nor a type this Blender lacks)."""
    if getattr(node, "bl_rna", None) is None:
        return True   # A host-test stand-in.
    return node.bl_idname not in {"NodeUndefined", "ShaderNodeScript", "NodeCustomGroup", "ShaderNodeCustomGroup"} \
        and type(node).__module__ == "bpy.types"


def _animated(block):
    data = getattr(block, "animation_data", None)
    return data is not None and bool(data.action is not None or len(data.drivers) or len(data.nla_tracks))


def _tree(tree, digest, depth):
    digest.uses(tree)
    nodes = list(tree.nodes)
    if len(nodes) > MAX_NODES or len(tree.links) > MAX_NODES * 4 or _animated(tree):
        digest.unreliable = True
    parts = []
    for node in nodes[:MAX_NODES]:
        known = _known(node)
        if not known:
            digest.unreliable = True   # Read whole below: a Script node gains its sockets when it compiles.
        elif not len(node.inputs) and not len(node.outputs):   # A frame: nothing it holds can reach a shader.
            parts.append((node.name, node.bl_idname))
            continue
        parts.append((node.name, node.bl_idname, bool(node.mute), _struct(node, digest, 0),
                      tuple(_socket(socket) for socket in node.inputs),
                      tuple(_socket(socket) for socket in node.outputs)))
        group = getattr(node, "node_tree", None)
        if group is None:
            continue
        if depth >= MAX_GROUP_DEPTH:
            digest.unreliable = True
            continue
        identity = getattr(group, "session_uid", None) or id(group)
        if identity not in digest.groups:
            digest.groups[identity] = None   # A group that holds itself is read once.
            digest.groups[identity] = _tree(group, digest, depth + 1)
        parts.append((group.name, identity))
    parts.extend((link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier,
                  bool(link.is_muted), bool(getattr(link, "is_valid", True)))
                 for link in list(tree.links)[:MAX_NODES * 4])
    return tuple(parts)


def _volatile(digest, frame):
    if not digest.unreliable:
        return None
    _watch(digest.ids)
    return (changes(), frame)


def key(world, engine, frame=None):
    """The key of ``world``'s picture as ``engine`` renders it. ``frame``: (frame, subframe), for what may move."""
    digest = _Digest(frame[0] if frame else 0)
    digest.uses(world)
    tree = world.node_tree
    settings = _struct(world, digest, 0, skip=("node_tree", "animation_data", "original"))
    graph = _tree(tree, digest, 0) if tree is not None else ()
    if _animated(world):
        digest.unreliable = True
    groups = tuple(sorted(digest.groups.items(), key=lambda item: str(item[0])))
    return ("BAKE", world.name, engine, settings, graph, groups, _volatile(digest, frame))


def image_key(image, turn, frame=None):
    """The key of a picture read from ``image`` turned by ``turn``."""
    digest = _Digest()
    stamp = image_stamp(image, digest)
    return ("IMAGE", stamp, turn, _volatile(digest, frame))
