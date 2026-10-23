# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The welcome's feature slideshow (``welcome_slides``, ``welcome_art``), on a stand-in ``bpy``.

Covered: the slides, their order and their copy (doc/angelo/studio_entry_points.rst: the chat first, then Media
Studio, Generation Flows and the assets); every slide laid out whole and fitting the welcome in English, German,
French, Russian and Arabic (the real catalogues) at two screen sizes, at one height for every slide, with Try it,
the arrows and the dots inside the box and apart; Try it is secondary, so Start creating stays the one primary
button; the arrow keys mirror in a right-to-left language; every picture stays inside its box; and the wiring:
``intro`` runs the clock and keys and hands Try it to the shared openers. The running app's proof is
``test_blender_welcome_slides``.
"""

import ast
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object, Menu=object)
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text))
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props}


with mock.patch.dict(sys.modules, fake_bpy()):
    from angelo_agent import ui_text, welcome_art, welcome_screen, welcome_slides

SIZES = ((1100, 860), (900, 700), (1100, 1400))   # A laptop, a small window (compact), a tall view.
LANGUAGES = ("en", "de", "fr", "ru", "ar")


class View:
    """Measures as a proportional font roughly does; wraps greedily."""
    _font = _mono = 0

    def measure(self, text, size, font=0):
        return len(text) * size * 0.55

    def wrap(self, text, width, size, font):
        lines, line = [], ""
        for word in text.split():
            trial = (line + " " + word).strip()
            if line and self.measure(trial, size) > width:
                lines.append((line, 0))
                line = word
            else:
                line = trial
        return lines + [(line, 0)]


def studio_tooltip():
    with mock.patch.dict(sys.modules, fake_bpy()):
        from angelo_agent import studio_toggle
    return studio_toggle.OPEN


def catalogue(code):
    from i18n import po
    if code == "en":
        return {}
    path = ROOT / "locale/angelo" / ("src/ar.po" if code == "ar" else "po/%s.po" % code)   # Arabic in reading order.
    _, entries = po.read(str(path))
    return {(entry.ctxt, entry.id): entry.str for entry in entries if entry.str and not entry.obsolete}


def plan(code, slide=0, width=1100, height=860):
    table = catalogue(code)

    def iface(text, context=""):
        return table.get((context or "", text)) or table.get(("", text)) or text
    with mock.patch.object(welcome_screen, "iface_", iface), mock.patch.object(welcome_slides, "iface_", iface), \
            mock.patch.object(welcome_screen, "shortcut_keys", lambda: ["Ctrl", "Alt", "M"]):
        return welcome_screen.layout(View(), width, height, 1.0, slide=slide), iface


def ops(result, kind):
    return [op for op in result["ops"] if op[0] == kind]


def overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


class SlideTests(unittest.TestCase):
    def test_slides_in_the_order_a_new_person_needs_them(self):
        with mock.patch.object(welcome_slides, "_shelf_opener", lambda: print):
            self.assertEqual(welcome_slides.keys(), ["chat", "studio", "flows", "assets"])
        copy = {key: rest for key, *rest in welcome_slides.ALL_SLIDES}
        # The spec's copy, word for word (studio_entry_points.rst, "First run"), and its length limits.
        self.assertEqual(copy["studio"][2], "Open Media Studio")
        self.assertEqual(copy["studio"][3], studio_tooltip())
        self.assertEqual(copy["flows"][2], "Open Generation Flows")
        for title, line, pill, tip in copy.values():
            self.assertLessEqual(len(title), 34, title)
            self.assertLessEqual(len(line), 100, line)
        self.assertEqual(set(welcome_slides.OPENERS), {slide[0] for slide in welcome_slides.ALL_SLIDES})
        self.assertEqual(set(welcome_art.PICTURES), set(welcome_slides.OPENERS))

    def test_the_assets_slide_shows_only_where_its_shelf_tab_opens(self):
        with mock.patch.object(welcome_slides, "_shelf_opener", lambda: None):
            self.assertEqual(welcome_slides.keys(), ["chat", "studio", "flows"])
            result, _ = plan("en", 5)
            self.assertEqual(ops(result, "slide_button")[0][2], "Open Generation Flows")
            self.assertEqual(len(ops(result, "slide_dot")), 3)
        with mock.patch.object(welcome_slides, "_shelf_opener", lambda: print):
            self.assertEqual(len(ops(plan("en", 0)[0], "slide_dot")), 4)

    def test_it_advances_every_dwell_holds_stops_for_good_and_rests_on_the_last_slide(self):
        advance, dwell = welcome_slides.auto_next, welcome_slides.DWELL
        self.assertEqual(dwell, 6.5)
        self.assertEqual(advance(0, 4, dwell, False, False, False), 1, "after 6.5 s, the next slide")
        self.assertIsNone(advance(0, 4, dwell - 0.1, False, False, False), "not before")
        self.assertIsNone(advance(0, 4, 60, True, False, False), "held under the pointer or focus")
        self.assertIsNone(advance(0, 4, 60, False, True, False), "never under Reduced Motion")
        self.assertIsNone(advance(1, 4, 60, False, False, True), "stopped for good once the person navigated")
        self.assertIsNone(advance(3, 4, 60, False, False, False), "rests on the last slide, no loop")
        self.assertEqual(advance(1, 3, dwell, False, False, False), 2)
        self.assertIsNone(advance(2, 3, 60, False, False, False))
        self.assertTrue(welcome_slides.held(True, None))
        self.assertTrue(welcome_slides.held(False, ("slide_try", "studio")))
        self.assertTrue(welcome_slides.held(False, ("slide_dot", 2)))
        self.assertFalse(welcome_slides.held(False, ("start", None)), "focus elsewhere in the card lets it run")
        intro = (ADDON / "intro.py").read_text()
        for navigation in ('show_slide(_open["slide"] + change, by_person=True)',
                           'show_slide(_open["slide"] + welcome_slides.key_step(event.type), by_person=True)'):
            self.assertIn(navigation, intro)
        clicks = intro.split('if action in {"slide_prev", "slide_next", "slide_dot"}:', 1)[1].split("return", 1)[0]
        self.assertIn("by_person=True", clicks, "an arrow or a dot stops it too")
        self.assertIn("welcome_slides.auto_next(", intro)

    def test_the_assets_pill_says_it_switches_to_manual_in_director_only(self):
        """The label is chosen as the slide draws, from the window's mode, so it is never stale after a switch."""
        def shown(code, here):
            table = catalogue(code)

            def iface(text, context=""):
                return table.get((context or "", text)) or table.get(("", text)) or text
            with mock.patch.object(welcome_slides, "_shelf_opener", lambda: print), \
                    mock.patch.object(welcome_slides, "iface_", iface), \
                    mock.patch.object(welcome_screen, "iface_", iface), \
                    mock.patch.object(welcome_screen, "shortcut_keys", lambda: ["Ctrl", "Alt", "M"]):
                result = welcome_screen.layout(View(), 1100, 860, 1.0, hover=("slide_try", "assets"), here=here,
                                               slide=3)
            (button,) = ops(result, "slide_button")
            return button[2], ops(result, "slide_tip")[-1][2], iface
        for code in ("en", "de", "ar"):
            with self.subTest(code=code):
                label, tip, iface = shown(code, "director")
                self.assertEqual((label, tip), tuple(iface(text) for text in welcome_slides.DIRECTOR_ASSETS))
                label, tip, iface = shown(code, "standard")
                self.assertEqual((label, tip), (iface("Show my assets"),
                                                iface("Show the Asset Shelf on its Angelo Media Studio tab")))
        self.assertEqual(welcome_slides.DIRECTOR_ASSETS[0], "Show in Manual")
        draw = (ADDON / "intro.py").read_text().split("\ndef _draw(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('here = "director" if is_director(bpy.context.window) else "standard"', draw,
                      "the mode is read on every draw")

    def test_a_sideways_swipe_changes_one_slide(self):
        with mock.patch.object(ui_text, "is_rtl", lambda: False):
            self.assertEqual(welcome_slides.swipe_step(0.0, -30, 2), (None, -30))
            self.assertEqual(welcome_slides.swipe_step(-30.0, -40, 0), (1, 0.0), "fingers left: the next slide")
            self.assertEqual(welcome_slides.swipe_step(0.0, 70, 0), (-1, 0.0))
            self.assertEqual(welcome_slides.swipe_step(40.0, 5, 50), (None, 0.0), "a vertical pan is no swipe")
        with mock.patch.object(ui_text, "is_rtl", lambda: True):
            self.assertEqual(welcome_slides.swipe_step(0.0, -70, 0), (-1, 0.0))

    def test_every_slide_is_whole_and_fits_in_every_language(self):
        for code in LANGUAGES:
            for width, height in SIZES:
                heights = set()
                for slide, (key, title, line, pill, _) in enumerate(welcome_slides.slides()):
                    with self.subTest(code=code, size=(width, height), slide=key):
                        result, iface = plan(code, slide, width, height)
                        heights.add(round(result["height"], 3))
                        self.assertLessEqual(result["height"], height - 32 * result["scale"])
                        s, box = result["scale"], result["slides"]
                        within = [op for op in ops(result, "text") if box[1] <= op[3] <= box[1] + box[3]]
                        title_rows = [op[1] for op in within if op[4] == 17 * s]
                        body_rows = [op[1] for op in within if op[4] == 13.5 * s]
                        self.assertEqual(" ".join(title_rows), iface(title), "the headline, whole")
                        expected = welcome_slides.DIRECTOR_ASSETS[0] if key == "assets" else pill   # In Director.
                        self.assertEqual(ops(result, "slide_button")[0][2], iface(expected))
                        self.assertEqual(" ".join(body_rows).split(), iface(line).split(), "the line, whole")
                        pictures = ops(result, "slide_picture")
                        self.assertEqual(len(pictures), 0 if result["compact"] else 1)
                        (button,) = ops(result, "slide_button")
                        arrows = [op[1] for op in ops(result, "slide_arrow")]
                        inside = lambda r: (box[0] <= r[0] and r[0] + r[2] <= box[0] + box[2] + 0.01   # noqa: E731
                                            and box[1] <= r[1] and r[1] + r[3] <= box[1] + box[3] + 0.01)
                        for rect in [p[2] for p in pictures] + [button[1]] + arrows + [
                                op[1] for op in ops(result, "slide_dot")]:
                            self.assertTrue(inside(rect), rect)
                        self.assertLess(button[1][0] + button[1][2], min(a[0] for a in arrows), "Try it clear of ‹ ›")
                        for op in within:
                            if op[4] in (17 * s, 13.5 * s):
                                end = op[2] + View().measure(op[1], op[4])
                                self.assertLessEqual(end, box[0] + box[2])
                                for picture in pictures:
                                    self.assertGreater(op[2], picture[2][0] + picture[2][2], "text beside the picture")
                                if result["compact"] and op[4] == 17 * s:
                                    self.assertLess(end, button[1][0], "the headline clear of Try it")
                self.assertEqual(len(heights), 1, "every slide at one height: changing slides moves nothing")

    def test_each_pill_names_where_it_goes_is_secondary_and_every_control_is_a_hit(self):
        result, _ = plan("en", 2)
        (button,) = ops(result, "slide_button")
        self.assertEqual(button[2], "Open Generation Flows")
        self.assertAlmostEqual(button[1][3], 28 * result["scale"], msg="BUTTON_HEIGHT_SMALL")
        widths = {ops(plan("en", i)[0], "slide_button")[0][1][2] for i in range(len(welcome_slides.slides()))}
        self.assertEqual(len(widths), 1, "every pill one width: changing slides moves nothing")
        english = mock.patch.object(welcome_slides, "iface_", lambda text, context="": text)
        with english, mock.patch.object(welcome_screen, "iface_", lambda text, context="": text), \
                mock.patch.object(welcome_screen, "shortcut_keys", lambda: ["Ctrl", "Alt", "M"]):
            hovered = welcome_screen.layout(View(), 1100, 860, 1.0, hover=("slide_try", "flows"), slide=2)
            pressed = welcome_screen.layout(View(), 1100, 860, 1.0, slide=2, pressed=("slide_try", "flows"))
            tip = welcome_slides.tooltip("flows")
        self.assertEqual(ops(hovered, "slide_tip")[-1][2], tip)
        self.assertEqual(hovered["ops"][-1][0], "slide_tip", "the tooltip draws over everything")
        self.assertEqual(ops(pressed, "slide_button")[0][3], "pressed")
        hits = {(action, value) for _, action, value in result["hits"]}
        self.assertIn(("slide_try", "flows"), hits)
        self.assertTrue({("slide_prev", None), ("slide_next", None), ("slide_dot", 0), ("slide_dot", 1)} <= hits)
        self.assertNotIn(("slide_dot", 2), hits, "the current slide's dot is not a control")
        source = (ADDON / "welcome_slides.py").read_text()
        draw = source.split('kind == "slide_button"', 1)[1].split("elif", 1)[0]
        self.assertNotIn("TEAL", draw.replace("kit.TEAL_SOFT if state == \"pressed\"", ""),
                         "Start creating stays the welcome's one teal button (pressed: the spec's TEAL_SOFT)")
        self.assertIn(("start", None), {(action, value) for _, action, value in result["hits"]})

    def test_arrow_keys_follow_the_layout_direction(self):
        with mock.patch.object(ui_text, "is_rtl", lambda: False):
            self.assertEqual([welcome_slides.key_step(k) for k in ('LEFT_ARROW', 'RIGHT_ARROW', 'A')], [-1, 1, None])
        with mock.patch.object(ui_text, "is_rtl", lambda: True):
            self.assertEqual([welcome_slides.key_step(k) for k in ('LEFT_ARROW', 'RIGHT_ARROW')], [1, -1])
        last = len(welcome_slides.slides()) - 1
        self.assertEqual(welcome_slides.step(last, 1), 0)
        self.assertEqual(welcome_slides.step(0, -1), last)

    def test_every_picture_stays_inside_its_box_in_card_colours(self):
        for key in welcome_slides.keys():
            for scale in (1.0, 2.0):
                rect = (100.0, 50.0, 204 * scale, 204 * scale * 0.58)
                points = []

                class Pen:
                    def shape(self, x, y, w, h, color, radius=0):
                        points.extend([(x, y), (x + w, y + h)])

                    def stroke(self, x1, y1, x2, y2, color, thickness=1):
                        points.extend([(x1, y1), (x2, y2)])

                def poly(vertices, color):
                    points.extend(vertices)

                def icon(kind, x, y, size, alpha=1.0):
                    points.extend([(x, y), (x + size, y + size)])
                    icons.append(kind)
                icons = []
                with self.subTest(key=key, scale=scale), mock.patch.object(welcome_art, "poly", poly), \
                        mock.patch.object(welcome_art, "icon", icon):
                    welcome_art.draw(Pen(), key, rect, scale, 1.0)
                    self.assertGreater(len(points), 40, "a picture, not a placeholder")
                    for x, y in points:
                        self.assertTrue(rect[0] - 1 <= x <= rect[0] + rect[2] + 1 and rect[1] - 1 <= y <= rect[1] + rect[3] + 1,
                                        (key, x, y))
                    # The spec's concept icons: Media Studio's spark frame, Generation Flows' three nodes.
                    self.assertEqual(icons, {"studio": ["studio"], "flows": ["flows"]}.get(key, []))

    def test_intro_runs_the_clock_and_keys_and_hands_try_it_to_the_openers(self):
        intro = (ADDON / "intro.py").read_text()
        self.assertIn("welcome_slides.key_step(event.type)", intro)
        self.assertIn("welcome_slides.open_feature(context.window, value)", intro)
        self.assertIn("bpy.app.timers.register(_tick", intro)
        self.assertIn("bpy.app.timers.unregister(_tick)", intro)
        self.assertIn('_open["over_slides"] = False', intro, "leaving the 3D view lets the slideshow run on")
        source = (ADDON / "welcome_slides.py").read_text()
        tree = ast.parse(source)
        bodies = {node.name: ast.get_source_segment(source, node) for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("bpy.ops.angelo.open_media_studio(kind='AUTO')", bodies["_open_studio"])
        flows = bodies["_open_flows"]
        self.assertIn("bpy.ops.angelo.show_flows()", flows, "Manual: the Generation Flows tab")
        self.assertIn('getattr(flow_menu, "open_from_top_bar", None)', flows, "Director: anchored under Flows")
        self.assertIn("studio_workspaces.in_manual(window)", flows)
        assets = bodies["_open_assets"]
        self.assertEqual(welcome_slides.SHELF_OPENER, ("media_shelf", "show_studio_tab"), "part D's call")
        self.assertIn('director.request_mode(window, "MANUAL")', assets, "Director shows no shelf")
        self.assertIn("not opener(target)", assets, "a False from the shelf is retried, then left")
        chat = bodies["_open_chat"]
        self.assertIn("chat_toggle.show(window)", chat)
        self.assertIn("bpy.ops.angelo.chat_example(index=0)", chat, "the empty chat's own example mechanism")
        self.assertLess(chat.index("if ui.connection_available():"), chat.index("bpy.ops.angelo.chat_example"),
                        "before a connection the waiting field takes no text")
        self.assertNotIn("send", chat.lower().replace("unsent", ""), "a pill never sends a message")
        self.assertIn("bpy.app.timers.register(run", bodies["open_feature"], "after the welcome's handler has ended")


if __name__ == "__main__":
    unittest.main()
