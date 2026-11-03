# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a texture-set bake read, as one digest: ``export_baked``'s freshness check.

Contract: doc/angelo/long_jobs.rst, "Baked materials at export". The list follows what a ``BAKE_MAPS`` bake reads
(``bake_maps_plan``, ``bake_maps_steps``, Cycles' object bake), so any change that would bake differently makes the
binding stale:

* Every object the bake renders from: the target, or with sources the sources and the target, and the cage. Of each,
  its evaluated mesh (face corners and sizes, corner normals, and every point, corner and face attribute: positions,
  face material indices, sharp faces, custom normals, UV maps, colour attributes and whatever else a node may read;
  edge attributes such as seams and sharp edges reach shading only through the normals, and Blender's internal
  ``.`` attributes such as selection are not shading), its generated texture space and its modifiers' settings: a
  bake evaluates render settings (measured), the digest the viewport's mesh plus every setting.
* The materials whose shading the bake reads (the sources', else the target's): node trees (every computing property,
  the values inside a node, input and output socket values, links, the active output, a script's text or file),
  groups by their own, the displacement method, the images they read (a file's size and time, packed bytes, or the pixels themselves while
  the image has unsaved edits such as image painting) and the view layer's material override.
* Placement, where the result depends on it: the sources and cage relative to the target; any object a node names
  (a Texture Coordinate's object) relative to the target; the target's own placement and object data when a node
  reads world space, the camera or the object (``placed``).
* Ambient occlusion: Cycles' AO reaches every surface a render shows within the world's AO distance (objects hidden
  only in the viewport included, measured), so with an AO map or a tree's non-local AO node the digest holds that
  distance and each object instance whose bounds come within it, relative to the target (its geometry, ray
  visibility and modifier settings). A target moved with nothing in reach and no placement-reading node stays
  current.

Relative placements are rounded to ``QUANTUM`` so moving a group together reads as unchanged within float rounding.
One ``Reader`` serves a whole export: trees, images, shapes and the scene's instances are read once. Main thread.
"""

import hashlib
import os

import bpy
import numpy

# A node's (or modifier's) own properties that only place or show it: they never change what it computes. Only the
# node itself: inside it, the same names compute (a color ramp stop's ``color``, a curve point's ``location``), so a
# nested struct skips only ``NESTED_LOOKS``.
LOOKS = frozenset({"rna_type", "name", "label", "location", "location_absolute", "width", "height", "dimensions",
                   "select", "show_options", "show_preview", "show_texture", "hide", "use_custom_color", "color",
                   "parent", "warning_propagation", "color_tag", "description", "inputs", "outputs",
                   "internal_links", "type", "interface",
                   # A modifier's panel and editing display.
                   "show_expanded", "is_active", "show_in_editmode", "show_on_cage", "is_override_data",
                   "persistent_uid", "execution_time", "use_pin_to_last"})
NESTED_LOOKS = frozenset({"rna_type", "select"})   # A curve point's selection in the editor.
QUANTUM = 1e-4    # Metres (and matrix units) a relative placement is rounded to.
# Shader nodes whose result depends on where the object is, the camera or the object's own data.
PLACED = frozenset({"ShaderNodeNewGeometry", "ShaderNodeObjectInfo", "ShaderNodeVectorTransform",
                    "ShaderNodeCameraData", "ShaderNodeTexPointDensity", "ShaderNodeParticleInfo",
                    "ShaderNodeHairInfo", "ShaderNodePointInfo", "ShaderNodeWireframe"})
VIEWED = frozenset({"Camera", "Window", "Reflection"})   # Texture Coordinate outputs that read the view.
GEOMETRY_TYPES = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "POINTCLOUD"})
RAYS = ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission", "visible_volume_scatter",
        "visible_shadow", "is_holdout")
# Attribute data types: (field, components, numpy type) for foreach_get.
FIELDS = {"FLOAT": ("value", 1, numpy.float32), "INT": ("value", 1, numpy.int32),
          "FLOAT_VECTOR": ("vector", 3, numpy.float32), "FLOAT_COLOR": ("color", 4, numpy.float32),
          "BYTE_COLOR": ("color", 4, numpy.float32), "FLOAT2": ("vector", 2, numpy.float32),
          "BOOLEAN": ("value", 1, bool), "INT8": ("value", 1, numpy.int32),
          "INT16_2D": ("value", 2, numpy.int32), "INT32_2D": ("value", 2, numpy.int32),
          "QUATERNION": ("value", 4, numpy.float32), "FLOAT4X4": ("value", 16, numpy.float32)}


def _text(value):
    return repr(value).encode("utf-8", "surrogatepass")


def _file(path):
    """A file's identity for the digest: its size and time, as for an image's file."""
    try:
        status = os.stat(bpy.path.abspath(path))
        return status.st_size, status.st_mtime_ns
    except OSError:
        return "missing"


def _plain(value):
    """An ID property's value as plain Python (groups and arrays included)."""
    for name in ("to_dict", "to_list"):
        if hasattr(value, name):
            return getattr(value, name)()
    return value


def _bounds(evaluated, matrix):
    """An evaluated object's world-space bounds, (low, high), under ``matrix``."""
    matrix = numpy.asarray(matrix, dtype=numpy.float64)
    corners = numpy.asarray([tuple(corner) for corner in evaluated.bound_box], dtype=numpy.float64)
    world = corners @ matrix[:3, :3].T + matrix[:3, 3]
    return world.min(axis=0), world.max(axis=0)


def quantized(matrix):
    return numpy.round(numpy.asarray(matrix, dtype=numpy.float64) / QUANTUM).astype(numpy.int64).tobytes()


class Notes:
    """What a tree's nodes read besides their own values."""
    __slots__ = ("placed", "reach", "named", "attributes")

    def __init__(self):
        self.placed, self.reach, self.named, self.attributes = False, False, set(), set()

    def add(self, other):
        self.placed |= other.placed
        self.reach |= other.reach
        self.named |= other.named
        self.attributes |= other.attributes


class Reader:
    """Digests for one export (or one bake's start) against one evaluated ``depsgraph``."""

    def __init__(self, depsgraph):
        self.depsgraph = depsgraph
        self.trees, self.images, self.shapes = {}, {}, {}
        self.instances = None   # (keys, digests, matrices, low, high) of the scene's object instances, when needed.

    # -- materials -------------------------------------------------------------------------------------------
    def _value(self, item, depth, notes, nested=False):
        """A stable text for an RNA struct's computing properties (bounded depth; every collection item, since an
        edit to any of them changes the result)."""
        parts = []
        skip = NESTED_LOOKS if nested else LOOKS
        for prop in item.bl_rna.properties:
            name = prop.identifier
            if name in skip or name.startswith("bl_"):
                continue
            try:
                value = getattr(item, name)
            except (AttributeError, RuntimeError, TypeError):
                continue
            kind = prop.type
            if kind == "POINTER":
                if value is None:
                    parts.append("%s=None" % name)
                elif isinstance(value, bpy.types.ID):
                    parts.append("%s=%s" % (name, self._id(value, notes)))
                elif depth > 0:
                    parts.append("%s={%s}" % (name, self._value(value, depth - 1, notes, True)))
            elif kind == "COLLECTION":
                if depth > 0:
                    parts.append("%s=[%s]" % (name, ";".join(self._value(entry, depth - 1, notes, True)
                                                             for entry in value)))
            elif not prop.is_readonly:
                if hasattr(value, "__len__") and not isinstance(value, str):
                    value = tuple(value)
                elif kind == "STRING" and prop.subtype == "FILE_PATH" and value:
                    value = (value, _file(value))   # A script or IES file the node reads: its content counts.
                elif kind == "ENUM" and isinstance(value, str) and ("WORLD" in value or "CAMERA" in value):
                    notes.placed = True   # A node working in world or camera space.
                elif name == "from_instancer" and value:
                    notes.placed = True
                parts.append("%s=%r" % (name, value))
        return ",".join(parts)

    def _id(self, block, notes):
        if isinstance(block, bpy.types.Image):
            return "Image(%r,%r,%r,%r,%s)" % (block.filepath_raw, block.source, block.colorspace_settings.name,
                                              block.alpha_mode, self.image(block))
        if isinstance(block, bpy.types.NodeTree):
            text, inner = self.tree(block)
            notes.add(inner)
            return "Tree(%s)" % text
        if isinstance(block, bpy.types.Text):   # A script node's internal script (or an IES text): its content.
            return "Text(%r,%s)" % (block.name, hashlib.blake2b(block.as_string().encode("utf-8", "surrogatepass"),
                                                                 digest_size=16).hexdigest())
        if isinstance(block, bpy.types.Object):
            notes.named.add(block.name)
        return "%s(%r)" % (type(block).__name__, block.name)

    def tree(self, tree):
        """A node tree's digest and ``Notes``: its nodes' computing properties, socket values and links."""
        key = tree.session_uid
        if key in self.trees:
            return self.trees[key]
        notes = Notes()
        self.trees[key] = ("cycle", notes)   # A group that (wrongly) contains itself ends here.
        parts = []
        for node in sorted(tree.nodes, key=lambda item: item.name):
            kind = node.bl_idname
            if kind in PLACED:
                notes.placed = True
            elif kind == "ShaderNodeTexCoord" and any(out.is_linked for out in node.outputs if out.name in VIEWED):
                notes.placed = True
            elif kind == "ShaderNodeAttribute" and node.attribute_type != "GEOMETRY":
                notes.attributes.add((node.attribute_type, node.attribute_name))
            elif kind == "ShaderNodeAmbientOcclusion" and not node.only_local:
                notes.reach = True
            sockets = []
            # Inputs, and outputs too: an RGB or Value node's value is its output socket's.
            for side, socket in [("in", item) for item in node.inputs] + [("out", item) for item in node.outputs]:
                value = getattr(socket, "default_value", None)
                if hasattr(value, "__len__") and not isinstance(value, str):
                    value = tuple(value)
                sockets.append("%s %s:%r:%d" % (side, socket.identifier, value, socket.is_linked))
            parts.append("%s|%s|%s|%s" % (node.name, kind, self._value(node, 3, notes), ";".join(sockets)))
        links = sorted("%s.%s>%s.%s:%d" % (link.from_node.name, link.from_socket.identifier, link.to_node.name,
                                           link.to_socket.identifier, link.is_muted) for link in tree.links)
        text = hashlib.blake2b(("\n".join(parts) + "\n" + "\n".join(links)).encode("utf-8", "surrogatepass"),
                               digest_size=16).hexdigest()
        self.trees[key] = (text, notes)
        return text, notes

    def image(self, image):
        """What an image's pixels are now: its unsaved pixels while edited, else its packed bytes, file or
        generator settings."""
        key = image.session_uid
        if key not in self.images:
            self.images[key] = self._image(image)
        return self.images[key]

    def _image(self, image):
        hasher = hashlib.blake2b(digest_size=16)
        if image.is_dirty:   # Painted or otherwise edited since it was loaded or saved.
            if image.has_data:
                pixels = numpy.empty(len(image.pixels), dtype=numpy.float32)
                image.pixels.foreach_get(pixels)
                hasher.update(b"pixels" + pixels.tobytes())
            else:
                hasher.update(b"dirty")
        elif image.packed_file is not None:
            hasher.update(b"packed" + bytes(image.packed_file.data))
        elif image.source in {"FILE", "SEQUENCE", "MOVIE", "TILED"}:
            path = bpy.path.abspath(image.filepath_raw, library=image.library)
            paths = ([path.replace("<UDIM>", str(tile.number)) for tile in image.tiles] if image.source == "TILED"
                     else [path])
            for each in paths:
                try:
                    status = os.stat(each)
                    hasher.update(_text((each, status.st_size, status.st_mtime_ns)))
                except OSError:
                    hasher.update(_text((each, "missing")))
            if image.source in {"SEQUENCE", "MOVIE"}:
                hasher.update(_text(self.depsgraph.scene.frame_current))
        else:
            hasher.update(_text((image.generated_type, tuple(image.generated_color), image.generated_width,
                                 image.generated_height, image.use_generated_float)))
        return hasher.hexdigest()

    # -- geometry --------------------------------------------------------------------------------------------
    def mesh(self, obj, hasher, full):
        """Hash an evaluated object's geometry: ``full`` every attribute and normal (the objects a bake renders
        from), else the surface AO rays hit (positions and faces)."""
        evaluated = obj if obj.is_evaluated else obj.evaluated_get(self.depsgraph)
        mesh, temporary = evaluated.data, False
        if not isinstance(mesh, bpy.types.Mesh):
            try:
                mesh, temporary = evaluated.to_mesh(), True
            except RuntimeError:
                hasher.update(_text(("no surface", obj.name)))
                return
        try:
            self._mesh(mesh, hasher, full)
        finally:
            if temporary:
                evaluated.to_mesh_clear()

    def _mesh(self, mesh, hasher, full):
        counts = (len(mesh.vertices), len(mesh.edges), len(mesh.loops), len(mesh.polygons))
        hasher.update(_text(counts))
        corners = numpy.empty(counts[2], dtype=numpy.int32)
        mesh.loops.foreach_get("vertex_index", corners)
        starts = numpy.empty(counts[3], dtype=numpy.int32)
        mesh.polygons.foreach_get("loop_start", starts)
        hasher.update(corners.tobytes())
        hasher.update(starts.tobytes())
        if not full:
            positions = numpy.empty(counts[0] * 3, dtype=numpy.float32)
            mesh.vertices.foreach_get("co", positions)
            hasher.update(positions.tobytes())
            return
        for attribute in sorted(mesh.attributes, key=lambda item: item.name):
            # Shaders read point, corner and face attributes; edges (seams, sharp edges) reach shading only
            # through the corner normals hashed below.
            if attribute.name.startswith(".") or attribute.domain == "EDGE":
                continue
            hasher.update(_text((attribute.name, attribute.domain, attribute.data_type)))
            field = FIELDS.get(attribute.data_type)
            if field is None:   # A string attribute: no shader reads it, but its values are still the mesh.
                hasher.update(_text([tuple(getattr(item, "value", ())) for item in attribute.data]))
                continue
            name, size, kind = field
            values = numpy.empty(len(attribute.data) * size, dtype=kind)
            try:
                attribute.data.foreach_get(name, values)
            except (TypeError, RuntimeError, AttributeError):   # A layout this table doesn't know: item by item.
                values = [tuple(value) if hasattr(value, "__len__") else value
                          for value in (getattr(item, name, None) for item in attribute.data)]
                hasher.update(_text(values))
                continue
            hasher.update(values.tobytes())
        normals = numpy.empty(counts[2] * 3, dtype=numpy.float32)
        mesh.corner_normals.foreach_get("vector", normals)
        hasher.update(normals.tobytes())
        hasher.update(_text((mesh.uv_layers.active.name if mesh.uv_layers.active else None,
                             tuple(mesh.texspace_location), tuple(mesh.texspace_size))))

    # -- the digest ------------------------------------------------------------------------------------------
    def digest(self, target, sources=(), cage=None, ao=False):
        """What a bake of ``target`` (from ``sources``, through ``cage``, with an AO map when ``ao``) read now."""
        return self.digests(target, sources, cage, ao)[0]

    def digests(self, target, sources=(), cage=None, ao=False):
        """``digest`` and the same without the AO reach: ``(all, own)``. When only ``all`` changed, what changed is
        geometry near the target, which its baked AO saw."""
        hasher = hashlib.blake2b(digest_size=16)
        inverse = numpy.linalg.pinv(numpy.asarray(target.matrix_world, dtype=numpy.float64))   # Zero scale too.
        readers = [target, *sources] + ([cage] if cage is not None else [])
        notes = Notes()
        for index, obj in enumerate(readers):
            if obj is None:   # A source or cage deleted since the bake.
                hasher.update(b"|gone")
                continue
            hasher.update(_text(("|object", index)))
            self.mesh(obj, hasher, True)
            # Its modifiers' settings, render-only ones too (a bake evaluates them). Their own notes are dropped:
            # what an armature or a hook's object does is already in the evaluated mesh.
            for modifier in obj.modifiers:
                hasher.update(_text(("|modifier", modifier.type, self._value(modifier, 1, Notes()))))
            if index:
                hasher.update(quantized(inverse @ numpy.asarray(obj.matrix_world, dtype=numpy.float64)))
        shaded = [obj for obj in (sources or [target]) if obj is not None]
        for index, obj in enumerate(shaded):
            for slot in obj.material_slots:
                material = slot.material
                tree = None if material is None or not material.use_nodes else material.node_tree
                text = ""
                if tree is not None:
                    text, inner = self.tree(tree)
                    notes.add(inner)
                hasher.update(_text(("|slot", index, None if material is None else material.displacement_method,
                                     text)))
        override = self.depsgraph.view_layer.material_override
        hasher.update(_text(("|override", None if override is None else override.name)))
        if override is not None and override.node_tree is not None:
            text, inner = self.tree(override.node_tree)
            notes.add(inner)
            hasher.update(text.encode())
        self._placement(hasher, target, inverse, notes, shaded)
        own = hasher.copy().hexdigest()
        if ao or notes.reach:
            self._reach(hasher, [obj for obj in readers if obj is not None], inverse)
        return hasher.hexdigest(), own

    def _placement(self, hasher, target, inverse, notes, shaded):
        for name in sorted(notes.named):
            other = bpy.data.objects.get(name)
            hasher.update(_text(("|named", name)))
            if other is not None:
                hasher.update(quantized(inverse @ numpy.asarray(other.matrix_world, dtype=numpy.float64)))
        if notes.placed:
            camera = self.depsgraph.scene.camera
            hasher.update(_text(("|placed", target.name, tuple(target.color), target.pass_index)))
            hasher.update(quantized(target.matrix_world))
            if camera is not None:
                hasher.update(quantized(camera.matrix_world))
                hasher.update(_text(self._value(camera.data, 0, Notes())))
        for kind, name in sorted(notes.attributes):
            owners = ((self.depsgraph.view_layer, self.depsgraph.scene, self.depsgraph.scene.world)
                      if kind == "VIEW_LAYER" else [owner for obj in shaded for owner in (obj, obj.data)])
            found = []
            for owner in owners:
                if owner is None:
                    continue
                found.append(_plain(owner.get(name)))
                try:
                    found.append(_plain(owner.path_resolve(name)))
                except (ValueError, TypeError):
                    found.append(None)
            hasher.update(_text(("|attribute", kind, name, found)))

    def _rendered(self):
        """The view layer's objects a render shows (``hide_render`` off, in no excluded or render-disabled
        collection), hidden in the viewport or not: a bake's AO sees them all (measured)."""
        found, todo = set(), [self.depsgraph.view_layer.layer_collection]
        while todo:
            layer = todo.pop()
            if layer.exclude or layer.collection.hide_render:
                continue
            found.update(obj.session_uid for obj in layer.collection.objects if not obj.hide_render)
            todo += list(layer.children)
        return found

    def _shape(self, obj, original):
        """An evaluated object's surface, its ray visibility and its modifiers' settings (a render evaluates
        render-only modifiers the viewport skips)."""
        data = obj.data
        key = (original.session_uid, data.name if data is not None else "", data.session_uid if data else 0,
               obj.is_evaluated)
        if key not in self.shapes:
            shape = hashlib.blake2b(digest_size=16)
            self.mesh(obj, shape, False)
            shape.update(_text(tuple(getattr(original, name, None) for name in RAYS)))
            for modifier in original.modifiers:
                shape.update(_text((modifier.type, self._value(modifier, 0, Notes()))))
            self.shapes[key] = shape.hexdigest()
        return self.shapes[key]

    def _scene(self):
        """Every geometry instance a render shows: owners (original object, is an instance), shape digests,
        world matrices and world bounds. The viewport's evaluated instances, plus rendered objects hidden in the
        viewport (their own mesh: the viewport evaluates no modifiers for them)."""
        rendered = self._rendered()
        owners, digests, matrices, low, high = [], [], [], [], []

        def add(obj, original, matrix, instanced):
            if obj.type not in GEOMETRY_TYPES:
                return
            matrix = numpy.asarray(matrix, dtype=numpy.float64)
            world_low, world_high = _bounds(obj, matrix)
            owners.append((original.session_uid, instanced))
            digests.append(self._shape(obj, original))
            matrices.append(matrix)
            low.append(world_low)
            high.append(world_high)

        seen = set()
        for instance in self.depsgraph.object_instances:
            original = instance.object.original
            owner = instance.parent.original if instance.is_instance and instance.parent else original
            if owner.session_uid not in rendered:
                continue
            seen.add(original.session_uid)
            add(instance.object, original, instance.matrix_world, instance.is_instance)
        for obj in self.depsgraph.scene.objects:
            if obj.session_uid in rendered and obj.session_uid not in seen:
                add(obj, obj, obj.matrix_world, False)
        return (owners, digests, numpy.asarray(matrices).reshape(-1, 4, 4), numpy.asarray(low).reshape(-1, 3),
                numpy.asarray(high).reshape(-1, 3))

    def _reach(self, hasher, readers, inverse):
        """The AO distance and every other instance whose bounds come within it of the readers' bounds: its shape
        and its placement relative to the target."""
        world = self.depsgraph.scene.world
        distance = float(world.light_settings.distance) if world is not None else 10.0
        hasher.update(_text(("|reach", distance)))
        if self.instances is None:
            self.instances = self._scene()
        owners, digests, matrices, low, high = self.instances
        if not owners:
            return
        own = {obj.session_uid for obj in readers}
        near = numpy.zeros(len(owners), dtype=bool)
        for obj in readers:   # Its own bounds, so a reader hidden in the viewport reaches the same neighbours.
            reader_low, reader_high = _bounds(obj.evaluated_get(self.depsgraph), obj.matrix_world)
            near |= numpy.all((low <= reader_high + distance) & (high >= reader_low - distance), axis=1)
        near &= ~numpy.asarray([uid in own and not instanced for uid, instanced in owners])
        relative = numpy.round(numpy.einsum("ij,njk->nik", inverse, matrices[near]) / QUANTUM).astype(numpy.int64)
        # Shapes and where they are, in no particular order: renaming or reordering objects reads as unchanged.
        for item in sorted(digests[index].encode() + matrix.tobytes()
                           for index, matrix in zip(numpy.flatnonzero(near), relative)):
            hasher.update(item)
