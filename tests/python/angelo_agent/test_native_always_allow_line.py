# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""AI Providers' Always allow scripts line says what the person's consent covers now (providers_page_model).

A consent saved before 2.0.1 covers Angelo's own Auto chats but not connected apps' chats until the person presses
Always allow on one of their script cards (always_allow.covers_external). The page showed it on with text saying
connected apps' chats were covered (break-it review of ca6f5ff137d, N2): it now says which it is.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import unittest
from types import SimpleNamespace
from unittest import mock

from angelo_agent import always_allow, providers_page_model as model


def _prefs(on):
    prop = SimpleNamespace(name="Always allow scripts", description="In chats set to Auto, run scripts")
    rna = SimpleNamespace(properties={"always_allow_scripts": prop, "helper_standing_consent": prop})
    return SimpleNamespace(bl_rna=rna, always_allow_scripts=on, helper_standing_consent=False)


POLICY = SimpleNamespace(allow_auto_approval=True, require_ask_for_edits=False)


def _row(on, covers):
    with mock.patch.object(always_allow, "covers_external", return_value=covers):
        section = model._permissions(_prefs(on), POLICY)
    return next(row for row in section["rows"] if row["value"] == "always_allow_scripts")


class AlwaysAllowLine(unittest.TestCase):
    def test_a_consent_that_covers_connected_apps_says_scripts_in_auto_chats_run(self):
        self.assertEqual(_row(True, True)["warning"], "On: scripts in Auto chats run without asking.")

    def test_a_consent_from_before_2_0_1_says_connected_apps_chats_still_ask(self):
        warning = _row(True, False)["warning"]
        self.assertIn("Connected apps' chats still ask", warning)
        self.assertNotIn("On: scripts in Auto chats run without asking.", warning)

    def test_off_shows_no_line(self):
        self.assertNotIn("warning", _row(False, False))


if __name__ == "__main__":
    unittest.main()
