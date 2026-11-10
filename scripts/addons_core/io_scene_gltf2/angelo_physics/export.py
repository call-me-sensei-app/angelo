# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: Apache-2.0
"""Writing ``Object.angelo_physics`` into the exporter's glTF tree: OMI_physics_body and OMI_physics_shape, or Godot's
name suffixes when the scene asks for them (``Scene.angelo_physics_format``).

The exporter calls ``gather_node_hook`` for every node; an unmarked object costs one storage lookup
(``props.marked``) and nothing else happens. When something is marked, ``gather_gltf_hook`` (the whole tree, before
it is numbered) rearranges it so engines import what the person meant. Godot 4.4 to 4.7.2 is the reference
(``modules/gltf/extensions/physics``), and every rule below also holds for the OMI specification:

* **A body keeps its mesh on a child.** Godot builds the body node *instead of* a mesh for a glTF node with physics,
  so a body's mesh (with its skin and morph weights, and the weight animations) moves to a child named
  ``<name>Mesh`` with no transform. The body keeps the object's name, transform, animation and children.
* **A shape sits on its own node directly below its body.** Godot attaches a shape only to its parent body
  (``_get_ancestor_collision_object`` checks the parent alone; godot-proposals#13419). A shape object deeper in a
  body's hierarchy is written under the body with the composed transform, when nothing on the way is animated;
  otherwise it stays where it is (correct OMI, which Godot reads as a separate static body or area) and the log says
  so. The shape node is ``<name>Collider`` or ``<name>Trigger``, unless the object itself can carry it (no mesh to
  keep, and the shape sits at its origin).
* **Triggers.** Below a Trigger body, shapes are trigger shapes and the body lists them (OMI's compound trigger).
* **Collision only.** A collision-only mesh is written only as its shape: the mesh is in the file (a convex or mesh
  shape points at it) but no node draws it, like Godot's ``-colonly``.

Mesh shapes point at the object's exported mesh. A primitive is fitted (``shapes``) to ``fitting.rest_bounds``, the
measurement the Game Physics panel and Edit Fitted Size share: the evaluated geometry, or for a mesh an armature
deforms the rest pose its skin binds (fitting the current frame would size the shape to a pose the engine never
loads). A skinned mesh's shape is placed where its skin draws it (its first joint times that joint's inverse bind
matrix, as glTF readers do; they ignore a skinned mesh's own node). Any scale between the shape's node and the
fitted object is baked into its dimensions. An export that doesn't apply modifiers writes a mesh the fit may not
match, and says so.
"""

import numpy as np

from ..io.com import gltf2_io
from ..io.com.constants import ComponentType, DataType
from ..io.com.gltf2_io_extensions import ChildOfRootExtension, Extension
from . import fitting, props, shapes
from .godot_suffixes import SUFFIX_NOTES, godot_suffix

BODY_EXTENSION = "OMI_physics_body"
LIVE_EXPORT_ID = "angelo_link_web"   # Angelo's web live link (angelo_agent.web_link_assets.LIVE_EXPORT_ID).
SHAPE_EXTENSION = "OMI_physics_shape"
TRS_PATHS = frozenset({"translation", "rotation", "scale"})
GEOMETRY_TYPES = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META"})
PHYSICS_TYPES = GEOMETRY_TYPES | {"EMPTY"}


class Marked:
    """One exported node of a marked object and the object's physics settings (read once)."""
    __slots__ = ("node", "obj", "body", "mass", "shape", "fit", "axis", "size", "radius", "height", "offset",
                 "collision_only", "mesh", "skin", "bounds")

    def __init__(self, node, obj):
        settings = obj.angelo_physics
        self.node, self.obj, self.mesh, self.skin = node, obj, node.mesh, node.skin
        self.body, self.mass, self.shape = settings.body, settings.mass, settings.shape
        self.fit, self.axis = settings.fit, settings.axis
        self.size, self.radius, self.height = tuple(settings.size), settings.radius, settings.height
        self.offset, self.collision_only = tuple(settings.offset), settings.collision_only
        self.bounds = None   # ``fitting.rest_bounds``, measured once (``_fit``).


class PhysicsExportExtension:
    """The exporter's user extension for game physics (added by ``ExportGLTF2`` to every export)."""

    def __init__(self):
        self.marked = []
        self.formats = {}

    def gather_node_hook(self, node, blender_object, export_settings):
        if blender_object is not None and props.marked(blender_object):
            self.marked.append((node, blender_object))

    def gather_scene_hook(self, scene, blender_scene, export_settings):
        self.formats[id(scene)] = props.scene_format(blender_scene)

    def gather_gltf_hook(self, active_scene_idx, scenes, animations, export_settings):
        if not self.marked:
            return
        log = export_settings['log']
        for index, scene in enumerate(scenes):
            tree = Tree(scene)
            records = [Marked(node, obj) for node, obj in self.marked if id(node) in tree.parents]
            records = [record for record in records if _supported(record, log)]
            if not records:
                continue
            if self.formats.get(id(scene), "OMI") == "GODOT_SUFFIXES":
                write_suffixes(records, log)
            else:
                write_omi(records, tree, animations, export_settings['gltf_yup'], log,
                          export_settings.get('gltf_apply', True),
                          live=export_settings.get("gltf_export_id") == LIVE_EXPORT_ID)


def _supported(record, log):
    if record.obj.type in PHYSICS_TYPES:
        return True
    log.warning("Game physics: %s is a %s; only meshes and empties carry physics, so it's left out"
                % (record.obj.name, record.obj.type.lower()))
    return False


class Tree:
    """Parents of every node reachable from one glTF scene (a root's parent is None)."""

    def __init__(self, scene):
        self.parents = {}
        stack = [(node, None) for node in scene.nodes or ()]
        while stack:
            node, parent = stack.pop()
            if not isinstance(node, gltf2_io.Node) or id(node) in self.parents:
                continue
            self.parents[id(node)] = parent
            stack.extend((child, node) for child in node.children or ())

    def path_below(self, ancestor, node):
        """The nodes from just below ``ancestor`` down to ``node``, or None when ``ancestor`` isn't above it."""
        path = [node]
        parent = self.parents.get(id(node))
        while parent is not None:
            if parent is ancestor:
                return list(reversed(path))
            path.append(parent)
            parent = self.parents.get(id(parent))
        return None


def owner_body(record, bodies, tree):
    """(owner record, path) of the nearest body at or above the record's object: the object's own body, else the
    nearest ancestor object's, found in this node's glTF ancestry (a collection instance has its own copy). The path
    runs from just below the owner's node down to this node ([] for its own body). (None, None) without one; (owner,
    None) when the owner's object exists but isn't above this node in the file (a flattened hierarchy)."""
    if record.body != "NONE":
        return record, []
    parent = record.obj.parent
    while parent is not None:
        candidates = bodies.get(parent.name)
        if candidates:
            for candidate in candidates:
                path = tree.path_below(candidate.node, record.node)
                if path is not None:
                    return candidate, path
            return candidates[0], None
        parent = parent.parent
    return None, None


def _relative(path):
    matrix = None
    for node in path:
        local = shapes.compose(node.translation, node.rotation, node.scale)
        matrix = local if matrix is None else shapes.multiply(matrix, local)
    return matrix


def _floats(accessor, count):
    """An accessor's first ``count`` floats, or None when its bytes aren't at hand."""
    data = getattr(getattr(accessor, "buffer_view", None), "data", None)
    if (data is None or accessor.component_type != ComponentType.Float or accessor.sparse is not None
            or accessor.byte_offset or len(data) < count * 4):
        return None
    return np.frombuffer(data, dtype="<f4", count=count).astype(np.float64)


def _world(tree, node):
    """A node's transform in the glTF scene, composed from its ancestors' TRS (numpy 4x4)."""
    path = [node]
    parent = tree.parents.get(id(node))
    while parent is not None:
        path.append(parent)
        parent = tree.parents.get(id(parent))
    return np.array(_relative(list(reversed(path))), dtype=np.float64)


def _skin_frame(skin, tree):
    """Where a skin draws the vertices written for it, in the scene: its first joint's transform times that joint's
    inverse bind matrix (how every glTF reader places a skinned mesh, whatever its own node says), or None."""
    joints = skin.joints or ()
    if not joints or not isinstance(joints[0], gltf2_io.Node) or id(joints[0]) not in tree.parents:
        return None
    values = _floats(skin.inverse_bind_matrices, 16)
    return None if values is None else _world(tree, joints[0]) @ values.reshape(4, 4).T   # Stored column by column.


def _gltf_matrix(matrix, yup):
    """A Blender object matrix in glTF axes (the exporter's +Y up conversion on both sides)."""
    axes = np.array(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)), dtype=np.float64) if yup else np.eye(4)
    return axes @ np.array(matrix, dtype=np.float64) @ axes.T


def _skinned_frames(record, tree, yup):
    """For a skinned record, (object axes -> its node's frame, written vertices -> its node's frame); None otherwise.
    The exporter writes a skinned mesh's vertices in world space and glTF draws them through the skin, never at the
    mesh's own node, so a shape fitted in the object's axes, or pointing at those vertices, needs these to sit where
    the mesh is drawn."""
    if record.skin is None:
        return None
    written = _skin_frame(record.skin, tree)
    to_node = np.linalg.inv(_world(tree, record.node)) @ (np.eye(4) if written is None else written)
    return to_node @ _gltf_matrix(record.obj.matrix_world, yup), to_node


def _then(relative, matrix):
    """``relative`` (a 4x4 tuple matrix or None) followed by ``matrix`` (numpy or None), as a tuple matrix or None."""
    if matrix is None:
        return relative
    matrix = tuple(tuple(float(value) for value in row) for row in matrix)
    return matrix if relative is None else shapes.multiply(relative, matrix)


def _fit(record):
    if record.fit == "MANUAL" or record.obj.type == "EMPTY":
        return shapes.manual(record.shape, record.offset, record.size, record.radius, record.height, record.axis)
    if record.bounds is None:
        record.bounds = fitting.rest_bounds(record.obj)   # The panel and Edit Fitted Size measure the same way.
    return shapes.fit_bounds(record.shape, *record.bounds, record.axis)


def _placed(record, relative, tree, yup):
    """(translation, rotation, scale, OMI shape entry) of a node holding ``record``'s shape below a node whose frame
    reaches the record's node through ``relative`` (None: the record's node itself); identity parts are None. A
    primitive bakes any scale into its size; a mesh shape can't be resized, so its node keeps the scale."""
    frames = _skinned_frames(record, tree, yup)
    if record.shape in shapes.MESH_SHAPES:
        matrix = _then(relative, frames[1] if frames else None)
        translation, rotation, scale = shapes.decompose(matrix) if matrix is not None else (None, None, None)
        entry = shapes.omi_shape(kind=record.shape, mesh=record.mesh)
    else:
        translation, rotation, fit = shapes.placement(shapes.to_gltf_fit(_fit(record), yup),
                                                      _then(relative, frames[0] if frames else None))
        scale, entry = None, shapes.omi_shape(fit)
    return (_vector(translation or (0, 0, 0), (0, 0, 0)),
            _vector(rotation or shapes.IDENTITY_ROTATION, shapes.IDENTITY_ROTATION),
            _vector(scale or (1, 1, 1), (1, 1, 1)), entry)


def _unapplied(record, yup):
    """Whether a fitted primitive measures modifiers this export doesn't write (it doesn't apply modifiers)."""
    if record.fit == "MANUAL" or record.shape in shapes.MESH_SHAPES or not any(
            modifier.show_viewport and modifier.type != "ARMATURE" for modifier in getattr(record.obj, "modifiers", ())):
        return False
    accessors = [(primitive.attributes or {}).get("POSITION") for primitive in (record.mesh.primitives or ())] if (
        record.mesh is not None) else []
    if record.skin is not None or not accessors or any(not getattr(item, "min", None) for item in accessors):
        return True
    written = [shapes.bounds_from_gltf(item.min, item.max, yup) for item in accessors]
    low = tuple(min(item[0][i] for item in written) for i in range(3))
    high = tuple(max(item[1][i] for item in written) for i in range(3))
    _fit(record)
    return any(abs(a - b) > 1e-4 for a, b in zip(low + high, record.bounds[0] + record.bounds[1]))


def _vector(values, identity):
    values = tuple(float(value) for value in values)
    return None if all(abs(a - b) <= shapes.EPSILON for a, b in zip(values, identity)) else list(values)


def _source(node):
    """The extras that name the node a collider is made for, in Angelo's web live link's frames only (``live``),
    where the tree is keyed (``extras.angelo_uid``), so the page sends it with the object whose shape it is."""
    key = (node.extras or {}).get("angelo_uid") if isinstance(node.extras, dict) else None
    return {"angelo_from": key} if key is not None else None


def _relocate_visual(node, animations):
    """Move a body node's mesh to a new ``<name>Mesh`` child (see the module notes); the child is returned."""
    visual = gltf2_io.Node(camera=None, children=[], extensions=None, extras=None, matrix=None, mesh=node.mesh,
                           name=(node.name or "") + "Mesh", rotation=None, scale=None, skin=node.skin,
                           translation=None, weights=node.weights)
    node.mesh, node.skin, node.weights = None, None, None
    node.children = [visual] + list(node.children or [])
    for animation in animations:
        for channel in animation.channels or ():
            target = channel.target
            if target is not None and target.node is node and target.path == "weights":
                target.node = visual
    return visual


def write_omi(records, tree, animations, yup, log, apply=True, live=False):
    animated = {id(channel.target.node) for animation in animations for channel in animation.channels or ()
                if channel.target is not None and channel.target.path in TRS_PATHS}
    bodies = {}
    for record in records:
        if record.body != "NONE":
            bodies.setdefault(record.obj.name, []).append(record)
    extensions = {}   # id(node) -> (node, OMI_physics_body dict)

    def extension_of(node):
        return extensions.setdefault(id(node), (node, {}))[1]

    for record in records:   # Bodies first: shapes below need the visual moved off their parent.
        if record.body == "NONE":
            continue
        if record.body in shapes.MOTION_TYPES:
            motion = {"type": shapes.MOTION_TYPES[record.body]}
            if record.body == "RIGID":
                motion["mass"] = round(float(record.mass), 6)
            extension_of(record.node)["motion"] = motion
        if record.node.mesh is not None and not record.collision_only:
            _relocate_visual(record.node, animations)
    triggers = {}   # id(trigger body node) -> its shape nodes
    for record in records:
        if record.shape == "NONE":
            if record.collision_only:
                log.warning("Game physics: %s is collision only but has no shape, so it's exported as a visible mesh"
                            % record.obj.name)
            continue
        if record.shape in shapes.MESH_SHAPES and record.mesh is None:
            log.warning("Game physics: %s has no mesh for its %s shape, so it has no collider"
                        % (record.obj.name, record.shape.lower()))
            continue
        owner, path = owner_body(record, bodies, tree)
        role = "trigger" if owner is not None and owner.body == "TRIGGER" else "collider"
        if record.collision_only:
            record.node.mesh = None   # The shape keeps the mesh in the file; no node draws it.
        hoist = None   # The path to compose when the shape node goes directly below the body.
        if owner is not None and owner is not record:
            if path is None:
                log.warning("Game physics: %s isn't below its body %s in this file (is the hierarchy flattened?), so "
                            "engines make it a separate static collider" % (record.obj.name, owner.obj.name))
            elif any(id(node) in animated for node in path):
                log.warning("Game physics: %s is animated inside its body %s, so its shape stays on its own node; "
                            "Godot attaches a shape only to its direct parent body and makes this one separate"
                            % (record.obj.name, owner.obj.name))
            else:
                hoist = path
        if not apply and _unapplied(record, yup):
            log.warning("Game physics: %s's shape is fitted to its modifiers' result, which this export doesn't apply; "
                        "export with Apply Modifiers, or size the shape by hand" % record.obj.name)
        own = _placed(record, None, tree, yup)
        own_node_fits = (record.node.mesh is None and record.node.camera is None and own[:3] == (None, None, None))
        if own_node_fits and (hoist is None or len(hoist) == 1):
            holder, entry = record.node, own[3]   # The object's own node is (or is directly below) its body.
        else:
            parent = owner.node if hoist is not None else record.node
            translation, rotation, scale, entry = _placed(record, _relative(hoist) if hoist else None, tree, yup)
            holder = gltf2_io.Node(camera=None, children=[], extensions=None,
                                   extras=_source(record.node) if live else None,
                                   matrix=None, mesh=None,
                                   name=(record.node.name or "") + ("Trigger" if role == "trigger" else "Collider"),
                                   rotation=rotation, scale=scale, skin=None, translation=translation, weights=None)
            parent.children = list(parent.children or []) + [holder]
        shape = ChildOfRootExtension(name=SHAPE_EXTENSION, path=["shapes"], extension=entry, required=False)
        extension_of(holder)[role] = {"shape": shape}
        if role == "trigger" and owner is not None and holder is not owner.node:
            triggers.setdefault(id(owner.node), []).append(holder)
    for record in records:
        if record.body == "TRIGGER":
            members = triggers.get(id(record.node))
            body = extension_of(record.node)
            if members:
                body.setdefault("trigger", {})["nodes"] = members
            elif "trigger" not in body:
                log.warning("Game physics: the trigger %s has no shape, so engines can't detect anything entering it"
                            % record.obj.name)
    for node, extension in extensions.values():
        if extension:
            node.extensions = dict(node.extensions or {})
            node.extensions[BODY_EXTENSION] = Extension(name=BODY_EXTENSION, extension=extension, required=False)


def write_suffixes(records, log):
    for record in records:
        suffix, note = godot_suffix(record.body, record.shape, record.node.mesh is not None, record.collision_only)
        if note:
            log.warning("Game physics: %s: %s" % (record.obj.name, SUFFIX_NOTES[note]))
        if suffix and not (record.node.name or "").lower().endswith(suffix):
            record.node.name = (record.node.name or "") + suffix
