# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

"""Dependency-free checks for this checkout's own coding-agent instructions."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GUIDES = ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md")


class RepositoryRulesTests(unittest.TestCase):
    def test_guides_are_owned_by_this_checkout(self):
        for name in GUIDES:
            with self.subTest(guide=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn("SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.", text)
                self.assertIn("SPDX-License-Identifier: GPL-2.0-or-later", text)
                self.assertTrue(text.endswith("\n"))
                self.assertTrue(all(line == line.rstrip() for line in text.splitlines()))

    def test_guide_links_resolve_inside_this_repository(self):
        for name in GUIDES:
            text = (ROOT / name).read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                with self.subTest(guide=name, target=target):
                    relative, _, fragment = target.partition("#")
                    self.assertNotIn("://", relative)
                    path = (ROOT / (relative or name)).resolve()
                    self.assertTrue(path.is_relative_to(ROOT.resolve()))
                    self.assertTrue(path.exists())
                    if fragment:
                        headings = re.findall(
                            r"^#{1,6} (.+)$", path.read_text(encoding="utf-8"), re.M
                        )
                        anchors = {
                            re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
                            for heading in headings
                        }
                        self.assertIn(fragment, anchors)

    def test_tool_specific_guide_delegates_to_project_rules(self):
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("[AGENTS.md](AGENTS.md)", text)
        self.assertIn("CONTRIBUTING.md#engineering-standards", text)
        self.assertLess(len(text.splitlines()), 30)

    def test_public_guides_name_no_private_infrastructure(self):
        """The guides ship with the source: no home-folder paths, cloud projects or private repositories."""
        for name in GUIDES + ("README.md", "CHANGELOG.md"):
            with self.subTest(guide=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertNotRegex(text, r"/Users/[^/\s<]+/|/home/[^/\s<]+/|C:\\Users\\[^\\\s<]+\\")
                self.assertNotRegex(text, r"\bgs://|projects/[a-z][-a-z0-9]{4,}/locations/")
                # Spelled in parts so a scan of the source for the private names finds none here.
                self.assertNotRegex(text.lower(), "angelo" r"-cloud|bitbucket\.org|pre-mvp")

    def test_project_entry_point_identifies_actual_source_and_contracts(self):
        text = " ".join((ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
        self.assertIn("built directly on Blender", text)
        for target in (
            "source/blender/",
            "scripts/addons_core/angelo_agent/",
            "tests/python/angelo_agent/",
            "doc/angelo/native_editor_build.rst",
            "doc/angelo/agent_execution.rst",
        ):
            with self.subTest(target=target):
                self.assertIn(f"]({target})", text)
        self.assertIn("CONTRIBUTING.md#engineering-standards", text)

    def test_shared_policy_covers_requested_engineering_standards(self):
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        headings = set(re.findall(r"^#{2,4} (.+)$", text, re.M))
        self.assertTrue(
            {
                "Engineering Standards",
                "Human Maintainability",
                "Performance",
                "Agent Readiness",
                "Required Coding-Agent Workflow",
                "Before Any Changes",
                "Implementation and Delivery",
            }.issubset(headings)
        )
        normalized = " ".join(text.split())
        for requirement in (
            "without AI assistance",
            "Before editing **any file**",
            "0% to 100%",
            "No risk identified within the assessed scope",
            "Never force a 100% score",
            "New features must be easy for coding agents",
            "Updated confidence per objective",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, normalized)

    def test_build_guidance_matches_the_existing_development_script(self):
        guide = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        script = (ROOT / "scripts/angelo/build_native.sh").read_text(encoding="utf-8")
        for value in ("build-angelo-native", "ANGELO_NATIVE_BUILD_DIR", "ANGELO_BUILD_JOBS",
                      "SENSEI_NATIVE_BUILD_DIR", "SENSEI_BUILD_JOBS", "TOONLAB_NATIVE_BUILD_DIR", "TOONLAB_BUILD_JOBS"):
            with self.subTest(value=value):
                self.assertIn(value, script)
                self.assertIn(value, guide)
        self.assertIn("macOS arm64", guide)
        self.assertIn("not a full release", guide)

    def test_readme_names_a_bug_report_route(self):
        """Visitors of the public repository find where to report a bug: the website form (the same link the
        Help > Report a Bug item opens, ``report_bug`` in the identity), GitHub's bug form, and SECURITY.md for
        vulnerabilities. Both README copies say it (GitHub shows ``.github/README.md``)."""
        import json
        identity = json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text(encoding="utf-8"))
        report_bug = identity["urls"]["report_bug"]
        for name in ("README.md", ".github/README.md"):
            with self.subTest(readme=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                section = re.search(r"^Report a bug\n-+\n(.*?)(?=^\S[^\n]*\n-+\n|\Z)", text, re.M | re.S)
                self.assertIsNotNone(section, "%s has no 'Report a bug' section" % name)
                body = section.group(1)
                self.assertIn(report_bug, body)
                self.assertIn("Help > Report a Bug", body)
                self.assertIn("/issues/new", body)
                self.assertIn("[SECURITY.md](SECURITY.md)", body)
        self.assertEqual((ROOT / "README.md").read_text(encoding="utf-8"),
                         (ROOT / ".github/README.md").read_text(encoding="utf-8"))

    def test_issue_forms_ask_for_the_facts_a_bug_needs(self):
        forms = ROOT / ".github/ISSUE_TEMPLATE"
        bug = (forms / "bug_report.yml").read_text(encoding="utf-8")
        for field in ("id: angelo-version", "id: blender-version", "id: system", "id: steps", "id: expected",
                      "id: actual", "id: attachments", "Help > About", "SECURITY.md"):
            self.assertIn(field, bug)
        self.assertEqual(bug.count("required: true"), 6)  # everything but the attachments
        config = (forms / "config.yml").read_text(encoding="utf-8")
        import json
        identity = json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text(encoding="utf-8"))
        self.assertIn("url: " + identity["urls"]["report_bug"], config)
        self.assertIn("security@angelo.studio", config)
        self.assertIn("blank_issues_enabled: false", config)
        for text in (bug, config):
            self.assertNotRegex(text.lower(), "angelo" r"-cloud|bitbucket\.org")


if __name__ == "__main__":
    unittest.main()
