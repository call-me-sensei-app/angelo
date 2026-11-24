# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sections other modules add to the engine preflight's report, without editing it. No Blender.

``engine_preflight.add_section(key, function, budget_ms)`` lets a module add a read-only part of the report (the
character pre-review is the first: ``character_checks.summary_for_engine``). ``apply_sections`` runs them after the
engine findings (doc/angelo/engine_preflight.rst, "Sections"). Covered:

* a section's result lands under its key; a section returning None adds nothing;
* a section that raises is reported under its key as ``{"error": <exception type>}`` and the others still run: a
  section never fails the preflight;
* a section whose declared budget doesn't fit in what is left of the preflight's budget is not run:
  ``{"skipped": "budget"}``; each section's time is recorded in ``report["sections_ms"]``;
* adding a key again replaces it; ``remove_section`` removes it; a key that collides with the report's own
  fields is refused;
* ``for_agent`` carries the sections to the agent as they are.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import engine_preflight  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Sections(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(engine_preflight, "add_section"), "engine_preflight has no section registry")
        for key in list(engine_preflight.SECTIONS):
            engine_preflight.remove_section(key)
        self.report = {"target": "UNREAL", "findings": [], "counts": {}}
        self.clock = Clock()

    def tearDown(self):
        for key in list(engine_preflight.SECTIONS):
            engine_preflight.remove_section(key)

    def run_sections(self, budget_ms=1000):
        engine_preflight.apply_sections(None, {"exported": []}, self.report, budget_ms=budget_ms, clock=self.clock)

    def test_results_land_under_their_keys(self):
        engine_preflight.add_section("character", lambda window, facts: {"counts": {"flipped_faces": 2}}, 100)
        engine_preflight.add_section("nothing", lambda window, facts: None, 100)
        self.run_sections()
        self.assertEqual(self.report["character"], {"counts": {"flipped_faces": 2}})
        self.assertNotIn("nothing", self.report)
        self.assertIn("character", self.report["sections_ms"])

    def test_a_failing_section_never_fails_the_preflight(self):
        def broken(window, facts):
            raise ValueError("boom")
        engine_preflight.add_section("broken", broken, 100)
        engine_preflight.add_section("fine", lambda window, facts: {"ok": True}, 100)
        self.run_sections()
        self.assertEqual(self.report["broken"], {"error": "ValueError"})
        self.assertEqual(self.report["fine"], {"ok": True})

    def test_budget(self):
        def slow(window, facts):
            self.clock.now += 0.4
            return {"done": True}
        engine_preflight.add_section("first", slow, 300)
        engine_preflight.add_section("second", lambda window, facts: {"done": True}, 700)
        self.run_sections(budget_ms=1000)
        self.assertEqual(self.report["first"], {"done": True})
        self.assertEqual(self.report["second"], {"skipped": "budget"})
        self.assertGreaterEqual(self.report["sections_ms"]["first"], 400)

    def test_registry(self):
        engine_preflight.add_section("x", lambda window, facts: {"v": 1}, 10)
        engine_preflight.add_section("x", lambda window, facts: {"v": 2}, 10)
        self.run_sections()
        self.assertEqual(self.report["x"], {"v": 2})
        engine_preflight.remove_section("x")
        self.assertNotIn("x", engine_preflight.SECTIONS)
        for reserved in ("findings", "counts", "target", "sections_ms"):
            with self.subTest(key=reserved), self.assertRaises(ValueError):
                engine_preflight.add_section(reserved, lambda window, facts: None, 10)

    def test_for_agent_carries_sections(self):
        engine_preflight.add_section("character", lambda window, facts: {"top": []}, 10)
        self.run_sections()
        self.assertEqual(engine_preflight.for_agent(self.report)["character"], {"top": []})


if __name__ == "__main__":
    unittest.main()
