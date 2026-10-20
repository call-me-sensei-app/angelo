# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A release build refuses a release whose own notes are undated (scripts/angelo/release_dated.py).

The class: a 1.0.1 build was gated and signed with "## 1.0.1 — Unreleased", so What's New would have said
"Unreleased"; only the feed builder caught it, after the gates. These checks use a temporary tree for several versions,
never the repository's own CHANGELOG.md (a development line is allowed to be Unreleased).
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import changelog  # noqa: E402
import release_dated  # noqa: E402

RELEASE_CHECKS = ROOT / "scripts/angelo/release_checks.py"
ENTRY = """## {version} — {date}
Based on Blender 5.2.2 LTS.

A summary of the release.

### Highlights
- One change.
"""


class ReleaseDatedTests(unittest.TestCase):
    def tree(self, label, entries, export=True):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "scripts/modules").mkdir(parents=True)
        (root / release_dated.IDENTITY).write_text(json.dumps({"version": {"label": label}}), encoding="utf-8")
        text = "# Angelo release notes\n\nIntro.\n\n" + "\n".join(ENTRY.format(version=v, date=d) for v, d in entries)
        (root / "CHANGELOG.md").write_text(text, encoding="utf-8")
        notes = root / release_dated.NOTES
        notes.parent.mkdir(parents=True)
        if export:
            notes.write_text(changelog.render(changelog.validate(changelog.parse(text))), encoding="utf-8")
        return root

    def test_a_dated_release_of_any_version_may_be_built(self):
        for label, entries in (("1.0.1", [("1.0.1", "2026-10-20"), ("1.0.0", "2026-10-19")]),
                               ("2.3.0", [("2.3.0", "2027-05-01"), ("2.2.9", "2027-04-01")]),
                               ("1.0.0", [("1.0.0", "2026-10-19")])):
            with self.subTest(label=label):
                self.assertEqual(release_dated.problems(self.tree(label, entries)), [])

    def test_the_built_versions_entry_must_be_dated(self):
        for label in ("1.0.1", "1.4.0", "3.0.0"):
            with self.subTest(label=label):
                found = release_dated.problems(self.tree(label, [(label, "Unreleased"), ("1.0.0", "2026-10-19")]))
                self.assertTrue(any("still says %s is Unreleased" % label in p for p in found), found)

    def test_the_built_version_must_have_an_entry(self):
        found = release_dated.problems(self.tree("1.0.2", [("1.0.1", "2026-10-20"), ("1.0.0", "2026-10-19")]))
        self.assertTrue(any("no entry for 1.0.2" in p for p in found), found)

    def test_a_newer_unreleased_entry_must_not_ship_in_a_release(self):
        found = release_dated.problems(self.tree("1.0.1", [("1.0.2", "Unreleased"), ("1.0.1", "2026-10-20")]))
        self.assertTrue(any("newest entry, 1.0.2, is Unreleased" in p for p in found), found)

    def test_the_bundled_notes_must_be_the_current_export(self):
        root = self.tree("1.0.1", [("1.0.1", "2026-10-20")])
        notes = root / release_dated.NOTES
        data = json.loads(notes.read_text(encoding="utf-8"))
        data["releases"][0]["date"] = None   # A dated CHANGELOG.md beside a stale, undated export.
        notes.write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(any("not the export" in p for p in release_dated.problems(root)))
        notes.unlink()
        self.assertTrue(any("is missing" in p for p in release_dated.problems(root)))

    def test_a_missing_identity_is_refused(self):
        root = self.tree("1.0.1", [("1.0.1", "2026-10-20")])
        (root / release_dated.IDENTITY).unlink()
        self.assertIn("no readable version.label", release_dated.problems(root)[0])

    def test_the_command_line_fails_closed_and_the_release_builds_call_it(self):
        run = lambda root: subprocess.run([sys.executable, "-I", "-B", str(RELEASE_CHECKS), "release-dated", str(root)],
                                          capture_output=True, text=True)
        self.assertEqual(run(self.tree("1.0.1", [("1.0.1", "2026-10-20")])).returncode, 0)
        failed = run(self.tree("1.0.1", [("1.0.1", "Unreleased")]))
        self.assertEqual(failed.returncode, 1)
        self.assertIn("Unreleased", failed.stderr)
        mac = (ROOT / "scripts/angelo/build_release_macos.sh").read_text(encoding="utf-8")
        self.assertIn('release_checks.py" release-dated "$SOURCE"', mac)
        self.assertLess(mac.index("release-dated"), mac.index("cmake -S"), "refused before anything is built")
        windows = (ROOT / "scripts/angelo/windows_release_validate.ps1").read_text(encoding="utf-8")
        self.assertIn("release-dated $SourceDir", windows)
        self.assertLess(windows.index("release-dated"), windows.index("$build = Join-Path"), "checked before the build runs")


if __name__ == "__main__":
    unittest.main()
