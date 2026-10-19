# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Can the model miss a person's edit? Reproductions on the change feed.

The real Controller, Claude provider (unbilled stdio fixture), context ledger,
turn context and change feed over the shared scene fakes; no Blender, no inference.
The property under test: every change a person makes is reported in some message
the model reads after it happened, or is still in the change feed for the next one.

Acknowledging a continuation's change feed (``feed_acks``) breaks that
property in four ways (D1-D4, the ``Defect*`` classes; each fails with it). The
change feed ships without that acknowledgement, so a continuation acknowledges
nothing and every reproduction here passes. The harness records the
continuation observations by wrapping ``turn_context.prepare``, the point where
``feed_acks`` would acknowledge them.
"""

from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import NativeInitializeFixture, SUPPORTED
from test_native_round_savings import CLAUDE, OfflineClaude
from test_native_turn_context import TurnContextFixture, Update, datablock
from angelo_agent import change_feed, codex_plan, model_routing, turn_context
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.request_fences import RequestFence

LOOKUP = {"reply": "Looking.", "operations": [{"tool": "angelo_scene_search", "arguments": {}}], "verify": True}
DONE = {"reply": "Done.", "operations": [], "verify": False}
REJECTED = {"reply": "Again.", "operations": [{"tool": "angelo_scene_search", "arguments": {"bogus": 1}}],
            "verify": True}


class Executor:
    """Read-only lookups over the fake window the observation builder reads."""

    def __init__(self, window):
        self.window, self.calls = window, []

    def supports_native_job(self, name):
        return False

    def precondition(self, name, arguments):
        return "unchanged"

    def execute(self, name, arguments):
        self.calls.append(name)
        return {"objects": [], "truncated": False, "success": True}


class Harness(TurnContextFixture):
    """A real Controller whose rounds run against the fixture scene; ``hooks[n]`` runs as prompt n arrives."""

    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("scratch", 1))
        self.c.choose_connection("claude_subscription", {"model": ""}, "", None, CLAUDE, "sonnet")
        self.c.state["options"]["approval"] = "AUTO_BOUNDED"
        self.chat = self.c.state["id"]
        self.executor = Executor(self.window)
        self.prompts, self.plans, self.hooks, self.processes = [], [], {}, []
        self.on_main, self.main_calls = {}, []   # Prompt n's person edits, run by pump() on the main thread.
        patches = [patch("angelo_agent.controller.ClaudeProvider", OfflineClaude),
                   patch("angelo_agent.child_process.popen", side_effect=self.process),
                   patch("angelo_agent.child_process.run",
                         return_value=SimpleNamespace(returncode=0, stdout=SUPPORTED))]
        for value in patches:
            value.start()
            self.addCleanup(value.stop)
        self._prepare = turn_context.prepare
        self._prepare(self.window, self.chat).commit()  # An earlier turn of this chat.
        self.built = []

        def record(*args, **kwargs):  # The controller's continuation observations; send() uses the original.
            turn = self._prepare(*args, **kwargs)
            self.built.append(turn.observation)
            return turn
        recorder = patch.object(turn_context, "prepare", side_effect=record)
        recorder.start()
        self.addCleanup(recorder.stop)

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()
        super().tearDown()

    def process(self, *args, **kwargs):
        fixture = NativeInitializeFixture(on_user=self.reply)
        self.processes.append(fixture)
        return fixture

    def reply(self, process, event):
        content = event["message"]["content"]
        self.prompts.append((self.processes.index(process), content if isinstance(content, str) else content[0]["text"]))
        edit = self.on_main.get(len(self.prompts))
        if edit is not None:   # Blender's main thread makes the person's edit while the model reads the prompt.
            done = threading.Event()
            self.main_calls.append((edit, done))
            done.wait(5)
        hook = self.hooks.get(len(self.prompts))
        if hook is not None and hook(process) is False:
            return  # The hook answers itself.
        plan = self.plans.pop(0) if self.plans else DONE
        process.stdout.emit({"type": "result", "subtype": "success", "is_error": False,
                             "session_id": "fixture-native-%d" % self.processes.index(process),
                             "structured_output": plan})

    def send(self, text):
        """What console.execute does: prepare, send, commit."""
        turn = self._prepare(self.window, self.chat, message=text)
        self.c.send(text, turn.observation, recovery_evidence=turn.recovery_evidence)
        turn.commit()

    def pump(self, until=None, seconds=8):
        apply = lambda call: self.c.execute_tool(call, self.executor, self.c.document)  # noqa: E731
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            while self.main_calls:
                edit, done = self.main_calls.pop(0)
                edit()
                done.set()
            self.c.pump_events()
            self.c.pump_tools(self.executor, self.c.document, apply_mutation=apply, budget=ToolDispatchBudget(seconds=60))
            if until is not None and until():
                return
            if until is None and not self.c.busy and not (self.c._worker and self.c._worker.is_alive()):
                self.c.pump_events()
                return
            time.sleep(.002)
        self.fail("The fixture request did not reach its state")

    def person_edits_material(self, name="Roof"):
        self.depsgraph(Update(datablock(9001, "MATERIAL", name)))

    def person_moves(self, obj, x=7.25):
        obj.location = [x, 0., 0.]
        self.depsgraph(Update(obj, transform=True))

    def next_report(self):
        return self._prepare(self.window, self.chat).observation["changes_since_last_turn"]

    def continuation_accepted(self):
        """The continuation's message was answered (the fixture's assistant event counts as acceptance)."""
        return len(self.prompts) >= 2 and self.c._accepted_ledger is not None


class DefectRepeatedEditDeltaTests(Harness):
    """D1: the ledger's continuation delta treats the change report as state (``events_as_state``).

    The continuation's report now starts at the previous boundary, so a report
    equal to the one the model read last describes NEW edits, yet the delta
    (``Controller.compact_observation``) leaves it out or sends ``unchanged``;
    feed_acks then acknowledged it once the round was accepted. Without it the
    feed keeps it for the next message.
    """

    def test_a_second_edit_of_the_same_material_during_the_round_is_never_reported(self):
        self.person_edits_material()                      # Before the message: the first message reports it.
        self.plans = [LOOKUP]
        self.on_main[1] = self.person_edits_material  # Again, while the model plans.
        self.send("Make the walls match.")
        self.pump()
        self.assertEqual(len(self.prompts), 2)
        self.assertIn("Roof", self.prompts[0][1])
        self.assertIn("Roof", str(self.built[0]), "The continuation observation listed the second edit ...")
        later = [text for _, text in self.prompts[1:]]
        self.assertTrue(any("Roof" in text for text in later) or "Roof" in str(self.next_report()),
                        "... but no message after it reported it and the feed no longer holds it: "
                        "prompt 2 observation part = %r" % later[0][later[0].index('"observation"'):][:120])


class DefectEscalationTests(Harness):
    """D2: an Auto escalation replays the request's first (stale) observation to a stronger model's own context.

    The weak model's accepted continuation acknowledged the person's edit at the
    failed request's end, so neither the replayed observation nor any later report
    tells the stronger model's context about it.
    """

    def test_the_escalated_context_never_learns_an_edit_the_weak_context_acknowledged(self):
        route = model_routing.Route(connection_id=self.c.state.get("connection_id"), model="sonnet", tier="fast",
                                    effort="low", cache_ttl=None, measured=True, pinned=False, reason="review",
                                    ladder=(model_routing.Rung("opus", "frontier", "high"),))
        mover = self.objects[20]   # Not a scene-order fill row: only a change lists it.
        self.plans = [LOOKUP, REJECTED, REJECTED, REJECTED]   # The host rejects three plans: PlanRejected, escalate.
        self.on_main[1] = lambda: self.person_moves(mover)
        with patch.object(Controller, "_auto_route", return_value=route):
            self.send("Tidy the props.")
            self.pump(seconds=15)
        contexts = {index for index, _ in self.prompts}
        self.assertEqual(len(contexts), 2, "The request escalated to the stronger model's own context")
        weak, strong = min(contexts), max(contexts)
        self.assertTrue(any("7.25" in text for index, text in self.prompts if index == weak),
                        "The weak context read the edit in its continuation")
        strong_text = [text for index, text in self.prompts if index == strong]
        self.assertTrue(any("7.25" in text for text in strong_text) or mover.name in str(self.next_report()),
                        "The stronger context got the stale first observation and the feed acknowledged the edit")


class DefectCodexLateObservationTests(Harness):
    """D3: a Codex correction's observation built after its waiter gave up is still acknowledged.

    ``codex_plan._observation`` returns None after ``DISPATCH_SECONDS`` (or on an
    error answer) and the correction is sent without an observation, but the
    late build registered its acknowledgement for the request's fence; the
    correction turn's acceptance then applies it.
    """

    def test_an_observation_the_correction_never_carried_acknowledges_nothing(self):
        mover = self.objects[6]
        self.c.busy, self.c.cancelled = True, False   # A Codex plan request in progress.
        context = self.c.runtime_context()
        fence = context.request_fence
        provider = SimpleNamespace(controller=context, _scope=SimpleNamespace(lock=threading.RLock(), fence=fence))
        self.person_moves(mover)                      # The plan's own edit, or the person's.
        carried = []
        with patch.object(codex_plan, "DISPATCH_SECONDS", 0.05):
            worker = threading.Thread(target=lambda: carried.append(codex_plan._observation(provider, context, fence)))
            worker.start()
            worker.join(5)
        self.assertEqual(carried, [None], "The correction is sent without an observation")
        self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60))  # The late build.
        self.assertIn(mover.name, str(self.built[-1]))
        context.record_provider_accepted()            # The correction turn's input was accepted.
        self.c.busy = False
        self.c._commit_ledger("completed")
        self.assertIn(mover.name, str(self.next_report()), "Nothing the model read reported this edit")


class DefectStaleAcknowledgementTests(Harness):
    """D4: an accepted acknowledgement whose request never finished survived into the next request.

    ``invalidate_runtime`` while busy ends a request without its ``finished``
    event; with ``feed_acks`` the next request's first continuation applied the
    old acknowledgement unfenced (the baseline moved back, and on a feed evicted
    and recreated in between its serial acknowledged new rows). Without it
    there is nothing to carry over.
    """

    def start_and_invalidate_after_acceptance(self):
        def accept_without_result(process):
            process.stdout.emit({"type": "assistant", "message": {"id": "m", "role": "assistant", "content": [],
                                                                  "usage": {"output_tokens": 3}}})
            return False
        self.plans = [LOOKUP]
        self.on_main[1] = lambda: self.person_moves(self.objects[3], 3.5)  # Reported by the accepted round.
        self.hooks[2] = accept_without_result
        self.send("Look around.")
        self.pump(until=self.continuation_accepted)
        self.c.invalidate_runtime()   # Reauthentication or connection removal while the round runs.
        self.pump()
        self.assertFalse(hasattr(self.c, "_feed_acks"), "The controller keeps no continuation acknowledgement")

    def test_a_new_request_starts_without_the_old_acknowledgement(self):
        self.start_and_invalidate_after_acceptance()
        self.window.scene.frame_current = 10
        self.plans = [LOOKUP]
        self.on_main[len(self.prompts) + 1] = lambda: setattr(self.window.scene, "frame_current", 1)
        self.send("Look again.")
        self.pump()
        report = self.built[-1]["changes_since_last_turn"]
        self.assertEqual(report.get("frame_changed"), {"before": 10, "after": 1},
                         "The frame the person changed back after the message is reported: %r" % report)

    def test_a_recreated_feed_keeps_rows_an_old_serial_would_cover(self):
        self.start_and_invalidate_after_acceptance()
        for index in range(change_feed.MAX_FEEDS):    # Other chats' turns evict this chat's feed.
            change_feed.acknowledge("other-%d" % index, 0, dict(change_feed._feeds[self.chat].baseline))
        self.assertNotIn(self.chat, change_feed._feeds)
        mover = self.objects[7]
        self.plans = [LOOKUP]
        self.on_main[len(self.prompts) + 1] = lambda: self.person_moves(mover)
        self.send("Look again.")
        self.pump()
        self.assertIn(mover.name, str(self.built[-1]["changes_since_last_turn"]),
                      "The edit made after the message is reported in the continuation")
