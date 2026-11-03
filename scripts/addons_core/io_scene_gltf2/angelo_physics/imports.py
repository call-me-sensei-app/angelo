# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: Apache-2.0
"""Reading OMI_physics_body and OMI_physics_shape (and OMI_collider) back into ``Object.angelo_physics`` on import.

A file without ``OMI_physics_body`` among its extensions costs one list lookup. Otherwise every node becomes the
object it always did, with its physics read into its settings, and two of the exporter's rearrangements
(``export``) fold back so a round trip gives the person the objects they had:

* **A visual child.** A node with physics and no mesh (Godot drops the mesh of any node with physics) whose only
  mesh child is unnamed, named like it or ``<name>Mesh``, untransformed, unanimated, without children, skin, extras
  or physics of its own, becomes one mesh object with it.
* **A mesh shape of its parent's mesh.** A node with only a convex or mesh shape of the mesh its parent draws (after
  the fold above), untransformed and without children, becomes the parent's shape.

Any other node keeps its place: a primitive shape node becomes an empty with that shape at its own size (Manual), and
a convex or mesh shape of a mesh no node draws becomes a collision-only mesh object, shown as a wireframe and kept
out of renders. Motion types and mass are read; velocities, inertia, physics materials and collision filters have no
Angelo setting and are not kept.
"""

from . import shapes

BODY_EXTENSION = "OMI_physics_body"
TRS_PATHS = frozenset({"translation", "rotation", "scale"})
LEGACY_TYPES = {"rigid": "RIGID", "vehicle": "RIGID", "character": "KINEMATIC", "trigger": "TRIGGER"}


def _physics(pynode):
    extensions = pynode.extensions or {}
    value = extensions.get(BODY_EXTENSION)
    return value if isinstance(value, dict) else {}


def _legacy_collider(pynode):
    value = (pynode.extensions or {}).get("OMI_collider")
    return value if isinstance(value, dict) else None


def _is_body(physics):
    trigger = physics.get("trigger")
    return "motion" in physics or "type" in physics or (isinstance(trigger, dict) and "shape" not in trigger)


def _body(physics):
    """The body type a node's OMI_physics_body gives it, or None. OMI's earlier node form had a flat ``type``
    (``rigid``, ``character``...), which Godot still reads."""
    motion = physics.get("motion")
    kind = motion.get("type") if isinstance(motion, dict) else physics.get("type")
    if kind is not None:
        return shapes.BODY_FROM_MOTION.get(kind, LEGACY_TYPES.get(kind, "STATIC"))
    return "TRIGGER" if _is_body(physics) else None


def _untransformed(pynode):
    return pynode.matrix in (None, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]) and shapes.is_identity(
        pynode.translation, pynode.rotation, pynode.scale)


class PhysicsImportExtension:
    """The importer's user extension for game physics (added by ``ImportGLTF2`` to every import)."""

    def __init__(self):
        self.active = False
        self.shapes = []
        self.visual_folds = {}   # body node index -> its visual child's index
        self.shape_folds = {}    # node index -> the child index whose mesh shape it takes
        self.collision_only = set()
        self.indices = {}

    def gather_import_gltf_before_hook(self, gltf):
        self.__init__()   # One extension serves every file of a multi-file import.
        data = gltf.data
        used = data.extensions_used or ()
        self.active = BODY_EXTENSION in used or "OMI_collider" in used
        if not self.active:
            return
        root = (data.extensions or {}).get("OMI_physics_shape") or (data.extensions or {}).get("OMI_collider") or {}
        self.shapes = list(root.get("shapes") or root.get("colliders") or [])
        nodes = data.nodes or []
        self.indices = {id(node): index for index, node in enumerate(nodes)}
        animated = {channel.target.node for animation in data.animations or () for channel in animation.channels or ()
                    if channel.target is not None and channel.target.path in TRS_PATHS}
        parents = {child: index for index, node in enumerate(nodes) for child in node.children or ()}
        for index, node in enumerate(nodes):
            if node.mesh is None and node.camera is None and _physics(node):
                visual = self._visual_child(node, index, nodes, animated)
                if visual is not None:
                    self.visual_folds[index] = visual
        for index, node in enumerate(nodes):
            mesh = self._mesh_shape(node)
            if mesh is None or node.mesh is not None:
                continue
            parent = parents.get(index)
            parent_mesh = None
            if parent is not None:
                parent_mesh = nodes[parent].mesh if parent not in self.visual_folds else nodes[
                    self.visual_folds[parent]].mesh
            if (parent_mesh == mesh and parent not in self.shape_folds and not node.children and index not in animated
                    and _untransformed(node) and not _is_body(_physics(node))):
                self.shape_folds[parent] = index
            else:
                node.mesh = mesh   # The importer makes it a mesh object; its settings say collision only.
                self.collision_only.add(index)

    def _visual_child(self, body, index, nodes, animated):
        meshes = [child for child in body.children or () if nodes[child].mesh is not None]
        if len(meshes) != 1:
            return None
        child = nodes[meshes[0]]
        names = (None, "", body.name, (body.name or "") + "Mesh")
        if (child.name in names and not child.children and child.skin is None and not child.extras
                and not child.extensions and child.camera is None and meshes[0] not in animated
                and _untransformed(child)):
            return meshes[0]
        return None

    def _shape_entry(self, physics_value):
        if not isinstance(physics_value, dict):
            return None
        index = physics_value.get("shape", -1)
        return self.shapes[index] if isinstance(index, int) and 0 <= index < len(self.shapes) else None

    def _mesh_shape(self, node):
        physics = _physics(node)
        for role in ("collider", "trigger"):
            parsed = shapes.read_shape(self._shape_entry(physics.get(role)))
            if parsed is not None and parsed[0] in shapes.MESH_SHAPES:
                return parsed[4]
        return None

    def gather_import_node_before_hook(self, vnode, gltf_node, gltf):
        if not self.active or gltf_node is None:
            return
        index = self.indices.get(id(gltf_node))
        for folds in (self.visual_folds, self.shape_folds):
            child = folds.get(index)
            if child is None or child not in vnode.children or child not in gltf.vnodes:
                continue
            if folds is self.visual_folds:
                vnode.mesh_node_idx = child
            vnode.children.remove(child)
            del gltf.vnodes[child]

    def gather_import_node_after_hook(self, vnode, gltf_node, obj, gltf):
        if not self.active or gltf_node is None or obj is None:
            return
        index = self.indices.get(id(gltf_node))
        physics = _physics(gltf_node)
        shape_source = _physics(gltf.data.nodes[self.shape_folds[index]]) if index in self.shape_folds else physics
        role, entry = self._shape(shape_source, _legacy_collider(gltf_node))
        body = _body(physics)
        if body is None and entry is None:
            return
        settings = obj.angelo_physics
        if body is not None:
            settings.body = body
            motion = physics.get("motion") if isinstance(physics.get("motion"), dict) else physics
            if body == "RIGID":
                settings.mass = max(0.001, float(motion.get("mass", 1.0)))
        parsed = shapes.read_shape(entry)
        if parsed is None:
            return
        kind, size, radius, height, _mesh = parsed
        settings.shape = kind
        if settings.body == "NONE":
            ancestor = self._ancestor_body(vnode, gltf)
            if role == "trigger" and ancestor != "TRIGGER":
                settings.body = "TRIGGER"   # A trigger shape of its own: an area.
            elif role == "collider" and ancestor == "TRIGGER":
                settings.body = "STATIC"    # A solid shape inside an area is its own body, as Godot makes it.
        if kind in shapes.PRIMITIVES:
            settings.fit = "MANUAL"
            settings.size = shapes.swap_size(size, True)
            settings.radius, settings.height = radius, height
            settings.axis = "Z"   # A glTF node's +Y is the Blender object's +Z.
        if index in self.collision_only:
            settings.collision_only = True
            obj.display_type = 'WIRE'
            obj.hide_render = True

    def _shape(self, physics, legacy):
        """(role, shape entry) of a node: its OMI_physics_body collider or trigger, else its OMI_collider."""
        for role in ("collider", "trigger"):
            entry = self._shape_entry(physics.get(role))
            if entry is not None:
                return role, entry
        if legacy is not None:
            index = legacy.get("collider")
            entry = self.shapes[index] if isinstance(index, int) and 0 <= index < len(self.shapes) else legacy
            return ("trigger" if entry.get("isTrigger") else "collider"), entry
        return None, None

    def _ancestor_body(self, vnode, gltf):
        parent = vnode.parent
        while isinstance(parent, int):
            body = _body(_physics(gltf.data.nodes[parent]))
            if body is not None:
                return body
            parent = gltf.vnodes[parent].parent if parent in gltf.vnodes else None
        return None
