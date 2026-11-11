# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Loading render kernels never counts against a production job suite's phase (production_jobs_harness).

Found on 1.1 (Blender 5.3, macOS): test_blender_render_deadline failed twice with "Phase warmup did not advance in
150 s" and then passed in 37 s. The suite's first render is untimed because it "loads kernels", but it ran inside a
phase. Every suite gets a fresh cache folder, so what persists is the Metal compiler cache macOS keeps per app. The
first run on a new Cycles version, or on a fresh Mac, compiled the kernels cold, and that took longer than one phase.
The same class applies to every suite on the harness, on every line and backend that compiles kernels on first use.

Covered against stand-ins, on a fake clock: Cycles' two kernel-loading status texts are recognised and nothing else
is; a long kernel load inside a phase (a stub render_stats) does not fail it, and the receipt records the seconds;
a stalled phase without one still fails; a load past KERNEL_LOAD_CAP_S fails, saying so; the suite timeout is
extended by kernel-load time up to the cap; and the runner gives every suite on the harness the same allowance, so
its own kill timeout never ends a run that the harness still allows.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib.util
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import native_suite_manifest as manifest  # noqa: E402
import run_native_suites  # noqa: E402

# Cycles' own texts: Scene::load_kernels (every backend) and MetalDevice::is_ready (Metal's generic kernels).
LOADING = "Loading render kernels (may take a few minutes the first time)"
METAL_LOADING = "Mem: 1M | 12 / 87 render kernels loaded (may take a few minutes the first time)"
RENDERING = ("Mem: 5475M | Sample 1/2048 (1 kernels to optimize)", "Mem: 5475M | Finished",
             "Mem: 1M | Synchronizing object | Cube", "Remaining: 03:49.40 | Mem: 5475M | Sample 12/2048")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def load_harness(folder):
    """``production_jobs_harness`` from its file against stand-ins; nothing is left in ``sys.modules``."""
    handlers = SimpleNamespace(render_stats=[], persistent=lambda function: function)
    quit_calls = []
    package = types.ModuleType("angelo_agent")
    ops = SimpleNamespace(wm=SimpleNamespace(quit_blender=lambda: quit_calls.append(1)))
    modules = {"bpy": SimpleNamespace(app=SimpleNamespace(handlers=handlers), ops=ops),
               "angelo_agent": package, "angelo_agent.files": SimpleNamespace(FileWorkspace=lambda root: root),
               "angelo_agent.render_extensions": SimpleNamespace(host_handler=lambda function: function),
               "qa_boot": SimpleNamespace(gated=lambda tick: tick)}
    with mock.patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("harness_under_test", HERE / "production_jobs_harness.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        clock = Clock()
        with mock.patch.object(module.time, "monotonic", clock):
            harness = module.Harness("ANGELO_TEST", "report.json", str(folder), timeout=600)
    return module, harness, handlers, clock, quit_calls


class KernelLoadTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.module, self.harness, self.handlers, self.clock, self.quits = load_harness(self.folder)
        patcher = mock.patch.object(self.module.time, "monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.harness.jobs = lambda: SimpleNamespace(active=lambda: None)   # No Angelo job in these phases.
        with mock.patch.dict(sys.modules, {"qa_boot": SimpleNamespace(gated=lambda tick: tick)}):
            self.tick = self.harness.run(lambda harness: 1.0)

    def stats(self, text):
        for handler in self.handlers.render_stats:
            handler(text)

    def advance(self, seconds, text=None):
        """``seconds`` of fake time, one tick a second, with ``text`` as Cycles' status meanwhile."""
        for _ in range(int(seconds)):
            self.clock.now += 1.0
            if text is not None:
                self.stats(text)
            if self.tick() is None:
                return False
        return True

    def receipt(self):
        return json.loads((self.folder / "report.json").read_text(encoding="utf-8"))

    def test_only_cycles_kernel_loading_texts_count(self):
        clock = self.module.KernelLoadClock()
        for text in (LOADING, METAL_LOADING):
            self.assertTrue(clock.loading(text), text)
        for text in RENDERING + ("", "Loading render kernels", "Mem: 1M | Initializing"):
            self.assertFalse(clock.loading(text), text)

    def test_a_long_kernel_load_in_a_phase_never_fails_it_and_the_receipt_records_it(self):
        self.harness.go("warmup")
        self.assertTrue(self.advance(100))
        self.stats(METAL_LOADING)   # Cycles reports the load as it starts, then on every update meanwhile.
        self.assertTrue(self.advance(400, METAL_LOADING), self.harness.state.get("error"))   # A cold Metal compile.
        self.stats(RENDERING[0])
        self.assertTrue(self.advance(40), self.harness.state.get("error"))   # 540 s in the phase, 140 s of it waiting.
        self.harness.finish()
        receipt = self.receipt()
        self.assertEqual(receipt["phase"], "complete")
        self.assertEqual(receipt["kernel_load_s"], 400.0)
        self.assertEqual(receipt["kernel_loads"], [{"phase": "warmup", "seconds": 400.0}])

    def test_a_stalled_phase_without_a_kernel_load_still_fails(self):
        self.harness.go("warmup")
        self.assertTrue(self.advance(50, LOADING))
        self.stats(RENDERING[1])
        self.assertFalse(self.advance(200))
        self.assertIn("Phase warmup did not advance in 150 s", self.receipt()["error"])

    def test_a_kernel_load_past_its_cap_fails_saying_so(self):
        self.harness.go("gpu")
        self.assertFalse(self.advance(self.module.KERNEL_LOAD_CAP_S + 5, LOADING))
        error = self.receipt()["error"]
        self.assertIn("still loading render kernels after", error)
        self.assertIn("KERNEL_LOAD_CAP_S", error)

    def test_the_suite_timeout_grows_by_kernel_load_time_up_to_the_cap(self):
        cap = self.module.KERNEL_LOAD_CAP_S
        for index in range(4):   # Each phase quick, each with a kernel load; the suite's 600 s pass meanwhile.
            self.harness.go("phase%d" % index)
            self.assertTrue(self.advance(140), self.harness.state.get("error"))
            self.assertTrue(self.advance(min(cap - 1, 200), LOADING), self.harness.state.get("error"))
            self.stats(RENDERING[1])
        self.harness.go("last")
        self.assertFalse(self.advance(self.harness.timeout + cap))
        self.assertIn("The suite timed out in last", self.receipt()["error"])
        elapsed = self.receipt()["elapsed_seconds"]
        self.assertLessEqual(elapsed, self.harness.timeout + cap + 1)

    def test_the_runner_gives_every_harness_suite_the_kernel_load_allowance(self):
        users = {path.name for path in HERE.glob("test_blender_*.py")
                 if re.search(r"^from production_jobs_harness import|^import production_jobs_harness",
                              path.read_text(encoding="utf-8"), re.M)}
        self.assertGreaterEqual(len(users), 10)
        suites = {suite.script: suite for suite in manifest.SUITES}
        for script in sorted(users):
            with self.subTest(script=script):
                self.assertEqual(suites[script].kernel_load_cap, self.module.KERNEL_LOAD_CAP_S)
        others = [suite.script for suite in manifest.SUITES if suite.kernel_load_cap and suite.script not in users]
        self.assertEqual(others, [], "only suites whose harness discounts kernel loads get the allowance")
        suite = suites["test_blender_render_deadline.py"]
        with tempfile.TemporaryDirectory() as run:
            planned = run_native_suites.plan_suite(suite, Path(run), Path(run, "Angelo"), {}, gui=True,
                                                   timeout_scale=2.0)
        self.assertEqual(planned["timeout"], (suite.timeout + suite.kernel_load_cap) * 2.0)


class HostedHandlerTests(unittest.TestCase):
    def test_a_shared_suite_helpers_render_handlers_are_the_fixtures_own(self):
        """A helper many GUI suites load (production_jobs_harness, the look fixtures) registers its render handlers
        through ``render_extensions.host_handler``. Otherwise its own measurement reads as add-on Python and every
        render of every suite on it stays in the application (``job_render_route.route``): the cold-kernel proof of
        2026-11-11 found test_blender_render_child's heavy still kept in the app by the harness's kernel-load clock."""
        import ast
        lists = re.compile(r"(render|composite)_\w+")
        unhosted = []
        for path in sorted(HERE.glob("*.py")):
            if path.name.startswith(("test_", "benchmark_")):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            hosted = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                        "host_handler" in ast.unparse(decorator) for decorator in node.decorator_list):
                    hosted.add(node.name)
                if isinstance(node, ast.Assign) and "host_handler(" in ast.unparse(node.value):
                    hosted.update(ast.unparse(target) for target in node.targets)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "append" and node.args):
                    continue
                owner = ast.unparse(node.func.value)
                if not owner.startswith("bpy.app.handlers.") or not lists.fullmatch(owner.rsplit(".", 1)[1]):
                    continue
                handler = ast.unparse(node.args[0])
                if "host_handler(" not in handler and handler not in hosted:
                    unhosted.append("%s:%d %s" % (path.name, node.lineno, owner))
        self.assertEqual(unhosted, [])


if __name__ == "__main__":
    unittest.main()
