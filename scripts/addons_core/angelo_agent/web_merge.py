# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Safe draw-call merging inside a web export's glTF tree. Needs numpy; no Blender data is read or written.

A web page pays one draw call per mesh primitive of each node, so many small objects with the same material
render faster as one primitive. ``merge_scene`` runs on the exporter's in-memory scene (``web_gltf_hook``, before
Draco or meshopt compression) and bakes each mergeable node's world transform into its vertices, then joins the
primitives that share a material, mode and attribute layout into one node at the scene root.

A node is merged only when nothing on the web can tell: its object is static (``static``: no animation, drivers
or constraints on it or a parent, decided on the Blender side), and the node has a mesh but no children, skin,
morph targets, camera, light or extensions, and its mesh is drawn by no other node; every primitive has only positions, normals, tangents, UVs and
colours, triangles (or no mode). Merged objects lose their own names on the web; ``merge=false`` keeps them.
The exporter's classes are taken from the objects themselves, so this module never imports the exporter.
"""

import numpy as np

TRIANGLES = 4
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
UNSIGNED_SHORT, UNSIGNED_INT, FLOAT = 5123, 5125, 5126
MERGEABLE = ("POSITION", "NORMAL", "TANGENT", "TEXCOORD_", "COLOR_")
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
DTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}


def _local(node):
    if node.matrix:
        return np.array(node.matrix, dtype=np.float64).reshape(4, 4).T   # glTF matrices are column-major.
    x, y, z, w = node.rotation or (0.0, 0.0, 0.0, 1.0)
    rotation = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    matrix = np.identity(4)
    matrix[:3, :3] = rotation * np.array(node.scale or (1.0, 1.0, 1.0))
    matrix[:3, 3] = node.translation or (0.0, 0.0, 0.0)
    return matrix


def _values(accessor):
    """The accessor's elements as a (count, width) array; None for a layout this module doesn't read."""
    if accessor is None or getattr(accessor, "sparse", None) or accessor.type not in WIDTH \
            or accessor.component_type not in DTYPE or getattr(accessor, "extensions", None):
        return None
    data = getattr(accessor.buffer_view, "data", None)
    if not isinstance(data, (bytes, bytearray)):
        return None
    width = WIDTH[accessor.type]
    values = np.frombuffer(data, dtype=DTYPE[accessor.component_type], offset=accessor.byte_offset or 0,
                           count=accessor.count * width)
    return values.reshape(accessor.count, width)


def _layout(primitive):
    """The key primitives must share to merge, or None when this primitive can't merge."""
    if primitive.targets or primitive.extensions or primitive.extras or (primitive.mode or TRIANGLES) != TRIANGLES:
        return None
    attributes = primitive.attributes or {}
    if "POSITION" not in attributes or any(not name.startswith(MERGEABLE) for name in attributes):
        return None
    layout = []
    for name in sorted(attributes):
        accessor = attributes[name]
        if _values(accessor) is None:
            return None
        layout.append((name, accessor.component_type, accessor.type, bool(accessor.normalized)))
    if primitive.indices is not None and _values(primitive.indices) is None:
        return None
    return (id(primitive.material), tuple(layout))


def _mergeable(node, static, uses):
    mesh = node.mesh
    # A mesh drawn by several nodes stays shared: copying it into a merge would grow the download per copy.
    return (mesh is not None and uses.get(id(mesh)) == 1 and not node.children and node.skin is None and node.camera is None
            and not node.extensions and not node.weights and not getattr(mesh, "weights", None)
            and static(node.name) and mesh.primitives and all(_layout(item) is not None for item in mesh.primitives))


def _walk(scene_nodes):
    """(node, parent or None, world matrix) for every node under the scene's roots, parents first."""
    stack = [(node, None, np.identity(4)) for node in reversed(scene_nodes)]
    while stack:
        node, parent, above = stack.pop()
        world = above @ _local(node)
        yield node, parent, world
        stack.extend((child, node, world) for child in reversed(node.children or ()))


def _transformed(name, values, world):
    if name == "POSITION":
        return (values.astype(np.float64) @ world[:3, :3].T + world[:3, 3]).astype(np.float32)
    if name in {"NORMAL", "TANGENT"}:
        linear = world[:3, :3]
        matrix = np.linalg.inv(linear).T if name == "NORMAL" else linear
        vectors = values[:, :3].astype(np.float64) @ matrix.T
        lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = np.where(lengths > 0.0, vectors / np.where(lengths > 0.0, lengths, 1.0), vectors)
        if name == "TANGENT":   # w is the bitangent's handedness, which a mirroring transform flips.
            sign = -1.0 if np.linalg.det(linear) < 0.0 else 1.0
            return np.hstack([vectors, values[:, 3:4] * sign]).astype(np.float32)
        return vectors.astype(np.float32)
    return values


def _accessor(template, values, target, component_type=None, bounds=False):
    Accessor, BinaryData = type(template), type(template.buffer_view)
    component_type = component_type or template.component_type
    data = np.ascontiguousarray(values, dtype=DTYPE[component_type])
    width = 1 if data.ndim == 1 else data.shape[1]
    low = [float(v) for v in data.min(axis=0)] if bounds and len(data) else None
    high = [float(v) for v in data.max(axis=0)] if bounds and len(data) else None
    return Accessor(BinaryData(data.tobytes(), target), None, component_type, len(data), None, None, high, low,
                    None, template.normalized, None, {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}[width])


def _join(parts):
    """One primitive from [(primitive, world)] sharing a layout."""
    first = parts[0][0]
    names = sorted(first.attributes)
    columns, indices, offset = {name: [] for name in names}, [], 0
    for primitive, world in parts:
        count = primitive.attributes["POSITION"].count
        for name in names:
            columns[name].append(_transformed(name, _values(primitive.attributes[name]), world))
        local = _values(primitive.indices).reshape(-1) if primitive.indices is not None else np.arange(count)
        triangles = local.astype(np.int64).reshape(-1, 3)
        if np.linalg.det(world[:3, :3]) < 0.0:   # Mirrored: keep the faces pointing outward.
            triangles = triangles[:, ::-1]
        indices.append(triangles.reshape(-1) + offset)
        offset += count
    attributes = {name: _accessor(first.attributes[name], np.concatenate(columns[name]), ARRAY_BUFFER,
                                  bounds=name == "POSITION") for name in names}
    index_type = UNSIGNED_SHORT if offset <= 65535 else UNSIGNED_INT
    joined = np.concatenate(indices)
    return type(first)(attributes, None, None, _accessor(first.attributes["POSITION"], joined, ELEMENT_ARRAY_BUFFER,
                                                         component_type=index_type),
                       first.material, TRIANGLES, None)


def merge_scene(scene, static, name="Merged"):
    """Merge ``scene``'s mergeable nodes in place; returns {"objects": merged nodes, "groups": primitives made}."""
    walked = list(_walk(scene.nodes or []))
    uses = {}
    for node, _parent, _world in walked:
        if node.mesh is not None:
            uses[id(node.mesh)] = uses.get(id(node.mesh), 0) + 1
    candidates = [(node, parent, world) for node, parent, world in walked if _mergeable(node, static, uses)]
    groups = {}
    for node, _parent, world in candidates:
        for primitive in node.mesh.primitives:
            groups.setdefault(_layout(primitive), []).append((node, primitive, world))
    # A node moves only if one of its primitives joins another node's: otherwise merging saves no draw call.
    moving = {id(node) for parts in groups.values() if len({id(item[0]) for item in parts}) > 1 for node, _p, _w in parts}
    if not moving:
        return {"objects": 0, "groups": 0}
    primitives = []
    for parts in groups.values():
        chosen = [(primitive, world) for node, primitive, world in parts if id(node) in moving]
        if chosen:
            primitives.append(_join(chosen))
    for node, parent, _world in candidates:
        if id(node) in moving:
            siblings = parent.children if parent is not None else scene.nodes
            siblings.remove(node)
    template_node, template_mesh = candidates[0][0], candidates[0][0].mesh
    mesh = type(template_mesh)(None, None, name, primitives, None)
    scene.nodes.append(type(template_node)(None, [], None, None, None, mesh, name, None, None, None, None, None))
    return {"objects": len(moving), "groups": len(primitives)}
