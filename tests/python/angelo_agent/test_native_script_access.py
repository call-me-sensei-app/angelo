# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What Angelo tells the person an agent script can reach, wherever it offers to run scripts without asking: one
sentence (``script_access.SENTENCE``) that makes no claim the confinement as it ships does not hold, and one link to
the published limits (``LIMITS_URL``, a section at the top of doc/angelo/script_confinement.rst naming every route
still open). The 2.1.0 break-it review's A-8 found cards and toggles each saying it their own way; the re-read of
dd87b2d5aa3 (S-1) found the Trust scripts dialog and the Preferences and AI Providers toggles without the sentence or
the link. No Blender."""

import suite_isolation  # noqa: F401

import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import chat_cards, script_access  # noqa: E402
from angelo_agent.ui_text import iface_  # noqa: E402

def wrap(text, width, size, font):
    return [text]


class ScriptAccessTests(unittest.TestCase):
    def test_the_sentence_claims_nothing_the_confinement_does_not_guarantee(self):
        """Re-reads of 1c2c4edaf48 and ccdfea52edb, M-1, and the coordinator's ruling (2026-11-20): what the
        confinement holds as it ships has documented routes around it (bundled ffmpeg reaches URLs, files other
        programs run stay writable, in-memory keys are readable), so the consent sentence makes no "can't" claim. It
        says plainly what scripts can do and asks for the person's own judgement. A claim needs an in-app proof."""
        sentence = script_access.SENTENCE
        for word in ("can't", "cannot", "never", "can not", "unable"):
            self.assertNotIn(word, sentence.lower(), "an absolute claim")
        self.assertIn("Only allow scripts you would run yourself.", sentence)
        self.assertIn(script_access.LIMIT, sentence)
        self.assertEqual(script_access.CLAIMS, (), "a claim goes here only with an in-app test that proves it")

    def test_the_limits_link_opens_the_published_limits(self):
        from angelo_agent.markdown_text import safe_url
        self.assertTrue(script_access.LIMITS_URL.endswith(
            "doc/angelo/script_confinement.rst#" + script_access.LIMITS_ANCHOR))
        self.assertTrue(safe_url(script_access.LIMITS_URL), "the chat opens only safe links")

    def test_the_linked_section_comes_first_and_names_every_route_still_open(self):
        """Re-read of dd87b2d5aa3, S-2: the link opened the whole page, whose lead and refusal list claim what the
        sentence no longer does, and the residual risks started at line 875; bundled ffmpeg's URLs were in none of it.
        The link opens a section at the top that names every route a script still has."""
        doc = (ROOT / "doc/angelo/script_confinement.rst").read_text(encoding="utf-8")
        title = "What agent scripts can still reach"
        self.assertEqual(script_access.LIMITS_ANCHOR, title.lower().replace(" ", "-"))
        start = doc.index("\n" + title + "\n" + "-" * len(title) + "\n")
        self.assertLess(start, doc.index("How it works\n"), "the first section a reader meets")
        section = doc[start:doc.index("How it works\n")]
        for route in script_access.ROUTES:
            with self.subTest(route=route):
                self.assertIn("**" + route, section)
        lead = doc[:start]
        for claim in ("can't", "cannot", "never"):
            self.assertNotIn(claim, lead.lower(), "the lead claims nothing the section contradicts")

    def test_every_limit_in_the_body_names_its_route_and_every_route_has_its_entry(self):
        """Re-read of 70142a371dd, S-1 and S-2: the first section was checked only against itself, so it missed two
        routes the body listed (Angelo's running state; removals) and the body had no entry for two it named (ffmpeg's
        URLs, Linux audio servers). Each bullet under "What is not covered (residual risk)" and "Known limits in 2.0.0"
        carries a marker, "(Route: <a first-section route>.)" or "(Not a route: <why>.)"; each route has at least one."""
        import re
        doc = (ROOT / "doc/angelo/script_confinement.rst").read_text(encoding="utf-8")
        first = doc[doc.index("\nWhat agent scripts can still reach\n"):doc.index("How it works\n")]
        for route in script_access.ROUTES:
            self.assertIn("* **" + route + ".**", first, "a bold lead of its own: " + route)
        body = doc[doc.index("\nWhat is not covered (residual risk)\n"):doc.index("\nResults\n")]
        bullets = re.findall(r"^\* \*\*.+?(?=^\* \*\*|^\S|\Z)", body, re.M | re.S)
        self.assertGreater(len(bullets), 40)
        used = set()
        for bullet in bullets:
            with self.subTest(bullet=bullet[:60]):
                marks = re.findall(r"\((Route|Not a route): ([^)]+?)\.\)", " ".join(bullet.split()))
                self.assertEqual(len(marks), 1, "exactly one marker")
                kind, name = marks[0]
                if kind == "Route":
                    self.assertIn(name, script_access.ROUTES)
                    used.add(name)
        self.assertEqual(used, set(script_access.ROUTES), "every route has its entry in the body")

    def test_the_bodys_network_claims_name_ffmpegs_file_only_rule(self):
        """ffmpeg's own opens raise no audit event: each network claim says what stops them instead (every FFmpeg
        open allows only the file protocol, "Network through FFmpeg"), and the URL route is no longer listed."""
        doc = (ROOT / "doc/angelo/script_confinement.rst").read_text(encoding="utf-8")
        body = doc[doc.index("How it works\n"):]
        for claim in ("An agent script can't use the network.", "**The network.**"):
            line = " ".join(body[body.index(claim):body.index(claim) + 600].split())
            self.assertIn("ffmpeg", line, claim)
            self.assertIn("allows only the ``file`` protocol", line, claim)
        self.assertNotIn("fix is in progress", doc[:doc.index("How it works\n")])
        self.assertNotIn("image sequence", doc[:doc.index("How it works\n")], "image sequences are read by imbuf")

    def test_every_control_that_widens_script_consent_shows_the_sentence_and_the_link(self):
        """One registry of the controls that let scripts run without asking (``SURFACES``); each shows the sentence
        and offers the link. The script card's rows are proven behaviourally above and below."""
        def body(module, name):
            tree = ast.parse((ADDON / module).read_text(encoding="utf-8"))
            node = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
            return ast.unparse(node)
        self.assertEqual({surface for surface, _module, _names, _grants in script_access.SURFACES},
                         {"script card", "Trust scripts for this chat", "Approval Policy menu", "Preferences toggle",
                          "AI Providers toggle"})
        for surface, module, names, _grants in script_access.SURFACES:
            with self.subTest(surface=surface):
                code = "\n".join(body(module, name) for name in names)
                if surface.endswith("toggle"):   # The toggle's own description (its tooltip and the page's line).
                    code += body("ui.py", "ANGELO_Preferences")
                self.assertIn("script_access.", code)
                if surface != "Approval Policy menu":   # A menu row: its dialog says the sentence before any grant.
                    self.assertIn("SENTENCE", code)
                self.assertTrue("LIMITS_URL" in code or "script_limits" in code, "the link")
        for stale in ("full Angelo privileges", "unrestricted Blender Python", "full access to files"):
            self.assertNotIn(stale, (ADDON / "trusted_scripts_ui.py").read_text(encoding="utf-8"))

    @staticmethod
    def _names_set_enabled(node):
        return isinstance(node, ast.Attribute) and node.attr == "set_enabled" and \
            getattr(node.value, "id", "") == "always_allow"

    def test_every_way_to_turn_standing_script_consent_on_is_a_registered_surface(self):
        """Re-read of 70142a371dd, N-2: SURFACES was pinned by its own names, so a new path that turns standing script
        consent on (a caller of set_trusted_scripts(True) or always_allow.set_enabled, or a new drawing of the
        Always allow scripts switch) would not fail. They are discovered from the add-on's source; each must be a
        surface's registered grant or drawing, and each registered one must still exist."""
        grants, drawings = set(), set()
        for path in sorted(ADDON.glob("*.py")):
            if path.name == "always_allow.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for function in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
                for node in ast.walk(function):
                    if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "set_trusted_scripts" and \
                            node.args and getattr(node.args[0], "value", None) is True:
                        grants.add("%s:%s" % (path.name, function.name))
                    if isinstance(node, ast.Call):   # always_allow.set_enabled called, or handed on with its value.
                        args = list(node.args)
                        if self._names_set_enabled(node.func):
                            value = args[0] if args else None
                        else:
                            at = next((i for i, a in enumerate(args) if self._names_set_enabled(a)), None)
                            value = args[at + 1] if at is not None and at + 1 < len(args) else None
                            if at is None:
                                continue
                        if getattr(value, "value", None) is not False:   # Turning it off grants nothing.
                            grants.add("%s:%s" % (path.name, function.name))
                    if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "prop" and any(
                            getattr(a, "value", None) == "always_allow_scripts" for a in node.args):
                        drawings.add((path.name, function.name))
                    if isinstance(node, ast.Dict) and any(getattr(k, "value", None) == "value" and
                                                          getattr(v, "value", None) == "always_allow_scripts"
                                                          for k, v in zip(node.keys, node.values)):
                        drawings.add((path.name, function.name))
        registered = {g for _s, _m, _n, listed in script_access.SURFACES for g in listed}
        self.assertEqual(grants, registered, "every grant point a surface reaches, and only those")
        surfaces = {(module, name) for _s, module, names, _g in script_access.SURFACES for name in names}
        self.assertLessEqual(drawings, surfaces, "every drawing of the switch is a surface")
        self.assertTrue(drawings)

    def test_the_ai_providers_link_opens_only_the_limits(self):
        host = (ADDON / "providers_page_host.py").read_text(encoding="utf-8")
        self.assertIn("elif action in providers_page_actions.OWN:", host)
        actions = (ADDON / "providers_page_actions.py").read_text(encoding="utf-8")
        self.assertIn('elif action == "script_limits":', actions)
        self.assertIn("wm.url_open(url=script_access.LIMITS_URL)", actions)

    def test_the_limits_link_is_offered_only_with_a_page_to_open(self):
        from unittest import mock
        controller = SimpleNamespace(offers_remember_script=lambda call: False, offers_session_allow=lambda call: True,
                                     offers_always_allow=lambda call: False)
        call = SimpleNamespace(id="call-1")
        with mock.patch.object(script_access, "LIMITS_URL", ""):
            self.assertNotIn("url", [button[0] for button in chat_cards.always_allow_button(controller, call)])
        with mock.patch.object(script_access, "LIMITS_URL", "https://example.com/limits"):
            links = [button for button in chat_cards.always_allow_button(controller, call) if button[0] == "url"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0][2], "https://example.com/limits")

    def test_every_offer_to_run_scripts_without_asking_says_what_they_can_reach(self):
        call = SimpleNamespace()
        never = dict(offers_remember_script=lambda call: False, offers_session_allow=lambda call: False,
                     offers_always_allow=lambda call: False)
        offers = {"remember": dict(never, offers_remember_script=lambda call: True),
                  "session_allow": dict(never, offers_session_allow=lambda call: True),
                  "always_allow": dict(never, offers_always_allow=lambda call: True)}
        for name, answers in offers.items():
            with self.subTest(offer=name):
                rows = chat_cards.always_allow_rows(SimpleNamespace(**answers), call, wrap, 300, 12, None)
                self.assertIn(iface_(script_access.SENTENCE), rows)

    def test_the_preferences_toggle_says_it_too(self):
        tree = ast.parse((ADDON / "ui.py").read_text(encoding="utf-8"))
        keyword = next(keyword for node in ast.walk(tree) if isinstance(node, ast.AnnAssign)
                       and getattr(node.target, "id", "") == "always_allow_scripts"
                       for keyword in node.annotation.keywords if keyword.arg == "description")
        self.assertIn("script_access", ast.unparse(keyword.value))


if __name__ == "__main__":
    unittest.main()
