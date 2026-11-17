# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A native suite's machine inputs (native_suite_inputs): resolved by the runner, or a counted skip with the reason.

1.0.10's background runs reported ``test_blender_godot_physics_e2e`` FAILED on every Mac although Godot was
installed: nothing gave the suite the held-out models its manifest pins, so it failed before testing anything, and
the failure read like a product defect. Here: each input resolves from its variable, else from what the machine or the
checkout provides (Godot's default install; the pinned models, fetched and checked into a cache); an input the machine
cannot provide is a skip that says why, never a failure and never a pass. Blender is never launched.

A hardware GPU is such an input (``hardware_gpu``). ``test_blender_view_warm`` times a compile-ahead draw against a
synchronous render, which is a property of a GPU's shader compiler: on a Linux gate VM that draws with llvmpipe it
failed steadily, alone on an idle machine (compile-ahead 910 to 968 ms against 2225 to 2356 ms, a ratio near 0.40
where a GPU gives under a third). Angelo does not support machines without a GPU, so the suite asks for one by
CAPABILITY, never by platform: the runner asks the app under test which renderer it draws with (or reads
``ANGELO_QA_GPU_RENDERER``), and a software renderer (llvmpipe, softpipe, SwiftShader, lavapipe, WARP or the
Microsoft Basic Render Driver, GDI Generic: the product's own list, ``parallel_capability``) is a counted skip with
that reason. On every machine with a real GPU the suite runs. An app that cannot say is a skip too, never taken for
hardware.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import native_suite_inputs as inputs  # noqa: E402
import native_suite_manifest as manifest  # noqa: E402
import run_native_suites as runner  # noqa: E402
from test_native_suite_runner import FAKE_BINARY, REAL_HOME, fake_suite  # noqa: E402

GODOT_SUITES = {"test_blender_godot_link_e2e", "test_blender_godot_link_busy", "test_blender_godot_link_gui", "test_blender_engine_suggest_gui",
                "test_blender_godot_physics_e2e"}


class GodotTests(unittest.TestCase):
    def test_the_variable_names_the_editors(self):
        found = {"/opt/godot-4.4", "/opt/godot-4.5"}
        value = os.pathsep.join(sorted(found))
        self.assertEqual(sorted(found), inputs.godot_editors({inputs.GODOT_ENV: value}, isfile=found.__contains__))

    def test_a_named_editor_that_is_not_there_is_unavailable_and_named(self):
        with self.assertRaises(inputs.Unavailable) as caught:
            inputs.godot_editors({inputs.GODOT_ENV: "/opt/missing-godot"}, isfile=lambda path: False)
        self.assertIn("/opt/missing-godot", str(caught.exception))

    def test_without_the_variable_the_default_install_is_used(self):
        default = inputs.DEFAULT_GODOT["darwin"]
        self.assertEqual([default], inputs.godot_editors({}, platform="darwin", isfile={default}.__contains__))

    def test_no_editor_anywhere_says_how_to_provide_one(self):
        for platform in ("darwin", "linux", "win32"):
            with self.assertRaises(inputs.Unavailable) as caught:
                inputs.godot_editors({}, platform=platform, isfile=lambda path: False)
            self.assertIn(inputs.GODOT_ENV, str(caught.exception))

    def test_the_suites_own_default_is_the_resolvers(self):
        # One default for every Godot suite: a suite started by hand looks where the runner looks.
        for name in ("test_blender_godot_link_e2e.py", "test_blender_godot_link_gui.py",
                     "test_blender_engine_suggest_gui.py"):
            text = (HERE / name).read_text(encoding="utf-8")
            self.assertNotIn("/Applications/Godot.app/Contents/MacOS/Godot\"", text, name)
            self.assertIn("native_suite_inputs.default_godot()", text, name)


class HeldOutModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_the_variable_names_a_folder_of_models(self):
        folder = self.root / "given"
        folder.mkdir()
        (folder / "a.glb").write_bytes(b"glTF")
        self.assertEqual(str(folder), inputs.physics_assets({inputs.PHYSICS_ASSETS_ENV: str(folder)}, self.root,
                                                            fetch=self.fail))

    def test_a_named_folder_without_models_is_unavailable(self):
        with self.assertRaises(inputs.Unavailable) as caught:
            inputs.physics_assets({inputs.PHYSICS_ASSETS_ENV: str(self.root)}, self.root, fetch=self.fail)
        self.assertIn(inputs.PHYSICS_ASSETS_ENV, str(caught.exception))

    def test_without_the_variable_the_pinned_models_are_fetched_into_a_cache_named_by_the_manifest(self):
        calls = []

        def fetch(manifest_data, dest):
            calls.append((manifest_data, Path(dest)))
            Path(dest).mkdir(parents=True, exist_ok=True)
            (Path(dest) / "model.glb").write_bytes(b"glTF")
            return manifest_data["models"]

        first = inputs.physics_assets({}, self.root, fetch=fetch)
        second = inputs.physics_assets({}, self.root, fetch=fetch)
        pinned = inputs.HELDOUT_MANIFEST.read_bytes()
        self.assertEqual(first, second)
        self.assertEqual(Path(first).parent, self.root / inputs.CACHE_FOLDER)
        self.assertIn(hashlib.sha256(pinned).hexdigest()[:12], Path(first).name)
        self.assertEqual(json.loads(pinned), calls[0][0])
        # Each call checks the folder again (the fetcher skips files whose sha256 already matches).
        self.assertEqual(2, len(calls))

    def test_a_fetch_that_fails_is_unavailable_with_its_reason(self):
        def offline(_manifest, _dest):
            raise OSError("network is unreachable")

        def refused(_manifest, _dest):
            raise SystemExit("model.glb: sha256 mismatch")

        for fetch, words in ((offline, "network is unreachable"), (refused, "sha256 mismatch")):
            with self.assertRaises(inputs.Unavailable) as caught:
                inputs.physics_assets({}, self.root, fetch=fetch)
            self.assertIn(words, str(caught.exception))
            self.assertIn(inputs.PHYSICS_ASSETS_ENV, str(caught.exception))

    def test_the_real_fetcher_is_the_one_the_manifest_documents(self):
        fetcher = inputs.load_fetcher()
        self.assertTrue(callable(fetcher.fetch))
        self.assertEqual(inputs.HELDOUT_MANIFEST, fetcher.MANIFEST)


class FetcherTrustTests(unittest.TestCase):
    """Angelo's bundled Python has no CA certificates of its own: every pinned download failed certificate checks."""

    class Context:
        def __init__(self, cas):
            self.cas, self.loaded = cas, []

        def cert_store_stats(self):
            return {"x509_ca": self.cas}

        def load_verify_locations(self, cafile):
            self.loaded.append(cafile)

    def test_a_context_with_certificates_is_used_as_it_is(self):
        context = self.Context(cas=140)
        fetcher = inputs.load_fetcher()
        self.assertIs(context, fetcher.ssl_context(bundles=("/nowhere",), create=lambda: context))
        self.assertEqual([], context.loaded)

    def test_without_certificates_the_first_system_bundle_is_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder) / "cert.pem"
            bundle.write_text("fixture")
            context = self.Context(cas=0)
            fetcher = inputs.load_fetcher()
            with unittest.mock.patch.dict(sys.modules, {"certifi": None}):   # Not installed.
                fetcher.ssl_context(bundles=(str(Path(folder) / "absent.pem"), str(bundle)), create=lambda: context)
        self.assertEqual([str(bundle)], context.loaded)

    def test_no_bundle_anywhere_keeps_verification_and_loads_nothing(self):
        context = self.Context(cas=0)
        fetcher = inputs.load_fetcher()
        with unittest.mock.patch.dict(sys.modules, {"certifi": None}):
            self.assertIs(context, fetcher.ssl_context(bundles=("/nowhere/cert.pem",), create=lambda: context))
        self.assertEqual([], context.loaded)

    def test_downloads_use_the_checked_context(self):
        text = inputs.FETCHER.read_text(encoding="utf-8")
        self.assertIn("context=ssl_context()", text)
        self.assertNotIn("CERT_NONE", text)


class PhysicsStepTimeoutTests(unittest.TestCase):
    def test_each_step_budget_honours_the_runners_timeout_scale(self):
        # Every model goes through one Angelo step: on a loaded machine the runner's --timeout-scale must reach it.
        import importlib.util
        spec = importlib.util.spec_from_file_location("physics_e2e", HERE / "test_blender_godot_physics_e2e.py")
        suite = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(suite)
        self.assertEqual(suite.TIMEOUT, suite.step_timeout({}))
        self.assertEqual(suite.TIMEOUT * 2.5, suite.step_timeout({"ANGELO_QA_TIMEOUT_SCALE": "2.5"}))


class HardwareGpuTests(unittest.TestCase):
    HARDWARE = ("Apple M4 Pro", "NVIDIA Tesla T4/PCIe/SSE2", "AMD Radeon RX 7900 XT", "Intel(R) Iris(R) Xe Graphics",
                "ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D11 vs_5_0 ps_5_0, D3D11)", "Mesa Intel(R) UHD 770")
    SOFTWARE = ("llvmpipe (LLVM 17.0.6, 256 bits)", "softpipe", "Google SwiftShader", "SwiftShader Device (Subzero)",
                "Microsoft Basic Render Driver", "D3D12 (Microsoft Basic Render Driver)", "GDI Generic",
                "lavapipe (LLVM 17.0.6)", "WARP", "Direct3D WARP rasterizer")

    def test_a_software_renderer_is_told_from_a_hardware_gpu_by_its_name(self):
        for name in self.HARDWARE:
            self.assertFalse(inputs.software_renderer(name), name)
        for name in self.SOFTWARE:
            self.assertTrue(inputs.software_renderer(name), name)
        self.assertFalse(inputs.software_renderer("Warpath GX"))   # WARP is a word, not a part of one.

    def test_the_names_are_the_products_own_list(self):
        from angelo_agent import parallel_capability
        self.assertGreaterEqual(len(parallel_capability.SOFTWARE_RENDERERS), 5)
        for name in parallel_capability.SOFTWARE_RENDERERS:
            self.assertTrue(inputs.software_renderer("X %s Y" % name.upper()), name)

    def test_the_variable_names_the_renderer_and_a_software_one_is_unavailable_with_the_reason(self):
        asked = []
        probe = lambda binary, env: asked.append(binary) or ("OPENGL", "never asked")   # noqa: E731
        self.assertEqual("Apple M4 Pro", inputs.hardware_gpu({inputs.GPU_ENV: "Apple M4 Pro"}, Path("/c"), probe))
        with self.assertRaisesRegex(inputs.Unavailable, r"hardware GPU.*software renderer \(llvmpipe \(LLVM 17"):
            inputs.hardware_gpu({inputs.GPU_ENV: "llvmpipe (LLVM 17.0.6, 256 bits)"}, Path("/c"), probe)
        self.assertEqual([], asked)

    def test_without_the_variable_the_app_under_test_is_asked_once_which_renderer_it_draws_with(self):
        asked = []

        def probe(binary, env):
            asked.append(binary)
            return answers[binary]
        answers = {"/apps/gpu": ("METAL", "Apple M4 Pro"), "/apps/soft": ("OPENGL", "llvmpipe (LLVM 17.0.6, 256 bits)"),
                   "/apps/t4": ("VULKAN", "NVIDIA Tesla T4")}
        self.assertEqual("Apple M4 Pro", inputs.hardware_gpu({inputs.BINARY_ENV: "/apps/gpu"}, Path("/c"), probe))
        self.assertEqual("NVIDIA Tesla T4", inputs.hardware_gpu({inputs.BINARY_ENV: "/apps/t4"}, Path("/c"), probe))
        with self.assertRaisesRegex(inputs.Unavailable, "software renderer"):
            inputs.hardware_gpu({inputs.BINARY_ENV: "/apps/soft"}, Path("/c"), probe)
        self.assertEqual(["/apps/gpu", "/apps/t4", "/apps/soft"], asked)

    def test_an_app_that_cannot_say_is_unavailable_never_taken_for_hardware(self):
        for answer in (("NONE", "RuntimeError"), ("", ""), ("OPENGL", "")):
            with self.subTest(answer=answer), self.assertRaisesRegex(inputs.Unavailable, "hardware GPU"):
                inputs.hardware_gpu({inputs.BINARY_ENV: "/apps/x"}, Path("/c"), lambda binary, env: answer)
        with self.assertRaisesRegex(inputs.Unavailable, "hardware GPU.*" + inputs.GPU_ENV):
            inputs.hardware_gpu({}, Path("/c"), lambda binary, env: ("METAL", "never asked"))   # No app to ask.

        def broken(binary, env):
            raise inputs.Unavailable("the app under test did not say which renderer it draws with (exit 1)")
        with self.assertRaisesRegex(inputs.Unavailable, "did not say"):
            inputs.hardware_gpu({inputs.BINARY_ENV: "/apps/x"}, Path("/c"), broken)

    def test_the_probe_starts_the_app_in_the_background_and_reads_its_one_marked_line(self):
        seen = {}

        def run(command, **options):
            seen.update(command=command, options=options)
            said = "Blender 5.3.0\nnoise\n%sVULKAN\tNVIDIA Tesla T4\nBlender quit\n" % inputs.PROBE_MARK
            return unittest.mock.Mock(returncode=0, stdout=said, stderr="")
        self.assertEqual(("VULKAN", "NVIDIA Tesla T4"), inputs.probe_renderer("/apps/Angelo", {"HOME": "/h"}, run))
        self.assertEqual("/apps/Angelo", seen["command"][0])
        self.assertIn("--background", seen["command"])
        self.assertIn("--factory-startup", seen["command"])
        self.assertEqual({"HOME": "/h"}, seen["options"]["env"])
        self.assertTrue(seen["options"]["timeout"])
        compile(seen["command"][seen["command"].index("--python-expr") + 1], "probe", "exec")   # It is Python.
        for stdout in ("Blender 5.3.0\n", ""):   # No marked line: the app did not say.
            with self.subTest(stdout=stdout), self.assertRaisesRegex(inputs.Unavailable, "did not say"):
                inputs.probe_renderer("/apps/Angelo", {}, lambda command, **options: unittest.mock.Mock(
                    returncode=1, stdout=stdout, stderr="boom"))

        def hangs(command, **options):
            raise inputs.subprocess.TimeoutExpired(command, options["timeout"])
        with self.assertRaisesRegex(inputs.Unavailable, "did not say"):
            inputs.probe_renderer("/apps/Angelo", {}, hangs)

    def test_it_is_a_resolver_and_the_view_warm_suite_declares_it(self):
        variable, resolver = inputs.RESOLVERS["hardware_gpu"]
        self.assertEqual((inputs.GPU_ENV, "Apple M4 Pro"), (variable, resolver({inputs.GPU_ENV: "Apple M4 Pro"}, "/c")))
        suite = manifest.by_name()["test_blender_view_warm"]
        self.assertIn("hardware_gpu", suite.inputs)
        self.assertIn(inputs.GPU_ENV, suite.options_env)
        self.assertEqual((), suite.platforms)   # By capability: it runs on every platform that has a GPU.
        source = (HERE / "test_blender_view_warm.py").read_text(encoding="utf-8")
        self.assertIn(inputs.GPU_ENV, source)   # Its report names the renderer it ran on.


class WindowCaptureTests(unittest.TestCase):
    """A suite that judges the person's window by ``screen.screenshot`` runs only where that captures the window
    (break-it C5 on 5.3, re-read): test_blender_compile_ahead_window on a software OpenGL window on a virtual
    display read a black picture, which is no verdict on the viewport. Only that is a skip: the first probe made a
    skip of every black picture, every exception and every app that did not answer, which hid a broken screenshot
    or a window that never drew behind a reason nobody reads as a defect."""
    SOFT, HARD = ("OPENGL", "llvmpipe (LLVM 17.0.6, 256 bits)"), ("METAL", "Apple M4 Pro")

    def capture(self, said, gpu, env=None):
        """``window_capture`` for an app whose capture probe says ``said`` and whose renderer probe says ``gpu``."""
        asked = []

        def gpu_probe(binary, env):
            asked.append(binary)
            if isinstance(gpu, Exception):
                raise gpu
            return gpu
        value = inputs.window_capture(dict({inputs.BINARY_ENV: "/apps/x"}, **(env or {})), Path("/c"),
                                      lambda binary, env, cache_root: said, gpu_probe)
        return value, asked

    def test_a_lit_capture_runs_without_asking_for_the_renderer(self):
        self.assertEqual(("1", []), self.capture(("1", "2294x1408"), self.HARD))
        self.assertEqual(("1", []), self.capture(("1", "1920x1080"), self.SOFT))   # Software Vulkan captures.

    def test_a_black_capture_is_a_skip_only_where_a_software_renderer_draws(self):
        with self.assertRaisesRegex(inputs.Unavailable, r"black picture \(1920x1080\) on a software renderer \(llvmp"):
            self.capture(("0", "1920x1080"), self.SOFT)

    def test_a_black_capture_where_a_hardware_gpu_draws_fails(self):
        """Forced black on a hardware GPU: the window did not draw or the screenshot is broken. Never a skip."""
        with self.assertRaisesRegex(inputs.Failed, r"black picture \(2294x1408\) though it draws with a hardware GPU "
                                                   r"\(Apple M4 Pro\)"):
            self.capture(("0", "2294x1408"), self.HARD)
        for unnamed in (("NONE", "RuntimeError"), ("OPENGL", ""),
                        inputs.Unavailable("the app under test did not say which renderer it draws with (exit 1)")):
            with self.subTest(gpu=unnamed), self.assertRaisesRegex(inputs.Failed, "could not be named"):
                self.capture(("0", "1920x1080"), unnamed)   # Not known to be software: not a skip either.

    def test_a_capture_that_raised_or_an_app_that_did_not_answer_fails_whatever_the_renderer(self):
        """A forced probe exception: a broken screenshot operator, a missing numpy, a crash at start-up."""
        for gpu in (self.SOFT, self.HARD):
            with self.subTest(gpu=gpu), self.assertRaisesRegex(inputs.Failed, r"could not capture its own window "
                                                               r"with screen.screenshot \(RuntimeError: Operator"):
                self.capture(("NONE", "RuntimeError: Operator bpy.ops.screen.screenshot.poll() failed"), gpu)

        def silent(binary, env, cache_root):
            raise inputs.Failed("the app under test did not say whether its window can be captured (exit -11)")
        with self.assertRaisesRegex(inputs.Failed, r"did not say .* \(exit -11\)"):
            inputs.window_capture({inputs.BINARY_ENV: "/apps/x"}, Path("/c"), silent, lambda binary, env: self.SOFT)
        with self.assertRaisesRegex(inputs.Failed, "no app to ask"):
            inputs.window_capture({}, Path("/c"), silent, lambda binary, env: self.SOFT)

    def test_the_variables_answer_without_asking(self):
        never = lambda *args: self.fail("asked")   # noqa: E731
        env = {inputs.CAPTURE_ENV: "1", inputs.BINARY_ENV: "/a"}
        self.assertEqual("1", inputs.window_capture(env, "/c", never, never))
        env = {inputs.CAPTURE_ENV: "0", inputs.BINARY_ENV: "/a", inputs.GPU_ENV: "llvmpipe (LLVM 17.0.6, 256 bits)"}
        with self.assertRaisesRegex(inputs.Unavailable, "software renderer"):
            inputs.window_capture(env, "/c", never, never)
        with self.assertRaisesRegex(inputs.Failed, "hardware GPU"):
            inputs.window_capture(dict(env, **{inputs.GPU_ENV: "NVIDIA Tesla T4"}), "/c", never, never)

    def test_the_probe_opens_a_window_with_a_profile_of_its_own_and_reads_its_one_marked_line(self):
        seen = {}

        def run(command, **options):
            seen.update(command=command, options=options)
            return unittest.mock.Mock(returncode=0, stderr="", stdout="Blender 5.3.0\n%s%s\nBlender quit\n"
                                      % (inputs.CAPTURE_MARK, seen.get("say", "1\t1920x1080")))
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(("1", "1920x1080"), inputs.probe_window_capture("/apps/Angelo", {"HOME": "/h"}, root, run))
            command, env = seen["command"], seen["options"]["env"]
            self.assertEqual("/apps/Angelo", command[0])
            self.assertNotIn("--background", command)   # A window, or there is nothing to capture.
            for flag in inputs.gui_launch.window_flags():   # Opened as a window suite's is: behind, input isolated.
                self.assertIn(flag, command)
            self.assertIn("--factory-startup", command)
            compile(command[command.index("--python-expr") + 1], "probe", "exec")   # It is Python.
            self.assertIn("screen.screenshot", command[command.index("--python-expr") + 1])
            self.assertEqual("/h", env["HOME"])
            for name in ("BLENDER_USER_CONFIG", "BLENDER_USER_SCRIPTS", "ANGELO_STATE_DIR", "ANGELO_CONNECTIONS_DIR"):
                self.assertTrue(Path(env[name]).is_relative_to(Path(root)), name)   # Never the person's own.
            self.assertEqual(("1", "off"), (env["ANGELO_DISABLE_KEYCHAIN"], env["ANGELO_AGENT_RUNTIME"]))
            probe = command[command.index("--python-expr") + 1]
            self.assertLess(probe.index("redraw_timer"), probe.index("screen.screenshot"))   # Drawn, then captured.
            self.assertIn("return 0.5", probe)   # A black capture is taken again, never judged from one moment.
            seen["say"] = "0\t1920x1080"
            self.assertEqual(("0", "1920x1080"), inputs.probe_window_capture("/apps/Angelo", {}, root, run))
            seen["say"] = "NONE\tRuntimeError: no numpy"
            self.assertEqual(("NONE", "RuntimeError: no numpy"),
                             inputs.probe_window_capture("/apps/Angelo", {}, root, run))
            for stdout in ("Blender 5.3.0\n", ""):   # No marked line: it opened no window, or died first.
                with self.subTest(stdout=stdout), self.assertRaisesRegex(inputs.Failed, r"did not say .*exit -11"):
                    inputs.probe_window_capture("/apps/Angelo", {}, root, lambda command, **options:
                                                unittest.mock.Mock(returncode=-11, stdout=stdout, stderr="boom"))

            def hangs(command, **options):
                raise inputs.subprocess.TimeoutExpired(command, options["timeout"])
            with self.assertRaisesRegex(inputs.Failed, "did not say"):   # A hang at start-up: a failure, not a skip.
                inputs.probe_window_capture("/apps/Angelo", {}, root, hangs)

    def test_it_is_a_resolver_and_the_window_suite_declares_it(self):
        variable, resolver = inputs.RESOLVERS["window_capture"]
        self.assertEqual((inputs.CAPTURE_ENV, "1"), (variable, resolver({inputs.CAPTURE_ENV: "1"}, "/c")))
        suite = manifest.by_name()["test_blender_compile_ahead_window"]
        self.assertIn("window_capture", suite.inputs)
        self.assertIn(inputs.CAPTURE_ENV, suite.options_env)
        self.assertEqual((), suite.platforms)   # By capability: wherever the window can be captured.
        source = (HERE / "test_blender_compile_ahead_window.py").read_text(encoding="utf-8")
        self.assertIn("window_capture", source)


class ResolveTests(unittest.TestCase):
    def test_each_input_is_resolved_once_and_reaches_only_its_suites(self):
        calls = []
        resolvers = {"one": ("ANGELO_QA_ONE", lambda env, cache: calls.append("one") or "value-one"),
                     "two": ("ANGELO_QA_TWO", lambda env, cache: (_ for _ in ()).throw(inputs.Unavailable("no two")))}
        suites = [fake_suite("a", inputs=("one",)), fake_suite("b", inputs=("one", "two")), fake_suite("c")]
        values, unavailable = inputs.resolve(suites, {}, Path("/cache"), resolvers=resolvers)
        self.assertEqual(["one"], calls)
        self.assertEqual({"a": {"ANGELO_QA_ONE": "value-one"}, "c": {}}, values)
        self.assertEqual({"b": "input two unavailable: no two"}, unavailable)

    def test_a_failed_input_is_neither_resolved_nor_unavailable_and_is_never_dropped(self):
        def broken(env, cache):
            raise inputs.Failed("the probe broke")
        resolvers = {"one": ("ANGELO_QA_ONE", lambda env, cache: "value-one"), "bad": ("ANGELO_QA_BAD", broken)}
        suites = [fake_suite("a", inputs=("one",)), fake_suite("b", inputs=("one", "bad")), fake_suite("c",
                                                                                                    inputs=("bad",))]
        failed = {}
        values, unavailable = inputs.resolve(suites, {}, Path("/cache"), resolvers=resolvers, failed=failed)
        self.assertEqual(({"a": {"ANGELO_QA_ONE": "value-one"}}, {}), (values, unavailable))
        self.assertEqual({"b": "input bad failed: the probe broke", "c": "input bad failed: the probe broke"}, failed)
        with self.assertRaisesRegex(inputs.Failed, "the probe broke"):   # No one to tell: raised, never a skip.
            inputs.resolve(suites, {}, Path("/cache"), resolvers=resolvers)


class ManifestTests(unittest.TestCase):
    def test_every_godot_suite_declares_its_inputs(self):
        suites = manifest.by_name()
        for name in GODOT_SUITES:
            self.assertIn("godot", suites[name].inputs, name)
        self.assertIn("physics_heldout", suites["test_blender_godot_physics_e2e"].inputs)
        uses_godot = {suite.name for suite in manifest.SUITES if inputs.GODOT_ENV in suite.options_env}
        self.assertEqual(GODOT_SUITES, uses_godot)

    def test_every_declared_input_is_known_and_its_variable_reaches_the_suite(self):
        for suite in manifest.SUITES:
            for name in suite.inputs:
                self.assertIn(name, inputs.RESOLVERS, suite.name)
                self.assertIn(inputs.RESOLVERS[name][0], suite.options_env, suite.name)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nsi-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.binary = self.root / "Angelo"
        if os.name == "nt":
            script, self.binary = self.root / "Angelo.py", self.root / "Angelo.cmd"
            script.write_text(FAKE_BINARY.format(python=sys.executable))
            self.binary.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, script), newline="")
        else:
            self.binary.write_text(FAKE_BINARY.format(python=sys.executable))
            self.binary.chmod(0o755)
        self.addCleanup(setattr, runner, "source_revision", runner.source_revision)
        runner.source_revision = lambda: "fixture"
        self.addCleanup(setattr, inputs, "RESOLVERS", inputs.RESOLVERS)
        inputs.RESOLVERS = {
            "here": ("ANGELO_QA_HERE", lambda env, cache: str(cache)),
            "absent": ("ANGELO_QA_ABSENT", lambda env, cache: (_ for _ in ()).throw(
                inputs.Unavailable("needs the absent thing"))),
        }

    def test_an_unavailable_input_is_a_counted_skip_and_a_resolved_one_reaches_the_suite(self):
        suites = (fake_suite("fake_pass", inputs=("here",), options_env=("ANGELO_QA_HERE",)),
                  fake_suite("fake_needs_absent", inputs=("absent",), options_env=("ANGELO_QA_ABSENT",)))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = runner.main(["--binary", str(self.binary), "--output", str(self.root), "--machine-factor", "off"],
                               suites=suites, base_env={"HOME": REAL_HOME, "PATH": os.environ.get("PATH", "")},
                               addon_check=lambda binary, root: {"matches": True})
        run = next(self.root.glob("angelo-native-suites-*"))
        summary = json.loads((run / "summary.json").read_text())
        self.assertEqual(0, code, output.getvalue())
        self.assertEqual(["fake_pass"], [item["name"] for item in summary["suites"]])
        self.assertEqual([{"name": "fake_needs_absent", "class": manifest.BACKGROUND,
                           "reason": "input absent unavailable: needs the absent thing"}], summary["skipped"])
        seen = json.loads(next(run.rglob("env.json")).read_text())
        self.assertEqual(str(self.root), seen["ANGELO_QA_HERE"])

    def test_a_resolver_is_told_the_app_under_test_and_a_software_renderer_skips_the_suite(self):
        """The whole path: the runner names its binary to the resolvers, and a machine whose app draws with a
        software renderer skips the suite that needs a hardware GPU, with the reason, while the others run."""
        told = {}

        def gpu(env, cache):
            told.update(binary=env.get(inputs.BINARY_ENV))
            return inputs.hardware_gpu(env, cache, lambda binary, env: ("OPENGL", "llvmpipe (LLVM 17.0.6, 256 bits)"))
        inputs.RESOLVERS = dict(inputs.RESOLVERS, hardware_gpu=(inputs.GPU_ENV, gpu))
        suites = (fake_suite("fake_pass"),
                  fake_suite("fake_needs_gpu", inputs=("hardware_gpu",), options_env=(inputs.GPU_ENV,)))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = runner.main(["--binary", str(self.binary), "--output", str(self.root), "--machine-factor", "off"],
                               suites=suites, base_env={"HOME": REAL_HOME, "PATH": os.environ.get("PATH", "")},
                               addon_check=lambda binary, root: {"matches": True})
        summary = json.loads((next(self.root.glob("angelo-native-suites-*")) / "summary.json").read_text())
        self.assertEqual(0, code, output.getvalue())
        self.assertEqual(str(self.binary.resolve()), told["binary"])
        self.assertEqual(["fake_pass"], [item["name"] for item in summary["suites"]])
        self.assertEqual(["fake_needs_gpu"], [item["name"] for item in summary["skipped"]])
        self.assertRegex(summary["skipped"][0]["reason"],
                         r"^input hardware_gpu unavailable: needs a hardware GPU.*software renderer \(llvmpipe")
        self.assertNotIn(inputs.BINARY_ENV, json.loads(next(
            next(self.root.glob("angelo-native-suites-*")).rglob("env.json")).read_text()))   # Not the suites'.

    def test_a_suite_whose_input_failed_is_an_error_of_the_run_with_the_reason_never_a_skip(self):
        """The whole path, for the two the re-read forced: a capture probe that raised, and a black capture where
        a hardware GPU draws. The run fails; the suite is not run, not skipped and not passed."""
        for said, reason in (
                (("NONE", "RuntimeError: forced"), r"could not capture its own window .*RuntimeError: forced"),
                (("0", "2294x1408"), r"black picture \(2294x1408\) though it draws with a hardware GPU")):
            with self.subTest(said=said), \
                    unittest.mock.patch.object(inputs, "probe_window_capture", lambda *args, said=said: said), \
                    unittest.mock.patch.object(inputs, "probe_renderer", lambda *args: ("METAL", "Apple M4 Pro")):
                inputs.RESOLVERS = dict(inputs.RESOLVERS,
                                        window_capture=(inputs.CAPTURE_ENV, inputs.window_capture))
                suites = (fake_suite("fake_pass"), fake_suite("fake_needs_window", inputs=("window_capture",),
                                                              options_env=(inputs.CAPTURE_ENV,)))
                root = self.root / said[0]
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    code = runner.main(["--binary", str(self.binary), "--output", str(root), "--machine-factor",
                                        "off"], suites=suites,
                                       base_env={"HOME": REAL_HOME, "PATH": os.environ.get("PATH", "")},
                                       addon_check=lambda binary, root: {"matches": True})
                summary = json.loads((next(root.glob("angelo-native-suites-*")) / "summary.json").read_text())
                status = {item["name"]: item for item in summary["suites"]}
                self.assertEqual((1, False), (code, summary["success"]), output.getvalue())
                self.assertEqual("passed", status["fake_pass"]["status"])
                self.assertNotIn(status["fake_needs_window"]["status"], ("passed", "skipped"))
                self.assertRegex(" ".join(status["fake_needs_window"]["reasons"]), "input window_capture failed: .*"
                                 + reason)
                self.assertEqual([], [item["name"] for item in summary["skipped"]])
                self.assertEqual(1, len(list(root.rglob("env.json"))), "the suite itself was never started")


if __name__ == "__main__":
    unittest.main()
