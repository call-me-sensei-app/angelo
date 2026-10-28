# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The private EEVEE draws behind ``view_warm``: a compile-ahead draw of a scene or of some of its objects, the
objects that draw given materials, and the first draws of newly compiled ones a tick at a time (``view_warm``'s
docstring; doc/angelo/agent_execution.rst)."""

import time

import bpy

SIZE = (64, 64)
TICK_SECONDS = 0.03      # A tick of first draws starts no further draw once this much of it is spent.
CHUNK_MAX = 8            # At most this many objects in one first draw (one of them bringing new pipelines).
GROUP_MAX = 4            # At most this many instancers in one (what their instances draw is unknown).
SLOW = 0.06              # A first draw over this built pipelines: the next draws one object alone.
OVERRIDE_TYPES = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "POINTCLOUD", "VOLUME"})


def _region(window, area=None):
    """``(area, region)`` the private draws take their settings from: ``area``, else the window's first 3D view."""
    from . import live_windows
    area = area or next((a for a in live_windows.areas(window) if a.type == 'VIEW_3D'), None)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area is not None else None
    return (area, region) if region is not None and region.data is not None else None


def _draw(window, area, region, only=None, lit=True):
    """One private compile-ahead draw of ``window``'s scene (of ``only`` alone when given) on a new offscreen, freed
    at once (its EEVEE instance with it), in the look the view asked for (``lit``: with the scene's lights and world;
    else neither, as MATERIAL draws: BREAKIT-viewwarm VW-M-1); its seconds. Raises what the draw raises."""
    import _bpy
    import gpu
    started = time.perf_counter()
    offscreen = gpu.types.GPUOffScreen(*SIZE)
    try:
        kwargs = {"only": only} if only is not None else {}
        kwargs.update({} if lit else {"lit": False})
        with bpy.context.temp_override(window=window, area=area, region=region):
            _bpy._angelo_live.draw(window.scene, offscreen, area.spaces.active, region.data.view_matrix,
                                   region.data.window_matrix, view_layer=window.view_layer, compile_ahead=True,
                                   **kwargs)
    finally:
        offscreen.free()
    return time.perf_counter() - started


def _uid(material):
    """What names a material across the native state and Python: its original's session UID (linked libraries hold
    same-named materials, VW9-H-1a); its name where a stand-in has none."""
    original = getattr(material, "original", material)
    uid = getattr(original, "session_uid", None)
    return uid if uid is not None else getattr(material, "name", None)


def _format(obj):
    """What of an object's vertex format a material's pipeline depends on, as cheaply as it reads: its type and, for a
    mesh, its UV maps and colour attributes (REVIEW-VIEW-SPREAD-2 MA). Used only to batch draws, never to skip one."""
    data = getattr(obj, "data", None)
    if obj.type != 'MESH' or data is None:
        return (obj.type,)
    return (obj.type, len(data.uv_layers), tuple(sorted(item.name for item in data.color_attributes)))


def _drawing(window, names):
    """``(entries, instancers, names drawn)``: ``(object, keys)`` for each visible object whose own evaluated
    material slots hold a material named in ``names`` (an instancer too: its draw draws its instances), ``keys`` the
    ``(material, format)`` pairs it brings, ordered so the same keys lie together; the other visible instancers (their
    instances may draw any); and the names those objects draw. A view layer's material override is what every object
    is drawn with (VW-M-2)."""
    from .object_bounds import makes_instances
    found, instancers, covered = [], [], set()
    try:
        depsgraph, objects = window.view_layer.depsgraph, list(window.view_layer.objects)
        override = getattr(window.view_layer, "material_override", None)
        override = _uid(override) if override is not None else None
    except (AttributeError, TypeError, RuntimeError, ReferenceError):
        return found, instancers, covered
    override = override if override in names else None
    for obj in objects:
        try:
            if not obj.visible_get(view_layer=window.view_layer):
                continue
            slots = obj.evaluated_get(depsgraph).material_slots
            new = {override} if override is not None and obj.type in OVERRIDE_TYPES else {
                _uid(slot.material) for slot in slots if slot.material is not None and _uid(slot.material) in names}
            if new:
                covered.update(new)
                kind = _format(obj)
                found.append((frozenset((name, kind) for name in new), obj))
            elif makes_instances(obj):
                instancers.append(obj)
        except (AttributeError, TypeError, ValueError, RuntimeError, ReferenceError):
            continue
    found.sort(key=lambda item: sorted(map(repr, item[0])))
    return [(obj, keys) for keys, obj in found], instancers, covered


def _first_draw_objects(window, names):
    """``(object, keys)`` for every visible object that draws a material named in ``names``, then ``(instancer,
    None)`` for every other visible instancer (module docstring)."""
    direct, instancers, _covered = _drawing(window, names)
    return direct + [(obj, None) for obj in instancers]


def _alive(obj):
    try:
        obj.name
        return True
    except (ReferenceError, AttributeError):
        return False


def _pointer(obj):
    try:
        return obj.as_pointer()
    except (AttributeError, ReferenceError):
        return id(obj)


def book(pace):
    """``(done, drawn)`` of ``pace``'s look: first draws build that look's pipelines only (LIT's scene lights and
    world are another variant than MATERIAL's studio light), so each look keeps its own (a merge warmed for MATERIAL
    left a LIT view to build 20 pipelines in its one draw, 506 ms on Mac 1)."""
    look = "LIT" if pace.get("lit", True) else "MATERIAL"
    entry = pace.setdefault("books", {}).setdefault(look, (set(), set()))
    return entry


def _batch(objects, pace):
    """The objects of the next first draw, taken from ``objects``: the first, and, unless the last draw was slow,
    those after it that bring nothing it and earlier draws did not (``CHUNK_MAX`` in all); for an instancer, up to
    ``pace["group"]`` instancers, doubled after a quick draw (their instances' materials are unknown)."""
    done, drawn = book(pace)
    while objects:
        obj, keys = objects.pop(0)
        if (_pointer(obj), keys) not in done and _alive(obj):
            break
    else:
        return [], None
    batch, seen = [(obj, keys)], set(keys or ())
    limit = pace.get("group", 1) if keys is None else (1 if pace.get("solo") else CHUNK_MAX)
    index = 0
    while len(batch) < limit and index < len(objects) and index < 64:
        other, other_keys = objects[index]
        fits = (other_keys is None) if keys is None else (other_keys is not None and other_keys <= drawn | seen)
        if fits and (_pointer(other), other_keys) not in done and _alive(other):
            batch.append((other, other_keys))
            seen |= other_keys or set()
            del objects[index]
        else:
            index += 1
    return batch, (None if keys is None else seen)


def first_draws(window, objects, area=None, pace=None):
    """Draw the next of ``objects`` (``(object, keys)``, taken as drawn) until ``TICK_SECONDS`` of this tick are
    spent (module docstring); False when nothing more can be spread here. ``pace`` (kept across ticks, across a
    merge's spreading and the view that takes it over: ``{"books", "solo", "group", "lit"}``, ``book``) holds the keys
    and the objects drawn with their keys, so none is drawn twice for the same keys (REVIEW-VIEW-SPREAD-3 M1) and one
    whose keys changed is drawn again (VW8-M-2). A draw brings at most one object's new
    pipelines, and others whose keys are all drawn already; after a draw over ``SLOW`` the next is one object alone.
    Each draw on a new private offscreen: one kept across draws holds one EEVEE instance, which renders its viewport
    sample count (16 by default) and then nothing more, since hiding objects changes no evaluation. An object removed
    meanwhile, or one an undo left stale, is skipped, never the rest (REVIEW-VIEW-SPREAD M1)."""
    pace = pace if pace is not None else {}
    host = _region(window, area)
    if host is None:
        objects.clear()
        return False
    tick = time.perf_counter()
    first = True
    while objects and (first or time.perf_counter() - tick < TICK_SECONDS):   # At least one draw a tick.
        upcoming = objects[0][1]
        if not first and (upcoming is None or not upcoming <= book(pace)[1]):
            break   # New pipelines only as a tick's first draw (REVIEW-VIEW-SPREAD-4 L1).
        batch, keys = _batch(objects, pace)
        if not batch:
            continue
        first = False
        only = [obj for obj, _keys in batch]
        try:
            spent = _draw(window, *host, only, pace.get("lit", True))
        except Exception:
            objects[:0] = batch   # Never done by failing (VW9-M-1): kept, and the view answers BROKE.
            raise
        done, drawn = book(pace)
        done.update((_pointer(obj), entry) for obj, entry in batch)   # Its keys too: changed, drawn again.
        drawn |= keys or set()
        pace["solo"] = spent > SLOW
        if keys is None:
            pace["group"] = 1 if spent > SLOW else min(GROUP_MAX, pace.get("group", 1) * 2)
    return True
