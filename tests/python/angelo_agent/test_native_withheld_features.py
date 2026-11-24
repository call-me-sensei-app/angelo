# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A feature held back from a release leaves nothing of itself a person or the agent can reach (no Blender).

2.1.0 ships retargeting and full-body IK; auto-rigging moves to the next release (the maintainer's ruling,
2026-11-20, "Next release"). A held-back feature merged by mistake, or half removed, would show a + menu row that
fails, an F3 operator, a key, a verb the agent is told of, a release note, a translated string or a guide that promises
it. Every shipped text is read, not chosen files (the assembly review of 9cfa023adf3, S1 and S2):

* every file under ``scripts/``: Python as tokens (names, imports and strings, never comments), other text assets
  (JSON, TOML, web pages and scripts, ...) whole, and every file's own path;
* C and C++ string literals under ``source/`` (operator idnames, RNA and UI text);
* every ``doc/angelo/*.rst`` paragraph, except a paragraph that says the feature comes in the ``next release``;
* the live (non-obsolete) entries of the translation template and every catalogue.

A feature in ``WITHHELD`` is its words and its identifiers (``Withheld.pattern``), matched whole: ``auto_right`` in
C++, Mixamo's auto-rigger, Auto-Rig Pro and another product's "Meshy auto-rig" are not it (``Withheld.others``).
``ScannerTests`` plants each kind of leftover in each kind of text and checks it is found. The entry is removed when the
feature ships.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import io
import re
import sys
import tokenize
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))


@dataclass(frozen=True)
class Withheld:
    pattern: re.Pattern   # the feature's words and identifiers, whole (no letter or digit on either side)
    others: tuple = ()    # other things that share its words


WITHHELD = {
    "auto-rig": Withheld(
        re.compile(r"(?<![a-z0-9])(?:auto[ _-]?rig(?:s|ged|ging)?|rig_auto(?:_[a-z0-9_]*)?)(?![a-z0-9])", re.I),
        ("Auto-Rig Pro", "Meshy auto-rig")),
}
NEXT_RELEASE = re.compile(r"\bnext release\b", re.I)
TEXT_ASSETS = {".json", ".toml", ".yaml", ".yml", ".txt", ".md", ".xml", ".html", ".js", ".css", ".csv"}
CPP = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".m", ".mm"}
CPP_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def found(feature, text):
    return [m.group(0) for m in feature.pattern.finditer(_without(feature, text))]


def _without(feature, text):
    for other in feature.others:   # Matched across a line break too: guides wrap their lines.
        text = re.sub(r"\s+".join(re.escape(word) for word in other.split()), "", text, flags=re.I)
    return text


def in_python(feature, source):
    """Names, imports and strings of Python source; comments are not shipped text."""
    hits = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.NAME, tokenize.STRING) or token.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
                hits += found(feature, token.string)
    except (tokenize.TokenError, SyntaxError, IndentationError):   # Not Python after all: read it whole.
        hits = found(feature, source)
    return hits


def in_cpp(feature, source):
    """C and C++ string literals (what reaches a person or registers an operator); code and comments are not."""
    if not feature.pattern.search(_without(feature, source)):
        return []
    return [hit for literal in CPP_STRING.findall(source) for hit in found(feature, literal)]


def in_rst(feature, source):
    """Every paragraph of a guide, except one that says the feature comes in the next release."""
    return [hit for paragraph in re.split(r"\n\s*\n|\n(?=\s*[*-] )", source) if not NEXT_RELEASE.search(paragraph)
            for hit in found(feature, paragraph)]


def in_catalogue(feature, source):
    """The live entries of a translation template or catalogue: their context, message, translation and source."""
    from i18n import po
    hits = []
    for entry in po.parse(source)[1]:
        if not entry.obsolete:
            for text in [entry.ctxt or "", entry.id, entry.str or ""] + list(entry.extracted):
                hits += found(feature, text)
    return hits


def shipped():
    """(path, scanner) for every shipped text this test reads, and how many of each kind there are."""
    files, counts = [], {"scripts": 0, "source": 0, "doc": 0, "catalogue": 0}
    for path in sorted((ROOT / "scripts").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            if path.suffix == ".py":
                files.append((path, in_python))
            elif path.suffix in TEXT_ASSETS:
                files.append((path, lambda feature, text: found(feature, text)))
            else:
                files.append((path, None))   # Its path only (an icon, a binary asset).
            counts["scripts"] += 1
    for path in sorted((ROOT / "source").rglob("*")):
        if path.suffix in CPP and path.is_file():
            files.append((path, in_cpp))
            counts["source"] += 1
    for path in sorted((ROOT / "doc/angelo").glob("*.rst")):
        files.append((path, in_rst))
        counts["doc"] += 1
    locale = ROOT / "locale/angelo"
    for path in [locale / "angelo.pot"] + sorted(locale.glob("po/*.po")) + sorted(locale.glob("src/*.po")):
        if path.is_file():
            files.append((path, in_catalogue))
            counts["catalogue"] += 1
    return files, counts


class ScannerTests(unittest.TestCase):
    """Each kind of leftover, in each kind of text, is found; what only looks like it is not."""

    LEFTOVERS = (
        ("a release note", lambda f: found(f, '{"highlights": ["Auto-rig builds a Rigify rig from your mesh."]}')),
        ("a guide's + menu list", lambda f: in_rst(f, "**Rig** holds Work on hands, Auto-rig and the rigging tools.")),
        ("a C++ operator idname", lambda f: in_cpp(f, 'ot->idname = "ANGELO_OT_auto_rig";')),
        ("a keymap entry", lambda f: in_python(f, 'items = [("angelo.auto_rig", {"type": "R", "value": "PRESS"})]')),
        ("a live catalogue message", lambda f: in_catalogue(f, 'msgid ""\nmsgstr ""\n\nmsgid "Auto-Rig"\nmsgstr "x"\n')),
        ("a + menu row id", lambda f: in_python(
            f, 'chat_destinations.Feature("rig_auto", n_("Rig a character"), operator="angelo.rig_character")')),
        ("an import of its module", lambda f: in_python(f, "from . import rig_auto_markers\n")),
        ("an operator class", lambda f: in_python(f, "class ANGELO_OT_rig_auto_propose(bpy.types.Operator):\n    pass\n")),
    )
    LOOKALIKES = (
        ("a C++ name sharing its letters", lambda f: in_cpp(
            f, "const IndexMask auto_right = handles_by_type(curves);  // auto-rig later")),
        ("Mixamo's auto-rigger", lambda f: in_python(f, '"""Mixamo\'s auto-rigger skeleton."""\n')),
        ("another product", lambda f: found(f, "Rigify, Auto-Rig Pro, VRoid, a Meshy auto-rig")),
        ("another product, wrapped", lambda f: in_rst(f, "Tiby, a Meshy\n         auto-rig, the studio's chibi")),
        ("a next-release passage", lambda f: in_rst(f, "* Auto-rigging on Rigify, in the next release.\n")),
        ("a comment", lambda f: in_python(f, "x = 1   # unlike auto_rig\n")),
        ("a retired catalogue message", lambda f: in_catalogue(f, 'msgid ""\nmsgstr ""\n\n#~ msgid "Auto-Rig"\n#~ msgstr "x"\n')),
    )

    def test_each_kind_of_leftover_is_found(self):
        for feature_name, feature in WITHHELD.items():
            for kind, scan in self.LEFTOVERS:
                with self.subTest(feature=feature_name, leftover=kind):
                    self.assertTrue(scan(feature), kind)

    def test_what_only_looks_like_it_is_not(self):
        for feature_name, feature in WITHHELD.items():
            for kind, scan in self.LOOKALIKES:
                with self.subTest(feature=feature_name, lookalike=kind):
                    self.assertEqual(scan(feature), [], kind)


class ShippedTexts(unittest.TestCase):
    def test_nothing_shipped_names_a_withheld_feature(self):
        files, counts = shipped()
        for kind, count in counts.items():   # Every kind of text is there to read (a sparse checkout reads none).
            self.assertGreater(count, 0, kind)
        for feature_name, feature in WITHHELD.items():
            leftovers = []
            for path, scan in files:
                relative = path.relative_to(ROOT).as_posix()
                leftovers += ["%s (its path)" % relative for _hit in found(feature, relative)]
                if scan is not None:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    leftovers += ["%s: %s" % (relative, hit) for hit in scan(feature, text)]
            with self.subTest(feature=feature_name):
                self.assertEqual(leftovers, [], "a withheld feature reaches people or the agent")


if __name__ == "__main__":
    unittest.main()
