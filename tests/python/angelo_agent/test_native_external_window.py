# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pieces of an app's looks and window picture that run without Blender: the window picture's layout and
composition (``external_window``), every result ``angelo_view`` can give a look (``external_visibility.look_outcome``),
the capture budget, the bounded scene facts and the Rendered-viewport refusal. The offscreen draw itself is
``test_blender_external_window_gui``'s. Contract: doc/angelo/external_mcp.rst, "Looks".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import external_visibility as visibility  # noqa: E402
from angelo_agent import external_window as window_picture  # noqa: E402
from angelo_agent.studio_agent import StudioPending  # noqa: E402


def area(kind, x, y, w, h, shading="SOLID", region=True, quad=False):
    regions = [NS(type="HEADER", x=x, y=y + h - 20, width=w, height=20)]
    if quad:   # Quad view: four drawing regions, each with its own view (region.data).
        half_w, half_h = (w - 10) // 2, (h - 20) // 2
        regions += [NS(type="WINDOW", x=x + 10 + i * half_w, y=y + j * half_h, width=half_w, height=half_h,
                       data=NS(name="view%d%d" % (i, j))) for i in (0, 1) for j in (0, 1)]
    elif region:
        regions.append(NS(type="WINDOW", x=x + 10, y=y, width=w - 10, height=h - 20, data=NS(name="view")))
    return NS(type=kind, ui_type=kind, x=x, y=y, width=w, height=h, regions=regions,
              spaces=NS(active=NS(shading=NS(type=shading))))


def screen(*areas):
    return NS(screen=NS(areas=list(areas)))


class LayoutTests(unittest.TestCase):
    """Third review: the window picture draws nothing (no own draw_view3d in 2.0.1); every editor, 3D viewports
    included, is grey and says why, with the layout as data."""

    def test_every_editor_is_grey_and_says_why_viewports_point_to_the_looks(self):
        size, editors = window_picture.layout(screen(
            area("VIEW_3D", 0, 0, 400, 300), area("PROPERTIES", 400, 0, 200, 300),
            area("VIEW_3D", 0, 300, 600, 100, shading="MATERIAL")))
        self.assertEqual(size, [600, 400])
        self.assertEqual([e["shown"] for e in editors], [False, False, False])
        self.assertEqual([e["why"] for e in editors], [window_picture.VIEWPORT, window_picture.NOT_A_VIEWPORT,
                                                        window_picture.VIEWPORT])
        self.assertIn("angelo_viewport", window_picture.VIEWPORT)
        self.assertIn("angelo_look", window_picture.VIEWPORT)

    def test_a_window_between_screens_is_refused(self):
        with self.assertRaises(RuntimeError):
            window_picture.layout(NS(screen=None))

    def test_the_picture_is_flat_grey_with_each_editor_outlined(self):
        size, editors = window_picture.layout(screen(area("VIEW_3D", 0, 0, 40, 30), area("PROPERTIES", 40, 0, 20, 30)))
        rows = window_picture.compose(size, editors)
        self.assertEqual(rows.shape, (30, 60, 4))
        self.assertTrue((rows[5:25, 5:35, :3] == window_picture.GREY).all(), "the 3D viewport is grey too")
        self.assertTrue((rows[15:25, 45:55, :3] == window_picture.GREY).all())
        self.assertTrue((rows[29, 40:60, :3] == window_picture.OUTLINE).all(), "each editor is outlined")
        self.assertEqual(set(numpy.unique(rows[..., :3])), {window_picture.GREY, window_picture.OUTLINE},
                         "nothing but grey and outlines")

    def test_the_window_module_draws_nothing(self):
        source = Path(window_picture.__file__).read_text(encoding="utf-8")
        for never in ("draw_view3d", "GPUOffScreen", "screenshot", "view_warm"):
            self.assertNotIn(never, source.split('"""', 2)[2], never)


class HeldTests(unittest.TestCase):
    """Third review SHOULD 3: a look while playback runs or the person navigates answered "compiling" forever."""

    def test_playback_and_the_persons_tool_are_said_never_compiling(self):
        from unittest import mock
        from angelo_agent import input_policy
        playing = NS(screen=NS(is_animation_playing=True))
        idle = NS(screen=NS(is_animation_playing=False))
        with mock.patch.object(input_policy, "tool_running", return_value=False):
            self.assertEqual(visibility.held_by_person(playing), visibility.PLAYBACK)
            self.assertIsNone(visibility.held_by_person(idle))
        with mock.patch.object(input_policy, "tool_running", return_value=True):
            self.assertEqual(visibility.held_by_person(idle), visibility.TOOL)


class HeldOrderTests(unittest.TestCase):
    """Fourth review SHOULD 2: ``VisibilityUi.look`` refuses for the person's hold before ``angelo_view`` runs, so no
    pending is made and no compile-ahead draw is queued."""

    def test_the_hold_is_refused_before_any_executor_exists(self):
        import sys
        from types import ModuleType
        from unittest import mock
        from angelo_agent import input_policy
        made = []
        executor = ModuleType("angelo_agent.blender_tools")
        executor.BlenderExecutor = lambda window: made.append(window) or self.fail("angelo_view was reached")
        env = visibility.VisibilityUi()
        playing = NS(screen=NS(is_animation_playing=True, areas=[]))
        for window, running, reason in ((playing, False, visibility.PLAYBACK),
                                        (NS(screen=NS(is_animation_playing=False, areas=[])), True, visibility.TOOL)):
            env.ui = NS(main_window=lambda window=window: window)
            with self.subTest(reason=reason), mock.patch.dict(sys.modules, {"angelo_agent.blender_tools": executor}), \
                    mock.patch.object(input_policy, "tool_running", return_value=running):
                with self.assertRaises(visibility.LookBusy) as caught:
                    env.look(NS(references=None), {"views": ["FIT"]})
                self.assertEqual(str(caught.exception), reason)
                self.assertEqual(env.held(), reason)
        self.assertEqual(made, [])


class LookOutcomeTests(unittest.TestCase):
    def test_views_come_back_with_their_disclosures(self):
        ids, disclosures = visibility.look_outcome({"views": [{"id": "a"}, {"id": "b"}], "scene_changed": False,
                                                    "uncertainty": [{"code": "x"}], "side_effects": []})
        self.assertEqual(ids, ["a", "b"])
        self.assertEqual(disclosures, {"scene_changed": False, "uncertainty": [{"code": "x"}], "side_effects": []})

    def test_a_deferred_look_is_cancelled_and_answered_busy(self):
        cancelled = []
        pending = StudioPending(lambda: None, lambda: cancelled.append(True))
        with self.assertRaises(visibility.LookBusy):
            visibility.look_outcome(pending)
        self.assertEqual(cancelled, [True], "the pending is never left queued")
        self.assertTrue(pending.closed)

    def test_an_error_or_anything_else_is_a_refusal_never_an_attribute_error(self):
        for result in ({"error": "no viewport"}, {"views": []}, None, "text", 3):
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                visibility.look_outcome(result)


class BudgetTests(unittest.TestCase):
    def test_a_capture_waits_its_wall_time_times_the_cool_down(self):
        budget = visibility.Budget()
        self.assertTrue(budget.allow(0.0))
        budget.spent(10.0, 10.5)   # Half a second of drawing.
        self.assertFalse(budget.allow(10.5 + 0.5 * visibility.COOL_DOWN - 0.01))
        self.assertTrue(budget.allow(10.5 + 0.5 * visibility.COOL_DOWN))
        budget.spent(20.0, 20.0)   # An instant one still waits the least gap.
        self.assertFalse(budget.allow(20.0 + visibility.MIN_GAP / 2))


class Objects(list):
    """A view layer's objects with ``get``; counts how many objects a walk visited."""

    def __init__(self, items):
        super().__init__(items)
        self.visited = 0

    def get(self, name):
        return next((obj for obj in list.__iter__(self) if obj.name == name), None)

    def __iter__(self):
        for obj in list.__iter__(self):
            self.visited += 1
            yield obj


def thing(name, kind="MESH", x=0.0):
    matrix = [[1.0, 0.0, 0.0, x], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
    data = NS(type="PERSP", lens=50.0, sensor_width=36.0, sensor_height=24.0, sensor_fit="AUTO")
    return NS(name=name, type=kind, matrix_world=matrix, data=data, visible_get=lambda view_layer=None: True,
              bound_box=[(a, b, c) for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)])


RENDER = NS(resolution_x=1920, resolution_y=1080, pixel_aspect_x=1.0, pixel_aspect_y=1.0)


class FactsTests(unittest.TestCase):
    def test_named_objects_and_cameras_are_looked_up_never_walked(self):
        objects = Objects([thing("o%d" % i) for i in range(500)] + [thing("Cam", "CAMERA")])
        facts = visibility.gather_facts(objects, None, RENDER, {"objects": ["o7"], "views": [{"camera": "Cam"}]},
                                        lambda obj: "h:" + obj.name)
        self.assertEqual(objects.visited, 0)
        self.assertEqual(facts["handles"], {"o7": "h:o7"})
        self.assertEqual(set(facts["cameras"]), {"Cam"})
        self.assertAlmostEqual(facts["aspect"], 1920 / 1080)

    def test_the_visible_scene_is_measured_over_a_bounded_walk(self):
        objects = Objects([thing("o%d" % i, x=float(i)) for i in range(visibility.MAX_FRAMED + 50)])
        facts = visibility.gather_facts(objects, None, RENDER, {"objects": [], "views": ["FIT"]}, str)
        self.assertEqual(objects.visited, visibility.MAX_FRAMED)
        self.assertIsNotNone(facts["bounds"])
        self.assertTrue(facts["partial"], "the framing says it covers part of the scene")

    def test_the_walk_is_bounded_even_when_little_is_visible_geometry(self):
        """Re-review SHOULD 2: a scene of mostly empties or lights was walked to its end."""
        objects = Objects([thing("e%d" % i, kind="EMPTY") for i in range(visibility.MAX_WALK + 100)])
        facts = visibility.gather_facts(objects, None, RENDER, {"objects": [], "views": ["FIT"]}, str)
        self.assertEqual(objects.visited, visibility.MAX_WALK)
        self.assertTrue(facts["partial"])
        small = Objects([thing("o1"), thing("e1", kind="EMPTY")])
        self.assertFalse(visibility.gather_facts(small, None, RENDER, {"objects": [], "views": ["FIT"]},
                                                 str)["partial"])

    def test_an_unknown_name_brings_a_bounded_sample_of_the_names_it_can_be(self):
        objects = Objects([thing("o%d" % i) for i in range(100)])
        facts = visibility.gather_facts(objects, None, RENDER, {"objects": ["nope"], "views": ["FIT"]}, str)
        self.assertEqual(len(facts["known"]), visibility.KNOWN)


class RenderedTests(unittest.TestCase):
    def test_a_look_is_refused_only_when_every_3d_viewport_renders(self):
        rendered, solid = area("VIEW_3D", 0, 0, 10, 10, "RENDERED"), area("VIEW_3D", 0, 0, 10, 10)
        self.assertTrue(visibility.all_rendered([rendered]))
        self.assertTrue(visibility.all_rendered([rendered, area("OUTLINER", 0, 0, 5, 5)]))
        self.assertFalse(visibility.all_rendered([rendered, solid]))
        self.assertFalse(visibility.all_rendered([area("OUTLINER", 0, 0, 5, 5)]), "no viewport: angelo_view says so")


if __name__ == "__main__":
    unittest.main()
