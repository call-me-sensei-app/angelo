# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A call Angelo already has is only ever asked about again: never reported "not admitted", never sent twice.

Fresh break-it review FR-H1 (2026-10-23): an approved script running longer than one launcher round plus the main
thread's pickup time came back ``busy ... not admitted`` while it kept running, and the app's retry ran it a second
time; a change to the app's levels, the window's approval mode or the team policy mid-call (even only its admin
contact, FR-H1b / M3) restarted the child and sent the call again. Release review S2: the authority a call runs under
is read inside the session's lock. FR-M3: this computer's (MDM) policy is read again at every call, so an admin's
change reaches a running session from its next call. Contract: doc/angelo/external_mcp.rst, "Headless".

The launcher, its session and a headless child in one process (test_native_headless_mcp's harness), with short rounds
and a main thread an executor keeps busy, as a long script does.
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import external_cli_protocol as protocol, external_mcp, managed_policy  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402
from angelo_agent.external_clients import EDIT, READ, AppRegistry, write_access  # noqa: E402
from angelo_agent.headless_session import (APPLY_AFTER_RUNNING, IN_FLIGHT_ENDED, IN_FLIGHT_STOPPED,  # noqa: E402
                                           POLICY_RESTARTED, only_more_access)
import test_native_headless_mcp as headless_harness  # noqa: E402  (a module: its tests are not collected again here)
from test_native_external_cli_host import Executor  # noqa: E402

CUBE = headless_harness.CUBE
ROUND = 0.3        # One launcher round (ROUND_WAIT) and the child's main-thread pickup time, shortened.
LONG = 4 * ROUND   # A call that holds the main thread across several rounds.


class SlowExecutor(Executor):
    """Holds the child's main thread for ``LONG`` seconds per call, as a long script does; ``during`` runs first."""

    def __init__(self, state):
        super().__init__(state)
        self.during = None

    def execute(self, name, arguments):
        if self.during is not None:
            self.during()
        time.sleep(LONG)
        return super().execute(name, arguments)


@unittest.skipIf(sys.platform == "win32", "Headless sessions are macOS and Linux for now")
class InFlightTests(unittest.TestCase):
    tick, call, open = headless_harness.HeadlessTests.tick, headless_harness.HeadlessTests.call, headless_harness.HeadlessTests.open
    text = headless_harness.HeadlessTests.text

    def setUp(self):
        headless_harness.HeadlessTests.setUp(self)
        self.executor = SlowExecutor(str(self.root / "child-state"))
        self.starts = 0
        rounds = patch.object(external_mcp, "ROUND_WAIT", ROUND)
        rounds.start()
        self.addCleanup(rounds.stop)

    def start_child(self, options, read_fd, write_fd, process):
        headless_harness.HeadlessTests.start_child(self, options, read_fd, write_fd, process)
        self.host.service.main_thread_timeout = ROUND
        self.starts += 1

    def test_a_call_longer_than_a_round_is_still_running_never_not_admitted_and_runs_once(self):
        self.open()
        self.executor.calls.clear()
        result = self.call("angelo_create_cube", CUBE)
        self.assertFalse(result["isError"], result)
        self.assertNotIn("not admitted", self.text(result))
        self.assertEqual(self.executor.calls.count("create_cube") + self.executor.calls.count("angelo_create_cube"), 1,
                         self.executor.calls)
        self.assertEqual(self.starts, 1)

    def test_a_change_of_authority_mid_call_never_restarts_the_child_or_sends_the_call_again(self):
        self.open()
        self.executor.calls.clear()
        self.executor.during = lambda: AppRegistry(self.state).set_levels(self.name, (READ,))
        result = self.call("angelo_create_cube", CUBE)
        self.executor.during = None
        self.assertFalse(result["isError"], result)
        self.assertEqual(len(self.executor.calls), 1, self.executor.calls)
        self.assertEqual(self.starts, 1, "never restarted mid-call")
        result = self.call("angelo_set_transform", {"object": "Box", "location": [1, 0, 0]})
        self.assertTrue(result["isError"], "the revoked level holds from the next call")
        self.assertEqual(self.starts, 2)
        self.assertEqual(len(self.executor.calls), 1)

    def test_access_turned_off_mid_call_ends_the_session_and_says_angelo_had_the_call(self):
        """"Turning this off closes the connection": the child ends with it, and the app learns the call was running,
        never a bare refusal that reads as "nothing ran"; nothing is sent again."""
        self.open()
        self.executor.calls.clear()
        self.executor.during = lambda: write_access(self.state, False)
        result = self.call("angelo_create_cube", CUBE)
        self.executor.during = None
        self.assertTrue(result["isError"])
        self.assertIn("Angelo already had this call", self.text(result))
        self.assertIn("Allow apps on this computer", self.text(result), "the reason is kept")
        self.assertEqual(len(self.executor.calls), 1)
        self.assertFalse(self.server.headless.opened)

    def test_a_restart_for_a_policy_or_level_change_says_so(self):
        self.open()
        AppRegistry(self.state).set_levels(self.name, (READ, EDIT))
        self.server.headless.levels = ("read",)   # The levels the child last started with differ from the record.
        result = self.call("angelo_inspect_scene")
        self.assertEqual(self.starts, 2)
        self.assertIn(POLICY_RESTARTED.split(" on ")[0], self.text(result))

    def test_only_an_effective_policy_change_restarts_the_child(self):
        organization = {"requireAskForEdits": False, "adminContact": "it@example.com"}
        self.server.organization = lambda: dict(organization)
        self.open()
        organization["adminContact"] = "help@example.com"
        self.call("angelo_inspect_scene")
        self.assertEqual(self.starts, 1, "an admin contact change is not a change in what the app may do")
        organization["requireAskForEdits"] = True
        self.call("angelo_inspect_scene")
        self.assertEqual(self.starts, 2)

    def test_a_child_that_ended_mid_call_answers_unknown_and_nothing_runs_again(self):
        self.open()
        session = self.server.headless
        session.child.close()
        envelope, note = session.request({"command": "call", "args": {}}, 1,
                                         authority=lambda: (session.levels, session.approval, session.organization,
                                                            session.machine),
                                         in_flight=True)
        self.assertEqual(envelope["code"], "unavailable")
        self.assertEqual(envelope["message"], IN_FLIGHT_ENDED)
        self.assertEqual(self.starts, 1, "never started again to resend the call")

    def ask_again(self, call_id):
        """The launcher's next round of ``call_id``, a call this session's child admitted."""
        session = self.server.headless
        authority = (session.levels, session.approval, session.organization, session.machine)
        envelope, _ = session.request({"command": "call", "args": {"id": call_id}}, 1, in_flight=True,
                                      authority=lambda: authority)
        return envelope

    def test_a_round_goes_only_to_the_child_that_admitted_the_call(self):
        """Re-check FR2-L1: another call restarted the child between this call's rounds; the new child never saw
        it, so the round answers unknown instead of admitting it a second time."""
        self.open()
        session = self.server.headless
        session._admitted["mcp-a"] = session.generation   # A long call the first child admitted.
        session.child.close()
        self.call("angelo_inspect_scene")                  # Another call starts the second child.
        self.assertEqual(self.starts, 2)
        self.executor.calls.clear()
        envelope = self.ask_again("mcp-a")
        self.assertEqual((envelope["code"], envelope["message"]), ("unavailable", IN_FLIGHT_ENDED))
        self.assertEqual(self.executor.calls, [], "never sent to the new child")

    def test_more_access_waits_for_a_running_call_and_less_access_stops_it_and_says_so(self):
        """Re-check FR2-L2: only more access never stops a running call (the next call is told to retry); a
        stricter change applies at once, and the running call's next round says it was stopped."""
        self.open()
        session = self.server.headless
        AppRegistry(self.state).set_levels(self.name, (READ, EDIT))
        self.call("angelo_inspect_scene")
        self.assertEqual(self.starts, 2)
        self.assertEqual(session._admitted, {}, "an answered call is not running")
        session._admitted["mcp-a"] = session.generation    # A long call the child is running.
        AppRegistry(self.state).set_levels(self.name, (READ, EDIT, "tasks"))
        result = self.call("angelo_inspect_scene")
        self.assertIn(APPLY_AFTER_RUNNING.split(".")[0], self.text(result))
        self.assertEqual(self.starts, 2, "nothing running was stopped")
        session.forget("mcp-a")                            # It finished: the next call applies the change.
        self.call("angelo_inspect_scene")
        self.assertEqual(self.starts, 3)
        session._admitted["mcp-b"] = session.generation
        AppRegistry(self.state).set_levels(self.name, (READ,))
        self.call("angelo_inspect_scene")
        self.assertEqual(self.starts, 4, "less access applies at once")
        envelope = self.ask_again("mcp-b")
        self.assertEqual((envelope["code"], envelope["message"]), ("unavailable", IN_FLIGHT_STOPPED))

    def test_a_call_counts_as_running_from_the_moment_it_is_sent(self):
        """Re-check 2 FR3-L1: during a long call's first round (before any "pending"), added access still waits."""
        AppRegistry(self.state).set_levels(self.name, (READ, EDIT))
        self.open()
        session, seen = self.server.headless, {}

        def during():   # In the child, while the call's first round is still waiting.
            seen["running"] = session.generation in session._admitted.values()
            AppRegistry(self.state).set_levels(self.name, (READ, EDIT, "python"))
            seen["other"], _ = self.server.headless_request({"command": "call", "args": {
                "tool": "angelo_inspect_scene", "arguments": {}, "id": "mcp-other", "presentation": "mcp",
                "wait": 0.1}}, 0.1, track=True)
        self.executor.during = during
        result = self.call("angelo_create_cube", CUBE)
        self.executor.during = None
        self.assertTrue(seen["running"], "marked as running when sent")
        self.assertEqual(seen["other"]["code"], "busy")
        self.assertFalse(result["isError"], result)
        self.assertEqual(self.starts, 1, "the added level never stopped it")
        self.assertEqual(session._admitted, {}, "answered: no longer running")

    def test_only_a_change_that_just_adds_access_counts_as_more_access(self):
        """Re-check 2 FR3-L2: per field, which direction is more access; anything mixed counts as stricter."""
        base = (("read", "edit"), "ASK", {"allowAutoApproval": False, "requireAskForEdits": True,
                                          "adminContact": "a@x"}, Policy(allow_auto_approval=False))

        def changed(**fields):
            levels, approval, organization, machine = base
            return (fields.get("levels", levels), fields.get("approval", approval),
                    fields.get("organization", organization), fields.get("machine", machine))
        more = [changed(levels=("read", "edit", "python")), changed(approval="AUTO_BOUNDED"), changed(approval=None),
                changed(organization=None), changed(organization={"allowAutoApproval": True,
                                                                   "requireAskForEdits": True, "adminContact": "a@x"}),
                changed(organization={"allowAutoApproval": False, "requireAskForEdits": False}),
                changed(organization={"allowAutoApproval": False, "requireAskForEdits": True, "adminContact": "b@y"}),
                changed(machine=Policy())]
        less = [changed(levels=("read",)), changed(approval="READ_ONLY"),
                changed(levels=("read", "edit", "python"), approval="READ_ONLY"),
                changed(organization={"allowAutoApproval": False, "requireAskForEdits": True,
                                      "allowLocalCliAccess": False}),
                changed(machine=Policy(allow_auto_approval=False, require_ask_for_edits=True)),
                changed(machine=Policy(allow_auto_approval=False, console_url="https://other"))]
        for new in more:
            self.assertTrue(only_more_access(base, new), new)
        for new in less:
            self.assertFalse(only_more_access(base, new), new)
        self.assertFalse(only_more_access(changed(organization=None), base), "an organization where there was none")

    def test_the_authority_is_read_inside_the_sessions_lock(self):
        self.open()
        session, held = self.server.headless, []

        def authority():
            held.append(session._lock.locked())
            return protocol.envelope("refused", "off")
        envelope, _ = session.request({"command": "call", "args": {}}, 1, authority=authority)
        self.assertEqual(held, [True])
        self.assertEqual(envelope["code"], "refused")
        self.assertFalse(session.opened, "a refusal ends the session")

    def test_a_machine_policy_change_reaches_a_running_session_from_its_next_call(self):
        policy = [Policy()]

        def machine():   # What managed_policy.reload does in the launcher: the cached policy follows the sources.
            managed_policy.reset(policy[0])
            return policy[0]
        self.server.machine = machine
        self.open()
        policy[0] = Policy(admin_contact="it@example.com")
        self.call("angelo_inspect_scene")
        self.assertEqual(self.starts, 1, "an admin contact is not a change in what the app may do")
        policy[0] = Policy(require_ask_for_edits=True)
        edit = self.call("angelo_create_cube", CUBE)
        self.assertIn("needs_gui", self.text(edit))
        self.assertEqual(self.starts, 1, "refused by the launcher (break-it F3) before the child saw it")
        self.call("angelo_inspect_scene")
        self.assertEqual(self.starts, 2, "the next call restarts the child under the new policy")
        self.assertEqual(self.child_options.get("--approval"), "ASK")
        policy[0] = Policy(allow_local_cli_access=False)
        result = self.call("angelo_inspect_scene")
        self.assertIn("organization turned off access", self.text(result))
        self.assertFalse(self.server.headless.opened, "access off ends the session")

    def test_an_unreadable_machine_policy_refuses(self):
        self.open()

        def broken():
            raise OSError("unreadable")
        self.server.machine = broken
        result = self.call("angelo_inspect_scene")
        self.assertTrue(result["isError"])
        self.assertIn("could not read this computer's policy", self.text(result))

    def test_the_launcher_reads_the_machine_policy_again_at_every_call(self):
        import inspect
        self.assertIn("machine=managed_policy.reload", inspect.getsource(external_mcp.main))

    def test_the_window_path_keeps_waiting_on_a_busy_round_after_admission(self):
        """The window answers through the same loop: busy after pending is a call still running there too."""
        replies = [{"code": "pending"}, {"code": "busy", "message": "not admitted"},
                   {"code": "ok", "result": {"content": [{"type": "text", "text": "done"}], "isError": False}}]
        with patch.object(self.server, "request", lambda request, wait: replies.pop(0)):
            result = self.server.call(1, {"name": "angelo_inspect_scene", "arguments": {}})
        self.assertFalse(result["isError"], result)
        self.assertEqual(replies, [])
        with patch.object(self.server, "request", lambda request, wait: {"code": "busy", "message": "queue full"}):
            result = self.server.call(1, {"name": "angelo_inspect_scene", "arguments": {}})
        self.assertTrue(result["isError"], "busy before admission still says so")


if __name__ == "__main__":
    unittest.main()
