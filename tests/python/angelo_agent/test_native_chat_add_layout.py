# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's + menu puts every feature in view without scrolling, in every language (``chat_add_layout``). No Blender.

1.0.8 regression this pins: the menu was a list of two-line rows whose groups opened inline and stayed open across
launches; at a 1440 x 900 screen it showed 10 of its 19 features, 6 with Generation Flows open, and a GUI suite failed
when a new row pushed Workflows below the fold. Covered here, with every feature the add-on's modules register (read
from their source) and their real translations (``locale``), measured with the advance widths of the fonts the canvas
draws with (``fixtures/menu_font_advances.json``):

* at a 1440 x 900 and a 1280 x 800 screen's chat, every top-level feature (``chat_destinations.TOP_LEVEL``) has a tile,
  button or footer link wholly inside the menu, in all 21 languages, and the menu does not scroll;
* opening a group never moves the top level (a group is a page: the same popup, the same tiles when it closes);
* no title is cut: one line or two whole-word lines, the whole row when half is too narrow; the strip shows every
  description whole;
* the two-line breaker balances lines, breaks Chinese and Japanese only where a line may break, and only at the
  catalogue's word marks when a title has them.
"""

import functools
import json
import math
import sys
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))
sys.path.insert(0, str(HERE))
from angelo_agent import chat_add_layout as layout, chat_add_nav as nav, chat_destinations as cd  # noqa: E402
from angelo_agent import rtl_text, text_layout  # noqa: E402
from i18n import languages, po  # noqa: E402
from plus_menu_features import shipped_features  # noqa: E402

ADVANCES = json.loads((HERE / "fixtures/menu_font_advances.json").read_text(encoding="utf-8"))["advances"]
# A chat column as Angelo lays it out (width, height above its bottom edge, the composer's top + 8): measured at a
# 1440 x 875 window (a 1440 x 900 screen less the menu bar), and the same layout at 1280 x 775 (1280 x 800).
SCREENS = {"1440x900": (502, 820, 126), "1280x800": (446, 720, 126)}


def advance(character):
    found = ADVANCES.get(format(ord(character), "x"))
    if found is not None:
        return found
    code = ord(character)
    if unicodedata.combining(character) or unicodedata.category(character) in ("Mn", "Me", "Cf"):
        return 0
    if 0xAC00 <= code <= 0xD7AF:
        return ADVANCES["hangul"]
    if unicodedata.east_asian_width(character) in ("W", "F"):
        return ADVANCES["han"]
    return 650   # A glyph the table lacks: wider than an average Latin letter.


def measure(text, size):
    """Glyph advances are whole pixels at the drawn size (``chat_view.measure``): each is rounded up here, so a title
    that fits this model fits the canvas."""
    return sum(math.ceil(size * advance(character) / 1000) for character in text if advance(character))


def wrap(text, width, size):
    lines, rest = [], text
    while rest:
        low, high = 1, len(rest)
        while low < high:
            middle = (low + high + 1) // 2
            low, high = (middle, high) if measure(rest[:middle], size) <= width else (low, middle - 1)
        cut = text_layout.line_break(rest, low)
        lines.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip(" ")
    return lines


def reading(lines):
    """Drawn lines as the text they read, without spaces or word marks: Arabic lines are stored and drawn in visual
    order, each on its own, so each is turned back to reading order first."""
    text = "".join(rtl_text.to_logical(line) if rtl_text.is_visual(line) else line for line in lines)
    return "".join(text.split()).replace("​", "")


def catalogue(path):
    if not path.exists() or path.read_bytes().startswith(b"version https://git-lfs"):
        return {}
    _header, entries = po.read(str(path))
    return {entry.id: entry.str for entry in entries if entry.str and not entry.obsolete and not entry.ctxt}


ENGLISH = "en_US"
LANGUAGE_CODES = (ENGLISH,) + tuple(language.code for language in languages.LANGUAGES)   # The source and its 20.


@functools.lru_cache(maxsize=None)
def translator(code):
    """Text as the canvas shows it in ``language`` (``ui_text.iface_``: Blender's catalogue asked first, then Angelo's;
    ``n_`` constants carry no context)."""
    if code == ENGLISH:
        return lambda message: message
    language = languages.BY_CODE[code]
    blender = catalogue(ROOT / "locale/po" / (language.po + ".po"))
    angelo = catalogue(ROOT / "locale/angelo/po" / (language.po + ".po"))
    return lambda message: blender.get(message) or angelo.get(message) or message


def items(translate, worker=False):
    return [dict(entry, title=translate(entry["label"]), why=translate(entry.get("subtitle") or ""),
                 section=translate(entry["section"])) for entry in cd.entries(worker)]


class Registered(unittest.TestCase):
    def setUp(self):
        saved = dict(cd._features)
        self.addCleanup(lambda: (cd._features.clear(), cd._features.update(saved)))
        for feature in shipped_features():
            cd.register(feature)


class FoldTests(Registered):
    def test_every_feature_is_in_view_without_scrolling_on_common_laptops_in_every_language(self):
        self.assertEqual(len(LANGUAGE_CODES), 21)
        for code in LANGUAGE_CODES:
            translate = translator(code)
            shown = items(translate)
            self.assertEqual({entry["feature"] for entry in shown}, set(cd.TOP_LEVEL), "one entry per feature")
            for screen, (width, height, bottom) in SCREENS.items():
                with self.subTest(language=code, screen=screen):
                    geometry = layout.place(shown, measure, wrap, width, height, bottom, translate("Search Angelo"))
                    self.assertEqual(geometry["max_scroll"], 0, "the top level never scrolls")
                    self.assertTrue(geometry["fits"])
                    popup = geometry["rect"]
                    found = {shown[cell["index"]]["feature"]: (cell["rect"], geometry["list_rect"])
                             for cell in geometry["cells"]}
                    found.update({shown[i]["feature"]: (rect, popup) for i, rect in geometry["attach"]})
                    found.update({shown[i]["feature"]: (rect, popup) for i, rect in geometry["links"]})
                    for feature in cd.TOP_LEVEL:
                        rect, area = found[feature]
                        self.assertTrue(layout.in_view(rect, area), "%s is below the fold: %s" % (feature, rect))
                    self.assertLessEqual(popup[1] + popup[3], height - layout.TOP_MARGIN + 0.5, "inside the chat")

    def test_no_title_or_description_is_cut_in_any_language(self):
        for code in LANGUAGE_CODES:
            translate = translator(code)
            shown = items(translate)
            for screen, (width, height, bottom) in SCREENS.items():
                geometry = layout.place(shown, measure, wrap, width, height, bottom)
                for cell in geometry["cells"]:
                    item, (_, _, cw, _) = shown[cell["index"]], cell["rect"]
                    room = cw - layout.TITLE_X - (layout.CHEVRON if item["kind"] == "group" else layout.END)
                    with self.subTest(language=code, screen=screen, feature=item["feature"]):
                        self.assertEqual(reading(cell["lines"]), reading([item["title"]]), "every character drawn")
                        for line in cell["lines"]:
                            self.assertLessEqual(measure(line, layout.TITLE), room + 0.5, line)
                strip = geometry["strip_lines"]
                for item in shown:
                    if item["placement"] == "grid":
                        self.assertLessEqual(len(wrap(layout.describe(item), geometry["rect"][2] - 28, layout.WHY)),
                                             strip, "%s's description fits the strip" % item["feature"])

    def test_the_footer_and_the_bar_hold_their_features(self):
        shown = items(lambda message: message)
        placed = {entry["feature"]: entry["placement"] for entry in shown}
        self.assertEqual(placed["attach"], "bar")
        self.assertEqual((placed["report_bug"], placed["request_feature"]), ("footer", "footer"))
        groups = [entry["feature"] for entry in shown if entry["kind"] == "group"]
        self.assertTrue(groups and all(placed[group] == "grid" for group in groups), "a group is a tile")
        with self.assertRaises(ValueError):
            cd.register(cd.Feature("x", "X", children=lambda chosen, fit: [], placement="footer"))

    def test_a_workers_chat_reflows_without_the_lead_only_tools(self):
        shown = items(lambda message: message, worker=True)
        self.assertNotIn("base_mesh", {entry["feature"] for entry in shown})
        geometry = layout.place(shown, measure, wrap, *SCREENS["1280x800"])
        self.assertEqual(geometry["max_scroll"], 0)

    def test_a_narrow_chat_falls_back_to_one_column_and_only_then_scrolls(self):
        shown = items(lambda message: message)
        narrow = layout.place(shown, measure, wrap, 300, 500, 126)
        self.assertEqual(narrow["columns"], 1)
        self.assertGreater(narrow["max_scroll"], 0)
        self.assertEqual(layout.place(shown, measure, wrap, 300, 500, 126, scroll=10 ** 6)["scroll"], narrow["max_scroll"])
        self.assertEqual(layout.place(shown, measure, wrap, *SCREENS["1280x800"])["columns"], 2)

    def test_the_menu_is_only_as_tall_as_its_content(self):
        shown = items(lambda message: message)
        short = layout.place(shown, measure, wrap, 502, 820, 126)
        tall = layout.place(shown, measure, wrap, 502, 2000, 126)
        self.assertEqual(short["rect"][3], tall["rect"][3])
        self.assertLess(short["rect"][3], 820 - 126 - layout.TOP_MARGIN)

    def test_a_page_uses_the_same_popup_and_its_rows_scroll_inside_it(self):
        shown = items(lambda message: message)
        top = layout.place(shown, measure, wrap, *SCREENS["1280x800"])
        page = layout.stack([46] * 30, top["list_rect"], 10 ** 6, header=layout.PAGE_HEADER)
        self.assertGreater(page["max_scroll"], 0)
        self.assertEqual(page["scroll"], page["max_scroll"])
        lx, ly, lw, lh = top["list_rect"]
        self.assertEqual(page["header"][1] + page["header"][3], ly + lh, "the header sits at the list's top")
        self.assertTrue(layout.in_view(page["rows"][-1], page["area"]), "scrolled to its end, the last row shows")


class KeyTests(unittest.TestCase):
    """The launcher's keys (break-it review, 1.0.8): a stop activates only with the keyboard on it; arrows move by
    column and mirror in a right-to-left interface; the list scrolls to the keyboard's stop before the next draw."""

    def press(self, popup, kind, rtl=False, text=""):
        from types import SimpleNamespace
        event = SimpleNamespace(type=kind, unicode=text, ctrl=False, oskey=False, shift=False)
        return nav.key(popup, event, rtl=rtl)

    def stops(self):
        return [("popup_add_attach", "attach", -1, 1), ("popup_add_open", "a", 0, 0), ("popup_add_open", "b", 0, 1),
                ("popup_add_group", "g", 1, 0)]

    def test_return_does_nothing_until_the_keyboard_is_on_a_stop(self):
        popup = {"stops": self.stops(), "cursor": 2, "keyboard": False}   # The model list's cursor, say.
        self.assertIsNone(self.press(popup, 'RET'), "a stale cursor never fires an unseen tile")
        self.press(popup, 'DOWN_ARROW')
        self.assertEqual(popup["cursor"], 0, "the first key starts at the first stop, not the stale index")
        self.assertEqual(self.press(popup, 'RET'), {"action": "popup_add_attach", "value": "attach"})
        popup["keyboard"] = False   # The pointer moved: it has the focus now.
        self.assertIsNone(self.press(popup, 'RET'))

    def test_the_first_key_on_a_page_lands_on_its_first_row(self):
        page = {"stops": [("popup_add_attach", "attach", -1, 1), ("popup_add_back", "g", -1, 0),
                          ("popup_add_pick", "m", 0, 0)], "page": "g", "query": ""}
        self.press(page, 'DOWN_ARROW')
        self.assertEqual(page["stops"][page["cursor"]][1], "m")

    def test_left_and_right_follow_the_reading_direction(self):
        popup = {"stops": self.stops(), "cursor": 1, "keyboard": True}
        self.press(popup, 'RIGHT_ARROW')
        self.assertEqual(popup["cursor"], 2)
        self.press(popup, 'RIGHT_ARROW', rtl=True)   # Mirrored grid: Right moves toward column 0.
        self.assertEqual(popup["cursor"], 1)
        page = {"stops": [("popup_add_attach", "attach", -1, 1), ("popup_add_back", "g", -1, 0)], "cursor": 1,
                "keyboard": True, "page": "g", "query": ""}
        self.press(page, 'RIGHT_ARROW', rtl=True)
        self.assertIsNone(page["page"], "in a right-to-left interface Right goes back")

    def test_the_list_scrolls_to_a_stop_below_the_fold_before_the_next_draw(self):
        area = (0, 100, 300, 200)
        rows = [("popup_add_pick", n, n, 0, (0, 300 - 46 * (n + 1), 300, 46)) for n in range(10)]
        popup = {"stops": rows, "cursor": 3, "keyboard": True, "scroll": 0,
                 "layout": {"area": area, "max_scroll": 260, "drawn_scroll": 0}}
        self.press(popup, 'DOWN_ARROW')
        self.press(popup, 'DOWN_ARROW')   # A second key before any redraw.
        _, ry, _, rh = rows[5][4]
        self.assertEqual(popup["cursor"], 5)
        self.assertGreaterEqual(ry + popup["scroll"], area[1], "the stop is whole in view")


class RegistryTests(Registered):
    def test_a_group_found_by_search_shows_a_closed_chevron_and_a_row_says_where_it_lives(self):
        shown = lambda row, key: row.get(key) if isinstance(row.get(key), str) else ""  # noqa: E731
        found = nav.search("render", (), "", False, shown)
        self.assertTrue(found)
        self.assertFalse(any(row.get("open") for row in found))
        inside = [row for row in found if row.get("feature") in cd.PARENTS and row["kind"] == "row"]
        self.assertTrue(inside and all(row["subtitle"] and row.get("shown_subtitle") for row in inside))

    def test_search_finds_english_and_product_names_in_any_language(self):
        """People type a feature's English name in a translated interface ("render" in Japanese found nothing)."""
        translate = translator("ja")
        shown = lambda row, key: translate(row.get(key)) if isinstance(row.get(key), str) else ""  # noqa: E731
        found = [row.get("feature") for row in nav.search("render", (), "", False, shown)]
        self.assertIn("render_tools", found)
        self.assertTrue(nav.search("レンダー", (), "", False, shown), "and its own words still match")

    def test_only_the_grids_headings_carry_over(self):
        cd.register(cd.Feature("zz_tile", "Z", operator="angelo.z", order=1))   # After Attach, with no heading.
        entry = next(entry for entry in cd.entries() if entry["value"] == "zz_tile")
        self.assertNotEqual(entry["section"], cd.ATTACH)

    def test_the_strip_holds_every_text_it_shows_and_one_row_of_tiles_always_stays(self):
        shown = items(lambda message: message)
        note = "word " * 120
        geometry = layout.place(shown, measure, wrap, *SCREENS["1440x900"], notes=(note,))
        self.assertGreaterEqual(geometry["strip_lines"], len(wrap(note, geometry["rect"][2] - 28, layout.WHY)))
        tiny = layout.place(shown, measure, wrap, 230, 300, 120)
        self.assertGreaterEqual(tiny["list_rect"][3], layout.PAGE_HEADER + layout.PAGE_ROW)


class CrossingTests(unittest.TestCase):
    """qa_plus_menu.crossings, the GUI suites' check that no text the menu draws crosses from one band into another
    (1.0.8 cut 03cb462ddb5: a scrolled page's rows ran over its pinned toggle and the strip, the clip never on)."""

    def view(self):
        layout = {"rect": (12, 120, 420, 540), "list_rect": (20, 204, 404, 412), "area": (20, 204, 404, 376),
                  "strip": (12, 156, 420, 44), "search": (20, 618, 300, 34)}   # Header 580-616 above the list.
        return {"popover": {"kind": "add", "layout": layout}}

    def test_a_row_over_the_strip_is_caught_and_a_clipped_one_is_not(self):
        import qa_plus_menu
        row = ("Body Female – Stylized", (70, 196, 160, 14))   # Its lower part below the list, over the strip.
        self.assertEqual(len(qa_plus_menu.crossings(self.view(), [row + (None,)])), 1)
        self.assertEqual(qa_plus_menu.crossings(self.view(), [row + ((20, 204, 404, 376),)]), [])

    def test_an_invisible_cut_out_is_caught_and_a_visible_one_is_not(self):
        import qa_plus_menu
        from angelo_agent import chat_add_menu, ui_tokens
        ground = (0.165, 0.165, 0.165, 1)
        disc = ("shape", (7, 7, 18, 18), ui_tokens.TEAL)
        before = [disc, ("shape", (8.6, 8.6, 14.8, 14.8), ui_tokens.TEAL_SOFT),
                  ("stroke", (8, 15, 16, 1.4), ui_tokens.TEAL)]   # 1.0.8's web glyph: a solid disc.
        self.assertEqual(len(qa_plus_menu.invisible(before, ground)), 2)
        after = [disc, ("shape", (8.6, 8.6, 14.8, 14.8), chat_add_menu.OPENING), ("stroke", (8, 15, 16, 1.4), ui_tokens.TEAL)]
        self.assertEqual(qa_plus_menu.invisible(after, ground), [])

    def test_text_past_the_popups_sides_is_caught(self):
        import qa_plus_menu
        records = [("Angelo Link zu einem Unreal-Engine-Projekt hinzufügen", (70, 300, 400, 14), None)]
        self.assertEqual(len(qa_plus_menu.overflowing(self.view(), records)), 1)
        self.assertEqual(qa_plus_menu.overflowing(self.view(), [("Web-Export", (70, 300, 80, 14), None)]), [])

    def test_a_row_over_a_pinned_toggle_is_caught(self):
        import qa_plus_menu
        row = ("Head – Generic Topology", (70, 575, 160, 14))   # Across the list's top into the page's header.
        self.assertEqual(len(qa_plus_menu.crossings(self.view(), [row + (None,)])), 1)
        self.assertEqual(qa_plus_menu.crossings(self.view(), [("Match the size of Cube", (68, 590, 160, 13), None)]), [])


class TwoLinesTests(unittest.TestCase):
    def width(self, text):
        return measure(text, layout.TITLE)

    def test_one_line_when_it_fits_else_balanced_whole_words(self):
        self.assertEqual(text_layout.two_lines("Web export", self.width, 200), ["Web export"])
        self.assertEqual(text_layout.two_lines("Live-Link zur Game-Engine", self.width, 110),
                         ["Live-Link zur", "Game-Engine"])

    def test_two_texts_join_in_reading_order(self):
        first, second = rtl_text.to_visual("تدفقات"), rtl_text.to_visual("التوليد")
        self.assertEqual(rtl_text.to_logical(layout.joined(first, second)), "تدفقات · التوليد")
        self.assertEqual(layout.joined("Video", "A clip"), "Video · A clip")

    def test_arabic_breaks_in_reading_order(self):
        title = "تدفقات التوليد"   # "Generation Flows": the first word belongs on the top line.
        lines = text_layout.two_lines(rtl_text.to_visual(title), self.width, self.width(rtl_text.to_visual(title)) * 0.7)
        self.assertEqual([rtl_text.to_logical(line) for line in lines], ["تدفقات", "التوليد"])

    def test_no_break_fits_is_none_never_a_cut_word(self):
        self.assertIsNone(text_layout.two_lines("Live-webbläsarförhandsvisning", self.width, 100))

    def test_japanese_breaks_at_its_marks_and_never_before_a_long_vowel(self):
        marked = "ライブ​ブラウザー​プレビュー"
        lines = text_layout.two_lines(marked, self.width, 110)
        self.assertEqual([line.replace("​", "") for line in lines], ["ライブブラウザー", "プレビュー"])
        unmarked = text_layout.two_lines("ライブブラウザープレビュー", self.width, 120)
        self.assertFalse(any(line[0] in "ーァィゥェォッャュョ" for line in unmarked), unmarked)


if __name__ == "__main__":
    unittest.main()
