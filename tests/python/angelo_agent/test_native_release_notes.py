# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Release notes: CHANGELOG.md's format, the exported JSON the app and website share, and What's New.

The repository's own CHANGELOG.md is checked like a contract: it parses, the
bundled ``assets/release_notes.json`` is exactly what the exporter writes, every
bullet is plain text for users (no markup, links, issue numbers or engineer
words), and the 1.0 entry has the Highlights the update notice and What's New
draw. The parser's refusals are tested on small documents. Every translated note quotes each interface
label exactly as the app shows it in that language (``locale/angelo/release_labels.json``, checked by
``scripts/angelo/i18n/release_labels.py`` against Blender's and Angelo's catalogues).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))
from angelo_agent import release_notes, rtl_text  # noqa: E402
from i18n import po, release_labels  # noqa: E402

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
        self.refused(document(entry("1.0.1", "Unreleased"), entry("1.0.0", "Unreleased")), "one Unreleased entry")
        self.refused(document(entry("1.0.0"), entry("1.1.0")), "newest first")
        self.refused(document(entry("1.0.0"), entry("1.0.0")), "newest first")
        self.refused(document(entry().replace("A summary of the release.\n", "")), "needs a summary")
        self.refused(document(entry().replace("- One\n- Two\n- Three\n", "")), "1 to 5")
        self.refused(document(entry(extra="")).replace("- Three", "- Three\n- Four\n- Five\n- Six"), "1 to 5")
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

    def test_any_release_may_have_one_to_five_highlights(self):
        # Every release, not only a patch: a small release lists only what it changed, with no padding bullets.
        for count in (1, 2, 5):
            bullets = "".join("- Bullet %d\n" % n for n in range(count))
            for version in ("1.0.1", "1.1.0", "2.0.0"):
                with self.subTest(count=count, version=version):
                    text = document(entry(version).replace("- One\n- Two\n- Three\n", bullets))
                    self.assertEqual(len(changelog.validate(changelog.parse(text))[0]["highlights"]), count)

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


class ReleaseLineTests(unittest.TestCase):
    """Two release lines in flight at once (1.0.x keeps getting fixes beside 1.1.x): at most one Unreleased entry per
    MAJOR.MINOR line, each line's Unreleased entry is that line's newest, and versions stay strictly descending."""

    refused = ParserTests.refused

    def test_each_line_may_have_its_own_unreleased_entry(self):
        releases = changelog.validate(changelog.parse(document(
            entry("1.1.0", "Unreleased"), entry("1.0.11", "Unreleased"), entry("1.0.10"), entry("1.0.9"))))
        self.assertEqual([(r["version"], r["date"]) for r in releases],
                         [("1.1.0", None), ("1.0.11", None), ("1.0.10", "2026-10-20"), ("1.0.9", "2026-10-20")])
        changelog.validate(changelog.parse(document(entry("2.0.0", "Unreleased"), entry("1.1.1"),
                                                    entry("1.0.12", "Unreleased"), entry("1.0.11"))))

    def test_two_unreleased_entries_in_one_line_are_refused(self):
        self.refused(document(entry("1.1.0", "Unreleased"), entry("1.0.12", "Unreleased"),
                              entry("1.0.11", "Unreleased")), "1.0.11: one Unreleased entry per release line")
        self.refused(document(entry("1.1.1", "Unreleased"), entry("1.1.0", "Unreleased")), "one Unreleased entry")

    def test_an_unreleased_entry_below_a_dated_one_of_its_line_is_refused(self):
        self.refused(document(entry("1.0.12"), entry("1.0.11", "Unreleased")),
                     "1.0.11: Unreleased below 1.0.12, a dated release of its own line")
        self.refused(document(entry("1.1.0", "Unreleased"), entry("1.0.12"), entry("1.0.11", "Unreleased")),
                     "dated release of its own line")

    def test_entries_stay_newest_first_across_lines(self):
        self.refused(document(entry("1.0.11", "Unreleased"), entry("1.1.0", "Unreleased")), "newest first")
        self.refused(document(entry("1.1.0", "Unreleased"), entry("1.0.10"), entry("1.0.11", "Unreleased")),
                     "newest first")

    def test_the_feed_fields_and_whats_new_pick_each_lines_entry(self):
        releases = changelog.validate(changelog.parse(document(
            entry("1.1.0", "Unreleased"), entry("1.0.11", "Unreleased"), entry("1.0.10"))))
        self.assertEqual(changelog.feed_fields(releases, "1.0.11")["title"], "Angelo 1.0.11")
        self.assertEqual(changelog.feed_fields(releases, "1.1.0")["title"], "Angelo 1.1")
        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder, "CHANGELOG.md"), Path(folder, "release_notes.json")
            source.write_text(document(entry("1.1.0", "Unreleased"), entry("1.0.11", "Unreleased"), entry("1.0.10")),
                              encoding="utf-8")
            self.assertEqual(changelog.main(["export", "--source", str(source), "--out", str(out)]), 0)
            shipped = release_notes.load(out)
            for running in ("1.0.11", "1.1.0"):
                with self.subTest(running=running):
                    notes, remember = release_notes.whats_new("1.0.10", running, shipped)
                    self.assertEqual((notes.version, notes.date, remember), (running, "", running))


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
        # A version past every shipped entry (a tree may already hold the next minor's notes) has none to show.
        unlisted = "%d.0.0" % (int(notes[0].version.split(".")[0]) + 1)
        self.assertEqual(release_notes.whats_new("1.0.0", unlisted, notes), (None, unlisted))

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


class ReleaseLabelTests(unittest.TestCase):
    """Release notes quote each label as the app shows it: the repository's table, then the rules on small trees."""

    def test_the_repository_notes_quote_every_declared_label_as_the_app_shows_it(self):
        findings, _warnings = release_labels.check(ROOT)
        self.assertEqual([], findings, "fix the translated line (or declare the label): python3 scripts/angelo/i18n/cli.py release-labels")

    def tree(self, folder, lines, blender=(), angelo=(), template=(), code="sv"):
        """A checkout with one release (``lines``) and one language's catalogues: entries are (ctxt, id, str[, flags])."""
        root = Path(folder)
        notes = {"releases": [{"version": "9.0.0", "date": None, "title": "Angelo 9", "blender": "5.2.2 LTS",
                               "summary": lines[0], "highlights": [], "sections": [{"heading": "New", "items": lines[1:]}]}]}
        (root / "scripts/addons_core/angelo_agent/assets").mkdir(parents=True)
        (root / release_labels.NOTES).write_text(json.dumps(notes), encoding="utf-8")
        for path, entries in ((f"locale/po/{code}.po", blender), (f"locale/angelo/po/{code}.po", angelo),
                              ("locale/angelo/angelo.pot", [(c, i, "") for c, i, *_ in template])):
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            po.write(str(root / path), "Language: x", [po.Entry(i, s, c, flags=rest[0] if rest else ())
                                                         for c, i, s, *rest in entries])
        return root

    def test_the_repository_keeps_every_product_name_as_written(self):
        findings = release_labels.check_product_names(ROOT)
        self.assertEqual([], [(f.code, f.label, f.line[:60]) for f in findings],
                         "keep the name as written: python3 scripts/angelo/i18n/cli.py release-labels")

    def test_a_translated_product_name_fails_in_a_note_and_in_angelos_catalogue(self):
        line = "Render engines: Cycles and EEVEE, ready on the GPU."
        menu = "Open Angelo Library"
        for note, entry, failing in ((line.replace("Render engines", "Renderingsmotorer"), "Öppna Angelo Library", set()),
                                     ("Renderingsmotorer: Cykler och EEVEE, klara på GPU:n.", "Öppna Angelo Library",
                                      {"Cycles"}),
                                     (line.replace("Render engines", "Renderingsmotorer"), "Öppna Ängel-biblioteket",
                                      {"Angelo"})):
            with self.subTest(failing=failing), tempfile.TemporaryDirectory() as folder:
                root = self.tree(folder, [line], angelo=[("", line, note), ("", menu, entry)],
                                 template=[("", line), ("", menu)])
                (root / release_labels.PRODUCTS).write_text(json.dumps({"names": ["Angelo", "Cycles", "EEVEE"]}),
                                                            encoding="utf-8")
                found = release_labels.check_product_names(root, codes=["sv"])
                self.assertEqual(failing, {f.label for f in found})

    def run_check(self, root, table, code="sv"):
        findings, warnings = release_labels.check(root, table=table, codes=[code])
        return [(f.kind, f.label) for f in findings], [(w.kind, w.label) for w in warnings]

    def test_blenders_catalogue_answers_first_and_an_angelo_context_equal_to_the_message_falls_through(self):
        line = "Clips: in the Video tab, make clips."
        with tempfile.TemporaryDirectory() as folder:
            root = self.tree(folder, [line], blender=[("", "Video", "Videon")],
                             angelo=[("", line, "Klipp: på fliken Video gör du klipp."), ("Angelo", "Video", "Video"),
                                     ("", "Video", "Video")], template=[("", "Video")])
            self.assertEqual(self.run_check(root, {line: [["Video", None]]})[0], [("label mismatch", "Video")])
            po.write(str(root / "locale/angelo/po/sv.po"), "Language: sv",
                     [po.Entry(line, "Klipp: på fliken Videon gör du klipp."), po.Entry("Video", "Filmen", "Angelo")])
            self.assertEqual(self.run_check(root, {line: [["Video", None]]})[0], [("label mismatch", "Video")])
            self.assertEqual(self.run_check(root, {line: [["Video", "*"]]})[0], [])   # The native default context.

    def test_a_fuzzy_or_empty_entry_is_not_compiled_and_an_untranslated_label_is_only_reported(self):
        line = "Use the Render menu."
        with tempfile.TemporaryDirectory() as folder:
            root = self.tree(folder, [line], blender=[("", "Render", "Rendera", ["fuzzy"])],
                             angelo=[("", line, "Använd menyn Render.")])
            self.assertEqual(self.run_check(root, {line: [["Render", "*"]]}), ([], [("untranslated label", "Render")]))

    def test_the_table_cannot_fall_behind_the_notes(self):
        lines = ["Summary.", "Open the Render menu.", "Nothing to quote."]
        with tempfile.TemporaryDirectory() as folder:
            root = self.tree(folder, lines, blender=[("", "Render", "Rendera")],
                             angelo=[("", lines[1], "Öppna menyn Rendera.")])
            found, _ = self.run_check(root, {lines[0]: [], lines[1]: [["Render", "*"]], "Gone.": [],
                                             lines[2]: [["Missing", None], ["Render", 7]]})
            self.assertEqual(found, [("stale line", ""), ("malformed entry", "")])
            found, _ = self.run_check(root, {lines[0]: [], lines[1]: [], lines[2]: [["Missing", None]]})
            self.assertEqual(found, [("undeclared label", "Render"), ("label not in line", "Missing")])
            found, _ = self.run_check(root, {lines[0]: [], lines[1]: [["Render", False]]})
            self.assertEqual(found, [("undeclared line", "")])   # A name mentioned in prose is declared, not compared.

    def test_what_reads_as_a_quoted_label(self):
        labels = {"Video", "Undo", "3D", "Edit Mode", "Make Video from This Shot", "GPU", "H", "Ctrl", "Render region",
                  "Shot", "Model"}
        everywhere = lambda msgid: True  # noqa: E731
        cases = {
            "Open the Video tab and Edit Mode.": ["Edit Mode", "Video"],    # Capitalized inside a sentence; any phrase.
            "Video plays here, as one Undo step.": ["Undo"],                 # A sentence capitalizes its first word.
            "Beside Tripo in the 3D tab.": ["3D"],                           # A code before a widget noun.
            "A 3D model rendered on the GPU.": [],                           # Codes and acronyms in prose.
            "Make Video from This Shot renders frames.": ["Make Video from This Shot"],   # Only the longest quote.
            "Writes MP4 (H.264) with Ctrl+M.": [],                           # Not a word of a code or a shortcut.
            "Render region: drag a region.": ["Render region"],
            "Model pickers name the provider.": [],
            "Videos and videos.": [],
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(release_labels.undeclared_labels(line, [], labels, everywhere), expected)
        self.assertEqual(release_labels.undeclared_labels("Open the Video tab.", [], labels, lambda m: False), [])
        self.assertEqual(release_labels.undeclared_labels("Make Video from This Shot.", ["Make Video from This Shot"],
                                                          labels, everywhere), [])

    def test_arabic_compares_in_reading_order_and_thai_without_break_hints(self):
        line = "In the 3D tab."
        with tempfile.TemporaryDirectory() as folder:
            shown = rtl_text.to_visual("ثلاثي الأبعاد")
            root = self.tree(folder, [line], code="ar", blender=[("", "3D", "\ufeff" + shown)],
                             angelo=[("", line, rtl_text.to_visual("في علامة تبويب «ثلاثي الأبعاد»."))])
            self.assertEqual(self.run_check(root, {line: [["3D", None]]}, "ar")[0], [])   # Blender's BOM is not text.
            po.write(str(root / "locale/angelo/po/ar.po"), "Language: ar",
                     [po.Entry(line, rtl_text.to_visual("في علامة تبويب «3D»."))])
            self.assertEqual(self.run_check(root, {line: [["3D", None]]}, "ar")[0], [("label mismatch", "3D")])
        with tempfile.TemporaryDirectory() as folder:
            root = self.tree(folder, [line], code="th", blender=[("", "3D", "สาม\u200bมิติ ")],
                             angelo=[("", line, "ใน\u200bแท็บ\u200bสามมิติ")])
            self.assertEqual(self.run_check(root, {line: [["3D", None]]}, "th")[0], [])


if __name__ == "__main__":
    unittest.main()
