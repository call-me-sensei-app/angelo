# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One exact account of a shape-key datablock, for every owner type (mesh, lattice, curve). No bpy import.

A Key changes what its owner evaluates to through its own settings (relative or absolute keys, the evaluation
time), each key's settings (value, mute, range, relative key, vertex group, interpolation; select and lock, which
Blender's shape-key operators read) and each key's points. Meshes used to hash a hand-picked subset and lattices and
curves none of it, so editing a key changed the result under one exact revision (recorded on 1.0.9, 1.0.10 and main,
2026-11-05). Settings come from RNA (``generic_settings.values``, every writable setting); points are copied as packed
floats like mesh positions where every point has the same layout, otherwise point by point. One Key is read once per
observation pass (``scene_shared_reads``), however many objects use it or depend on its owner. The Key's datablock
bookkeeping (users, fake user) and a key's relative key beyond its name are left out: neither changes the result.

The owner object's shape-key display settings (pinning one key, the active key) also change its result; they belong to
the object, so ``owner_settings`` reads them separately and ``test_blender_revision_deformers`` pins the list
against Blender's RNA.
"""

from array import array
import hashlib

from .scene_read_values import SceneQueryError, all_finite, dump, label

# Object settings that change what a Key evaluates to on its owner. Pinned against every writable Object property
# whose name mentions a shape key (test_blender_revision_deformers), so a new one is reviewed.
OWNER_SETTINGS = ("active_shape_key_index", "show_only_shape_key", "use_shape_key_edit_mode")


def _reference(value):
    return label(getattr(value, "name", str(value)))


def owner_settings(obj):
    """The owner's shape-key settings, plain."""
    return [getattr(obj, name, None) for name in OWNER_SETTINGS]


def _packed(block, width):
    """Every point's ``co`` as packed floats, or None when the points do not share one layout."""
    data = block.data
    values = array("f", [0.0]) * (len(data) * width)
    try:
        data.foreach_get("co", values)
    except (AttributeError, TypeError, RuntimeError):
        return None
    return values


def _id_bookkeeping():
    """Settings every datablock has (its name, users, fake user, library state): bookkeeping, not evaluation."""
    import bpy
    return frozenset(prop.identifier for prop in bpy.types.ID.bl_rna.properties)


def _settings(struct, skip=()):
    """``struct``'s writable settings (``generic_settings.values``), without those under a ``skip`` name."""
    from .generic_settings import values
    return {path: value for path, value in values(struct, _reference).items()
            if path.split(".", 1)[0].split("[", 1)[0] not in skip}


def key_digest(key, owner_type, work):
    """The exact account of ``key`` (a hex digest), read once per pass (``scene_shared_reads``: a later owner in the
    pass replays the first read's work charges, so an object is exact in a pass exactly when it is alone)."""
    from .scene_shared_reads import shared_read
    cache_key = ("shape_key_account", key.as_pointer()) if hasattr(key, "as_pointer") else None
    return shared_read(work, cache_key, lambda budget, _unsupported, _resource: _read(key, owner_type, budget))


def _read(key, owner_type, work):
    digest = hashlib.sha256()
    digest.update(dump(_settings(key, _id_bookkeeping())).encode("utf-8"))
    for block in key.key_blocks:
        work.take(items=1)
        relative = block.relative_key  # By its name: its own settings are hashed as its own key block.
        digest.update(dump([_settings(block, ("relative_key",)),
                            label(relative.name) if relative is not None else None]).encode("utf-8"))
        points = _packed(block, 3) if owner_type in ("MESH", "LATTICE") else None
        if points is not None:
            work.take(packed=len(points) * points.itemsize)
            if not all_finite(points):
                raise SceneQueryError("Non-finite shape-key points cannot provide a trustworthy revision")
            digest.update(b"co")
            digest.update(points)
            continue
        for point in block.data:  # A curve's keys: Bezier points carry handles, tilt and radius as well.
            work.take(items=1)
            digest.update(dump(_settings(point)).encode("utf-8"))
    return digest.hexdigest()
