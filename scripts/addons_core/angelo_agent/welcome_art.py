# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The welcome slideshow's pictures (``welcome_slides``), drawn as vectors in the card's own colours.

No image files: every picture is rounded rectangles, strokes and filled polygons in ``card_kit``'s tokens, laid
out in fractions of its box, so it is sharp at any interface scale and adds nothing to the installer. Each one is
a small, simplified picture of the feature as Angelo shows it: the chat building in the 3D view, Media Studio's
prompt and results, a Generation Flow's chained nodes, and the asset library with its shelf. They show Blender's
real, unmirrored screen, so a right-to-left draw places them mirrored but draws them upright
(``canvas_rtl.upright``), like the welcome's layout diagrams. ``alpha`` fades the whole picture (slide changes).
"""

import math

from . import card_kit as kit

FIELD = (0.060, 0.068, 0.082, 1)     # The welcome diagrams' ground.
VIEW = (0.125, 0.140, 0.168, 1)      # A 3D view or canvas inside the picture.
STONE = ((0.66, 0.70, 0.76, 1), (0.45, 0.49, 0.55, 1), (0.32, 0.35, 0.41, 1))    # Cube top, left, right.
MINT = ((0.55, 0.92, 0.88, 1), kit.TEAL, kit.TEAL_PRESSED)
SKY, SUN = (0.16, 0.30, 0.37, 1), kit.AMBER
HILLS = ((0.12, 0.52, 0.50, 1), (0.08, 0.36, 0.37, 1))
NIGHT, MOON = (0.12, 0.13, 0.27, 1), (0.88, 0.90, 0.95, 1)


def _c(color, alpha):
    return (color[0], color[1], color[2], color[3] * alpha)


def poly(points, color):
    """A convex filled polygon (a triangle fan), in region pixels."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    from .canvas_controls import _shader_get
    if len(points) < 3:
        return
    shader = _shader_get()
    batch = batch_for_shader(shader, 'TRIS', {"pos": [(x, y, 0) for x, y in points]},
                             indices=[(0, i, i + 1) for i in range(1, len(points) - 1)])
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


_icons = {}   # (kind, size) -> GPU texture of a concept icon's pixels.


def icon(kind, x, y, size, alpha=1.0):
    """A concept icon from the pixels its button uses (doc/angelo/studio_entry_points.rst, "One icon per concept"):
    ``studio`` is Media Studio's spark frame (``studio_toggle.frame_icon``), ``flows`` the three-node glyph
    (``flow_open.flow_icon``). Drawn as a texture, ``size`` pixels square at ``(x, y)``."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    texels = 64 if kind == "studio" else 32   # The flow glyph is laid out on its 32-unit grid.
    texture = _icons.get(kind)
    if texture is None:
        if kind == "studio":
            from .studio_toggle import frame_icon
            pixels = frame_icon(False, texels)
        else:
            from .flow_open import flow_icon
            pixels = flow_icon(texels)
        texture = _icons[kind] = gpu.types.GPUTexture((texels, texels), format='RGBA16F',
                                                      data=gpu.types.Buffer('FLOAT', [texels * texels * 4], pixels))
    shader = gpu.shader.from_builtin('IMAGE_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": [(x, y), (x + size, y), (x + size, y + size), (x, y + size)],
                                              "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)]}, indices=[(0, 1, 2), (0, 2, 3)])
    gpu.state.blend_set('ALPHA_PREMULT')   # The icons' pixels are premultiplied.
    shader.bind()
    shader.uniform_sampler("image", texture)
    shader.uniform_float("color", (alpha, alpha, alpha, alpha))
    batch.draw(shader)
    gpu.state.blend_set('ALPHA')


class Pen:
    """Draws in fractions of a box: ``p(u, v)`` is ``u`` across and ``v`` up from its bottom-left corner."""

    def __init__(self, view, rect, s, alpha):
        self.view, (self.x, self.y, self.w, self.h), self.s, self.a = view, rect, s, alpha

    def p(self, u, v):
        return self.x + u * self.w, self.y + v * self.h

    def box(self, u, v, du, dv, color, radius=0.0):
        x, y = self.p(u, v)
        self.view.shape(x, y, du * self.w, dv * self.h, _c(color, self.a), radius * self.s)

    def disc(self, u, v, r, color):
        x, y = self.p(u, v)
        r *= self.s
        self.view.shape(x - r, y - r, 2 * r, 2 * r, _c(color, self.a), r)

    def line(self, u1, v1, u2, v2, color, thickness=1.5):
        (x1, y1), (x2, y2) = self.p(u1, v1), self.p(u2, v2)
        self.view.stroke(x1, y1, x2, y2, _c(color, self.a), thickness * self.s)

    def bars(self, u, v, widths, color, gap=0.075, height=0.035):
        """Lines of text as rounded bars, top to bottom from ``v``."""
        for index, width in enumerate(widths):
            self.box(u, v - index * gap - height, width, height, color, 2)

    def cube(self, u, v, size, faces):
        """An isometric cube standing on ``(u, v)``; ``size`` in pixels at scale 1."""
        bx, by = self.p(u, v)
        a = size * self.s
        r, l, up = (0.866 * a, 0.5 * a), (-0.866 * a, 0.5 * a), (0, a)
        add = lambda *vs: (bx + sum(v[0] for v in vs), by + sum(v[1] for v in vs))   # noqa: E731
        top, left, right = (_c(face, self.a) for face in faces)
        poly([add(), add(l), add(l, up), add(up)], left)
        poly([add(), add(up), add(r, up), add(r)], right)
        poly([add(up), add(r, up), add(r, l, up), add(l, up)], top)

    def spark(self, u, v, reach, color):
        """Media Studio's four-point spark (``studio_toggle``'s icon), ``reach`` pixels at scale 1."""
        cx, cy = self.p(u, v)
        outer, inner = reach * self.s, reach * self.s * 0.30
        points = [(cx, cy)]
        for step in range(9):
            angle, radius = math.radians(90 + step * 45), outer if step % 2 == 0 else inner
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        poly(points, _c(color, self.a))

    def picture(self, u, v, du, dv, night=False):
        """A generated image: sky, a sun or moon, two hills (the Studio icon's hill)."""
        self.box(u, v, du, dv, NIGHT if night else SKY, 4)
        self.disc(u + du * 0.70, v + dv * 0.70, min(du * self.w, dv * self.h) / self.s * 0.12, MOON if night else SUN)
        x0, y0 = self.p(u, v)
        w, h = du * self.w, dv * self.h
        inset = 1.5 * self.s
        poly([(x0 + inset, y0 + inset), (x0 + inset, y0 + h * 0.42), (x0 + w * 0.30, y0 + h * 0.58),
              (x0 + w * 0.62, y0 + h * 0.34), (x0 + w * 0.62, y0 + inset)], _c(HILLS[0], self.a))
        poly([(x0 + w * 0.34, y0 + inset), (x0 + w * 0.70, y0 + h * 0.44), (x0 + w - inset, y0 + h * 0.26),
              (x0 + w - inset, y0 + inset)], _c(HILLS[1], self.a))

    def link(self, u1, v1, u2, v2, color, segments=18):
        """A node link: a horizontal-tangent curve between two sockets."""
        (x1, y1), (x2, y2) = self.p(u1, v1), self.p(u2, v2)
        reach = abs(x2 - x1) * 0.5
        points = []
        for step in range(segments + 1):
            t = step / segments
            m = 1 - t
            points.append((m ** 3 * x1 + 3 * m * m * t * (x1 + reach) + 3 * m * t * t * (x2 - reach) + t ** 3 * x2,
                           m ** 3 * y1 + 3 * m * m * t * y1 + 3 * m * t * t * y2 + t ** 3 * y2))
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            self.view.stroke(ax, ay, bx, by, _c(color, self.a), 2.0 * self.s)

    def badge(self, u, v, kind, size=24):
        """A concept icon on a round badge centred at ``(u, v)``; ``size`` pixels at scale 1."""
        self.disc(u, v, size / 2, kit.LINE)
        cx, cy = self.p(u, v)
        side = size * 0.72 * self.s
        icon(kind, cx - side / 2, cy - side / 2, side, self.a)

    def pointer(self, u, v, size=14):
        """The mouse pointer, its tip at ``(u, v)``."""
        x, y = self.p(u, v)
        k = size * self.s / 14
        outline = [(0, 0), (0, -14), (3.6, -10.6), (6.4, -16.4), (8.6, -15.4), (5.9, -9.8), (10.4, -9.8)]
        pts = [(x + px * k, y + py * k) for px, py in outline]
        poly([(x - 1.4 * k, y + 1.6 * k), (x - 1.4 * k, y - 16.8 * k), (12.8 * k + x, y - 10.8 * k)],
             _c(kit.INK, self.a))
        poly(pts[:3] + [pts[-1]], _c(kit.TEXT, self.a))
        poly([pts[2], pts[3], pts[4], pts[5]], _c(kit.TEXT, self.a))


def draw(view, key, rect, s, alpha=1.0):
    """The picture for slide ``key`` in ``rect`` (region pixels)."""
    pen = Pen(view, rect, s, alpha)
    pen.box(0, 0, 1, 1, FIELD, 10)
    PICTURES[key](pen)


def _chat(pen):
    """Describe it, Angelo builds it: the 3D view with what the chat asked for, and the chat beside it."""
    pen.box(0.03, 0.05, 0.60, 0.90, VIEW, 6)
    for index in range(4):   # The floor grid, receding.
        v = 0.12 + index * 0.085
        pen.line(0.06 + index * 0.025, v, 0.60 - index * 0.025, v, kit.LINE, 1.0)
    pen.cube(0.25, 0.20, 26, STONE)
    pen.cube(0.42, 0.24, 17, MINT)
    pen.cube(0.335, 0.43, 13, MINT)
    pen.spark(0.53, 0.72, 9, kit.TEAL)
    pen.spark(0.47, 0.82, 5, kit.TEAL)
    pen.box(0.66, 0.05, 0.31, 0.90, kit.TEAL_SOFT, 6)
    pen.box(0.76, 0.66, 0.18, 0.17, kit.LINE, 6)          # The person's message.
    pen.bars(0.785, 0.79, (0.12, 0.08), kit.MUTED, gap=0.06, height=0.03)
    pen.box(0.69, 0.33, 0.21, 0.26, kit.PANEL, 6)         # Angelo's reply.
    pen.bars(0.71, 0.54, (0.16, 0.13, 0.10), kit.TEAL, gap=0.065, height=0.03)
    pen.box(0.69, 0.09, 0.25, 0.14, kit.PANEL, 7)         # The composer and its send button.
    pen.disc(0.895, 0.16, 5.5, kit.TEAL)


def _studio(pen):
    """Media Studio: a prompt and Generate, then images and a 3D model."""
    pen.box(0.04, 0.83, 0.12, 0.09, kit.TEAL, 5)          # Image and 3D tabs.
    pen.box(0.175, 0.83, 0.10, 0.09, kit.LINE, 5)
    pen.box(0.04, 0.30, 0.36, 0.46, kit.PANEL, 6)         # The prompt.
    pen.bars(0.07, 0.70, (0.28, 0.24, 0.18), kit.MUTED, gap=0.085)
    pen.spark(0.355, 0.36, 6, kit.TEAL)
    pen.box(0.04, 0.08, 0.36, 0.15, kit.TEAL, 8)          # Generate.
    pen.bars(0.15, 0.175, (0.14,), kit.INK, height=0.04)
    pen.picture(0.45, 0.52, 0.245, 0.40)
    pen.box(0.715, 0.52, 0.245, 0.40, VIEW, 4)
    pen.cube(0.8375, 0.60, 15, MINT)
    pen.picture(0.45, 0.08, 0.245, 0.40, night=True)
    pen.box(0.715, 0.08, 0.245, 0.40, kit.PANEL, 4)       # Still generating, under Media Studio's icon.
    pen.badge(0.8375, 0.31, "studio")
    pen.box(0.745, 0.13, 0.185, 0.035, kit.LINE, 2)
    pen.box(0.745, 0.13, 0.11, 0.035, kit.TEAL, 2)


def _flows(pen):
    """Generation Flows: a prompt, an image and a 3D model as chained nodes."""
    for row in range(5):   # The node canvas's dot grid.
        for column in range(9):
            pen.disc(0.06 + column * 0.11, 0.12 + row * 0.19, 0.9, kit.LINE)
    nodes = ((0.04, 0.40), (0.37, 0.12), (0.70, 0.44))
    w, h = 0.26, 0.46
    for (u, v), (nu, nv) in zip(nodes, nodes[1:]):
        pen.link(u + w, v + h * 0.55, nu, nv + h * 0.55, kit.TEAL)
    for index, (u, v) in enumerate(nodes):
        pen.box(u, v, w, h, kit.PANEL, 6)
        pen.box(u, v + h - 0.11, w, 0.11, kit.TEAL_SOFT, 6)
        pen.bars(u + 0.03, v + h - 0.035, (0.12,), kit.TEXT, height=0.04)
        if index == 0:
            pen.bars(u + 0.03, v + h - 0.15, (0.20, 0.17, 0.12), kit.MUTED, gap=0.07)
        elif index == 1:
            pen.picture(u + 0.025, v + 0.04, w - 0.05, h - 0.19)
        else:
            pen.box(u + 0.025, v + 0.04, w - 0.05, h - 0.19, VIEW, 4)
            pen.cube(u + w / 2, v + 0.07, 12, MINT)
        if index < 2:
            pen.disc(u + w, v + h * 0.55, 3.2, kit.TEAL)
        if index > 0:
            pen.disc(u, v + h * 0.55, 3.2, kit.TEAL)
    pen.badge(0.90, 0.20, "flows", 28)   # Generation Flows' icon.


def _assets(pen):
    """Your generations as assets: the Asset Browser's Angelo Media Studio library, and the shelf below."""
    pen.box(0.03, 0.30, 0.22, 0.65, kit.PANEL, 6)         # Libraries and catalogs.
    pen.box(0.045, 0.79, 0.19, 0.10, kit.TEAL_SOFT, 4)
    pen.bars(0.065, 0.865, (0.13,), kit.TEXT, height=0.04)
    pen.bars(0.085, 0.71, (0.11, 0.09), kit.MUTED, gap=0.11, height=0.035)
    tiles = ((0.29, 0.64), (0.52, 0.64), (0.75, 0.64), (0.29, 0.30), (0.52, 0.30), (0.75, 0.30))
    for index, (u, v) in enumerate(tiles):
        if index % 3 == 1:
            pen.picture(u, v, 0.21, 0.31, night=index > 2)
        else:
            pen.box(u, v, 0.21, 0.31, VIEW, 4)
            pen.cube(u + 0.105, v + 0.04, 12, MINT if index in (0, 5) else STONE)
    u, v = tiles[0]
    pen.view.shape(*(pen.p(u - 0.012, v - 0.02)), 0.234 * pen.w, 0.35 * pen.h, _c(kit.TEAL, pen.a), 6 * pen.s)
    pen.box(u, v, 0.21, 0.31, VIEW, 4)
    pen.cube(u + 0.105, v + 0.04, 12, MINT)
    pen.box(0.03, 0.05, 0.94, 0.19, kit.LINE, 6)          # The Asset Shelf.
    for index in range(6):
        u = 0.05 + index * 0.155
        if index % 2:
            pen.picture(u, 0.075, 0.13, 0.14, night=index == 3)
        else:
            pen.box(u, 0.075, 0.13, 0.14, VIEW, 3)
            pen.cube(u + 0.065, 0.085, 6.5, MINT if index == 0 else STONE)
    pen.pointer(0.455, 0.71)   # Dragging the chosen model.


PICTURES = {"chat": _chat, "studio": _studio, "flows": _flows, "assets": _assets}
