# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Why the lead started no model runtime for a worker's chat, as its person reads it (design A′, R7).

The lead's runtime service refused every such launch with "This worker does not host that chat", also for a chat its
worker does host whose pinned connection is no longer usable (removed, signed out or refused by policy), the one case
a person can act on. Now:

* the lead names the case by a ``worker_reasons`` support code (``worker_chat_wiring.launch_refusal``):
  ``WRK-NO-CONNECTION`` for a hosted chat on an unusable connection, ``WRK-NOT-OPEN`` otherwise;
* the code crosses the worker channel with the refusal (``worker_channel``), and the worker says the reason from its
  own translatable template (``runtime_relay``): the chat's error row keeps that recipe (``i18n``) and shows the code;
* an unknown code changes nothing: the lead's words, as before, with no recipe.

No Blender.
"""

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import (runtime_relay, runtime_service, ui_text, worker_channel,  # noqa: E402
                          worker_chat_wiring as wiring, worker_reasons)
from angelo_agent.protocol import RpcError  # noqa: E402

SESSION = "6f9d1e0c-8f4b-4b1e-9a55-3c1f0f2b6d11"


def service(refused=None):
    return runtime_service.RuntimeService(chats=lambda session: None, worker_pid=-31, refused=refused)


class LeadSaysWhyTests(unittest.TestCase):
    def test_a_chat_on_an_unusable_connection_is_refused_as_such(self):
        with self.assertRaises(runtime_service.Refused) as caught:
            service(lambda session: "WRK-NO-CONNECTION").handle({"op": "runtime.launch", "session": SESSION})
        self.assertEqual(caught.exception.support, "WRK-NO-CONNECTION")
        self.assertIn("connection can't be used now", str(caught.exception))
        self.assertNotIn("does not host", str(caught.exception))

    def test_any_other_chat_is_not_open_in_its_worker(self):
        for refused in (None, lambda session: "WRK-SOMETHING-NEW", lambda session: None):
            with self.subTest(refused=refused), self.assertRaises(runtime_service.Refused) as caught:
                service(refused).handle({"op": "runtime.probe", "session": SESSION})
            self.assertEqual(caught.exception.support, "WRK-NOT-OPEN")

    def test_the_wiring_tells_the_two_cases_apart(self):
        worker, mirror = object(), SimpleNamespace(state={"id": SESSION})
        cases = ((worker, ("OPENAI", "c1"), False, "WRK-NO-CONNECTION"), (worker, ("OPENAI", "c1"), True, "WRK-NOT-OPEN"),
                 (worker, None, False, "WRK-NOT-OPEN"), (object(), ("OPENAI", "c1"), False, "WRK-NOT-OPEN"))
        for hosted_on, pinned, usable, expected in cases:
            with self.subTest(expected=expected, pinned=pinned, usable=usable), \
                    mock.patch.object(wiring.lifecycle, "hosted", lambda session, w=hosted_on: w), \
                    mock.patch.object(wiring, "_mirror", lambda session: mirror), \
                    mock.patch("angelo_agent.worker_mirror_pins.route", lambda chat, p=pinned: p), \
                    mock.patch("angelo_agent.worker_consent_lead._connected", lambda chat, u=usable: u):
                self.assertEqual(wiring.launch_refusal(worker, SESSION), expected)


class CodeCrossesTheChannelTests(unittest.TestCase):
    def test_a_refusals_support_code_is_part_of_its_answer(self):
        sent = []
        client = object.__new__(worker_channel.Client)
        client.endpoint = SimpleNamespace(send=lambda frame, handle=None: sent.append(frame))
        client._reverse_slots = threading.Semaphore(1)
        client._reverse_slots.acquire()
        client.on_reverse = service(lambda session: "WRK-NO-CONNECTION").handle
        client._serve_reverse(7, {"op": "runtime.launch", "session": SESSION})
        error = sent[0]["response"]["error"]
        self.assertEqual((error["type"], error["support"]), ("Refused", "WRK-NO-CONNECTION"))
        client._reverse_slots.acquire()   # Released once it answered.
        client.on_reverse = lambda request: (_ for _ in ()).throw(RuntimeError("anything else"))
        client._serve_reverse(8, {"op": "runtime.launch"})
        self.assertNotIn("support", sent[1]["response"]["error"])


class WorkerShowsItTranslatedTests(unittest.TestCase):
    def ask(self, error):
        caller = SimpleNamespace(request=lambda request, timeout: ({"error": error}, []))
        with self.assertRaises(RpcError) as caught:
            runtime_relay.Launcher(caller, lambda: [SESSION]).ask({"op": "runtime.launch"}, 5)
        return caught.exception

    def test_a_known_code_is_said_from_its_own_template(self):
        failure = self.ask({"type": "Refused", "message": "anything the lead said", "support": "WRK-NO-CONNECTION"})
        self.assertIn("support code WRK-NO-CONNECTION", str(failure))
        self.assertNotIn("anything the lead said", str(failure))
        template, fields = failure.i18n
        self.assertEqual(template, runtime_relay.NOT_STARTED)
        self.assertEqual(fields["code"], "WRK-NO-CONNECTION")
        self.assertEqual(ui_text.display_notice({"text": str(failure), "i18n": failure.i18n}), str(failure))

    def test_an_unknown_code_keeps_the_leads_words(self):
        for error in ({"message": "Busy; retry"}, {"message": "Busy; retry", "support": "WRK-NEW"}):
            with self.subTest(error=error):
                failure = self.ask(error)
                self.assertEqual(str(failure), "Busy; retry")
                self.assertIsNone(failure.i18n)

    def test_every_support_code_has_one_reason(self):
        self.assertEqual(len(worker_reasons.BY_SUPPORT), len(worker_reasons.SUPPORT))
        self.assertEqual(worker_reasons.SUPPORT[worker_reasons.NO_CONNECTION], "WRK-NO-CONNECTION")


def setUpModule():   # Journal reads run inline here, as each pump asks; the reader thread has its own tests.
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.bind(inline=True)


def tearDownModule():
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.reset()


if __name__ == "__main__":
    unittest.main()
