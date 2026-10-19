# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The release notes the app ships and when to show "What's New".

``assets/release_notes.json`` is exported from ``CHANGELOG.md`` by
``scripts/angelo/changelog.py`` (a host test fails when it is stale); this
module only reads it. "What's New" shows the running version's highlights
once after an update, never on the very first launch (the welcome screen
covers that), and never again for a version already shown. It stays in the Help
menu. Release notes are content, not interface text: they are English and are
not run through the interface translations.

Standard library only; no bpy.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from . import app_version

ASSET = Path(__file__).resolve().parent / "assets" / "release_notes.json"
MAX_FILE = 256 * 1024


@dataclass(frozen=True)
class Notes:
    version: str
    date: str          # "" while unreleased
    title: str
    blender: str       # The Blender release it is built on ("5.2.2 LTS"); "" in an older file.
    summary: str
    highlights: tuple
    sections: tuple    # ((heading, (item, …)), …)


def load(path=ASSET):
    """Every shipped release, newest first; an unreadable or malformed file reads as no notes."""
    try:
        if Path(path).stat().st_size > MAX_FILE:
            return ()
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        return tuple(Notes(version=entry["version"], date=entry["date"] or "", title=entry["title"],
                           blender=entry.get("blender") or "", summary=entry["summary"], highlights=tuple(entry["highlights"]),
                           sections=tuple((s["heading"], tuple(s["items"])) for s in entry["sections"]))
                     for entry in document["releases"] if app_version.parse(entry["version"]))
    except (OSError, ValueError, KeyError, TypeError):
        return ()


def for_version(version, releases=None):
    releases = load() if releases is None else releases
    return next((notes for notes in releases if notes.version == version), None)


def whats_new(last_seen, current, releases=None):
    """``(notes to show or None, version to remember)``.

    A first launch (nothing remembered), a downgrade and a version without notes remember the
    current version silently; only an upgrade with notes shows them.
    """
    seen, running = app_version.parse(last_seen), app_version.parse(current)
    if running is None:
        return None, last_seen
    if seen is None:
        return None, current
    if seen >= running:
        return None, last_seen if seen > running else current
    return for_version(current, releases), current
