# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The sign-in pages the app serves on 127.0.0.1 (team_pages.py, team_page_art.py).

Every state renders the console's frame (wordmark, one panel, the next step),
self-contained under a strict Content-Security-Policy whose style hash matches,
with fields escaped, in the person's language and direction, and with the
wordmark identical to the brand asset.
"""

import base64
import hashlib
import html
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import managed_policy, team_pages  # noqa: E402
from angelo_agent.display_text import Text  # noqa: E402
from angelo_agent.team_page_art import WORDMARK  # noqa: E402
from angelo_agent.team_pages import STATES, Pages  # noqa: E402

CONSOLE = "https://console.angelo.studio"


def text_of(page):
    """The visible words: inline bold kept in its sentence, blocks separated, whitespace collapsed."""
    body = re.sub(r"</?b>", "", page.split("</style>", 1)[1])
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))


class PageTests(unittest.TestCase):
    def render(self, kind, pages=None, **fields):
        status, headers, body = (pages or Pages(CONSOLE)).render(kind, **fields)
        return status, headers, body.decode("utf-8")

    def test_every_state_is_a_complete_branded_page(self):
        expected = {"signed_in": 200, "no_seat": 200, "refused": 403, "denied": 200, "expired": 400,
                    "unreachable": 502, "error": 502, "mismatch": 400, "already_used": 200, "waiting": 200,
                    "not_found": 404}
        self.assertEqual(set(STATES), set(expected))
        for kind, code in expected.items():
            with self.subTest(state=kind):
                status, headers, page = self.render(kind, device="Mika's Mac", org="Northwind Studio",
                                                    reason=Text(managed_policy.WRONG_ORGANIZATION))
                self.assertEqual(status, code)
                self.assertTrue(page.startswith("<!doctype html><html lang=\"en\" dir=\"ltr\">"))
                self.assertIn('class="mark" role="img" aria-label="Angelo"', page)
                self.assertIn(WORDMARK, page)
                state = STATES[kind]
                words = text_of(page)
                for part in (state.eyebrow, state.title):
                    self.assertIn(part, words)
                self.assertIn("return to angelo", words.lower())
                self.assertIn("Angelo · console.angelo.studio", words)
                self.assertNotRegex(page, r"<script|<img|<link|<iframe|<form|https?://(?!www\.w3\.org|console\.angelo)")

    def test_the_outcomes_say_what_happened_and_what_to_do(self):
        words = text_of(self.render("signed_in", device="Mika's Mac", org="Northwind Studio")[2])
        self.assertIn("Angelo on Mika's Mac can now use Northwind Studio.", words)
        self.assertIn("Manage your devices", words)
        words = text_of(self.render("no_seat", device="Mac", org="Northwind",
                                    contact=Text(managed_policy.CONTACT_ADMIN_AT, contact="it@northwind.example"))[2])
        self.assertIn("you don't have a seat", words)
        self.assertIn("Contact your admin for access: it@northwind.example Then return to Angelo.", words)
        self.assertIn('<p class="action">Contact your admin for access: <b>it@northwind.example</b></p>',
                      self.render("no_seat", contact=Text(managed_policy.CONTACT_ADMIN_AT, contact="it@northwind.example"))[2])
        words = text_of(self.render("refused", reason=Text(managed_policy.WRONG_EMAIL, domains="@northwind.example"))[2])
        self.assertIn("work account (@northwind.example)", words)
        self.assertIn("Nothing was shared", words)
        self.assertIn("console.angelo.studio", text_of(self.render("unreachable")[2]))
        defaults = text_of(self.render("signed_in")[2])
        self.assertIn("Angelo on this computer can now use your team.", defaults)

    def test_fields_are_escaped(self):
        page = self.render("signed_in", device="<img src=x onerror=alert(1)>", org="A & <b>B</b>")[2]
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)
        self.assertIn("A &amp; &lt;b&gt;B&lt;/b&gt;", page)
        console = Pages('https://evil.example/"><script>')
        self.assertNotIn('"><script>', console.render("signed_in")[2].decode())

    def test_a_strict_policy_allows_exactly_the_inline_style(self):
        status, headers, page = self.render("signed_in")
        style = page.split("<style>", 1)[1].split("</style>", 1)[0]
        digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
        csp = headers["Content-Security-Policy"]
        self.assertIn("style-src 'sha256-%s'" % digest, csp)
        for directive in ("default-src 'none'", "img-src 'none'", "base-uri 'none'", "form-action 'none'",
                          "frame-ancestors 'none'"):
            self.assertIn(directive, csp)
        self.assertNotIn("script-src", csp, "no script is allowed at all (default-src 'none')")
        self.assertEqual(page.count("<style>"), 1)
        self.assertNotRegex(page, r' style="')
        self.assertEqual((headers["Referrer-Policy"], headers["Cache-Control"], headers["X-Frame-Options"]),
                         ("no-referrer", "no-store", "DENY"))
        self.assertIn("prefers-color-scheme:light", style)
        self.assertIn("max-width:480px", style)
        self.assertIn("prefers-reduced-motion", style)

    def test_the_page_is_in_the_persons_language_and_direction(self):
        catalog = {STATES["signed_in"].title: "أنت مسجل الدخول",
                   STATES["signed_in"].body: "يمكن لـ Angelo على {device} الآن استخدام {org}.",
                   STATES["signed_in"].next: "{broken} placeholder"}
        pages = Pages(CONSOLE, catalog=catalog, language="ar", direction="rtl")
        page = self.render("signed_in", pages, device="Mac", org="Northwind")[2]
        self.assertIn('<html lang="ar" dir="rtl">', page)
        self.assertIn("أنت مسجل الدخول", page)
        self.assertIn("يمكن لـ Angelo على <b>Mac</b> الآن استخدام <b>Northwind</b>.", page)
        self.assertIn("Return to Angelo to continue.", page, "a broken translation falls back to English")
        catalog, language, direction = team_pages.translations(lambda text, context=None: text if context else text.upper(),
                                                               "he_IL")
        self.assertEqual((language, direction), ("he-IL", "rtl"))
        self.assertEqual(catalog[STATES["denied"].title], STATES["denied"].title.upper())
        self.assertIn(managed_policy.WRONG_ORGANIZATION, catalog, "quoted policy messages are translated too")
        self.assertEqual(team_pages.translations(lambda text, context=None: text, "de_DE")[1:], ("de-DE", "ltr"))

    def test_a_message_marked_with_angelos_context_keeps_angelos_meaning_and_arabic_reaches_the_browser_logical(self):
        from angelo_agent import rtl_text
        finishing = STATES["waiting"].eyebrow
        blender = {(None, finishing): "Done (Blender's word)"}
        angelo = {("Angelo", finishing): rtl_text.to_visual("جارٍ الإنهاء")}

        def translate(text, context=None):
            return angelo.get((context, text)) or blender.get((context, text), text)
        catalog = team_pages.translations(translate, "ar_EG")[0]
        self.assertEqual("جارٍ الإنهاء", catalog[finishing], "Angelo's meaning, in reading order for the browser to shape")

    def test_every_template_names_its_placeholders(self):
        for template in team_pages.templates():
            with self.subTest(template=template[:40]):
                self.assertFalse(re.search(r"\{\}|\{\d+\}|%s|%d", template), "a translator must be able to reorder")

    def test_the_wordmark_is_the_brand_asset(self):
        asset = (ROOT / "release/branding/angelo/angelo-wordmark.svg").read_text().strip()
        expected = asset.replace('fill="#ffffff"', 'fill="currentColor"').replace(' width="2030" height="560"', "", 1)
        expected = expected.replace('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"',
                                    '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false"', 1)
        normalize = lambda svg: re.sub(r"\s+", " ", svg)  # noqa: E731
        self.assertEqual(normalize(WORDMARK), normalize(expected))


if __name__ == "__main__":
    unittest.main()
