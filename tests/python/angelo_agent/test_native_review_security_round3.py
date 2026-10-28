# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The A′ security review, round 3 (``release-1.0.6.noindex/review-security/REPORT-r3.md`` findings 1-5), and the
round-3 concurrency review's N3. Each class failed while its finding stood. No child process starts: the fake
children have negative pids, ``RuntimeChild`` runs with ``group=False`` and their ``kill`` sends no signal.
"""

import ast
import base64
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (codex_bases, runtime_bindings, runtime_filter, worker_chat_wiring,  # noqa: E402
                          worker_consent_lead, worker_mirror, worker_mirror_ops, worker_mirror_pins)
from angelo_agent.worker_mirror import MirrorController  # noqa: E402
from test_native_runtime_bindings import Case, chat  # noqa: E402
from test_native_worker_mirror import Fixture  # noqa: E402

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
OWN, OTHER = str(uuid.uuid4()), str(uuid.uuid4())
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4


class Pinned(Fixture):
    """A mirror made from the file Angelo saved: its route is ``codex_api`` on connection ``OWN``."""

    def setUp(self):
        super().setUp()
        self.worker.state.update(provider="codex_api", connection_id=OWN)
        self.worker.save()
        self.mirror = MirrorController(self.directory, self.session, self.ops)
        self.router = worker_mirror.EventRouter()
        self.router.open(self.mirror)
        self.killed = []
        worker_consent_lead.bind(kill=self.killed.append)
        self.lane()
        self.keys = SimpleNamespace(_memory_keys={OWN: "key-of-own-connection", OTHER: "key-of-another-connection",
                                                  "codex_api": "key-of-the-mode"})

    def forge(self, **fields):
        """The worker, the file's single writer, records a ``set`` of ``fields`` (an option as ``approval``)."""
        approval = fields.pop("approval", None)
        if approval is not None:
            self.worker.state["options"] = dict(self.worker.state["options"], approval=approval)
        self.worker.state.update(fields)
        self.worker.save()
        self.seq += 1
        self.frame("journal", {"seq": self.seq, "path": str(self.worker.store.path)})
        self.mirror.pump_events()

    def launch(self):
        hosted = SimpleNamespace(private=None)
        with mock.patch.object(worker_chat_wiring.lifecycle, "hosted", lambda session: hosted), \
                mock.patch.object(worker_chat_wiring, "_mirror", lambda session: self.mirror), \
                mock.patch.dict(sys.modules, {"angelo_agent.ui": self.keys}):
            import angelo_agent
            with mock.patch.object(angelo_agent, "ui", self.keys, create=True):
                return worker_chat_wiring.launch_chat(hosted, self.session)


class JournalNeverDecides(Pinned):
    """Finding 1: the worker-written file chose the connection, key and consent of its chat's model runtime."""

    def test_the_review_poc_a_worker_written_record_never_moves_the_chat_to_another_key(self):
        honest = self.launch()
        self.assertEqual((honest.mode, honest.connection_id, honest.api_key),
                         ("codex_api", OWN, "key-of-own-connection"))
        self.forge(provider="codex_subscription", connection_id=OTHER)
        self.assertIsNone(self.launch())   # Refused, never another connection's key.
        self.assertEqual((self.mirror.state["provider"], self.mirror.state["connection_id"]), ("codex_api", OWN))

    def test_each_forged_field_changes_no_lead_decision(self):
        forgeries = {"provider": {"provider": "codex_subscription"}, "connection_id": {"connection_id": OTHER},
                     "no connection": {"connection_id": None}, "id": {"id": str(uuid.uuid4())},
                     "approval": {"approval": "READ_ONLY"}}
        for name, fields in forgeries.items():
            with self.subTest(name), mock.patch.object(worker_mirror_pins, "print", create=True):
                self.setUp()
                self.assertEqual(worker_consent_lead.grants(self.mirror)["auto"], True)
                self.forge(**fields)
                if name == "id":   # The file's reader already refuses another chat's id: never adopted either.
                    self.assertEqual(self.mirror.state["id"], self.session)
                    continue
                self.assertEqual(worker_mirror_pins.tampered(self.mirror), name.replace("no connection",
                                                                                         "connection_id"))
                self.assertEqual(self.mirror.state["id"], self.session)   # Shown as the lead pinned it.
                self.assertEqual(self.mirror.state["connection_id"], OWN)
                self.assertEqual(self.mirror.state["options"]["approval"], "AUTO_BOUNDED")
                self.assertEqual(worker_consent_lead.grants(self.mirror), {"auto": False})
                self.assertIsNone(self.launch())
                self.assertEqual(self.killed, [self.session])   # Its model children end at once.
                with self.assertRaises(RuntimeError):   # A choice on the forged route is still another connection.
                    self.mirror.choose_connection(fields.get("provider", "codex_api"), {}, "",
                                                  fields.get("connection_id", OWN))
                notices = [row for row in self.mirror.state["messages"] if row.get("role") == "error"]
                self.assertEqual(len(notices), 1)
                self.assertIn("did not choose", notices[0]["text"])
                self.forge(approval="AUTO_BOUNDED")   # Nothing it writes later lifts it, or says so twice.
                self.assertEqual(worker_consent_lead.grants(self.mirror), {"auto": False})
                self.assertEqual(len([row for row in self.mirror.state["messages"] if row.get("role") == "error"]), 1)

    def test_an_approval_the_lead_asked_for_is_never_a_mismatch(self):
        self.mirror.set_options(approval="ASK")   # Sent; its answer is applied by the UI timer.
        self.assertFalse(worker_consent_lead.grants(self.mirror)["auto"])   # Narrower at once.
        self.forge(approval="ASK")   # The worker applied it before its answer was read.
        worker_mirror_ops.reconcile()
        self.assertIsNone(worker_mirror_pins.tampered(self.mirror))
        self.mirror.set_options(approval="AUTO_BOUNDED")
        self.assertFalse(worker_consent_lead.grants(self.mirror)["auto"])   # Wider only once the worker confirmed.
        self.forge(approval="AUTO_BOUNDED")
        worker_mirror_ops.reconcile()
        self.assertEqual(worker_consent_lead.grants(self.mirror)["auto"], True)
        self.assertIsNone(worker_mirror_pins.tampered(self.mirror))
        self.assertEqual(self.killed, [])

    def test_a_refused_change_keeps_the_confirmed_approval(self):
        self.ops.answer = lambda op: {"error": "no"} if op == "chat.options" else {"ok": True}
        self.mirror.set_options(approval="ASK")
        worker_mirror_ops.reconcile()
        self.assertEqual(self.mirror.state["options"]["approval"], "AUTO_BOUNDED")
        self.forge(approval="ASK")   # The worker holds what it refused: not the lead's.
        self.assertEqual(worker_mirror_pins.tampered(self.mirror), "approval")

    def test_an_answer_naming_an_approval_never_asked_for_marks_the_chat(self):
        self.ops.answer = lambda op: {"ok": True, "options": {"approval": "READ_ONLY"}}
        with mock.patch.object(worker_mirror_pins, "print", create=True):
            self.mirror.set_options(model="m2")
            worker_mirror_ops.reconcile()
        self.assertEqual(worker_mirror_pins.tampered(self.mirror), "approval")

    def test_the_worker_turning_fast_off_is_shown_and_decides_nothing(self):
        self.worker.state["options"] = dict(self.worker.state["options"], fast=True, model="m9")
        self.forge()
        self.assertIsNone(worker_mirror_pins.tampered(self.mirror))
        self.assertEqual(self.mirror.state["options"]["model"], "m9")   # The model the worker runs: display only.
        self.assertEqual(self.launch().connection_id, OWN)

    def test_the_open_carries_the_lead_approval(self):
        self.mirror.set_options(approval="ASK")
        self.assertEqual(worker_mirror_pins.options(self.mirror)["approval"], "ASK")
        self.mirror.state["options"]["approval"] = "AUTO_BOUNDED"   # Whatever the state holds meanwhile.
        self.assertEqual(worker_mirror_pins.options(self.mirror)["approval"], "ASK")


class WarmUpBases(unittest.TestCase):
    """Finding 2: any thread became a connection-wide warm-up base when one of its turns was the warm-up."""

    def setUp(self):
        self.based, self.bases = [], set()
        self.filter = runtime_filter.Filter("codex", "/ws", based=self.based.append, bases=lambda: self.bases)
        self.next = iter(range(1, 100))

    def opened(self, method="thread/start", thread="t1"):
        identity = next(self.next)
        params = {"threadId": thread} if method != "thread/start" else {}
        self.assertTrue(self.filter.inbound(json.dumps({"id": identity, "method": method, "params": params})).passed)
        self.filter.outbound(json.dumps({"id": identity, "result": {"thread": {"id": thread}}}))

    def turn(self, text, thread="t1"):
        return self.filter.inbound(json.dumps({"id": next(self.next), "method": "turn/start", "params": {
            "threadId": thread, "input": [{"type": "text", "text": text}]}})).passed

    def test_the_warm_up_as_the_first_turn_of_a_started_thread_is_a_base(self):
        self.opened()
        self.assertTrue(self.turn(codex_bases.WARM_UP))
        self.assertEqual(self.based, ["t1"])
        self.assertFalse(self.turn("a later turn carrying context"))   # Only ever forked after it.

    def test_the_review_poc_a_warm_up_after_another_turn_is_no_base(self):
        self.opened()
        self.assertTrue(self.turn("injected context"))
        self.assertTrue(self.turn(codex_bases.WARM_UP))
        self.assertEqual(self.based, [])

    def test_a_resumed_or_forked_thread_is_no_base(self):
        for method in ("thread/resume", "thread/fork"):
            self.opened(method, thread=method)
            self.assertTrue(self.turn(codex_bases.WARM_UP, thread=method))
        self.assertEqual(self.based, [])

    def test_no_turn_runs_on_a_recorded_base(self):
        self.bases.add("base")
        self.opened("thread/resume", thread="base")
        self.assertFalse(self.turn("injected context", thread="base"))


class LocalImages(unittest.TestCase):
    """Finding 3: the child opened the path the worker named after the check (a symbolic link swapped in)."""

    def setUp(self):
        self.root = Path(os.path.realpath(tempfile.mkdtemp())) / "references"
        self.root.mkdir()
        self.image = self.root / (str(uuid.uuid4()) + ".png")
        self.image.write_bytes(PNG)
        self.filter = runtime_filter.Filter("codex", "/ws", image_roots=lambda: (str(self.root),))

    def turn(self, *paths):
        items = [{"type": "text", "text": "Match this"}] + [{"type": "localImage", "path": str(p)} for p in paths]
        return self.filter.inbound(json.dumps({"id": 1, "method": "turn/start", "params": {
            "threadId": "t", "input": items}}))

    def test_an_image_reaches_the_child_as_codex_frames_a_local_image(self):
        verdict = self.turn(self.image, self.image)
        self.assertTrue(verdict.passed)
        sent = json.loads(verdict.line)["params"]["input"]
        url = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
        self.assertEqual(sent, [{"type": "text", "text": "Match this"}] + [
            item for number in (1, 2) for item in (
                {"type": "text", "text": '<image name=[Image #%d] path="%s">' % (number, self.image)},
                {"type": "image", "url": url}, {"type": "text", "text": "</image>"})])
        self.assertNotIn("localImage", verdict.line)

    def test_a_link_swapped_in_after_the_check_is_never_opened(self):
        outside = Path(tempfile.mkdtemp()) / "secret.png"
        outside.write_bytes(PNG)
        self.image.unlink()
        os.symlink(outside, self.image)
        with mock.patch.object(runtime_bindings, "_resolve", lambda path: path):   # Resolved before the swap.
            self.assertIsNone(runtime_bindings.read_image(str(self.image), (str(self.root),)))
            folder = self.root / "folder"
            os.symlink(outside.parent, folder)   # A folder of the path swapped for a link.
            self.assertIsNone(runtime_bindings.read_image(str(folder / "secret.png"), (str(self.root),)))

    def test_only_a_bounded_regular_image_passes(self):
        fifo, text, large = self.root / "pipe.png", self.root / "notes.png", self.root / "large.png"
        os.mkfifo(fifo)   # Opened without blocking, and refused.
        text.write_bytes(b"not an image")
        large.write_bytes(PNG[:8] + b"0" * runtime_bindings.IMAGE_BYTES)
        for path in (fifo, text, large, self.root):
            with self.subTest(path.name):
                self.assertFalse(self.turn(path).passed)

    def test_the_overhead_on_a_reference_image(self):
        """Latency-first: a reference image of the size a person attaches (2 MiB, 4 per turn at most)."""
        import time
        self.image.write_bytes(PNG[:8] + os.urandom(2 * 1024 * 1024))
        started = time.perf_counter()
        verdict = self.turn(self.image)
        seconds = time.perf_counter() - started
        print("\nAngelo test: a 2 MiB reference image read and sent inline in %.1f ms; the line is %.2f MiB"
              % (seconds * 1000, len(verdict.line) / 1048576))
        self.assertTrue(verdict.passed)
        self.assertLess(seconds, 1.0)


class WithdrawnTeamKey(unittest.TestCase):
    """Finding 4: a Team key the organization withdrew left a turn in flight streaming on it."""

    def test_withdrawing_a_team_key_ends_its_children_now(self):
        source = (ADDON / "team_account_ui.py").read_text()
        function = next(ast.get_source_segment(source, node) for node in ast.walk(ast.parse(source))
                        if isinstance(node, ast.FunctionDef) and node.name == "_withdraw_key")
        ended = []
        ui = SimpleNamespace(_memory_keys={"team-conn": "grant"}, _controllers={})
        namespace = {"_keyed": {"team-conn"}, "end_connection": ended.append, "threading": None}
        exec(function, namespace)
        namespace["_withdraw_key"](ui, "team-conn")
        self.assertEqual((ended, ui._memory_keys), (["team-conn"], {}))

    def test_a_replaced_grant_key_ends_its_children_too(self):
        source = (ADDON / "team_account_ui.py").read_text()
        self.assertIn("if ui._memory_keys.get(identity) not in (None, key):\n                end_connection(identity)",
                      source)


class ThreadStartNamesNoThread(unittest.TestCase):
    """Finding 5: ``thread/start`` passed a ``threadId`` through."""

    def test_a_thread_start_with_a_thread_id_is_refused(self):
        flt = runtime_filter.Filter("codex", "/ws")
        self.assertFalse(flt.inbound(json.dumps({"id": 1, "method": "thread/start",
                                                 "params": {"threadId": "someone-elses"}})).passed)
        self.assertTrue(flt.inbound(json.dumps({"id": 2, "method": "thread/start", "params": {"cwd": "/ws"}})).passed)


class AKeyGapIsUnknown(Case):
    """Concurrency N3: keys briefly absent (a reload) made the next request end a live turn's child."""

    def test_a_missing_key_never_ends_a_child_and_another_key_does(self):
        A = str(uuid.uuid4())
        self.hosted[A] = chat(mode="codex_api")
        child = self.launch(A)
        self.hosted[A] = chat(mode="codex_api", key="")   # The window's keys are reloading.
        self.service.handle({"op": "runtime.kill", "session": A, "pid": 1})   # Any request claims first.
        self.assertTrue(child.alive())
        self.assertTrue(self.send(child, {"id": 1, "method": "turn/interrupt",
                                          "params": {"threadId": "t", "turnId": "u"}}).passed)
        self.assertTrue(self.send(child, {"id": 2, "method": "thread/start", "params": {}}).passed)
        self.hosted[A] = chat(mode="codex_api", key="key-of-connection-a-02")   # Present, and another key.
        self.service.handle({"op": "runtime.kill", "session": A, "pid": 1})
        self.assertFalse(child.alive())


if __name__ == "__main__":
    unittest.main()
