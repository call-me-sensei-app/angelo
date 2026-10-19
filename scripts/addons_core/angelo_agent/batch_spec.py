# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``batch``: one unit of objects repeated as a pattern (pure Python, no bpy).

A batch names a unit (``sources``: one to eight staged objects, created earlier
in the program or declared) and one placement of it:

* ``array``: up to three nested rows, each a ``step`` and a ``count`` that
  includes the unit itself, like stacked Array modifiers;
* ``at``: explicit positions for the unit's anchor (the first source's
  origin), with optional per-instance ``rotations`` and ``scales``;
* ``along``: positions at a ``spacing`` or as a ``count`` along a polyline
  (two points are an edge; a curve is given by its points), optionally turned
  to the path's direction;
* ``scatter``: ``count`` positions inside a box, one per slice of its longest
  axis, from a seed.

Steps, positions, paths, the scatter box and offsets are world-space, like the
locations a program gives the objects it creates. ``mirror`` adds the mirror
image of every instance across the plane through the origin of the unit's
parent, along that parent's axis (the world's when the unit has no parent), and
``vary`` gives every instance a rotation, scale and offset drawn from ranges
with a seed. The unit itself is the first instance (moved to the first position); the
others are copies that share each source's data (``linked``) or own a copy.

Everything here is a pure function of the operation: how many instances it
makes, their transforms, the copies' names and aliases and the checks it
implies are known before anything is staged, so a plan is validated, counted
and approved as written. The kernel (``action_batch``) only composes these
transforms with the staged sources.

Counting. A batch is one operation of the program's 32; what it expands to is
bounded separately (``MAX_BATCH_COPIES`` per batch, ``MAX_PROGRAM_COPIES`` per
program), because every copy is an object the stage holds, verifies and
commits. Random numbers come from a stateless generator (``unit``), so the
same seed gives the same scene on every platform and Python version.
"""

import math
import re

from .action_schema_types import POINTER, array, boolean, enum, integer, nullable, number, obj, vector

MAX_BATCH_PARTS = 8
# Objects one batch may create, and all batches of one program together (doc/angelo/native_coverage.rst,
# "Batch work bounds": staging, verification and Undo are linear in them).
MAX_BATCH_COPIES = 1024
MAX_PROGRAM_COPIES = 1024
MAX_PATH_POINTS = 64
ANGLE = 6.2832  # A full turn, to four places: every digit here is in the schema a model reads.
_RANGE = lambda limit: array(vector(limit=limit), 2, 2)  # noqa: E731 ([low, high], per axis)

BATCH_SCHEMA = obj({
    'sources': dict(array(POINTER, 1, MAX_BATCH_PARTS), uniqueItems=True),
    'linked': boolean(True),
    'array': nullable(array(obj({'step': vector(), 'count': integer(2, MAX_BATCH_COPIES + 1)}, ['step', 'count']), 1, 3)),
    'at': nullable(array(vector(), 1, MAX_BATCH_COPIES + 1)),
    'rotations': nullable(array(vector(limit=ANGLE), 1, MAX_BATCH_COPIES + 1)),
    'scales': nullable(array(vector(limit=1000), 1, MAX_BATCH_COPIES + 1)),
    'along': nullable(obj({'points': array(vector(), 2, MAX_PATH_POINTS), 'spacing': nullable(number(.001, 100000)),
                           'count': nullable(integer(2, MAX_BATCH_COPIES + 1)), 'align': boolean(False)}, ['points'])),
    'scatter': nullable(obj({'count': integer(1, MAX_BATCH_COPIES + 1), 'min': vector(), 'max': vector()},
                            ['count', 'min', 'max'])),
    'mirror': nullable(enum(['X', 'Y', 'Z'])),
    'vary': nullable(obj({'seed': integer(0, 2147483647, 0), 'rotation': nullable(_RANGE(ANGLE)),
                          'scale': nullable(_RANGE(1000)), 'offset': nullable(vector(limit=1000))})),
}, ['sources'])
PLACEMENTS = ('array', 'at', 'along', 'scatter')
# What the schema cannot say, for every model that reads it: the Codex program reference (codex_reference) and
# an exact lookup (action_catalog.catalog). In a recorded footbridge one-shot GPT-6.1-Sol, given only
# ``at?:Vec3[]``, wrote at as offsets for three copies of a post it had just placed; the unit moved to the first
# and the bridge had three posts, two off the deck.
# It counts as copies() does: every instance but the first, mirror images included, copies each source
# ("n positions make n-1 copies" alone was wrong with mirror or several sources).
# The last sentence is the order program_rules.linked_edits enforces, and what copies take (action_batch): on the
# recorded runs one of five GPT-6.1-Sol arched-footbridge turns gave the posts' unit its material after
# the batch, was refused (LINKED_EDIT) and spent a round writing the program again. It is also the field's
# description in Claude's plan, where batch is exact (plan_schema).
# "Pier, Pier.001": a name without a number keeps it and takes Blender's copy suffix (unit_names), as Shift+D and an
# MCP script's Object.copy() give it.
PLACEMENT_NOTE = ("batch: the unit (sources) is the first instance. at, along and scatter are world positions of "
                  "its anchor (the first source's origin): the unit moves to the first, so n positions make n-1 "
                  "more instances (2n-1 with mirror), each copying every source. array steps are offsets from the "
                  "unit, and each count includes it. Copies continue the unit's name (Post 1, Post 2; Pier, Pier.001) "
                  "and alias ($post_2, $post_3) and take what it has when the batch runs; linked ones (the default) "
                  "share its data and materials, so give the unit its material and modifiers before the batch.")
_NUMBERED = re.compile(r'^(.*?)(\d+)$')
_MASK = (1 << 64) - 1


def unit(seed, instance, channel):
    """A reproducible number in [0, 1) for one instance and channel of a seed (SplitMix64 of their mix)."""
    z = (seed * 0x9E3779B97F4A7C15 + instance * 0xBF58476D1CE4E5B9 + channel * 0x94D049BB133111EB + 0x9E3779B97F4A7C15) & _MASK
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
    return ((z ^ (z >> 31)) >> 11) / 9007199254740992.0


def _path_positions(along):
    """Positions and headings along a polyline, at a spacing or as an even count including both ends."""
    points = along['points']
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    if total < 1e-9:
        raise ValueError('batch along needs a path longer than zero')
    if along['count'] is not None:
        distances = [total * index / (along['count'] - 1) for index in range(along['count'])]
    else:
        steps = int(math.floor(total / along['spacing'] + 1e-6))
        if steps > MAX_BATCH_COPIES:
            raise ValueError('batch BATCH_TOO_LARGE: this spacing puts more than %d instances on the path'
                             % (MAX_BATCH_COPIES + 1))
        distances = [along['spacing'] * index for index in range(steps + 1)]
    placed, segment, start = [], 0, 0.0
    for distance in distances:
        while segment < len(lengths) - 1 and distance > start + lengths[segment] + 1e-9:
            start += lengths[segment]
            segment += 1
        a, b = points[segment], points[segment + 1]
        factor = 0.0 if lengths[segment] < 1e-12 else min(1.0, max(0.0, (distance - start) / lengths[segment]))
        heading = math.atan2(b[1] - a[1], b[0] - a[0])
        placed.append(([a[axis] + (b[axis] - a[axis]) * factor for axis in range(3)], heading))
    return placed


def _scatter_positions(scatter, seed):
    low, high, count = scatter['min'], scatter['max'], scatter['count']
    extent = [high[axis] - low[axis] for axis in range(3)]
    longest = max(range(3), key=lambda axis: (extent[axis], -axis))
    positions = []
    for index in range(count):
        point = [low[axis] + extent[axis] * unit(seed, index, axis) for axis in range(3)]
        # One instance per slice of the longest axis: spread along it, random across it.
        point[longest] = low[longest] + extent[longest] * (index + unit(seed, index, longest)) / count
        positions.append(point)
    return positions


def _uniform(pair):
    return all(len(set(value)) == 1 for value in pair)


def instances(params):
    """The instances of a canonical batch, in order; the first is the unit itself.

    Each is ``{"position" | "offset", "rotation", "scale", "mirror"}``: an
    absolute anchor ``position`` or an ``offset`` from the unit's own place, a
    rotation (XYZ Euler) and scale factors applied about the anchor, and whether
    it is a mirror image. ``scale`` is None when the unit keeps its size.
    """
    vary = params['vary'] or {'seed': 0, 'rotation': None, 'scale': None, 'offset': None}
    seed = vary['seed']
    if params['array'] is not None:
        placed = [{'offset': [0.0, 0.0, 0.0]}]
        for level in params['array']:
            placed = [{'offset': [base['offset'][axis] + level['step'][axis] * index for axis in range(3)]}
                      for index in range(level['count']) for base in placed]
    elif params['at'] is not None:
        placed = [{'position': list(position)} for position in params['at']]
        for key, field in (('rotation', 'rotations'), ('scale', 'scales')):
            if params[field] is not None:
                for item, value in zip(placed, params[field]):
                    item[key] = list(value)
    elif params['along'] is not None:
        placed = [dict({'position': position}, **({'rotation': [0.0, 0.0, heading]} if params['along']['align'] else {}))
                  for position, heading in _path_positions(params['along'])]
    elif params['scatter'] is not None:
        placed = [{'position': position} for position in _scatter_positions(params['scatter'], seed)]
    else:
        placed = [{'offset': [0.0, 0.0, 0.0]}]
    if params['mirror'] is not None:  # Every instance first, then the mirror image of each, in the same order.
        placed = placed + [dict(item, mirror=True) for item in placed]
    result = []
    for index, item in enumerate(placed):
        rotation, scale = item.get('rotation'), item.get('scale')
        if vary['rotation'] is not None:
            low, high = vary['rotation']
            drawn = [low[axis] + (high[axis] - low[axis]) * unit(seed, index, 3 + axis) for axis in range(3)]
            rotation = drawn if rotation is None else [rotation[axis] + drawn[axis] for axis in range(3)]
        if vary['scale'] is not None:
            low, high = vary['scale']
            # A range of equal components is one factor for the whole unit; otherwise each axis has its own.
            draws = [unit(seed, index, 6)] * 3 if _uniform(vary['scale']) else [unit(seed, index, 6 + axis) for axis in range(3)]
            drawn = [low[axis] + (high[axis] - low[axis]) * draws[axis] for axis in range(3)]
            scale = drawn if scale is None else [scale[axis] * drawn[axis] for axis in range(3)]
        entry = {'rotation': rotation, 'scale': scale, 'mirror': bool(item.get('mirror'))}
        key = 'position' if 'position' in item else 'offset'
        entry[key] = list(item[key])
        if vary['offset'] is not None:
            entry[key] = [entry[key][axis] + vary['offset'][axis] * (2 * unit(seed, index, 9 + axis) - 1) for axis in range(3)]
        result.append(entry)
    return result


def instance_count(params):
    """How many instances a canonical batch has (the unit included), without building them."""
    if params['array'] is not None:
        count = math.prod(level['count'] for level in params['array'])
    elif params['at'] is not None:
        count = len(params['at'])
    elif params['along'] is not None:
        count = len(_path_positions(params['along']))
    elif params['scatter'] is not None:
        count = params['scatter']['count']
    else:
        count = 1
    return count * (2 if params['mirror'] is not None else 1)


def copies(params):
    """Objects a canonical batch creates: every instance but the unit itself, for every source."""
    return (instance_count(params) - 1) * len(params['sources'])


def canonical(result, raw):
    """Cross-field rules of a schema-validated batch."""
    params = result['parameters']
    chosen = [key for key in PLACEMENTS if params[key] is not None]
    if len(chosen) > 1 or (not chosen and params['mirror'] is None):
        raise ValueError('batch takes one placement (array, at, along or scatter), or mirror alone')
    for field in ('rotations', 'scales'):
        if params[field] is not None and (params['at'] is None or len(params[field]) != len(params['at'])):
            raise ValueError('batch %s gives one value per position of at' % field)
    if params['scales'] is not None and any(abs(value) < .000001 for scale in params['scales'] for value in scale):
        raise ValueError('batch scale factors must be nonzero')
    if params['along'] is not None and (params['along']['spacing'] is None) == (params['along']['count'] is None):
        raise ValueError('batch along takes a spacing or a count')
    if params['scatter'] is not None and any(low > high for low, high in zip(params['scatter']['min'], params['scatter']['max'])):
        raise ValueError('batch scatter needs min at or below max on every axis')
    vary = params['vary']
    if vary is not None:
        if vary['rotation'] is None and vary['scale'] is None and vary['offset'] is None:
            raise ValueError('batch vary takes a rotation range, a scale range or an offset')
        for key in ('rotation', 'scale'):
            if vary[key] is not None and any(low > high for low, high in zip(*vary[key])):
                raise ValueError('batch vary %s is [low, high] with low at or below high on every axis' % key)
        if vary['scale'] is not None and any(value < .000001 for value in vary['scale'][0]):
            raise ValueError('batch vary scale factors must be positive')
        if vary['offset'] is not None and any(value < 0 for value in vary['offset']):
            raise ValueError('batch vary offset is the largest move along each axis, not negative')
    count = instance_count(params)
    if count < 2:
        raise ValueError('batch makes no copy: one instance is the unit itself')
    for source in params['sources']:
        if source.startswith('$') and len(source) + len(str(count)) > 32:
            raise ValueError('batch: shorten the alias %s so its copies (%s_2, ...) stay within 32 characters'
                             % (source, source))
    if copies(params) > MAX_BATCH_COPIES:
        raise ValueError('batch BATCH_TOO_LARGE: %d instances of %d source(s) are %d new objects (limit %d per batch)'
                         % (count, len(params['sources']), copies(params), MAX_BATCH_COPIES))
    return params


def moves_sources(params):
    """Whether the unit itself is moved or varied (then an existing source is written, not only read)."""
    return any(params[key] is not None for key in ('at', 'along', 'scatter', 'vary'))


def moved(operation):
    """The sources whose own transform a canonical operation changes: those of a batch that places or varies them."""
    params = operation["parameters"]
    return list(params["sources"]) if operation["kind"] == "batch" and moves_sources(params) else []


def _numbered(name):
    """``(stem, number, width)`` of a name: its trailing number, or Blender's ``.000`` for a name without one."""
    match = _NUMBERED.match(name)
    return (match.group(1), int(match.group(2)), len(match.group(2))) if match else (name + '.', 0, 3)


def unit_names(names, total):
    """``(names of the copies, stride)`` for every source when a batch has ``total`` instances.

    A name that ends in a number continues it, keeping its width ('Post 01' ->
    'Post 02', ...); any other name keeps itself and takes Blender's copy suffix
    ('Pier' -> 'Pier.001', 'Pier.002', ...), the names Shift+D or a script's
    ``Object.copy()`` give, so a name the person asked for stays that name
    (a brief's "'Bridge Abutments' are blocks under both deck ends" made 'Bridge
    Abutments 2' before, a different name). Sources that share a stem share its
    numbering: every instance takes the next block after the highest ('Pier 1',
    'Pier 2' -> 'Pier 3', 'Pier 4', then 'Pier 5', 'Pier 6'), so a source's
    copies are ``stride`` (the sources of its stem) apart.
    """
    parsed = [_numbered(name) for name in names]
    result = []
    for index, (stem, number, _) in enumerate(parsed):
        group = sorted((item[1], position) for position, item in enumerate(parsed) if item[0] == stem)
        stride, rank, highest = len(group), group.index((number, index)), group[-1][0]
        width = max(item[2] for item in parsed if item[0] == stem) or len(str(highest + (total - 1) * stride))
        result.append((['%s%0*d' % (stem, width, highest + step * stride + rank + 1) for step in range(total - 1)], stride))
    return result


def copy_names(name, total, first=None, stride=1):
    """Names of one source's copies: from ``first`` every ``stride``, or continuing ``name`` itself."""
    if first is None:
        return unit_names([name], total)[0][0]
    stem, number, width = _numbered(first)
    return ['%s%0*d' % (stem, width, number + step * stride) for step in range(total - 1)]


def copy_aliases(source, ordinal, part, total):
    """Aliases of a source's copies: ``$post_2``... for ``$post``, a host name for a declared object."""
    stem = source[1:] if source.startswith('$') else 'copy%d_%d' % (ordinal, part)
    return ['$%s_%d' % (stem, index) for index in range(2, total + 1)]


def aliases(operation, ordinal):
    """Every alias a canonical batch defines (``ordinal``: its place among the program's batches, from 1)."""
    params = operation['parameters']
    total = instance_count(params)
    return [alias for part, source in enumerate(params['sources']) for alias in copy_aliases(source, ordinal, part, total)]


def program_aliases(operation, state):
    """The aliases a canonical batch adds to its program; ``state`` counts the program's batches and copies."""
    state["batches"] = state.get("batches", 0) + 1
    state["copies"] = state.get("copies", 0) + copies(operation["parameters"])
    if state["copies"] > MAX_PROGRAM_COPIES:
        raise ValueError("batch PROGRAM_TOO_LARGE: this program's batches would create %d objects (limit %d); split "
                         "them across programs" % (state["copies"], MAX_PROGRAM_COPIES))
    return aliases(operation, state["batches"])


# Later kinds that leave a copies check true: none of them removes, renames or converts objects.
COPIES_BREAKERS = frozenset({"object_rename", "object_delete", "object_join", "object_convert", "mesh_separate",
                             "operator_run"})


def checks(operations, index, kinds):
    """``(check, kinds that keep it)`` pairs for ``operations[index]`` when it is a batch (derived_everyday)."""
    operation = operations[index]
    if operation["kind"] != "batch":
        return []
    ordinal = sum(1 for item in operations[:index + 1] if item["kind"] == "batch")
    params = operation["parameters"]
    total = instance_count(params)
    result = []
    for part, source in enumerate(params["sources"]):
        check = {"kind": "copies", "target": source, "count": total - 1, "linked": params["linked"]}
        if not source.startswith("$"):
            check["alias"] = "$copy%d_%d" % (ordinal, part)
        result.append((check, kinds - COPIES_BREAKERS))
    return result


def evaluate_copies(reader, source, check):
    """The ``copies`` check: ``count`` copies of ``source`` exist, of its type, sharing its data when linked.

    Copies are found by the aliases a batch gives them when the context has
    them (a staged program), else by name: from ``first`` every ``stride``
    when the check states them (a result's ``parts`` do), else continuing the
    source's own name.
    """
    from .validation_reader import context_value, resolve_target
    count = check["count"]
    reader.spend(count)
    stem = check.get("alias") or (check["target"] if check["target"].startswith("$") else None)
    found = [resolve_target(reader.context, "%s_%d" % (stem, index)) for index in range(2, count + 2)] if stem else []
    if not found or any(obj is None for obj in found):
        names = context_value(reader.context, "logical_names", None) or {}
        wanted = copy_names(names.get(check["target"], source.name), count + 1, check.get("first"), check.get("stride", 1))
        by_name = {obj.name: obj for obj in reader.scene_objects()}
        found = [by_name.get(name) for name in wanted]
    present = [obj for obj in found if obj is not None]
    same_type = all(obj.type == source.type for obj in present)
    shared = all(obj.data == source.data for obj in present)
    observed = {"copies": len(present), "linked": shared and bool(present) and source.data is not None}
    if present:
        observed.update(first=present[0].name[:64], last=present[-1].name[:64])
    return len(present) == count and same_type and (shared or not check["linked"]), observed
