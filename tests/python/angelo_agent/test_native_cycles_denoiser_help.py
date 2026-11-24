# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The right Cycles denoiser is findable on every GPU vendor (doc/angelo/render_engines.rst, "Denoisers on every
GPU"). No Blender: Cycles' add-on is read as source, and the one decision it adds runs against fake device lists.

* OpenImageDenoise says what it is: an AI denoiser for the CPU and the GPUs it supports, of every vendor, not an
  Intel-only one (Blender 5.3's description says "Intel OpenImageDenoise").
* DLSS, where a build has it, says what it needs and does: NVIDIA RTX, the Rendered viewport only.
* Where "Use GPU" for OpenImageDenoise is greyed out, both Denoise panels say why from Cycles' own device listing
  (``CyclesPreferences.oidn_gpu_state``): a GPU here supports it but is not chosen (where to choose it), or none here
  does (it runs on the CPU). Never a list of GPU names.
* The reworded and new text is in Angelo's catalogue (``sources.EXTRA_MSGIDS``), since Blender's has only its own.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "intern/cycles/blender/addon"
sys.path.insert(0, str(ROOT / "scripts/angelo"))
from i18n import sources  # noqa: E402


def tree(name):
    return ast.parse((ADDON / name).read_text(encoding="utf-8"))


def function(module, name, owner=None):
    nodes = module.body
    if owner:
        nodes = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == owner).body
    return next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name)


def description(name):
    """The description of the enum item a denoiser function returns."""
    node = function(tree("properties.py"), name)
    item = next(n for n in ast.walk(node) if isinstance(n, ast.Tuple) and len(n.elts) == 4)
    text = item.elts[2]
    if isinstance(text, ast.Call):   # n_("...")
        text = text.args[0]
    return ast.literal_eval(text)


def device(kind, oidn, driver_ok=True):
    """A ``_cycles.available_devices`` tuple: (name, type, id, ..., [5] OIDN, [6] OptiX denoiser, [7] optimized,
    [8] meets the driver requirement, [9] DLSS)."""
    return ("GPU", kind, kind + "-0", False, False, oidn, False, True, driver_ok, False)


class DeviceState:
    """``oidn_gpu_state`` run on its own source against a fake ``_cycles``."""

    def state(self, lists, in_use=False):
        method = function(tree("properties.py"), "oidn_gpu_state", owner="CyclesPreferences")
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "properties.py", "exec"), namespace)
        fake = types.ModuleType("_cycles")
        fake.available_devices = lambda kind: lists.get(kind, [("CPU", "CPU", "cpu", False, False, True, False,
                                                                 True, True, False)])
        sys.modules["_cycles"] = fake
        self.addCleanup(sys.modules.pop, "_cycles", None)
        prefs = types.SimpleNamespace(has_oidn_gpu_devices=lambda: in_use)
        return namespace["oidn_gpu_state"](prefs)


class DescriptionTests(unittest.TestCase):
    def test_openimagedenoise_is_for_every_vendor_and_the_cpu(self):
        text = description("enum_openimagedenoise_denoiser")
        for word in ("AMD", "Apple", "Intel", "NVIDIA", "CPU"):
            with self.subTest(word=word):
                self.assertIn(word, text)
        self.assertNotIn("Intel OpenImageDenoise", text)

    def test_dlss_says_what_it_needs_and_where_it_works(self):
        text = description("enum_dlss_denoiser")
        self.assertIn("NVIDIA RTX", text)
        self.assertIn("Rendered viewport", text)


class StateTests(DeviceState, unittest.TestCase):
    def test_a_gpu_in_use_needs_no_line(self):
        self.assertEqual(self.state({"METAL": [device("METAL", True)]}, in_use=True), 'USED')

    def test_a_supported_gpu_not_chosen_is_available_on_any_backend(self):
        for kind in ("CUDA", "OPTIX", "HIP", "METAL", "ONEAPI"):
            with self.subTest(kind=kind):
                self.assertEqual(self.state({kind: [device(kind, True)]}), 'AVAILABLE')

    def test_no_supported_gpu_means_the_cpu(self):
        self.assertEqual(self.state({}), 'NONE')
        self.assertEqual(self.state({"HIP": [device("HIP", False)]}), 'NONE', "a GPU OIDN does not support")
        self.assertEqual(self.state({"CUDA": [device("CUDA", True, driver_ok=False)]}), 'NONE', "a driver too old")


class PanelTests(unittest.TestCase):
    def test_both_denoise_panels_say_why_use_gpu_is_greyed(self):
        module = tree("ui.py")
        for panel in ("CYCLES_RENDER_PT_sampling_viewport_denoise", "CYCLES_RENDER_PT_sampling_render_denoise"):
            with self.subTest(panel=panel):
                draw = function(module, "draw", owner=panel)
                calls = {n.func.id for n in ast.walk(draw) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
                self.assertIn("draw_oidn_gpu_hint", calls)

    def test_the_lines_name_where_to_choose_the_gpu(self):
        hint = ast.unparse(function(tree("ui.py"), "draw_oidn_gpu_hint"))
        self.assertIn("Preferences > System > Cycles Render Devices", hint)
        self.assertIn("'AVAILABLE'", hint)
        self.assertIn("'NONE'", hint)


class CatalogueTests(unittest.TestCase):
    def test_the_new_text_is_in_angelos_catalogue(self):
        listed = {msgid for _ctxt, msgid, _origin in sources.EXTRA_MSGIDS}
        texts = [description("enum_openimagedenoise_denoiser"), description("enum_dlss_denoiser")]
        hint = function(tree("ui.py"), "draw_oidn_gpu_hint")
        texts += [ast.literal_eval(n.args[0]) for n in ast.walk(hint)
                  if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "rpt_"]
        self.assertEqual(len(texts), 4)
        for text in texts:
            with self.subTest(text=text[:40]):
                self.assertIn(text, listed)


if __name__ == "__main__":
    unittest.main()
