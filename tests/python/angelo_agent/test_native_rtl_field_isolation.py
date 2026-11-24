# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A filled-in value is laid out as one unit in a right-to-left sentence (``rtl_text.isolate``).

Blender draws text left to right, so Angelo puts Arabic in visual order itself (``rtl_text``). A value filled into
an Arabic template (a network share, a folder or file path, a model name) used to be laid out letter by letter with
the sentence around it, so its neutral characters at either end ("\\\\" of a share, "/" of a path, the ")" of a
name, a trailing ".") were resolved by the Arabic and jumped to the other end of the value. Each value is now one
unit whose neutrals take its own first strong direction. Checked over every Arabic catalogue template, not one card,
with values of every shape; values with no letter, and text with no value, are laid out exactly as before.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from i18n import po  # noqa: E402
from angelo_agent import canvas_rtl, rtl_isolate, rtl_text, share_refusal_notice, text_layout, ui_text  # noqa: E402

CATALOGUE = {entry.id: entry.str for entry in po.read(str(ROOT / "locale/angelo/po/ar.po"))[1]
             if entry.str and not entry.ctxt and not entry.obsolete}
FIELD = re.compile(r"\{([A-Za-z_][A-Za-z_0-9]*)([:!][^}]*)?\}")
MARKS = set("\u2066\u2067\u2068\u2069")

# Left-to-right values whose first or last characters are neutral (bidi class not L).
LATIN_VALUES = (
    "\\\\fileserver\\projects",       # A UNC share: leading backslashes.
    "/Users/a/b.blend",               # A POSIX path: leading slash.
    "C:\\x\\y",                       # A Windows path: ends with a letter, colon and backslashes inside.
    "(beta)",                         # A name in parentheses: neutral at both ends.
    "Final render v2...",             # Ends with neutrals.
    "[draft] scene_01.blend",         # Starts with a bracket, ends with a letter after digits.
    "\"quoted\"",                     # Quotes at both ends.
)
ARABIC_VALUE = "مجلد المشهد (2)"       # Starts with an Arabic letter, ends with a number in parentheses.
DIGITS = "2048"
NO_LETTER = "--- / ..."


def fill(template, **fields):
    """``iface_``'s Arabic fill of the catalogue's visual ``template``."""
    return ui_text._fill_visual(CATALOGUE[template], fields)


def before(template, **fields):
    """How the fill was laid out before values were isolated: the logical sentence put in visual order as a whole."""
    return rtl_text.to_visual(rtl_text.to_logical(CATALOGUE[template]).format(**fields))


def single_field_templates():
    """Every Arabic catalogue template with exactly one plain named field (no format spec): many unrelated cards."""
    found = []
    for message in sorted(CATALOGUE):
        fields = FIELD.findall(message)
        if len(fields) == 1 and not fields[0][1] and FIELD.findall(rtl_text.to_logical(CATALOGUE[message])) == fields:
            found.append((message, fields[0][0]))
    return found


class FilledValueTests(unittest.TestCase):
    def test_the_refused_share_card_shows_the_share_whole(self):
        share = "\\\\angelo-share-proof.invalid\\castle"
        message = ui_text.notice(share_refusal_notice.ONE, share=share)
        with patch.dict(ui_text._pgettext, {"iface": lambda text, context=None: CATALOGUE.get(text, text)}):
            shown = ui_text.display_notice(message)
        self.assertIn("«" + share + "»", shown)
        self.assertFalse(MARKS & set(shown))

    def test_every_template_shows_each_latin_value_whole(self):
        templates = single_field_templates()
        self.assertGreater(len(templates), 100)   # The class, across the catalogue, not one card.
        broken = []
        for message, name in templates:
            for value in LATIN_VALUES:
                shown = fill(message, **{name: value})
                if value not in shown or MARKS & set(shown):
                    broken.append((message, value, shown))
        self.assertEqual(broken[:3], [])

    def test_an_arabic_value_is_one_right_to_left_unit(self):
        for message, name in single_field_templates()[:200]:
            shown = fill(message, **{name: ARABIC_VALUE})
            self.assertIn(rtl_text.to_visual(ARABIC_VALUE), shown, message)

    def test_a_value_with_no_letter_is_laid_out_as_before(self):
        for message, name in single_field_templates():
            for value in (DIGITS, NO_LETTER, ""):
                self.assertEqual(fill(message, **{name: value}), before(message, **{name: value}), (message, value))

    def test_numbers_and_templates_without_a_string_are_unchanged(self):
        counted = [message for message in sorted(CATALOGUE)
                   if "{count}" in message and len(FIELD.findall(message)) == 1]
        self.assertTrue(counted)
        for message in counted:
            self.assertEqual(fill(message, count=12), before(message, count=12), message)
        for message in sorted(CATALOGUE)[:500]:
            if not FIELD.search(message):
                self.assertEqual(rtl_text.to_visual(rtl_text.to_logical(CATALOGUE[message])), CATALOGUE[message])

    def test_reading_order_comes_back_exactly(self):
        for message, name in single_field_templates()[:200]:
            for value in LATIN_VALUES + (ARABIC_VALUE,):
                logical = rtl_text.to_logical(CATALOGUE[message]).format(**{name: value})
                self.assertEqual(rtl_text.to_logical(fill(message, **{name: value})), logical, (message, value))

    def test_isolate_marks_inside_a_value_cannot_unbalance_it(self):
        message, name = "Angelo can use the folder “{name}” in this chat.", "name"
        shown = fill(message, **{name: "/srv/\u2069a\u2066b\u2067c\u2068"})
        self.assertIn("/srv/abc", shown)
        self.assertFalse(MARKS & set(shown))


class JoinTests(unittest.TestCase):
    def test_a_name_joined_to_arabic_keeps_its_brackets(self):
        label = CATALOGUE["Angelo can use the folder “{name}” in this chat."].replace("{name}", "x")
        for value in LATIN_VALUES:
            shown = ui_text.join([label, value])
            self.assertIn(value, shown)
            self.assertFalse(MARKS & set(shown))

    def test_arabic_pieces_alone_join_as_before(self):
        pieces = [CATALOGUE[message] for message in sorted(CATALOGUE) if not FIELD.search(message)][:40]
        for first, second in zip(pieces, pieces[1:]):
            expected = rtl_text.to_visual(rtl_text.to_logical(first) + " · " + rtl_text.to_logical(second))
            self.assertEqual(ui_text.join([first, second]), expected)


def pieces_in_order(rows, value):
    """``value`` found in ``rows`` (visual) as consecutive pieces, in reading order, each whole in one row."""
    found = ""
    for row in rows:
        size = len(value) - len(found)
        while size and value[len(found):len(found) + size] not in row:
            size -= 1
        found += value[len(found):len(found) + size]
        if value[len(found):len(found) + 1] == " ":   # A row cut at a space drops it.
            found += " "
    return found


class WrappedTests(unittest.TestCase):
    MESSAGE = share_refusal_notice.ONE   # A long sentence that wraps; other templates are checked above.

    def test_a_value_cut_over_rows_stays_whole_on_each_row(self):
        for value in ("\\\\fileserver\\projects\\shots\\2026\\final", "/Users/a/My Projects/shots/b.blend",
                      "C:\\Program Files\\Angelo\\x.blend"):
            shown = ui_text._fill_visual(CATALOGUE[self.MESSAGE], {"share": value})
            for width in (14, 23, 40, 200):
                rows = rtl_text.rows_to_visual(shown, len, width)
                self.assertFalse(MARKS & set("".join(rows)))
                self.assertEqual(pieces_in_order(rows, value), value, (value, width, rows))

    def test_a_two_line_label_keeps_its_value_whole(self):
        shown = fill("Angelo can use the folder “{name}” in this chat.", name="/Users/a/My Projects/shots")
        lines = text_layout.two_lines(shown, len, len(shown) // 2 + 6)   # Two lines, cut inside the path.
        self.assertIsNotNone(lines)
        self.assertFalse(MARKS & set("".join(lines)))
        self.assertEqual(pieces_in_order(lines, "/Users/a/My Projects/shots"), "/Users/a/My Projects/shots")

    def test_an_elided_value_draws_no_mark(self):
        shown = fill("Angelo can use the folder “{name}” in this chat.", name="\\\\fileserver\\projects")
        for width in (10, 20, 30, 80):
            self.assertFalse(MARKS & set(canvas_rtl.elide(shown, len, width)))

    def test_an_elided_last_row_keeps_the_start_of_the_value(self):
        from angelo_agent import studio_state
        for value in ("\\\\fileserver\\projects\\shots", "/Users/a/b/c/d.blend", "(beta) draft name"):
            shown = ui_text._fill_visual(CATALOGUE[self.MESSAGE], {"share": value})
            last = studio_state.notice_rows(shown, len, 30, max_rows=2)[-1]
            self.assertFalse(MARKS & set(last))
            self.assertIn(value[:6], last)   # The value's first characters, its leading neutrals included, in place.

    def test_one_row_is_the_whole_sentence(self):
        for value in LATIN_VALUES:
            shown = ui_text._fill_visual(CATALOGUE[self.MESSAGE], {"share": value})
            self.assertEqual(rtl_text.rows_to_visual(shown, len, 10_000), [shown])



class RememberedTests(unittest.TestCase):
    """The lines ``to_visual`` laid out with units are remembered (least recently used out) and only those drawn."""

    def test_a_line_read_back_while_many_others_are_laid_out_stays_exact(self):
        template = CATALOGUE["Angelo can use the folder “{name}” in this chat."]
        for value in ("\\\\nas\\media\\renders", "/Users/a/b.blend", "(beta)"):
            shown = ui_text._fill_visual(template, {"name": value})
            logical = rtl_text.to_logical(template).format(name=value)
            rows = rtl_text.rows_to_visual(shown, len, 30)
            for index in range(rtl_isolate._ISOLATED_LIMIT + 300):   # Other values laid out meanwhile, past the bound.
                ui_text._fill_visual(template, {"name": "folder %d" % index})
                if index % 64 == 0:   # The line still drawn reads itself back now and then (a draw, a wrap).
                    self.assertEqual(rtl_text.to_logical(shown), logical)
            self.assertLessEqual(len(rtl_isolate._ISOLATED), rtl_isolate._ISOLATED_LIMIT)
            self.assertEqual(rtl_text.to_logical(shown), logical)
            self.assertEqual(rtl_text.rows_to_visual(shown, len, 30), rows)

    def test_measured_forms_are_not_remembered(self):
        template = CATALOGUE["Angelo can use the folder “{name}” in this chat."]
        for value in LATIN_VALUES:
            shown = ui_text._fill_visual(template, {"name": value})
            for width in (12, 25, 40, len(shown) // 2 + 6):
                before = set(rtl_isolate._ISOLATED)
                drawn = [canvas_rtl.elide(shown, len, width)]
                drawn += text_layout.two_lines(shown, len, width) or []
                drawn += rtl_text.rows_to_visual(shown, len, width)
                self.assertLessEqual(set(rtl_isolate._ISOLATED) - before, set(drawn), (value, width))


class ComposerHintTests(unittest.TestCase):
    """The composer's hint cut short with "…" (``chat_composer.hint_rows``) keeps a filled-in value whole."""

    def test_a_hint_cut_short_keeps_the_start_of_its_value(self):
        """A hint keeps whole sentences that fit, so a value is cut with "…" when it is in the first sentence and
        that sentence does not fit: such templates, at several widths. Wherever the value's first letter is drawn,
        the neutrals before it (a share's "\\\\", a path's "/", a bracket, a quote) are drawn just before it."""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from angelo_agent import chat_composer
        from test_native_composer_hint import chat, measured_wrap
        cases = 0
        for message, name in single_field_templates():
            logical = rtl_text.to_logical(CATALOGUE[message])
            other = logical.replace("{" + name + "}", "")
            first_end = logical.find(". ")
            if any(ch.isascii() and ch.isalpha() for ch in other) or not 0 < logical.index("{") < first_end:
                continue   # Latin elsewhere in the sentence would hide a misplaced letter; values in a later sentence.
            for value in LATIN_VALUES:
                lead = next(index for index, ch in enumerate(value) if unicodedata.bidirectional(ch) == "L") + 1
                shown = fill(message, **{name: value})
                for width in (20, 35, 50, 80, 400):
                    rows = chat_composer.hint_rows(shown, chat(width), 12, measured_wrap, None)
                    cases += 1
                    self.assertFalse(MARKS & set("".join(rows)))
                    if value[lead - 1] in "".join(rows):
                        self.assertTrue(any(value[:lead] in row for row in rows), (message, value, width, rows))
        self.assertGreater(cases, 100)


class PresentationFormTests(unittest.TestCase):
    """Only Arabic presentation forms mark pre-shaped text: an emoji's variation selector or a byte order mark don't."""

    def test_a_value_with_a_variation_selector_or_bom_is_still_isolated(self):
        template = "Angelo can use the folder “{name}” in this chat."
        for value in ("\\\\server\\share \u2764\ufe0f", "\ufeff/Users/a", "/Users/a\ufe31", "(x)\ufe50"):
            self.assertFalse(rtl_text.is_visual(value) or ui_text._visual_rtl(value), value)
            shown = fill(template, name=value)
            self.assertIn(value, shown)
            self.assertEqual(rtl_text.to_logical(shown), rtl_text.to_logical(CATALOGUE[template]).format(name=value))

    def test_every_presentation_form_is_still_pre_shaped(self):
        for code in list(range(0xFB50, 0xFE00)) + list(range(0xFE70, 0xFEFD)):
            self.assertTrue(rtl_text.is_visual(chr(code)) and ui_text._visual_rtl(chr(code)), hex(code))


if __name__ == "__main__":
    unittest.main()
