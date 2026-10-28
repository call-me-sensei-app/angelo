# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat hosted in its worker Blender (design A′, contract C6), worker side: approval digests and the consent
lease (``worker_consent``), with real ``Controller`` objects and no Blender, provider or channel.

Threats pinned here, each with its own test:

* a stale digest replayed (the call's binding, arguments or document moved after its card was drawn);
* an approval carrying another call's digest, or naming a call that is not pending;
* a duplicated Approve; an Approve after Stop or after close;
* an epoch rollback or a conflicting lease under one epoch;
* a revocation racing an autonomous admission that has not run yet;
* a script queued under a revoked or replaced script consent;
* nothing runs unasked before the first lease, in Ask, or for a script naming Angelo's add-on.

The lead side (sending, acknowledgement, Stop timing) is ``test_native_worker_consent_lead``.
"""

import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import worker_consent  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402

CUBE = {"name": "Lease Cube", "size": 1, "location": [0, 0, 0]}
SCRIPT = {"description": "Set a value", "code": "result = 1"}
CHECKPOINT = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"


class Hosted(worker_consent.LeasedConsent, Controller):
    """The hosted chat's Controller as ``worker_chat_host`` composes it."""


class Part:
    """The worker's own document, as its local executor sees it."""

    def __init__(self):
        self.binding, self.ran = "revision-1", []

    def precondition(self, name, arguments):
        return self.binding

    def execute(self, name, arguments):
        self.ran.append(name)
        return {"success": True, "result": 1, "scene_changed": True,
                "changes": {"added": [], "removed": [], "modified": []}}


class HostedFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.part = Part()

    def chat(self, mode="ASK"):
        folder = Path(self.directory.name) / uuid.uuid4().hex
        folder.mkdir()
        chat = Hosted(str(folder))
        self.addCleanup(chat.close)
        chat.busy = True
        chat.state["options"]["approval"] = mode
        chat.set_document(("worker-part", 7))
        chat.request_checkpoint = CHECKPOINT
        return chat

    def request(self, chat, name="angelo_create_cube", arguments=None, identity=None):
        results = []
        identity = identity or uuid.uuid4().hex[:8]
        chat.request_tool(identity, name, dict(arguments or CUBE), results.append)
        return chat.pending[chat.state["id"] + "/" + identity], results

    def pump(self, chat, apply_mutation=None):
        chat.pump_tools(self.part, chat.document, apply_mutation=apply_mutation or (
            lambda call: chat.execute_tool(call, self.part, chat.document)), budget=ToolDispatchBudget(seconds=60))

    def card(self, chat, name="angelo_create_cube", arguments=None, identity=None):
        """A call waiting for the person, its card drawn (Ask, or Auto without a lease)."""
        call, results = self.request(chat, name, arguments, identity)
        self.pump(chat)
        self.assertTrue(worker_consent.awaiting_person(call), call)
        return call, results

    def run_now(self, chat):
        return lambda call: chat.execute_tool(call, self.part, chat.document)


class DigestTests(HostedFixture):
    def test_the_digest_is_stable_and_moves_with_every_part_of_the_binding(self):
        chat = self.chat()
        call, _ = self.card(chat)
        first = worker_consent.approval_digest(call)
        self.assertEqual(first, worker_consent.approval_digest(call))
        self.assertEqual(worker_consent.pending_digests(chat), {call.id: first})
        for change in (lambda: setattr(call, "precondition", "revision-2"),
                       lambda: call.arguments.__setitem__("size", 2),   # An in-place edit after the card.
                       lambda: setattr(call, "document", ("worker-part", 8)),
                       lambda: setattr(call, "recovery_review", True)):
            before = worker_consent.approval_digest(call)
            change()
            self.assertNotEqual(worker_consent.approval_digest(call), before)

    def test_two_identical_calls_have_different_digests(self):
        chat = self.chat()
        first, _ = self.card(chat)
        second, _ = self.card(chat)
        self.assertNotEqual(worker_consent.approval_digest(first), worker_consent.approval_digest(second))

    def test_a_binding_that_cannot_be_encoded_never_has_a_digest(self):
        chat = self.chat()
        call, _ = self.card(chat)
        call.precondition = object()
        self.assertIsNone(worker_consent.approval_digest(call))
        self.assertEqual(worker_consent.approve(chat, call.id, None, self.fail_apply)["error_code"], "STALE_REVISION")

    def fail_apply(self, call):
        self.fail("Nothing may run")


class ApprovalTests(HostedFixture):
    def test_the_drawn_digest_approves_and_runs_the_call_once(self):
        chat = self.chat()
        call, results = self.card(chat)
        answer = worker_consent.approve(chat, call.id, worker_consent.approval_digest(call), self.run_now(chat))
        self.assertEqual(answer, {"approved": True})
        self.assertEqual(self.part.ran, ["angelo_create_cube"])
        self.assertTrue(results[0]["success"])

    def test_a_stale_digest_replayed_runs_nothing_and_ends_the_call(self):
        chat = self.chat()
        call, results = self.card(chat)
        drawn = worker_consent.approval_digest(call)
        call.precondition = "revision-2"   # Rebound after the card was drawn.
        answer = worker_consent.approve(chat, call.id, drawn, self.run_now(chat))
        self.assertFalse(answer["approved"])
        self.assertEqual(answer["error_code"], "STALE_REVISION")
        self.assertEqual(results[0]["error"], worker_consent.STALE_APPROVAL)
        self.assertFalse(results[0]["scene_changed"])
        self.assertEqual(self.part.ran, [])
        self.assertNotIn(call.id, chat.pending)

    def test_an_earlier_calls_digest_never_approves_a_later_call(self):
        chat = self.chat()
        earlier, _ = self.card(chat)
        replay = worker_consent.approval_digest(earlier)
        chat.decline_tool(earlier.id)
        later, _ = self.card(chat)   # Same tool and arguments, a new id.
        self.assertFalse(worker_consent.approve(chat, later.id, replay, self.run_now(chat))["approved"])
        self.assertEqual(self.part.ran, [])

    def test_another_calls_digest_refuses_the_named_call_and_leaves_the_other_pending(self):
        chat = self.chat()
        first, _ = self.card(chat)
        second, second_results = self.card(chat)
        answer = worker_consent.approve(chat, second.id, worker_consent.approval_digest(first), self.run_now(chat))
        self.assertEqual(answer["error_code"], "STALE_REVISION")
        self.assertEqual(second_results[0]["error_code"], "STALE_REVISION")
        self.assertTrue(worker_consent.awaiting_person(first))
        self.assertEqual(self.part.ran, [])

    def test_a_call_id_that_is_not_pending_runs_nothing(self):
        chat = self.chat()
        call, _ = self.card(chat)
        for identity in (chat.state["id"] + "/missing", None, 7):
            answer = worker_consent.approve(chat, identity, worker_consent.approval_digest(call), self.run_now(chat))
            self.assertEqual(answer["error"], worker_consent.NOT_AWAITING)
        self.assertTrue(worker_consent.awaiting_person(call))

    def test_a_duplicated_approve_applies_once(self):
        chat = self.chat()
        call, _ = self.card(chat)
        digest, applied = worker_consent.approval_digest(call), []
        self.assertTrue(worker_consent.approve(chat, call.id, digest, applied.append)["approved"])
        answer = worker_consent.approve(chat, call.id, digest, applied.append)   # Applied, not finished yet.
        self.assertEqual(answer["error"], worker_consent.ALREADY)
        self.assertEqual(applied, [call])

    def test_an_approve_after_the_call_ran_finds_nothing(self):
        chat = self.chat()
        call, _ = self.card(chat)
        digest = worker_consent.approval_digest(call)
        worker_consent.approve(chat, call.id, digest, self.run_now(chat))
        self.assertFalse(worker_consent.approve(chat, call.id, digest, self.run_now(chat))["approved"])
        self.assertEqual(self.part.ran, ["angelo_create_cube"])

    def test_an_approve_after_stop_runs_nothing(self):
        chat = self.chat()
        call, results = self.card(chat)
        digest = worker_consent.approval_digest(call)
        chat.stop()
        self.assertFalse(worker_consent.approve(chat, call.id, digest, self.run_now(chat))["approved"])
        self.assertEqual(self.part.ran, [])
        self.assertEqual(results[0]["error_code"], "CANCELLED")

    def test_an_approve_of_a_call_revoked_with_its_turn_runs_nothing(self):
        chat = self.chat()
        call, _ = self.card(chat)
        call.revoked = "The agent turn ended"
        answer = worker_consent.approve(chat, call.id, worker_consent.approval_digest(call), self.run_now(chat))
        self.assertEqual(answer["error"], worker_consent.TURN_ENDED)
        self.assertEqual(self.part.ran, [])

    def test_an_approve_after_close_runs_nothing(self):
        chat = self.chat()
        call, _ = self.card(chat)
        digest = worker_consent.approval_digest(call)
        chat.close()
        self.assertFalse(worker_consent.approve(chat, call.id, digest, self.run_now(chat))["approved"])
        self.assertEqual(self.part.ran, [])

    def test_an_approved_script_is_its_exact_code_review(self):
        chat = self.chat()
        call, results = self.card(chat, "angelo_execute_python", SCRIPT)
        self.assertFalse(chat.script_authorized(call))
        worker_consent.approve(chat, call.id, worker_consent.approval_digest(call), self.run_now(chat))
        self.assertEqual(self.part.ran, ["angelo_execute_python"])
        self.assertEqual(results[0]["consent"], "exact_code_review")


class LeaseTests(HostedFixture):
    def lease(self, chat, epoch, **grants):
        return worker_consent.apply_consent(chat, epoch, dict({"auto": True}, **grants))

    def test_nothing_runs_unasked_before_the_first_lease(self):
        chat = self.chat("AUTO_BOUNDED")
        call, results = self.card(chat)
        self.assertEqual((results, self.part.ran), ([], []))
        script, _ = self.card(chat, "angelo_execute_python", SCRIPT)
        self.assertIsNone(script.script_consent)

    def test_an_auto_lease_runs_a_bounded_edit_unasked(self):
        chat = self.chat("AUTO_BOUNDED")
        self.assertEqual(self.lease(chat, 5), {"epoch": 5})
        call, results = self.request(chat)
        self.pump(chat)
        self.assertTrue(results[0]["success"])
        self.assertEqual(self.part.ran, ["angelo_create_cube"])

    def test_an_auto_lease_runs_nothing_in_ask(self):
        chat = self.chat("ASK")
        self.lease(chat, 5, scripts="n1")
        self.card(chat)
        self.card(chat, "angelo_execute_python", SCRIPT)
        self.assertEqual(self.part.ran, [])

    def test_an_older_epoch_is_refused_and_the_lease_stays(self):
        chat = self.chat("AUTO_BOUNDED")
        self.lease(chat, 9, scripts="n1")
        answer = worker_consent.apply_consent(chat, 8, {"auto": True, "scripts": "n2", "always_allow": "n3"})
        self.assertEqual((answer["error_code"], answer["epoch"]), ("STALE_REVISION", 9))
        self.assertEqual(chat.consent_lease.grants(), {"auto": True, "scripts": "n1"})

    def test_a_repeat_of_the_held_epoch_is_acknowledged_and_a_conflict_revokes_everything(self):
        chat = self.chat("AUTO_BOUNDED")
        self.lease(chat, 9, scripts="n1")
        self.assertEqual(self.lease(chat, 9, scripts="n1"), {"epoch": 9})   # Its first answer was lost.
        self.assertEqual(self.lease(chat, 9, scripts="n2")["error_code"], "STALE_REVISION")
        self.assertEqual(chat.consent_lease.grants(), {"auto": False})
        self.card(chat)

    def test_malformed_leases_are_refused_and_grant_nothing(self):
        chat = self.chat("AUTO_BOUNDED")
        for epoch, grants in ((True, {"auto": True}), (-1, {"auto": True}), ("3", {"auto": True}), (3, ["auto"])):
            self.assertEqual(worker_consent.apply_consent(chat, epoch, grants)["error_code"], "VALIDATION")
        self.assertIsNone(chat.consent_lease.epoch)
        self.assertEqual(worker_consent.clean_grants({"auto": "true", "scripts": "", "always_allow": "x" * 129,
                                                      "everything": True}), {"auto": False})

    def test_a_revocation_revokes_an_autonomous_call_admitted_before_it_that_has_not_run(self):
        chat = self.chat("AUTO_BOUNDED")
        self.lease(chat, 1)
        call, results = self.request(chat)
        held = []
        self.pump(chat, apply_mutation=held.append)   # Admitted unasked; its edit has not run yet.
        self.assertTrue(call.automatic and held == [call])
        self.assertEqual(self.lease(chat, 2, auto=False), {"epoch": 2})
        self.assertEqual(call.revoked, worker_consent.CONSENT_REVOKED)
        chat.execute_tool(call, self.part, chat.document)
        self.assertEqual(self.part.ran, [])
        self.assertEqual(results[0]["error_code"], "CANCELLED")

    def test_a_revocation_never_touches_a_call_already_running_or_a_manual_one(self):
        chat = self.chat("AUTO_BOUNDED")
        self.lease(chat, 1)
        running, _ = self.request(chat)
        self.pump(chat, apply_mutation=lambda call: None)
        running.executing = True
        chat.state["options"]["approval"] = "ASK"
        manual, _ = self.card(chat)
        self.lease(chat, 2, auto=False)
        self.assertEqual((running.revoked, manual.revoked), ("", ""))
        running.executing = False

    def test_a_script_grant_runs_scripts_unasked_until_it_is_withdrawn(self):
        chat = self.chat("AUTO_BOUNDED")
        self.lease(chat, 1, scripts="n1")
        call, results = self.request(chat, "angelo_execute_python", SCRIPT)
        self.pump(chat)
        self.assertEqual(results[0]["consent"], "chat_session")
        queued, _ = self.request(chat, "angelo_execute_python", SCRIPT)   # Admitted under n1, not dispatched yet.
        self.lease(chat, 2)
        self.pump(chat)
        self.assertTrue(worker_consent.awaiting_person(queued))
        self.assertEqual(self.part.ran, ["angelo_execute_python"])

    def test_a_replaced_or_regranted_script_consent_never_covers_a_script_queued_under_the_old_one(self):
        chat = self.chat("AUTO_BOUNDED")
        self.lease(chat, 1, scripts="n1")
        replaced, _ = self.request(chat, "angelo_execute_python", SCRIPT)
        self.lease(chat, 2, scripts="n2")
        self.pump(chat)
        self.assertTrue(worker_consent.awaiting_person(replaced))
        chat.decline_tool(replaced.id)
        regranted, _ = self.request(chat, "angelo_execute_python", SCRIPT)
        self.lease(chat, 3)
        self.lease(chat, 4, scripts="n2")   # The same nonce again: still a new token.
        self.pump(chat)
        self.assertTrue(worker_consent.awaiting_person(regranted))
        self.assertEqual(self.part.ran, [])

    def test_always_allow_covers_scripts_as_its_own_consent(self):
        chat = self.chat("AUTO_BOUNDED")
        self.lease(chat, 1, always_allow="a1")
        _, results = self.request(chat, "angelo_execute_python", SCRIPT)
        self.pump(chat)
        self.assertEqual(results[0]["consent"], "always_allow")

    def test_a_script_naming_angelos_add_on_always_asks(self):
        chat = self.chat("AUTO_BOUNDED")
        self.lease(chat, 1, scripts="n1", always_allow="a1")
        call, _ = self.card(chat, "angelo_execute_python", dict(SCRIPT, code="import angelo_agent"))
        self.assertIsNone(chat.script_consent_kind(call))

    def test_no_consent_is_set_or_offered_in_the_worker(self):
        chat = self.chat("AUTO_BOUNDED")
        self.lease(chat, 1, scripts="n1")
        with self.assertRaises(RuntimeError):
            chat.set_trusted_scripts(False)
        call, _ = self.card(chat, "angelo_execute_python", dict(SCRIPT, code="import angelo_agent"))
        self.assertIsNone(chat.own_chat_consent())
        self.assertFalse(chat.standing_consent(call, {}))
        self.assertFalse(chat.offers_always_allow(call))


if __name__ == "__main__":
    unittest.main()
