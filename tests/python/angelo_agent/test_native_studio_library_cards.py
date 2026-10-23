# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's library row asks in a centred Angelo card, not a stock popup at the window's edge (1.0.5 L1-L4).

On a stand-in ``bpy``. Per dialog: Turn off (the copies' size on Also delete its copies, the warning only while it
deletes, the confirm destructive only then) and Free up space (it says Media Studio keeps everything, destructive);
Return never deletes; the confirm runs the operator's unchanged ``execute`` with the person's choice and Cancel runs
nothing; the card is centred over the editor where the person asked, the Preferences window's for the question after
removing the library there. The running app's proof is ``test_blender_studio_library_cards``.
"""

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

calls = []


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    handlers.__getattr__ = lambda name: []
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text),
                              background=True, handlers=handlers, timers=SimpleNamespace(register=lambda *a, **k: None))
    angelo = SimpleNamespace(studio_library_off=lambda *a, **k: calls.append(("off", a, k)),
                             studio_library_free=lambda *a, **k: calls.append(("free", a, k)))
    bpy.ops = SimpleNamespace(angelo=angelo)
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "bpy.app": bpy.app,
            "bpy.app.handlers": handlers}


with mock.patch.dict(sys.modules, fake_bpy()):
    from angelo_agent import studio_library_ops as ops
LOADED = {"bpy": sys.modules.get("bpy")}


class View:
    _font = _mono = 0

    def measure(self, text, size, font=0):
        return len(text) * size * 0.5

    def wrap(self, text, width, size, font=0):
        return [(text, 0)]


def plan(card, width=1400, height=900):
    return card.layout(View(), width, height, 1.0, None)


def texts(p):
    return [op[1] for op in p.ops if op[0] == "text"]


def buttons(p):
    """{label: style} of the card's buttons."""
    return {op[2]: op[3] for op in p.ops if op[0] == "button"}


class CardTests(unittest.TestCase):
    def card(self, what, measured=3 * 1024 * 1024, linked=False):
        with mock.patch.object(ops.space, "size", lambda directory: measured), \
                mock.patch.object(ops, "_directory", lambda: "/lib"), \
                mock.patch.object(ops, "links_from_library", lambda: linked):
            return ops.LibraryCard(what, 77)

    def test_linked_models_start_with_also_delete_unticked(self):
        """Review 5 (S3): the open file links from the library, or it imports by linking: nothing is deleted unless
        the person ticks it, and the card says why."""
        card = self.card("off", linked=True)
        self.assertFalse(card.free)
        shown = " ".join(texts(plan(card)))
        self.assertIn(ops.LINKED, shown)
        self.assertIn(ops.WARNING[0], shown)
        self.assertEqual(buttons(plan(card))["Turn off"], "primary")

    def test_turn_off_asks_with_the_size_and_warns_only_while_deleting(self):
        card = self.card("off")
        p = plan(card)
        shown = " ".join(texts(p))
        self.assertIn(ops.TURN_OFF_TITLE, shown)
        self.assertIn("Also delete its copies (3.0 MB)", shown.replace("3 MB", "3.0 MB"))
        self.assertIn(ops.WARNING[0], shown)
        self.assertEqual(buttons(p), {"Cancel": "secondary", "Turn off": "destructive"})
        card.act("free", None, None)
        p = plan(card)
        self.assertNotIn(ops.WARNING[0], " ".join(texts(p)), "nothing is deleted: no warning")
        self.assertEqual(buttons(p)["Turn off"], "primary", "turning off alone is not destructive")

    def test_free_up_says_media_studio_keeps_everything_and_is_destructive(self):
        p = plan(self.card("free"))
        shown = " ".join(texts(p))
        self.assertIn("Media Studio keeps every model and image", shown)
        self.assertIn(ops.WARNING[0], shown)
        self.assertEqual(buttons(p)["Free up space"], "destructive")
        self.assertTrue(shown.startswith("Free up"), "the size in the title")

    def test_centred_over_the_editor_and_return_never_deletes(self):
        card = self.card("free")
        p = plan(card, 1400, 900)
        x, y, w, h = p.card
        self.assertAlmostEqual(x + w / 2, 700, delta=1)
        self.assertIsNone(card.default_action)
        self.assertEqual(card.cancel_action, "cancel")
        window = SimpleNamespace(screen=SimpleNamespace(areas=[SimpleNamespace(as_pointer=lambda: 77)]))
        self.assertIsNotNone(card.target_area(window))

    def test_confirm_runs_the_unchanged_execute_and_cancel_runs_nothing(self):
        host = SimpleNamespace(close=lambda how: None)
        calls.clear()
        card = self.card("off")
        card.act("free", None, host)   # Unticked: turn off only.
        card.act("confirm", None, host)
        self.assertEqual([(c[0], c[1], sorted(c[2])) for c in calls], [("off", ('EXEC_DEFAULT',), ["free", "token"])])
        self.assertFalse(calls[0][2]["free"])
        self.assertTrue(ops.consume_token(calls[0][2]["token"]), "the card's token, good once")
        self.assertFalse(ops.consume_token(calls[0][2]["token"]))
        calls.clear()
        self.card("free").act("cancel", None, host)
        self.assertEqual(calls, [])
        self.card("free").act("confirm", None, host)
        self.assertEqual([(c[0], sorted(c[2])) for c in calls], [("free", ["token"])])   # Only the card confirms.

    def test_no_stock_popup_and_the_preferences_question_shows_in_preferences(self):
        source = (ADDON / "studio_library_ops.py").read_text()
        self.assertNotIn("invoke_props_dialog", source)
        self.assertIn('return ask("off", context)', source)
        self.assertIn('return ask("free", context)', source)
        requested = []
        prefs = SimpleNamespace(type='PREFERENCES', as_pointer=lambda: 5)
        context = SimpleNamespace(area=None, window=SimpleNamespace(screen=SimpleNamespace(areas=[prefs])))
        host = SimpleNamespace(request=requested.append, pump=lambda ready: None)
        with mock.patch.dict(sys.modules, {"angelo_agent.card_host": host}), \
                mock.patch.object(ops.space, "size", lambda directory: 0), mock.patch.object(ops, "_directory", lambda: ""), \
                mock.patch.object(ops, "links_from_library", lambda: False):
            import angelo_agent
            with mock.patch.object(angelo_agent, "card_host", host, create=True):
                self.assertEqual(ops.ask("free", context), {'FINISHED'})
        self.assertEqual(requested[0].area_pointer, 5)


class FreePlumbingTests(unittest.TestCase):
    """Review 6 (1): Free up space never stays dead for the session."""

    def setUp(self):
        self.timers, self.reports = [], []
        timers = SimpleNamespace(register=lambda f, **k: self.timers.append((f, k)), is_registered=lambda f: False)
        for patch in (mock.patch.object(ops.bpy.app, "timers", timers),
                      mock.patch.object(ops, "report", lambda level, text: self.reports.append((level, text))),
                      mock.patch.object(ops, "_directory", lambda: "/lib"), mock.patch.object(ops, "active", lambda: False),
                      mock.patch.object(ops.host, "redraw_studio", lambda: None, create=True),
                      mock.patch.dict(ops._freeing, result=None, done=False, running=False)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_start_that_fails_never_strands_it(self):
        with mock.patch.object(ops.space, "free_later", side_effect=ValueError("on")):
            ops.start_free()
        self.assertFalse(ops._freeing["running"])
        started = []
        with mock.patch.object(ops.space, "free_later", lambda *a: started.append(a)):
            ops.start_free()
        self.assertEqual(len(started), 1, "the next Free up space runs")

    def test_the_worker_ends_running_and_the_poll_survives_a_file_load(self):
        dones = []
        with mock.patch.object(ops.space, "free_later", lambda directory, active, done: dones.append(done)):
            ops.start_free()
        self.assertTrue(ops._freeing["running"])
        self.assertEqual([k.get("persistent") for _f, k in self.timers], [True], "a load never drops the poll")
        dones[0]((2048, 1))
        self.assertFalse(ops._freeing["running"], "ended by the worker itself")
        self.assertIsNone(ops._poll_free())
        self.assertEqual(self.reports, [('INFO', "Freed 2.0 KB")])
        dones.clear()
        with mock.patch.object(ops.space, "free_later", lambda directory, active, done: dones.append(done)):
            ops.start_free()
        dones[0](ops.space.ON)
        ops._poll_free()
        self.assertEqual(self.reports[-1], ('WARNING', "The library is on again, so nothing was deleted"))

    def test_a_token_is_good_once_and_only_the_cards(self):
        token = ops.issue_token()
        self.assertFalse(ops.consume_token(""))
        self.assertFalse(ops.consume_token("made-up"))
        self.assertTrue(ops.consume_token(token))
        self.assertFalse(ops.consume_token(token))


if __name__ == "__main__":
    unittest.main()
