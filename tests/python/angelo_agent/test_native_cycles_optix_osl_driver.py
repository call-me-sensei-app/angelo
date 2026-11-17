# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Open Shading Language on OptiX with an NVIDIA driver that cannot link its pipeline (Blender #163420), read
from the sources.

The drivers from 590 up to, not including, 610 fail to link the OSL shading pipeline after minutes of compiling
its kernels, with an error that names no cause. Cycles' OptiX device now reads the installed driver's version and
ends kernel loading at once with one sentence that names the fix. This file pins what a machine without an NVIDIA
GPU can check: the check runs before anything is loaded, an unknown version never refuses, the sentence is one
constant the interface can translate, every language translates it, and the Image Editor shows the translated
text. The decisions themselves are unit-tested in intern/cycles/test/device_optix_driver_test.cpp; the renders
are proven on a Tesla T4 (doc/angelo/render_engines.rst).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DRIVER = "intern/cycles/device/optix/driver.cpp"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def body(text, signature):
    return re.search(re.escape(signature) + r".*?\n\}", text, re.S).group(0)


def literals(code):
    """Each string literal of ``code``, adjacent literals joined as the compiler joins them."""
    joined = re.sub(r'"\s*\n\s*"', "", code)
    return [text.encode().decode("unicode_escape") for text in re.findall(r'"((?:[^"\\]|\\.)*)"', joined)]


def error_sentence():
    function = body(read(DRIVER), "string optix_osl_driver_error(")
    returned = [text for text in literals(function) if text]
    assert len(returned) == 1, returned
    return returned[0]


class OptiXOSLDriverTest(unittest.TestCase):
    def test_the_check_runs_before_anything_is_loaded(self):
        load = body(read("intern/cycles/device/optix/device_impl.cpp"), "bool OptiXDevice::load_kernels(")
        check = load.index("optix_osl_driver_error(nvidia_driver_version_installed(),")
        refused = load.index("set_error(driver_error);", check)
        self.assertLess(refused, load.index("return false;", refused))
        for later in ('path_get("lib/kernel_optix"', "compile_kernel(", "CUDADevice::load_kernels(",
                      "create_optix_module(", "optixModuleDestroy("):
            self.assertIn(later, load)
            self.assertLess(refused, load.index(later), later)

    def test_the_driver_library_is_loaded_only_when_osl_is_asked_for(self):
        load = body(read("intern/cycles/device/optix/device_impl.cpp"), "bool OptiXDevice::load_kernels(")
        check = load.index("optix_osl_driver_error(")
        guard = load.rindex("if (use_osl_shading || use_osl_camera) {", 0, check)
        self.assertNotIn("}", load[guard:check])

    def test_an_osl_camera_alone_is_checked_like_osl_shading(self):
        # Tesla T4, driver 596.86: node shading with a camera script fails to link like OSL shading does.
        function = body(read(DRIVER), "string optix_osl_driver_error(")
        self.assertIn("(kernel_features & (KERNEL_FEATURE_OSL_SHADING | KERNEL_FEATURE_OSL_CAMERA)) == 0", function)

    def test_an_unknown_version_never_refuses(self):
        function = body(read(DRIVER), "string optix_osl_driver_error(")
        unknown = function.index("!version.has_value()")
        self.assertLess(unknown, function.index("Open Shading Language"))
        self.assertIn('return "";', function[unknown:function.index("Open Shading Language")])

    def test_the_library_comes_from_the_system_only(self):
        source = read(DRIVER)
        self.assertIn('LoadLibraryExW(L"nvml.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32)', source)
        self.assertEqual(source.count("LoadLibrary"), 1)
        self.assertIn('dlopen("libnvidia-ml.so.1"', source)
        self.assertEqual(source.count("dlopen("), 1)

    def test_the_sentence_is_one_constant_that_names_the_fix(self):
        sentence = error_sentence()
        self.assertNotIn("%", sentence)   # No installed version in it: the interface looks the whole text up.
        self.assertIn("610", sentence)
        self.assertIn("580", sentence)
        self.assertFalse(sentence.endswith("."))   # As Blender's reports.
        self.assertNotIn("Blender", sentence)

    def test_every_language_translates_the_sentence(self):
        sys.path.insert(0, str(ROOT / "scripts/angelo"))
        from i18n import languages, po, sources
        sentence = error_sentence()
        self.assertIn(("", sentence, DRIVER), sources.EXTRA_MSGIDS)
        template = {entry.key for entry in po.read(str(sources.POT))[1] if not entry.obsolete}
        self.assertIn(("", sentence), template)
        names = ("Open Shading Language", "OptiX", "NVIDIA", "610", "580")
        for language in languages.LANGUAGES:
            entries = {entry.key: entry for entry in po.read(str(sources.LOCALE_DIR / "po" / f"{language.po}.po"))[1]
                       if not entry.obsolete}
            translated = entries[("", sentence)].str
            self.assertTrue(translated and translated != sentence, language.po)
            for name in names:
                if language.po == "ar":   # The shipped Arabic is in visual order: a name's letters run reversed.
                    self.assertTrue(name in translated or name[::-1] in translated, (language.po, name))
                else:
                    self.assertIn(name, translated, language.po)

    def test_the_report_and_the_image_editor_show_the_translated_text(self):
        # Cycles hands its error to the report (translated by BKE_report) and to the render result.
        session = body(read("intern/cycles/blender/session.cpp"),
                       "bool BlenderSession::check_and_report_session_error(")
        self.assertIn("RE_engine_report(&b_engine, 1 << 5, error.c_str());", session)
        self.assertIn("RE_engine_set_error_message(&b_engine, error.c_str());", session)
        report = body(read("source/blender/blenkernel/intern/report.cc"), "void BKE_report(ReportList *reports,")
        self.assertIn("RPT_(_message)", report)
        # The Image Editor's render line draws the render result's error: the same lookup.
        line = body(read("source/blender/editors/render/render_internal.cc"), "static void make_renderinfo_string(")
        self.assertIn("info_extra = RPT_(error);", line)
        self.assertNotIn("info_extra = error;", line)


if __name__ == "__main__":
    unittest.main()
