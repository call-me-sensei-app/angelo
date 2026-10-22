# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The GPU test VMs' Cycles preference (``bench_gpu``), set the same way on every side of a trial.

Cycles' preferences are a stand-in with the real methods' contracts (``get_device_types``,
``get_devices_for_type``, ``devices``), so the selection, the saved preferences, the scenes' device and the
receipt's device line are checked without Blender or a GPU. The wiring into both sides' trial setup, their
environment and the dry runs is checked on the harness sources. No Blender, client or model is started.
"""

import ast
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_gpu  # noqa: E402

T4 = ("Tesla T4", "OPTIX", "optix-0")


class Prefs:
    """Cycles' add-on preferences: per backend, the devices a listing returns (always with the CPU)."""

    def __init__(self, listed):
        self.listed = listed   # {backend: [(name, type, id), ...]}
        self.devices = []
        self.compute_device_type = "NONE"

    def get_device_types(self, _context):
        return [("NONE", "None", "", 0)] + [(kind, kind, "", 1) for kind in self.listed]

    def get_devices_for_type(self, kind):
        entries = {(e.name, e.type, e.id): e for e in self.devices}
        for name, type_, id_ in self.listed[kind] + [("CPU", "CPU", "cpu")]:
            entries.setdefault((name, type_, id_), SimpleNamespace(name=name, type=type_, id=id_, use=True))
        self.devices = list(entries.values())   # A listing re-allocates the collection.
        return [e for e in self.devices if e.type in (kind, "CPU")]


class FakeBpy:
    def __init__(self, listed, save_ok=True):
        self.prefs = Prefs(listed)
        self.config = tempfile.mkdtemp()
        self.save_ok = save_ok
        addons = {"cycles": SimpleNamespace(preferences=self.prefs)}
        self.context = SimpleNamespace(preferences=SimpleNamespace(addons=addons))
        self.data = SimpleNamespace(scenes=[self.scene("Scene"), self.scene("Other")])
        persistent = lambda function: setattr(function, "_bpy_persistent", True) or function  # noqa: E731
        self.app = SimpleNamespace(handlers=SimpleNamespace(load_post=[], persistent=persistent))
        self.utils = SimpleNamespace(user_resource=lambda kind: self.config)
        self.ops = SimpleNamespace(wm=SimpleNamespace(save_userpref=self.save))

    @staticmethod
    def scene(name):
        return SimpleNamespace(name=name, cycles=SimpleNamespace(device="CPU"))

    def save(self):
        if self.save_ok:
            Path(self.config, "userpref.blend").write_bytes(b"BLENDER")
            return {"FINISHED"}
        return {"CANCELLED"}


class Mode(unittest.TestCase):
    def test_on_by_default_on_windows_only(self):
        self.assertEqual(bench_gpu.mode({}, "win32"), "require")
        self.assertEqual(bench_gpu.mode({}, "darwin"), "off")
        self.assertEqual(bench_gpu.mode({}, "linux"), "off")

    def test_the_variable_decides_both_ways(self):
        self.assertEqual(bench_gpu.mode({bench_gpu.ENV: "off"}, "win32"), "off")
        self.assertEqual(bench_gpu.mode({bench_gpu.ENV: "Require"}, "darwin"), "require")
        with self.assertRaises(ValueError):
            bench_gpu.mode({bench_gpu.ENV: "cpu"}, "win32")

    def test_every_child_gets_the_runs_decision(self):
        self.assertEqual(bench_gpu.environment({}, "win32"), {bench_gpu.ENV: "require"})
        self.assertEqual(bench_gpu.environment({bench_gpu.EXPECT_ENV: "T4"}, "darwin"),
                         {bench_gpu.ENV: "off", bench_gpu.EXPECT_ENV: "T4"})


class Prepare(unittest.TestCase):
    def test_optix_with_the_gpu_and_not_the_cpu(self):
        bpy = FakeBpy({"CUDA": [("Tesla T4", "CUDA", "cuda-0")], "OPTIX": [T4]})
        entry = bench_gpu.prepare(bpy)
        self.assertEqual(bpy.prefs.compute_device_type, "OPTIX")
        self.assertEqual({e.name: e.use for e in bpy.prefs.devices if e.type in ("OPTIX", "CPU")},
                         {"Tesla T4": True, "CPU": False})
        self.assertEqual(entry["device_line"], "Cycles OPTIX: Tesla T4; scene device GPU")
        self.assertEqual((entry["mode"], entry["backend"], entry["devices"], entry["preferences_saved"]),
                         ("require", "OPTIX", ["Tesla T4"], True))
        self.assertTrue(Path(bpy.config, "userpref.blend").is_file())
        self.assertEqual({scene.cycles.device for scene in bpy.data.scenes}, {"GPU"})

    def test_cuda_when_optix_lists_no_gpu(self):
        bpy = FakeBpy({"OPTIX": [], "CUDA": [("Tesla T4", "CUDA", "cuda-0")]})
        self.assertEqual(bench_gpu.prepare(bpy)["device_line"], "Cycles CUDA: Tesla T4; scene device GPU")

    def test_no_gpu_fails_the_trial_and_leaves_the_cpu_choice_unmade(self):
        bpy = FakeBpy({"OPTIX": [], "CUDA": []})
        with self.assertRaisesRegex(RuntimeError, "no GPU device"):
            bench_gpu.prepare(bpy)
        self.assertEqual(bpy.prefs.compute_device_type, "NONE")
        with self.assertRaisesRegex(RuntimeError, "no GPU device"):
            bench_gpu.prepare(FakeBpy({}))

    def test_unsaved_preferences_fail_the_trial(self):
        with self.assertRaisesRegex(RuntimeError, "not saved"):
            bench_gpu.prepare(FakeBpy({"OPTIX": [T4]}, save_ok=False))

    def test_files_opened_later_render_on_the_gpu_too(self):
        bpy = FakeBpy({"OPTIX": [T4]})
        bench_gpu.prepare(bpy)
        bench_gpu.prepare(bpy)   # Once per trial; a second call adds no second handler.
        self.assertEqual(len(bpy.app.handlers.load_post), 1)
        self.assertTrue(getattr(bpy.app.handlers.load_post[0], "_bpy_persistent", False))
        bpy.data.scenes[:] = [FakeBpy.scene("Opened")]
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            bpy.app.handlers.load_post[0]("opened.blend")
        self.assertEqual(bpy.data.scenes[0].cycles.device, "GPU")

    def test_off_records_the_factory_preference(self):
        bpy = FakeBpy({"OPTIX": [T4]})
        self.assertEqual(bench_gpu.run_if_required(bpy, {bench_gpu.ENV: "off"}), {"mode": "off"})
        self.assertEqual(bpy.prefs.compute_device_type, "NONE")
        self.assertEqual(bench_gpu.run_if_required(bpy, {bench_gpu.ENV: "require"})["backend"], "OPTIX")


class Expected(unittest.TestCase):
    def test_the_variable_then_nvidia_smi(self):
        self.assertEqual(bench_gpu.expected_gpu({bench_gpu.EXPECT_ENV: "T4"}), "T4")
        listed = SimpleNamespace(stdout="Tesla T4\n")
        with mock.patch.object(bench_gpu.shutil, "which", return_value="nvidia-smi"):
            self.assertEqual(bench_gpu.expected_gpu({}, run=lambda *a, **k: listed), "Tesla T4")
        with mock.patch.object(bench_gpu.shutil, "which", return_value=None):
            with mock.patch.object(bench_gpu, "WINDOWS_SMI", ()):
                self.assertIsNone(bench_gpu.expected_gpu({}))
            with tempfile.NamedTemporaryFile() as smi, mock.patch.object(bench_gpu, "WINDOWS_SMI", (smi.name,)):
                seen = []
                self.assertEqual(bench_gpu.expected_gpu({}, run=lambda line, **k: seen.append(line[0]) or listed),
                                 "Tesla T4")
                self.assertEqual(seen, [smi.name])   # The driver's own folder when PATH has none.

    def test_receipt_must_name_the_expected_gpu_on_gpu_scenes(self):
        good = {"device_line": "Cycles OPTIX: Tesla T4; scene device GPU"}
        self.assertIsNone(bench_gpu.receipt_problem(good, "T4"))
        self.assertIsNone(bench_gpu.receipt_problem(good, "Tesla T4"))
        self.assertIn("does not name", bench_gpu.receipt_problem(good, "L4"))
        self.assertIn("not rendering on the GPU", bench_gpu.receipt_problem(
            {"device_line": "Cycles OPTIX: Tesla T4; scene device CPU/GPU"}, "T4"))
        self.assertIn("no Cycles device line", bench_gpu.receipt_problem({"mode": "off"}, "T4"))
        self.assertIn("no Cycles device line", bench_gpu.receipt_problem(None, "T4"))
        self.assertIn("no GPU to expect", bench_gpu.receipt_problem(good, None))

    def test_dry_run_check_applies_only_when_required(self):
        self.assertIsNone(bench_gpu.dry_run_problem(None, {bench_gpu.ENV: "off"}))
        self.assertIn("no Cycles device line", bench_gpu.dry_run_problem(
            None, {bench_gpu.ENV: "require", bench_gpu.EXPECT_ENV: "T4"}))


@unittest.skipIf(sys.platform == "win32", "a shell script stands in for Blender")
class Probe(unittest.TestCase):
    """``probe`` runs the side's binary with bench_gpu.py; a script stands in for Blender's output."""

    def fake_blender(self, printed, code=0):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        binary = Path(folder.name, "blender")
        binary.write_text("#!/bin/sh\nprintf '%%s\\n' %s\nexit %d\n" % (json.dumps(printed), code))
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        return binary

    def test_pass_fail_and_silence(self):
        line = bench_gpu.MARK + json.dumps({"device_line": "Cycles OPTIX: Tesla T4; scene device GPU"})
        environ = dict(os.environ, **{bench_gpu.EXPECT_ENV: "T4"})
        self.assertIsNone(bench_gpu.probe(self.fake_blender(line), environ)["problem"])
        self.assertIn("does not name", bench_gpu.probe(self.fake_blender(line), dict(
            environ, **{bench_gpu.EXPECT_ENV: "L4"}))["problem"])
        failed = bench_gpu.MARK + json.dumps({"error": "RuntimeError: Cycles lists no GPU device"})
        self.assertIn("no GPU device", bench_gpu.probe(self.fake_blender(failed), environ)["problem"])
        self.assertIn("no device line", bench_gpu.probe(self.fake_blender("crashed", 1), environ)["problem"])

    def test_probes_skip_when_off_and_name_the_side(self):
        self.assertEqual(bench_gpu.probes([("mcp", "/x")], {bench_gpu.ENV: "off"}), ([], []))
        line = bench_gpu.MARK + json.dumps({"device_line": "Cycles CUDA: Tesla T4; scene device GPU"})
        results, problems = bench_gpu.probes([("mcp", self.fake_blender(line)), ("angelo", "")], dict(
            os.environ, **{bench_gpu.ENV: "require", bench_gpu.EXPECT_ENV: "A100"}))
        self.assertEqual([r["side"] for r in results], ["mcp"])
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("GPU check, mcp side: "))


def _function(path, name):
    tree = ast.parse((HERE / path).read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)


def _calls(node):
    return [ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)]


class Wiring(unittest.TestCase):
    """Both sides run the same step before their trial starts, and every dry run checks the device line."""

    def test_angelo_side_sets_it_in_both_boot_paths_before_the_chat_opens(self):
        calls = _calls(_function("eval_live_agent_session.py", "configure"))
        self.assertLess(calls.index("bench_gpu.run_if_required"), calls.index("begin_opening"))
        source = (HERE / "eval_live_agent_session.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("**self.configure()"), 2)   # Selected connection and team sign-in.
        self.assertIn('self.report["gpu_preference"], self.opening = bench_gpu.run_if_required(bpy), begin_opening(', source)

    def test_mcp_side_sets_it_before_its_server_starts_and_reports_it(self):
        source = (HERE / "bench_mcp_blender_startup.py").read_text(encoding="utf-8")
        self.assertLess(source.index("bench_gpu.run_if_required(bpy)"), source.index("bpy.ops.blmcp.server_start()"))
        self.assertIn("gpu_preference=gpu", source)

    def test_every_child_of_a_run_gets_the_same_mode(self):
        import bench_mcp_trial as mcp
        import eval_live_agent
        for value in ("off", "require"):
            with mock.patch.dict(os.environ, {bench_gpu.ENV: value}):
                self.assertEqual(mcp.clean_environment()[bench_gpu.ENV], value)
        self.assertIn("bench_gpu.environment", _calls(_function("eval_live_agent.py", "trial_environment")))
        self.assertTrue(callable(eval_live_agent.trial_environment))

    def test_dry_runs_check_the_device_line(self):
        for path in ("bench_mcp_trial.py", "bench_mcp_codex.py"):
            node = _function(path, "dry_run")
            self.assertIn("bench_gpu.dry_run_problem", _calls(node), path)
            self.assertIn('output["gpu_problem"] is None', ast.unparse(node).replace("'", '"'), path)
        main = ast.unparse(_function("bench_mcp_vs_angelo.py", "main"))
        self.assertLess(main.index("gpu_check(args)"), main.index("scenario_dry_run.run("))
        self.assertLess(main.index("gpu_check(args)", main.index("args.preflight_only")), main.index("PREFLIGHT_OK"))

    def test_gpu_check_probes_each_sides_own_blender(self):
        import bench_mcp_vs_angelo as bench
        seen = []
        with mock.patch.object(bench.bench_gpu, "probes", lambda sides: seen.append(sides) or ([], [])):
            for sides, dry_mcp in (("both", False), ("angelo", False), ("mcp", False), ("both", True)):
                bench.gpu_check(SimpleNamespace(sides=sides, dry_run_mcp=dry_mcp, stock_blender="stock",
                                                angelo_binary="angelo"))
        self.assertEqual(seen, [[("mcp", "stock"), ("angelo", "angelo")], [("angelo", "angelo")],
                                [("mcp", "stock")], [("mcp", "stock")]])

    def test_render_device_gpu_uses_the_same_selection(self):
        import scenario_session
        bpy = FakeBpy({"METAL": [("Apple M4", "METAL", "metal-0")]})
        self.assertEqual(scenario_session.use_render_device(bpy, "gpu"),
                         {"device": "gpu", "backend": "METAL", "devices": ["Apple M4"]})
        with self.assertRaisesRegex(RuntimeError, "no GPU device"):
            scenario_session.use_render_device(FakeBpy({"OPTIX": []}), "gpu")


if __name__ == "__main__":
    unittest.main()
