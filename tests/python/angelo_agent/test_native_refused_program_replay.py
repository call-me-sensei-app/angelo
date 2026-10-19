# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A recorded refusal replayed through the real controller: a refused program never holds the render job.

``refused_program_replay.json`` is taken from a recorded trial (its ``source`` says which): a sixteen-object
transform program bound to revisions the model's own frame change had moved, refused as ``STALE_REVISION``
with ``scene_changed: false``; the model's sixteen reads of those objects; and the render job it then
started. On an earlier build the refusal held the request until a cited correction, the reads could not explain
the change (the host had never captured those objects before) and the job was refused with ``RECOVERY_REQUIRED``.

Here the same results pass through ``Controller.finish_tool``, its read path and its admission
(``request_recovery``). The second class proves the hold that remains: an edit whose commit started and then
failed (``mutation_started``) holds every scene mutation until its targets are read again.
"""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime_fixtures import AgentRuntimeFixture, Backend, program
from angelo_agent.program_jobs import ProgramError

REPLAY = json.loads((Path(__file__).resolve().parent / "refused_program_replay.json").read_text(encoding="utf-8"))
TOOL = "angelo_scene_program"


class RecordedRefusalReplay(AgentRuntimeFixture, unittest.TestCase):
    def ask(self, identity, name, arguments):
        answers = []
        self.c.request_tool(identity, name, copy.deepcopy(arguments), answers.append)
        return answers

    def refuse(self):
        self.assertEqual(self.ask("program", TOOL, REPLAY["program"]), [])
        call = self.c.pending[self.c.state["id"] + "/program"]
        answers = []
        call.callback = answers.append  # The recorded refusal is this call's own result.
        self.c.finish_tool(call, dict(REPLAY["refusal"]))
        return answers[0] if answers else self.c.state["results"][call.id]["result"]

    def lookup(self, row):
        result = dict(row["result"], read_only=True)
        answers = []
        with patch.object(self.executor, "execute", return_value=result):
            self.c.request_tool("read-" + row["arguments"]["object"][-3:], "angelo_scene_object",
                                row["arguments"], answers.append)
            for _ in range(10):
                self.pump()
                if answers:
                    break
        return answers[0]

    def test_the_recorded_refusal_holds_nothing_and_the_render_job_is_admitted(self):
        refused = self.refuse()
        self.assertEqual(refused["error_code"], "STALE_REVISION")
        self.assertNotIn("recovery_required", refused)
        self.assertEqual((refused["recovery"]["holding"], refused["recovery"]["read"]),
                         (False, REPLAY["refusal"]["refusal_details"]["handles"]))
        # The job right away, before any read: nothing the refused program did needs reading first.
        self.assertNotIn("RECOVERY_REQUIRED", [a.get("error_code") for a in self.ask("job-now", "angelo_job_start",
                                                                                     REPLAY["job"])])
        self.assertIn(self.c.state["id"] + "/job-now", self.c.pending)

    def test_the_recorded_reads_show_the_target_and_close_the_recovery(self):
        self.refuse()
        first = self.lookup(REPLAY["reads"][0])
        recovery = first["recovery"]
        self.assertFalse(recovery["holding"])
        self.assertEqual(recovery["not_captured"], REPLAY["refusal"]["refusal_details"]["handles"])
        self.assertIsNone(self.c.recovery_barrier.pending)
        for row in REPLAY["reads"][1:]:
            self.assertNotIn("recovery", self.lookup(row))
        for identity, name, arguments in (("retry", TOOL, REPLAY["program"]), ("job", "angelo_job_start", REPLAY["job"]),
                                          ("script", "angelo_execute_python", {"description": "x", "code": "pass"})):
            self.assertNotIn("RECOVERY_REQUIRED", [a.get("error_code") for a in self.ask(identity, name, arguments)])


class DivergedCommitStillHolds(AgentRuntimeFixture, unittest.TestCase):
    """An edit whose live commit started and then failed may have changed the scene: it holds until read."""

    def diverge(self):
        def commit(backend, stage):
            # A write happened and was not rolled back (program_commit keeps mutation_started), then the live
            # state was found changed.
            backend.source, backend.mutation_started = list(stage["location"]), True
            raise ProgramError("The exact target changed during commit", code="STALE_REVISION",
                               details={"handles": [program()["write_set"][0]["handle"]]})
        answers, call = self.enqueue()
        with patch.object(Backend, "commit", commit):
            for _ in range(40):
                self.pump()
                if answers:
                    break
        return answers[0]

    def test_mutations_wait_for_the_reads_and_a_render_does_not(self):
        failed = self.diverge()
        self.assertEqual(failed["error_code"], "STALE_REVISION")
        self.assertTrue(failed["partial_changes_possible"])
        self.assertIs(failed["recovery_required"], True)
        handle = program()["write_set"][0]["handle"]
        self.assertIsInstance(failed["recovery"], str)  # The native recovery text stands beside the barrier's.
        self.assertEqual(failed["recovery_requirements"]["read"], [handle])
        for identity, name, arguments in (("edit", TOOL, program()), ("script", "angelo_execute_python",
                                                                      {"description": "x", "code": "pass"}),
                                          ("bake", "angelo_job_start", {"kind": "BAKE_TEXTURE", "target": handle})):
            self.assertEqual([a.get("error_code") for a in self.ask(identity, name, arguments)], ["RECOVERY_REQUIRED"])
        self.assertNotIn("RECOVERY_REQUIRED", [a.get("error_code") for a in self.ask(
            "render", "angelo_job_start", REPLAY["job"])])
        answers = []
        with patch.object(self.executor, "execute", return_value={"object": handle, "object_revision": "b" * 64}):
            self.c.request_tool("read", "angelo_scene_object", {"object": handle}, answers.append)
            for _ in range(10):
                self.pump()
                if answers:
                    break
        self.assertIn("next step", answers[0]["recovery"]["reason"])
        same = self.ask("edit-after-read", TOOL, program())  # The step that read: refused, carrying what changed.
        self.assertEqual([a.get("error_code") for a in same], ["RECOVERY_REQUIRED"])
        self.c.recovery_barrier.step()  # The model's next response.
        self.assertEqual(self.ask("edit-next-step", TOOL, program()), [])

    ask = RecordedRefusalReplay.ask


if __name__ == "__main__":
    unittest.main()
