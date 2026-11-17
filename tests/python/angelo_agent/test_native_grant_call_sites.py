# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every place Angelo's own UI calls a grant operator is reached by the person's press in a grant scope
(grant_call_sites, doc/angelo/grant_operators.rst). Host test: reads the add-on's source, no Blender."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import unittest

import grant_call_sites as sites


class CallSiteTests(unittest.TestCase):
    def test_every_grant_call_runs_in_the_scope_of_the_press_that_asked_for_it(self):
        """A surface that calls a grant operator from a click outside a grant scope refuses the person's own click
        (2.0.0: Media Studio Generate and Enhance, the Flows bar's Run, Studio Workflows' run, check and fit)."""
        self.assertEqual(sites.uncovered(), [], "pass the click through grant_guard.ticketed / by_click (deferred "
                         "for a later step), or declare in NOT_PERSON why no person's click reaches it")

    def test_the_scan_sees_the_surfaces_that_grant(self):
        """The scan is not blind: it finds the call sites of the surfaces this test was written for."""
        found = {(module, function) for module, function, _line, _text in sites.call_sites()}
        for expected in (("studio", "click"), ("flow_window", "act"), ("studio_workflow_run", "_run"),
                         ("studio_workflow_run", "click"), ("chat_view", "dispatch_action"),
                         ("engine_suggest_ui", "SuggestionCard._answer.pick")):
            self.assertIn(expected, found)
        self.assertIn("studio_generate", sites.guarded_names())
        self.assertIn("flow_run", sites.guarded_names())

    def test_angelos_own_decisions_are_listed_apart(self):
        """Every automatic scope is declared with why no press is needed there, and none is stale: a new one is a
        decision to review, not a silent cover for a UI surface's call."""
        self.assertEqual(sorted(sites.automatic_sites() ^ set(sites.AUTOMATIC)), [],
                         "declare the automatic site in AUTOMATIC with why, or remove a stale entry")

    def test_openers_match_by_exact_owner(self):
        """``x_grant_guard.ticketed`` or a ``GUARD.grant`` is no opener; an undeclared automatic site covers nothing."""
        import ast

        def call(text):
            return ast.parse(text).body[0].value
        self.assertEqual(sites._opener(call("grant_guard.ticketed('a', 'b', f)")), "person")
        self.assertEqual(sites._opener(call("grant_gestures.dragged(hold, f)")), "person")
        for text in ("my_grant_guard.ticketed('a', 'b', f)", "GUARD.grant('a', 'b', f)",
                     "grant_authority.GUARD.grant('a', 'b', f)", "live_windows.deferred(f)"):
            self.assertIsNone(sites._opener(call(text)), text)
        self.assertFalse(sites._is_opener(call("grant_authority.automatic(f)"), "chat_view", None))
        self.assertTrue(sites._is_opener(call("grant_authority.automatic(f)"), "script_admission",
                                         ("script_admission", "prepare", None, None)))

    def test_each_declared_exception_names_a_call_site(self):
        found = {"%s:%s" % (module, function) for module, function, _line, _text in sites.call_sites()}
        self.assertEqual(sorted(set(sites.NOT_PERSON) - found), [], "a NOT_PERSON entry with no call site is stale")


if __name__ == "__main__":
    unittest.main()
