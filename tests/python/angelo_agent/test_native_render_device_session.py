# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every Cycles session starts on a decided device (render_device_session), and later starts need no listing.

Same fakes as ``test_native_render_device``. Covers the saved listing (applied at once, per build), the one
listing per launch, a refresh that finds the GPU gone, Cycles' ``--cycles-device``, every start mode of the
session gate (windowed, background with and without a saved listing, a render job's thread, previews, the
viewport, the listing child itself), the device a job really used and the person's card line. Cycles' add-on
calls the gate before it creates or resets a session (checked on its source).
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from angelo_agent import job_chat, job_manager, job_store, render_device  # noqa: E402
from angelo_agent import render_device_session as session  # noqa: E402
from test_native_render_device import APPLE, NONE_FOUND, NVIDIA, Child, Prefs, Preferences, scene  # noqa: E402


class SlowChild(Child):
    """A child still listing until ``wait`` (or ``finish``) lets it report."""

    def __init__(self, listed, finishes=True):
        super().__init__(listed, running=True)
        self.finishes = finishes

    def wait(self, timeout=None):
        if self.finishes:
            self.running = False
            return self.returncode
        raise subprocess.TimeoutExpired("angelo", timeout)


class Base(unittest.TestCase):
    """A fake app (``bpy``) and a fresh process's module state; the listing children come from ``self.children``."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        render_device.attach(self.folder)
        self.addCleanup(render_device.attach, None)
        render_device._rates.clear()
        self.timers, self.commands, self.children = [], [], []
        timers = SimpleNamespace(is_registered=lambda function: function in self.timers,
                                 register=lambda function, **_: self.timers.append(function),
                                 unregister=lambda function: self.timers.remove(function))
        self.app = SimpleNamespace(binary_path="/Applications/Angelo.app/Contents/MacOS/Angelo", timers=timers,
                                   version_string="5.2.2", build_hash=b"42d31e3d07a5", background=False,
                                   handlers=SimpleNamespace(load_factory_preferences_post=[]))
        self.bpy = SimpleNamespace(app=self.app, utils=SimpleNamespace(user_resource=lambda *a, **k: self.folder),
                                   context=SimpleNamespace(window_manager=SimpleNamespace(windows=[])))
        self.machine = {}   # What Cycles finds in this process (render_device.stale): {backend: devices}.
        cycles = SimpleNamespace(available_devices=lambda kind: list(self.machine.get(kind, ())))
        for patcher in (mock.patch.dict(sys.modules, {"bpy": self.bpy, "_cycles": cycles}),
                        mock.patch.object(render_device, "_popen", side_effect=self.launch)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.new_start()

    def launch(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if not self.children:
            self.fail("listed with no child prepared")
        return self.children.pop(0)

    def saved(self):
        return json.loads((Path(self.folder) / render_device.FILE).read_text())

    def new_start(self, prefs=None):
        """The app again: a fresh process's module state, the same state folder, these (unsaved) preferences."""
        render_device._seen = render_device._available = render_device._listing = None
        render_device._tried = False
        render_device._status = {"state": "not_set_up"}
        session._count, session._used = 0, None
        self.prefs = prefs or Prefs()
        self.preferences = Preferences(self.prefs)
        self.bpy.context.preferences = self.preferences
        self.timers.clear()
        self.commands.clear()

    def first_listing(self, machine):
        self.children.append(Child(machine))
        render_device.setup(self.preferences)
        render_device._poll(self.preferences)


class SavedListingTests(Base):
    def test_a_later_start_is_on_the_gpu_at_once_without_listing(self):
        self.first_listing(NVIDIA)
        self.assertEqual(self.saved()["listed"]["devices"]["OPTIX"][0][2], "OPTIX_Tesla T4_0000:00:04")
        self.new_start()
        result = render_device.setup(self.preferences, listing=False)
        self.assertEqual((result["state"], result["backend"], result["from"]), ("set_up", "OPTIX", "saved"))
        self.assertEqual(self.commands, [])
        self.assertEqual(render_device.facts(scene(), self.preferences)["device"], "GPU (OptiX: Tesla T4)")
        self.assertFalse(self.preferences.is_dirty)

    def test_the_same_app_started_by_another_path_is_the_same_build(self):
        self.first_listing(APPLE)
        self.new_start()
        cwd = Path(self.app.binary_path).parents[2]
        with mock.patch("os.getcwd", return_value=str(cwd)):
            self.app.binary_path = "Contents/MacOS/Angelo"   # Started from a shell inside the app's folder.
            self.assertEqual(render_device.setup(self.preferences, listing=False)["from"], "saved")

    def test_another_build_lists_again(self):
        self.first_listing(APPLE)
        self.new_start()
        self.app.build_hash = b"0123456789ab"
        self.assertEqual(render_device.setup(self.preferences, listing=False)["state"], "not_set_up")
        self.assertEqual(self.prefs.compute_device_type, "NONE")

    def test_at_most_one_listing_per_launch(self):
        self.children.append(Child(code=-11, text=""))
        render_device.setup(self.preferences)
        render_device._poll(self.preferences)
        self.assertEqual(render_device.status()["state"], "failed")
        render_device.setup(self.preferences)   # A render's facts, later: no second child.
        self.assertEqual(len(self.commands), 1)

    def test_a_failed_refresh_keeps_the_saved_setup(self):
        self.first_listing(APPLE)
        self.new_start()
        self.children.append(Child(code=-11, text=""))
        render_device.setup(self.preferences)   # Saved setup applied, then the launch's refresh listing.
        render_device._poll(self.preferences)
        self.assertEqual((render_device.status()["state"], self.prefs.compute_device_type), ("set_up", "METAL"))

    def test_a_gpu_gone_is_said_and_a_gpu_back_is_used(self):
        self.first_listing(NVIDIA)
        self.new_start()
        self.children.append(Child(NONE_FOUND))
        render_device.setup(self.preferences)
        self.assertEqual(self.prefs.compute_device_type, "OPTIX")   # Saved, at once.
        render_device._poll(self.preferences)
        # Never set to None (a None someone sets stays theirs): the backend stays, Cycles renders on the CPU, and
        # the facts and the card say why.
        self.assertEqual((render_device.status()["state"], self.prefs.compute_device_type), ("no_gpu", "OPTIX"))
        self.assertEqual(render_device.shown(scene(), self.preferences), {"gpu": False, "reason": "no_gpu"})
        self.new_start(self.prefs)   # Saved preferences kept OptiX: still Angelo's, refreshed, never the person's.
        self.children.append(Child(NVIDIA))   # The GPU is back.
        render_device.setup(self.preferences)
        render_device._poll(self.preferences)
        self.assertEqual((render_device.status()["state"], self.prefs.compute_device_type), ("set_up", "OPTIX"))

    def test_the_command_line_device_is_never_overridden_or_listed(self):
        with mock.patch.object(sys, "argv", ["angelo", "-b", "f.blend", "-f", "1", "--", "--cycles-device", "CPU"]):
            self.assertEqual(render_device.setup(self.preferences)["state"], "override")
            self.assertEqual(render_device.shown(scene(), self.preferences), {"gpu": False, "reason": "override"})
        self.assertEqual((self.commands, self.prefs.compute_device_type), ([], "NONE"))

    def test_the_listing_child_is_marked_and_a_change_is_announced(self):
        heard = []
        render_device._listeners.append(lambda: heard.append(True))
        self.addCleanup(render_device._listeners.clear)
        self.first_listing(APPLE)
        self.assertEqual(self.commands[0][1]["env"][render_device.CHILD], "1")
        self.assertEqual(heard, [True])

    def test_a_refresh_that_finds_the_same_devices_changes_nothing(self):
        self.first_listing(APPLE)
        self.new_start()
        heard = []
        render_device._listeners.append(lambda: heard.append(True))
        self.addCleanup(render_device._listeners.clear)
        self.children.append(Child(APPLE))
        render_device.setup(self.preferences)   # Saved setup applied (a change), then the launch's refresh.
        render_device._poll(self.preferences)
        self.assertEqual(heard, [True])   # Only the saved setup's own change; the refresh found the same devices.
        self.assertEqual(self.saved()["applied"]["type"], "METAL")

    def test_shown_says_why_the_cpu(self):
        self.first_listing(APPLE)
        self.assertEqual(render_device.shown(scene(), self.preferences),
                         {"gpu": True, "backend": "METAL", "names": ["Apple M4 Pro (GPU - 20 cores)"], "cpu": False})
        self.assertEqual(render_device.shown(scene("CPU"), self.preferences), {"gpu": False, "reason": "scene"})
        self.prefs.compute_device_type = "NONE"
        self.assertEqual(render_device.shown(scene(), self.preferences), {"gpu": False, "reason": "none"})


class SessionGateTests(Base):
    def test_windowed_first_launch_lists_at_once_and_a_later_one_refreshes_a_second_after(self):
        self.children.append(Child(running=True))
        session.early()
        self.assertEqual((render_device.status()["state"], len(self.commands)), ("listing", 1))
        self.new_start()
        render_device._write(dict(render_device._read(), listed={"build": render_device._build(), "at": time.time(),
                                                                 "devices": APPLE}))
        session.early()
        self.assertEqual((self.prefs.compute_device_type, self.commands), ("METAL", []))   # Before any window.
        self.assertIn(session._startup, self.timers)

    def test_background_run_with_a_saved_listing_never_lists(self):
        self.machine = APPLE
        self.first_listing(APPLE)
        self.new_start()
        self.app.background = True
        session.early()
        session.before_session(scene())
        self.assertEqual((self.prefs.compute_device_type, self.commands), ("METAL", []))
        self.assertEqual(session.used_since(0)[0]["device"], "GPU (Metal: Apple M4 Pro (GPU - 20 cores))")

    def test_background_render_with_nothing_saved_waits_for_the_listing(self):
        self.app.background = True
        session.early()
        self.assertEqual(self.commands, [])   # A background run that never renders never lists.
        self.children.append(SlowChild(NVIDIA))
        session.before_session(scene())
        self.assertEqual(self.prefs.compute_device_type, "OPTIX")
        self.assertEqual(session.used_since(0)[1]["backend"], "OPTIX")

    def test_background_listing_over_the_wait_is_stopped_and_the_render_says_cpu(self):
        self.app.background = True
        self.enterContext(mock.patch.object(session, "WAIT", 0.3))
        child = SlowChild(NVIDIA, finishes=False)
        self.children.append(child)
        session.before_session(scene())
        self.assertTrue(child.killed)
        self.assertEqual(render_device.status()["state"], "failed")
        self.assertEqual(session.used_since(0)[1], {"gpu": False, "reason": "failed"})

    def test_a_render_job_thread_waits_for_the_main_thread_and_never_writes(self):
        self.children.append(Child(APPLE, running=True))
        session.early()   # First launch: listing.
        child = render_device._listing["process"]
        done = []
        writes = mock.patch.object(render_device, "_apply", side_effect=AssertionError("written off the main thread"))
        with writes:
            job = threading.Thread(target=lambda: (session.before_session(scene()), done.append(True)))
            job.start()
            time.sleep(0.2)
            self.assertEqual(done, [])   # Still waiting.
        child.running = False
        real_apply, during = render_device._apply, []

        def apply(*args, **kwargs):   # A waiting thread is released only once the result is applied.
            during.append(render_device._listing is not None)
            return real_apply(*args, **kwargs)
        with mock.patch.object(render_device, "_apply", side_effect=apply):
            render_device._poll(self.preferences)   # The app's timer, on the main thread.
        self.assertEqual(during, [True])
        job.join(5)
        self.assertEqual(done, [True])
        self.assertEqual(session.used_since(0)[1]["backend"], "METAL")

    def test_previews_and_viewports_never_wait_or_count(self):
        self.children.append(Child(running=True))
        session.early()
        session.before_session(scene(), preview=True)
        session.before_session(scene(), viewport=True)
        self.assertEqual((session.count(), render_device.status()["state"]), (0, "listing"))

    def test_the_listing_child_does_nothing(self):
        with mock.patch.dict("os.environ", {render_device.CHILD: "1"}):
            session.early()
            session.before_session(scene())
        self.assertEqual((self.commands, session.count()), ([], 0))

    def test_load_factory_preferences_decides_again(self):
        self.first_listing(APPLE)
        session.register()
        self.prefs._type = None
        self.prefs.devices.clear()
        for handler in self.app.handlers.load_factory_preferences_post:
            handler(None)
        self.assertIn(session._startup, self.timers)
        session._startup()
        self.assertEqual(self.prefs.compute_device_type, "METAL")
        session.unregister()
        self.assertIsNone(render_device._path)


class ReviewRegressionTests(Base):
    """The independent break-it review's reproductions (2026-10-21), each now the other way round."""

    def test_a_refresh_never_holds_a_render_job(self):
        self.first_listing(APPLE)
        self.new_start()
        self.children.append(Child(APPLE, running=True))
        session.early()
        session._startup()   # The launch's refresh is listing; the device is already decided.
        self.assertIsNotNone(render_device._listing)
        started = time.monotonic()
        job = threading.Thread(target=session.before_session, args=(scene(),))
        job.start()
        job.join(5)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_a_refresh_does_not_write_preferences_while_a_render_runs(self):
        self.first_listing(APPLE)
        self.new_start()
        child = Child(NVIDIA)
        self.children.append(child)
        render_device.setup(self.preferences)
        self.app.is_job_running = lambda kind: kind == "RENDER"
        self.assertEqual(render_device._poll(self.preferences), 0.5)   # Held: the render reads these preferences.
        self.assertEqual(self.prefs.compute_device_type, "METAL")
        self.app.is_job_running = lambda kind: False
        self.assertIsNone(render_device._poll(self.preferences))
        self.assertEqual(self.prefs.compute_device_type, "OPTIX")

    def test_writers_never_share_a_temporary_file(self):
        written = []
        real = render_device.os.replace

        def replace(source, target):   # Another process writes in between.
            written.append(source)
            if len(written) == 1:
                render_device._write({"applied": {"type": "OPTIX", "ids": []}})
            real(source, target)
        with mock.patch.object(render_device.os, "replace", side_effect=replace):
            render_device._write({"listed": {"build": "b", "at": 1, "devices": {"METAL": [["a" * 400, "METAL", "x"]]}}})
        self.assertNotEqual(written[0], written[1])
        self.assertEqual(render_device._read()["version"], 1)   # Valid JSON whichever write landed last.
        self.assertEqual([name for name in os.listdir(self.folder) if name.endswith(".tmp")], [])

    def test_a_failed_listing_is_remembered_and_not_waited_for_again(self):
        self.app.background = True
        self.children.append(Child(code=-11, text=""))
        session.before_session(scene())
        self.assertEqual(render_device.status()["state"], "failed")
        self.new_start()
        session.before_session(scene())   # Another background render: no child, no wait, the reason kept.
        self.assertEqual((self.commands, render_device.status()["state"]), ([], "failed"))
        self.assertEqual(session.used_since(0)[1], {"gpu": False, "reason": "failed"})

    def test_a_background_run_lists_again_when_its_saved_gpu_is_gone(self):
        self.machine = {}   # This process's Cycles finds no Metal device any more.
        self.first_listing(APPLE)
        self.new_start()
        self.app.background = True
        self.children.append(SlowChild(NONE_FOUND))
        session.before_session(scene())
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(session.used_since(0)[1], {"gpu": False, "reason": "no_gpu"})

    def test_a_cancelled_render_stops_waiting(self):
        self.children.append(Child(APPLE, running=True))
        session.early()
        started = time.monotonic()
        job = threading.Thread(target=session.before_session, args=(scene(),), kwargs={"stop": lambda: True})
        job.start()
        job.join(5)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_cycles_abbreviated_option_counts(self):
        with mock.patch.object(sys, "argv", ["angelo", "-b", "--", "--cycles-dev", "CPU"]):
            self.assertEqual(render_device.setup(self.preferences)["state"], "override")

    def test_the_factory_reset_handler_survives_file_loads_and_an_undecided_none_is_a_warning(self):
        self.assertTrue(hasattr(session.reset, "_bpy_persistent"))
        self.assertEqual(render_device.shown(scene(), self.preferences), {"gpu": False, "reason": "not_set_up"})

    def test_only_cycles_jobs_of_the_same_scene_take_a_sessions_device(self):
        self.first_listing(NVIDIA)
        export = job_store.new_record("j2", "s", "c", {"kind": "EXPORT_USD"}, now=1.0)
        other = job_store.new_record("j3", "s", "c", {"kind": "RENDER_STILL"}, now=1.0)
        other["detail"]["engine"] = "CYCLES"
        rendered = SimpleNamespace(render=SimpleNamespace(engine="CYCLES"), cycles=SimpleNamespace(device="GPU"),
                                   session_uid=7)
        session.before_session(rendered)
        job_manager._device_used(export, SimpleNamespace(device_mark=0, scene_uid=7))
        job_manager._device_used(other, SimpleNamespace(device_mark=0, scene_uid=8))
        self.assertNotIn("device", export["detail"])
        self.assertNotIn("device", other["detail"])


class ReviewRoundTwoTests(Base):
    """The second break-it pass (2026-10-21), each now the other way round."""

    def test_a_background_run_checks_a_setup_kept_in_saved_preferences(self):
        self.machine = {}
        self.first_listing(NVIDIA)
        self.new_start(self.prefs)   # The saved preferences kept Angelo's OptiX setup.
        self.app.background = True
        self.children.append(SlowChild(NONE_FOUND))
        session.before_session(scene())
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(session.used_since(0)[1], {"gpu": False, "reason": "no_gpu"})

    def test_a_deferred_refresh_still_corrects_the_facts(self):
        self.first_listing(NVIDIA)
        self.new_start()
        self.children.append(Child(NONE_FOUND))
        render_device.setup(self.preferences)
        self.app.is_job_running = lambda kind: kind == "RENDER"
        self.assertEqual(render_device._poll(self.preferences), 0.5)
        self.assertEqual(render_device.facts(scene(), self.preferences)["device"], "CPU")

    def test_a_render_begun_before_the_refresh_reported_is_corrected(self):
        self.first_listing(NVIDIA)
        self.new_start()
        self.app.is_job_running = lambda kind: kind == "RENDER"
        self.children.append(Child(NONE_FOUND))
        session.early()
        session._startup()
        record = job_store.new_record("j1", "s", "c", {"kind": "RENDER_STILL"}, now=1.0)
        record["detail"]["engine"] = "CYCLES"
        runner = SimpleNamespace(device_mark=0, scene_uid=None)
        job = threading.Thread(target=session.before_session, args=(scene(),))
        job.start()
        job.join(5)
        render_device._poll(self.preferences)   # The refresh reports (its writes wait for the render).
        job_manager._device_used(record, runner)
        self.assertEqual((record["detail"]["device"], record["detail"]["device_shown"]),
                         ("CPU", {"gpu": False, "reason": "no_gpu"}))

    def test_a_cancelled_background_listing_is_not_remembered(self):
        self.app.background = True
        self.children.append(SlowChild(NVIDIA, finishes=False))
        session.before_session(scene(), stop=lambda: True)
        self.assertIsNone(render_device._read().get("failed"))

    def test_a_slow_or_cancelled_background_listing_is_not_remembered(self):
        self.app.background = True
        self.enterContext(mock.patch.object(session, "WAIT", 0.3))
        self.children.append(SlowChild(NVIDIA, finishes=False))
        session.before_session(scene())
        self.assertEqual(render_device._read()["failed"]["slow"], 1)   # One slow run: not yet a failure.
        self.new_start()
        self.children.append(SlowChild(NVIDIA))
        session.before_session(scene())   # The next run lists again, and uses the GPU.
        self.assertEqual(session.used_since(0)[1]["backend"], "OPTIX")

    def test_a_listing_too_slow_twice_in_a_row_is_remembered(self):
        self.app.background = True
        self.enterContext(mock.patch.object(session, "WAIT", 0.3))
        for _ in range(2):
            self.new_start()
            self.children.append(SlowChild(NVIDIA, finishes=False))
            session.before_session(scene())
        self.new_start()
        session.before_session(scene())   # The third run: no child, no wait.
        self.assertEqual((self.commands, render_device.status()["state"]), ([], "failed"))

    def test_a_saved_no_gpu_listing_is_listed_again_after_a_day(self):
        self.first_listing(NONE_FOUND)
        saved = render_device._read()
        saved["listed"]["at"] -= render_device.NO_GPU_DAYS * 86400 + 1
        render_device._write(saved)
        self.new_start()
        self.assertEqual(render_device.setup(self.preferences, listing=False)["state"], "not_set_up")

    def test_the_facts_module_imports_first(self):
        import subprocess as run
        code = ("import sys; sys.path.insert(0, %r); import types; sys.modules['bpy'] = types.SimpleNamespace();"
                "from angelo_agent import render_device_facts; from angelo_agent import render_device;"
                "assert render_device.facts is render_device_facts.facts" % str(ROOT / "scripts/addons_core"))
        self.assertEqual(run.run([sys.executable, "-c", code], capture_output=True).returncode, 0)


class JobDeviceTests(Base):
    def test_a_job_records_the_device_its_session_used_and_the_card_says_it(self):
        record = job_store.new_record("j1", "s", "c", {"kind": "RENDER_STILL"}, now=1.0)
        record["detail"].update(engine="CYCLES", device="CPU", device_why="the scene asks for the GPU; Angelo is still listing")
        runner = SimpleNamespace(device_mark=session.count())
        self.first_listing(NVIDIA)
        session.before_session(scene())
        job_manager._device_used(record, runner)
        self.assertEqual(record["detail"]["device"], "GPU (OptiX: Tesla T4)")
        self.assertNotIn("device_why", record["detail"])
        record.update(status="running", progress=0.5)
        self.assertNotIn("device_shown", job_store.context_entry(record))   # The model reads facts, not the card.
        self.assertEqual(job_chat.device_text(record)[0], "On the GPU: Tesla T4 (OptiX)")
        record["detail"]["device_shown"] = {"gpu": False, "reason": "no_gpu"}
        text, colour = job_chat.device_text(record)
        self.assertEqual(text, "On the CPU: no GPU that Cycles supports was found")
        self.assertEqual(colour, job_chat.STATE_COLORS["stopping"])
        record["detail"]["device_shown"] = {"gpu": False, "reason": "not_enabled", "backend": "CUDA"}
        self.assertEqual(job_chat.device_text(record)[0], "On the CPU: no CUDA device is enabled in Preferences")
        record["detail"]["device_shown"] = {"gpu": False, "reason": "scene"}
        self.assertEqual(job_chat.device_text(record)[1], job_chat.MUTED)   # The person's own choice: no warning.

    def test_an_earlier_session_is_not_this_jobs(self):
        self.first_listing(APPLE)
        session.before_session(scene())
        self.assertIsNone(session.used_since(session.count()))
        self.assertIsNone(session.used_since(None))


class StandalonePackageTests(unittest.TestCase):
    """Cycles imports the setup, never the agent package (start-up cost and import order; 2026-10-22: a native
    suite's checkout-first import met the bundled agent that Cycles had loaded)."""

    def test_importing_the_gate_leaves_the_agent_package_unimported(self):
        import subprocess as run
        code = ("import sys, types; sys.path.insert(0, %r); sys.modules['bpy'] = types.SimpleNamespace();"
                "from angelo_render_device import session; session._state_override();"
                "assert not [m for m in sys.modules if m.split('.')[0] == 'angelo_agent'], sorted(sys.modules)"
                % str(ROOT / "scripts/modules"))
        done = run.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])

    def test_the_agent_names_are_the_same_modules(self):
        import angelo_render_device.device
        import angelo_render_device.facts
        from angelo_agent import render_device_facts, state_paths
        self.assertIs(render_device, angelo_render_device.device)
        self.assertIs(session, angelo_render_device.session)
        self.assertIs(render_device_facts, angelo_render_device.facts)
        self.assertEqual(session.STATE_FOLDER, state_paths.STATE_FOLDER)

    def test_the_state_folder_override_is_read_as_the_agent_reads_it(self):
        from angelo_agent import product_env
        for variable in product_env.names("STATE_DIR"):   # The current name and each deprecated spelling.
            with mock.patch.dict(os.environ, {name: "" for name in product_env.names("STATE_DIR")}):
                os.environ[variable] = "/state/" + variable
                self.assertEqual(session._state_override(), "/state/" + variable)


class CyclesHookTests(unittest.TestCase):
    """Cycles' add-on decides the device before every session it makes (source-level: bpy is not real here)."""

    def test_update_view_update_and_register_call_the_gate_first(self):
        tree = ast.parse((ROOT / "intern/cycles/blender/addon/__init__.py").read_text(encoding="utf-8"))
        functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

        def calls(function):
            return [ast.unparse(node) for node in ast.walk(functions[function]) if isinstance(node, ast.Call)]
        update = calls("update")
        self.assertTrue(update[0].startswith("_angelo_device('before_session'"))   # Before create and reset.
        view = calls("view_update")
        gate = next(index for index, text in enumerate(view) if text.startswith("_angelo_device('before_session'"))
        self.assertLess(gate, next(index for index, text in enumerate(view) if text.startswith("engine.create")))
        self.assertIn("_angelo_device('register')", calls("register"))
        self.assertIn("_angelo_device('unregister', load=False)", calls("unregister"))
        # A background start imports none of Angelo's add-on at register: native suites put the checkout first on
        # sys.path afterwards, and a run that never renders pays nothing (test_blender_review_light_rig, 2026-10-22).
        register = next(node for node in ast.walk(functions["register"]) if isinstance(node, ast.If)
                        and "_angelo_device('register')" in ast.unparse(node))
        self.assertEqual(ast.unparse(register.test), "not bpy.app.background")


if __name__ == "__main__":
    unittest.main()
