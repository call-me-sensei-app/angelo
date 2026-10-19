# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure key-filter rules for ``keyframe_edit``: Clean, Decimate, Smooth and the Euler filter. No bpy.

Each function takes the keys of one F-Curve, sorted by frame, as plain values
and returns what the kernel (``anim_key_edits``) writes back, so the rules are
tested on the host:

* ``clean`` is Blender's Clean Keyframes rule (``clean_fcurve`` in
  ``keyframes_general.cc``): a key is dropped when its value matches the last
  kept key's within ``threshold`` and, unless it is the last key, the next
  key's too; the first key is always kept.
* ``smooth`` is Blender's Smooth Keys (``smooth_fcurve``): two passes of
  5-point weighted means, end keys untouched, handles pulled 30% towards the
  averaged neighbours.
* ``decimate`` removes the interior keys whose value deviates least from the
  straight line between their kept neighbours, smallest first (a heap, so
  O(n log n)): a ``ratio`` of the interior keys, or every key within ``error``.
  This is a value-space simplification, not Blender's Bezier curve fit
  (``BKE_curve_decimate_bezt_array``); Blender then recalculates the handles.
* ``euler_single`` and ``euler_multi`` are Blender's Euler filter
  (``graph_edit.cc``): whole turns removed between consecutive keys of one
  channel, and, where X, Y and Z are keyed at the same frames, each rotation
  re-expressed as the compatible Euler closest to the previous one.
"""

import heapq
import math


def _equal(a, b, threshold):
    return abs(a - b) <= threshold


def clean(points, threshold, selected=None):
    """Indexes of ``points`` ((frame, value) pairs) Blender's Clean Keyframes keeps.

    ``selected`` (one bool per point, default all) limits the rule to those
    keys; the others are kept, as Blender's "only selected keys" clean does.
    """
    if len(points) < 2:
        return list(range(len(points)))
    kept = [0]
    for index in range(1, len(points)):
        if selected is not None and not selected[index]:
            kept.append(index)
            continue
        prev = points[kept[-1]]
        cur = points[index]
        nxt = points[index + 1] if index + 1 < len(points) else None
        if _equal(cur[0], prev[0], threshold):
            if nxt is not None and _equal(cur[0], nxt[0], threshold) and not _equal(nxt[1], prev[1], threshold):
                keep = cur[1] > nxt[1] and not _equal(cur[1], prev[1], threshold)
            else:
                keep = not _equal(cur[1], prev[1], threshold)
        elif nxt is not None:
            keep = not _equal(cur[1], prev[1], threshold) or not _equal(cur[1], nxt[1], threshold)
        else:
            keep = not _equal(cur[1], prev[1], threshold)
        if keep:
            kept.append(index)
    return kept


def smooth(values, handles_left, handles_right):
    """Blender's Smooth Keys on selected keys: (values, left handle values, right handle values)."""
    count = len(values)
    if count < 3:
        return list(values), list(handles_left), list(handles_right)
    new_values, new_left, new_right = list(values), list(handles_left), list(handles_right)
    for i in range(1, count - 1):
        p1 = values[i - 1]
        p2 = values[i - 2] if i - 2 > 0 else p1
        c1 = values[i]
        n1 = values[i + 1]
        n2 = values[i + 2] if i + 2 < count else n1
        y1 = (3 * p2 + 5 * p1 + 2 * c1 + n1 + n2) / 12
        y3 = (p2 + p1 + 2 * c1 + 5 * n1 + 3 * n2) / 12
        new_values[i] = (y1 + y3) / 2
        new_left[i] = handles_left[i] * .7 + y1 * .3
        new_right[i] = handles_right[i] * .7 + y3 * .3
    return new_values, new_left, new_right


def _deviation(points, left, middle, right):
    (x0, y0), (x1, y1), (x2, y2) = points[left], points[middle], points[right]
    if x2 == x0:
        return abs(y1 - y0)
    return abs(y1 - (y0 + (y2 - y0) * (x1 - x0) / (x2 - x0)))


def decimate(points, ratio=None, error=None):
    """Indexes of ``points`` kept after removing the least significant interior keys.

    ``ratio`` removes that share of the interior keys (rounded up, as Blender
    does); ``error`` removes keys while their deviation stays within it. The
    first and last keys always stay.
    """
    count = len(points)
    if count < 3:
        return list(range(count))
    budget = math.ceil((count - 2) * ratio) if ratio is not None else count
    left = {index: index - 1 for index in range(1, count)}
    right = {index: index + 1 for index in range(count - 1)}
    alive = [True] * count
    heap = [(_deviation(points, i - 1, i, i + 1), i, i - 1, i + 1) for i in range(1, count - 1)]
    heapq.heapify(heap)
    removed = 0
    while heap and removed < budget:
        cost, index, before, after = heapq.heappop(heap)
        if not alive[index] or left[index] != before or right[index] != after:
            continue  # A neighbour changed since this cost was computed.
        if error is not None and cost > error:
            break
        alive[index] = False
        removed += 1
        right[before], left[after] = after, before
        for neighbour in (before, after):
            if 0 < neighbour < count - 1 and alive[neighbour]:
                heapq.heappush(heap, (_deviation(points, left[neighbour], neighbour, right[neighbour]), neighbour,
                                      left[neighbour], right[neighbour]))
    return [index for index in range(count) if alive[index]]


def euler_single(values):
    """Per-key offsets (whole turns) Blender's single-channel Euler filter adds; empty for <= 2 keys."""
    if len(values) <= 2:
        return [0.0] * len(values)
    offsets, previous = [0.0], values[0]
    for value in values[1:]:
        offset = 0.0
        sign = 1.0 if previous > value + offset else -1.0
        while sign * (previous - (value + offset)) >= math.pi:
            offset += sign * 2 * math.pi
        offsets.append(offset)
        previous = value + offset
    return offsets


def euler_multi(rotations, compatible):
    """Rotations re-expressed key by key as Eulers compatible with the previous (``compatible(euler, prev)``)."""
    if len(rotations) < 2:
        return [list(rotation) for rotation in rotations]
    result = [list(rotations[0])]
    for rotation in rotations[1:]:
        result.append(list(compatible(rotation, result[-1])))
    return result
