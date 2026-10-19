# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The block of marble the raven is carved from: its chips, the carving schedule and the chips in flight.

No ``bpy``: host-testable and measured by ``benchmark_blender_chat_waiting``. ``marble_wait`` decides *when*
chips fall (the steady schedule, a chisel tap, the reveal); this module decides *which* and *how*, and builds
the triangles ``marble_draw`` hands to the GPU, in mark units (``marble_mark``: 1254 square, y up).

* **Chips** are a jittered grid of triangles over the concept's block (``BLOCK``, its rounded corners
  chamfered), ordered outside-in with a little randomness, so the carving works inward. Each chip is a pale
  stone gradient (``STONE``), one shade per facet, with a slightly darker edge (``EDGE``) and, on the hero, the
  pieces of a few grey veins (``VEINS``) that cross it, so a vein falls with its chip.
* **Schedule**: ``steady_count(elapsed)`` approaches ``ASYMPTOTE`` of the chips (72%) as ``1 - exp(-t / TAU)``,
  so chips fall steadily, slow down as the block thins, and a long wait never runs out of stone. A tap knocks
  off ``TAP_SHARE`` of the chips at once, never past ``CAP`` (80%); the steady schedule then waits for it.
* **Motion** is analytic in time (no integration), so a frame depends only on its clock: a chip falls outward
  under gravity, turning and fading for ``FALL_SECONDS``; with reduced motion it fades in place instead
  (``FADE_SECONDS``), with no dust and no shake.

Randomness is a fixed-seed Park-Miller generator advanced only by events, so the same events give the same
frames (the GUI suite and the benchmark rely on it).
"""

import math
from collections import namedtuple
from functools import lru_cache

from .marble_mark import SIZE, polyline

BLOCK = (92.0, SIZE - 1160.0, 1070.0, 1120.0)  # left, bottom, width, height (the concept's 92, 40, 1070, 1120).
CORNER = 120.0
CENTRE = (627.0, SIZE - 600.0)  # Carving works towards this point.
ASYMPTOTE, TAU, CAP, TAP_SHARE = 0.72, 22.0, 0.8, 0.05
FALL_SECONDS, FADE_SECONDS, GRAVITY = 0.9, 0.6, 900.0
DUST_SECONDS, DUST_GRAVITY, MAX_DUST = 0.8, 200.0, 60
SHAKE_UNITS, SHAKE_DECAY = 18.0, 6.0  # Mark units at full strength; strength lost per second.
STONE = ((0.0, (0.965, 0.969, 0.961)), (0.7, (0.890, 0.906, 0.898)), (1.0, (0.788, 0.816, 0.808)))
STONE_CENTRE, STONE_RADIUS = (564.3, SIZE - 501.6), 940.5  # The concept's radial gradient (45%, 40%, r 75%).
EDGE = (0.27, 0.35, 0.35)
EDGE_MIX = 0.12  # A hint of an edge, as the concept's 16% stroke: the stone reads as stone, not as a mesh.
VEIN = (0.663, 0.714, 0.706, 0.5)
DUST = (0.910, 0.925, 0.918)
# (path data in the concept's y-down units, width in mark units).
VEINS = (("M60 300 C320 260 420 520 700 470 S1100 620 1240 560", 20.0),
         ("M120 900 C380 760 560 980 820 840 S1080 760 1200 900", 15.0),
         ("M300 60 C340 300 260 520 420 700 S520 1060 470 1220", 13.0),
         ("M880 120 C820 360 980 520 900 780", 12.0))

Mesh = namedtuple("Mesh", "positions colours indices")  # positions are (x, y, 0); colours RGBA; index triples.
Chip = namedtuple("Chip", "centre mesh")
Flight = namedtuple("Flight", "chip start vx vy spin")
Speck = namedtuple("Speck", "x y vx vy start radius")
EMPTY = Mesh((), (), ())
SEED = 7  # The concept page's seed.


class Random:
    """Park-Miller minimal standard generator, as the concept page's; deterministic per seed."""

    def __init__(self, seed):
        self.state = seed

    def __call__(self):
        self.state = (self.state * 16807) % 2147483647
        return self.state / 2147483647


def _stone(point, shade):
    t = min(1.0, math.dist(point, STONE_CENTRE) / STONE_RADIUS)
    for (start, low), (end, high) in zip(STONE, STONE[1:]):
        if t <= end:
            f = (t - start) / (end - start)
            return tuple(min(1.0, (low[k] + (high[k] - low[k]) * f) * shade) for k in range(3)) + (1.0,)
    return STONE[-1][1] + (1.0,)


def _grid(columns, rows, random):
    left, bottom, width, height = BLOCK
    step_x, step_y = width / columns, height / rows
    chamfer = CORNER * (1 - math.sqrt(0.5))
    points = []
    for j in range(rows + 1):
        line = []
        for i in range(columns + 1):
            edge = i in (0, columns) or j in (0, rows)
            jitter_x = 0.0 if edge else (random() - 0.5) * step_x * 0.55
            jitter_y = 0.0 if edge else (random() - 0.5) * step_y * 0.55
            x, y = left + i * step_x + jitter_x, bottom + j * step_y + jitter_y
            if i in (0, columns) and j in (0, rows):  # A corner moves onto the rounded corner's arc.
                x += chamfer if i == 0 else -chamfer
                y += chamfer if j == 0 else -chamfer
            line.append((x, y))
        points.append(line)
    return points


def _inset(triangle, distance):
    """The triangle with every edge moved ``distance`` inwards (scaled about its incentre), or None."""
    a, b, c = triangle
    la, lb, lc = math.dist(b, c), math.dist(c, a), math.dist(a, b)
    perimeter = la + lb + lc
    area = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
    if perimeter <= 0 or area <= 0:
        return None
    radius = 2 * area / perimeter
    if radius <= distance:
        return None
    centre = ((la * a[0] + lb * b[0] + lc * c[0]) / perimeter, (la * a[1] + lb * b[1] + lc * c[1]) / perimeter)
    k = (radius - distance) / radius
    return tuple((centre[0] + (p[0] - centre[0]) * k, centre[1] + (p[1] - centre[1]) * k) for p in triangle)


def _clip(start, end, triangle):
    """The part of segment ``start``-``end`` inside the counter-clockwise ``triangle`` (Cyrus-Beck), or None."""
    low, high = 0.0, 1.0
    dx, dy = end[0] - start[0], end[1] - start[1]
    for k in range(3):
        a, b = triangle[k], triangle[(k + 1) % 3]
        nx, ny = -(b[1] - a[1]), b[0] - a[0]  # Points inwards for a counter-clockwise triangle.
        numerator = nx * (start[0] - a[0]) + ny * (start[1] - a[1])
        denominator = nx * dx + ny * dy
        if abs(denominator) < 1e-12:
            if numerator < 0:
                return None
            continue
        t = -numerator / denominator
        if denominator > 0:
            low = max(low, t)
        else:
            high = min(high, t)
        if low > high:
            return None
    return ((start[0] + dx * low, start[1] + dy * low), (start[0] + dx * high, start[1] + dy * high))


def _vein_lines():
    lines = []
    for data, width in VEINS:
        points = [(x, SIZE - y) for x, y in polyline(data)]
        lines += [(points[k], points[k + 1], width) for k in range(len(points) - 1)]
    return lines


def _chip_mesh(triangle, shade, inset, veins):
    """One chip's triangles: the darker edge, the inset face, then its vein pieces."""
    positions, colours, indices = [], [], []

    def add(points, colour_of):
        start = len(positions)
        for point in points:
            positions.append((point[0], point[1], 0.0))
            colours.append(colour_of(point))
        for k in range(1, len(points) - 1):
            indices.append((start, start + k, start + k + 1))

    face = _inset(triangle, inset) if inset else None
    if face is not None:
        add(triangle, lambda p: tuple(c + (EDGE[k] - c) * EDGE_MIX
                                      for k, c in enumerate(_stone(p, shade)[:3])) + (1.0,))
        add(face, lambda p: _stone(p, shade))
    else:
        add(triangle, lambda p: _stone(p, shade))
    low_x, high_x = min(p[0] for p in triangle), max(p[0] for p in triangle)
    low_y, high_y = min(p[1] for p in triangle), max(p[1] for p in triangle)
    for start, end, width in veins:
        if (max(start[0], end[0]) < low_x or min(start[0], end[0]) > high_x
                or max(start[1], end[1]) < low_y or min(start[1], end[1]) > high_y):
            continue  # Cannot cross this chip: skip the clip.
        piece = _clip(start, end, triangle)
        if piece is None or math.dist(*piece) < 1e-6:
            continue
        (x1, y1), (x2, y2) = piece
        length = math.dist(*piece)
        nx, ny = -(y2 - y1) / length * width / 2, (x2 - x1) / length * width / 2
        add(((x1 - nx, y1 - ny), (x2 - nx, y2 - ny), (x2 + nx, y2 + ny), (x1 + nx, y1 + ny)), lambda p: VEIN)
    return Mesh(tuple(positions), tuple(colours), tuple(indices))


@lru_cache(maxsize=4)
def chips(columns, rows, inset=0.0, veins=False):
    """The block's chips in the order they fall (outside first): ``Chip(centre, mesh)``; built once, cached."""
    random = Random(SEED)
    points = _grid(columns, rows, random)
    lines = _vein_lines() if veins else ()
    out = []
    for j in range(rows):
        for i in range(columns):
            a, b, c, d = points[j][i], points[j][i + 1], points[j + 1][i + 1], points[j + 1][i]
            for triangle in (((a, b, c), (a, c, d)) if (i + j) % 2 else ((a, b, d), (b, c, d))):
                centre = (sum(p[0] for p in triangle) / 3, sum(p[1] for p in triangle) / 3)
                order = math.dist(centre, CENTRE) + random() * 260
                shade = 1 + (random() - 0.5) * 0.05
                out.append((order, Chip(centre, _chip_mesh(triangle, shade, inset, lines))))
    out.sort(key=lambda item: -item[0])
    return tuple(chip for _, chip in out)


def merge(meshes):
    positions, colours, indices = [], [], []
    for mesh in meshes:
        start = len(positions)
        positions += mesh.positions
        colours += mesh.colours
        indices += [(a + start, b + start, c + start) for a, b, c in mesh.indices]
    return Mesh(positions, colours, indices)


def steady_count(total, elapsed):
    """How many of ``total`` chips the steady schedule has removed after ``elapsed`` seconds of waiting."""
    return int(total * ASYMPTOTE * (1 - math.exp(-max(0.0, elapsed) / TAU)))


def steady_time(total, count):
    """When (seconds of waiting) the steady schedule removes chip number ``count``, or None: never."""
    share = count / (total * ASYMPTOTE)
    return None if share >= 1 else -TAU * math.log(1 - share)


class Block:
    """One block over one mark (the hero or the live row's icon); time is ``time.monotonic`` seconds."""

    def __init__(self, columns, rows, inset=0.0, veins=False, dust=False):
        self.chips = chips(columns, rows, inset, veins)
        self.dust = dust
        self.random = Random(SEED * 31 + 1)
        self.removed = 0
        self.flights, self.specks = [], []
        self.shake = (0.0, -1e9)  # (strength, when)
        self._solid = None

    @property
    def total(self):
        return len(self.chips)

    def drop(self, count, when, strength=0.35):
        """Let the next ``count`` chips fall, starting at ``when``; returns how many fell."""
        count = max(0, min(count, self.total - self.removed))
        for index in range(self.removed, self.removed + count):
            centre = self.chips[index].centre
            angle = math.atan2(centre[1] - CENTRE[1], centre[0] - CENTRE[0])
            speed = 120 + 160 * self.random()
            self.flights.append(Flight(index, when, math.cos(angle) * speed, 120 + 140 * self.random(),
                                       (self.random() - 0.5) * 240))
            if self.dust and len(self.specks) < MAX_DUST:
                for _ in range(3):
                    self.specks.append(Speck(centre[0], centre[1], (self.random() - 0.5) * 220,
                                             60 + self.random() * 120, when, 10 + self.random() * 12))
        self.removed += count
        if count:
            self.shake = max(self.shake, (strength, when), key=lambda item: self.shake_at(when, item))
        return count

    def carve(self, elapsed, started):
        """Drop whatever the steady schedule owes after ``elapsed`` seconds, each at its own scheduled time."""
        while self.removed < steady_count(self.total, elapsed):
            due = steady_time(self.total, self.removed + 1)
            self.drop(1, started + (due if due is not None else elapsed))

    def next_drop(self, started):
        """When (monotonic) the steady schedule drops its next chip, or None when it never will."""
        due = steady_time(self.total, self.removed + 1)
        return None if due is None else started + due

    def tap(self, when):
        """A chisel tap: a bigger burst at once, never past ``CAP``; returns how many chips fell."""
        room = int(self.total * CAP) - self.removed
        return self.drop(min(max(2, round(self.total * TAP_SHARE)), room), when, 1.0)

    def fall_all(self, when):
        """The reveal: every chip still standing falls."""
        return self.drop(self.total - self.removed, when, 1.0)

    def clear(self):
        """Every chip gone, nothing in flight: a block already carved when it is first seen."""
        self.removed, self.flights, self.specks = self.total, [], []

    def shake_at(self, now, shake=None):
        strength, when = shake or self.shake
        return max(0.0, strength - (now - when) * SHAKE_DECAY) if now >= when else 0.0

    def prune(self, now):
        self.flights = [f for f in self.flights if now - f.start < FALL_SECONDS]
        self.specks = [s for s in self.specks if now - s.start < DUST_SECONDS]

    def moving(self, now, reduced):
        """Whether anything in the block changes between frames at ``now``."""
        life = FADE_SECONDS if reduced else FALL_SECONDS
        return (any(now - f.start < life for f in self.flights)
                or (not reduced and (any(now - s.start < DUST_SECONDS for s in self.specks)
                                     or self.shake_at(now) > 0)))

    def solid(self):
        """The chips still standing as one mesh, rebuilt only when a chip falls."""
        if self._solid is None or self._solid[0] != self.removed:
            self._solid = (self.removed, merge(chip.mesh for chip in self.chips[self.removed:]))
        return self._solid[1]

    def offset(self, now, reduced):
        """The standing block's shake at ``now``: a small jitter that dies away, in mark units."""
        amount = 0.0 if reduced else self.shake_at(now) * SHAKE_UNITS
        if not amount:
            return 0.0, 0.0
        frame = int(now * 60)
        return (((frame * 7919) % 101) / 100 - 0.5) * amount, (((frame * 104729) % 97) / 96 - 0.5) * amount

    def flying(self, now, reduced):
        """The chips in flight at ``now``, moved, turned and faded, as one mesh."""
        positions, colours, indices = [], [], []
        life = FADE_SECONDS if reduced else FALL_SECONDS
        for flight in self.flights:
            age = now - flight.start
            if age < 0 or age >= life:
                continue
            chip = self.chips[flight.chip]
            alpha = 1 - age / life
            if reduced:
                dx = dy = turn_sin = 0.0
                turn_cos = 1.0
            else:
                dx, dy = flight.vx * age, flight.vy * age - GRAVITY * age * age / 2
                angle = math.radians(flight.spin * age)
                turn_sin, turn_cos = math.sin(angle), math.cos(angle)
            cx, cy = chip.centre
            start = len(positions)
            for x, y, _ in chip.mesh.positions:
                rx, ry = x - cx, y - cy
                positions.append((cx + rx * turn_cos - ry * turn_sin + dx,
                                  cy + rx * turn_sin + ry * turn_cos + dy, 0.0))
            colours += [(r, g, b, a * alpha) for r, g, b, a in chip.mesh.colours]
            indices += [(a + start, b + start, c + start) for a, b, c in chip.mesh.indices]
        return Mesh(positions, colours, indices)

    def dust_mesh(self, now, reduced):
        """The specks of dust at ``now``: small diamonds that rise, fall and fade (none with reduced motion)."""
        if reduced:
            return EMPTY
        positions, colours, indices = [], [], []
        for speck in self.specks:
            age = now - speck.start
            alpha = 0.8 - age
            if age < 0 or age >= DUST_SECONDS or alpha <= 0:
                continue
            x = speck.x + speck.vx * age
            y = speck.y + speck.vy * age - DUST_GRAVITY * age * age / 2
            r = speck.radius
            start = len(positions)
            positions += [(x, y - r, 0.0), (x + r, y, 0.0), (x, y + r, 0.0), (x - r, y, 0.0)]
            colours += [DUST + (alpha,)] * 4
            indices += [(start, start + 1, start + 2), (start, start + 2, start + 3)]
        return Mesh(positions, colours, indices)
