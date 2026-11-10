# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Feedback is one click away, and no donation link is reachable (doc/angelo/app_identity.rst, "Links"). No Blender.

Covered: ``angelo_identity.feedback_url`` puts only the allowed details in the link's fragment (never the query a
server sees), drops empty ones, caps each, and refuses unknown kinds and details; ``feedback_details`` fills in the
version, build (no branch), system and, for a bug only, the graphics card, and nothing from the person's home, user
or computer name; the Help menu and the chat bar's + menu both reach the form for each kind, and the agent's
``FEATURES`` lookup names where. No donation link: the URL table has no donation destination, the URL presets have
no ``FUND`` type, and no Angelo-drawn UI (startup menus, the add-on, the identity, the Linux metainfo) carries a
Donate, Support Blender or Development Fund link, while Blender's credits, licence and website stay in About.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import os
import re
import sys
import types
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/modules"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import angelo_identity  # noqa: E402
from angelo_agent import chat_destinations as cd, feature_guide  # noqa: E402


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def fragment(link):
    base, _, frag = link.partition("#")
    return base, dict(urllib.parse.parse_qsl(frag, keep_blank_values=True)), list(urllib.parse.parse_qsl(frag))


class FeedbackLinkTests(unittest.TestCase):
    def test_details_travel_in_the_fragment_only(self):
        link = angelo_identity.feedback_url("bug", version="Angelo 1.0.8 (Blender 5.2.2)", os="macOS 26.1 (arm64)",
                                            gpu="Apple M4 Pro", build="2026-11-01 12:00, a486d96a9e8", report="")
        base, values, ordered = fragment(link)
        self.assertEqual(base, angelo_identity.url("feedback"))
        self.assertNotIn("?", base, "nothing in the query a web server logs")
        self.assertEqual(ordered[0], ("kind", "bug"))
        self.assertEqual(values["os"], "macOS 26.1 (arm64)")
        self.assertNotIn("report", values, "an empty detail is left out")
        self.assertNotIn("+", link.partition("#")[2], "spaces are %20, as URLSearchParams and the form expect")

    def test_each_detail_is_capped_and_unknown_ones_refused(self):
        _, values, _ = fragment(angelo_identity.feedback_url("feature", version="x" * 1000))
        self.assertEqual(len(values["version"]), 300)
        with self.assertRaises(ValueError):
            angelo_identity.feedback_url("praise")
        with self.assertRaises(ValueError):
            angelo_identity.feedback_url("bug", path="/Users/someone/scene.blend")
        self.assertEqual(set(angelo_identity.FEEDBACK_FIELDS), {"version", "build", "os", "gpu", "report"})

    def test_the_app_fills_in_its_version_build_and_system_and_nothing_personal(self):
        home = "/Users/feedback-test-person"
        bpy = types.SimpleNamespace(app=types.SimpleNamespace(
            version_string="5.2.2 LTS", build_commit_date=b"2026-11-01", build_commit_time=b"12:00",
            build_hash=b"a486d96a9e844079831330968fe727eebf54a36c", build_branch=b"integration/v1.0.8-rc1"))
        gpu = types.SimpleNamespace(platform=types.SimpleNamespace(
            renderer_get=lambda: "Apple M4 Pro", vendor_get=lambda: "Apple", version_get=lambda: "4.1 Metal",
            backend_type_get=lambda: "METAL"))
        stubs = {"bpy": bpy, "gpu": gpu, "_bpy": types.SimpleNamespace(_ghost_backend=lambda: "DEFAULT")}
        with mock.patch.dict(sys.modules, stubs), mock.patch.dict(os.environ, {"HOME": home, "USER": "feedback-test"}):
            sys.modules.pop("_bpy_internal.system_info.url_prefill_runtime", None)
            from _bpy_internal.system_info import url_prefill_runtime as runtime
            bug, feature = runtime.feedback_details("bug"), runtime.feedback_details("feature")
            link = runtime.url_from_blender("feature")
        self.assertEqual(set(bug), {"version", "build", "os", "gpu"})
        self.assertEqual(set(feature), {"version", "build", "os"}, "a feature request carries no graphics card")
        self.assertEqual(bug["version"], "Angelo %s (Blender 5.2.2 LTS)" % angelo_identity.APP_VERSION)
        self.assertEqual(bug["build"], "2026-11-01 12:00, a486d96a9e8")
        self.assertEqual(bug["gpu"], "Apple M4 Pro Apple 4.1 Metal")
        for value in bug.values():
            for private in (home, "feedback-test", "integration/", os.path.expanduser("~")):
                self.assertNotIn(private, value)
        self.assertEqual(fragment(link)[1]["kind"], "feature")


class ReachTests(unittest.TestCase):
    def test_help_and_the_plus_menu_reach_the_form_for_each_kind(self):
        wm = read("scripts/startup/bl_operators/wm.py")
        for preset in ("'BUG'", "'FEATURE'"):
            self.assertIn(preset, wm)
        self.assertIn('url_from_blender("feature")', wm)
        help_menu = read("scripts/startup/bl_ui/space_topbar.py")
        self.assertIn(".type = 'FEATURE'", help_menu)
        self.assertIn("report_bug", cd.TOP_LEVEL)
        self.assertIn("request_feature", cd.TOP_LEVEL)
        source = read("scripts/addons_core/angelo_agent/feedback_ui.py")
        self.assertIn('properties=(("kind", "BUG"),)', source)
        self.assertIn('properties=(("kind", "FEATURE"),)', source)
        self.assertIn("feedback_ui.register()", read("scripts/addons_core/angelo_agent/__init__.py"))
        self.assertIn("feedback_ui.unregister()", read("scripts/addons_core/angelo_agent/__init__.py"))

    def test_the_agent_can_point_people_to_it(self):
        guide = {item.id: item for item in feature_guide.offered(set())}["feedback"]
        self.assertEqual(set(guide.menu), {"report_bug", "request_feature"})
        for words in ("Report a bug", "Request a feature", "Help"):
            self.assertIn(words, guide.reach)
        # The click opens the browser: every row and tooltip says so, so it never surprises anyone.
        source = read("scripts/addons_core/angelo_agent/feedback_ui.py") + read("scripts/startup/bl_operators/wm.py")
        self.assertEqual(source.count("on angelo.studio"), 5)
        entry = feature_guide.lookup(["feedback"], set())[0]["features"][0]
        self.assertIn("suggest_when", entry)


# What a donation link looks like, in a URL or a label people read. The heart icon ('FUND') alone is not a link: the
# paid-generation button uses it.
DONATION_URL = re.compile(r"fund\.blender\.org|blender\.org/(?:fund|donate|join)|/donate\b|donation", re.I)
DONATION_TEXT = re.compile(r"\bDonate\b|Support Blender|Development Fund", re.I)
UI_SOURCES = ("scripts/startup/bl_ui", "scripts/startup/bl_operators", "scripts/addons_core/angelo_agent",
              "scripts/modules/angelo_identity.json", "scripts/modules/_bpy_internal/system_info",
              "release/freedesktop/angelo.metainfo.xml.in", "source/blender/windowmanager/intern")


def strings(path):
    """The string constants a Python file's code carries (labels, URLs, ``n_`` text), without its comments."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body
                  and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings]


class NoDonationLinkTests(unittest.TestCase):
    """Maintainer, 2026-11-01: no donation link in Angelo; the app does not ask people for donations."""

    def test_the_url_table_has_no_donation_destination(self):
        for key, value in angelo_identity.url_table().items():
            self.assertNotRegex(key, r"donat|fund", key)
            self.assertIsNone(DONATION_URL.search(value), key)
        self.assertNotIn("donate", angelo_identity.pending_urls())

    def test_no_preset_or_ui_text_offers_a_donation(self):
        wm = read("scripts/startup/bl_operators/wm.py")
        presets = wm[wm.index("    preset_items = ["):wm.index("    @staticmethod\n    def lookup_url_from_type")]
        self.assertNotIn("'FUND'", presets)
        for entry in UI_SOURCES:
            target = ROOT / entry
            files = [target] if target.is_file() else sorted(p for p in target.rglob("*")
                                                             if p.suffix in {".py", ".cc", ".c", ".hh", ".h"})
            for path in files:
                with self.subTest(path=str(path.relative_to(ROOT))):
                    texts = strings(path) if path.suffix == ".py" else [path.read_text(encoding="utf-8")]
                    if path.suffix not in {".py", ".json"}:   # C++ and XML: only literals and markup matter.
                        texts = [re.sub(r"//[^\n]*|/\*.*?\*/|<!--.*?-->", "", text, flags=re.S) for text in texts]
                    if path.suffix == ".json":
                        data = angelo_identity.data()
                        texts = list(data["urls"].values()) + list(data["pending_urls"])
                    for text in texts:
                        self.assertIsNone(DONATION_URL.search(text), text[:200])
                        self.assertIsNone(DONATION_TEXT.search(text), text[:200])

    def test_blender_stays_credited_in_about(self):
        about = read("scripts/addons_core/angelo_agent/about_card.py")
        for action in ('"credits"', '"license"', '"blender"', "BASED_ON"):
            self.assertIn(action, about)
        for key in ("credits", "license", "blender_website"):
            self.assertIn(key, angelo_identity.url_table())


if __name__ == "__main__":
    unittest.main()
