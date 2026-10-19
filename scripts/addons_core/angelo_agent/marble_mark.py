# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo raven mark as coloured triangles for the chat's GPU canvas. No ``bpy``: host-testable.

The chat's waiting animation (``marble_wait``) carves the mark out of a block of marble, so the mark is drawn
with the chat's own GPU shapes, never as a picture. Nothing in the add-on triangulated the mark before (the
native toolbar rasterizes the SVG with NanoSVG), so it is done here once per process and cached (``mesh``).

* The path data and paints are ``release/branding/angelo/angelo-mark-color.svg``'s, copied verbatim because
  the installed app does not ship ``release/``; ``test_native_marble_waiting`` keeps them equal to the file.
* Paths are absolute ``M``/``L``/``C``/``S``/``Z`` commands; cubic curves are flattened into chords of at most
  ``CHORD`` path units (a fraction of a pixel at the 112 px hero) and each closed outline is ear-clipped.
* A linear gradient is affine in position between two stops, so every triangle is split along each stop's line
  (``_split``) and coloured per vertex: the GPU's vertex interpolation then reproduces the gradient exactly.

Coordinates are "mark units": the SVG's 1254 x 1254 viewBox with y pointing **up** (GPU convention), the SVG
group's ``translate(-313 -306) scale(1.5)`` applied. Colours are the SVG's hex values over 255, as every chat
colour is (``ui_tokens``).
"""

import math
import re
from functools import lru_cache

SIZE = 1254.0
_GROUP_SCALE, _GROUP_X, _GROUP_Y = 1.5, -313.0, -306.0  # The SVG group's transform.
CHORD = 10.0  # Longest chord of a flattened curve, in path units (1.5 mark units).

# (name, path data, paint). A paint is a hex colour or the name of a linear gradient in GRADIENTS.
PARTS = (
    ("helmet", "M450 232 C484 272 562 278 641 307 C697 328 735 357 764 388 C838 410 889 454 925 514 C960 574 974 "
               "667 963 758 C959 818 948 860 920 886 C813 933 674 957 536 944 C426 935 351 922 320 861 C282 791 "
               "288 707 310 637 C337 548 392 467 504 393 C477 373 464 348 456 330 L526 355 C498 334 475 285 450 "
               "232 Z", "helmet"),
    ("feather", "M456 330 C482 348 536 367 634 405 C576 386 548 388 504 393 C477 373 464 348 456 330 Z", "feather"),
    ("visor", "M646 522 C714 504 779 488 830 479 C882 470 907 484 926 520 C958 580 968 695 944 775 C937 817 905 "
              "850 862 864 L734 897 C649 921 523 936 484 889 C454 852 448 800 457 738 C464 657 517 610 594 563 "
              "C609 548 627 534 646 522 Z", "visor"),
    ("eye_left", "M604 629 C577 634 571 653 576 679 L590 742 C595 764 608 778 628 775 C652 770 665 754 660 729 "
                 "L646 657 C642 637 626 625 604 629 Z", "#e6ffef"),
    ("eye_right", "M854 569 C834 573 830 590 833 609 L849 678 C853 698 865 711 879 706 C894 702 902 690 899 669 "
                  "L885 599 C881 578 870 566 854 569 Z", "#e6ffef"),
    ("beak", "M710 779 C738 747 765 711 790 709 C839 700 912 774 941 858 C943 864 939 870 928 866 C853 859 772 "
             "850 718 833 C694 827 688 808 701 791 Z", "beak"),
    ("beak_shade", "M701 797 C770 802 845 813 938 860 L928 866 C853 859 772 850 718 833 C699 828 691 813 701 797 Z",
     "#d66f55"),
)
# name -> ((x1, y1, x2, y2) in path units, ((offset, hex), ...)); gradientUnits="userSpaceOnUse".
GRADIENTS = {
    "helmet": ((340, 925, 800, 350), ((0.0, "#087787"), (0.55, "#27bdbf"), (1.0, "#a1f4d6"))),
    "feather": ((460, 380, 610, 345), ((0.0, "#0795a6"), (1.0, "#36c5c0"))),
    "visor": ((470, 875, 860, 520), ((0.0, "#151c23"), (1.0, "#29333a"))),
    "beak": ((720, 850, 800, 710), ((0.0, "#ef8869"), (1.0, "#ffb79b"))),
}
EYE_CENTRES_PATH = {"eye_left": (618.0, 702.0), "eye_right": (866.0, 637.0)}  # In path units.
_TOKEN = re.compile(r"[MLCSZmlcsz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def hex_colour(value, alpha=1.0):
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)) + (alpha,)


def to_mark(point):
    """A path-unit point (SVG, y down) in mark units (y up)."""
    return (_GROUP_X + _GROUP_SCALE * point[0], SIZE - (_GROUP_Y + _GROUP_SCALE * point[1]))


def eye_centre(name):
    return to_mark(EYE_CENTRES_PATH[name])


def _cubic(p0, p1, p2, p3):
    """Points after ``p0`` along a cubic Bézier, at most ``CHORD`` apart along its control polygon."""
    length = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
    steps = max(2, min(32, math.ceil(length / CHORD)))
    out = []
    for step in range(1, steps + 1):
        t = step / steps
        u = 1 - t
        out.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return out


def outlines(data):
    """The closed outlines of absolute path ``data`` (``M``, ``L``, ``C``, ``S``, ``Z``), flattened, in path units.

    Open subpaths are closed. A relative or unsupported command raises ValueError: the mark uses none.
    """
    tokens, index, shapes, current, last_control = _TOKEN.findall(data), 0, [], [], None

    def numbers(count):
        nonlocal index
        values = [float(value) for value in tokens[index:index + count]]
        if len(values) != count or any(value in "MLCSZmlcsz" for value in tokens[index:index + count]):
            raise ValueError("Malformed path data")
        index += count
        return values

    command = None
    while index < len(tokens):
        if tokens[index] in "MLCSZmlcsz":
            command = tokens[index]
            index += 1
        if command not in ("M", "L", "C", "S", "Z"):
            raise ValueError("Unsupported path command: %r" % command)
        if command == "M":
            if len(current) > 2:
                shapes.append(current)
            current, last_control = [tuple(numbers(2))], None
            command = "L"  # Further pairs after a moveto are linetos.
        elif command == "L":
            current.append(tuple(numbers(2)))
            last_control = None
        elif command == "C":
            x1, y1, x2, y2, x, y = numbers(6)
            current += _cubic(current[-1], (x1, y1), (x2, y2), (x, y))
            last_control = (x2, y2)
        elif command == "S":
            x2, y2, x, y = numbers(4)
            start = current[-1]
            first = (2 * start[0] - last_control[0], 2 * start[1] - last_control[1]) if last_control else start
            current += _cubic(start, first, (x2, y2), (x, y))
            last_control = (x2, y2)
        else:  # Z
            if len(current) > 2:
                shapes.append(current)
            current, last_control, command = [current[0]] if current else [], None, None
    if len(current) > 2:
        shapes.append(current)
    return [_clean(shape) for shape in shapes]


def polyline(data):
    """The flattened points of an open path (the marble's veins), in its own units."""
    tokens = _TOKEN.findall(data)
    return outlines(data + (" Z" if tokens and tokens[-1] not in "Zz" else ""))[0]


def _clean(points):
    out = []
    for point in points:
        if not out or math.dist(out[-1], point) > 1e-6:
            out.append(point)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= 1e-6:
        out.pop()
    return out


def signed_area(points):
    return 0.5 * sum(points[i - 1][0] * points[i][1] - points[i][0] * points[i - 1][1] for i in range(len(points)))


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _inside(point, a, b, c):
    """Whether ``point`` is inside or on the counter-clockwise triangle ``a b c``."""
    return _cross(a, b, point) >= 0 and _cross(b, c, point) >= 0 and _cross(c, a, point) >= 0


def triangulate(points):
    """Ear-clipping of one simple polygon: index triples, counter-clockwise, covering its area exactly.

    Collinear vertices are dropped without a triangle. A polygon that is not simple still ends (the least
    bad ear is clipped once no proper ear is left), so a damaged path degrades instead of hanging the draw.
    """
    order = list(range(len(points)))
    if signed_area(points) < 0:
        order.reverse()
    triangles = []
    while len(order) > 3:
        count = len(order)
        reflex = {order[i] for i in range(count)
                  if _cross(points[order[i - 1]], points[order[i]], points[order[(i + 1) % count]]) < 0}
        clipped = False
        for i in range(count):
            a, b, c = order[i - 1], order[i], order[(i + 1) % count]
            turn = _cross(points[a], points[b], points[c])
            if abs(turn) < 1e-9:  # Collinear: no area to cover.
                del order[i]
                clipped = True
                break
            if turn < 0:
                continue
            if any(_inside(points[r], points[a], points[b], points[c]) for r in reflex if r not in (a, b, c)):
                continue
            triangles.append((a, b, c))
            del order[i]
            clipped = True
            break
        if not clipped:  # Not simple: clip the most convex vertex anyway.
            i = max(range(count), key=lambda k: _cross(points[order[k - 1]], points[order[k]],
                                                       points[order[(k + 1) % count]]))
            triangles.append((order[i - 1], order[i], order[(i + 1) % count]))
            del order[i]
    if len(order) == 3 and abs(_cross(*(points[k] for k in order))) >= 1e-9:
        triangles.append(tuple(order))
    return triangles


def _split(triangle, measure, level):
    """``triangle`` (three points) cut along ``measure(point) == level``: one to three triangles."""
    values = [measure(point) - level for point in triangle]
    below = [k for k in range(3) if values[k] < -1e-9]
    above = [k for k in range(3) if values[k] > 1e-9]
    if not below or not above:
        return [triangle]

    def cut(i, j):
        t = values[i] / (values[i] - values[j])
        a, b = triangle[i], triangle[j]
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    # Each piece replaces vertices of the triangle, in place, by points on its edges: the winding is kept.
    def replaced(**points):
        return tuple(points.get("p%d" % k, triangle[k]) for k in range(3))

    if len(below) + len(above) == 2:  # The third vertex lies on the line: two triangles.
        i, j = below[0], above[0]
        point = cut(i, j)
        return [replaced(**{"p%d" % j: point}), replaced(**{"p%d" % i: point})]
    lone = below[0] if len(below) == 1 else above[0]
    first, second = (k for k in range(3) if k != lone)
    on_first, on_second = cut(lone, first), cut(lone, second)
    return [replaced(**{"p%d" % first: on_first, "p%d" % second: on_second}),
            replaced(**{"p%d" % lone: on_first}),
            replaced(**{"p%d" % lone: on_second, "p%d" % first: on_first})]


def _gradient(name):
    (x1, y1, x2, y2), stops = GRADIENTS[name]
    dx, dy = x2 - x1, y2 - y1
    span = dx * dx + dy * dy

    def measure(point):
        return ((point[0] - x1) * dx + (point[1] - y1) * dy) / span

    colours = [(offset, hex_colour(value)) for offset, value in stops]

    def colour(point):
        t = min(1.0, max(0.0, measure(point)))
        for (start, low), (end, high) in zip(colours, colours[1:]):
            if t <= end:
                f = 0.0 if end <= start else (t - start) / (end - start)
                return tuple(low[k] + (high[k] - low[k]) * f for k in range(4))
        return colours[-1][1]

    return measure, [offset for offset, _ in stops], colour


@lru_cache(maxsize=None)
def mesh(name):
    """``(positions, colours, triangles)`` of one part in mark units: ``positions`` ``(x, y)``, ``colours`` RGBA."""
    data, paint = next((data, paint) for part, data, paint in PARTS if part == name)
    pieces = []
    for outline in outlines(data):
        pieces += [tuple(outline[k] for k in triangle) for triangle in triangulate(outline)]
    if paint.startswith("#"):
        flat = hex_colour(paint)
        colour = lambda point: flat  # noqa: E731
    else:
        measure, levels, colour = _gradient(paint)
        for level in levels:
            pieces = [part for piece in pieces for part in _split(piece, measure, level)]
    positions, colours, triangles = [], [], []
    for piece in pieces:
        start = len(positions)
        for point in piece:
            positions.append(to_mark(point))
            colours.append(colour(point))
        triangles.append((start, start + 2, start + 1))  # Counter-clockwise again once y points up.
    return tuple(positions), tuple(colours), tuple(triangles)


PART_NAMES = tuple(name for name, _, _ in PARTS)
