# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Main tile's picture is framed as the maintainer ruled (R3-M2, 2026-10-25 16:55; for the camera, "Yes, camera
too"): through the scene camera only if the geometry fills a reasonable part of its frame, through the artist's own
view only if it fills a reasonable part of that (``FILL`` of the picture, the picture decides), otherwise on the
geometry the scene shows.

Without Blender: ``rail_pictures.lead_view`` and its helpers against stub matrices, cameras and views and a stand-in
for the decision picture (``shows``): the scene box's projected share rules a candidate out without a picture, ``fill``
measures one, and the choice is kept until the camera's frame, the artist's view or the box changes. The real
matrices and pictures are proven by ``test_blender_rail_main_view.py`` (background, with a timing budget) and the Main
shots of ``test_blender_rail_crowd.py`` (GUI, Director Mode).
"""

import math
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))

BPY = ModuleType("bpy")
BPY.app = SimpleNamespace(background=True, timers=SimpleNamespace(is_registered=lambda f: False))
ADDED = "bpy" not in sys.modules
sys.modules.setdefault("bpy", BPY)
from angelo_agent import parallel_sessions, rail_pictures  # noqa: E402
if ADDED:
    del sys.modules["bpy"]



try:
    import numpy  # noqa: F401
    NUMPY = True
except ImportError:
    NUMPY = False

class Matrix:
    """The few ``mathutils.Matrix`` operations ``lead_view`` uses, on rows."""

    def __init__(self, rows):
        self.rows = [list(map(float, row)) for row in rows]

    @classmethod
    def Translation(cls, vector):
        return cls([[1, 0, 0, vector[0]], [0, 1, 0, vector[1]], [0, 0, 1, vector[2]], [0, 0, 0, 1]])

    def __matmul__(self, other):
        return Matrix([[sum(self.rows[r][k] * other.rows[k][c] for k in range(4)) for c in range(4)]
                       for r in range(4)])

    def inverted(self):   # Gauss-Jordan; the stub views are invertible.
        size = len(self.rows)
        work = [row[:] + [float(r == c) for c in range(size)] for r, row in enumerate(self.rows)]
        for column in range(size):
            pivot = max(range(column, size), key=lambda r: abs(work[r][column]))
            work[column], work[pivot] = work[pivot], work[column]
            scale = work[column][column]
            work[column] = [value / scale for value in work[column]]
            for r in range(size):
                if r != column:
                    factor = work[r][column]
                    work[r] = [a - factor * b for a, b in zip(work[r], work[column])]
        return Matrix([row[size:] for row in work])

    def copy(self):
        return Matrix(self.rows)

    def normalized(self):   # The stub cameras are unscaled.
        return self.copy()

    def to_4x4(self):
        return self

    def to_matrix(self):
        return self

    def __getitem__(self, index):
        return self.rows[index]

    def __setitem__(self, index, row):
        self.rows[index] = list(row)

    def __iter__(self):
        return iter(self.rows)

    def __eq__(self, other):
        return all(abs(a - b) < 1e-9 for r, s in zip(self.rows, other.rows) for a, b in zip(r, s))


MATHUTILS = ModuleType("mathutils")
MATHUTILS.Matrix = Matrix
IDENTITY = Matrix([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])


def perspective(fov, aspect, near=0.1, far=100.0):
    f = 1.0 / math.tan(fov / 2)
    return Matrix([[f / aspect, 0, 0, 0], [0, f, 0, 0],
                   [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)], [0, 0, -1, 0]])


def camera(projection, at=(0, 0, 5)):
    evaluated = SimpleNamespace(matrix_world=Matrix.Translation(at))   # Looking down -Z at the origin's box.
    return SimpleNamespace(calc_matrix_camera=lambda depsgraph, **frame: projection.copy(),
                           evaluated_get=lambda depsgraph: evaluated, as_pointer=lambda: 7)


def window(scene_camera=None, resolution=(1920, 1080)):
    render = SimpleNamespace(resolution_x=resolution[0], resolution_y=resolution[1], pixel_aspect_x=1.0,
                             pixel_aspect_y=1.0)
    scene = SimpleNamespace(camera=scene_camera, render=render)
    scene.as_pointer = lambda: id(scene)
    return SimpleNamespace(scene=scene,
                           view_layer=SimpleNamespace(depsgraph=SimpleNamespace(as_pointer=lambda: 11)))


def artist(view=None, projection=None, size=(1600, 900)):
    """A 3D view area of ``size`` whose artist's view has those matrices, and its region."""
    view = view or IDENTITY
    projection = projection or perspective(math.radians(50), size[0] / size[1])
    rv3d = SimpleNamespace(view_matrix=view, window_matrix=projection, perspective_matrix=projection @ view,
                           as_pointer=lambda: 21)
    region = SimpleNamespace(type='WINDOW', data=rv3d, width=size[0], height=size[1])
    return SimpleNamespace(spaces=SimpleNamespace(active=SimpleNamespace(region_3d=rv3d)), regions=[region]), region


BOX = ((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0))
BOUNDS = (Matrix([[2, 0, 0, 0]] * 4), Matrix([[3, 0, 0, 0]] * 4))


def decide(*args, **kwargs):
    return rail_pictures.lead_view(*args, **kwargs)


class LeadViewTests(unittest.TestCase):
    def setUp(self):
        patches = [patch.dict(sys.modules, {"mathutils": MATHUTILS}),
                   patch.object(rail_pictures, "framing", lambda box, aspect: BOUNDS + (None, None, None))]
        for each in patches:
            each.start()
            self.addCleanup(each.stop)

    def test_the_scene_camera_when_the_geometry_fills_its_frame(self):
        lens = perspective(math.radians(40), 1920 / 1080)
        area, region = artist()
        asked = []
        view, projection, source = decide(window(camera(lens)), area, region, BOX,
                                          shows=lambda view, decide: asked.append(decide) or 0.5)
        self.assertEqual(source, "camera")
        self.assertEqual(asked, [lens], "decided on its own render frame, nothing else")
        self.assertEqual(view, Matrix.Translation((0, 0, -5)), "the camera's own place, inverted")
        wider = 1920 / 1080 / 1.6   # A 16:9 frame in a 16:10 picture: its width kept, its height shown whole.
        self.assertEqual(projection[0], lens[0])
        self.assertEqual(projection[1], [value / wider for value in lens[1]])

    def test_the_picture_decides_after_the_box_test(self):
        """REVIEW-MAIN-THUMB-2 H1: a box that meets a view is not geometry seen (one joined mesh either side of it)."""
        lens = perspective(math.radians(40), 1920 / 1080)
        near, _ = artist(view=Matrix.Translation((0, 0, -4)), size=(1000, 1000))
        drawn = []

        def shows(answers):
            return lambda view, projection: drawn.append(projection) or answers.pop(0)
        scene = window(camera(lens))
        self.assertEqual(decide(scene, near, near.regions[0], BOX, shows=shows([0.0, 0.5]))[2], "view",
                         "no camera pixel: through")
        self.assertEqual(drawn[0], lens, "the camera's own frame is drawn to decide")
        self.assertEqual(decide(scene, near, near.regions[0], BOX, shows=shows([0.0, rail_pictures.FILL / 2])),
                         BOUNDS + ("bounds",), "the view's geometry pixels fill too little: the bounds")
        self.assertEqual(decide(scene, near, near.regions[0], BOX, shows=shows([rail_pictures.FILL]))[2], "camera")
        self.assertEqual(decide(scene, near, near.regions[0], BOX, shows=shows([rail_pictures.FILL / 2, 0.5]))[2],
                         "view", "camera too (the maintainer): a camera that sees too little falls through")
        self.assertEqual(decide(scene, near, near.regions[0], BOX, shows=lambda view, projection: None)[2], "camera",
                         "a build that cannot tell: the box test decides")
        drawn.clear()
        far_camera = window(camera(lens, at=(0, 0, 60)))
        self.assertEqual(decide(far_camera, near, near.regions[0], BOX, shows=shows([0.5]))[2], "view")
        self.assertEqual(len(drawn), 1, "a camera whose frame the box cannot fill: ruled out before a picture")
        drawn.clear()
        far, _ = artist(view=Matrix.Translation((0, 0, -60)), size=(1000, 1000))
        decide(window(), far, far.regions[0], BOX, shows=shows([1.0]))
        self.assertEqual(drawn, [], "ruled out by the box's coverage: nothing drawn")

    def test_each_redraw_decides_afresh_both_ways(self):
        """Nothing is kept across redraws: inside an unchanged box (walls fix it) a model moved out of the camera's
        frame gives up the camera (REVIEW-TILE-COST H1), and one moved in front of it takes the camera back from the
        view or the bounds (REVIEW-TILE-COST-2 M); each redraw draws at most two decision pictures."""
        lens = perspective(math.radians(40), 1920 / 1080)
        near, _ = artist(view=Matrix.Translation((0, 0, -4)), size=(1000, 1000))
        scene, drawn = window(camera(lens)), []

        def shows(answers):
            return lambda view, projection: drawn.append(projection) or answers.pop(0)
        for answers, expected in (([0.5], "camera"), ([0.0, 0.5], "view"), ([0.0, 0.0], "bounds"),
                                  ([0.5], "camera"), ([0.0, 0.0], "bounds"), ([0.3], "camera")):
            drawn.clear()
            pictures = len(answers)
            self.assertEqual(rail_pictures.lead_view(scene, near, near.regions[0], BOX, shows=shows(answers))[2],
                             expected)
            self.assertEqual(len(drawn), pictures, "every answer drawn, nothing more")
        self.assertFalse(hasattr(rail_pictures, "_decided"), "no choice is kept to go stale")

    def test_a_shown_source_keeps_while_it_fills_keep(self):
        """REVIEW-TILE-COST-2 H: a 2 px pan moved Suzanne at ~55 x 39 px from 12 cells to 9. The source the picture
        shows (``last``) stays while it fills ``KEEP`` (7 of 160 cells), a new one needs ``FILL`` (10)."""
        near, _ = artist(view=Matrix.Translation((0, 0, -4)), size=(1000, 1000))
        scene = window()
        cells = lambda count: (lambda v, p, grow=0: count / 160)   # noqa: E731
        self.assertEqual(rail_pictures.KEEP, 7 / 160)
        self.assertEqual(rail_pictures.lead_view(scene, near, near.regions[0], BOX, shows=cells(9), last="view")[2],
                         "view", "Suzanne panned 2 px stays on the view")
        self.assertEqual(rail_pictures.lead_view(scene, near, near.regions[0], BOX, shows=cells(7), last="view")[2],
                         "view")
        self.assertEqual(rail_pictures.lead_view(scene, near, near.regions[0], BOX, shows=cells(6), last="view"),
                         BOUNDS + ("bounds",), "below 7: decided afresh")
        self.assertEqual(rail_pictures.lead_view(scene, near, near.regions[0], BOX, shows=cells(9))[2], "bounds",
                         "not shown before: needs FILL")
        self.assertEqual(rail_pictures.lead_view(scene, near, near.regions[0], BOX, shows=cells(10))[2], "view")

    @unittest.skipUnless(NUMPY, "numpy draws the shapes")
    def test_keep_is_translation_stable_on_held_out_shapes(self):
        """REVIEW-TILE-STAGES-2 L-c: KEEP held Suzanne, yet 7.3% of rectangles and thin bars adopted at 10-13 cells
        fell below 7 on a 2 px pan (a 68 x 16 px bar, 12 to 5; half-cell-shifted grids still let 5.7% of bars fall).
        Grown by ``KEEP_PX`` first, a shape panned that far touches every cell it touched: none falls, while a
        subject that genuinely shrinks still does."""
        import math
        import numpy
        rng = numpy.random.default_rng(11)
        ys, xs = numpy.mgrid[0:160, 0:256]

        def shape(kind, cx, cy, w, h, angle):
            c, s = math.cos(angle), math.sin(angle)
            u = (xs + 0.5 - cx) * c + (ys + 0.5 - cy) * s
            v = -(xs + 0.5 - cx) * s + (ys + 0.5 - cy) * c
            if kind == "ellipse":
                return (u / (w / 2)) ** 2 + (v / (h / 2)) ** 2 <= 1
            return (abs(u) <= w / 2) & (abs(v) <= h / 2)
        adopted = 0
        for kind, widths, heights in (("ellipse", (20, 80), (15, 60)), ("rect", (20, 80), (15, 60)),
                                      ("bar", (40, 120), (2, 8))):
            for _ in range(400):
                w, h = rng.uniform(*widths), rng.uniform(*heights)
                cx, cy, angle = rng.uniform(40, 216), rng.uniform(30, 130), rng.choice([0.0, rng.uniform(0, math.pi)])
                if not 10 <= round(rail_pictures.fill(shape(kind, cx, cy, w, h, angle)) * 160) <= 13:
                    continue
                adopted += 1
                for dx in (-2, -1, 0, 1, 2):
                    for dy in (-2, -1, 0, 1, 2):
                        kept = rail_pictures.fill(shape(kind, cx + dx, cy + dy, w, h, angle), rail_pictures.KEEP_PX)
                        self.assertGreaterEqual(kept, rail_pictures.KEEP, (kind, w, h, dx, dy))
        self.assertGreater(adopted, 100)
        shrunk = [round(rail_pictures.fill(shape("ellipse", 128, 80, 60 * f, 40 * f, 0.3), rail_pictures.KEEP_PX) * 160)
                  for f in (1.0, 0.6, 0.45)]
        self.assertGreaterEqual(shrunk[1], 7, shrunk)
        self.assertLess(shrunk[2], 7, ("a subject that shrinks re-decides", shrunk))

    def test_a_box_under_a_sixteenth_that_straddles_cells_is_drawn_to_decide(self):
        """The grid measure can exceed the box rectangle's area share: such a candidate gets its picture."""
        ortho = Matrix([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 0], [0, 0, 0, 1]])   # World x, y are the picture's.
        area, region = artist(projection=ortho, size=(1600, 1000))
        box = ((15 / 128 - 1, 15 / 80 - 1, -1.0), (55 / 128 - 1, 40 / 80 - 1, 1.0))   # 40 x 25 px from (15, 15).
        self.assertLess(rail_pictures.coverage(ortho, box), rail_pictures.FILL)
        asked = []
        self.assertEqual(decide(window(), area, region, box, shows=lambda *view: asked.append(view) or 12 / 160)[2],
                         "view")
        self.assertEqual(len(asked), 1)

    def test_the_artists_view_when_the_geometry_fills_enough_of_it(self):
        area, region = artist(view=Matrix.Translation((0, 0, -4)), size=(1000, 1000))
        view, projection, source = decide(window(), area, region, BOX, shows=lambda view, projection: 0.5)
        self.assertEqual(source, "view")
        self.assertEqual(view, Matrix.Translation((0, 0, -4)))
        square = area.spaces.active.region_3d.window_matrix   # A square view in a 16:10 picture: its height kept.
        self.assertEqual(projection[1], square[1])
        self.assertEqual(projection[0], [value / 1.6 for value in square[0]])

    def test_a_far_artists_view_frames_the_bounds(self):
        """The same model further off, and a model of any size as far off for its size: a dot is never the picture."""
        for size, distance in ((1.0, 40.0), (0.002, 0.08), (1000.0, 40000.0)):
            box = ((-size,) * 3, (size,) * 3)
            lens = perspective(math.radians(50), 1.0, 1e-6, 1e7)   # Every case lies between its clip planes.
            area, region = artist(view=Matrix.Translation((0, 0, -distance)), projection=lens, size=(1000, 1000))
            asked = []
            self.assertEqual(decide(window(), area, region, box, shows=lambda *view: asked.append(view) or 1.0),
                             BOUNDS + ("bounds",), size)
            self.assertEqual(asked, [], "too small to fill the picture: no picture is drawn to decide")

    def test_the_geometry_bounds_without_an_artists_view(self):
        area = SimpleNamespace(spaces=SimpleNamespace(active=SimpleNamespace(region_3d=None)), regions=[])
        self.assertEqual(decide(window(), area, None, BOX, shows=lambda *view: 1 / 0), BOUNDS + ("bounds",))

    def test_a_frame_fits_whole_whatever_its_shape(self):
        lens = perspective(math.radians(40), 1.0)
        for frame, row, scale in ((2.0, 1, 1.6 / 2.0), (1.0, 0, 1.0 / 1.6), (1.6, 0, 1.0)):
            fitted = rail_pictures.fitted(lens, frame, 1.6)
            self.assertEqual(fitted[row], [value * scale for value in lens[row]], frame)
            self.assertEqual(fitted[1 - row], lens[1 - row], frame)
        self.assertEqual(lens, perspective(math.radians(40), 1.0), "the projection given is never changed")


def mask(size, shape, shift=(0, 0)):
    """A ``size`` (width, height) mask of ``shape`` given as fractions of the picture, moved ``shift`` pixels: a
    rectangle ``(x0, y0, x1, y1)`` holds the pixels whose centres lie in it; ``"line"`` is one row across the whole
    width at mid height, ``"diagonal"`` one pixel per column from corner to corner."""
    width, height = size
    rows = [[False] * width for _ in range(height)]
    if shape == "line":
        cells = [(x, height // 2) for x in range(width)]
    elif shape == "diagonal":
        cells = [(x, x * height // width) for x in range(width)]
    else:
        inside = lambda low, high, pixels: range(math.ceil(low * pixels - 0.5), math.ceil(high * pixels - 0.5))  # noqa
        cells = [(x, y) for box in shape for y in inside(box[1], box[3], height) for x in inside(box[0], box[2], width)]
    for x, y in cells:
        if 0 <= x + shift[0] < width and 0 <= y + shift[1] < height:
            rows[y + shift[1]][x + shift[0]] = True
    return rows


SPECKS = ((0.0, 0.0, 0.012, 0.02), (0.988, 0.98, 1.0, 1.0))      # Two specks in opposite corners.
QUARTER = ((0.3, 0.45, 0.55, 0.7),)                             # A quarter of the width by a quarter of the height.
NINE = ((1 / 16, 1 / 10, 4 / 16, 4 / 10),)                      # Exactly 3 x 3 cells.


class FillTests(unittest.TestCase):
    """``fill``: the one measure of how much of the picture geometry fills (the maintainer, "Count where it is"): the
    share of a 16 x 10 grid's cells that hold any geometry pixel, the grid laid over whatever picture it is given."""

    def fills(self, size, shape, shift=(0, 0)):
        return rail_pictures.fill(mask(size, shape, shift)) >= rail_pictures.FILL

    def test_the_cells_holding_geometry(self):
        self.assertEqual(rail_pictures.fill(mask((256, 160), ())), 0.0)
        self.assertEqual(rail_pictures.fill([]), 0.0)
        self.assertEqual(rail_pictures.fill(mask((256, 160), SPECKS)), 2 / 160, "two specks: two cells")
        self.assertEqual(rail_pictures.fill(mask((256, 160), "line")), 16 / 160, "one row across: every column")
        self.assertGreaterEqual(rail_pictures.fill(mask((256, 160), "diagonal")), 16 / 160)
        self.assertEqual(rail_pictures.fill(mask((256, 160), NINE)), 9 / 160)

    def test_the_rulings_cases(self):
        for size in ((256, 160), (512, 320), (100, 50)):   # The same fractions of any picture: the same answers.
            self.assertFalse(self.fills(size, SPECKS), size)
            self.assertTrue(self.fills(size, "line"), size)
            self.assertTrue(self.fills(size, "diagonal"), size)
            self.assertFalse(self.fills(size, NINE), size)
            self.assertEqual(rail_pictures.fill(mask(size, NINE)), 9 / 160, size)

    def test_a_quarter_by_a_quarter_fills_wherever_it_sits(self):
        places = (0.0, 0.013, 0.027, 0.04, 0.051, 0.37, 0.75)   # Every phase across a cell's lines, and far apart.
        for size in ((256, 160), (512, 320), (100, 50)):
            for x in places:
                for y in places:
                    self.assertTrue(self.fills(size, ((x, y, x + 0.25, y + 0.25),)), (size, x, y))

    def test_a_few_pixels_of_pan(self):
        """Panning a few pixels never flips a view that fills comfortably more or less than the threshold: a quarter
        by a quarter touches at least 4 x 3 = 12 cells wherever it lies, two specks at most 8, a line or a diagonal
        across the picture at least 15. Only geometry right at the threshold can flip, by the grid's quantization:
        3 x 3 cells moved one pixel across cell lines touch 4 x 4 = 16 cells. That is the measure the maintainer
        chose (count where it is), not noise in it."""
        for shape in (SPECKS, "line", "diagonal", QUARTER):
            expected = self.fills((256, 160), shape)
            for dx in range(1, 9):
                for dy in range(1, 9):
                    self.assertEqual(self.fills((256, 160), shape, (dx, dy)), expected, (shape, dx, dy))
        self.assertFalse(self.fills((256, 160), NINE))
        self.assertTrue(self.fills((256, 160), NINE, (1, 1)), "at the threshold the grid's quantization decides")

    def test_numpy_and_sequences_agree(self):
        try:
            import numpy
        except ImportError:
            self.skipTest("numpy")
        for size in ((256, 160), (100, 50)):
            for shape in (SPECKS, "line", "diagonal", QUARTER, NINE, ()):
                rows = mask(size, shape, (3, 2))
                self.assertEqual(rail_pictures.fill(numpy.array(rows, dtype=bool)), rail_pictures.fill(rows),
                                 (size, shape))


class FillsAtMostTests(unittest.TestCase):
    """``fills_at_most``: from a box alone, never below what ``fill`` gives of any picture of what it holds, so it may
    rule a candidate out without a picture. Its area could not: cells are coarser than pixels."""

    ORTHO = Matrix([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 0], [0, 0, 0, 1]])   # World x, y are the picture's -1..1.

    def box(self, x0, y0, x1, y1, size=(256, 160)):   # Pixels of the picture to a world box through ORTHO.
        width, height = size
        return ((x0 * 2 / width - 1, y0 * 2 / height - 1, -1.0), (x1 * 2 / width - 1, y1 * 2 / height - 1, 1.0))

    def test_a_rectangle_under_a_sixteenth_across_cell_lines(self):
        box = self.box(15, 15, 55, 40)   # 40 x 25 px: 0.024 of the picture, across 4 x 3 cells.
        self.assertLess(rail_pictures.coverage(self.ORTHO, box), rail_pictures.FILL)
        shown = rail_pictures.fill(mask((256, 160), ((15 / 256, 15 / 160, 55 / 256, 40 / 160),)))
        self.assertEqual(shown, 12 / 160, "the geometry fills: the area alone would have ruled it out")
        self.assertGreaterEqual(rail_pictures.fills_at_most(self.ORTHO, box), shown)

    def test_never_below_the_measure(self):
        import random
        chance = random.Random(16)
        for size in ((256, 160), (100, 50)):
            width, height = size
            for _ in range(300):
                x0, y0 = chance.uniform(-20, width), chance.uniform(-20, height)
                x1, y1 = x0 + chance.uniform(0, width / 2), y0 + chance.uniform(0, height / 2)
                shown = rail_pictures.fill(mask(size, ((x0 / width, y0 / height, x1 / width, y1 / height),)))
                bound = rail_pictures.fills_at_most(self.ORTHO, self.box(x0, y0, x1, y1, size), size)
                self.assertGreaterEqual(bound, shown, (size, x0, y0, x1, y1))

    def test_small_far_away_or_behind_rules_out(self):
        self.assertLess(rail_pictures.fills_at_most(self.ORTHO, self.box(100, 60, 110, 70)), rail_pictures.FILL)
        self.assertEqual(rail_pictures.fills_at_most(self.ORTHO, self.box(300, 10, 400, 100)), 0.0, "beside it")
        lens = perspective(math.radians(60), 1.6, 1e-6, 1e7)
        self.assertEqual(rail_pictures.fills_at_most(lens @ Matrix.Translation((0, 0, 10)), BOX), 0.0, "behind")
        self.assertEqual(rail_pictures.fills_at_most(lens @ Matrix.Translation((0, 0, -1.5)), BOX), 1.0, "all of it")


class CoverageTests(unittest.TestCase):
    """``coverage``: the share of the picture a world box covers, the same for any scale."""

    MATRIX = perspective(math.radians(60), 1.0, 1e-6, 1e7)

    def at(self, box, distance, matrix=None):
        return rail_pictures.coverage((matrix or self.MATRIX) @ Matrix.Translation((0, 0, -distance)), box)

    def test_the_same_for_any_scale(self):
        shares = [self.at(((-k, -k, -k), (k, k, k)), 10 * k) for k in (0.001, 1.0, 1000.0)]
        for share in shares:
            self.assertAlmostEqual(share, shares[1], places=9)
        self.assertGreater(shares[1], 0.0)

    def test_a_box_seen_head_on(self):
        f = 1.0 / math.tan(math.radians(30))   # Its near face, 2 m across at 9 m, spans 2f/9 of the picture's 2.
        self.assertAlmostEqual(self.at(((-1, -1, -1), (1, 1, 1)), 10.0), (2 * f / 9 / 2) ** 2, places=9)

    def test_closer_covers_more_and_clipped_to_the_picture(self):
        box = ((-1, -1, -1), (1, 1, 1))
        self.assertLess(self.at(box, 20.0), self.at(box, 10.0))
        self.assertEqual(self.at(box, 1.5), 1.0, "larger than the picture: all of it")

    def test_around_or_behind_the_eye(self):
        self.assertEqual(self.at(((-1, -1, -1), (1, 1, 1)), 0.0), 1.0, "the eye inside it")
        self.assertEqual(self.at(((-1, -1, -1), (1, 1, 1)), -10.0), 0.0, "wholly behind the eye")
        ground = ((-100, -100, -1), (100, 100, 200))   # Reaches far ahead and behind: what lies ahead counts.
        self.assertEqual(self.at(ground, 0.0), 1.0)

    def test_a_box_that_is_no_box(self):
        self.assertEqual(self.at(((-1, -1, float("nan")), (1, 1, 1)), 10.0), 0.0)

    def test_beside_the_view(self):
        self.assertEqual(self.at(((40, -1, -1), (42, 1, 1)), 10.0), 0.0)
        half = self.at(((0, -1, -1), (40, 1, 1)), 10.0)   # From the middle out past the edge: the right half only.
        self.assertAlmostEqual(half, 0.5 * 2 * (1 / math.tan(math.radians(30))) / 9 / 2, places=9)

    def test_an_orthographic_view(self):
        ortho = Matrix([[0.1, 0, 0, 0], [0, 0.1, 0, 0], [0, 0, -0.02, -1], [0, 0, 0, 1]])   # 20 m across.
        self.assertAlmostEqual(self.at(((-1, -1, -1), (1, 1, 1)), 10.0, ortho), 0.01, places=9)
        self.assertAlmostEqual(self.at(((-1, -1, -1), (1, 1, 1)), 90.0, ortho), 0.01, places=9, msg="any distance")


class ViewMovedTests(unittest.TestCase):
    """The lead's picture goes out of date when what decides its view changed since it was drawn, never otherwise."""

    def setUp(self):
        self.key, self.shown = ("view", 1), True
        patches = [patch.object(parallel_sessions, "lead", "lead"), patch.dict(parallel_sessions.stale, clear=True),
                   patch.dict(rail_pictures._lead_keys, {"lead": ("view", 1)}, clear=True),
                   patch.object(rail_pictures, "_host", lambda: ("window", "area", "region")),
                   patch.object(rail_pictures, "lead_shown", lambda session, host=None: self.shown),
                   patch.object(rail_pictures, "_view_key", lambda *host: self.key)]
        for each in patches:
            each.start()
            self.addCleanup(each.stop)

    def test_the_same_view_draws_nothing(self):
        rail_pictures._view_moved()
        self.assertNotIn("lead", parallel_sessions.stale)

    def test_a_view_that_settled_elsewhere_is_drawn_again(self):
        self.key = ("view", 2)
        rail_pictures._view_moved()
        self.assertIn("lead", parallel_sessions.stale)

    def test_never_while_the_window_shows_another_scene(self):
        self.key, self.shown = ("view", 2), False
        rail_pictures._view_moved()
        self.assertNotIn("lead", parallel_sessions.stale)

    def test_a_scene_camera_change_is_a_new_key(self):
        self.key = ("camera", 7, 1920, 1080, 1.0, 1.0)
        rail_pictures._view_moved()
        self.assertIn("lead", parallel_sessions.stale)

    def test_the_view_key_reads_the_camera_frame_and_the_view_unless_the_camera_decided(self):
        area, region = artist(size=(800, 600))
        scene_camera = window(camera(IDENTITY), (1280, 720))
        self.assertEqual(_real_view_key(scene_camera, area, region, "camera"), ("camera", 7, 1280, 720, 1.0, 1.0))
        key = _real_view_key(scene_camera, area, region, "bounds")   # A camera aimed away: the view decides too.
        self.assertEqual(key[:6], ("camera", 7, 1280, 720, 1.0, 1.0))
        self.assertEqual((key[6],) + key[8:], ("view", 800, 600))
        key = _real_view_key(window(), area, region)
        self.assertEqual(key[0], "view")
        self.assertEqual(key[2:], (800, 600))


_real_view_key = rail_pictures._view_key


if __name__ == "__main__":
    unittest.main()
