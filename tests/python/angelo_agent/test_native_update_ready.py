# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Installing updates inside Blender: update_ready (background preparation, the card's phases, throttling,
retries) and update_restart (never during work, the save prompt, the installer's hand-over, the next launch).

On the stand-in bpy of test_native_app_notices; preparation and the installer are stood in for (their own rules
are test_native_update_flow and test_native_update_install). The real thing: the end-to-end updates.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
import update_fixtures as ufx  # noqa: E402
from notices_fakes import Notices  # noqa: E402
from angelo_agent import managed_policy, update_check, update_feed, update_flow, update_state, work_guard  # noqa: E402

NOTICE_VARIABLES = ("ANGELO_DISABLE_INTRO", "ANGELO_DISABLE_DEFAULT_CHAT", "ANGELO_ENABLE_APP_NOTICES")


class Policy:
    disable_update_check = False
    update_feed_url = ""
    disable_crash_reporting = False


class FakePreparer:
    """``update_flow.Preparer`` as update_ready uses it."""

    def __init__(self, root, outcome=None, reason=""):
        self.root, self.outcome, self.reason, self.prepared, self.valid = Path(root), outcome, reason, [], True
        self.cleaned, self.result_list = [], []

    def support(self):
        return self.reason, "/Applications/Angelo.app"

    def prepare(self, release, download, cancel=None, limit=None, progress=None):
        self.prepared.append(release.version)
        self.limit = limit
        if progress is not None:
            progress(download.size, download.size)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome or update_flow.Ready(release.version, "macos", str(self.root / release.version / "staged"),
                                                 str(self.root / release.version), "/Applications/Angelo.app", "f", "full")

    def still_valid(self, ready):
        return self.valid

    def work(self, version):
        return self.root / version

    def results(self):
        return list(self.result_list)

    def clean(self, keep=()):
        self.cleaned.append(set(keep))
        return []

    def clean_older(self, version):
        self.cleaned.append(("older than", version))
        return []

    def recall(self, version, download=None):
        return None


class ReadyCase(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.folder, True)
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in NOTICE_VARIABLES:
            os.environ.pop(name, None)
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        notices = Notices()
        self.n = notices.__enter__()
        self.addCleanup(notices.__exit__, None, None, None)
        self.n.update_ui.directory = lambda: str(self.folder)
        self.n.card_host._queue.clear()
        self.ready, self.restart = self.n.update_ready, self.n.update_restart
        self.ready.register()
        self.reasons = []
        guard = mock.patch.object(work_guard, "reasons", lambda bpy=None: list(self.reasons))
        guard.start()
        self.addCleanup(guard.stop)
        self.preparer = FakePreparer(self.folder / "updates")
        self.ready.preparer = lambda: self.preparer
        self.seed, self.keys = ufx.new_key()

    def offer(self, version="1.1.0", url=None, **changes):
        from datetime import datetime, timezone
        document = ufx.payload(version=version, **changes)
        document["latest"]["downloads"]["macos-arm64"]["url"] = url or (
            "https://downloads.angelo.studio/%s/Angelo-%s-macos-arm64.dmg" % (version, version))
        feed = update_feed.open_feed(ufx.envelope(document, self.seed), self.keys,
                                     datetime(2026, 10, 25, tzinfo=timezone.utc))
        return update_feed.offer_for(feed, "1.0.0", "macos-arm64")

    def finish_worker(self):
        job = self.ready._job
        if job is not None:
            job["thread"].join(5)
        self.ready.tick()

    def queued(self, kind=None):
        return self.n.card_host.queued(kind)

    def shown(self, kind):
        """The waiting card of ``kind``, taken off the queue as card_host does when it shows it."""
        card = self.queued(kind)[0]
        self.n.card_host._queue[:] = [item for item in self.n.card_host._queue if item[2] is not card]
        return card


class PreparationTests(ReadyCase):
    def test_an_automatic_check_prepares_quietly_then_offers_the_restart(self):
        offer = self.offer()
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, offer), manual=False)
        self.assertEqual(self.queued(), [], "nothing is shown while it downloads")
        self.finish_worker()
        self.assertEqual(self.preparer.prepared, ["1.1.0"])
        self.assertEqual(self.ready.phase(offer), "ready")
        card = self.queued("update")[0]
        self.assertIs(card.live, self.ready)
        self.assertTrue(self.ready.ready_for(offer))
        self.assertEqual(self.n.update_ui._status_text(), "Restart to Update")

    def test_a_manual_check_shows_the_card_at_once(self):
        offer = self.offer()
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, offer), manual=True)
        self.assertEqual([c.kind for c in self.queued()], ["update"])
        self.finish_worker()

    def test_later_and_skip_hold_the_ready_card_back(self):
        offer = self.offer()
        self.n.update_ui.checker().snooze(offer)
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, offer, snoozed=True), manual=False)
        self.finish_worker()
        self.assertEqual(self.ready.phase(offer), "ready")
        self.assertEqual(self.queued(), [], "Later: ready, but no card yet")
        self.assertEqual(self.n.update_ui._status_text(), "Restart to Update", "the status bar still offers it")

    def test_an_install_that_cannot_update_itself_keeps_the_notice(self):
        self.preparer.reason = "permission"
        offer = self.offer()
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, offer), manual=False)
        self.assertEqual(self.preparer.prepared, [])
        self.assertEqual([c.kind for c in self.queued()], ["update"])
        self.assertEqual(self.ready.unsupported_reason(offer), "permission")

    def test_a_release_the_feed_says_to_install_by_hand(self):
        offer = self.offer()
        manual = type(offer)(type(offer.release)(**dict(offer.release.__dict__, automatic_install=False)),
                             offer.download, offer.critical, offer.unsupported)
        self.assertFalse(self.ready.consider(manual))
        self.assertEqual(self.ready.automatic(manual), "manual")

    def test_only_the_release_downloads_host_is_installed_from(self):
        for url in ("https://github.com/someone/angelo/releases/download/v1.1.0/Angelo-1.1.0-macos-arm64.dmg",
                    "https://angelo.studio/downloads/Angelo-1.1.0-macos-arm64.dmg"):
            with self.subTest(url=url):
                offer = self.offer(url=url)
                self.assertFalse(self.ready.consider(offer))
                self.assertEqual(self.ready.automatic(offer), "manual")
        self.assertEqual(self.preparer.prepared, [])

    def test_a_failed_preparation_falls_back_to_the_download_notice(self):
        self.preparer.outcome = update_flow.PrepareError("verify:signature")
        offer = self.offer()
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, offer), manual=False)
        self.finish_worker()
        self.assertEqual(self.ready.phase(offer), "notify")
        self.assertEqual(self.ready.unsupported_reason(offer), "failed")
        self.assertEqual([c.kind for c in self.queued()], ["update"])

    def test_a_passing_failure_is_tried_again_later_and_told_after_three(self):
        self.preparer.outcome = update_flow.PrepareError("download:offline", transient=True)
        offer = self.offer()
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, offer), manual=False)
        self.finish_worker()
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.ready.automatic(offer), "", "still to be installed automatically")
        for attempt in range(2):
            code, transient, when, attempts = self.ready._failed["1.1.0"]
            self.ready._failed["1.1.0"] = (code, transient, when - 10 ** 6, attempts)   # The backoff has passed.
            self.ready.tick()
            self.finish_worker()
        self.assertEqual(self.preparer.prepared, ["1.1.0"] * 3)
        self.assertEqual([c.kind for c in self.queued()], ["update"], "told after the third passing failure")

    def test_a_download_is_throttled_while_an_agent_turn_runs(self):
        offer = self.offer()
        self.ready.consider(offer)
        self.finish_worker()
        self.reasons = ["agent"]
        self.ready.tick()
        self.assertEqual(self.preparer.limit(), self.ready.AGENT_RATE)
        self.assertEqual(self.ready.wait_reason(), "agent")
        self.reasons = []
        self.ready.tick()
        self.assertIsNone(self.preparer.limit())
        self.assertEqual(self.ready.wait_reason(), "")

    def test_nothing_downloads_on_its_own_when_automatic_checks_are_off(self):
        self.n.update_ui.checker().set_automatic(False)
        offer = self.offer()
        self.assertFalse(self.ready.consider(offer))
        self.assertIsNone(self.ready._job)
        self.assertTrue(self.ready.consider(offer, manual=True), "Check for Updates… still gets it")
        self.finish_worker()
        self.assertEqual(self.preparer.prepared, ["1.1.0"])

    def test_a_newer_release_supersedes_an_older_ones_retries(self):
        self.preparer.outcome = update_flow.PrepareError("download:offline", transient=True)
        self.ready.consider(self.offer("1.1.0"))
        self.finish_worker()
        self.assertIn("1.1.0", self.ready._failed)
        self.preparer.outcome = None
        self.ready.consider(self.offer("1.2.0"))
        self.finish_worker()
        self.assertEqual((list(self.ready._failed), list(self.ready._offers)), ([], ["1.2.0"]))
        self.assertEqual(self.preparer.cleaned[-1], ("older than", "1.2.0"), "never a newer version's folder")

    def test_a_newer_release_stops_an_older_ones_download_even_when_it_cannot_install_itself(self):
        import threading
        release_old = threading.Event()

        def slow(release, download, cancel=None, **kwargs):
            release_old.wait(5)
            raise update_flow.PrepareError("download:cancelled", transient=True)
        self.preparer.prepare = slow
        self.ready.consider(self.offer("1.1.0"))
        job = self.ready._job
        github = "https://github.com/someone/angelo/releases/download/v1.2.0/Angelo-1.2.0-macos-arm64.dmg"
        self.assertFalse(self.ready.consider(self.offer("1.2.0", url=github)))
        self.assertTrue(job["cancel"].is_set())
        self.assertIsNone(self.ready._job)
        release_old.set()
        job["thread"].join(5)
        self.ready.tick()
        self.assertEqual(list(self.ready._ready), [])

    def test_an_unexpected_error_is_tried_again_later(self):
        self.preparer.outcome = RuntimeError("a mount left behind")

        def prepare(*args, **kwargs):
            raise RuntimeError("boom")
        self.preparer.prepare = prepare
        self.ready.consider(self.offer())
        self.finish_worker()
        self.assertTrue(self.ready._failed["1.1.0"][1], "transient: retried, not a permanent download")

    def test_two_failed_installations_turn_the_version_into_a_download(self):
        state = update_state.load(self.folder)
        state.install_failed_version, state.install_failures = "1.1.0", 2
        update_state.save(self.folder, state)
        self.assertFalse(self.ready.consider(self.offer()))
        self.assertEqual(self.ready.automatic(self.offer()), "failed")


class RestartTests(ReadyCase):
    def setUp(self):
        super().setUp()
        self.offer_ = self.offer()
        self.ready.consider(self.offer_)
        self.finish_worker()
        self.prepared = self.ready._ready["1.1.0"]
        self.host = SimpleNamespace(closed=[], close=lambda how="done": self.host.closed.append(how), redraw=lambda: None)
        self.started, self.killed = [], []
        process = SimpleNamespace(kill=lambda: self.killed.append(True))
        start = mock.patch.object(self.restart.update_install, "start",
                                  lambda request: self.started.append(request) or process)
        requirement = mock.patch.object(self.restart, "_requirement", lambda ready: 'identifier "test"')
        requirement.start()
        self.addCleanup(requirement.stop)
        start.start()
        self.addCleanup(start.stop)
        others = mock.patch.object(self.restart, "_other_instances", lambda ready: [])
        others.start()
        self.addCleanup(others.stop)
        self.bpy = self.n.bpy
        self.n.card_host._queue.clear()

    def press(self):
        self.restart.begin(self.offer_, self.prepared, self.host)

    def run_timers(self):
        for function in list(self.bpy.app.timers.registered):
            if function.__module__ != "angelo_agent.update_ui":
                self.bpy.app.timers.registered.pop(function)
                again = function()
                if again is not None:
                    self.bpy.app.timers.registered[function] = again

    def quits(self):
        return [op for op in self.n.record.operators if op[0] == "wm.quit_blender"]

    def test_nothing_happens_while_work_runs(self):
        self.reasons = ["agent"]
        self.press()
        self.assertEqual((self.started, self.queued()), ([], []))

    def test_a_saved_file_hands_over_and_quits_once_the_installer_runs(self):
        self.bpy.data.filepath = "/work/castle.blend"
        self.press()
        request = self.started[0]
        self.assertEqual((request.family, request.version, request.open_file, request.pid),
                         ("macos", "1.1.0", "/work/castle.blend", os.getpid()))
        self.assertEqual(request.new, self.prepared.payload)
        self.assertEqual(self.ready.phase(self.offer_), "restarting")
        self.run_timers()
        self.assertEqual(self.quits(), [], "never before the installer says it runs")
        Path(self.prepared.work).mkdir(parents=True, exist_ok=True)
        (Path(self.prepared.work) / "helper.ready").write_text("")
        self.run_timers()
        self.assertEqual(len(self.quits()), 1)

    def test_an_installer_that_never_starts_leaves_angelo_running(self):
        self.press()
        self.assertEqual(self.started[0].requirement, 'identifier "test"', "re-checked at swap time")
        with mock.patch.object(self.restart.time, "monotonic", return_value=time.monotonic() + 60):
            self.run_timers()
        self.assertEqual(self.quits(), [])
        self.assertEqual(self.killed, [True], "a late installer never acts behind Angelo's back")
        self.assertEqual(self.ready.phase(self.offer_), "ready")
        self.assertIn("The update could not start", [c.title for c in self.queued("notice")])

    def test_unsaved_changes_ask_first_and_dont_save_restarts(self):
        self.bpy.data.is_dirty, self.bpy.data.filepath = True, "/work/castle.blend"
        self.press()
        self.assertEqual(self.started, [])
        prompt = self.shown("update_save")
        self.assertEqual(prompt.name, "castle.blend")
        prompt.act("dont_save", None, self.host)
        self.assertEqual(len(self.started), 1)

    def test_save_and_restart_saves_the_file_first(self):
        self.bpy.data.is_dirty, self.bpy.data.filepath = True, "/work/castle.blend"
        saves = []
        # As Blender does: the file reads as modified until the save's notifier is handled, after the call.
        self.bpy.ops.wm.save_mainfile = lambda *a, **k: saves.append(1) or {'FINISHED'}
        self.press()
        self.shown("update_save").act("save", None, self.host)
        self.assertEqual((len(saves), len(self.started)), (1, 1))

    def image(self, name, dirty=True):
        return SimpleNamespace(name=name, is_dirty=dirty)

    def save_images_and_file(self, order):
        def save_images(*args, **kwargs):
            order.append("images")
            for image in self.bpy.data.images:
                image.is_dirty = False   # Saved, or packed into the file.
            return {'FINISHED'}
        self.bpy.ops.image.save_all_modified = save_images
        self.bpy.ops.image.save_all_modified.poll = lambda: True
        self.bpy.ops.wm.save_mainfile = lambda *a, **k: order.append("file") or {'FINISHED'}

    def test_images_are_saved_before_the_file_that_packs_them(self):
        self.bpy.data.is_dirty, self.bpy.data.filepath = True, "/work/castle.blend"
        self.bpy.data.images = [self.image("Painted")]
        order = []
        self.save_images_and_file(order)
        self.press()
        self.shown("update_save").act("save", None, self.host)
        self.assertEqual(order, ["images", "file"])
        self.assertEqual(len(self.started), 1)

    def test_only_unsaved_images_still_save_the_file(self):
        self.bpy.data.filepath = "/work/castle.blend"   # The file itself reads as saved.
        self.bpy.data.images = [self.image("Generated")]
        order = []
        self.save_images_and_file(order)
        self.press()
        self.assertEqual(self.shown("update_save").image_count, 1)
        self.n.card_host._queue.clear()
        self.restart._after_prompt("save", self.offer_, self.prepared)
        self.assertEqual(order, ["images", "file"], "a packed image is in the file only once the file is saved")

    def test_an_image_that_cannot_be_saved_stops_before_the_file(self):
        self.bpy.data.is_dirty, self.bpy.data.filepath = True, "/work/castle.blend"
        self.bpy.data.images = [self.image("Read-only")]
        order = []
        self.save_images_and_file(order)
        self.bpy.ops.image.save_all_modified = lambda *a, **k: order.append("images") or {'CANCELLED'}
        self.bpy.ops.image.save_all_modified.poll = lambda: True
        self.press()
        self.shown("update_save").act("save", None, self.host)
        self.assertEqual((order, self.started), (["images"], []))

    def test_a_release_withdrawn_from_automatic_install_is_never_installed(self):
        withdrawn = type(self.offer_)(type(self.offer_.release)(**dict(self.offer_.release.__dict__,
                                                                         automatic_install=False)),
                                      self.offer_.download, False, False)
        self.restart.begin(withdrawn, self.prepared, self.host)
        self.assertEqual(self.started, [])
        self.assertFalse(self.ready.ready_for(withdrawn))

    def test_a_file_that_could_not_be_saved_stops_the_restart(self):
        self.bpy.data.is_dirty, self.bpy.data.filepath = True, "/work/castle.blend"
        for answer in ({'CANCELLED'}, RuntimeError("Cannot write to a read-only folder")):
            with self.subTest(answer=answer):
                self.n.card_host._queue.clear()

                def save(*args, **kwargs):
                    if isinstance(answer, Exception):
                        raise answer
                    return answer
                self.bpy.ops.wm.save_mainfile = save
                self.press()
                self.shown("update_save").act("save", None, self.host)
                self.assertEqual(self.started, [])
                self.assertIn("Angelo did not restart", [c.title for c in self.queued("notice")])

    def test_an_untitled_file_is_saved_through_the_file_browser_or_not_at_all(self):
        self.bpy.data.is_dirty = True
        self.press()
        self.shown("update_save").act("save", None, self.host)
        self.assertEqual([op[0] for op in self.n.record.operators][-1], "wm.save_as_mainfile")
        self.assertEqual(self.started, [])
        browser = self.n.bpy.context.window_manager.windows[0].screen.areas
        browser.append(SimpleNamespace(type='FILE_BROWSER'))
        self.run_timers()
        self.assertEqual(self.started, [], "waits while the browser is open")
        self.bpy.data.filepath = "/work/new.blend"   # Still reads as modified: the notifier comes later.
        saves = []
        self.bpy.ops.wm.save_mainfile = lambda *a, **k: saves.append(1) or {'FINISHED'}
        for handler in list(self.bpy.app.handlers.save_post):
            handler()
        self.run_timers()
        self.assertEqual(self.started[0].open_file, "/work/new.blend")
        self.assertEqual(saves, [], "saved once, by the File Browser")
        self.assertEqual(self.bpy.app.handlers.save_post, [])

    def test_closing_the_file_browser_without_saving_keeps_angelo_open(self):
        self.bpy.data.is_dirty = True
        self.press()
        self.shown("update_save").act("save", None, self.host)
        with mock.patch.object(self.restart.time, "monotonic", return_value=time.monotonic() + 5):
            self.run_timers()
        self.assertEqual((self.started, self.quits()), ([], []))

    def test_cancel_brings_the_update_card_back(self):
        self.bpy.data.is_dirty = True
        self.press()
        self.shown("update_save").act("cancel", None, self.host)
        self.assertEqual(self.started, [])
        self.assertEqual([c.kind for c in self.queued()], ["update"])

    def test_a_changed_stage_is_prepared_again_instead_of_installed(self):
        self.preparer.valid = False
        self.press()
        self.assertEqual(self.started, [])
        self.assertEqual(self.preparer.prepared, ["1.1.0", "1.1.0"])

    def test_another_running_copy_stops_the_restart(self):
        with mock.patch.object(self.restart, "_other_instances", lambda ready: [4242]):
            self.press()
        self.assertEqual(self.started, [])
        self.assertIn("Close the other Angelo windows first", [c.title for c in self.queued("notice")])


class NextLaunchTests(ReadyCase):
    def test_a_successful_install_is_cleaned_up_quietly(self):
        current = self.n.update_restart.app_version.current()
        self.preparer.result_list = [(current, {"status": "installed", "step": "swap"})]
        (self.preparer.root / current).mkdir(parents=True)
        with mock.patch.object(self.restart.update_flow, "remove_previous") as removed:
            self.restart.handle_results(self.preparer, str(self.folder))
        removed.assert_called_once()
        self.assertEqual(self.queued(), [])
        self.assertEqual(update_state.load(self.folder).install_failures, 0)

    def test_a_failed_install_is_told_once_and_counted(self):
        self.preparer.result_list = [("9.0.0", {"status": "failed", "step": "move_new"})]
        work = self.preparer.root / "9.0.0"
        work.mkdir(parents=True)
        (work / "result.txt").write_text("status=failed\n")
        self.restart.handle_results(self.preparer, str(self.folder))
        self.assertIn("Angelo 9.0 could not be installed", [c.title for c in self.queued("notice")])
        self.assertFalse((work / "result.txt").exists(), "told once")
        state = update_state.load(self.folder)
        self.assertEqual((state.install_failed_version, state.install_failures), ("9.0.0", 1))
        self.assertIn("9.0.0", self.preparer.cleaned[-1], "the prepared update stays for another try")


class ResultOrderTests(ReadyCase):
    def test_the_last_installers_result_counts_before_the_version_is_considered(self):
        state = update_state.load(self.folder)
        state.install_failed_version, state.install_failures = "1.1.0", 1
        update_state.save(self.folder, state)
        self.preparer.result_list = [("1.1.0", {"status": "failed", "step": "msiexec", "code": "1603"})]
        (self.preparer.root / "1.1.0").mkdir(parents=True)
        self.ready.register()   # A fresh launch: nothing handled yet.
        self.assertFalse(self.ready.consider(self.offer()), "the second failure turns it into a download")
        self.assertEqual(self.preparer.prepared, [])


class WorkGuardTests(unittest.TestCase):
    def test_each_kind_of_work_is_named_and_a_broken_owner_fails_closed(self):
        idle = SimpleNamespace(busy=False, pending={}, _native_preview_cards={})
        bpy = SimpleNamespace(app=SimpleNamespace(is_job_running=lambda kind: False),
                              data=SimpleNamespace(scenes=[]), context=SimpleNamespace(
                                  window_manager=SimpleNamespace(windows=[])))
        checks = {"_controllers": lambda: [idle], "_flow": lambda: False, "_generation": lambda: False,
                  "_job": lambda: False}
        with mock.patch.multiple(work_guard, **checks):
            self.assertEqual(work_guard.reasons(bpy), [])
            for attribute, value, code in (("busy", True, "agent"), ("pending", {"c": 1}, "agent"),
                                           ("_native_preview_cards", {"p": 1}, "preview")):
                busy = SimpleNamespace(**dict(vars(idle), **{attribute: value}))
                with mock.patch.object(work_guard, "_controllers", lambda: [busy]):
                    self.assertEqual(work_guard.reasons(bpy), [code])
            held = SimpleNamespace(**dict(vars(idle), busy=True, _held=True))   # Waits for its scene (R5-M-1).
            with mock.patch.object(work_guard, "_controllers", lambda: [held]):
                self.assertEqual(work_guard.reasons(bpy), ["scene", "agent"])
            rendering = SimpleNamespace(app=SimpleNamespace(is_job_running=lambda kind: kind == "RENDER"),
                                        data=bpy.data, context=bpy.context)
            self.assertEqual(work_guard.reasons(rendering), ["render"])

            def window(*names):
                return SimpleNamespace(modal_operators=[SimpleNamespace(bl_idname=n, idname=n) for n in names],
                                       screen=SimpleNamespace(areas=[], is_temporary=False))
            for names, expected in ((("angelo.card_host",), []), (("angelo.card_host", "TRANSFORM_OT_translate"),
                                                                      ["interaction"])):
                with self.subTest(names=names):
                    with_cards = SimpleNamespace(app=bpy.app, data=bpy.data, context=SimpleNamespace(
                        window_manager=SimpleNamespace(windows=[window(*names)])))
                    with mock.patch("angelo_agent.input_policy.active_interactions", lambda w: [
                            op.bl_idname for op in w.modal_operators]):
                        self.assertEqual(work_guard.reasons(with_cards), expected,
                                         "the card the person presses is not their work")

            def broken():
                raise RuntimeError("owner changed")
            with mock.patch.object(work_guard, "_flow", broken):
                self.assertEqual(work_guard.reasons(bpy), ["unknown"])
            with mock.patch.object(work_guard, "_controllers", broken):
                self.assertEqual(work_guard.reasons(bpy), ["unknown"])


if __name__ == "__main__":
    unittest.main()
