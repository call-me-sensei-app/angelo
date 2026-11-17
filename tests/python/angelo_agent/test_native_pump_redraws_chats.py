# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A change the UI pump shows in Preferences is shown in the chats too (``ui.pump``).

Evidence (2026-11-16, 2.0.0): Generation Flows' pump used to ask every chat to redraw every 2 s, changed or not
(``test_blender_idle_redraw``). With that gone, a change reaches a chat only when the stage that found it asks. Two
stages asked Preferences alone: a saved API key that finished loading from the key store (``saved_keys_ui.pump``) and
an organization endpoint's key or Test connection result (``endpoint_ui.pump``). The chat's connect card and model
choice read the same connections and keys (``connection_access``), so a chat open beside Preferences kept its old
picture until something else redrew it (found by v201-local-models).

The rule, for every stage now and later: wherever the pump redraws Preferences for a change, it redraws the chats
for it. Preferences shows connections, keys and sign-ins; so does every chat. (The reverse is not required: most
of what a chat shows, Preferences does not.) Asking is ``console.redraw_chats``: each chat's version moves and the
pump's last step redraws the areas whose version moved, so a stage that found nothing costs nothing
(``test_blender_idle_redraw`` counts an idle window's draws).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/ui.py"


def calls(statement, name):
    """Whether a statement is the call ``name(...)``, alone on its line."""
    return (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
            and ast.unparse(statement.value.func) == name)


def always_reached(statements, name):
    """Whether ``name(...)`` is called whenever these statements run: one of them, or one of a loop's among them."""
    return any(calls(statement, name) or (isinstance(statement, ast.For) and any(calls(inner, name) for inner in statement.body))
               for statement in statements)


def blocks(function):
    """Every statement list inside a function (its own, and each branch and loop body)."""
    for node in ast.walk(function):
        for field in ("body", "orelse", "finalbody"):
            statements = getattr(node, field, None)
            if isinstance(statements, list) and statements and isinstance(statements[0], ast.stmt):
                yield node, statements


class PumpTests(unittest.TestCase):
    def pump(self):
        tree = ast.parse(UI.read_text(encoding="utf-8"))
        return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "pump")

    def test_every_change_shown_in_preferences_is_shown_in_the_chats(self):
        found, missing = 0, []
        for node, statements in blocks(self.pump()):
            if any(calls(statement, "preferences_ui.redraw") for statement in statements):
                found += 1
                if not always_reached(statements, "console.redraw_chats"):
                    missing.append("line %d: %s" % (node.lineno, ast.unparse(getattr(node, "test", node))[:80]))
        self.assertGreaterEqual(found, 5, "the pump no longer redraws Preferences where this test looks")
        self.assertEqual([], missing, "these stages redraw Preferences for a change and leave the chats as they were")

    def test_a_loaded_key_and_an_endpoint_result_reach_the_chats(self):
        # The two stages by name, so a rewrite of the pump that drops one of them is seen here.
        source = ast.unparse(self.pump())
        for stage in ("saved_keys_ui.pump(sys.modules[__name__])", "endpoint_ui.pump()"):
            block = source.split("if %s:" % stage, 1)[1].split("\n    ", 3)
            self.assertIn("console.redraw_chats(_controllers.values())", "\n".join(block[:3]), stage)

    def test_asking_is_a_version_that_moves_never_a_redraw_by_itself(self):
        # What keeps this free while nothing changes: redraw_chats only moves versions.
        console = ast.parse((UI.parent / "console.py").read_text(encoding="utf-8"))
        asked = next(node for node in console.body if isinstance(node, ast.FunctionDef) and node.name == "redraw_chats")
        self.assertNotIn("tag_redraw", ast.unparse(asked))
        self.assertIn("controller.version += 1", ast.unparse(asked))


if __name__ == "__main__":
    unittest.main()
