# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A release build only builds a dated release (release_checks.py ``release-dated``).

The app shows the bundled ``release_notes.json`` in What's New, and an entry without a date is drawn as "Unreleased".
The feed builder refuses an undated entry too, but only after the gates and signing, so the release builds refuse
first, for any version:

* the identity's version (``scripts/modules/angelo_identity.json`` version.label) has a CHANGELOG.md entry with a date;
* the newest CHANGELOG.md entry is dated (nothing newer than the release is pending in it);
* the bundled ``release_notes.json`` is exactly the export of that CHANGELOG.md (so it carries the same date).

Standard library only.
"""

import json
from pathlib import Path

import changelog

IDENTITY = "scripts/modules/angelo_identity.json"
NOTES = "scripts/addons_core/angelo_agent/assets/release_notes.json"


def version_label(source):
    """The version the tree at ``source`` builds (its identity's version.label)."""
    return json.loads((Path(source) / IDENTITY).read_text(encoding="utf-8"))["version"]["label"]


def problems(source):
    """Why the tree at ``source`` must not be built as a release (empty: it may)."""
    source = Path(source)
    try:
        version = version_label(source)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return ["%s has no readable version.label (%s)" % (IDENTITY, error)]
    try:
        releases = changelog.load(source / "CHANGELOG.md")
    except (OSError, changelog.ChangelogError) as error:
        return ["CHANGELOG.md is not valid: %s" % error]
    found = []
    entry = next((r for r in releases if r["version"] == version), None)
    if entry is None:
        found.append("CHANGELOG.md has no entry for %s, the version this tree builds" % version)
    elif entry["date"] is None:
        found.append("CHANGELOG.md still says %s is Unreleased: date it (\"## %s — YYYY-MM-DD\") and run "
                     "scripts/angelo/changelog.py export before a release build" % (version, version))
    if releases and releases[0]["date"] is None and releases[0]["version"] != version:
        found.append("CHANGELOG.md's newest entry, %s, is Unreleased: a release of %s must not ship it"
                     % (releases[0]["version"], version))
    try:
        bundled = (source / NOTES).read_text(encoding="utf-8")
    except OSError as error:
        return found + ["%s is missing (%s)" % (NOTES, error)]
    if bundled != changelog.render(releases):
        found.append("%s is not the export of CHANGELOG.md (run scripts/angelo/changelog.py export)" % NOTES)
    return found
