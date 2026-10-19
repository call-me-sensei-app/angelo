# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The not-connected showcase over the 3D view (``connect_hero``): its media slot and lifecycle, without Blender.

The window suite ``test_blender_connect_hero`` shows it with screenshots.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
SOURCE = (ADDON / "connect_hero.py").read_text()


class MediaTests(unittest.TestCase):
    def test_one_table_a_bundled_still_now(self):
        self.assertIn('MEDIA = ({"kind": "image", "file": "hero-stone-bridge.webp"', SOURCE)
        still = ADDON / "assets/hero-stone-bridge.webp"
        self.assertTrue(still.is_file())
        self.assertLess(still.stat().st_size, 400_000, "A few hundred KB at most in the add-on")

    def test_a_video_entry_shows_its_poster_until_it_plays(self):
        self.assertIn('return ASSETS / (entry["file"] if entry["kind"] == "image" else entry["poster"])', SOURCE)

    def test_captions_are_translatable_whole_sentences(self):
        for marker in ('n_("Made with Angelo")', 'n_("Connect an AI account in the chat to start creating.")',
                       'n_("Explore the empty scene")'):
            self.assertIn(marker, SOURCE)


class LifecycleTests(unittest.TestCase):
    def test_it_shows_only_while_an_empty_chat_has_no_connection(self):
        self.assertIn('return not controller.state["messages"] and not ui.connection_available()', SOURCE)

    def test_it_fades_then_releases_everything(self):
        release = SOURCE.split("\ndef _release():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("draw_handler_remove", release)
        self.assertIn("_state.clear()", release, "Pixels and the GPU texture go with the state")
        tick = SOURCE.split("\ndef _tick():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_release()", tick)
        self.assertIn("return 1 / 60", tick, "It redraws on its own only while it fades")

    def test_the_welcome_screen_and_cards_draw_over_it(self):
        draw = SOURCE.split("\ndef _draw():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if area is not None and not _yields(area.as_pointer()):", draw,
                      "Its own handler yields while the welcome or a card is open")
        yields = SOURCE.split("\ndef _yields(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("return intro.is_open() or card_host.shown_on(area_pointer)", yields)
        intro = (ADDON / "intro.py").read_text().split("\ndef _draw():", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(intro.index("connect_hero.paint_beneath(area, region)"), intro.index("welcome_screen.draw("),
                        "The welcome paints the showcase beneath its scrim, so the empty scene never shows")
        cards = (ADDON / "card_host.py").read_text().split("\ndef _draw():", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(cards.index("connect_hero.paint_beneath(area, region)"), cards.index("card_draw.draw("),
                        "A card paints it beneath itself too, whichever of the two opened first")
        modal = SOURCE.split("class ANGELO_OT_connect_hero", 1)[1]
        self.assertIn("if intro.is_open() or", modal)
        self.assertLess(modal.index("if card_host.takes_input("), modal.index("hit = canvas_controls.hit_at("),
                        "A card under it in the handler stack still gets its clicks")

    def test_a_drawing_failure_dismisses_it(self):
        paint = SOURCE.split("\ndef paint_beneath(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("except Exception:", paint)
        self.assertIn("_dismissed = True", paint)
        self.assertIn('_state["fading_since"] = time.monotonic() - FADE_SECONDS', paint)


class InputTests(unittest.TestCase):
    def test_the_chat_counts_it_as_a_canvas_not_a_foreground_tool(self):
        sys.path.insert(0, str(ROOT / "scripts/addons_core"))
        from angelo_agent import input_policy

        class Showcase:
            bl_idname = "angelo.connect_hero"

        class Window:
            modal_operators = (Showcase(),)

        self.assertFalse(input_policy.interaction_active(Window()), "Otherwise the connect options stop responding")

    def test_shortcuts_timers_and_the_chats_focus_keys_pass_through(self):
        modal = SOURCE.split("class ANGELO_OT_connect_hero", 1)[1]
        self.assertIn("if event.type in WINDOW_EVENTS or event.ctrl or event.oskey or function_key:", modal)
        self.assertIn("if event.type in CHAT_FOCUS_KEYS and _chat_has_focus():", modal)
        self.assertIn('CHAT_FOCUS_KEYS = frozenset({"TAB", "RET", "NUMPAD_ENTER", "SPACE", "ESC"})', SOURCE)
        self.assertIn('"TIMER"', SOURCE.split("WINDOW_EVENTS = ", 1)[1].split(")", 1)[0])

    def test_automation_turns_it_off(self):
        self.assertIn('product_env.flag("DISABLE_HERO")', SOURCE)
        runner = (Path(__file__).resolve().parent / "run_native_suites.py").read_text()
        self.assertIn('ANGELO_DISABLE_HERO="1"', runner)


if __name__ == "__main__":
    unittest.main()
