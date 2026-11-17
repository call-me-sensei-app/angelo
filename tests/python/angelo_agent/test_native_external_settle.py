# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Answered work never counts as running: a call or command right after an answer is admitted before the tick's sweep.

An answer goes out (from a call's worker thread, or from the headless host's ``finish_answers``) before the main
thread's next sweep closes the work it ended (``ExternalApps.pump_apps``, ``ExternalHost.close_finished``), and the
client's next call can be admitted ahead of that sweep: order within one main-thread tick decided whether it was
refused "stale: ... earlier work is still finishing" (an app) or "busy: ... still has calls in flight" (a CLI's
``session new``). Without the window a reload tells no conversation, so a request that could still take calls stayed
open until it idled out (120 s) and every call meanwhile was refused. Found by the 1.0.10 stat-before-read helper in
``test_blender_headless_native_commits`` (1 of 7 runs). Admission now closes what is over and has nothing in flight,
as the sweep would (``ExternalApps.settle``); work still in flight is still refused.

Every test here holds the sweeps (``held_sweeps``), the order the race produced at random. The call that moves the
app's conversation says so in the window, where the person changed what it shows: until a read's answer has gone out
there, a read runs with ``MOVED`` and anything else does not run (``MOVED_NOT_RUN``), calls sent together included;
without the window the call that reloaded already said it. An answer still on
its way to the host counts as in flight, and the headless host's answers held back for a save or reload go out before
the next request is handled (break-it reviews M1 and M-A, 2026-11-05). Contract: doc/angelo/external_mcp.rst.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

import contextlib
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import claude_direct  # noqa: E402
from angelo_agent.external_apps import MOVED, MOVED_NOT_RUN  # noqa: E402
from angelo_agent.external_clients import SCRIPT_FILE_TOOL  # noqa: E402
from angelo_agent.external_host import ClientRegistry  # noqa: E402
import test_native_external_mcp as window_harness  # noqa: E402  (modules: their tests are not collected again here)
import test_native_external_cli_host as cli_harness  # noqa: E402
import test_native_headless_script_files as script_harness  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

STALE = "earlier work is still finishing"


@contextlib.contextmanager
def held_sweeps(host):
    """The tick's sweeps have not run since the last answer: what a call admitted in the same tick finds."""
    with patch.object(host, "pump_apps", lambda: None), patch.object(host, "close_finished", lambda: None):
        yield


@unittest.skipIf(sys.platform == "win32", window_harness.LISTENER)
class WindowTests(unittest.TestCase):
    """Angelo's window: an app's next call after a file change, and a call still in flight."""
    setUp, tick, server, rpc = (window_harness.AppTests.setUp, window_harness.AppTests.tick,
                                window_harness.AppTests.server, window_harness.AppTests.rpc)
    call, conversation = window_harness.AppTests.call, window_harness.AppTests.conversation

    def load_another_file(self, old):
        """What a load does to the app's conversation (``document_work.document_changed``): invalid, its calls
        cancelled, its request stopped; the request itself closes at the next sweep."""
        self.env.document = ("doc-b", 1)
        old.set_document(self.env.document)

    def test_the_call_right_after_a_file_change_moves_on_without_waiting_for_the_sweep(self):
        self.call("angelo_inspect_scene")
        old = self.conversation()
        with held_sweeps(self.host):
            self.load_another_file(old)
            result = self.call("angelo_inspect_scene")
        self.assertFalse(result["isError"], result)
        self.assertNotIn(STALE, json.dumps(result))
        self.assertIn(MOVED, json.dumps(result), "the app's model is told where this call ran")
        self.assertNotEqual(self.conversation().state["id"], old.state["id"], "a fresh conversation")
        self.assertFalse(old.busy, "the answered request was closed, as the sweep closes it")
        self.assertEqual(self.executor.calls.count("angelo_inspect_scene"), 2)
        self.assertNotIn(MOVED, json.dumps(self.call("angelo_inspect_scene")), "said once, by the call that moved")

    def test_the_call_after_a_switch_to_another_scene_moves_on_at_once(self):
        """The window showing another scene of the same file never invalidates the chat (document_scene), so its
        request could still take calls and stayed open until it idled out: every call meanwhile was "stale"."""
        self.call("angelo_inspect_scene")
        old = self.conversation()
        self.env.document = ("doc-a", 2)
        result = self.call("angelo_inspect_scene")
        self.assertFalse(result["isError"], result)
        self.assertIn(MOVED, json.dumps(result), "never a silent move to the scene the window shows now")
        self.assertNotEqual(self.conversation().state["id"], old.state["id"])
        self.assertFalse(old.busy)

    def test_an_edit_after_a_switch_does_not_run_before_the_model_is_told(self):
        self.call("angelo_inspect_scene")
        old = self.conversation()
        self.env.document = ("doc-a", 2)
        refused = self.call("angelo_create_cube", window_harness.CUBE)
        self.assertTrue(refused["isError"], refused)
        self.assertIn(MOVED_NOT_RUN, json.dumps(refused))
        self.assertNotIn("create_cube", " ".join(self.executor.calls))
        self.assertNotEqual(self.conversation().state["id"], old.state["id"], "moved, never stuck")
        self.assertIn(MOVED_NOT_RUN, json.dumps(self.call("angelo_create_cube", window_harness.CUBE)), "not read yet")
        self.assertIn(MOVED, json.dumps(self.call("angelo_inspect_scene")))
        again = self.call("angelo_create_cube", window_harness.CUBE)
        self.assertFalse(again["isError"], again)
        self.assertNotIn(MOVED, json.dumps(again))

    def answer(self, out, identity):
        lines = [json.loads(line) for line in out.getvalue().splitlines() if line]
        return next((line["result"] for line in lines if line.get("id") == identity), None)

    def test_an_edit_sent_with_a_read_after_a_switch_does_not_run_before_the_read_is_answered(self):
        """MCP clients send a batch of calls at once: none of them saw the read's MOVED."""
        self.call("angelo_inspect_scene")
        old = self.conversation()
        self.env.document = ("doc-a", 2)
        gate, answered = threading.Event(), claude_direct.DirectRequest.answered

        def held(direct, key, value):   # The read's answer is not out yet.
            gate.wait(CAP_S)
            answered(direct, key, value)
        (read, read_out), (edit, edit_out) = self.server(), self.server()
        deadline = time.monotonic() + CAP_S
        with patch.object(claude_direct.DirectRequest, "answered", held):
            read.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "angelo_inspect_scene", "arguments": {}}})
            while self.conversation() is old and time.monotonic() < deadline:
                self.tick()
                time.sleep(0.003)
            edit.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "angelo_create_cube", "arguments": window_harness.CUBE}})
            while self.answer(edit_out, 2) is None and time.monotonic() < deadline:
                self.tick()
                time.sleep(0.003)
            gate.set()
            while self.answer(read_out, 1) is None and time.monotonic() < deadline:
                self.tick()
                time.sleep(0.003)
        self.assertIn(MOVED_NOT_RUN, json.dumps(self.answer(edit_out, 2)))
        self.assertIn(MOVED, json.dumps(self.answer(read_out, 1)))
        self.assertNotIn("create_cube", " ".join(self.executor.calls))

    def test_a_tool_still_pending_at_the_switch_still_makes_the_app_wait(self):
        self.call("angelo_inspect_scene")
        old = self.conversation()
        work = self.host._work[old.state["id"]]
        self.env.document = ("doc-a", 2)
        with held_sweeps(self.host), patch.object(work.context, "tools_pending", lambda: True):
            refused = self.call("angelo_inspect_scene")
            self.assertIn(STALE, json.dumps(refused))
            self.assertFalse(work.closed, "never closed under a tool Angelo still holds")
            self.assertEqual(self.conversation().state["id"], old.state["id"])
        result = self.call("angelo_inspect_scene")
        self.assertFalse(result["isError"], result)
        self.assertTrue(work.closed)

    def test_a_call_still_in_flight_when_the_file_changes_still_makes_the_app_wait(self):
        self.call("angelo_inspect_scene")
        old = self.conversation()
        work = self.host._work[old.state["id"]]
        work.direct.arrived()   # A call's worker thread still has the request (DirectRequest.busy).
        with held_sweeps(self.host):
            self.load_another_file(old)
            refused = self.call("angelo_inspect_scene")
            self.assertTrue(refused["isError"], refused)
            self.assertIn(STALE, json.dumps(refused))
            self.assertEqual(self.conversation().state["id"], old.state["id"], "never rotated under a running call")
            self.assertFalse(work.closed)
            self.assertTrue(old.busy)
            self.assertEqual(self.executor.calls.count("angelo_inspect_scene"), 1, "nothing else ran meanwhile")
            work.direct.left()   # Its worker ended.
            result = self.call("angelo_inspect_scene")
        self.assertFalse(result["isError"], result)
        self.assertTrue(work.closed)
        self.assertNotEqual(self.conversation().state["id"], old.state["id"])

    def test_a_task_started_right_after_a_file_change_is_not_refused_as_stale(self):
        from angelo_agent.external_clients import TASKS
        self.call("angelo_inspect_scene")
        old = self.conversation()
        self.assertIn(TASKS, window_harness.AppRegistry(self.state).load()[self.name]["levels"])
        with held_sweeps(self.host):
            self.load_another_file(old)
            result = self.call("angelo_task", {"task": "model a bridge"})
        # The window stand-in has no connection: the task reaches preparation in a fresh conversation, never "stale".
        self.assertNotIn(STALE, json.dumps(result))
        self.assertIn("no connection", json.dumps(result))
        self.assertFalse(old.busy)
        self.assertIn(MOVED, json.dumps(self.call("angelo_inspect_scene")), "the app's next tool call is told")

    def test_a_call_refused_before_it_ran_never_holds_up_the_next_call(self):
        """Break-it review 2026-11-13, finding 2: in the window too, a script file call refused before it reached the
        request's order never ended its turn, so the request's next call waited for good."""
        refused = self.call(SCRIPT_FILE_TOOL, {"file": str(Path(self.state) / "missing.py")})
        self.assertTrue(refused["isError"], refused)
        self.assertIn("nothing ran", json.dumps(refused))
        answer = self.rpc("tools/call", {"name": "angelo_inspect_scene", "arguments": {}}, timeout=10)
        self.assertIsNotNone(answer, "the next call was answered")
        self.assertFalse(answer["result"]["isError"], answer)

    def test_an_answer_on_its_way_to_the_host_is_still_in_flight(self):
        """Between a call's worker leaving the request and its answer going out, the request is not finishable: a
        request closed or rotated then could be followed by work the late answer (headless: its reload) undoes."""
        self.call("angelo_inspect_scene")
        work = self.host._work[self.conversation().state["id"]]
        gate, answers, answered = threading.Event(), [], work.direct.answered

        def late(key, value):   # A worker pre-empted right after ``left()``.
            gate.wait(CAP_S)
            answered(key, value)
        with patch.object(work.direct, "answered", late):
            work.submit(("call", "late"), "angelo_inspect_scene", {}, answers.append)
            deadline = time.monotonic() + CAP_S
            while (work.direct.busy() or not self.executor.calls.count("angelo_inspect_scene") == 2) \
                    and time.monotonic() < deadline:
                self.tick()
                time.sleep(0.003)
            self.assertFalse(work.direct.busy(), "its worker has left the request")
            self.assertFalse(work.finishable(), "its answer has not reached the host")
            gate.set()
            while not answers and time.monotonic() < deadline:
                time.sleep(0.003)
        self.assertTrue(answers)
        self.assertTrue(work.finishable())


@unittest.skipIf(sys.platform == "win32", environment_skip("Needs the Unix-socket listener, which Windows does not "
                                                           "open yet (doc/angelo/external_cli.rst)"))
class CliTests(unittest.TestCase):
    """A local CLI client's ``session new`` right after its read was answered."""
    setUp, tick, cli = (cli_harness.ExternalHostTests.setUp, cli_harness.ExternalHostTests.tick,
                        cli_harness.ExternalHostTests.cli)

    def test_session_new_right_after_an_answered_read_rotates(self):
        with held_sweeps(self.host):
            read = self.cli("call", "angelo_inspect_scene")
            self.assertEqual(read["code"], "ok", read)
            old = self.env._controllers[ClientRegistry(self.state).load()["agent"]]
            self.assertTrue(old.busy, "the read's request is still open: the sweep has not run")
            rotated = self.cli("session", "new")
        self.assertEqual(rotated["code"], "ok", rotated)
        self.assertEqual(rotated["result"]["previous"], old.state["id"])
        self.assertFalse(old.busy)


@unittest.skipIf(sys.platform == "win32", environment_skip("Headless sessions are macOS and Linux for now"))
class HeadlessTests(unittest.TestCase):
    """Without the window (``HeadlessHost``): the call right after a reload, the case the built-app suite hit."""
    tick, call, open = (script_harness.HeadlessScriptFileTests.tick, script_harness.HeadlessScriptFileTests.call,
                        script_harness.HeadlessScriptFileTests.open)
    setUp, start_child = script_harness.HeadlessScriptFileTests.setUp, script_harness.HeadlessScriptFileTests.start_child
    approve, run_file = script_harness.HeadlessScriptFileTests.approve, script_harness.HeadlessScriptFileTests.run_file

    def text(self, result):
        return json.dumps(result)

    def held_sweeps(self):
        """The sweeps do not run from now until the call after ``state["armed"]`` is set has been admitted (or
        refused): the answer and the next call in one tick, the order the race produced at random."""
        host, original = self.host, type(self.host).handle_app_call
        state = {"held": True, "armed": False}

        def admitted(work):
            try:
                return original(host, work)
            finally:
                state["held"] = state["held"] and not state["armed"]
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(host, "handle_app_call", admitted))
        for name in ("pump_apps", "close_finished"):
            sweep = getattr(host, name)
            stack.enter_context(patch.object(host, name, lambda sweep=sweep: None if state["held"] else sweep()))
        return stack, state

    def test_the_call_right_after_a_failed_script_reload_is_admitted(self):
        self.approve()
        self.open()

        def fail():
            raise RuntimeError("on purpose")
        self.executor.on_run = fail
        loads = len(self.bpy.loads)
        held, state = self.held_sweeps()
        with held:
            failed = self.run_file()
            self.assertTrue(failed["isError"])
            self.assertIn("reloaded", self.text(failed))
            self.assertGreater(len(self.bpy.loads), loads, "the child reloaded the last saved state")
            self.executor.on_run = None
            state["armed"] = True
            result = self.call("angelo_inspect_scene")
            self.assertFalse(state["held"], "the sweeps were held until this call was admitted")
        self.assertFalse(result["isError"], result)
        self.assertNotIn(STALE, self.text(result))
        self.assertNotIn(MOVED, self.text(result), "no window; the failed call already said it reloaded")

    def test_the_call_after_a_reload_for_a_failed_save_is_admitted(self):
        """The edit itself succeeded, so its request could still take calls: nothing but the idle timer closed it."""
        self.open()
        wm = self.bpy.ops.wm
        saving = wm.save_as_mainfile
        wm.save_as_mainfile = lambda **_options: {"CANCELLED"}
        edit = self.call("angelo_create_cube", {"name": "Box", "size": 1, "location": [0, 0, 0]})
        wm.save_as_mainfile = saving
        self.assertIn("could not be saved", self.text(edit))
        result = self.call("angelo_inspect_scene")
        self.assertFalse(result["isError"], result)
        self.assertNotIn(STALE, self.text(result))
        again = self.call("angelo_create_cube", {"name": "Again", "size": 1, "location": [2, 0, 0]})
        self.assertFalse(again["isError"], again)
        self.assertIn("Saved as", self.text(again))
        self.assertTrue(self.host._work and all(work.finishable() for work in self.host._work.values()),
                        "every held answer was counted out as it went (finish_answers)")

    def test_the_call_after_a_script_opened_another_file_is_admitted(self):
        self.approve()
        self.open()
        self.executor.on_run = lambda: setattr(self.bpy.data, "filepath", str(self.folder / "other.blend"))
        held, state = self.held_sweeps()
        with held:
            result = self.run_file()
            self.assertIn("opened or saved as another file", self.text(result))
            self.executor.on_run = None
            state["armed"] = True
            again = self.call(SCRIPT_FILE_TOOL, {"file": str(self.script)})
        self.assertFalse(again["isError"], again)
        self.assertIn("Saved as", self.text(again))


    def test_answers_held_back_go_out_before_the_next_request_is_handled(self):
        """An answer waits for its save or reload (``finish_answers``); a request handled first could be admitted,
        even rotated and run, before that reload, which would then undo its edit (break-it review M1)."""
        self.open()
        host, order = self.host, []
        take, handle = host.service.work.take, type(host).handle_app_call

        def taken():
            work = take()
            if work is not None and work.kind == "app_call" and not order:
                host._answers.put((("other", "held"), "angelo_inspect_scene", False, None, {"content": []},
                                   lambda _value: order.append("answered")))
            return work

        def handled(work):
            order.append("handled")
            return handle(host, work)
        with patch.object(host.service.work, "take", taken), patch.object(host, "handle_app_call", handled):
            result = self.call("angelo_inspect_scene")
        self.assertFalse(result["isError"], result)
        self.assertEqual(order[:2], ["answered", "handled"])

    def test_a_later_call_sent_together_never_runs_before_an_earlier_calls_reload(self):
        """Agent-B2 (Devin audit, 2026-11-13; doc/angelo/external_mcp.rst): without the window an answer waits for its
        save or reload (``finish_answers``). A later call of the same request ran as soon as the earlier call's worker
        ended, so its edit could run before that reload, which then undid it while its answer said it was saved."""
        self.approve()
        self.open()
        host, wm = self.host, self.bpy.ops.wm
        saving, after_call, loaded = wm.save_as_mainfile, type(host).after_call, self.bpy.on_load
        failing, reloads = [], []

        def after(tool, current, value):   # The script's save fails, so its answer reloads the last saved state.
            failing.append(tool == SCRIPT_FILE_TOOL)
            try:
                return after_call(host, tool, current, value)
            finally:
                failing.pop()

        def save(**options):
            return {"CANCELLED"} if failing and failing[-1] else saving(**options)

        def on_load():   # How many of the later call's edits had run at each reload.
            reloads.append(self.executor.calls.count("angelo_create_cube"))
            loaded()
        wm.save_as_mainfile, self.bpy.on_load = save, on_load
        try:
            with patch.object(host, "after_call", after):
                out = self.sent_together((SCRIPT_FILE_TOOL, {"file": str(self.script)}),
                                         ("angelo_create_cube", {"name": "Again", "size": 1, "location": [2, 0, 0]}),
                                         ran=lambda: self.executor.calls.count("angelo_create_cube"))
        finally:
            wm.save_as_mainfile, self.bpy.on_load = saving, loaded
        self.assertEqual(sorted(out), [1, 2], "both calls were answered")
        self.assertIn("reloaded", self.text(out[1]), "the script's save failed: the last saved state again")
        self.assertEqual(reloads, [0], "no later call's edit ran before the earlier call's reload")
        edits = self.executor.calls.count("angelo_create_cube")
        if out[2]["isError"]:
            self.assertEqual(edits, 0, "a call that says it did not run did not run")
        else:
            self.assertEqual(edits, 1)
            self.assertIn("Saved as", self.text(out[2]), "its edit is in the saved file")

    def test_a_call_refused_before_it_ran_never_holds_up_the_calls_sent_with_it(self):
        """A script file call refused before it reached the request's order (no such file) never ended its turn, so
        a call sent with it waited for good (found with Agent-B2, 2026-11-13)."""
        self.open()
        out = self.sent_together((SCRIPT_FILE_TOOL, {"file": str(self.root / "work" / "missing.py")}),
                                 ("angelo_inspect_scene", {}), ran=lambda: False)
        self.assertEqual(sorted(out), [1, 2], "the call sent with it was answered too")
        self.assertIn("nothing ran", self.text(out[1]))

    def sent_together(self, first, second, ran, during=None):
        """``first`` and ``second`` ((tool, arguments)) sent in one batch, as an MCP client does: the server runs each
        on its own thread, and ``second`` is admitted to the request while ``first``'s answer is still on its way to
        the host (its worker pre-empted). That answer is held until ``ran()`` or for half a second after ``second``
        was admitted. Returns {1: first's answer, 2: second's} for the calls answered within the cap (only a call stuck
        behind a turn never ended reaches it)."""
        host, out = self.host, {}
        gate, held_keys, answered = threading.Event(), [], claude_direct.DirectRequest.answered

        def held(direct, key, value):
            if not held_keys:
                held_keys.append(key)
                gate.wait(CAP_S)
            answered(direct, key, value)

        def send(identity, tool, arguments):
            out[identity] = self.server.call(identity, {"name": tool, "arguments": arguments})
        threads = [threading.Thread(target=send, args=(1, *first), daemon=True),
                   threading.Thread(target=send, args=(2, *second), daemon=True)]
        deadline = time.monotonic() + CAP_S
        try:
            with patch.object(claude_direct.DirectRequest, "answered", held):
                threads[0].start()
                while not held_keys and time.monotonic() < deadline:   # The first call ended; its answer is held.
                    self.tick()
                    time.sleep(0.003)
                threads[1].start()
                settle = None
                while time.monotonic() < deadline:
                    self.tick()
                    time.sleep(0.003)
                    work = next(iter(host._work.values()), None)
                    if settle is None and work is not None and work.direct._tickets >= 2:
                        settle = time.monotonic() + 0.5   # window: fixed, the later call cannot run, so no signal
                        if during is not None:
                            during(work)
                    if ran() or (settle is not None and time.monotonic() >= settle):
                        break
                gate.set()
                while any(thread.is_alive() for thread in threads) and time.monotonic() < deadline:   # Stuck: cap.
                    self.tick()
                    time.sleep(0.003)
        finally:
            gate.set()
        return dict(out)

    def test_a_call_sent_while_a_script_saved_as_another_file_never_reloads_before_that_scripts_answer(self):
        """Break-it review 2026-11-13, finding 1 (predates Agent-B2): admission reloaded the session's file whenever
        Blender had another open, also while an earlier call that caused it was still unanswered. The script's own
        answer then found its file open again, saved and said "Saved as" while its edits were gone; it must say the
        script saved as another file (FOREIGN_FILE), the reload being its own."""
        self.approve()
        self.open()
        self.executor.on_run = lambda: setattr(self.bpy.data, "filepath", str(self.folder / "other.blend"))
        out = self.sent_together((SCRIPT_FILE_TOOL, {"file": str(self.script)}), ("angelo_inspect_scene", {}),
                                 ran=lambda: False)
        self.assertEqual(sorted(out), [1, 2])
        self.assertIn("opened or saved as another file", self.text(out[1]))
        self.assertNotIn("Saved as", self.text(out[1]), "never a save of the reloaded file in its name")

    def test_a_replay_while_a_turn_is_held_neither_ends_it_nor_misses_the_answer(self):
        """Break-it review 2026-11-13, finding 3: the launcher asks again under the same call ID every 25 s. A replay
        delivered while the call's answer is held gets that answer and never ends the call's turn."""
        self.open()
        host, replies, seen = self.host, [], {}

        def replay(work):   # The first call delivered again while its answer is held.
            label = host.client.label
            first = next(key for key, record in host._calls.items() if record["work"] is work and key[0] == label)
            payload = {"id": first[1], "tool": "angelo_create_cube",
                       "arguments": {"name": "Box", "size": 1, "location": [0, 0, 0]}}
            again = SimpleNamespace(client=host.client, payload=payload, admitted=False, complete=replies.append)
            host._again(again, host._calls[first], payload)
            seen["cubes"] = self.executor.calls.count("angelo_create_cube")
        out = self.sent_together(("angelo_create_cube", {"name": "Box", "size": 1, "location": [0, 0, 0]}),
                                 ("angelo_create_cube", {"name": "Again", "size": 1, "location": [2, 0, 0]}),
                                 ran=lambda: False, during=replay)
        self.assertEqual(sorted(out), [1, 2])
        self.assertEqual(seen["cubes"], 1, "the replay did not end the held turn: the second call had not run")
        self.assertEqual(len(replies), 1, "the replay got the call's answer")
        self.assertTrue(replies[0]["call"]["replayed"])
        self.assertEqual(self.executor.calls.count("angelo_create_cube"), 2, "each call ran once")
        self.assertTrue(all(work.finishable() for work in host._work.values()), "nothing left counted as in flight")

    def test_a_failed_script_that_ended_its_request_reloads_before_any_call_sent_with_it_runs(self):
        """Break-it review 2026-11-13, finding 3: a failed script ends its request and reloads (RELOADED); a call sent
        with it never runs before that reload."""
        self.approve()
        self.open()

        def fail():
            raise RuntimeError("on purpose")
        self.executor.on_run = fail
        reloads, loaded = [], self.bpy.on_load

        def on_load():
            reloads.append(self.executor.calls.count("angelo_create_cube"))
            loaded()
        self.bpy.on_load = on_load
        try:
            out = self.sent_together((SCRIPT_FILE_TOOL, {"file": str(self.script)}),
                                     ("angelo_create_cube", {"name": "Again", "size": 1, "location": [2, 0, 0]}),
                                     ran=lambda: self.executor.calls.count("angelo_create_cube"))
        finally:
            self.bpy.on_load = loaded
        self.assertEqual(sorted(out), [1, 2])
        self.assertIn("reloaded", self.text(out[1]))
        self.assertTrue(reloads and reloads[0] == 0, "no call sent with it ran before its reload")
        if not out[2]["isError"]:
            self.assertIn("Saved as", self.text(out[2]), "a call that ran after the reload is saved")

    def test_a_failed_edit_reloads_only_when_it_ended_its_request(self):
        """A request the host closed since (a sweep, a rotation) is not one this call ended: nothing to reload."""
        self.open()
        loads = len(self.bpy.loads)
        failed = {"isError": True, "content": [{"type": "text", "text": "Error: no object named Box"}]}
        closed = SimpleNamespace(direct=SimpleNamespace(closed="the request ended", closed_by_call=False))
        self.assertIsNone(self.host.after_call("angelo_create_cube", closed, failed))
        self.assertEqual(len(self.bpy.loads), loads)
        ended = SimpleNamespace(direct=SimpleNamespace(closed="it failed", closed_by_call=True))
        self.assertIn("reloaded", self.host.after_call("angelo_create_cube", ended, failed))
        self.assertGreater(len(self.bpy.loads), loads)


if __name__ == "__main__":
    unittest.main()
