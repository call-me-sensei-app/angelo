# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A window chat's call start against its turn's end (the start gap, ``tool_deadlines.started``).

The turn's end revokes admitted calls on the provider's thread (``revoke_turn_calls``, under the chat's lock), while
the call's last start check (``_tool_available``) runs on the main thread without it. A revocation landing between
that check and the start used to mark the call revoked while it then ran anyway: an edit applied after its turn
ended. The start is now atomic with the revocation: one that lands first refuses the call (nothing ran), one that
lands after finds it started (it runs and reports it). Deterministic: the turn's end runs on a real thread at the
exact step, joined before the main thread goes on. No Blender.
"""

import ast
import inspect
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import controller as controller_module, program_commit_controller, tool_deadlines  # noqa: E402
from angelo_agent.tool_deadlines import TURN_ENDED  # noqa: E402
from test_native_tool_deadlines import RevocationFixture  # noqa: E402


class WindowChatStartGap(RevocationFixture):
    def end_turn_on_its_thread(self):
        """The provider thread's last act (``finished``), run now on its own thread and joined."""
        thread = threading.Thread(target=self.context.emit, args=("finished", {"status": "completed"}))
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())

    def end_after_last_check(self, call):
        """The turn ends right after the call's last start check passed (after ``execution_started`` is journaled)."""
        journal, check, state = self.controller._journal_tool, self.controller._tool_available, {"armed": False}

        def journaled(target, phase, *args, **kwargs):
            state["armed"] = state["armed"] or target is call and phase == "execution_started"
            return journal(target, phase, *args, **kwargs)

        def checked(target, document):
            passed = check(target, document)
            if passed and state["armed"]:
                state["armed"] = False
                self.end_turn_on_its_thread()
            return passed
        self.controller._journal_tool, self.controller._tool_available = journaled, checked

    def test_a_turn_end_after_the_last_check_refuses_the_call_and_nothing_runs(self):
        replies, call = self.awaiting_approval()
        self.end_after_last_check(call)
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.executor.execute.assert_not_called()   # Before: it ran, though its turn had ended.
        self.assertEqual((replies[0]["error"], replies[0]["revoked"]), (TURN_ENDED, True))
        self.assertFalse(call.executing)
        self.assertNotIn(call.id, self.controller.pending)

    def test_a_turn_end_after_the_start_finds_it_running_and_its_result_is_reported(self):
        replies, call = self.awaiting_approval()
        emit, fired = self.controller.emit, []

        def emitting(kind, data=None):
            if not fired and kind == "activity" and (data or {}).get("status") == "running":
                fired.append(call.executing)
                self.end_turn_on_its_thread()
            return emit(kind, data)
        self.controller.emit = emitting
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertEqual(fired, [True])   # The turn ended once the call had started.
        self.executor.execute.assert_called_once()
        self.assertEqual(call.revoked, "")
        self.assertIs(replies[0].get("success"), True)

    def test_a_turn_end_before_the_last_check_still_stops_it(self):
        replies, call = self.awaiting_approval()
        self.end_turn_on_its_thread()
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.executor.execute.assert_not_called()
        self.assertEqual(replies[0]["error"], TURN_ENDED)


class StartedTests(unittest.TestCase):
    """``started`` against ``revoke_turn_calls`` in both orders, on real threads."""

    def setUp(self):
        self.call = SimpleNamespace(epoch=1, generation=2, revoked="", executing=False, committing=False,
                                    staging=False, mutation_started=False)
        self.host = SimpleNamespace(lock=threading.RLock(), pending={"c": self.call})
        self.fence = SimpleNamespace(epoch=1, generation=2)

    def revoke(self):
        thread = threading.Thread(target=tool_deadlines.revoke_turn_calls, args=(self.host, self.fence))
        thread.start()
        thread.join(5)

    def test_a_revocation_first_refuses_the_start_and_marks_nothing(self):
        self.revoke()
        self.assertFalse(tool_deadlines.started(self.host, self.call, executing=True))
        self.assertEqual((self.call.executing, self.call.revoked), (False, TURN_ENDED))

    def test_a_start_first_is_never_revoked_after(self):
        self.assertTrue(tool_deadlines.started(self.host, self.call, executing=True, mutation_started=True))
        self.revoke()
        self.assertEqual((self.call.executing, self.call.revoked), (True, ""))

    def test_a_commit_start_first_leaves_a_staged_call_unrevoked(self):
        self.call.staging = self.call.executing = True   # A staged native job: revocable until its live apply.
        self.assertTrue(tool_deadlines.started(self.host, self.call, committing=True, mutation_started=True))
        self.revoke()
        self.assertEqual(self.call.revoked, "")

    def test_a_start_held_out_by_the_lock_sees_the_revocation_it_waited_for(self):
        self.host.lock.acquire()   # The revocation holds the lock: the start waits, then sees it.
        results, waiting = [], threading.Event()

        def start():
            waiting.set()
            results.append(tool_deadlines.started(self.host, self.call, executing=True))
        thread = threading.Thread(target=start)
        thread.start()
        waiting.wait(5)
        self.call.revoked = TURN_ENDED
        self.host.lock.release()
        thread.join(5)
        self.assertEqual((results, self.call.executing), ([False], False))


class EveryStartSiteTests(unittest.TestCase):
    """Each place a revocable call starts goes through ``started``: no bare start mark (the native stage's
    ``executing``, revocable until its live apply by design, is not a start)."""

    SITES = ((controller_module.Controller, "_execute_tool"),
             (program_commit_controller.ProgramCommitController, "_commit_native_job"),
             (program_commit_controller.ProgramCommitController, "_begin_prepared_apply"))

    def test_no_bare_start_marks(self):
        for owner, name in self.SITES:
            with self.subTest(site=name):
                import textwrap
                tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(owner, name))))
                marks = [node for node in ast.walk(tree) if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Attribute) and target.attr in ("executing", "committing", "mutation_started")
                    and isinstance(node.value, ast.Constant) and node.value.value is True for target in node.targets)]
                self.assertEqual(marks, [])
                self.assertIn("started_call(", inspect.getsource(getattr(owner, name)))


if __name__ == "__main__":
    unittest.main()
