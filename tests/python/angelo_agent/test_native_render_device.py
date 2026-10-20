# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cycles renders on the GPU by default, and the agent is told the device (render_device).

Fakes Cycles' add-on preferences (``is_property_set`` as Blender reports it) and the listing child: ``setup``
never lists in the app, it starts the child and returns; ``_poll`` applies what the child printed. Covers every
backend, no GPU, a child that crashed or hung, the person's own choice (None included), Angelo's kept setup, the
facts a scene reports and the duration estimate.
"""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import job_store, render_device  # noqa: E402

CPU = ("Apple M4 Pro", "CPU", "CPU")
NVIDIA = {"CUDA": [("Tesla T4", "CUDA", "CUDA_Tesla T4_0000:00:04"), ("Intel Xeon", "CPU", "CPU")],
          "OPTIX": [("Tesla T4", "OPTIX", "OPTIX_Tesla T4_0000:00:04"), ("Intel Xeon", "CPU", "CPU")],
          "HIP": [("Intel Xeon", "CPU", "CPU")], "ONEAPI": [("Intel Xeon", "CPU", "CPU")]}
APPLE = {"METAL": [("Apple M4 Pro (GPU - 20 cores)", "METAL", "METAL_Apple M4 Pro"), CPU]}
AMD = {"HIP": [("AMD Radeon RX 7900 XTX", "HIP", "HIP_AMD Radeon RX 7900 XTX_0000:03:00"), CPU], "CUDA": [CPU]}
INTEL = {"ONEAPI": [("Intel Arc A770", "ONEAPI", "ONEAPI_Intel Arc A770_0000:03:00"), CPU]}
NONE_FOUND = {"CUDA": [CPU], "OPTIX": [CPU]}


class Device(SimpleNamespace):
    pass


class Devices(list):
    def add(self):
        entry = Device(id="", name="", type="CUDA", use=True)
        self.append(entry)
        return entry


class Prefs:
    def __init__(self, kind=None, entries=()):
        self._type, self.devices = kind, Devices(entries)

    @property
    def compute_device_type(self):
        return self._type or "NONE"

    @compute_device_type.setter
    def compute_device_type(self, value):
        self._type = value

    def is_property_set(self, name):
        return name == "compute_device_type" and self._type is not None


class Preferences:
    def __init__(self, prefs):
        self.is_dirty, self.prefs = False, prefs
        self.addons = {"cycles": SimpleNamespace(preferences=prefs)} if prefs is not None else {}
        self.bl_rna = SimpleNamespace(properties={"is_dirty": SimpleNamespace(is_readonly=False)})


class Child:
    """The listing child: ``poll`` says whether it exited, ``stdout`` holds what it printed."""

    def __init__(self, listed=None, code=0, running=False, text=None):
        self.stdout = io.StringIO(text if text is not None else
                                  ("noise\n" + render_device.MARK + json.dumps(listed) + "\n") if listed is not None else "")
        self.returncode, self.running, self.killed = code, running, False

    def poll(self):
        return None if self.running else self.returncode

    def kill(self):
        self.killed, self.running = True, False

    def wait(self, timeout=None):
        return self.returncode


def scene(device="GPU", engine="CYCLES"):
    return SimpleNamespace(render=SimpleNamespace(engine=engine), cycles=SimpleNamespace(device=device))


class RenderDeviceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        render_device.attach(self.folder)
        self.addCleanup(render_device.attach, None)
        render_device._seen = render_device._available = render_device._listing = None
        render_device._status = {"state": "not_set_up"}
        render_device._rates.clear()
        self.timers, self.commands = [], []
        timers = SimpleNamespace(is_registered=lambda function: function in self.timers,
                                 register=lambda function, **_: self.timers.append(function))
        fake = SimpleNamespace(app=SimpleNamespace(binary_path="/Applications/Angelo.app/Contents/MacOS/Angelo",
                                                   timers=timers))
        patcher = mock.patch.dict(sys.modules, {"bpy": fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def saved(self):
        return json.loads((Path(self.folder) / render_device.FILE).read_text())

    def run_setup(self, prefs, child):
        preferences = Preferences(prefs)

        def launch(command, **_):
            self.commands.append(command)
            return child
        result = render_device.setup(preferences, launch=launch)
        return preferences, result

    def listed(self, machine, kind=None, entries=()):
        """A fresh setup whose child lists ``machine``: started, then applied by the poll."""
        self.prefs = Prefs(kind, entries)
        preferences, started = self.run_setup(self.prefs, Child(machine))
        self.assertEqual(started["state"], "listing")
        self.assertIsNone(render_device._poll(preferences))
        self.preferences = preferences
        return render_device.status()

    # Selection, never blocking -------------------------------------------------------------------------------------

    def test_setup_only_starts_a_background_listing_of_this_app(self):
        preferences, result = self.run_setup(Prefs(), Child(running=True))
        self.assertEqual(result["state"], "listing")
        command = self.commands[0]
        self.assertEqual(command[:4], ["/Applications/Angelo.app/Contents/MacOS/Angelo", "--background",
                                       "--factory-startup", "--disable-autoexec"])
        self.assertIn("available_devices", command[-1])
        self.assertEqual(self.timers, [render_device._poll])
        self.assertEqual(render_device._poll(preferences), 0.1)   # Still listing: polled again, nothing applied.
        self.assertEqual(preferences.prefs.compute_device_type, "NONE")

    def test_each_backend_is_chosen_on_its_machine_and_optix_before_cuda(self):
        for machine, expected, name in ((NVIDIA, "OPTIX", "Tesla T4"), (APPLE, "METAL", "Apple M4 Pro (GPU - 20 cores)"),
                                        (AMD, "HIP", "AMD Radeon RX 7900 XTX"), (INTEL, "ONEAPI", "Intel Arc A770")):
            with self.subTest(expected=expected):
                self.setUp()
                self.assertEqual(self.listed(machine)["state"], "set_up")
                self.assertEqual(self.prefs.compute_device_type, expected)
                self.assertEqual(self.saved()["applied"]["type"], expected)
                self.assertFalse(self.preferences.is_dirty)   # Blender's preferences are never saved for Angelo.
                cpu = [entry for entry in self.prefs.devices if entry.type == "CPU"]
                self.assertEqual([entry.use for entry in cpu], [False])   # As Cycles adds it: listed, not used.
                self.assertEqual(render_device.facts(scene(), self.preferences)["device"],
                                 render_device.gpu_text(expected, [name]))

    def test_no_gpu_renders_on_the_cpu_and_says_why(self):
        self.assertEqual(self.listed(NONE_FOUND)["state"], "no_gpu")
        self.assertEqual(self.prefs.compute_device_type, "NONE")
        found = render_device.facts(scene(), self.preferences)
        self.assertEqual(found["device"], "CPU")
        self.assertIn("No GPU that Cycles supports was found", found["device_why"])

    def test_a_decided_setup_is_not_listed_again_and_a_reset_reuses_the_listing(self):
        self.listed(APPLE)
        render_device.setup(self.preferences, launch=lambda *a, **k: self.fail("listed again"))
        self.prefs.devices.clear()   # Load Factory Preferences: Blender's defaults again.
        self.prefs._type = None
        self.assertEqual(render_device.setup(self.preferences, launch=lambda *a, **k: self.fail("listed again"))["state"],
                         "set_up")
        self.assertEqual(self.prefs.compute_device_type, "METAL")

    # Failures ---------------------------------------------------------------------------------------------------

    def test_a_crashed_listing_child_falls_back_to_the_cpu_with_a_note(self):
        preferences, _ = self.run_setup(Prefs(), Child(code=-11, text=""))
        render_device._poll(preferences)
        result = render_device.status()
        self.assertEqual(result["state"], "failed")
        self.assertIn("ended with code -11", result["note"])
        self.assertEqual(preferences.prefs.compute_device_type, "NONE")
        self.assertIn("Listing GPU devices failed", render_device.facts(scene(), preferences)["device_why"])

    def test_a_hung_listing_child_is_stopped(self):
        child = Child(running=True)
        preferences, _ = self.run_setup(Prefs(), child)
        with mock.patch.object(render_device, "_clock", return_value=render_device._listing["started"] + 121):
            self.assertIsNone(render_device._poll(preferences))
        self.assertTrue(child.killed)
        self.assertIn("took over 120 s", render_device.status()["note"])

    def test_without_cycles_nothing_happens(self):
        self.assertEqual(render_device.setup(Preferences(None))["state"], "unavailable")
        self.assertIsNone(render_device.facts(scene(), Preferences(None)))
        self.assertEqual(render_device.describe(scene(), {"engine": "CYCLES"}, Preferences(None)), {"engine": "CYCLES"})

    # The person's choice ------------------------------------------------------------------------------------------

    def test_a_backend_someone_set_is_left_alone_and_never_listed(self):
        for kind in ("CUDA", "NONE"):   # None chosen in Preferences > System is a choice too.
            with self.subTest(kind=kind):
                self.setUp()
                entries = [Device(id=NVIDIA["CUDA"][0][2], name="Tesla T4", type="CUDA", use=True)]
                preferences, result = self.run_setup(Prefs(kind, entries), Child(NVIDIA))
                self.assertEqual((result["state"], result["backend"]), ("person", kind))
                self.assertEqual(self.commands, [])
                self.assertEqual(preferences.prefs.compute_device_type, kind)

    def test_blenders_untouched_default_in_a_stock_profile_gets_the_gpu(self):
        entries = [Device(id=NVIDIA["OPTIX"][0][2], name="Tesla T4", type="OPTIX", use=True)]   # Listed, never chosen.
        self.assertEqual(self.listed(NVIDIA, None, entries)["state"], "set_up")
        self.assertEqual(self.prefs.compute_device_type, "OPTIX")
        self.assertEqual(len([entry for entry in self.prefs.devices if entry.type == "OPTIX"]), 1)

    def test_a_change_while_listing_wins(self):
        self.prefs = Prefs()
        preferences, _ = self.run_setup(self.prefs, Child(NVIDIA))
        self.prefs.compute_device_type = "CUDA"   # The person, before the child reported.
        render_device._poll(preferences)
        self.assertEqual(self.prefs.compute_device_type, "CUDA")
        self.assertEqual(render_device.status()["state"], "person")

    def test_none_chosen_after_angelo_set_a_gpu_up_is_respected_at_the_next_start(self):
        self.listed(NVIDIA)
        self.prefs.compute_device_type = "NONE"   # The person, in Preferences > System; saved at exit.
        render_device._seen = render_device._available = None
        preferences, result = self.run_setup(self.prefs, Child(NVIDIA))
        self.assertEqual(result["state"], "person")
        self.assertIn("is None", render_device.facts(scene(), preferences)["device_why"])

    def test_angelos_setup_kept_in_saved_preferences_stays_angelos(self):
        self.listed(NVIDIA)
        render_device._seen = render_device._available = None   # A new start whose saved preferences kept it.
        preferences, result = self.run_setup(self.prefs, Child(NVIDIA))
        self.assertEqual((result["state"], result["backend"]), ("set_up", "OPTIX"))
        render_device._poll(preferences)   # Its listing only refreshes which devices exist.
        self.assertEqual((render_device.status()["state"], self.prefs.compute_device_type), ("set_up", "OPTIX"))

    # Facts and estimates ---------------------------------------------------------------------------------------

    def test_facts_name_the_device_for_cycles_only(self):
        self.listed(NVIDIA)
        self.assertIsNone(render_device.facts(scene(engine="BLENDER_EEVEE"), self.preferences))
        cpu = render_device.facts(scene("CPU"), self.preferences)
        self.assertEqual(cpu["device"], "CPU")
        self.assertEqual(cpu["gpu_available"], "GPU (OptiX: Tesla T4)")
        next(entry for entry in self.prefs.devices if entry.type == "CPU").use = True   # The CPU too, as allowed.
        self.assertEqual(render_device.facts(scene(), self.preferences)["device"], "GPU (OptiX: Tesla T4) + CPU")
        with mock.patch.object(sys, "argv", ["blender", "--cycles-device", "CUDA+CPU", "--", "--cycles-device"]):
            self.assertEqual(render_device.facts(scene(), self.preferences)["device"], "CUDA")

    def test_facts_while_listing_say_so(self):
        preferences, _ = self.run_setup(Prefs(), Child(running=True))
        self.assertIn("still listing", render_device.facts(scene(), preferences)["device_why"])

    def test_estimate_scales_the_last_render_on_the_same_device(self):
        device = "GPU (Metal: Apple M4 Pro (GPU - 20 cores))"
        done = {"status": "done", "started": 100.0, "ended": 110.0,
                "detail": {"engine": "CYCLES", "device": device, "resolution": [1000, 1000], "samples": 100,
                           "frames": [1, 1]}}
        self.assertEqual(render_device.estimate({"device": device, "resolution": [1000, 1000], "samples": 400}), {})
        render_device.observe(done)
        found = render_device.estimate({"device": device, "resolution": [2000, 1000], "samples": 400, "frames": [1, 2]})
        self.assertEqual(found["estimate_s"], 160)
        self.assertIn("10.0 s for 100", found["estimate_from"])
        self.assertEqual(render_device.estimate({"device": "CPU", "resolution": [10, 10], "samples": 1}), {})

    def test_the_jobs_block_carries_the_device(self):
        record = job_store.new_record("j1", "s", "c", {"kind": "RENDER_STILL"}, now=1.0)
        record["detail"].update(device="CPU", device_why="the scene's Cycles device (cycles.device) is CPU",
                                estimate_s=30)
        record.update(status="running", progress=0.5)
        entry = job_store.context_entry(record)
        self.assertEqual((entry["device"], entry["estimate_s"]), ("CPU", 30))
        self.assertIn("device_why", entry)
        record.update(status="done", started=1.0, ended=2.0)
        self.assertNotIn("estimate_s", job_store.context_entry(record))


if __name__ == "__main__":
    unittest.main()
