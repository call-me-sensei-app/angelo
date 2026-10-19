# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Release notes: CHANGELOG.md's format, the exported JSON the app and website share, and What's New.

The repository's own CHANGELOG.md is checked like a contract: it parses, the
bundled ``assets/release_notes.json`` is exactly what the exporter writes, every
bullet is plain text for users (no markup, links, issue numbers or engineer
words), and the 1.0 entry has the Highlights the update notice and What's New
draw. The parser's refusals are tested on small documents.
"""

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import release_notes  # noqa: E402

_spec = importlib.util.spec_from_file_location("angelo_changelog", ROOT / "scripts/angelo/changelog.py")
changelog = importlib.util.module_from_spec(_spec)
sys.modules["angelo_changelog"] = changelog
_spec.loader.exec_module(changelog)

ENGINEER_WORDS = re.compile(r"toolset|ratchet|depsgraph|dependency graph|\bRNA\b|operator_run|schema|refactor|mutex|"
                            r"commit|\bgate\b|stdout|regression|latency|ceiling", re.I)
ENTRY = """## {version} — {date}
Based on Blender 5.2.2 LTS.

A summary of the release.

### Highlights
- One
- Two
- Three
{extra}"""


def document(*entries):
    return "# Angelo release notes\n\nIntro with `code` and [links](https://example.com).\n\n" + "\n".join(entries)


def entry(version="1.0.0", date="2026-10-20", extra=""):
    return ENTRY.format(version=version, date=date, extra=extra)


class ParserTests(unittest.TestCase):
    def refused(self, text, fragment):
        with self.assertRaises(changelog.ChangelogError) as caught:
            changelog.validate(changelog.parse(text))
        self.assertIn(fragment, str(caught.exception))

    def test_a_valid_document_parses_newest_first_and_the_introduction_is_free_text(self):
        releases = changelog.validate(changelog.parse(document(
            entry("1.1.0", "Unreleased", "### New\n- A new thing\n### Fixed\n- A fixed thing\n"), entry())))
        self.assertEqual([r["version"] for r in releases], ["1.1.0", "1.0.0"])
        self.assertIsNone(releases[0]["date"])
        self.assertEqual(releases[0]["sections"], [{"heading": "New", "items": ["A new thing"]},
                                                   {"heading": "Fixed", "items": ["A fixed thing"]}])
        exported = changelog.document(releases)["releases"]
        self.assertEqual((exported[0]["title"], exported[1]["title"]), ("Angelo 1.1", "Angelo 1.0"))
        self.assertEqual(changelog.document(changelog.parse(document(entry("1.0.2"))))["releases"][0]["title"], "Angelo 1.0.2")

    def test_structure_is_enforced(self):
        self.refused(document(entry("1.0.0", "Unreleased"), entry("0.9.0", "Unreleased")), "only the newest")
        self.refused(document(entry("1.0.0"), entry("1.1.0")), "newest first")
        self.refused(document(entry("1.0.0"), entry("1.0.0")), "newest first")
        self.refused(document(entry().replace("A summary of the release.\n", "")), "needs a summary")
        self.refused(document(entry().replace("- Three\n", "")), "3 to 5")
        self.refused(document(entry(extra="")).replace("- Three", "- Three\n- Four\n- Five\n- Six"), "3 to 5")
        self.refused(document(entry(extra="### New\n- a\n### New\n- b\n")), "twice")
        self.refused(document(entry(extra="### Fixed\n- a\n### New\n- b\n")), "order")
        self.refused(document(entry(extra="### New\n")), "empty")
        self.refused(document(entry(extra="### Other\n- a\n")), "unknown section")
        self.refused(document(entry().replace("2026-10-20", "soon")), "release heading")
        self.refused(document(entry().replace("—", "-")), "release heading")
        self.refused(document("## 1.0.0 — 2026-10-20\n- bullet before a section\n"), "outside a section")
        self.refused("# Title only\n", "no release")
        self.refused(document(entry().replace("Based on Blender 5.2.2 LTS.\n", "")), "Based on Blender")
        self.refused(document(entry().replace("Based on Blender 5.2.2 LTS.", "Based on Blender 5.2.")), "Based on Blender")

    def test_each_release_names_the_blender_it_is_built_on(self):
        releases = changelog.validate(changelog.parse(document(entry("1.0.1"), entry().replace("5.2.2 LTS", "5.2.1"))))
        exported = changelog.document(releases)["releases"]
        self.assertEqual([r["blender"] for r in exported], ["5.2.2 LTS", "5.2.1"])
        self.assertEqual(list(exported[0]), ["version", "date", "title", "blender", "summary", "highlights", "sections"])
        self.assertEqual(releases[0]["summary"], "A summary of the release.")

    def test_bullets_are_plain_text_for_people(self):
        for bad in ("Uses `code`", "**Bold** claim", "See [the docs](https://angelo.studio)", "Visit https://angelo.studio",
                    "Fixes #123", "Merged PR 12", "snake_case", "<b>html</b>", " leading space", "two  spaces",
                    "x" * 141):
            with self.subTest(bad=bad):
                self.refused(document(entry().replace("- One", "- " + bad)), "")
        self.refused(document(entry(extra="### New\n- " + "x" * 301 + "\n")), "300")
        self.refused(document(entry().replace("A summary of the release.", "x" * 301)), "300")

    def test_check_and_export_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder, "CHANGELOG.md"), Path(folder, "out.json")
            source.write_text(document(entry()))
            self.assertEqual(changelog.main(["check", "--source", str(source), "--out", str(out)]), 1)   # Not exported yet.
            self.assertEqual(changelog.main(["export", "--source", str(source), "--out", str(out)]), 0)
            self.assertEqual(changelog.main(["check", "--source", str(source), "--out", str(out)]), 0)
            source.write_text(document(entry(extra="### New\n- Another\n")))
            self.assertEqual(changelog.main(["check", "--source", str(source), "--out", str(out)]), 1)   # Stale.
            source.write_text("not a changelog")
            self.assertEqual(changelog.main(["check", "--source", str(source), "--out", str(out)]), 1)
            self.assertEqual(changelog.feed_fields(changelog.parse(document(entry())), "1.0.0")["title"], "Angelo 1.0")
            with self.assertRaises(changelog.ChangelogError):
                changelog.feed_fields(changelog.parse(document(entry())), "9.9.9")


class RepositoryChangelogTests(unittest.TestCase):
    def test_the_changelog_is_valid_and_the_bundled_json_is_current(self):
        releases = changelog.load()
        self.assertEqual((ROOT / "scripts/addons_core/angelo_agent/assets/release_notes.json").read_text(encoding="utf-8"),
                         changelog.render(releases), "run: python3 scripts/angelo/changelog.py export")

    def test_the_1_0_entry_is_written_for_users(self):
        entry = next(r for r in changelog.load() if r["version"] == "1.0.0")
        self.assertGreaterEqual(len(entry["highlights"]), 3)
        text = " ".join([entry["summary"], *entry["highlights"], *(i for s in entry["sections"] for i in s["items"])])
        self.assertIsNone(ENGINEER_WORDS.search(text), ENGINEER_WORDS.search(text))
        names = [s["heading"] for s in entry["sections"]]
        self.assertEqual(names, ["New", "For IT administrators", "Known issues"], "a first release has nothing to improve on")
        for needed in ("Claude Opus 5.5", "GPT-6 Astra", "GPT-6.1 Sol", "DisableUpdateCheck", "UpdateFeedURL",
                       "DisableCrashReporting", "Apple silicon", "64-bit Windows"):
            self.assertIn(needed, text)
        self.assertIn("Restart to Update", text, "the notes say how an update is installed, and that it waits")
        self.assertIn("never restarts while the agent", text)
        self.assertIn("can't be sent from the app yet", text, "the release notes never promise a send that isn't there")

    def test_the_newest_release_names_the_blender_this_source_is(self):
        header = (ROOT / "source/blender/blenkernel/BKE_blender_version.h").read_text(encoding="utf-8")
        number = int(re.search(r"#define BLENDER_VERSION (\d+)", header).group(1))
        patch = re.search(r"#define BLENDER_VERSION_PATCH (\d+)", header).group(1)
        suffix = re.search(r"#define BLENDER_VERSION_SUFFIX (\w*)", header)
        expected = "%d.%d.%s" % (number // 100, number % 100, patch) + (" LTS" if suffix and suffix.group(1) == "LTS" else "")
        self.assertEqual(changelog.load()[0]["blender"], expected)

    def test_the_changelog_names_its_contract_and_the_contract_exists(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        target = re.search(r"\]\((doc/angelo/[a-z_]+\.rst)\)", text).group(1)
        self.assertTrue((ROOT / target).is_file(), target)


class WhatsNewTests(unittest.TestCase):
    def setUp(self):
        self.releases = release_notes.load()

    def test_the_shipped_notes_load_newest_first(self):
        self.assertTrue(self.releases)
        versions = [tuple(int(p) for p in n.version.split(".")) for n in self.releases]
        self.assertEqual(versions, sorted(versions, reverse=True))
        notes = release_notes.for_version("1.0.0")
        self.assertEqual((notes.title, notes.date, notes.blender), ("Angelo 1.0", "2026-10-19", "5.2.2 LTS"))
        self.assertIn("New", dict(notes.sections))
        self.assertIsNone(release_notes.for_version("9.9.9"))

    def test_shown_once_after_an_upgrade_never_on_a_first_launch(self):
        notes = release_notes.load()
        self.assertEqual(release_notes.whats_new("", "1.0.0", notes), (None, "1.0.0"))
        self.assertEqual(release_notes.whats_new("1.0.0", "1.0.0", notes), (None, "1.0.0"))
        shown, remember = release_notes.whats_new("0.9.0", "1.0.0", notes)
        self.assertEqual((shown.version, remember), ("1.0.0", "1.0.0"))
        self.assertEqual(release_notes.whats_new("1.0.0", "1.1.0", notes), (None, "1.1.0"))   # No entry for 1.1.0 yet.

    def test_a_downgrade_keeps_the_highest_seen_version(self):
        self.assertEqual(release_notes.whats_new("1.2.0", "1.1.0", ()), (None, "1.2.0"))
        self.assertEqual(release_notes.whats_new("1.2.0", "garbage", ()), (None, "1.2.0"))
        self.assertEqual(release_notes.whats_new("junk", "1.0.0", ()), (None, "1.0.0"))

    def test_a_damaged_or_missing_file_means_no_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "notes.json")
            self.assertEqual(release_notes.load(path), ())
            for text in ("nope", "[]", '{"releases": [{"version": "1.0.0"}]}', '{"releases": 5}'):
                path.write_text(text)
                self.assertEqual(release_notes.load(path), ())
            path.write_text(json.dumps({"releases": [{"version": "x.y", "date": None, "title": "t", "summary": "s",
                                                      "highlights": [], "sections": []}]}))
            self.assertEqual(release_notes.load(path), ())


if __name__ == "__main__":
    unittest.main()
