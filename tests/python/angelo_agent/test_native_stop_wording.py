# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stop's wording knows which work keeps running, and billing, at the provider after a local Stop.

Stop on a paid generation ended only Angelo's watching of it (``Controller.cancel_tools``: the provider may still
finish and bill it), yet the chat said "Stopped" and the step's chip "Stopped" (1.0.10 chat audit). Now the Stop
notice (``stop_notice``) says the provider may still finish and charge, and the stopped step of any billed tool
(``chat_titles.BILLED_TOOLS``, the tools Controller gives a usage label) reads "Stopped watching". Person-facing only:
the result the model reads is unchanged. Dependency-free: the controller's own rows, no Blender or provider.
"""

import suite_isolation  # noqa: F401  Standalone provider isolation.

import itertools
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import activity_text, chat_titles  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.pending_delivery import ToolCall  # noqa: E402
from angelo_agent.stop_notice import stop_message, stop_notice_for  # noqa: E402
from angelo_agent.studio_agent import StudioPending  # noqa: E402


def row(tool, result, status="completed"):
    return {"role": "activity", "call_id": "c", "text": "", "tool": tool, "arguments": {}, "status": status,
            "result": result}


SUBMITTED = {"error_code": "CANCELLED", "error": "Studio monitoring stopped.", "asset_id": "a1"}   # cancel_tools'
NOT_SENT = {"error_code": "CANCELLED", "error": "The user cancelled this turn; no operation was applied"}


class StopNoticeWordingTests(unittest.TestCase):
    def test_never_a_bare_stopped_while_billed_work_was_running(self):
        for descriptors, script in itertools.product(([], [{"state": "RUNNING"}], [{"state": "PREVIEW"}]), (False, True)):
            with self.subTest(descriptors=descriptors, script=script):
                text = stop_message(descriptors, script, remote_paid=True)
                self.assertNotEqual(text, "Stopped")
                self.assertIn("charge", text, "the provider may still bill it")
                unpaid = stop_message(descriptors, script)
                for fact in ("Previews are not applied", "between steps", "interrupted"):
                    if fact in unpaid:
                        self.assertIn(fact, text, "billing is said on top of what Stop does locally")

    def test_nothing_billed_keeps_the_existing_wording(self):
        self.assertEqual(stop_message([]), "Stopped")
        self.assertIn("between steps", stop_message([{"state": "RUNNING"}]))


class ControllerStopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller(self.directory.name)
        self.controller.set_document(("stop-wording", 1))
        self.controller.busy = True

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def watch(self, call_id, tool, usage_label):
        """A submitted job the controller watches, as ``execute_tool`` leaves a StudioPending result."""
        call = ToolCall(id=call_id, name=tool, arguments={}, document=self.controller.document, callbacks=[],
                        usage_label=usage_label, executing=True)
        self.controller.pending[call_id] = call
        self.controller.media_jobs[call_id] = StudioPending(lambda: None, lambda: None, asset_id="asset-" + call_id)
        self.controller.emit("activity", {"id": call_id, "tool": tool, "arguments": {}, "status": "running",
                                          "result": {"asset_id": "asset-" + call_id, "account": usage_label}})
        return call

    def test_stop_on_a_paid_generation_says_the_provider_may_still_charge_and_its_step_says_stopped_watching(self):
        for tool in sorted(chat_titles.BILLED_TOOLS):
            with self.subTest(tool=tool):
                self.watch("job-" + tool, tool, "Provider · API billing")
                notice = stop_notice_for(self.controller)
                self.assertNotEqual(notice["text"], "Stopped")
                self.assertIn("charge", notice["text"])
                self.assertIn("i18n", notice, "translated where it is drawn")
                self.controller.cancel_tools("The user cancelled this turn; no operation was applied")
                self.controller.pump_events()
                step = next(m for m in self.controller.state["messages"] if m.get("call_id") == "job-" + tool)
                self.assertEqual(activity_text.step_status(step), "stopped_watching")
                self.assertEqual(activity_text.status_label("stopped_watching"), "Stopped watching")
                self.assertIn("stopped watching", chat_titles.tool_title(step))
                self.assertEqual(step["result"]["error_code"], "CANCELLED", "the model's result is unchanged")

    def test_a_watched_job_with_no_billing_label_keeps_plain_stopped(self):
        self.watch("look", "angelo_view", "")
        self.assertEqual(stop_notice_for(self.controller)["text"], "Stopped")


class StoppedStepTests(unittest.TestCase):
    def test_every_billed_tool_stopped_after_it_was_sent_reads_stopped_watching(self):
        for tool in sorted(chat_titles.BILLED_TOOLS):
            with self.subTest(tool=tool):
                self.assertEqual(activity_text.step_status(row(tool, SUBMITTED)), "stopped_watching")
                self.assertEqual(activity_text.step_status(row(tool, NOT_SENT)), "stopped",
                                 "stopped before anything was sent: nothing can be billed")

    def test_other_tools_stay_plain_stopped(self):
        self.assertEqual(activity_text.step_status(row("angelo_scene_program", SUBMITTED)), "stopped")
        self.assertEqual(activity_text.step_status(row("angelo_execute_python", NOT_SENT)), "stopped")

    def test_every_status_has_a_label_a_tone_and_a_chip_and_counts_as_not_run(self):
        for status in activity_text.STATUSES:
            self.assertNotEqual(activity_text.status_label(status), "", status)
        self.assertIn("stopped_watching", chat_titles.NOT_RUN_STATES)
        self.assertIn("stopped_watching", chat_titles.STATES)
        # Exercise the actual row function, whose mapping is the shared
        # activity_text.CHIP alias; do not require duplicated source literals.
        from angelo_agent import chat_rows, ui_kit_status
        gpu = SimpleNamespace(state=SimpleNamespace(blend_set=lambda mode: None))
        view = object()
        with mock.patch.dict(sys.modules, {"gpu": gpu}), \
             mock.patch.object(chat_rows, '_view', return_value=view), \
             mock.patch.object(ui_kit_status, 'chip_size', return_value=(90, 20)) as size, \
             mock.patch.object(ui_kit_status, 'draw_chip') as draw:
            self.assertEqual(chat_rows.chip(100, 50, 'stopped_watching'), 90)
        size.assert_called_once_with(view, 'needs_you', 1.0, 'Stopped watching')
        self.assertEqual(draw.call_args.args[4:6], ('needs_you', 'Stopped watching'))
        title = activity_text.group_title([row(tool, SUBMITTED) for tool in sorted(chat_titles.BILLED_TOOLS)], False)
        self.assertIn("Stopped watching", title)


if __name__ == "__main__":
    unittest.main()
