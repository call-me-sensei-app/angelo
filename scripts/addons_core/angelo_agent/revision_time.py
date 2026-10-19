# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether an object's revision carries the current frame (``scene_fingerprints.context_field``). No bpy import.

A revision's fields hash original data; an object's evaluated state (a Wave or geometry-nodes result, keyed or
driven values, a constraint, physics) can change with the frame without any of them changing, so the frame
guards that. ``still`` says when nothing can move an object in time; its revision then leaves the frame out, so
a frame change, by the person or by the model's own script, stales exactly the bindings of objects that can
change with time (recorded: 16 still rocks of an Astra bridge trial were refused STALE_REVISION after the
model's own ``frame_set(60)``).

It is ``program_frames.frame_independent`` widened by one step: a parent, a Mirror or Array object and a camera's
focus object are followed and must be still themselves (``frame_independent`` refuses any parent and does not
look at modifier or focus objects). The approved-program frame rebind keeps using ``frame_independent``
(``native_programs.rebind``), so a still child of a still parent keeps its binding across a frame change made
before its program, but a frame move between approval and staging still refuses it there (STALE_DEPENDENCY).
One dependency walk could later serve both questions.
"""

from .program_frames import FRAME_INDEPENDENT_MODIFIERS, FRAME_INDEPENDENT_TYPES, _animated, _graph_frame_independent
from .program_frames import MAX_FRAME_SCAN_NODES

# Object pointers of the allowed modifiers (Mirror, Array): the evaluated result follows those objects.
_MODIFIER_OBJECTS = ("mirror_object", "offset_object", "start_cap", "end_cap", "curve")
# Objects one answer may visit (the object, its parent chain and the objects its modifiers read).
MAX_FRAME_SCAN_OBJECTS = 64


def _read_objects(obj):
    """The objects ``obj``'s evaluated state follows: its parent, its modifiers' objects, a camera's focus object."""
    found = [obj.parent]
    found += [getattr(modifier, name, None) for modifier in obj.modifiers for name in _MODIFIER_OBJECTS]
    if obj.type == "CAMERA":
        found.append(getattr(getattr(obj.data, "dof", None), "focus_object", None))
    # Only objects: an Array's ``curve`` is an object, but other attributes of that name are not.
    return [item for item in found if item is not None and hasattr(item, "modifiers") and hasattr(item, "parent")]


def still_in_pass(obj, memo):
    """``still(obj)``, remembered for the rest of one observation pass (``ReadBudget.shared``).

    Only the object asked about is remembered: one reached inside a cycle was judged provisionally (a cycle
    counts as still until its own checks run). Node groups are remembered by pointer, so a group many objects
    share is scanned once a pass (otherwise 2,000 props on one 300-node group took 3.9 times as long).
    """
    pointer = obj.as_pointer() if hasattr(obj, "as_pointer") else id(obj)
    found = memo.get(("still", pointer))
    if found is None:
        found = memo[("still", pointer)] = still(obj, groups=memo)
    return found


def _group_still(tree, groups):
    """``_graph_frame_independent`` with a fresh node budget for each group, remembered by pointer in ``groups``."""
    if groups is None or tree is None:
        return _graph_frame_independent(tree, set(), [MAX_FRAME_SCAN_NODES])
    key = ("still_group", tree.as_pointer() if hasattr(tree, "as_pointer") else id(tree))
    if key not in groups:
        groups[key] = _graph_frame_independent(tree, set(), [MAX_FRAME_SCAN_NODES])
    return groups[key]


def still(obj, _visited=None, groups=None):
    """Conservatively, whether nothing about ``obj`` can change with the current frame.

    ``program_frames.frame_independent``'s rules (allowed object types and
    modifiers, time-free node graphs, no constraints, physics, particles,
    animation or drivers), with what the object follows judged the same way
    instead of refused outright. An object it follows (its parent, whatever the parent type; a Mirror or
    Array object; a camera's focus object) must be frame independent too, so a
    child of a still parent is still and a child of an animated one is not.
    Constraints of any kind, physics, particles, other object types, any
    modifier outside the allowed set and any animation or driver make it
    dependent, as does visiting more than ``MAX_FRAME_SCAN_OBJECTS`` objects.
    """
    visited = set() if _visited is None else _visited
    pointer = getattr(obj, "as_pointer", None)
    key = pointer() if callable(pointer) else id(obj)  # One Blender object may have several Python wrappers.
    if key in visited:  # A cycle adds no time of its own: each object's own check decides.
        return True
    visited.add(key)
    if len(visited) > MAX_FRAME_SCAN_OBJECTS or obj.type not in FRAME_INDEPENDENT_TYPES or len(obj.constraints):
        return False
    if getattr(obj, "rigid_body", None) is not None or len(getattr(obj, "particle_systems", ())):
        return False
    for modifier in obj.modifiers:
        if modifier.type == "NODES":
            if not _group_still(modifier.node_group, groups):
                return False
        elif modifier.type not in FRAME_INDEPENDENT_MODIFIERS:
            return False
    if not all(still(item, visited, groups) for item in _read_objects(obj)):
        return False
    data = obj.data
    if _animated(getattr(data, "node_tree", None)):  # A light's own nodes (its strength, colour) keyed or driven.
        return False
    return not any(_animated(block) for block in (obj, data, getattr(data, "shape_keys", None)))
