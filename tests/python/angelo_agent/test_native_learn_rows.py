# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Learn is strictly opt-in and costs the transcript nothing while it is off (learn_rows). No Blender.

Covered: with Teach me as you work off, a step's row, opened or not, latest request or not, comes back from
``learn_rows.under`` as the very object it was, with nothing of learn asked (``learn_cards.has_card`` and
``learn_resolver.card`` raise if called), and ``draw_under`` draws nothing; ``begin`` reads the option once per
draw and an off chat stays off for the whole draw; with it on, the latest request's step is kept open and an earlier
step shows its card only when the person opened it. In the source: ``chat_blocks.build`` reads the option before it
builds any row; ``under`` checks it before anything else; only the add-on's ``register``, ``chat_blocks`` and
``chat_rows`` import learn (nothing on a request's path does); no learn module registers a handler, a message-bus
subscription or a persistent timer (the Learn card's one-shot after a click is the only timer); and the option is read
where Blender is not (``learn_option.teaching_now``): ``begin`` runs in a process that cannot import ``bpy``, as
the host tests that build the transcript do (VM CI 2026-11-13: an import of ``learn_ui`` there stopped
``test_native_chat_history``). The running app's side is ``test_blender_learn_cards`` (off: expanded steps draw the
rows they drew before learn).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import angelo_agent  # noqa: E402
from angelo_agent import learn_cards, learn_option, learn_rows  # noqa: E402

STEP = {"role": "activity", "call_id": "s1", "tool": "angelo_scene_program", "status": "completed",
        "arguments": {"mode": "APPLY", "operations": [{"kind": "mesh_bevel", "parameters": {}}]},
        "result": {"status": "committed"}}
OLDER = dict(STEP, call_id="s0")


def refuse(*_args, **_kwargs):
    raise AssertionError("learn work while Teach me as you work is off")


class Chat:
    """The chat on screen, with Teach me as you work on or off in a state folder of its own (``learn_option``)."""

    def __init__(self, teaching):
        self.messages = [{"role": "user", "text": "a"}, OLDER, {"role": "user", "text": "b"}, STEP]
        self.state = {"id": "chat-1", "messages": self.messages}
        self.folder = tempfile.TemporaryDirectory()
        learn_option.use(self.folder.name)
        self.set(teaching)

    def set(self, on):
        learn_option.set_teaching(self.folder.name, "chat-1", on)

    def modules(self):
        """A stand-in for the resolver (Blender-side), which only a card that is on reaches."""
        resolver = types.SimpleNamespace(card=lambda message: ("step",))
        return [mock.patch.dict(sys.modules, {"angelo_agent.learn_resolver": resolver}),
                mock.patch.object(angelo_agent, "learn_resolver", resolver, create=True)]

    def close(self):
        learn_option.forget()
        self.folder.cleanup()


class Off(unittest.TestCase):
    def setUp(self):
        learn_rows.forget()
        self.addCleanup(learn_rows.forget)
        self.chat = Chat(teaching=False)
        self.addCleanup(self.chat.close)
        for patch in self.chat.modules():
            patch.start()
            self.addCleanup(patch.stop)

    def test_off_by_default_and_read_once_per_draw(self):
        self.assertFalse(learn_rows._on)   # Nothing read yet: off.
        self.assertFalse(learn_rows.begin(self.chat))
        self.assertFalse(learn_rows.begin(None))   # No chat on screen.

    def test_no_row_is_touched_and_nothing_of_learn_runs(self):
        learn_rows.begin(self.chat)
        expanded = {"s0", "s1"}   # Both opened by the person, and s1 is the latest request's.
        with mock.patch.object(learn_cards, "has_card", refuse), \
                mock.patch.object(sys.modules["angelo_agent.learn_resolver"], "card", refuse):
            for message in (STEP, OLDER):
                for opened in (expanded, set()):
                    with self.subTest(step=message["call_id"], opened=bool(opened)):
                        found, below = learn_rows.under(message, "activity", message["call_id"], opened, 600, 13,
                                                        18, 7)
                        self.assertIs(found, message)
                        self.assertEqual(below, 7)
                        self.assertIsNone(learn_rows.block(message, "activity", message["call_id"], opened, 600,
                                                           13, 18))
                        self.assertFalse(learn_rows.taught(message))
            self.assertEqual(learn_rows.draw_under(None, STEP, 0, 0, 600, {}), 0)

    def test_turned_off_mid_session_shows_nothing_on_the_next_draw(self):
        self.chat.set(True)
        self.assertTrue(learn_rows.begin(self.chat))
        self.chat.set(False)
        learn_rows.begin(self.chat)
        with mock.patch.object(learn_cards, "has_card", refuse):
            self.assertIs(learn_rows.under(STEP, "activity", "s1", {"s1"}, 600, 13, 18, 0)[0], STEP)


class On(unittest.TestCase):
    def setUp(self):
        learn_rows.forget()
        self.addCleanup(learn_rows.forget)
        self.chat = Chat(teaching=True)
        self.addCleanup(self.chat.close)
        for patch in self.chat.modules():
            patch.start()
            self.addCleanup(patch.stop)
        learn_rows.begin(self.chat)

    def test_the_latest_request_is_kept_open_and_an_earlier_step_only_when_opened(self):
        self.assertTrue(learn_rows.taught(STEP))
        self.assertFalse(learn_rows.taught(OLDER))
        shown = []
        with mock.patch.object(learn_rows, "block", lambda message, role, identity, expanded, *rest: shown.append(
                (identity, identity in expanded)) or None):
            learn_rows.under(OLDER, "activity", "s0", set(), 600, 13, 18, 0)
            learn_rows.under(OLDER, "activity", "s0", {"s0"}, 600, 13, 18, 0)
        self.assertEqual(shown, [("s0", False), ("s0", True)])   # Asked only while on; block decides.


def _tree(name):
    return ast.parse((ADDON / name).read_text(encoding="utf-8"))


class Source(unittest.TestCase):
    def test_the_draw_reads_the_option_before_it_builds_any_row(self):
        build = next(node for node in _tree("chat_blocks.py").body if isinstance(node, ast.FunctionDef)
                     and node.name == "build")
        begin = next(index for index, statement in enumerate(build.body)
                     if "learn_rows.begin" in ast.unparse(statement))
        loop = next(index for index, statement in enumerate(build.body) if isinstance(statement, ast.For))
        self.assertLess(begin, loop)

    def test_under_checks_the_option_before_anything_else(self):
        under = next(node for node in _tree("learn_rows.py").body if isinstance(node, ast.FunctionDef)
                     and node.name == "under")
        first = next(statement for statement in under.body if not (
            isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)))   # After the docstring.
        self.assertEqual(ast.unparse(first), "if not _on:\n    return (message, below)")

    def test_only_register_and_the_transcript_import_learn(self):
        importers = set()
        for path in sorted(ADDON.glob("*.py")):
            if path.name.startswith("learn_"):
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [alias.name for alias in getattr(node, "names", ())] if isinstance(
                    node, (ast.Import, ast.ImportFrom)) else []
                module = getattr(node, "module", None) or ""
                if any(name.startswith("learn_") for name in names) or module.startswith("learn_") or \
                        ".learn_" in module:
                    importers.add(path.name)
        self.assertEqual(importers, {"__init__.py", "chat_blocks.py", "chat_rows.py"})

    def test_the_option_is_read_where_blender_is_not(self):
        script = ("import sys, types; sys.path.insert(0, %r); sys.modules['bpy'] = None\n"
                  "from angelo_agent import chat_blocks, learn_rows\n"
                  "learn_rows.begin(types.SimpleNamespace(state={'id': 'chat-1', 'messages': []}))\n"
                  "assert 'angelo_agent.learn_ui' not in sys.modules and 'angelo_agent.ui' not in sys.modules\n"
                  % str(ROOT / "scripts/addons_core"))
        done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])

    def test_no_learn_module_keeps_a_handler_a_subscription_or_a_timer(self):
        for path in sorted(ADDON.glob("learn_*.py")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("bpy.app.handlers", source)
                self.assertNotIn("msgbus", source)
                timers = source.count("bpy.app.timers.register")
                self.assertEqual(timers, 1 if path.name == "learn_ui.py" else 0)   # The card's one-shot after a click.


if __name__ == "__main__":
    unittest.main()
