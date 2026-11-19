# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an app reads in the state is what Angelo will do: a message Angelo will never send by itself is not queued,
and the goal the app sent is visible before the agent holds it.

From the 2.0.1 acceptance run on acd0d465ee9: after a Stop the paused "Not sent" bubble kept ``queued: 1`` in every
later answer, Reconnect included, so an app polling for the queue to drain waited forever; and an app's goal was
invisible (no ``goal`` in the send's answer or the state) unless the agent held it. Real host, launcher and
controller (``AgentFixture``). Contract: doc/angelo/external_mcp.rst, "State" and "Goals".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import external_agent  # noqa: E402
from angelo_agent.external_clients import AppRegistry  # noqa: E402
from test_native_external_agent import LISTENER, AgentFixture, TurnProvider  # noqa: E402


@unittest.skipIf(sys.platform == "win32", LISTENER)
class NotSentTests(AgentFixture):
    def stopped_with_one_waiting(self):
        self.env.provider = TurnProvider(self.release, script=False)
        self.call("angelo_send", {"message": "Make a cube"})
        self.until(lambda: self.env.provider.texts)
        queued = self.call("angelo_send", {"message": "Then a sphere"})["structuredContent"]
        self.assertEqual((queued["delivered"], queued["queued"]), ("queued", 1), "waiting while the turn runs")
        self.assertNotIn("not_sent", queued)
        self.assertFalse(self.call("angelo_stop")["isError"])
        self.until(lambda: not self.conversation().turn_running())
        for _ in range(10):
            self.tick()

    def test_a_message_a_stop_left_unsent_is_never_reported_queued(self):
        """Failed on acd0d465ee9: ``queued`` stayed 1 in every later answer."""
        self.stopped_with_one_waiting()
        for tool in ("angelo_status", "angelo_read"):
            with self.subTest(tool=tool):
                state = self.call(tool)["structuredContent"]
                self.assertEqual((state["turn"], state["queued"]), ("idle", 0), state)
                self.assertEqual(state["not_sent"]["count"], 1, state)
                self.assertIn("the person stopped the turn", state["not_sent"]["why"])
                self.assertIn("Angelo never sends them by itself", state["not_sent"]["next"])
        self.assertEqual(len(self.env.provider.texts), 1, "and it is still not sent")

    def test_it_stays_truthful_after_a_reconnect(self):
        self.stopped_with_one_waiting()
        self.name = AppRegistry(self.state).connect("claude_code")   # The person's Reconnect: a new key.
        self.host.pump()
        state = self.call("angelo_status")["structuredContent"]
        self.assertEqual(state["queued"], 0, state)
        self.assertEqual(state["not_sent"]["count"], 1, state)

    def test_each_reason_says_why_in_words(self):
        for reason, words in (("stopped", "the person stopped the turn"), ("failed", "ended with a problem"),
                              ("not_started", "did not reach Angelo's agent"), ("relaunch", "Angelo closed"),
                              ("A refused send's notice", "were not sent")):
            with self.subTest(reason=reason):
                self.assertIn(words, external_agent.not_sent_why([reason]))


@unittest.skipIf(sys.platform == "win32", LISTENER)
class GoalTests(AgentFixture):
    GOAL = "Ten cubes on a circle of radius 4"

    def test_the_goal_an_app_sent_is_in_the_answer_and_the_state_until_the_agent_holds_one(self):
        """Failed on acd0d465ee9: no ``goal`` anywhere unless the agent held it."""
        self.env.provider = TurnProvider(self.release, script=False)
        sent = self.call("angelo_send", {"message": "Add ten cubes", "goal": self.GOAL})["structuredContent"]
        self.assertEqual(sent["goal"]["status"], "with_task", sent)
        self.assertEqual(sent["goal"]["objective"], self.GOAL)
        self.assertIn("holds it as a goal", sent["goal"]["note"])
        self.assertEqual(self.call("angelo_status")["structuredContent"]["goal"]["objective"], self.GOAL)
        held = {"status": "active", "objective": self.GOAL}
        with mock.patch("angelo_agent.goal_loop.goal_of", return_value=dict(held, id="g")), \
                mock.patch("angelo_agent.goal_controls.summary", return_value=held):
            self.assertEqual(self.call("angelo_status")["structuredContent"]["goal"], held, "the held goal wins")
        self.release.set()
        self.until(lambda: not self.conversation().turn_running())
        plain = self.call("angelo_send", {"message": "Now say hello"})["structuredContent"]
        self.assertNotIn("goal", plain, "a send without a goal leaves none behind")
        self.assertNotIn("goal", self.call("angelo_status")["structuredContent"])




@unittest.skipIf(sys.platform == "win32", LISTENER)
class NewChatRuntimeTests(AgentFixture):
    """The 2.0.1 acceptance rerun (3b2b492bb4a), 4 of 4: angelo_new_chat said runtime codex (the new conversation's
    placeholder header), then its first send ran on Claude."""

    def test_a_new_chat_names_no_runtime_before_its_first_message_decides_one(self):
        self.env.provider = TurnProvider(self.release, script=False)
        self.call("angelo_send", {"message": "Make a cube"})
        self.release.set()
        self.until(lambda: not self.conversation().turn_running())
        fresh = self.call("angelo_new_chat")["structuredContent"]
        self.assertEqual(fresh["connection"]["runtime"], "", fresh)
        self.assertFalse(fresh["connection"]["decided"])
        self.assertIn("first message", fresh["connection"]["note"])
        sent = self.call("angelo_send", {"message": "Then a sphere"})["structuredContent"]
        self.assertNotIn("decided", sent["connection"], "decided once its turn starts")
        self.assertTrue(sent["connection"]["runtime"], sent)


@unittest.skipIf(sys.platform == "win32", LISTENER)
class TaskIdentityTests(AgentFixture):
    """The 2.0.1 audit (P2-3, P2-4) on 904e6ff9724: two sends queued behind a running task both got the same task id
    (conversation:3, from the transcript's length while they waited), and a completed task's status counted later
    tasks' tokens (usage.input 65 beside result.cost.input 20)."""

    def queued_twice(self):
        self.env.provider = TurnProvider(self.release, script=False)
        started = self.call("angelo_send", {"message": "Create a cube"})["structuredContent"]
        self.until(lambda: self.env.provider.texts)
        first = self.call("angelo_send", {"message": "Then add a sphere"})["structuredContent"]
        second = self.call("angelo_send", {"message": "Then add a cone"})["structuredContent"]
        return started, first, second

    def test_each_queued_send_gets_its_own_task_and_follows_it_to_its_result(self):
        started, first, second = self.queued_twice()
        self.assertEqual((first["delivered"], second["delivered"]), ("queued", "queued"))
        tasks = [started["task"], first["task"], second["task"]]
        self.assertEqual(len(set(tasks)), 3, tasks)
        waiting = self.call("angelo_status", {"task": first["task"]})["structuredContent"]
        self.assertEqual(waiting["task"], first["task"], "a queued task is followed by its own id")
        self.assertNotIn("result", waiting, "no result before it was even sent")
        self.release.set()
        self.until(lambda: not self.conversation().turn_running() and not len(self.conversation().inbox))
        for task in tasks:
            with self.subTest(task=task):
                found = self.call("angelo_status", {"task": task})["structuredContent"]
                self.assertEqual(found["task"], task, "never another task's tracking")
                self.assertIn("result", found, "bound to its request's row once delivered")

    def test_a_completed_task_s_usage_ends_where_the_next_one_started_on_every_surface(self):
        self.env.provider = TurnProvider(self.release, script=False)
        self.release.set()
        self.call("angelo_send", {"message": "Create a cube"})
        self.until(lambda: not self.conversation().turn_running())
        chat = self.conversation().state["id"]
        starts = {chat + ":0": {"input": 10}, chat + ":4": {"input": 30}}
        with mock.patch.dict(self.host._tasks, {self.name: starts}), \
                mock.patch("angelo_agent.external_status.usage", return_value={"input": 75}):
            found = self.host._task_status(self.conversation(), self.name, chat + ":0")
        self.assertEqual(found["usage"]["input"], 20, "up to the next task's start, never later tasks' tokens")
        self.assertEqual(found["result"]["cost"]["input"], 20, "the same figure as the result's cost")

class LongPollEndTests(unittest.TestCase):
    """The 2.0.1 acceptance rerun's check 7 (3b2b492bb4a): on Disconnect, a parked status read was answered at once,
    but with the last good state (turn running), because the long poll kept it over the host's "unavailable"."""

    def poll(self, answers, sent=None):
        from angelo_agent.external_mcp_agent import AgentTools

        class Launcher(AgentTools):
            def __init__(self):
                self.answers = list(answers)

            def agent_request(self, args, wait=0.0):
                return self.answers.pop(0)

            def _cancelled_request(self, identity):
                return False

            def _progress(self, params, count, message):
                pass
        return Launcher()._gather(1, {}, sent, "c0", 30, "brief", {"action": "status"})

    RUNNING = {"code": "ok", "result": {"turn": "running", "cursor": "c1", "rows": [{"i": 3, "role": "agent"}]}}

    def test_access_ending_mid_poll_is_said_never_the_last_state(self):
        ended = {"code": "unavailable", "message": "This app was disconnected in Angelo"}
        result = self.poll([self.RUNNING, ended])
        self.assertTrue(result["isError"], result)
        self.assertEqual(result["structuredContent"]["code"], "unavailable")
        self.assertIn("disconnected", result["structuredContent"]["message"])
        self.assertNotIn("turn", result["structuredContent"], "never the running state it no longer has")
        self.assertEqual(result["structuredContent"]["rows"], [{"i": 3, "role": "agent"}], "what it read is kept")

    def test_a_sent_message_stays_sent_when_the_poll_ends_in_an_error(self):
        ended = {"code": "incompatible", "message": "Angelo 2.0.0 (PID 1) has access"}
        value = self.poll([ended], sent={"delivered": "started", "task": "t1"})["structuredContent"]
        self.assertEqual((value["code"], value["delivered"], value["task"]), ("incompatible", "started", "t1"),
                         "the app knows its message went and never sends it again")

    def test_a_busy_read_still_answers_the_state_it_had(self):
        """Round-2 review S-c: a read refused for the parked cap is not an end; the state read so far is answered."""
        busy = {"code": "busy", "message": "Too many reads are waiting"}
        self.assertEqual(self.poll([self.RUNNING, busy])["structuredContent"]["turn"], "running")
        self.assertFalse(self.poll([busy], sent={"delivered": "queued", "task": "t2"})["isError"])


class WaitingSendFieldsTests(LongPollEndTests):
    """A send with wait_seconds (Claude Code's) keeps the send's own facts and takes the state from the read alone.

    The 2.0.1 final smoke (904e6ff9724): the answer kept only delivered, task and moved, so live_page never reached
    the app. The review of ffe232b8253 (SHOULD-2): keeping the rest of the send's answer kept its live state too, so an
    approval the person answered during the wait (a hidden session's relayed card included) was still offered. The
    review of b3726b1c11f (NIT-1): the link is state too, so a page that ended for any reason is never offered."""

    FACTS = {"delivered": "started", "task": "t1", "cursor": "c0", "moved": True}
    STATE = {"turn": "waiting_for_approval", "approval": {"tool": "angelo_execute_python", "script": "x = 1"},
             "needs": "The person in Angelo", "session": {"hidden": True}, "queued": 1, "live_page": "http://x/s/a/",
             "not_sent": {"count": 1}, "goal": {"status": "with_task"}, "a_future_field": 7}
    SENT = dict(FACTS, **STATE)
    SETTLED = {"code": "ok",
               "result": {"turn": "idle", "queued": 0, "cursor": "c2", "rows": [{"i": 4, "role": "agent"}]}}

    def test_the_send_s_own_facts_are_kept_and_none_of_its_state(self):
        """Failed on ffe232b8253: approval, needs, session, not_sent, goal and a_future_field of the send survived."""
        value = self.poll([self.SETTLED], sent=dict(self.SENT))["structuredContent"]
        for key in set(self.FACTS) - {"cursor"}:
            with self.subTest(kept=key):
                self.assertEqual(value[key], self.FACTS[key])
        for key in set(self.STATE) - set(self.SETTLED["result"]):
            with self.subTest(dropped=key):
                self.assertNotIn(key, value, "the send's live state, which the read no longer says")
        self.assertEqual((value["turn"], value["queued"], value["cursor"]), ("idle", 0, "c2"), "the read's, now")

    def test_the_link_is_the_read_s_never_the_send_s(self):
        """Failed on b3726b1c11f: a link no read offered any more (Stop sharing, a move, an error) was kept."""
        ended = {"code": "ok", "result": dict(self.SETTLED["result"], live_page_ended={"why": "ended"})}
        for read in (self.SETTLED, ended):
            with self.subTest(ended="live_page_ended" in read["result"]):
                self.assertNotIn("live_page", self.poll([read], sent=dict(self.SENT))["structuredContent"])
        live = {"code": "ok", "result": dict(self.SETTLED["result"], live_page="http://x/s/b/")}
        self.assertEqual(self.poll([live], sent=dict(self.SENT))["structuredContent"]["live_page"], "http://x/s/b/")

    def test_no_later_answer_brings_back_a_link_a_read_said_ended(self):
        """Failed on b3726b1c11f: after a read that said the page ended, the error the poll ended in (access off)
        offered the send's link again."""
        ended = {"code": "ok", "result": dict(self.SETTLED["result"], turn="running", live_page_ended={"why": "x"})}
        for last in ({"code": "unavailable", "message": "This app was disconnected in Angelo"},
                     {"code": "busy", "message": "Too many reads are waiting"}):
            with self.subTest(last=last["code"]):
                self.assertNotIn("live_page", self.poll([ended, last], sent=dict(self.SENT))["structuredContent"])

    def test_a_poll_that_ends_in_an_error_keeps_the_send_s_facts_but_not_its_state(self):
        ended = {"code": "unavailable", "message": "This app was disconnected in Angelo"}
        value = self.poll([ended], sent=dict(self.SENT))["structuredContent"]
        for key in self.FACTS:
            with self.subTest(kept=key):
                self.assertEqual(value[key], self.FACTS[key])
        for key in self.STATE:
            with self.subTest(dropped=key):
                self.assertNotIn(key, value, "never the state the poll no longer knows")
        self.assertEqual(value["code"], "unavailable")

    def test_a_busy_read_before_any_read_answers_the_send_as_a_send_without_a_wait_does(self):
        """No read yet: the send's own state is the latest there is."""
        busy = {"code": "busy", "message": "Too many reads are waiting"}
        value = self.poll([busy], sent=dict(self.SENT))["structuredContent"]
        self.assertEqual({key: value[key] for key in self.SENT}, self.SENT)


@unittest.skipIf(sys.platform == "win32", LISTENER)
class WaitingSendApprovalTests(AgentFixture):
    """The review of ffe232b8253 (SHOULD-2) through the real host and launcher: a send made while a card waits, and
    the person answers the card while the send waits."""

    def test_an_approval_answered_during_the_wait_is_not_in_the_send_s_answer(self):
        """Failed on ffe232b8253: the final answer still had the answered card's approval and needs."""
        from angelo_agent import external_mcp_agent
        self.env.provider = TurnProvider(self.release)   # Asks for a script: a card only the person answers.
        sent = self.call("angelo_send", {"message": "Make a cube"})["structuredContent"]
        waiting = self.call("angelo_read", {"cursor": sent["cursor"], "wait_seconds": 20})["structuredContent"]
        self.assertEqual(waiting["turn"], "waiting_for_approval")
        answers = []
        original = external_mcp_agent._agent_result

        def recorded(envelope, rows=None, sent=None):
            answers.append(sent)
            return original(envelope, rows, sent)
        def once_sent(controller, call):   # The person answers the card once the send below waits in the tray.
            if len(controller.inbox.items()):
                self.person_approves(controller, call)
        self.approve = once_sent
        self.release.set()
        with mock.patch.object(external_mcp_agent, "_agent_result", recorded):
            waited = self.call("angelo_send", {"message": "Then make it red", "wait_seconds": 20})
        self.assertFalse(waited["isError"], waited)
        self.assertIn("approval", answers[-1], "the send's own answer had the card waiting")
        value = waited["structuredContent"]
        self.assertNotIn(value["turn"], ("waiting_for_approval",), value)
        self.assertNotIn("approval", value, "an answered card is never offered to the app")
        self.assertNotIn("needs", value)
        self.assertEqual(value["task"], answers[-1]["task"], "the send's own task is kept")
        self.until(lambda: not self.conversation().turn_running())


    def test_the_host_s_parked_send_answers_the_state_now(self):
        """The review of b3726b1c11f (SHOULD-1): the local protocol's own wait on a send parks the send's answer in
        the host, which kept its state under the read's. Failed on b3726b1c11f: approval and needs came back."""
        import threading
        from angelo_agent import cli_client
        self.env.provider = TurnProvider(self.release)
        sent = self.call("angelo_send", {"message": "Make a cube"})["structuredContent"]
        self.call("angelo_read", {"cursor": sent["cursor"], "wait_seconds": 20})
        parked, original = [], self.host._park

        def park(*args):
            parked.append(args[-1])
            return original(*args)

        def once_parked(controller, call):   # The person answers the card once the send below is parked.
            if parked:
                self.person_approves(controller, call)
        self.approve = once_parked
        self.release.set()
        key, answer = AppRegistry(self.state).key(self.name), {}
        request = {"command": "agent", "args": {"action": "send", "message": "Then make it red", "wait": 20}}
        with mock.patch.object(self.host, "_park", park):
            thread = threading.Thread(target=lambda: answer.update(cli_client.run(request, self.state, self.name, 30,
                                                                                  key=key)), daemon=True)
            thread.start()
            self.until(lambda: not thread.is_alive())
        self.assertEqual(answer.get("code"), "ok", answer)
        self.assertIn("approval", parked[0], "the send's own answer had the card waiting")
        value = answer["result"]
        self.assertNotIn("approval", value, "an answered card is never offered to the app")
        self.assertNotIn("needs", value)
        self.assertEqual((value["task"], value["delivered"]), (parked[0]["task"], parked[0]["delivered"]))
        self.until(lambda: not self.conversation().turn_running())


if __name__ == "__main__":
    unittest.main()
