# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a typed edit changed: a bounded signature of the scene's objects before and after, and their difference.

Moved out of ``blender_tools`` (it is re-exported there), so that module stays within the size rule.

A whole-scene signature keeps at most ``LIMIT`` objects. Within that it reads every object's world matrix with one
``foreach_get`` (``_matrices``); a larger scene's kept objects are read one by one, because ``foreach_get`` fills the
whole collection, so the read stays bounded by what is kept, never by the scene (audit #7). It keeps each as
``Matrix``, which compares equal exactly when the old rounded lists did: every value equal to 6 decimals. Rounding
16 values per object in Python was half the signature's cost (2.8 of 5.8 ms on 1,536 cubes), and an edit reads the
signature twice; now only an object whose matrix bytes differ is rounded, when it is compared.
"""

import hashlib
from array import array
from itertools import islice

from .motion_facts import motion_signature

LIMIT = 1000   # Objects a signature keeps; a larger scene's signature is ``truncated``.


class Matrix:
    """An object's world matrix in a signature: its 16 single-precision values' bytes, column by column. Equal to
    another exactly when every value is equal to 6 decimals (the rounded lists signatures held before): equal bytes
    are, and any others are rounded and compared. Never serialized; only compared."""
    __slots__ = ("raw",)

    def __init__(self, raw):
        self.raw = raw

    def __eq__(self, other):
        if not isinstance(other, Matrix):
            return NotImplemented
        return self.raw == other.raw or self.rounded() == other.rounded()

    __hash__ = None   # Mutable-looking equality: never a key.

    def rounded(self):
        values = array("f")
        values.frombytes(self.raw)
        return [round(float(value), 6) for value in values]

    def __repr__(self):
        return "Matrix(%r)" % self.rounded()


def _one_matrix(obj):
    """``obj``'s world matrix as ``Matrix`` bytes, read on its own (a typed edit's few targets)."""
    rows = [tuple(row) for row in obj.matrix_world]
    return array("f", [rows[row][column] for column in range(4) for row in range(4)]).tobytes()


def _matrices(objects, count):
    """Every scene object's world matrix bytes in one ``foreach_get`` (the order iteration gives), or None when the
    collection cannot be read that way (a stand-in) or its length is not ``count``."""
    try:
        values = array("f", [0.0]) * (16 * count)
        objects.foreach_get("matrix_world", values)
    except (AttributeError, TypeError, RuntimeError, ValueError):
        return None
    raw = values.tobytes()
    return [raw[index * 64:index * 64 + 64] for index in range(count)]


def scene_signature(scene, targets=None):
    objects = {}
    vertex_budget = 1000000
    collection = scene.objects if targets is None else targets
    count = len(collection)
    truncated = count > LIMIT
    matrices = _matrices(collection, count) if targets is None and not truncated else None
    for index, obj in enumerate(islice(collection, LIMIT)):
        data, kind = obj.data, obj.type  # Each read once: the motion digest's reads cost what these saved.
        mesh = kind == 'MESH'
        vertices, geometry = (len(data.vertices) if mesh else 0), ""
        if mesh and vertices <= vertex_budget:
            coordinates = array('f', [0]) * (vertices * 3)
            data.vertices.foreach_get("co", coordinates)
            geometry = hashlib.blake2b(coordinates,digest_size=32).hexdigest()
            vertex_budget -= vertices
        elif mesh:
            truncated = True
        objects[str(obj.session_uid)] = {
            "name": obj.name, "type": kind, "matrix": Matrix(matrices[index] if matrices else _one_matrix(obj)),
            "vertices": vertices, "polygons": len(data.polygons) if mesh else 0,
            "materials": [slot.material.name if slot.material else "" for slot in obj.material_slots],
            "modifiers": [(m.name, m.type) for m in obj.modifiers],
            "geometry": geometry, "motion": motion_signature(obj, data, kind),  # Keys, constraints, parent.
        }
    total = count if targets is None else len(scene.objects)   # Counting a scene's objects walks them all: once.
    return {"objects": objects, "count": total, "truncated": truncated, "targeted": targets is not None}


def scene_diff(before, after):
    old, new = before["objects"], after["objects"]
    added = sorted((new[key] for key in new.keys() - old.keys()), key=lambda row: row["name"])  # One order every run.
    removed = sorted((old[key] for key in old.keys() - new.keys()), key=lambda row: row["name"])
    modified = [{"name": new[key]["name"], "fields": [field for field in new[key] if new[key][field] != old[key][field]]}
                for key in sorted(new.keys() & old.keys(), key=lambda key: new[key]["name"]) if new[key] != old[key]]
    return {"added": [{"name": o["name"], "type": o["type"], "vertices": o["vertices"]} for o in added],
            "removed": [{"name": o["name"], "type": o["type"]} for o in removed], "modified": modified,
            "object_count_before": before["count"], "object_count_after": after["count"],
            "truncated": before["truncated"] or after["truncated"],
            "scope": ("Declared typed-operation targets; derived changes to unrelated objects and external effects are not tracked"
                      if before.get('targeted') else "Scene object/dependency-graph read-back; external effects are not tracked")}
