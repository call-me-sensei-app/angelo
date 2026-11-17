# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""After a crash in the graphics driver while Angelo drew with Vulkan, it offers to restart with OpenGL.

Blender 5.3 moves every Windows x64 preference file to Vulkan, upgraders included, while 1.0.x ran on OpenGL; a
driver that crashes on Vulkan left a person with a ``.cmd`` file to find (5.3 review R13). Covered (no Blender):

* ``crash_gpu.offers_opengl``: only when this launch draws with Vulkan and the crashed thread, or the module that
  faulted, is the Vulkan loader, a graphics driver or Blender's own Vulkan backend;
* the crash card shows the reason and **Restart with OpenGL** in its first question, and the button restarts;
* a crash the card does not ask about (reports turned off by policy, never, or sent automatically) gets one notice
  with the same button;
* the restart chooses OpenGL in the preferences, saves them, starts Angelo again and quits this one;
* the new text is translatable.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import crash_fixtures as cfx  # noqa: E402
import test_native_notice_cards as cards  # noqa: E402  (its fake view, flow and plan helpers)
from angelo_agent import crash_card, crash_gpu, crash_log  # noqa: E402

WINDOWS_ROOTS = ("C:\\Program Files\\Angelo\\5.3",)


def parsed(text):
    return crash_log.parse(text, WINDOWS_ROOTS)


DRIVER = cfx.WINDOWS   # The crashed thread runs through nvoglv64.dll.
NO_GPU = cfx.WINDOWS.replace("nvoglv64.dll        :0x00007FFA11223344  Symbols not available\n", "")
BACKEND = cfx.WINDOWS.replace("nvoglv64.dll        :0x00007FFA11223344  Symbols not available",
                              "Angelo.exe          :0x00007FF6A1B2C600  blender::gpu::VKContext::flush_render_graph "
                              "C:\\build-machine\\angelo\\source\\blender\\gpu\\vulkan\\vk_context.cc:300")
LOADER = cfx.WINDOWS.replace("Exception Module      : C:\\Users\\alice\\AppData\\Local\\Programs\\Angelo\\Angelo.exe",
                             "Exception Module      : C:\\Windows\\System32\\amdvlk64.dll").replace(
                             "nvoglv64.dll        :0x00007FFA11223344  Symbols not available\n", "")


class OfferTests(unittest.TestCase):
    def test_offered_for_a_crash_in_the_driver_the_loader_or_blenders_vulkan_backend(self):
        for name, text in (("driver frame", DRIVER), ("Blender's Vulkan code", BACKEND), ("faulting module", LOADER)):
            with self.subTest(name):
                self.assertTrue(crash_gpu.offers_opengl(parsed(text), "VULKAN"))

    def test_never_for_another_crash_or_another_backend(self):
        self.assertFalse(crash_gpu.offers_opengl(parsed(NO_GPU), "VULKAN"))
        for backend in ("OPENGL", "METAL", "", None):
            self.assertFalse(crash_gpu.offers_opengl(parsed(DRIVER), backend))


class CardTests(unittest.TestCase, cards.PlanChecks):
    REGION = cards.CrashCardTests.REGION

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.restarted = []
        self.actions = SimpleNamespace(save=lambda: None, open_problem=lambda url: None, copy=lambda text: None,
                                       finished=lambda: None, stop_automatic=lambda: None,
                                       restart_opengl=lambda: self.restarted.append(True))

    def card(self, gpu):
        flow = cards.make_flow(self.folder.name)
        flow.gpu_restart = gpu
        return crash_card.CrashCard(flow, self.actions)

    def plan(self, card):
        return card.layout(cards.VIEW, self.REGION[0], self.REGION[1], 1.0, cards.kit.UiState())

    def test_the_question_says_why_and_offers_opengl(self):
        card = self.card(True)
        plan = self.plan(card)
        self.assertIn("Restart with OpenGL", cards.labels(plan))
        self.assertTrue(any("Vulkan" in text for text in cards.texts(plan)))
        self.check_plan(plan, self.REGION)
        closed = []
        card.act("opengl", None, SimpleNamespace(close=closed.append, redraw=lambda: None, after=lambda s, f: None))
        self.assertEqual(self.restarted, [True])

    def test_no_offer_without_a_driver_crash_on_vulkan(self):
        plan = self.plan(self.card(False))
        self.assertNotIn("Restart with OpenGL", cards.labels(plan))
        self.assertFalse(any("Vulkan" in text for text in cards.texts(plan)))
        self.card(False).act("opengl", None, SimpleNamespace(close=lambda how: None))
        self.assertEqual(self.restarted, [])


class RestartTests(unittest.TestCase):
    def test_opengl_is_chosen_saved_then_angelo_starts_again_and_this_one_quits(self):
        calls = []
        system = SimpleNamespace(gpu_backend="VULKAN")
        timers = []
        bpy = SimpleNamespace(
            context=SimpleNamespace(preferences=SimpleNamespace(system=system)),
            ops=SimpleNamespace(wm=SimpleNamespace(save_userpref=lambda: calls.append(("save", system.gpu_backend)),
                                                   quit_blender=lambda: calls.append(("quit",)))),
            app=SimpleNamespace(binary_path="C:\\Program Files\\Angelo\\Angelo.exe",
                                timers=SimpleNamespace(register=lambda f, first_interval=0: timers.append(f))))
        started = []
        crash_gpu.restart_with_opengl(bpy, popen=lambda command, **options: started.append((command, options)),
                                      platform="win32")
        self.assertEqual(calls, [("save", "OPENGL")])
        self.assertEqual(started[0][0], ["C:\\Program Files\\Angelo\\Angelo.exe"])
        flags = started[0][1]["creationflags"]
        self.assertTrue(flags & getattr(subprocess, "DETACHED_PROCESS", 0x8))
        for timer in timers:
            timer()
        self.assertEqual(calls[-1], ("quit",))


class WiringTests(unittest.TestCase):
    def test_the_crash_handler_offers_it_on_the_card_and_otherwise_as_a_notice(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/crash_ui.py").read_text(encoding="utf-8")
        self.assertIn("crash_gpu.offers_opengl(", source)
        self.assertIn("gpu_restart", source)
        self.assertIn("_offer_opengl(", source)
        self.assertIn("def restart_opengl(self)", source)

    def test_the_text_is_translatable(self):
        tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent/crash_card.py").read_text(encoding="utf-8"))
        marked = {n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call)
                  and getattr(n.func, "id", "") == "n_" and n.args and isinstance(n.args[0], ast.Constant)}
        self.assertIn("Restart with OpenGL", marked)
        self.assertTrue(any("Vulkan" in text for text in marked))


if __name__ == "__main__":
    unittest.main()
