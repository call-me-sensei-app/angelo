# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A running look is finished the moment Blender's render job ends (``look_notify``, on unless ``ANGELO_LOOK_NOTIFY=0``).

Over the stand-in Blender of ``rendered_view_case``, with the real job manager:
the watch timer keeps ticking while the render runs, runs the manager's tick as
soon as no render job runs for the look's scene, and then stops; it never
watches other jobs, and does nothing with ``ANGELO_LOOK_NOTIFY=0``.
"""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rendered_view_case import LookCase  # noqa: E402
from angelo_agent import job_manager, look_notify  # noqa: E402


class NotifyTests(LookCase):
    def setUp(self):
        super().setUp()
        self.timers, self.finished = [], 0
        self.blender.bpy.app.timers = SimpleNamespace(
            is_registered=lambda function: function in self.timers,
            register=lambda function, **_: self.timers.append(function),
            unregister=lambda function: self.timers.remove(function))
        self.patches += [patch.object(job_manager, "_shared", self.manager),
                         patch.dict(os.environ, {"ANGELO_LOOK_NOTIFY": "1"})]
        for item in self.patches[-2:]:
            item.start()

    def finish(self):
        self.finished += 1
        self.manager.poll()

    def running_record(self):
        pending = self.begin()
        self.manager.poll()
        return pending, self.manager.store.get(pending.record["id"])

    def test_the_watch_finishes_the_look_when_the_render_ends_and_stops(self):
        pending, record = self.running_record()
        self.assertEqual(record["status"], "running")
        look_notify.watch(record, self.finish)
        self.assertEqual(self.timers, [look_notify._timer])
        self.assertEqual(look_notify._timer(), look_notify.SECONDS)  # Still rendering: one native call, no tick.
        self.assertEqual(self.finished, 0)
        self.blender.end()
        self.assertIsNone(look_notify._timer())  # Ended: the manager's tick ran at once; the watch stops.
        self.assertEqual(self.finished, 1)
        self.assertEqual(pending.record["status"], "done")
        self.assertTrue(pending.poll()["success"])

    def test_registered_once(self):
        _pending, record = self.running_record()
        look_notify.watch(record, self.finish)
        look_notify.watch(record, self.finish)
        self.assertEqual(self.timers, [look_notify._timer])

    def test_on_by_default_off_with_zero_and_never_for_other_jobs(self):
        _pending, record = self.running_record()
        self.assertTrue(look_notify.enabled({}))
        self.assertTrue(look_notify.enabled({"ANGELO_LOOK_NOTIFY": ""}))
        self.assertFalse(look_notify.enabled({"ANGELO_LOOK_NOTIFY": "0"}))
        with patch.dict(os.environ, {"ANGELO_LOOK_NOTIFY": "0"}):
            look_notify.watch(record, self.finish)
        look_notify.watch(dict(record, kind="RENDER_STILL"), self.finish)
        look_notify.watch(dict(record, status="starting"), self.finish)
        self.assertEqual(self.timers, [])

    def test_the_default_environment_watches(self):
        # On by default: with no ANGELO_LOOK_NOTIFY at all the watch is armed (setUp forces "1" for the other tests).
        _pending, record = self.running_record()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANGELO_LOOK_NOTIFY", None)
            look_notify.watch(record, self.finish)
        self.assertEqual(self.timers, [look_notify._timer])

    def test_no_running_look_stops_the_watch(self):
        _pending, record = self.running_record()
        look_notify.watch(record, self.finish)
        self.blender.end()
        self.manager.poll()  # The 0.25 s fallback got there first.
        self.assertIsNone(look_notify._timer())
        self.assertEqual(self.finished, 0)


if __name__ == "__main__":
    unittest.main()
