# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An app's cold MATERIAL or LIT look through the real ``view_warm``: busy now, compiled meanwhile, drawn next.

Re-review MUST A: an app's look cancelled ``view_warm``'s pending (``keep``), which stores what is owed but starts no
compile timer; every later look took it over, was pending again and answered busy for good. A cancelled look must
go on compiling to completion (``view_warm``'s pending ``go_on``), so the next look, once the shaders are ready,
draws. Against ``test_native_view_warm``'s stand-in of the native shader state and Blender's timers, no stub of the
pending. Contract: doc/angelo/external_mcp.rst, "Looks".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_view_warm as warm  # noqa: E402  (its fixture: the shader state, timers and draws)
from angelo_agent import external_visibility, view_warm  # noqa: E402


def thing(name, materials):
    slots = [SimpleNamespace(material=SimpleNamespace(name=m)) for m in materials]
    evaluated = SimpleNamespace(material_slots=slots)
    return SimpleNamespace(name=name, type="MESH", visible_get=lambda view_layer=None: True,
                           evaluated_get=lambda depsgraph: evaluated, instance_type="NONE", modifiers=[])


class ColdLookTests(warm.Fixture):
    def setUp(self):
        super().setUp()
        self.window.view_layer.objects = [thing("Cube", ["Cold"])]
        self.drawn_views = []

    def look(self):
        """What ``view_capture.execute`` does for an app's MATERIAL look: ``view_warm.wait`` with its Solid fallback,
        then the app's reading of the result (``external_visibility.look_outcome``)."""
        def view(shading=None):
            self.drawn_views.append(shading or "MATERIAL")
            return {"views": [{"id": "v%d" % len(self.drawn_views)}], "scene_changed": False, "timing": {}}
        pending = view_warm.wait(self.window, self.area, view, None, False, lambda: view("SOLID"))
        result = pending if pending is not None else view()
        return external_visibility.look_outcome(result)

    def run_timers(self, limit=200):
        """Blender's timers until none wants another tick (each returns its next interval, or None)."""
        for _ in range(limit):
            if not self.timers:
                return
            due, self.timers[:] = list(self.timers), []
            for function in due:
                if function() is not None:
                    self.timers.append(function)
        self.fail("the timers never finished")

    def test_a_cold_look_is_busy_its_shaders_compile_and_the_next_look_draws(self):
        self.shaders = (4, 1, 0, ["Cold"], ["Cold"])   # One material missing: EEVEE has not compiled it.
        with self.assertRaises(external_visibility.LookBusy):
            self.look()
        self.assertEqual(self.drawn_views, [], "nothing drawn on a cold engine")
        self.assertTrue(self.timers, "the cancelled look goes on compiling")
        self.shaders = (4, 0, 0, [])   # Blender's background compile finished.
        self.run_timers()
        self.assertEqual(view_warm._soon, {}, "nothing is owed once compiled and first-drawn")
        ids, _disclosures = self.look()
        self.assertEqual((ids, self.drawn_views), (["v1"], ["MATERIAL"]), "the next look draws its look")

    def test_a_look_asked_while_still_compiling_is_busy_again_and_still_compiles(self):
        self.shaders = (4, 1, 0, ["Cold"], ["Cold"])
        with self.assertRaises(external_visibility.LookBusy):
            self.look()
        self.shaders = (4, 0, 1, ["Cold"])   # Queued, still compiling.
        with self.assertRaises(external_visibility.LookBusy):
            self.look()
        self.assertTrue(self.timers, "compiling goes on after the second cancel too")
        self.shaders = (4, 0, 0, [])
        self.run_timers()
        self.assertEqual(self.look()[0], ["v1"])

    def test_an_apps_looks_never_extend_the_preparation_past_its_minute(self):
        """Third review SHOULD 2: every cold look re-armed up to 60 s of background preparation. Later looks within
        the minute resume it (a look takes the owed work over) at the first arming's deadline, never later."""
        self.shaders = (4, 1, 0, ["Cold"], ["Cold"])
        owner = view_warm._key(self.window)
        with self.assertRaises(external_visibility.LookBusy):
            self.look()
        first = view_warm._soon[owner]["deadline"]
        self.clock[0] += 20.0
        with self.assertRaises(external_visibility.LookBusy):
            self.look()
        self.assertEqual(view_warm._soon[owner]["deadline"], first, "resumed, not extended")
        self.clock[0] = first + 1.0   # Its minute is over: the preparation stopped at its deadline.
        self.run_timers()
        with self.assertRaises(external_visibility.LookBusy):
            self.look()
        self.assertGreater(view_warm._soon[owner]["deadline"], first, "a new minute arms it once more")

    def test_a_warm_scene_draws_at_once(self):
        self.shaders = (4, 0, 0, [])
        self.assertEqual(self.look()[0], ["v1"])
        self.assertEqual(self.timers, [])


if __name__ == "__main__":
    unittest.main()
