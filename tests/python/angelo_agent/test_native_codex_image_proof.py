# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Codex pictures proven delivered by content: ``turn/modelInputImages`` (image_proof, picture_ledger).

On a runtime that reports the pictures each model request carried, a picture
Angelo handed over (a tool result's data URL, an attachment's file) is
delivered when a report names the SHA-256 of its bytes, or of its source bytes
when the runtime re-encoded it, and not delivered when no report named it by
the end of the request. Any other runtime keeps the unconfirmed record.

The unit cases feed ``PathFacts`` directly. The provider cases run a real
Controller and Codex provider over the scripted app-server, which emits the
report the way the runtime would, for each kind of picture. The recorded case
replays two real view cells of a recorded Codex run (fixtures/
codex_recorded_view_cells.json). No model, process or Blender.
"""

import base64
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import agent_runtime, image_proof, job_manager, request_path  # noqa: E402
from angelo_agent.job_manager import JobManager  # noqa: E402
from angelo_agent.job_store import JobStore  # noqa: E402
from angelo_agent.request_path import PathFacts, clean_path  # noqa: E402
from test_native_codex_plan import PlanTurnFixture  # noqa: E402
from test_native_long_jobs import FakeRunner  # noqa: E402
from test_native_request_path import PNG, VIEW_ARGUMENTS, view, view_result  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "codex_recorded_view_cells.json"
PIN = dict(agent_runtime.PIN, notifications=[image_proof.METHOD])  # A pinned build that emits the report.
OTHER_PNG = PNG + b"other"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def report(*images, thread="native-thread", turn=None, response="resp-1"):
    """The runtime's report, as the fork emits it (camelCase, app-server v2)."""
    params = {"threadId": thread, "responseId": response,
              "images": [dict({"callId": "call-%d" % index, "sha256": digest}, **({"sourceSha256": source}
                                                                                 if source else {}))
                         for index, (digest, source) in enumerate(images)]}
    return dict(params, **({"turnId": turn} if turn else {}))


class LedgerTests(unittest.TestCase):
    """``PathFacts`` alone: what a report proves, and what its absence proves."""

    def returned(self, path, identity="v", data=PNG, **extra):
        picture = view(identity, **extra)
        path.tool("angelo_view", VIEW_ARGUMENTS, view_result(picture))
        path.expect([picture], [sha(data)])
        return picture

    def proving(self):
        path = PathFacts()
        path.prove(True)
        path.accepted()
        return path

    def test_a_hash_match_confirms(self):
        path = self.proving()
        self.returned(path)
        self.assertEqual(path.snapshot().get("images_delivered"), None, "Expected, not reported yet")
        path.confirm([(sha(PNG), None)], "resp-1")
        receipt = path.snapshot(settled=True)
        self.assertEqual((receipt["images_delivered"], receipt["image_delivery"]), (1, "confirmed"))
        self.assertEqual(receipt["images"], [[1, 0, {"view:SOLID:AXIS": 1}]])
        self.assertNotIn("images_unconfirmed", receipt)
        self.assertNotIn("images_not_delivered", receipt)

    def test_a_resized_picture_matches_through_its_source_hash(self):
        path = self.proving()
        self.returned(path)
        path.confirm([(sha(b"re-encoded by the runtime"), sha(PNG))], "resp-1")
        self.assertEqual(path.snapshot(settled=True)["images_delivered"], 1)

    def test_no_report_leaves_the_picture_not_delivered_once_the_request_ends(self):
        path = self.proving()
        self.returned(path)
        path.confirm([(sha(OTHER_PNG), None)], "resp-1")  # Another picture's report proves nothing of this one.
        running = path.snapshot()
        self.assertNotIn("images_not_delivered", running, "Still expected while the request runs")
        receipt = path.snapshot(settled=True)
        self.assertEqual((receipt["images_not_delivered"], receipt["image_delivery"]), (1, "none"))
        self.assertEqual(receipt["not_delivered_images"], [[1, 0, {"view:SOLID:AXIS": 1}]])
        self.assertNotIn("images_delivered", receipt)
        self.assertEqual(clean_path(receipt), receipt)

    def test_a_picture_is_never_confirmed_twice(self):
        path = self.proving()
        self.returned(path, "a")
        self.returned(path, "b")  # The same bytes again: a second picture.
        path.confirm([(sha(PNG), None)], "resp-1")
        path.confirm([(sha(PNG), None)], "resp-1")  # The same model request reported again.
        receipt = path.snapshot(settled=True)
        self.assertEqual((receipt["images_delivered"], receipt["images_not_delivered"]), (1, 1))
        path = self.proving()
        self.returned(path, "a")
        path.confirm([(sha(PNG), None)], "resp-1")
        path.confirm([(sha(PNG), None)], "resp-2")  # A spent report cannot deliver a picture already delivered.
        self.assertEqual(path.snapshot(settled=True)["images_delivered"], 1)

    def test_a_report_that_arrives_before_the_picture_is_recorded_still_delivers_it(self):
        path = self.proving()
        path.confirm([(sha(PNG), None)], "resp-1")  # The runtime forwarded it before the write returned here.
        self.returned(path)
        self.assertEqual(path.snapshot(settled=True)["images_delivered"], 1)

    def test_a_job_picture_proven_by_content_names_its_job(self):
        path = self.proving()
        picture = view("j", job="j7")
        path.tool("angelo_view", VIEW_ARGUMENTS, view_result(picture))
        path.expect([picture], [sha(PNG)])
        jobs = path.confirm([(sha(PNG), None)], "resp-1")
        self.assertEqual(list(jobs), ["j7"])

    def test_only_the_current_contract_reloads_a_negative(self):
        receipt = {"edits": 0, "images_returned": 1, "images_not_delivered": 1, "image_delivery": "none",
                   "not_delivered_images": [[1, 0, {"view:SOLID:SCENE_CAMERA": 1}]]}
        self.assertEqual(clean_path(receipt), receipt)
        old = {key: value for key, value in receipt.items() if key != "image_delivery"}
        self.assertNotIn("images_not_delivered", clean_path(old), "A receipt before the contract never had one")
        dirty = dict(receipt, not_delivered_images=[[1, 0, {"view:ACME:LOGO": 1}]])
        self.assertNotIn("not_delivered_images", clean_path(dirty))
        self.assertEqual(clean_path(dirty)["images_not_delivered"], 1)

    def test_a_report_for_a_finished_or_other_request_records_nothing(self):
        path = self.proving()
        self.returned(path)
        stale = SimpleNamespace(host=SimpleNamespace(_request_metrics=SimpleNamespace(path=path)),
                                request_fence=SimpleNamespace(matches=lambda host: False))
        request_path.confirmed(stale, [(sha(PNG), None)], "resp-1")
        self.assertEqual(path.snapshot(settled=True)["images_not_delivered"], 1)


class NotificationTests(unittest.TestCase):
    """The report's field names live in ``image_proof.FIELDS`` alone."""

    def test_camel_and_snake_case_read_alike_and_malformed_entries_are_skipped(self):
        digest, source = sha(PNG), sha(OTHER_PNG)
        camel = image_proof.notification({"threadId": "t", "turnId": "u", "responseId": "r", "images": [
            {"callId": "c", "sha256": digest.upper(), "sourceSha256": source}, {"callId": "c", "sha256": digest.upper(),
                                                                               "sourceSha256": source},
            {"callId": "d", "sha256": "not a digest"}, "junk"]})
        snake = image_proof.notification({"thread_id": "t", "turn_id": "u", "response_id": "r", "images": [
            {"call_id": "c", "sha256": digest, "source_sha256": source}]})
        self.assertEqual(camel, snake)
        self.assertEqual(camel, {"thread": "t", "turn": "u", "response": "r", "images": [(digest, source)]})
        self.assertIsNone(image_proof.notification({"threadId": "t"}))

    def test_a_data_url_is_hashed_by_the_bytes_it_carries(self):
        url = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
        self.assertEqual(image_proof.url_digest(url), sha(PNG))
        for value in ("https://example.com/x.png", "data:image/png,raw", "data:image/png;base64,***", None):
            self.assertIsNone(image_proof.url_digest(value))


class GateTests(unittest.TestCase):
    def test_only_angelos_runtime_with_the_pinned_report_proves_pictures(self):
        for kind, notifications, expected in (("bundled", [image_proof.METHOD], True),
                                              ("override", [image_proof.METHOD], True),
                                              ("own", [image_proof.METHOD], False), (None, [image_proof.METHOD], False),
                                              ("bundled", [], False)):
            with self.subTest(kind=kind, notifications=notifications), \
                    patch.object(agent_runtime, "PIN", dict(agent_runtime.PIN, notifications=notifications)):
                self.assertIs(image_proof.supported(SimpleNamespace(rpc=None, _runtime_kind=kind)), expected)

    def test_the_shipped_pin_lists_only_notifications_the_build_emits(self):
        # The pinned build (agent_runtime.json, the hold-images fork) emits the report, so Codex pictures are
        # graded by content on it; the release checks confirm the build's schema has it.
        self.assertEqual(agent_runtime.PIN["notifications"], [image_proof.METHOD])


class ForwardingTests(unittest.TestCase):
    """A build that forwards listed tools' pictures in code mode: the launch lists them, the guidance says so."""

    def overrides(self, kind, config_keys):
        from angelo_agent import runtime_launch
        with patch.object(agent_runtime, "PIN", dict(agent_runtime.PIN, config_keys=config_keys)), \
                patch.object(agent_runtime, "kind", lambda executable, environ=None: kind), \
                tempfile.TemporaryDirectory() as state:
            command, _env = runtime_launch.codex_command("codex", "codex_subscription", state)
        return dict(value.split("=", 1) for flag, value in zip(command, command[1:]) if flag == "-c")

    def test_the_list_is_every_tool_whose_results_can_carry_pictures(self):
        from angelo_agent.claude_evidence import VISUAL_IMAGE_TOOLS
        from angelo_agent.plan_recovery import PREVIEW_IMAGE_TOOLS
        from angelo_agent.tool_specs import TOOLS
        names = [tool["name"] for tool in TOOLS]
        listed = image_proof.image_tools()
        self.assertEqual(list(listed), [name for name in names if image_proof.returns_images(name)], "Registry order")
        self.assertEqual(sorted((VISUAL_IMAGE_TOOLS | PREVIEW_IMAGE_TOOLS) & set(names) - set(listed)), [])
        self.assertTrue(set(listed) < set(names), "Never a tool whose results are only text")
        self.assertEqual([name for name in names if name.startswith(image_proof.MEDIA_PREFIXES)
                          and name not in listed], [])

    def test_the_list_is_passed_only_to_a_build_that_reads_it(self):
        from angelo_agent.runtime_launch import FORWARD_IMAGES_KEY
        on = self.overrides("bundled", [FORWARD_IMAGES_KEY])
        self.assertEqual(json.loads(on[FORWARD_IMAGES_KEY]), list(image_proof.image_tools()))
        for kind, keys in (("own", [FORWARD_IMAGES_KEY]), ("bundled", []), ("override", [])):
            with self.subTest(kind=kind, keys=keys):
                off = self.overrides(kind, keys)
                self.assertNotIn(FORWARD_IMAGES_KEY, off)
                self.assertIn("code_mode_hold_dynamic_tools", off, "Everything else is launched as before")
        self.assertEqual(agent_runtime.PIN["config_keys"], [FORWARD_IMAGES_KEY],
                         "The pinned build (the hold-images fork) reads the setting, and only that one")

    def test_the_guidance_is_true_of_each_runtime(self):
        from angelo_agent import providers
        for options in ({"short_identifiers": True, "deferred": True, "plan": True, "team": True},
                        {"short_identifiers": False, "deferred": True}):
            with self.subTest(options=options):
                before = providers.codex_instructions(**options)
                after = providers.codex_instructions(forwarded=True, **options)
                self.assertEqual(before, providers.codex_instructions(forwarded=False, **options))
                self.assertIn(image_proof.SHOWN_BY_IMAGE, before)
                self.assertEqual(after, before.replace(image_proof.SHOWN_BY_IMAGE, image_proof.SHOWN_ALWAYS),
                                 "Only how a result's pictures are shown differs")
                self.assertNotIn(image_proof.SHOWN_BY_IMAGE, after)
        undeferred = providers.codex_instructions(short_identifiers=False, deferred=False, forwarded=True)
        self.assertEqual(undeferred, providers.codex_instructions(short_identifiers=False, deferred=False),
                         "A thread without the code-mode guidance reads the same text")

    def test_a_thread_is_told_what_its_process_does(self):
        from angelo_agent.runtime_launch import FORWARD_IMAGES_KEY
        for kind, keys, forwarded in (("bundled", [FORWARD_IMAGES_KEY], True), ("own", [FORWARD_IMAGES_KEY], False),
                                      ("bundled", [], False)):
            with self.subTest(kind=kind, keys=keys), \
                    patch.object(agent_runtime, "PIN", dict(agent_runtime.PIN, config_keys=keys)):
                self.assertIs(image_proof.forwards_images(SimpleNamespace(rpc=None, _runtime_kind=kind)), forwarded)


class ProviderFixture(PlanTurnFixture):
    """A real Controller and Codex provider over the scripted app-server, through the real request context."""

    kind = "bundled"

    def setUp(self):
        super().setUp()
        del self.context.request_tool  # The request context's own dispatch, as test_native_request_path's.

        def dispatch(key, name, arguments, callback, request_fence=None, delivery_owner=None, result_observer=None,
                     completion_sink=None):
            def complete(result):
                if result_observer is not None:
                    result_observer(result)
                callback(result)
            self.request_tool(key, name, arguments, complete)
        self.c.request_tool = dispatch
        self.p._usage.thread_started("native-thread")
        self.p._runtime_kind = self.kind
        pin = patch.object(agent_runtime, "PIN", PIN)
        pin.start()
        self.addCleanup(pin.stop)

    def view(self, data=PNG, **rendered):
        reference = self.c.references.new_observation("Camera view")
        Path(reference["path"]).write_bytes(data)
        return view_result(dict(reference, view_id=reference["id"], view="SCENE_CAMERA", framing="SCENE_CAMERA",
                                shading="SOLID", width=8, height=8, camera={}, cache_hit=False),
                           **({"rendered": rendered} if rendered else {}))

    def emit(self, *images, turn=True, **fields):
        """A step: the runtime reports the pictures its next model request carried."""
        return ("call", lambda: self.p._event(image_proof.METHOD, report(
            *images, turn=self.p.turn_id if turn is True else turn, **fields)))

    def finish(self):
        self.c._finish_metrics("completed")
        return self.c.telemetry.receipts[-1]["path"]


class ProviderTests(ProviderFixture):
    """Every kind of picture is proven the same way: by its bytes, never by its tool or kind."""

    def picture(self, kind):
        """Run one turn that hands over a picture of ``kind``; returns the step that would report it."""
        if kind == "attachment":
            self.context.turn_attachments = [self.view()["views"][0]]
            return []
        if kind == "job":
            manager = JobManager(JobStore(self.temp.name))
            job_manager.install(manager)
            self.addCleanup(job_manager.install, None)
            runner = FakeRunner()
            self.job = manager.submit(self.c.state["id"], "call-1", {"kind": "RENDER_STILL", "output": "renders/a.png",
                                                                     "minutes": 30.0}, runner)
            runner.ended = True
            manager.poll()
            self.results["angelo_view"] = [self.view(job=self.job["id"])]
        else:
            self.results["angelo_view"] = [self.view()]
        return [("tool", "angelo_view", VIEW_ARGUMENTS)]

    def delivered(self, kind, receipt_kind=None):
        steps = self.picture(kind)
        status = self.run_turns(steps + [self.emit((sha(PNG), None)), ("message", '{"reply":"Seen.","operations":[]}')])
        self.assertEqual(status, "completed")
        path = self.finish()
        self.assertEqual((path["images_delivered"], path["image_delivery"]), (1, "confirmed"), path)
        self.assertNotIn("images_unconfirmed", path)
        self.assertNotIn("images_not_delivered", path)
        self.assertEqual(list(path["images"][0][2]), [receipt_kind or "job:" + self.job["id"]])

    def not_delivered(self, kind):
        steps = self.picture(kind)
        self.run_turns(steps + [self.emit((sha(OTHER_PNG), None)), ("message", '{"reply":"Done.","operations":[]}')])
        path = self.finish()
        self.assertEqual((path["images_not_delivered"], path["image_delivery"]), (1, "none"), path)
        self.assertNotIn("images_delivered", path)

    def test_a_reported_view_is_delivered(self):
        self.delivered("view", "view:SOLID:SCENE_CAMERA")

    def test_a_reported_job_picture_is_delivered_and_marks_the_job_shown(self):
        self.delivered("job")
        self.assertTrue(self.job["shown"].get("image_at"), "Proven delivery marks the job's picture shown")

    def test_a_reported_attachment_is_delivered(self):
        self.delivered("attachment", "attachment")

    def test_a_view_never_reported_is_not_delivered(self):
        self.not_delivered("view")

    def test_a_job_picture_never_reported_is_not_delivered_or_shown(self):
        self.not_delivered("job")
        self.assertEqual(self.job["shown"], {})

    def test_an_attachment_never_reported_is_not_delivered(self):
        self.not_delivered("attachment")

    def test_a_report_for_another_turn_or_thread_is_ignored(self):
        self.results["angelo_view"] = [self.view()]
        self.run_turns([("tool", "angelo_view", VIEW_ARGUMENTS), self.emit((sha(PNG), None), turn="another-turn"),
                        self.emit((sha(PNG), None), thread="another-thread"),
                        ("message", '{"reply":"Done.","operations":[]}')])
        self.assertEqual(self.finish()["images_not_delivered"], 1)

    def test_a_report_without_a_turn_id_counts_only_while_the_turn_is_open(self):
        self.results["angelo_view"] = [self.view()]
        self.run_turns([("tool", "angelo_view", VIEW_ARGUMENTS), self.emit((sha(PNG), None), turn=None),
                        ("message", '{"reply":"Done.","operations":[]}')])
        self.assertEqual(self.finish()["images_delivered"], 1)


class OlderRuntimeTests(ProviderFixture):
    """A Codex without the report keeps the unconfirmed record exactly, even if something emitted one."""

    kind = "own"

    def test_an_older_runtime_stays_unknown(self):
        self.results["angelo_view"] = [self.view()]
        self.run_turns([("tool", "angelo_view", VIEW_ARGUMENTS), self.emit((sha(PNG), None)),
                        ("message", '{"reply":"Done.","operations":[]}')])
        path = self.finish()
        self.assertEqual(path, {"edits": 0, "image_delivery": "unknown", "images_returned": 1, "images_unconfirmed": 1,
                                "unconfirmed_images": [[1, 0, {"view:SOLID:SCENE_CAMERA": 1}]]})

    def test_an_older_runtimes_attachment_keeps_its_acknowledgement(self):
        self.context.turn_attachments = [self.view()["views"][0]]
        self.run_turns([("message", '{"reply":"Received.","operations":[]}')])
        path = self.finish()
        self.assertEqual((path["images"], path["image_delivery"]), ([[1, None, {"attachment": 1}]], "confirmed"))


class RecordedRunTests(ProviderFixture):
    """Real bytes: two view cells of a recorded Codex run (fixtures/codex_recorded_view_cells.json).

    Each cell holds the data URL Angelo returned and the input image code mode forwarded to the model. Angelo
    packs the recorded picture into the same data URL, and the SHA-256 the runtime reports of the forwarded
    bytes is the one Angelo expects; the other cell's picture, which this report did not carry, is not delivered.
    """

    def setUp(self):
        super().setUp()
        self.cells = json.loads(FIXTURE.read_text(encoding="utf-8"))["cells"]

    @staticmethod
    def forwarded_digest(cell):
        """What the runtime reports: the SHA-256 of the decoded bytes it sent to the model."""
        return sha(base64.b64decode(cell["forwarded"]["image_url"].split(",", 1)[1]))

    def test_the_recorded_forwarded_bytes_match_the_returned_data_url(self):
        for cell in self.cells:
            self.assertEqual(image_proof.url_digest(cell["returned"]), self.forwarded_digest(cell))
        self.assertNotEqual(*[self.forwarded_digest(cell) for cell in self.cells], "Two different pictures")

    def test_a_report_of_the_recorded_bytes_delivers_its_picture_only(self):
        pictures = [base64.b64decode(cell["returned"].split(",", 1)[1]) for cell in self.cells]
        self.results["angelo_view"] = [self.view(data) for data in pictures]
        self.run_turns([("tool", "angelo_view", VIEW_ARGUMENTS), ("tool", "angelo_view", VIEW_ARGUMENTS),
                        self.emit((self.forwarded_digest(self.cells[1]), None)),
                        ("message", '{"reply":"Done.","operations":[]}')])
        packed = [item["imageUrl"] for _, payload in self.p.rpc.responses for item in payload["contentItems"]
                  if item["type"] == "inputImage"]
        self.assertEqual(packed, [cell["returned"] for cell in self.cells], "Angelo packs the recorded data URLs")
        path = self.finish()
        self.assertEqual((path["images_delivered"], path["images_not_delivered"]), (1, 1))
        self.assertEqual(path["image_delivery"], "confirmed")


if __name__ == "__main__":
    unittest.main()
