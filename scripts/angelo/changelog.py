#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""CHANGELOG.md: the one source of Angelo's release notes.

The same file feeds the in-app "What's New", the update notice's highlights
and the website's /releases page, so its format is strict and checked::

    # Angelo release notes
    <free introduction>

    ## 1.0.0 — Unreleased          (or  ## 1.0.0 — 2026-10-20 )
    Based on Blender 5.2.2 LTS.    (the Blender release this one is built on)
    <one-paragraph summary, at most 300 characters>

    ### Highlights                  (3 to 5 bullets, at most 140 characters each)
    - …
    ### New | Improved | Fixed | For IT administrators | Known issues
    - …

Rules: newest release first; versions are MAJOR.MINOR.PATCH and strictly
descending; only the newest entry may be ``Unreleased``; every release names
the Blender it is built on; every bullet is one line of plain text for people
(no markup, no links, no issue numbers); a release needs a summary and
Highlights. Notes are written for users, not engineers: what you can do, what
got better, what was fixed.

The export (``release_notes.json``) is what the website's /releases page renders:
``{"schema": 1, "product": "Angelo", "releases": [{"version", "date" (null while
unreleased), "title", "blender", "summary", "highlights", "sections":
[{"heading", "items"}]}]}``, newest first. Each release's anchor on that page is
its version (``https://angelo.studio/releases#1.0.1``), the link the update
notice opens.

Commands (from the repository root)::

    python3 scripts/angelo/changelog.py check            # validate CHANGELOG.md and the exported JSON
    python3 scripts/angelo/changelog.py export           # write the JSON the app bundles and the website reads
    python3 scripts/angelo/changelog.py export --out F   # ... somewhere else (the website's data folder)
    python3 scripts/angelo/changelog.py show 1.0.0       # one entry as the update feed's fields

Standard library only; the app never reads CHANGELOG.md, only the exported JSON.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "CHANGELOG.md"
EXPORT = ROOT / "scripts/addons_core/angelo_agent/assets/release_notes.json"
SECTIONS = ("New", "Improved", "Fixed", "For IT administrators", "Known issues")
HEADING = re.compile(r"## (\d{1,3}\.\d{1,3}\.\d{1,3}) — (Unreleased|\d{4}-\d{2}-\d{2})")
BASED_ON = re.compile(r"Based on Blender (\d{1,3}\.\d{1,3}\.\d{1,3}(?: LTS)?)\.")
SUMMARY_LIMIT, HIGHLIGHT_LIMIT, ITEM_LIMIT = 300, 140, 300
# Markup that would show literally in the app: emphasis, code, links, HTML, issue references.
_MARKUP = re.compile(r"[`*_\[\]<>]|\(https?:|https?://|#\d+\b|\bPR\b")


class ChangelogError(ValueError):
    pass


def _plain(text, limit, where):
    if not text or len(text) > limit:
        raise ChangelogError("%s: must be 1 to %d characters, is %d" % (where, limit, len(text)))
    if _MARKUP.search(text):
        raise ChangelogError("%s: plain text only (no markup, links or issue numbers): %r" % (where, text))
    if text != text.strip() or "  " in text:
        raise ChangelogError("%s: stray whitespace: %r" % (where, text))
    return text


def parse(text):
    """The list of releases (newest first) in ``text``; raises ``ChangelogError`` with the offending place."""
    releases, current, section = [], None, None
    summary = []
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        where = "line %d" % number
        if line.startswith("<!--") or (not releases and current is None and not line.startswith("## ")):
            continue  # SPDX header, title and introduction.
        if line.startswith("## "):
            match = HEADING.fullmatch(line)
            if not match:
                raise ChangelogError("%s: a release heading is '## 1.0.0 — 2026-10-20' or '## 1.0.0 — Unreleased'" % where)
            _close(current, summary)
            current = {"version": match.group(1), "date": None if match.group(2) == "Unreleased" else match.group(2),
                       "blender": "", "summary": "", "highlights": [], "sections": []}
            releases.append(current)
            section, summary = None, []
        elif line.startswith("### "):
            name = line[4:]
            if name != "Highlights" and name not in SECTIONS:
                raise ChangelogError("%s: unknown section %r (use Highlights, %s)" % (where, name, ", ".join(SECTIONS)))
            _close(current, summary)
            section = name
            if name != "Highlights":
                if any(s["heading"] == name for s in current["sections"]):
                    raise ChangelogError("%s: section %r appears twice" % (where, name))
                current["sections"].append({"heading": name, "items": []})
        elif line.startswith("- "):
            if current is None or section is None:
                raise ChangelogError("%s: a bullet outside a section" % where)
            if section == "Highlights":
                current["highlights"].append(_plain(line[2:], HIGHLIGHT_LIMIT, where))
            else:
                current["sections"][-1]["items"].append(_plain(line[2:], ITEM_LIMIT, where))
        elif line.strip():
            if current is None or section is not None:
                raise ChangelogError("%s: text is only allowed as the summary under a release heading" % where)
            based_on = BASED_ON.fullmatch(line.strip())
            if not current["blender"] and not summary and based_on:
                current["blender"] = based_on.group(1)
                continue
            if not current["blender"]:
                raise ChangelogError("%s: the first line under a release heading is 'Based on Blender 5.2.2 LTS.'" % where)
            summary.append(line.strip())
        elif line:
            raise ChangelogError("%s: whitespace-only line" % where)
    _close(current, summary)
    return releases


def _close(release, summary):
    if release is not None and summary and not release["summary"]:
        release["summary"] = _plain(" ".join(summary), SUMMARY_LIMIT, "summary of %s" % release["version"])


def validate(releases):
    if not releases:
        raise ChangelogError("no release entries")
    versions = [tuple(int(p) for p in r["version"].split(".")) for r in releases]
    if versions != sorted(set(versions), reverse=True):
        raise ChangelogError("releases must be newest first, each version once")
    for index, release in enumerate(releases):
        where = release["version"]
        if release["date"] is None and index:
            raise ChangelogError("%s: only the newest release may be Unreleased" % where)
        if not release["blender"]:
            raise ChangelogError("%s: needs 'Based on Blender X.Y.Z.' under its heading" % where)
        if not release["summary"]:
            raise ChangelogError("%s: needs a summary paragraph under its heading" % where)
        if not 3 <= len(release["highlights"]) <= 5:
            raise ChangelogError("%s: Highlights needs 3 to 5 bullets, has %d" % (where, len(release["highlights"])))
        for section in release["sections"]:
            if not section["items"]:
                raise ChangelogError("%s: section %r is empty" % (where, section["heading"]))
        if [s["heading"] for s in release["sections"]] != [h for h in SECTIONS if any(
                s["heading"] == h for s in release["sections"])]:
            raise ChangelogError("%s: sections go in this order: %s" % (where, ", ".join(SECTIONS)))
    return releases


def document(releases):
    """What is exported: the app bundles it (What's New) and the website reads it (/releases)."""
    entries = []
    for release in releases:
        major, minor, patch = release["version"].split(".")
        title = "Angelo " + (release["version"] if patch != "0" else major + "." + minor)
        entries.append({key: release[key] for key in ("version", "date")} | {"title": title} | {
            key: release[key] for key in ("blender", "summary", "highlights", "sections")})
    return {"schema": 1, "product": "Angelo", "releases": entries}


def render(releases):
    return json.dumps(document(releases), ensure_ascii=False, indent=2) + "\n"


def load(source=SOURCE):
    return validate(parse(Path(source).read_text(encoding="utf-8")))


def feed_fields(releases, version):
    """The title, summary and highlights of one release, for the update feed (sign_update_feed.py build)."""
    entry = next((r for r in document(releases)["releases"] if r["version"] == version), None)
    if entry is None:
        raise ChangelogError("CHANGELOG.md has no entry for %s" % version)
    return {key: entry[key] for key in ("title", "summary", "highlights")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("check", "export", "show"))
    parser.add_argument("version", nargs="?")
    parser.add_argument("--source", default=str(SOURCE))
    parser.add_argument("--out", default=str(EXPORT))
    args = parser.parse_args(argv)
    try:
        releases = load(args.source)
        if args.command == "show":
            print(json.dumps(feed_fields(releases, args.version or releases[0]["version"]), ensure_ascii=False, indent=2))
        elif args.command == "export":
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(render(releases), encoding="utf-8")
            print("wrote %s (%d releases)" % (args.out, len(releases)))
        else:
            current = Path(args.out).read_text(encoding="utf-8") if Path(args.out).exists() else ""
            if current != render(releases):
                print("%s is out of date: run  python3 scripts/angelo/changelog.py export" % args.out, file=sys.stderr)
                return 1
            print("CHANGELOG.md is valid and %s is current (%d releases)" % (args.out, len(releases)))
    except ChangelogError as error:
        print("CHANGELOG.md: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
