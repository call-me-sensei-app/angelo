# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A native suite's machine inputs (native_suite_inputs): resolved by the runner, or a counted skip with the reason.

1.0.10's background runs reported ``test_blender_godot_physics_e2e`` FAILED on every Mac although Godot was
installed: nothing gave the suite the held-out models its manifest pins, so it failed before testing anything, and
the failure read like a product defect. Here: each input resolves from its variable, else from what the machine or the
checkout provides (Godot's default install; the pinned models, fetched and checked into a cache); an input the machine
cannot provide is a skip that says why, never a failure and never a pass. Blender is never launched.
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

GODOT_SUITES = {"test_blender_godot_link_e2e", "test_blender_godot_link_gui", "test_blender_engine_suggest_gui",
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


if __name__ == "__main__":
    unittest.main()
