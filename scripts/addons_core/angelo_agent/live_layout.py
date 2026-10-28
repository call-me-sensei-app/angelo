# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a worker's part shows, as names: the worker reports it, Angelo's live view mirrors it (``live_view``).

A worker's saved part is linked into Angelo by **collections and objects, never its Scene**: Blender always keeps a
linked Scene as directly linked, so a saved main file would reference the part (``lib_id.cc``,
``BKE_main_id_indirect_linked_update``). Collections and objects used only by Angelo's runtime-only live scene are
written nowhere. The live scene therefore needs to know what the worker's own scene holds at its top level, and
only the worker knows that exactly: ``layout`` runs there after each save, and its answer travels with the edit's
result.

Each layout also carries the size of what the part shows, measured by the worker on its own evaluated scene, so the
lead model reads the part's real size (``worker_sessions.status``).

``plan`` is pure (host-tested): what to link and what the live scene's master collection must hold afterwards.
"""

LIMIT = 4096   # Names per kind a layout carries; a part with more top-level members is cut, and says so.


def layout(scene, revision, depsgraph=None):
    """The worker's scene top level: its master collection's child collections and objects, its World, in order;
    and, given its evaluated ``depsgraph``, the size of what it shows (``size``)."""
    children = [child.name for child in scene.collection.children]
    objects = [obj.name for obj in scene.collection.objects]
    return {"revision": int(revision), "collections": children[:LIMIT], "objects": objects[:LIMIT],
            "world": scene.world.name if scene.world is not None else None,
            "truncated": len(children) > LIMIT or len(objects) > LIMIT,
            **measure(depsgraph)}


def measure(depsgraph):
    """``{"size": [x, y, z], "bounds": [[min x, y, z], [max x, y, z]]}`` metres of what the worker shows, measured
    once (``depsgraph_bounds``; ``size`` alone in a build without it, None without a depsgraph). Helper geometry is
    left out of both (Boolean operands, wire or bounds display, hidden from renders: the maintainer, 2026-10-25)."""
    if depsgraph is None:
        return {"size": None, "bounds": None}
    try:
        import _bpy
        box = _bpy._angelo_live.depsgraph_bounds(depsgraph)
    except (ImportError, AttributeError):
        return {"size": size(depsgraph), "bounds": None}
    if box is None:
        return {"size": [0.0, 0.0, 0.0], "bounds": None}
    low, high = [[round(v, 4) for v in corner] for corner in box]
    return {"size": [round(b - a, 4) for a, b in zip(low, high)], "bounds": [low, high]}


def size(depsgraph):
    """``[x, y, z]`` metres of the world-space box around every visible object and instance, as the worker evaluates
    it: its own Multires levels and modifiers, never the simplified copy Angelo's pictures show (break-it P1-3 M5).
    Measured in C++ (``_angelo_live.depsgraph_size``: O(objects), never a Python loop per instance on each save's
    path, P1-4 M4); None in a build without it. A Subdivision Surface counts at its cage (Blender subdivides it only
    to draw it): an upper bound."""
    try:
        import _bpy
        measure = _bpy._angelo_live.depsgraph_size
    except (ImportError, AttributeError):
        return None
    extent = measure(depsgraph)
    return [round(v, 4) for v in extent] if extent is not None else [0.0, 0.0, 0.0]


def clean(value):
    """A layout from the worker, checked (it crosses a process boundary); None when it is not one."""
    if not isinstance(value, dict) or type(value.get("revision")) is not int:
        return None
    names = {}
    for kind in ("collections", "objects"):
        items = value.get(kind)
        if not isinstance(items, list) or not all(isinstance(item, str) and item for item in items):
            return None
        names[kind] = list(dict.fromkeys(items))[:LIMIT]
    world = value.get("world")
    if world is not None and (not isinstance(world, str) or not world):
        return None
    extent = value.get("size")
    if not (isinstance(extent, list) and len(extent) == 3 and all(
            type(v) in (int, float) and 0.0 <= v < 1e9 for v in extent)):
        extent = None   # An older worker, or none sent: the size is unknown, never guessed.
    box = value.get("bounds")
    if not (isinstance(box, list) and len(box) == 2 and all(
            isinstance(c, list) and len(c) == 3 and all(type(v) in (int, float) and abs(v) < 1e9 for v in c)
            for c in box) and all(box[0][i] <= box[1][i] for i in range(3))):
        box = None   # Unknown, never guessed.
    from .worker_merge import clean_view   # What the worker's view layer hides: the merge keeps it hidden.
    return {"revision": value["revision"], "collections": names["collections"], "objects": names["objects"],
            "world": world, "truncated": bool(value.get("truncated")),
            "size": [float(v) for v in extent] if extent is not None else None,
            "bounds": [[float(v) for v in corner] for corner in box] if box is not None else None,
            "view": clean_view(value.get("view"))}


def plan(wanted, linked, available):
    """``(to_load, kept)`` for one kind of ID.

    ``wanted`` is the layout's names in order, ``linked`` the names already linked from the part (present and not
    missing), ``available`` the names the part file holds now (``libraries.load``'s listing; None when not read).
    ``to_load`` is what must be linked; ``kept`` is what the live scene holds afterwards, in the layout's order. A
    name the part no longer has is dropped: the worker removed it after reporting, so the next report is newer.
    """
    linked = set(linked)
    present = linked if available is None else set(available)
    kept = [name for name in wanted if name in present]
    return [name for name in kept if name not in linked], kept
