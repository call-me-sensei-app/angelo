# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a web page shows for a node of a live frame, as one digest (``web_link_live``; doc/angelo/web_link.rst,
"Live frames"). Standard library only; no Blender.

A digest is the SHA-256 of one canonical walk of everything the page reads for the node: its name, transform,
children's keys and own extras; its mesh with every primitive (attributes, indices, morph targets, material, extras,
extensions) and the mesh's own weights and extras (``targetNames``, which three.js reads for the morph dictionary);
its skin; its camera; and its extensions. Every index into the document is replaced by the canonical form of what it
points to: an accessor by its type and bytes, sparse indices and values included, read through its buffer view's
offset and stride; an image by the digest of its bytes (``extras.angelo_digest``); a texture by its image, sampler and
extensions; a node by its key; an index under an extension by what ``TARGETS`` says it names (a light, a physics
shape, the mesh a convex or trimesh shape uses, the nodes of a trigger), and an ``attributes`` map by its accessors.
An integer no entry names stays as written (break-it round 3, M3). So a digest moves when what the page shows moves, and never because an object
elsewhere moved what came before it in the file (break-it N1, N4, N5).
"""

import hashlib
import json
import struct

KEY, ROLE, DIGEST, CONTENT = "angelo_uid", "angelo_role", "angelo_digest", "content"
SOURCE = "angelo_from"   # A node an extension added names the keyed node it was made for (io_scene_gltf2 physics).
OWN_EXTRAS = (KEY, ROLE, DIGEST)
# (extension, field) -> what an integer there indexes: a list of a document-level extension, or a top-level array.
TARGETS = {("KHR_lights_punctual", "light"): ("KHR_lights_punctual", "lights"),
           ("OMI_physics_body", "shape"): ("OMI_physics_shape", "shapes"),
           ("OMI_physics_body", "nodes"): (None, "nodes"),
           ("OMI_physics_shape", "mesh"): (None, "meshes")}
COMPONENT_BYTES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
TYPE_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


def read_glb(body):
    """(JSON document, binary chunk) of a .glb; raises ValueError."""
    if len(body) < 20 or body[:4] != b"glTF":
        raise ValueError("Not a .glb")
    length, kind = struct.unpack("<II", body[12:20])
    if kind != 0x4E4F534A:
        raise ValueError("A .glb starts with its JSON")
    document = json.loads(body[20:20 + length].decode("utf-8"))
    binary, offset = b"", 20 + length
    if offset + 8 <= len(body):
        size, kind = struct.unpack("<II", body[offset:offset + 8])
        if kind == 0x004E4942:
            binary = body[offset + 8:offset + 8 + size]
    return document, binary


def write_glb(document, binary):
    """A .glb of ``document`` and the unchanged ``binary`` chunk."""
    text = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    text += b" " * (-len(text) % 4)
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text
    if binary:
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks


class _Walk:
    """One document's canonical forms, each computed once."""

    def __init__(self, document, binary):
        self.document, self.binary = document, binary
        self.accessors = {}

    def _list(self, name):
        return self.document.get(name) or []

    def _elements(self, view_index, offset, count, element):
        """``count`` elements of ``element`` bytes from a buffer view, at ``offset``, through its stride."""
        view = self._list("bufferViews")[view_index]
        start = view.get("byteOffset", 0) + offset
        stride = view.get("byteStride") or element
        if stride == element:
            return self.binary[start:start + count * element]
        return b"".join(self.binary[start + i * stride:start + i * stride + element] for i in range(count))

    def accessor(self, index):
        if index in self.accessors:
            return self.accessors[index]
        accessor = self._list("accessors")[index]
        width = COMPONENT_BYTES.get(accessor.get("componentType"), 4) * TYPE_COUNT.get(accessor.get("type"), 1)
        hasher = hashlib.sha256(json.dumps([accessor.get(key) for key in (
            "componentType", "type", "count", "normalized")]).encode())
        if "bufferView" in accessor:
            hasher.update(self._elements(accessor["bufferView"], accessor.get("byteOffset", 0),
                                         accessor.get("count", 0), width))
        sparse = accessor.get("sparse")
        if isinstance(sparse, dict):   # Shape keys: the exporter writes only the vertices a target moves.
            indices, values = sparse.get("indices", {}), sparse.get("values", {})
            count = sparse.get("count", 0)
            hasher.update(json.dumps([count, indices.get("componentType")]).encode())
            if "bufferView" in indices and "bufferView" in values:
                hasher.update(self._elements(indices["bufferView"], indices.get("byteOffset", 0), count,
                                             COMPONENT_BYTES.get(indices.get("componentType"), 4)))
                hasher.update(self._elements(values["bufferView"], values.get("byteOffset", 0), count, width))
        self.accessors[index] = found = hasher.hexdigest()
        return found

    def image(self, index):
        image = self._list("images")[index] if index is not None else {}
        return (image.get("extras") or {}).get(DIGEST)

    def texture(self, index):
        texture = self._list("textures")[index]
        sources = {"source": self.image(texture.get("source"))}
        for name, extension in sorted((texture.get("extensions") or {}).items()):   # EXT_texture_webp, basisu.
            sources[name] = self.image(extension.get("source")) if isinstance(extension, dict) else None
        sampler = self._list("samplers")[texture["sampler"]] if "sampler" in texture else {}
        return {"images": sources, "sampler": sampler}

    def material_value(self, value):
        """A material, with every texture info's index replaced by its texture."""
        if isinstance(value, dict):
            found = {key: self.material_value(item) for key, item in value.items()}
            if isinstance(value.get("index"), int) and "texture" not in value:
                found["index"] = self.texture(value["index"])
            return found
        if isinstance(value, list):
            return [self.material_value(item) for item in value]
        return value

    def extension(self, name, value, key=None, seen=()):
        """An extension's value, every index ``TARGETS`` knows replaced by what it names in the document; ``seen``
        stops an element that names itself."""
        if isinstance(value, dict):
            found = {}
            for item_key, item in value.items():
                if item_key == "attributes" and isinstance(item, dict):
                    found[item_key] = {attribute: self.accessor(index) for attribute, index in item.items()
                                       if isinstance(index, int)}
                else:
                    found[item_key] = self.extension(name, item, item_key, seen)
            return found
        if isinstance(value, list):
            return [self.extension(name, item, key, seen) for item in value]
        target = TARGETS.get((name, key))
        if target is None or not isinstance(value, int) or isinstance(value, bool):
            return value
        owner, array = target
        if owner is None:
            elements = self.document.get(array)
        else:
            elements = ((self.document.get("extensions") or {}).get(owner) or {}).get(array)
        if not isinstance(elements, list) or not 0 <= value < len(elements) or (owner, array, value) in seen:
            return {"missing": [owner, array]}
        if array == "nodes":
            return {"node": self.node_key(value)}
        if array == "meshes":
            return {"mesh": self.mesh(elements[value])}
        return {"element": self.extension(owner, elements[value], None, seen + ((owner, array, value),))}

    def mesh(self, mesh):
        return {"weights": mesh.get("weights"), "extras": mesh.get("extras"),
                "primitives": [self.primitive(primitive) for primitive in mesh.get("primitives", [])]}

    def primitive(self, primitive):
        return {"mode": primitive.get("mode", 4),
                "attributes": {name: self.accessor(index) for name, index in primitive.get("attributes", {}).items()},
                "indices": self.accessor(primitive["indices"]) if "indices" in primitive else None,
                "targets": [{name: self.accessor(index) for name, index in target.items()}
                            for target in primitive.get("targets", [])],
                "material": self.material_value(self._list("materials")[primitive["material"]])
                if "material" in primitive else None,
                "extras": primitive.get("extras"),
                "extensions": self.extension("primitive", primitive.get("extensions") or {})}

    def node_key(self, index):
        return (self._list("nodes")[index].get("extras") or {}).get(KEY)

    def node(self, node, parent=""):
        """``parent``: the key of the node above it; a node moved between parents at one place changes no transform
        (break-it round 6, H1). Ancestors are in every frame, so a delta names the same parent a full frame does."""
        extras = {key: value for key, value in (node.get("extras") or {}).items() if key not in OWN_EXTRAS}
        found = {name: node.get(name) for name in ("name", "weights")}
        found.update({name: _numbers(node.get(name)) for name in ("matrix", "translation", "scale")},
                     rotation=_rotation(node.get("rotation")))
        # Children carry their own place (a delta holds only some of them: break-it round 5, M1).
        found.update(parent=parent, extras=extras, extensions={name: self.extension(name, value)
                                 for name, value in (node.get("extensions") or {}).items()})
        if "mesh" in node:
            found["mesh"] = self.mesh(self._list("meshes")[node["mesh"]])
        if "skin" in node:
            skin = self._list("skins")[node["skin"]]
            found["skin"] = {"joints": [self.node_key(joint) for joint in skin.get("joints", [])],
                             "skeleton": self.node_key(skin["skeleton"]) if "skeleton" in skin else None,
                             "inverse": self.accessor(skin["inverseBindMatrices"])
                             if "inverseBindMatrices" in skin else None}
        if "camera" in node:
            found["camera"] = self._list("cameras")[node["camera"]]
        return hashlib.sha256(json.dumps(found, sort_keys=True, default=str).encode()).hexdigest()


def complete_keys(document, owners):
    """Key the nodes written after Angelo's hooks keyed the tree, on the final node list: a node the exporter or an
    extension added (a physics collider, the visual it moved off a body) gets the key of the node it was made for
    (``extras.angelo_from``, else its parent) and ``#`` with its place among that node's added nodes, with that
    node's role and owners; a node with a key already used (an object's particle copies) gets that key
    and ``~`` with its occurrence. Each takes its parent's role and owner, so every node has its own key (break-it
    round 3, H1). ``owners`` (key -> object uid) gains the new keys."""
    nodes = document.get("nodes") or []
    scenes = document.get("scenes") or []
    roots = scenes[document.get("scene", 0)].get("nodes", []) if scenes else []
    roles = {(node.get("extras") or {}).get(KEY): (node.get("extras") or {}).get(ROLE, CONTENT) for node in nodes
             if (node.get("extras") or {}).get(KEY) is not None}
    seen, pending = {}, [(index, None) for index in reversed(roots)]
    while pending:
        index, parent = pending.pop()
        node = nodes[index]
        extras = dict(node.get("extras") or {})
        parent_extras = (nodes[parent].get("extras") or {}) if parent is not None else {}
        parent_key = parent_extras.get(KEY, "")
        key = extras.get(KEY)
        if key is None:
            source = extras.pop(SOURCE, None)   # A physics collider says whose shape it is (break-it round 4, M1).
            origin = source if source in roles else parent_key
            count = seen.get((origin, "#"), 0)
            seen[(origin, "#")] = count + 1
            key = "%s#%d" % (origin, count)
            # Its shape's object and the body it hangs under both shape it (round 5, M2): either sends it.
            sides = {origin, parent_key}
            known = [role for role in (roles.get(origin), parent_extras.get(ROLE)) if role is not None]
            extras[ROLE] = CONTENT if not known or CONTENT in known else known[0]
            chain = set()
            for side in sides:
                if side in owners:
                    chain |= owners[side] if isinstance(owners[side], (set, frozenset)) else {owners[side]}
            if chain:
                owners[key] = frozenset(chain)
        elif key in seen:
            seen[key] += 1
            owner = owners.get(key)
            key = "%s~%d" % (key, seen[key])
            if owner is not None:
                owners[key] = owner
        seen.setdefault(key, 0)
        extras[KEY] = key
        node["extras"] = extras
        for child in reversed(node.get("children", [])):
            pending.append((child, index))


DIGITS = 7   # Significant digits a transform is compared at (float32's): export noise never moves a digest, and a
#             tiny object's change still does (break-it round 6: decimal places hid it).


def _numbers(values):
    return [float("%.*g" % (DIGITS, value)) + 0.0 for value in values] if isinstance(values, list) else values


def _rotation(quaternion):
    """A rotation as the page sees it: ``q`` and ``-q`` are the same rotation, and the exporter writes either
    (break-it round 5: a particle copy's quaternion flipped sign between two exports of an unchanged scene)."""
    if not isinstance(quaternion, list) or len(quaternion) != 4:
        return quaternion
    values = _numbers(quaternion)
    leading = next((value for value in reversed(values) if value != 0.0), 0.0)   # w first, then z, y, x.
    return [-value + 0.0 for value in values] if leading < 0.0 else values


def node_digests(document, binary):
    """{key: digest} of every content node: what the page shows for it, with each image by its digest."""
    walk = _Walk(document, binary)
    nodes = document.get("nodes", [])
    parents = {child: str(walk.node_key(index)) for index, node in enumerate(nodes)
               for child in node.get("children", [])}
    found = {}
    for index, node in enumerate(nodes):
        extras = node.get("extras") or {}
        if extras.get(KEY) is not None and extras.get(ROLE, CONTENT) == CONTENT:
            found[extras[KEY]] = walk.node(node, parents.get(index, ""))
    return found


def stamp_digests(document, digests):
    """Write each content node's digest into its extras, for the page to compare with what it has."""
    for node in document.get("nodes", []):
        extras = node.get("extras") or {}
        if extras.get(KEY) in digests and extras.get(ROLE, CONTENT) == CONTENT:
            extras[DIGEST] = digests[extras[KEY]]
            node["extras"] = extras


def image_digests(document):
    return {(image.get("extras") or {}).get(DIGEST) for image in document.get("images", [])} - {None}
