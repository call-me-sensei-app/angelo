# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only: a worker's deepest file paths (``worker_paths``), measured with the product's own writers, and the
Windows refusal before a worker starts or its chat exists (support code WRK-PATH-TOO-LONG)."""

import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
import uuid
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent import (private_roots, references, session_journal, session_kept, worker_paths,  # noqa: E402
                          worker_process, worker_sessions)
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

LIMIT = worker_paths.WINDOWS_LIMIT


class Recorded:
    """Every path ``session_journal`` and ``session_kept`` open, and hard links refused (a copy is made)."""

    def __enter__(self):
        self.opened, real = [], os.open

        def recording(path, *arguments):
            self.opened.append(str(path))
            return real(path, *arguments)
        self.patches = [mock.patch.object(session_journal.os, "open", recording),
                        mock.patch.object(session_kept.os, "link", side_effect=OSError("no links here"))]
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()


class DeepestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()

    def test_private_paths_are_the_ones_its_writers_make(self):
        private = Path(tempfile.mkdtemp(prefix=private_roots.WORKER_PREFIX, dir=self.parent))
        session = str(uuid.uuid4())
        store = SessionStore(private / worker_process.STATE, session_id=session)
        with Recorded() as recorded:
            session_journal.rewrite(store.path, b"{}\n")
            kept = session_kept.preserve(store.path)   # The copy route, as where hard links are missing.
        temporary, unreadable, copy, view = (len(str(path)) for path in worker_paths.private_paths(self.parent))
        self.assertEqual(len(str(private)) - len(str(self.parent)) - 1,
                         len(private_roots.WORKER_PREFIX) + worker_paths.RANDOM)
        journal_temporary = [path for path in recorded.opened if path.startswith(str(store.path) + ".")
                             and path.endswith(".tmp") and ".unreadable." not in path]
        self.assertEqual({len(path) for path in journal_temporary}, {temporary})
        suffix = len("-%d" % session_kept.KEPT_NAMES)   # Its first name has none; the last one the most.
        self.assertEqual(len(str(store.path.parent / kept)) + suffix, unreadable)
        copied = [path for path in recorded.opened if ".unreadable." in path and path.endswith(".tmp")]
        self.assertEqual({len(path) + suffix for path in copied}, {copy})
        views = references.ReferenceStore(private / worker_process.STATE, session).directory
        self.assertEqual(views, worker_process.references_of(private, session))
        self.assertEqual(len(str(views / (session + max(references.MIME, key=len)))), view)

    def test_snapshot_and_part_paths(self):
        store = CheckpointStore(self.parent, str(uuid.uuid4()))
        scene = store.folder(str(uuid.uuid4())) / "scene.blend"
        self.assertEqual(worker_paths.deepest(snapshots=self.parent), (len(str(scene)) + 1, str(self.parent)))
        part = self.parent / "parallel" / "p" / "part.blend"
        self.assertEqual(worker_paths.deepest(part=part), (len(str(part)) + 1, str(part.parent)))   # Blender's <part>@.

    def test_short_by_construction(self):
        """A person's profile leaves room to spare: about 30 characters of %LOCALAPPDATA% plus Angelo's folders."""
        scratch = "C:\\Users\\a-twenty-char-person\\AppData\\Local\\Angelo\\scratch"
        self.assertLessEqual(worker_paths.deepest(scratch)[0], LIMIT - 80)


class RefusalTests(unittest.TestCase):
    def test_windows_only_and_past_the_limit_only(self):
        base = worker_paths.deepest("C:\\a")[0] - len("C:\\a")
        at, past = "C:\\" + "a" * (LIMIT - base - 3), "C:\\" + "a" * (LIMIT - base - 2)
        self.assertEqual(worker_paths.deepest(at)[0], LIMIT)
        worker_paths.check(at, platform="win32")
        worker_paths.check(past, platform="darwin")
        with self.assertRaises(worker_paths.PathTooLong) as raised:
            worker_paths.check(past, platform="win32")
        error = raised.exception
        self.assertEqual((error.folder, error.needed, error.support, error.code),
                         (past, LIMIT + 1, "WRK-PATH-TOO-LONG", "LIMIT_EXCEEDED"))
        self.assertIn(past, str(error))
        self.assertIn("WRK-PATH-TOO-LONG", str(error))
        row = error.notice()
        self.assertEqual(row["i18n"][0], worker_paths.TOO_LONG)   # Translated whole at display time.
        self.assertEqual(row["i18n"][1]["folder"], past)

    def test_a_start_is_refused_before_anything_starts_and_the_lead_is_told_once(self):
        lead = types.SimpleNamespace(state={"id": str(uuid.uuid4()), "messages": []})
        lead.emit = lambda kind, row: lead.state["messages"].append(row)
        deep = "C:\\" + "s" * 220
        ui = types.SimpleNamespace(state_directory=lambda: deep)
        parts = [{"name": "Kettle", "brief": "A kettle."}]
        with mock.patch.dict(sys.modules, {"angelo_agent.ui": ui}), \
                mock.patch.object(private_roots, "scratch_root", return_value=Path("C:\\short")):
            for _ in range(2):   # An Ask card asks again before it runs: one row, not one per check.
                with self.assertRaises(worker_paths.PathTooLong):
                    worker_paths.check_start(lead, parts, 1, worker_sessions._slug, platform="win32")
            worker_paths.check_start(lead, parts, 1, worker_sessions._slug, platform="darwin")
        self.assertEqual(len(lead.state["messages"]), 1)
        self.assertEqual(lead.state["messages"][0]["role"], "system")
        self.assertIn("WRK-PATH-TOO-LONG", lead.state["messages"][0]["text"])

    def test_start_precondition_runs_the_check(self):
        lead = types.SimpleNamespace(state={"id": str(uuid.uuid4())})
        refused = worker_paths.PathTooLong("C:\\deep", LIMIT + 1)
        with mock.patch("angelo_agent.parallel_access.enabled", return_value=True), \
                mock.patch("angelo_agent.worker_routing.worker_snapshots", return_value=False), \
                mock.patch("angelo_agent.worker_dispatch.reclaim"), \
                mock.patch.object(worker_paths, "check_start", side_effect=refused) as check:
            with self.assertRaises(worker_paths.PathTooLong):
                worker_sessions.precondition(lead, "angelo_workers_start", {"parts": [{"name": "Lid", "brief": "x"}]})
        self.assertEqual(check.call_args.args[1], [{"name": "Lid", "brief": "x"}])

    def test_a_launch_past_the_limit_makes_no_private_folder_and_runs_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            popen = mock.Mock(side_effect=AssertionError("nothing may run"))
            part = Path(temporary, "p" * (LIMIT + 5), "part.blend")
            worker = worker_process.WorkerProcess(part, binary="angelo", script="worker.py", platform="win32",
                                                  popen=popen, listener=mock.Mock(), temporary=temporary)
            with self.assertRaises(worker_process.WorkerUnavailable) as raised:
                worker.start()
            self.assertIn("WRK-PATH-TOO-LONG", str(raised.exception))
            self.assertFalse(raised.exception.sent)
            self.assertEqual(os.listdir(temporary), [])
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
