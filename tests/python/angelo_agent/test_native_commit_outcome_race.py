# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stop cannot replace an already-committed outcome during result cleanup."""

import unittest
from unittest.mock import patch

from agent_runtime_fixtures import AgentRuntimeFixture


class CommitOutcomeRaceTests(AgentRuntimeFixture, unittest.TestCase):
    def test_stop_after_commit_before_cleanup_preserves_actual_result(self):
        answers, call = self.enqueue()
        job = self.ready()
        original = self.c._discard_native_job
        injected = False
        def discard(completed):
            nonlocal injected
            if not injected:
                injected = True
                self.assertEqual(job.backend.commits, 1)
                self.c.stop()
            return original(completed)
        with patch.object(self.c, "_discard_native_job", side_effect=discard):
            result = self.c.commit_native_job(call, self.executor, self.c.document)
        self.assertEqual(job.backend.commits, 1)
        self.assertEqual(job.backend.source, [1, 2, 3])
        self.assertEqual(len(answers), 1)
        self.assertTrue(answers[0]["success"])
        self.assertTrue(answers[0]["scene_changed"])
        self.assertNotIn("cancelled", answers[0])
        self.assertTrue(result["scene_changed"])
        self.assertFalse(self.c.pending)


if __name__ == "__main__":
    unittest.main()
