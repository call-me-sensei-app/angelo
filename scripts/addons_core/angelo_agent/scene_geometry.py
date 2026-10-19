# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact bounded original/live-edit mesh fingerprints and topology/UV metadata."""

from array import array
import hashlib
from itertools import islice
from .scene_read_values import BudgetExceeded, SceneQueryError, all_finite, count_nonzero, dump, label


# (domain, element property) -> (attribute, data type, attribute property,
# value when the attribute is absent or None if it must exist, inverted flag).
MESH_ATTRIBUTE_SOURCES = {
    ("POINT", "co"): ("position", "FLOAT_VECTOR", "vector", None, False),
    ("EDGE", "vertices"): (".edge_verts", "INT32_2D", "value", None, False),
    ("CORNER", "vertex_index"): (".corner_vert", "INT", "value", None, False),
    ("CORNER", "edge_index"): (".corner_edge", "INT", "value", None, False),
    ("POINT", "select"): (".select_vert", "BOOLEAN", "value", 0, False),
    ("EDGE", "select"): (".select_edge", "BOOLEAN", "value", 0, False),
    ("FACE", "select"): (".select_poly", "BOOLEAN", "value", 0, False),
    ("POINT", "hide"): (".hide_vert", "BOOLEAN", "value", 0, False),
    ("EDGE", "hide"): (".hide_edge", "BOOLEAN", "value", 0, False),
    ("FACE", "hide"): (".hide_poly", "BOOLEAN", "value", 0, False),
    ("FACE", "material_index"): ("material_index", "INT", "value", 0, False),
    # MeshPolygon.use_smooth is the inverse of sharp_face; absent means smooth.
    ("FACE", "use_smooth"): ("sharp_face", "BOOLEAN", "value", 1, True),
}
_INVERT_FLAGS = bytes([1, 0]) + bytes(range(2, 256))


def _attribute_source(mesh, domain, prop, count, width):
    """(source spec, attribute or None for a documented default), or None."""
    source = MESH_ATTRIBUTE_SOURCES.get((domain, prop))
    if source is None:
        return None
    attribute = mesh.attributes.get(source[0])
    if attribute is None:
        return (source, None) if source[3] is not None else None
    if attribute.data_type == source[1] and attribute.domain == domain and len(attribute.data) * width == count:
        return source, attribute
    return None


def mesh_array(mesh, domain, collection, prop, code, width):
    """An element property's values and whether one C copy produced them.

    Values come from the property's contiguous attribute. Element RNA
    (``MeshVertex.select`` and so on) costs a getter per element; the attribute
    arrays in MESH_ATTRIBUTE_SOURCES hold the same values and copy in one call,
    so hashes of the returned bytes are unchanged. A missing flag/material
    attribute means its documented default; any other missing or unexpected
    layout falls back to the exact element read.
    """
    count = len(collection) * width
    found = _attribute_source(mesh, domain, prop, count, width)
    if found is not None:
        source, attribute = found
        if attribute is None:
            return array(code, [source[3]]) * count, True
        values = array(code, [0]) * count
        try:
            if count:
                attribute.data.foreach_get(source[2], values)
            return (array(code, bytes(values).translate(_INVERT_FLAGS)) if source[4] else values), True
        except (AttributeError, TypeError, RuntimeError):
            pass  # An unexpected accessor keeps the exact element read below.
    values = array(code, [0]) * count
    if count:
        try:
            collection.foreach_get(prop, values)
        except (AttributeError, TypeError, RuntimeError):
            raise BudgetExceeded("Native attribute API is unsupported by this bounded reader") from None
    return values, False


class GeometryReader:
    """Read original/live-edit meshes within caller-owned budgets.

    Metric counters belong to scene_queries; no meshes/pointers are retained.
    """
    def __init__(self, metadata, counters, max_list):
        self.metadata = metadata
        self.counters = counters
        self.max_list = max_list

    def read(self, collection, prop, code, width, work, digest=None, packed=False):
        """``packed`` marks a contiguous Attribute.data copy, charged by bytes."""
        count = len(collection) * width
        work.take(packed=count * array(code).itemsize) if packed else work.take(elements=count)
        values = array(code, [0]) * count
        if count:
            try:
                collection.foreach_get(prop, values)
            except (AttributeError, TypeError, RuntimeError):
                raise BudgetExceeded("Native attribute API is unsupported by this bounded reader") from None
        if code == "f" and not all_finite(values):
            raise SceneQueryError("Non-finite original geometry cannot provide a trustworthy revision")
        if digest is not None:
            digest.update(prop.encode("ascii"))
            digest.update(values)
            self.counters["fingerprint_bytes"] += len(values) * values.itemsize
        return values

    def read_mesh_array(self, mesh, domain, collection, prop, code, width, work, digest=None):
        """Like read(), but from the contiguous attribute backing the property."""
        count = len(collection) * width
        planned = _attribute_source(mesh, domain, prop, count, width) is not None
        # Charge the planned path before any copy: one C copy costs bytes, the
        # element-getter fallback costs per element.
        work.take(packed=count * array(code).itemsize) if planned else work.take(elements=count)
        values, packed = mesh_array(mesh, domain, collection, prop, code, width)
        if planned and not packed:
            work.take(elements=count)  # An unexpected accessor fell back to elements.
        if code == "f" and not all_finite(values):
            raise SceneQueryError("Non-finite original geometry cannot provide a trustworthy revision")
        if digest is not None:
            digest.update(prop.encode("ascii"))
            digest.update(values)
            self.counters["fingerprint_bytes"] += len(values) * values.itemsize
        return values

    def mesh(self, obj, work, details=False, output=None):
        """Original geometry, or live BMesh in Edit Mode; no evaluated mesh export."""
        if obj.type != "MESH":
            return None
        mesh = obj.data
        digest = hashlib.blake2b(digest_size=32)
        selection = {}
        uv_layers = []
        limitations = []
        weights = hashlib.blake2b(digest_size=32)
        if obj.mode == "EDIT":
            import bmesh
            bm = bmesh.from_edit_mesh(mesh)
            count = (len(bm.verts), len(bm.edges), len(bm.faces), sum(len(f.loops) for f in bm.faces))
            work.take(elements=sum(count) * 4)
            indices = {v: i for i, v in enumerate(bm.verts)}
            positions = array("f", (v for vertex in bm.verts for v in vertex.co))
            edge_vertices = array("i", (indices[v] for e in bm.edges for v in e.verts))
            polygon_sizes = array("i", (len(f.verts) for f in bm.faces))
            edge_indices = {e: i for i, e in enumerate(bm.edges)}
            corner_edges = array("i", (edge_indices[l.edge] for f in bm.faces for l in f.loops))
            for field, collection in (("vertices", bm.verts), ("edges", bm.edges), ("faces", bm.faces)):
                selection[field] = {"count": sum(bool(v.select) for v in collection),
                                    "indices": [i for i, v in enumerate(collection) if v.select] if details else []}
                digest.update(array("b", (int(v.select) | (int(v.hide) << 1) for v in collection)))
            for f in bm.faces:
                digest.update(array("i", [f.material_index]))
                digest.update(array("i", [indices[v] for v in f.verts]))
            # Native Mesh attributes are not synchronized while editing. These
            # flags live on BMesh and must be read even before update_edit_mesh.
            work.take(elements=count[2] + count[1] * 2)
            digest.update(b"live_shading")
            digest.update(array("b", (int(f.smooth) for f in bm.faces)))
            digest.update(array("b", (int(e.smooth) | (int(e.seam) << 1) for e in bm.edges)))
            deform = bm.verts.layers.deform.active
            if deform:
                for index, vertex in enumerate(bm.verts):
                    for group, weight in sorted(vertex[deform].items()):
                        work.take(elements=3)
                        weights.update(array("i", [index, group])); weights.update(array("f", [weight]))
            for name in bm.loops.layers.uv.keys():
                layer = bm.loops.layers.uv[name]
                work.take(elements=count[3] * 5)
                values = array("f", (v for face in bm.faces for loop in face.loops for v in loop[layer].uv))
                digest.update(name.encode("utf-8")); digest.update(values)
                flags = array("b", (int(loop.uv_select_vert if hasattr(loop, "uv_select_vert") else loop[layer].select)
                                    | (int(loop.uv_select_edge if hasattr(loop, "uv_select_edge") else loop[layer].select_edge) << 1) | (int(loop[layer].pin_uv) << 2)
                                    for face in bm.faces for loop in face.loops))
                digest.update(flags)
                uv_layers.append({"name": label(name), "loop_count": count[3],
                                  "selected": sum(bool(v & 1) for v in flags), "pinned": sum(bool(v & 4) for v in flags)})
            # Scalar named built-in sculpt/selection attributes only; no user props.
            for name, domain, code in ((".sculpt_mask", bm.verts, "f"), (".sculpt_face_set", bm.faces, "i")):
                layers = domain.layers.float if code == "f" else domain.layers.int
                layer = layers.get(name)
                if layer:
                    digest.update(name.encode("ascii")); digest.update(array(code, (v[layer] for v in domain)))
            # Base shape-key buffers/custom attributes do not describe every live
            # edit BMesh layer. Inspection is available, authorization is not guessed.
            if mesh.shape_keys:
                limitations.append("live_edit_shape_keys")
            admitted_attrs = {"position", ".edge_verts", ".corner_vert", ".corner_edge", ".select_vert", ".select_edge", ".select_poly",
                              ".hide_vert", ".hide_edge", ".hide_poly", "material_index", "sharp_face", "sharp_edge", "uv_seam", ".sculpt_mask", ".sculpt_face_set",
                              ".uv_select_vert", ".uv_select_edge", ".uv_select_face"}
            if any(a.name not in admitted_attrs and a.name not in bm.loops.layers.uv.keys() and not a.name.startswith(".pn.") for a in mesh.attributes):
                limitations.append("live_edit_custom_mesh_layers")
        else:
            count = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops))
            positions = self.read_mesh_array(mesh, "POINT", mesh.vertices, "co", "f", 3, work, digest)
            edge_vertices = self.read_mesh_array(mesh, "EDGE", mesh.edges, "vertices", "i", 2, work, digest)
            self.read_mesh_array(mesh, "CORNER", mesh.loops, "vertex_index", "i", 1, work, digest)
            corner_edges = self.read_mesh_array(mesh, "CORNER", mesh.loops, "edge_index", "i", 1, work, digest)
            polygon_sizes = self.read(mesh.polygons, "loop_total", "i", 1, work, digest)
            for field, domain, collection in (("vertices", "POINT", mesh.vertices), ("edges", "EDGE", mesh.edges),
                                              ("faces", "FACE", mesh.polygons)):
                selected = self.read_mesh_array(mesh, domain, collection, "select", "b", 1, work, digest)
                self.read_mesh_array(mesh, domain, collection, "hide", "b", 1, work, digest)
                selection[field] = {"count": count_nonzero(selected),
                                    "indices": [i for i, v in enumerate(selected) if v] if details else []}
            self.read_mesh_array(mesh, "FACE", mesh.polygons, "material_index", "i", 1, work, digest)
            self.read_mesh_array(mesh, "FACE", mesh.polygons, "use_smooth", "b", 1, work, digest)
            native = getattr(mesh, "angelo_vertex_group_weights", None)
            if isinstance(native, bytes):
                # Angelo's native bulk read: exactly the (vertex, group, weight)
                # records the per-vertex walk below hashes, in the same order.
                if len(native) % 12:
                    raise BudgetExceeded("Vertex-group weights exceed the native bulk read")
                work.take(packed=len(native))
                weights.update(native)
            else:
                for vertex in mesh.vertices:
                    for group in vertex.groups:
                        work.take(elements=3)
                        weights.update(array("i", [vertex.index, group.group])); weights.update(array("f", [group.weight]))
            for layer in mesh.uv_layers:
                digest.update(layer.name.encode("utf-8"))
                if hasattr(layer, "uv"):
                    self.read(layer.uv, "vector", "f", 2, work, digest)
                    pins = self.read(layer.pin, "value", "b", 1, work, digest)
                    attribute = mesh.attributes.get(".uv_select_vert")
                    selected = self.read(attribute.data, "value", "b", 1, work, digest) if attribute else []
                else:
                    self.read(layer.data, "uv", "f", 2, work, digest)
                    pins = self.read(layer.data, "pin_uv", "b", 1, work, digest)
                    selected = self.read(layer.data, "select", "b", 1, work, digest)
                    self.read(layer.data, "select_edge", "b", 1, work, digest)
                uv_layers.append({"name": label(layer.name), "loop_count": len(layer.data),
                                  "active": layer.name == mesh.uv_layers.active.name if mesh.uv_layers.active else False,
                                  "active_render": bool(layer.active_render), "selected": count_nonzero(selected),
                                  "pinned": count_nonzero(pins)})
            captured_attrs = {"position", ".edge_verts", ".corner_vert", ".corner_edge", ".select_vert", ".select_edge", ".select_poly",
                              ".hide_vert", ".hide_edge", ".hide_poly", "material_index", "sharp_face", *[l.name for l in mesh.uv_layers]}
            attributes = []
            for attribute in mesh.attributes:
                work.take(items=1)
                name = attribute.name
                attributes.append({"name": label(name), "domain": attribute.domain, "type": attribute.data_type, "count": len(attribute.data)})
                if name in captured_attrs or name.startswith(".pn."):
                    continue
                digest.update(dump([name, attribute.domain, attribute.data_type]).encode("utf-8"))
                properties = {"FLOAT": ("value", "f", 1), "INT": ("value", "i", 1), "INT8": ("value", "b", 1),
                              "BOOLEAN": ("value", "b", 1), "FLOAT_VECTOR": ("vector", "f", 3), "FLOAT2": ("vector", "f", 2),
                              "FLOAT_COLOR": ("color", "f", 4), "BYTE_COLOR": ("color", "f", 4), "QUATERNION": ("value", "f", 4),
                              "INT32_2D": ("value", "i", 2), "FLOAT4X4": ("value", "f", 16)}
                spec = properties.get(attribute.data_type)
                if spec:
                    self.read(attribute.data, *spec, work, digest, packed=True)
                else:
                    limitations.append("unread_mesh_attribute_type:" + attribute.data_type)
            if mesh.shape_keys:
                for key in mesh.shape_keys.key_blocks:
                    work.take(items=1)
                    digest.update(dump(self.metadata.settings(key, ("name", "value", "mute", "slider_min", "slider_max", "vertex_group", "interpolation"))).encode("utf-8"))
                    digest.update(label(key.relative_key.name).encode("utf-8") if key.relative_key else b"")
                    self.read(key.data, "co", "f", 3, work, digest)
        # Also covers live Edit Mode positions, which come from BMesh, not read().
        if not all_finite(positions):
            raise SceneQueryError("Non-finite original geometry cannot provide a trustworthy revision")
        digest.update(positions); digest.update(edge_vertices); digest.update(polygon_sizes)
        digest.update(str(count).encode("ascii")); digest.update(weights.digest())
        digest.update(dump(uv_layers).encode("utf-8"))
        digest.update(dump(self.metadata.settings(obj, ("active_shape_key_index", "use_shape_key_edit_mode", "show_only_shape_key"))).encode("utf-8"))
        result = {"source": "LIVE_EDIT_MESH" if obj.mode == "EDIT" else "ORIGINAL_MESH",
                  "vertices": count[0], "edges": count[1], "faces": count[2], "corners": count[3],
                  "geometry_revision": digest.hexdigest(), "selection": selection,
                  "uv_layers": uv_layers, "shape_key_count": len(mesh.shape_keys.key_blocks) if mesh.shape_keys else 0,
                  "revision_limitations": limitations}
        if obj.mode != "EDIT":
            result["attributes"] = attributes
        if details:
            for field, selected in selection.items():
                selected["indices"] = output.listing(selected["indices"], "mesh.selected_" + field)
            # Exactly counted base topology; no evaluation of modifier geometry.
            uses = [0] * count[1]
            for index in corner_edges:
                if 0 <= index < count[1]:
                    uses[index] += 1
            parents = list(range(count[0]))
            vertex_uses = [0] * count[0]

            def find(i):
                while parents[i] != i:
                    parents[i] = parents[parents[i]]
                    i = parents[i]
                return i

            for i in range(0, len(edge_vertices), 2):
                a, b = edge_vertices[i:i+2]
                if 0 <= a < count[0] and 0 <= b < count[0]:
                    vertex_uses[a] += 1; vertex_uses[b] += 1
                    parents[find(a)] = find(b)
            result["topology"] = {"triangles": sum(n == 3 for n in polygon_sizes), "quads": sum(n == 4 for n in polygon_sizes),
                                  "ngons": sum(n > 4 for n in polygon_sizes), "triangulated_faces": sum(max(0, n-2) for n in polygon_sizes),
                                  "loose_edges": sum(n == 0 for n in uses), "boundary_edges": sum(n == 1 for n in uses),
                                  "manifold_edges": sum(n == 2 for n in uses), "nonmanifold_edges": sum(n > 2 for n in uses),
                                  "isolated_vertices": sum(n == 0 for n in vertex_uses),
                                  "connected_components": len({find(i) for i in range(count[0])}),
                                  "euler_characteristic": count[0]-count[1]+count[2]}
            if count[0]:
                result["local_bounds"] = [[min(positions[j::3]) for j in range(3)], [max(positions[j::3]) for j in range(3)]]
            else:
                result["local_bounds"] = None
        return result

    def uv_metadata(self, obj, work):
        """Useful layer metadata even when coordinates exceed the mesh budget."""
        if obj.type != "MESH":
            return []
        result = []
        for layer in islice(obj.data.uv_layers, self.max_list):
            work.take(items=1)
            item = {"name": label(layer.name), "active": bool(layer.active), "active_render": bool(layer.active_render),
                    "source": "ORIGINAL_LAYER_METADATA", "loop_count": len(layer.uv) if hasattr(layer, "uv") else len(layer.data),
                    "selection_counts_available": False}
            if obj.mode == "EDIT":
                item.update(source="LIVE_EDIT_COUNTS_UNAVAILABLE", loop_count=None)
            result.append(item)
        return result
