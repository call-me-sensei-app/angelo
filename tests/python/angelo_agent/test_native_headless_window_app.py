# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hidden-window session, the hidden app's side (``headless_window_app``): its session facts, the launcher's pipe,
**every end** (close, the pipe's end, the person's hand-over, SIGTERM, idle, a cap: each with a busy turn) stopping
the turn, withdrawing its cards and saving beside the source, saves as it works, ``show`` and the app's decline.
Contract: doc/angelo/mcp_headless_window.rst, "Lifecycle"."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import queue
import shutil
import signal
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import approval_arming, headless_window_lock, headless_window_relay
from angelo_agent import headless_window_app as app
from angelo_agent.headless_window import (DEFAULT_CAPS, ENV_CAPS, ENV_CLIENT, ENV_FLAG, ENV_PIPE, ENV_SESSION,
                                          ENV_SOURCE)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_test_policy import environment_skip

# A hidden session registers on macOS only (headless_window.supported): its reveal and withdrawal are SIGUSR2 and
# SIGUSR1, which Windows lacks. The rest of the session (saves, ends, caps, the pipe, decline) runs everywhere.
POSIX_SIGNALS = unittest.skipUnless(hasattr(signal, "SIGUSR2"), environment_skip(
    "SIGUSR1/SIGUSR2 are POSIX; a hidden session registers on macOS only (headless_window.supported)"))

SOURCE_BYTES = b"BLENDER-v502 source scene"
EDITED = b"BLENDER-v502 edited by the agent"


class FakeBpy:
    """``bpy.data`` (the open file, its dirty flag), the save-version preference and the two ``wm`` operators."""

    def __init__(self, filepath="", dirty=True, fails=False):
        self.saved, self.quit, self.versions_seen, self.fails = [], False, [], fails
        self.data = SimpleNamespace(is_dirty=dirty, filepath=str(filepath))
        self.context = SimpleNamespace(preferences=SimpleNamespace(filepaths=SimpleNamespace(save_version=1),
                                                                   is_dirty=False))
        self.ops = SimpleNamespace(wm=SimpleNamespace(save_mainfile=self.save_mainfile,
                                                      quit_blender=self.quit_blender))

    def save_mainfile(self, filepath, check_existing):
        self.versions_seen.append(self.context.preferences.filepaths.save_version)
        self.context.preferences.is_dirty = True   # Setting a preference dirties them, as Blender does.
        if self.fails:
            raise RuntimeError("Error: cannot write (disk full)")
        Path(filepath).write_bytes(EDITED)
        self.saved.append(filepath)
        self.data.is_dirty = False
        return {'FINISHED'}

    def quit_blender(self):
        self.quit = True


class Chat:
    """A conversation whose turn runs until stopped, with a card waiting for the person."""

    def __init__(self, busy=True, card=True, settles=True):
        self.state, self.busy, self.stopped, self.declined, self.rows, self.version = {"id": "c1"}, busy, 0, [], [], 0
        self.settles = settles
        call = SimpleNamespace(id="c1/call-1", name="angelo_execute_python", approval=True, invocation_digest="d1")
        self.pending = {call.id: call} if card else {}

    def stop(self):
        self.stopped += 1
        if self.settles:
            self.busy = False

    def decline_tool(self, key):
        self.declined.append(key)
        self.pending.pop(key, None)

    def _append_row(self, row):
        self.rows.append(row)


def _reset():
    app._state.update(started=None, last_call=None, output=None, saved_at=None, ending=None, stopping=None,
                      registered=False, shown=False, chats=[], spent={}, changed=False)
    for key in ("facts", "hides", "controllers", "shown_by", "armed", "copy"):
        app._state.pop(key, None)
    while True:
        try:
            app._inbox.get_nowait()
        except queue.Empty:
            return


class AppTest(unittest.TestCase):
    def setUp(self):
        _reset()
        self.addCleanup(_reset)
        self.root = Path(tempfile.mkdtemp(prefix="shwa-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source = self.root / "scene.blend"
        self.source.write_bytes(SOURCE_BYTES)
        self.copy = self.root / "scene.angelo-1.blend"   # The launcher's working copy: the session's output.
        self.copy.write_bytes(SOURCE_BYTES)
        self.environ = {ENV_FLAG: "1", ENV_SESSION: "s" * 32, ENV_SOURCE: str(self.source), ENV_CLIENT: "claude-code",
                        ENV_CAPS: json.dumps({"minutes": 60, "usd": 1.0, "tokens": None})}
        patcher = unittest.mock.patch.dict(os.environ, self.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("release", "mark"):
            patcher = unittest.mock.patch.object(headless_window_lock, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        app._state["started"] = app._state["last_call"] = 0.0
        app._state["armed"] = True   # The agent has worked in this session (a test of the unarmed case says so).

    def no_spend(self):
        return unittest.mock.patch.object(app, "used", return_value={"minutes": 0, "usd": 0.0, "tokens": 0})

    def end(self, message=None, clock=1.0, chat=None, bpy=None):
        bpy, chat = bpy or FakeBpy(self.copy), chat or Chat()
        if message is not None:
            app._inbox.put(message)
        with self.no_spend():
            first = app.step(bpy, [chat], clock=lambda: clock)
        return bpy, chat, first

    def assert_ended_saved(self, bpy, chat, ending):
        self.assertEqual(app._state["ending"], ending)
        self.assertEqual(chat.stopped, 1, "the running turn is stopped, as the person's Stop")
        self.assertEqual(chat.declined, ["c1/call-1"], "its card is withdrawn: nobody will answer it")
        self.assertTrue(bpy.quit)
        self.assertEqual(self.copy.read_bytes(), EDITED, "saved beside the source")
        self.assertEqual(self.source.read_bytes(), SOURCE_BYTES, "never the source")

    def test_close_with_a_busy_turn_stops_it_withdraws_its_card_saves_and_quits(self):
        bpy, chat, answer = self.end({"close": True})
        self.assertIsNone(answer)
        self.assert_ended_saved(bpy, chat, "closed")

    def test_the_pipes_end_does_the_same(self):
        self.assert_ended_saved(*self.end({"pipe_end": True})[:2], "closed")

    def test_the_persons_hand_over_does_the_same(self):
        self.assert_ended_saved(*self.end({"close": True, "by": "person"})[:2], "handed_over")

    def test_sigterm_does_the_same(self):
        self.assert_ended_saved(*self.end({"close": True, "by": "terminated"})[:2], "terminated")

    def test_idle_does_the_same(self):
        self.assert_ended_saved(*self.end(clock=app.IDLE_EXIT)[:2], "idle")

    def test_a_cap_does_the_same(self):
        bpy, chat = FakeBpy(self.copy), Chat()
        with unittest.mock.patch.object(app, "used", return_value={"minutes": 1, "usd": 1.0, "tokens": 5}):
            app.step(bpy, [chat], clock=lambda: 60.0)
        self.assert_ended_saved(bpy, chat, "cap_usd")

    def test_a_turn_that_wont_settle_is_waited_for_only_so_long_then_saved(self):
        chat = Chat(settles=False)
        bpy, chat, answer = self.end({"close": True}, chat=chat)
        self.assertEqual(answer, app.STEP, "the stopped turn gets a moment to settle")
        self.assertFalse(bpy.quit)
        with self.no_spend():
            self.assertIsNone(app.step(bpy, [chat], clock=lambda: 1.0 + app.STOP_WAIT))
        self.assertEqual(chat.stopped, 1, "stopped once")
        self.assertTrue(bpy.quit)
        self.assertEqual(self.copy.read_bytes(), EDITED)

    def test_changes_are_saved_as_the_agent_works_and_at_once_when_it_is_idle(self):
        bpy, chat = FakeBpy(self.copy), Chat(card=False)
        with self.no_spend():
            app._state["saved_at"] = 0.0
            self.assertEqual(app.step(bpy, [chat], clock=lambda: app.SAVE_BUSY - 1), app.STEP)
            self.assertEqual(bpy.saved, [], "not every tick while a turn runs")
            app.step(bpy, [chat], clock=lambda: app.SAVE_BUSY)
            self.assertEqual(len(bpy.saved), 1, "but every SAVE_BUSY seconds")
            chat.busy, bpy.data.is_dirty = False, True
            app.step(bpy, [chat], clock=lambda: app.SAVE_BUSY + app.SAVE_IDLE)
        self.assertEqual(len(bpy.saved), 2, "idle: at once")
        self.assertEqual(bpy.versions_seen, [0, 0], "no .blend1 beside the person's file")
        self.assertEqual(bpy.context.preferences.filepaths.save_version, 1, "their preference restored")
        self.assertFalse(bpy.quit)

    def test_a_document_open_from_the_source_is_never_saved_and_comes_up_for_the_person(self):
        shown = []
        bpy, chat = FakeBpy(self.source), Chat()
        app._inbox.put({"close": True})
        with self.no_spend():
            app.step(bpy, [chat], clock=lambda: 1.0, show=lambda: shown.append(True))
        self.assertEqual(bpy.saved, [])
        self.assertEqual(self.source.read_bytes(), SOURCE_BYTES)
        self.assertFalse(bpy.quit, "never a silent loss")
        self.assertEqual(shown, [True])

    def test_a_failed_end_save_brings_the_window_up_instead_of_quitting(self):
        shown = []
        bpy, chat = FakeBpy(self.copy, fails=True), Chat()
        app._inbox.put({"pipe_end": True})
        with self.no_spend():
            self.assertIsNone(app.step(bpy, [chat], clock=lambda: 1.0, show=lambda: shown.append(True)))
        self.assertFalse(bpy.quit)
        self.assertEqual(shown, [True])
        self.assertEqual(app._state["shown_by"], "save_failed")
        self.assertIsNone(app.session(), "the person's window now, with the unsaved work")

    def test_the_person_bringing_it_up_makes_it_their_window(self):
        shown = []
        with self.no_spend():
            self.assertIsNone(app.step(FakeBpy(self.copy), [Chat()], clock=lambda: 1.0, visible=True,
                                       show=lambda: shown.append(True)))
        self.assertEqual((shown, app._state["shown_by"]), ([True], "person"))

    def test_the_persons_launch_reveals_rather_than_ends(self):
        shown, chat = [], Chat()
        app._inbox.put({"show": True, "by": "person"})
        with self.no_spend():
            app.step(FakeBpy(self.copy), [chat], clock=lambda: 1.0, show=lambda: shown.append(True))
        self.assertEqual(shown, [True])
        self.assertEqual((chat.stopped, chat.declined), (0, []), "its turn and cards stay as they are")

    def test_nothing_is_saved_while_a_card_waits(self):
        bpy, chat = FakeBpy(self.copy), Chat(busy=False)
        with self.no_spend():
            app.step(bpy, [chat], clock=lambda: 100.0)
        self.assertEqual(bpy.saved, [], "what the waiting call staged is not approved")
        chat.pending.clear()
        with self.no_spend():
            app.step(bpy, [chat], clock=lambda: 101.0)
        self.assertEqual(len(bpy.saved), 1)

    def test_the_persons_quit_withdraws_a_queued_reveal(self):
        shown = []
        app._inbox.put({"show": True, "by": "person"})
        app._inbox.put({"cancel_show": True})
        with self.no_spend():
            self.assertEqual(app.step(FakeBpy(self.copy, dirty=False), [Chat(busy=False, card=False)],
                                      clock=lambda: 1.0, show=lambda: shown.append(True)), app.STEP)
        self.assertEqual(shown, [])
        self.assertFalse(app._state["shown"])

    def test_the_lock_is_released_only_by_the_process_ending(self):
        with unittest.mock.patch.object(headless_window_lock, "release") as release:
            self.end({"close": True})
        release.assert_not_called()

    def test_a_record_that_cant_be_rewritten_keeps_the_saving_sigterm_handler(self):
        with unittest.mock.patch.object(headless_window_lock, "mark", side_effect=OSError("disk")), \
                unittest.mock.patch.object(signal, "signal") as handlers:
            app._inbox.put({"show": True})
            with self.no_spend():
                app.step(FakeBpy(self.copy), [Chat()], clock=lambda: 1.0, show=lambda: True)
        self.assertNotIn(signal.SIGTERM, [call.args[0] for call in handlers.call_args_list])

    def test_show_is_tried_again_until_the_switch_to_a_regular_app_takes(self):
        timers, answers = [], iter([False, False, True])
        fake = SimpleNamespace(app=SimpleNamespace(timers=SimpleNamespace(
            register=lambda function, first_interval: timers.append(function))))
        with unittest.mock.patch.dict(sys.modules, {"bpy": fake}):
            self.assertFalse(app._bring_up(lambda: next(answers)))
            while timers:
                timers.pop(0)()
        self.assertIsNone(next(answers, None), "tried until it took")

    def test_a_file_the_session_didnt_open_makes_it_the_persons_window(self):
        paths = iter(["", str(self.copy), str(self.copy), "/Users/x/their.blend"])   # Startup file first: no path.
        fake = SimpleNamespace(data=SimpleNamespace())
        with unittest.mock.patch.dict(sys.modules, {"bpy": fake}):
            for _ in range(4):
                fake.data.filepath = next(paths)
                app._loaded()
        self.assertEqual(app._inbox.get_nowait(), {"show": True, "by": "person"})
        self.assertTrue(app._inbox.empty(), "its own file, loaded again, is no reason")

    def test_the_apps_send_arms_saving(self):
        app._state["armed"] = False
        app.arm()
        self.assertTrue(app._state["armed"])

    def test_nothing_is_saved_before_the_agent_has_worked(self):
        app._state["armed"] = False
        bpy, chat = FakeBpy(self.copy), Chat(busy=False, card=False)
        app._inbox.put({"close": True})
        with self.no_spend():
            app.step(bpy, [chat], clock=lambda: 100.0)
        self.assertEqual(bpy.saved, [], "an unedited copy is never saved (its launcher removes it)")
        self.assertTrue(bpy.quit)

    def test_a_busy_turn_arms_the_session(self):
        app._state["armed"] = False
        with self.no_spend():
            app.step(FakeBpy(self.copy, dirty=False), [Chat(card=False)], clock=lambda: 1.0)
        self.assertTrue(app._state["armed"])

    def test_only_data_updates_mark_a_change(self):
        def update(kind, **flags):
            return SimpleNamespace(id=SimpleNamespace(id_type=kind), is_updated_geometry=flags.get("geometry", False),
                                   is_updated_shading=flags.get("shading", False),
                                   is_updated_transform=flags.get("transform", False))
        for updates, expected in (([update("OBJECT")], False),                  # A selection: no data flag.
                                  ([update("SCREEN", shading=True)], False),    # Interface, not the document.
                                  ([update("SCENE", shading=True)], True),      # A scene setting the agent changed.
                                  ([update("KEY", geometry=True)], True),
                                  ([update("PARTICLE", geometry=True)], True),
                                  ([update("MESH", geometry=True)], True),
                                  ([update("MATERIAL", shading=True)], True)):
            app._state["changed"] = False
            app._mark_changed(None, SimpleNamespace(updates=updates))
            self.assertEqual(app._state["changed"], expected, updates)
        app._state.update(armed=False, changed=False)
        app._mark_changed(None, SimpleNamespace(updates=[update("MESH", geometry=True)]))
        self.assertFalse(app._state["changed"], "the file's own load evaluation, before the agent worked")

    def test_an_edit_that_leaves_the_dirty_flag_off_is_still_saved(self):
        bpy, chat = FakeBpy(self.copy, dirty=False), Chat(busy=False, card=False)
        app._state["changed"] = True   # The handler saw a data update (test_only_data_updates_mark_a_change).
        with self.no_spend():
            app.step(bpy, [chat], clock=lambda: 100.0)
        self.assertEqual(len(bpy.saved), 1)
        self.assertFalse(app._state["changed"])

    def test_a_save_leaves_the_persons_preferences_unchanged(self):
        bpy = FakeBpy(self.copy)
        app.save(bpy)
        self.assertFalse(bpy.context.preferences.is_dirty)

    def test_a_chat_angelo_closed_still_counts_its_spend(self):
        from angelo_agent import goal_probes
        first, second = Chat(), Chat()
        second.state = {"id": "c2"}
        with unittest.mock.patch.object(goal_probes, "spent", side_effect=[(10, 0.4), (20, 0.7), (25, 0.9)]):
            app.used([first, second])
            self.assertEqual(app.used([second])["usd"], 1.3, "c1's 0.4 stays counted")

    def test_the_apps_decline_declines_that_card_only_and_is_recorded(self):
        chat = Chat(busy=False)
        call = chat.pending["c1/call-1"]
        app._inbox.put({"decline": "0" * 16})
        with self.no_spend():
            app.step(FakeBpy(self.copy, dirty=False), [chat], clock=lambda: 1.0)
        self.assertEqual(chat.declined, [], "another card's id declines nothing")
        app._inbox.put({"decline": headless_window_relay.card_id(call)})
        with self.no_spend():
            app.step(FakeBpy(self.copy, dirty=False), [chat], clock=lambda: 2.0)
        self.assertEqual(chat.declined, ["c1/call-1"])
        self.assertIn("answered through claude-code", chat.rows[-1]["text"])
        self.assertIsNone(app._state["ending"], "a decline never ends the session")

    def test_nothing_answers_approve(self):
        self.assertEqual(app.handle({"approve": "x"}), (None, None))
        self.assertEqual(app.handle({"relay": "r", "answer": "approve"}), (None, None))

    @POSIX_SIGNALS
    def test_show_makes_it_the_persons_window_with_its_cards_shields_restarted(self):
        bpy, shown, marks = FakeBpy(self.copy), [], []
        app._inbox.put({"show": True})
        app._inbox.put({"pipe_end": True})
        before = approval_arming._restarts[0]
        order = []
        with unittest.mock.patch.object(headless_window_lock, "mark",
                                        side_effect=lambda session: (marks.append(session), order.append("mark"))), \
                unittest.mock.patch.object(signal, "signal",
                                           side_effect=lambda *args: order.append(args)) as handlers:
            self.assertIsNone(app.step(bpy, [Chat()], clock=lambda: 1.0, show=lambda: shown.append(True)))
        self.assertEqual(order[0], "mark", "the record names no session before anything else changes")
        self.assertEqual({call.args for call in handlers.call_args_list},
                         {(signal.SIGTERM, signal.SIG_DFL), (signal.SIGUSR2, signal.SIG_IGN),
                          (signal.SIGUSR1, signal.SIG_IGN)},
                         "a second SIGUSR2 (or SIGUSR1) is ignored; SIGTERM is Blender's own again")
        self.assertEqual(shown, [True])
        self.assertEqual(marks, [None], "it holds the state folder as the person's window, with no session")
        self.assertEqual(approval_arming._restarts[0], before + 1, "cards drawn while hidden start their shield again")
        self.assertFalse(bpy.quit)
        self.assertIsNone(app.session())
        self.assertIsNone(app.state(None))

    def test_unreadable_spend_counts_as_past_the_cap(self):
        broken = SimpleNamespace(telemetry=None, state={"id": "c1"})
        spent = app.used([broken])
        self.assertEqual(app.cap_reached(app.caps(), spent), "usd")

    def test_spend_is_counted_across_the_sessions_chats(self):
        from angelo_agent import goal_probes
        first, second = Chat(), Chat()
        second.state = {"id": "c2"}
        with unittest.mock.patch.object(goal_probes, "spent", side_effect=[(10, 0.4), (20, 0.7)]):
            self.assertEqual(app.used([first, second])["usd"], 1.1)

    def test_caps_from_the_environment_never_widen_the_defaults(self):
        self.assertEqual(app.caps({ENV_CAPS: json.dumps({"usd": 1e9, "minutes": 5})}), dict(DEFAULT_CAPS, minutes=5))

    def test_a_hidden_session_answers_only_the_app_that_started_it(self):
        self.assertFalse(app.foreign("claude-code", self.environ))
        self.assertTrue(app.foreign("codex", self.environ))
        self.assertFalse(app.foreign("codex", {}), "the person's window serves every connected app")

    def test_the_pipe_must_name_this_session_first(self):
        for lines, expected in ((['{"session": "s"}', '{"close": true}'], [{"close": True}]),
                                (['{"session": "forged"}', '{"show": true}'], []),
                                (['{"show": true}'], [])):
            read, write = os.pipe()
            with os.fdopen(write, "w") as pipe:
                pipe.write("\n".join(lines) + "\n")
            app._read_pipe(read, "s")
            got = []
            while True:
                message = app._inbox.get_nowait()
                if message.get("pipe_end"):
                    break
                got.append(message)
            self.assertEqual(got, expected, lines)

    @POSIX_SIGNALS
    def test_register_takes_the_session_out_of_the_environment_and_fails_closed_without_the_lock(self):
        environ = dict(self.environ, **{ENV_PIPE: "x"})
        _reset()
        modules = {"bpy": SimpleNamespace(app=SimpleNamespace(timers=SimpleNamespace(register=lambda *a, **k: None),
                                                              handlers=SimpleNamespace(depsgraph_update_post=[],
                                                                                       load_post=[])))}
        import angelo_agent
        from angelo_agent import headless_window_appkit
        ui = SimpleNamespace(state_directory=lambda: str(self.root), _controllers={})
        modules["angelo_agent.ui"] = ui
        with unittest.mock.patch.object(headless_window_lock, "take", return_value=False), \
                unittest.mock.patch.object(headless_window_appkit, "hide_app"), \
                unittest.mock.patch.object(angelo_agent, "ui", ui, create=True), \
                unittest.mock.patch.dict(sys.modules, modules), unittest.mock.patch.object(signal, "signal"):
            self.assertTrue(app.register(environ))
        self.assertFalse(any(name in environ for name in app.SESSION_NAMES), "children never inherit a session")
        self.assertEqual(app._state["ending"], "window_running", "another Angelo holds the state folder")


if __name__ == "__main__":
    unittest.main()
