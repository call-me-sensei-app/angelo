# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Release notes quote every interface label exactly as the app shows it, in every language.

``locale/angelo/release_labels.json`` declares, for each English release-note line, the names it quotes:
``{line: [[label, context], ...]}``. The context says how the app looks the label up: ``null`` is Angelo's
``ui_text.iface_`` (the ``"Angelo"`` context first, then the default context), a string is that context as
a native widget asks for it (``"*"`` the default context, as menus and ``layout.label`` do; ``"Operator"``
an operator button). ``false`` declares a name the line mentions in running prose, which a translation may
inflect (an editor, a mode, an engine, a feature's name), not a control it points the reader to.

For every language the check resolves each label the way the app does (``BLT_pgettext``: Blender's
catalogue ``locale/po`` first, then Angelo's ``locale/angelo/po``; fuzzy and empty entries are not
compiled) and requires that exact text in the language's translation of the line.

The table cannot fall behind the notes:

* every line of the newest release must be declared (``[]`` when it quotes nothing), and every declared
  line must still be a release-note line;
* a declared label must appear verbatim in its English line;
* an interface label the English line quotes but the table leaves out is refused. A label is a message of
  either catalogue shaped like one (short, no placeholder, no closing punctuation, starting with a capital
  or a digit) that reads as quoted where it stands (``_quoted_as_label``); only the longest such quote
  counts.

A label no catalogue translates in a language (the app falls back to English there) is a missing
translation, not a wording choice of the note: it is reported as a warning, never required verbatim.
Product names (``locale/angelo/product_names.json``: Cycles, EEVEE, V-Ray, Angelo...) stay as written:
``check_product_names`` refuses a newest release-note line or an entry of Angelo's own catalogue whose translation
changes one (1.0.8: the engine switcher showed Blender's "Cycles" F-curve modifier word as the engine's name).

Text is compared as read: Arabic (``po/ar.po`` and Blender's ``ar.po`` are shaped visual text) in reading
order, without Thai line-break hints (U+200B) or edge spaces.
"""

import json
import re
import sys
from collections import namedtuple
from pathlib import Path

from i18n import languages, po

TABLE = "locale/angelo/release_labels.json"
PRODUCTS = "locale/angelo/product_names.json"
NOTES = "scripts/addons_core/angelo_agent/assets/release_notes.json"
ANGELO = "Angelo"
# Nouns English prose puts after a control's name ("the 3D tab", "the Render menu", "its Restore snapshot link"):
# generic widget kinds, the release notes of 1.0.0-1.0.8 use 18 of them 70 times.
WIDGET_NOUNS = frozenset("tab tabs menu menus submenu button buttons panel card cards link field toggle checkbox "
                         "switch switcher slider dropdown popover dialog sidebar toolbar header picker pickers".split())

Finding = namedtuple("Finding", "code line label kind detail")
Resolved = namedtuple("Resolved", "text source")   # source: "blender", "angelo" or None (shown in English)


def _domain(path):
    """A compiled catalogue as the app loads it: msgfmt skips fuzzy, empty and obsolete entries."""
    if not path.exists():
        return {}
    _, entries = po.read(str(path))
    return {entry.key: entry.str for entry in entries
            if entry.str and not entry.obsolete and "fuzzy" not in entry.flags and entry.id}


class Catalogues:
    """Both text domains of every shipped language, from one checkout (``root``)."""

    def __init__(self, root, angelo_root=None):
        self.root, angelo_root = Path(root), Path(angelo_root or root)
        self.blender, self.angelo = {}, {}
        for language in languages.LANGUAGES:
            self.blender[language.code] = _domain(self.root / "locale/po" / f"{language.po}.po")
            self.angelo[language.code] = _domain(angelo_root / "locale/angelo/po" / f"{language.po}.po")
        _, self.template = po.read(str(angelo_root / "locale/angelo/angelo.pot"))

    def pgettext(self, code, context, msgid):
        """``BLT_pgettext``: the default context for an empty or ``*`` context, Blender's domain first."""
        context = "" if not context or context.startswith("*") else context
        for source, domain in (("blender", self.blender[code]), ("angelo", self.angelo[code])):
            text = domain.get((context, msgid))
            if text:
                return Resolved(text, source)
        return Resolved(msgid, None)

    def resolve(self, code, msgid, context):
        if context is None:   # ui_text.iface_: a translation equal to the message falls through to the default context.
            hit = self.pgettext(code, ANGELO, msgid)
            if hit.source and hit.text != msgid:
                return hit
            return self.pgettext(code, "", msgid)
        return self.pgettext(code, context, msgid)

    def angelo_entries(self, code):
        """``{(context, msgid): translation}`` of Angelo's own compiled catalogue for ``code``."""
        return {(key[0], key[1]) if isinstance(key, tuple) else ("", key): text for key, text in self.angelo[code].items()}

    def label_msgids(self):
        """Every message shaped like an interface label, from Angelo's template and Blender's catalogues."""
        ids = {entry.id for entry in self.template if not entry.ctxt.startswith("plural:")
               and not any("release_notes.json" in origin for origin in entry.extracted)}
        for domain in self.blender.values():
            ids.update(msgid for _, msgid in domain)
        return {msgid for msgid in ids if label_shaped(msgid)}


def label_shaped(msgid):
    if not msgid or msgid != msgid.strip() or any(ch in msgid for ch in "{}%\n\t\"") or msgid[-1] in ".!?:;,":
        return False
    words = msgid.split()
    return (msgid[0].isupper() or msgid[0].isdigit()) and len(words) <= 7 and any(ch.isalpha() for ch in msgid)


def readable(text):
    """Catalogue text as read: Arabic (shaped, visual order) in reading order, no edge spaces or Thai break hints."""
    text = text.replace("\ufeff", "").replace("\u200b", "").strip()
    if any("\ufb50" <= ch <= "\ufefe" for ch in text):
        from angelo_agent import rtl_text
        return rtl_text.to_logical(text)
    return text


def note_lines(notes):
    """Release-note lines per release, newest first: the summary, the highlights and every section item."""
    return [(release["version"], [release["summary"], *release["highlights"],
                                  *(item for section in release["sections"] for item in section["items"])])
            for release in notes["releases"]]


def _spans(text, phrase):
    """Where ``phrase`` stands as whole words in ``text``: not "H" in "H.264", "Ctrl" in "Ctrl+M" or "Video" in "Videos"."""
    pattern = r"(?<![\w.+-])" + re.escape(phrase) + r"(?![\w-]|[.+]\w)"
    return [match.span() for match in re.finditer(pattern, text)]


def _sentence_start(line, start):
    before = line[:start].rstrip()
    return not before or before[-1] in ".:;!?"


def _capitalized(word):
    """A capitalized word ("Video", "ToonLab"), not an acronym or a code ("GPU", "UVs", "3D", "X-ray")."""
    return len(word) > 1 and word[0].isupper() and word[1].islower()


def _quoted_as_label(line, start, end):
    """Whether the words at ``line[start:end]`` read as a quoted label rather than prose.

    English prose capitalizes a label it quotes ("the Video tab", "as one Undo step", "Edit Mode"), so a
    capitalized word inside a sentence marks one; so does a following widget noun ("the 3D tab"). A phrase
    of several words that opens a sentence names its subject ("Render region: ..."). Acronyms and codes
    carry no such signal, and a single word that opens a sentence is capitalized by the sentence.
    """
    words = line[start:end].split()
    opening = _sentence_start(line, start)
    if len(words) >= 2 and opening:
        return True
    if any(_capitalized(word) for word in (words[1:] if opening else words)):
        return True
    following = re.match(r"\s+(\w+)", line[end:])
    return bool(not _capitalized(words[0]) and following and following.group(1) in WIDGET_NOUNS)


def undeclared_labels(line, declared, candidates, translated_anywhere):
    """Labels the English ``line`` quotes that ``declared`` leaves out.

    Only the longest quote counts: "Video" inside "Make Video from This Shot" is part of that label, and
    anything inside a declared label's text is declared with it.
    """
    found = [(start, end, msgid) for msgid in candidates
             if msgid in line and msgid not in declared and msgid not in languages.PRODUCT_NAMES
             for start, end in _spans(line, msgid)]
    declared_spans = [span for label in declared for span in _spans(line, label)]
    missing = set()
    for start, end, msgid in found:
        if any(a <= start and end <= b for a, b in declared_spans):
            continue
        if any(a <= start and end <= b and (a, b) != (start, end) for a, b, _ in found):
            continue
        if _quoted_as_label(line, start, end) and (len(msgid.split()) >= 2 or translated_anywhere(msgid)):
            missing.add(msgid)
    return sorted(missing)


def _malformed(labels):
    """Why a table value is not ``[[label, context], ...]`` with a context of null, a string or false; else None."""
    if not isinstance(labels, list):
        return "not a list"
    for item in labels:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and item[0]
                and (item[1] is None or item[1] is False or isinstance(item[1], str))):
            return "%r is not [label, null | context | false]" % (item,)
    return None


def load_table(root):
    path = Path(root) / TABLE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def check(root, angelo_root=None, table=None, codes=None):
    """Return ``(findings, warnings)`` for the checkout at ``root`` (catalogues optionally from ``angelo_root``)."""
    root = Path(root)
    sys.path.insert(0, str(root / "scripts/addons_core"))   # rtl_text, for Arabic reading order.
    notes = json.loads((root / NOTES).read_text(encoding="utf-8"))
    table = load_table(root) if table is None else table
    cats = Catalogues(root, angelo_root)
    codes = [language.code for language in languages.LANGUAGES if not codes or language.code in codes]
    releases = note_lines(notes)
    every_line = {line for _, lines in releases for line in lines}
    findings, warnings = [], []
    for line in releases[0][1] if releases else ():
        if line not in table:
            findings.append(Finding("*", line, "", "undeclared line", "add it to %s ([] if it quotes no label)" % TABLE))
    for line in table:
        if line not in every_line:
            findings.append(Finding("*", line, "", "stale line", "not a release-note line any more"))
    candidates = cats.label_msgids()
    translated = {}

    def translated_anywhere(msgid):
        if msgid not in translated:
            translated[msgid] = any(cats.pgettext(language.code, context, msgid).text != msgid
                                    for language in languages.LANGUAGES for context in ("", ANGELO))
        return translated[msgid]

    for line, labels in table.items():
        problem = _malformed(labels)
        if problem:
            findings.append(Finding("*", line, "", "malformed entry", problem))
            continue
        if line not in every_line:
            continue
        declared = [label for label, _ in labels]
        for label in undeclared_labels(line, declared, candidates, translated_anywhere):
            findings.append(Finding("*", line, label, "undeclared label", "the line quotes it; declare it in %s" % TABLE))
        for label, context in labels:
            if not _spans(line, label):
                findings.append(Finding("*", line, label, "label not in line", "the English line does not quote it"))
                continue
            if context is False:   # Declared as a name the line mentions, not a control it points at.
                continue
            for code in codes:
                note = cats.resolve(code, line, None)
                if note.source is None:
                    findings.append(Finding(code, line, label, "missing translation", "the line has no translation"))
                    continue
                shown = cats.resolve(code, label, context)
                if shown.source is None:
                    warnings.append(Finding(code, line, label, "untranslated label",
                                            "the app shows English %r; the note is not required to" % label))
                    continue
                want, text = readable(shown.text), readable(note.text)
                if want not in text:
                    findings.append(Finding(code, line, label, "label mismatch",
                                            "the app shows %r (%s)" % (want, shown.source)))
    return findings, warnings


def product_names(root):
    path = Path(root) / PRODUCTS
    return json.loads(path.read_text(encoding="utf-8"))["names"] if path.exists() else []


def _mentions(text, name):
    """``name`` as a whole word of ``text`` (case as written: "fal" the provider, never "fall")."""
    return re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(name), text) is not None


def check_product_names(root, angelo_root=None, codes=None):
    """Product names stay as written, in every language (``locale/angelo/product_names.json``): each newest
    release-note line and each message of Angelo's own catalogue that names one keeps it verbatim in its
    translation. Blender's catalogue is not checked: its messages are its own (its "Cycles" is an F-curve modifier),
    and a widget that shows a product name must not look it up there (the engine switcher, 1.0.8)."""
    root = Path(root)
    sys.path.insert(0, str(root / "scripts/addons_core"))   # rtl_text, for Arabic reading order.
    names = product_names(root)
    notes = json.loads((root / NOTES).read_text(encoding="utf-8"))
    releases = note_lines(notes)
    cats = Catalogues(root, angelo_root)
    codes = [language.code for language in languages.LANGUAGES if not codes or language.code in codes]
    findings = []
    lines = list(releases[0][1]) if releases else []
    for code in codes:
        for line in lines:
            for name in (n for n in names if _mentions(line, n)):
                note = cats.resolve(code, line, None)
                if note.source is not None and name not in readable(note.text):
                    findings.append(Finding(code, line, name, "product name changed", "keep %r as written" % name))
        for (context, msgid), text in cats.angelo_entries(code).items():
            for name in (n for n in names if _mentions(msgid, n)):
                if name not in readable(text):
                    findings.append(Finding(code, msgid, name, "product name changed",
                                            "Angelo's catalogue entry translates %r; keep it as written" % name))
    return findings


def report(findings, warnings, out=sys.stdout):
    for finding in findings:
        print("FAIL %-5s %-19s %r in %r: %s" % (finding.code, finding.kind, finding.label, finding.line[:60],
                                                finding.detail), file=out)
    for warning in warnings:
        print("warn %-5s %-19s %r in %r: %s" % (warning.code, warning.kind, warning.label, warning.line[:60],
                                                warning.detail), file=out)
