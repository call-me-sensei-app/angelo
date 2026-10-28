# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A background job outside Angelo's window, without Blender: awake while it runs, a notification when it ends.

The policy (which jobs hold the computer awake, which endings are announced), the
translated notification text, the fallbacks when notifications are not allowed and
the ctypes layouts of the Windows calls. On macOS the real power assertion is held
and released once and read back from ``pmset -g assertions``. The native
notification itself is checked in the running app (``test_blender_job_desktop``).
"""

import ctypes
import subprocess
import sys
import unittest

from host_test_policy import environment_skip
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import desktop_os, job_desktop, ui_text  # noqa: E402
from angelo_agent.job_store import new_record  # noqa: E402

PREVIEW = "RENDER_PREVIEW"


def job(identity="j1", kind="RENDER_ANIMATION", status="done", seconds=90.0, reason="", files=True, **fields):
    record = new_record(identity, "chat", "call", {"kind": kind, "output": "renders/walk"}, now=100.0)
    record.update(status=status, reason=reason, started=100.0, ended=100.0 + seconds, **fields)
    record["receipt"] = {"files": 24} if files else {}
    record["detail"].setdefault("frames", [1, 24])
    return record


class Manager:
    def __init__(self, active=None, queued=()):
        self._active, self._queued = active, list(queued)

    def active(self):
        return self._active

    def queued(self):
        return list(self._queued)


class Translated:
    def __enter__(self):
        self.saved = dict(ui_text._pgettext)
        mark = lambda message, context=None: "‹" + message + "›"  # noqa: E731
        ui_text._pgettext.update(iface=mark, tip=mark, rpt=mark)

    def __exit__(self, *_):
        ui_text._pgettext.clear()
        ui_text._pgettext.update(self.saved)


class Native:
    """Stand-in for ``_bpy._angelo_jobs``' desktop calls: records each call, answers as configured."""

    def __init__(self, notify="posted", attention="bounced"):
        self.calls, self.notify, self.attention = [], notify, attention

    def desktop_notify(self, *args):
        self.calls.append(("notify",) + args)
        return self.notify

    def desktop_attention(self):
        self.calls.append(("attention",))
        return self.attention

    def desktop_request_permission(self):
        self.calls.append(("request",))

    def desktop_permission(self):
        self.calls.append(("permission",))
        return "authorized"


class Backend:
    def __init__(self, token=7):
        self.token, self.held, self.released = token, [], []

    def hold(self, reason):
        self.held.append(reason)
        return self.token

    def release(self, token):
        self.released.append(token)


class Patched(unittest.TestCase):
    def setUp(self):
        self.saved = (job_desktop._preference, job_desktop._native, job_desktop._awake, job_desktop._asked,
                      job_desktop._automation_off, job_desktop._working)
        self.prefs = {"job_keep_awake": True, "job_notifications": True}
        self.native = Native()
        self.backend = Backend()
        job_desktop._preference = lambda name, default=True: self.prefs.get(name, default)
        job_desktop._native = lambda: self.native
        job_desktop._awake = desktop_os.KeepAwake(platform="test", backend=self.backend)
        job_desktop._asked = job_desktop._working = False
        job_desktop._automation_off = lambda: False

    def tearDown(self):
        (job_desktop._preference, job_desktop._native, job_desktop._awake, job_desktop._asked,
         job_desktop._automation_off, job_desktop._working) = self.saved


class Announced(unittest.TestCase):
    def test_long_endings_nobody_chose_are_announced(self):
        from angelo_agent.job_outputs import MOVED
        for status, reason in (("done", ""), ("failed", "Blender refused"), ("failed", MOVED),
                               ("cancelled", "time_budget")):
            self.assertTrue(job_desktop.announced(job(status=status, reason=reason)), (status, reason))

    def test_short_chosen_or_closed_endings_and_looks_are_not(self):
        self.assertFalse(job_desktop.announced(job(seconds=job_desktop.NOTIFY_AFTER_SECONDS - 0.5)))
        self.assertTrue(job_desktop.announced(job(seconds=job_desktop.NOTIFY_AFTER_SECONDS)))
        for reason in ("user", "agent", "some later reason"):   # An allowlist: an unknown cancel is not announced.
            self.assertFalse(job_desktop.announced(job(status="cancelled", reason=reason)), reason)
        self.assertFalse(job_desktop.announced(job(status="interrupted", reason="file_closed")))
        self.assertFalse(job_desktop.announced(job(kind=PREVIEW, seconds=600)))
        self.assertFalse(job_desktop.announced(job(status="running")))
        never_started = job(status="failed")
        never_started["started"] = None
        self.assertFalse(job_desktop.announced(never_started))

    def test_the_reasons_are_the_ones_the_manager_writes(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/job_manager.py").read_text(encoding="utf-8")
        self.assertIn('by="time_budget"', source)
        host = (ROOT / "scripts/addons_core/angelo_agent/job_host.py").read_text(encoding="utf-8")
        self.assertIn('by="agent"', host)

    def test_every_kind_is_announced_alike(self):
        from angelo_agent.job_spec import KINDS
        for kind in KINDS:
            self.assertTrue(job_desktop.announced(job(kind=kind)), kind)


class Awake(unittest.TestCase):
    def test_jobs_hold_and_looks_do_not(self):
        self.assertFalse(job_desktop.holds_awake(None))
        self.assertFalse(job_desktop.holds_awake(Manager()))
        self.assertTrue(job_desktop.holds_awake(Manager(active=job(status="running"))))
        self.assertFalse(job_desktop.holds_awake(Manager(active=job(kind=PREVIEW, status="running"))))
        # A stale pointer to an ended record never holds it (by status, not by which record is "running").
        self.assertFalse(job_desktop.holds_awake(Manager(active=job(status="interrupted"))))
        # A job queued behind a look holds it: the computer must be awake when that job starts.
        self.assertTrue(job_desktop.holds_awake(Manager(active=job(kind=PREVIEW, status="running"),
                                                        queued=[job("j2", status="queued")])))

    def test_keep_awake_is_one_idempotent_hold(self):
        backend = Backend()
        awake = desktop_os.KeepAwake(platform="test", backend=backend)
        self.assertTrue(awake.hold("reason") and awake.hold("reason"))
        self.assertEqual((backend.held, awake.status), (["reason"], "held"))
        awake.release()
        awake.release()
        self.assertEqual((backend.released, awake.status, awake.held), ([7], "idle", False))

    def test_a_refused_or_missing_backend_is_a_status_never_an_error(self):
        awake = desktop_os.KeepAwake(platform="test", backend=Backend(token=None))
        self.assertEqual((awake.hold("reason"), awake.status), (False, "failed"))
        awake = desktop_os.KeepAwake(platform="plan9")
        self.assertEqual((awake.hold("reason"), awake.status), (False, "unsupported"))
        awake.release()
        self.assertEqual(desktop_os.flash_taskbar(platform="plan9"), "unsupported")


class WindowsGate(unittest.TestCase):
    def test_windows_stays_off_until_verified_or_opted_in(self):
        import os
        saved = os.environ.pop("ANGELO_WINDOWS_JOB_DESKTOP", None)
        try:
            self.assertFalse(desktop_os.WINDOWS_VERIFIED)
            awake = desktop_os.KeepAwake(platform="win32")
            self.assertEqual((awake.hold("reason"), awake.status), (False, "unsupported"))
            self.assertEqual(desktop_os.flash_taskbar(platform="win32"), "unsupported")
            os.environ["ANGELO_WINDOWS_JOB_DESKTOP"] = "1"
            self.assertTrue(desktop_os.windows_enabled())
        finally:
            os.environ.pop("ANGELO_WINDOWS_JOB_DESKTOP", None)
            if saved is not None:
                os.environ["ANGELO_WINDOWS_JOB_DESKTOP"] = saved


class Sync(Patched):
    def test_held_from_the_first_job_to_the_end_of_the_last_and_asked_once(self):
        job_desktop.sync(Manager(active=job(status="running")))
        job_desktop.sync(Manager(active=job(status="running")))
        self.assertEqual(len(self.backend.held), 1)
        self.assertEqual(self.native.calls, [("request",), ("permission",)])
        job_desktop.sync(Manager())
        self.assertEqual((self.backend.released, job_desktop._awake.held), ([7], False))
        job_desktop.sync(Manager(active=job("j2", status="running")))
        # Never asked twice; the permission is read again when the next job starts (settings may have changed).
        self.assertEqual(self.native.calls, [("request",), ("permission",), ("permission",)])

    def test_preferences_turn_each_off(self):
        self.prefs.update(job_keep_awake=False, job_notifications=False)
        job_desktop.sync(Manager(active=job(status="running")))
        self.assertEqual((self.backend.held, self.native.calls), ([], []))
        self.assertEqual(job_desktop.ended(job()), "skipped")

    def test_turning_keep_awake_off_while_held_releases_it(self):
        job_desktop.sync(Manager(active=job(status="running")))
        self.prefs["job_keep_awake"] = False
        job_desktop.sync(Manager(active=job(status="running")))
        self.assertFalse(job_desktop._awake.held)

    def test_an_automation_run_is_never_asked_notified_or_bounced(self):
        job_desktop._automation_off = lambda: True
        job_desktop.sync(Manager(active=job(status="running")))
        self.assertEqual(job_desktop.ended(job()), "automation")
        self.assertEqual(self.native.calls, [])
        self.assertEqual(len(self.backend.held), 1)   # Keeping the computer awake is harmless there.

    def test_the_proving_suite_opts_in(self):
        import os
        job_desktop._automation_off = lambda: True
        os.environ["ANGELO_ENABLE_JOB_NOTIFICATIONS"] = "1"
        try:
            self.assertEqual(job_desktop.ended(job()), "posted")
        finally:
            del os.environ["ANGELO_ENABLE_JOB_NOTIFICATIONS"]

    def test_a_failing_call_never_raises_into_the_poll(self):
        def broken(name, default=True):
            raise RuntimeError("preferences gone")
        job_desktop._preference = broken
        job_desktop.sync(Manager(active=job(status="running")))
        self.assertEqual(job_desktop.ended(job()), "failed")


class Ended(Patched):
    def test_posted_with_translated_text_and_no_local_path(self):
        with Translated():
            self.assertEqual(job_desktop.ended(job()), "posted")
        (_, identifier, title, body), = self.native.calls
        self.assertEqual(identifier, "angelo.job.j1")
        self.assertEqual(title, "‹‹Rendering an animation› finished›")
        self.assertEqual(body, "‹Took ‹1 min 30 s›› · renders/walk")

    def test_a_failure_reason_never_reaches_the_notification(self):
        reason = "Finishing the job failed (PermissionError: [Errno 13] denied: '/Users/someone/project/out')"
        _identifier, title, body = job_desktop.notification(job(status="failed", reason=reason))
        self.assertNotIn("/Users", title + body)
        self.assertNotIn("PermissionError", title + body)
        self.assertNotIn("renders/walk", body)   # A failed job published nothing.

    def test_each_ending_has_its_own_heading(self):
        headings = {job_desktop.notification(job(status=status, reason="time_budget"))[1]
                    for status in ("done", "failed", "cancelled")}
        self.assertEqual(len(headings), 3)

    def test_right_to_left_text_reaches_the_system_in_reading_order(self):
        from angelo_agent import rtl_text
        logical = "تم العرض في دقيقة واحدة"
        visual = rtl_text.to_visual(logical)
        self.assertTrue(rtl_text.is_visual(visual))
        self.assertEqual(job_desktop.for_the_system(visual), logical)
        self.assertEqual(job_desktop.for_the_system("renders/walk"), "renders/walk")
        saved = dict(ui_text._pgettext)
        ui_text._pgettext.update(iface=lambda message, context=None: visual if message == job_desktop.TOOK else message)
        try:
            body = job_desktop.notification(job())[2]
        finally:
            ui_text._pgettext.clear()
            ui_text._pgettext.update(saved)
        self.assertFalse(rtl_text.is_visual(body), body)

    def test_not_allowed_falls_back_to_attention_and_says_which(self):
        self.native.notify = "denied"
        self.assertEqual(job_desktop.ended(job()), "bounced")
        self.assertEqual([call[0] for call in self.native.calls], ["notify", "attention"])
        self.native.calls, self.native.attention = [], "foreground"
        self.assertEqual(job_desktop.ended(job()), "foreground")   # Nothing to bounce: said, never "attention".

    def test_windows_falls_back_to_the_taskbar(self):
        self.native.notify, self.native.attention = "unsupported", "unsupported"
        saved = desktop_os.flash_taskbar
        desktop_os.flash_taskbar = lambda: "flashed"
        try:
            self.assertEqual(job_desktop.ended(job()), "flashed")
        finally:
            desktop_os.flash_taskbar = saved

    def test_short_jobs_post_nothing(self):
        self.assertEqual(job_desktop.ended(job(seconds=5)), "skipped")
        self.assertEqual(self.native.calls, [])


class WindowsLayouts(unittest.TestCase):
    @unittest.skipUnless(ctypes.sizeof(ctypes.c_void_p) == 8, environment_skip("64-bit layouts"))
    def test_structures_match_the_64_bit_windows_abi(self):
        self.assertEqual(ctypes.sizeof(desktop_os._ReasonContext), 32)   # REASON_CONTEXT
        self.assertEqual(desktop_os._ReasonContext.reason.offset, 8)
        self.assertEqual(ctypes.sizeof(desktop_os._FlashInfo), 32)       # FLASHWINFO
        self.assertEqual(desktop_os._FlashInfo.window.offset, 8)


class Hooks(unittest.TestCase):
    def test_job_ui_calls_the_desktop_on_change_poll_finish_and_unregister(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/job_ui.py").read_text(encoding="utf-8")
        finished = source[source.index("def _finished"):source.index("def _tick")]
        self.assertLess(finished.index("return\n"), finished.index("job_desktop.ended(record)"))   # Never for a look.
        self.assertIn("job_desktop.sync(", source[source.index("def _changed"):source.index("def _finished")])
        self.assertIn("job_desktop.sync(jobs)", source[source.index("def _tick"):source.index("def busy_scenes")])
        self.assertIn("job_desktop.shutdown()", source[source.index("def unregister"):])


@unittest.skipUnless(sys.platform == "darwin", environment_skip("macOS power assertion"))
class MacAssertion(unittest.TestCase):
    def test_the_real_assertion_is_listed_while_held_and_gone_after(self):
        reason = "Angelo host test: a background job is running"
        awake = desktop_os.KeepAwake()
        listed = lambda: reason in subprocess.run(["pmset", "-g", "assertions"], capture_output=True,  # noqa: E731
                                                  text=True).stdout
        try:
            self.assertTrue(awake.hold(reason))
            self.assertTrue(listed())
        finally:
            awake.release()
        self.assertFalse(listed())


if __name__ == "__main__":
    unittest.main()
