# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Catalogue checks: what a translation must keep, and what is likely to break the layout.

Errors fail the host test; warnings go to the reviewer's checklist.

* header: UTF-8, the language, the machine-translation status field;
* no ``fuzzy`` flag (the build's ``msgfmt`` silently drops fuzzy entries);
* placeholders ``{name}``, ``%s`` ... identical to the source, as a multiset, and the
  translation formats with the source's fields;
* product names, shortcuts and newlines preserved;
* plural entries exist for every category the language needs;
* length: an estimate of drawn width against the source, with a tighter budget for
  short control labels (the canvas wraps sentences but not buttons);
* glossary: approved renderings of the fixed terms;
* Arabic: the build file is exactly the shaped, visual-order form of the reading-order source.
"""

import re
import string
import unicodedata

from i18n import languages

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)?(?:![rsa])?(?::[^{}]*)?\}"
                         r"|%(?:\([A-Za-z_]+\))?[-+#0]*\d*(?:\.\d+)?[sdifxXeEgGc]|%%")
# No space flag: "50% done" is a percentage followed by a word, never printf's "% d".
# Key combinations, the macOS modifier symbols, function keys and Esc. Enter, Return, Tab, Space and Delete alone
# are also ordinary words ("Enter a message", "Return to Angelo", "Delete {path}"), so only a combination pins them.
SHORTCUT = re.compile(r"\b(?:Ctrl|Cmd|Alt|Option|Shift)(?:\+[A-Za-z0-9]+)+\b|[⌃⌥⌘⇧]+[A-Za-z]?|\bF\d{1,2}\b|\bEsc\b")
STATUS_FIELD = "X-Angelo-Translation"
STATUS_VALUE = "machine-produced; pending native review"
REVIEWED = re.compile(r"^reviewed by \S.* on \d{4}-\d{2}-\d{2}$")  # Set by hand when a native reviewer signs off.
# Languages that share many words with English, so an identical translation is often right.
LATIN_COGNATES = ("pt", "pt-BR", "es", "it", "fr", "id", "tr", "nl", "de", "sv", "pl", "vi")
BREAK = "\u200b"  # ZERO WIDTH SPACE: Thai marks word boundaries with it so canvases wrap between words.
WIDE = re.compile(r"[ᄀ-ᇿ⺀-鿿가-힯豈-﫿＀-￯぀-ヿ]")


class Issue:
    def __init__(self, severity, language, key, message):
        self.severity, self.language, self.key, self.message = severity, language, key, message

    def __repr__(self):
        ctxt, msgid = self.key
        context = f" ({ctxt})" if ctxt else ""
        return f"{self.severity.upper()} [{self.language}] {msgid[:70]!r}{context}: {self.message}"


def status_ok(value):
    """A catalogue's review status: machine-produced and pending review, or signed off by a named reviewer."""
    return value == STATUS_VALUE or bool(REVIEWED.match(value))


def placeholders(text):
    return sorted(match.group(0) for match in PLACEHOLDER.finditer(text))


def fields(text):
    names = [name for _, name, _, _ in string.Formatter().parse(text) if name is not None]
    return names


class _Field:
    """A stand-in value for any field: every format spec (``{:s}``, ``{n:d}``) accepts it."""

    def __format__(self, spec):
        return ""


def format_arguments(text):
    """``(args, kwargs)`` that ``text.format`` accepts: named fields (Angelo's templates) and positional ones
    (``{:s}``, ``{0}``: Blender's own UI scripts, which Angelo edits for the Help menu, splash and About)."""
    names = [re.split(r"[.\[]", name)[0] for name in fields(text)]
    automatic = sum(1 for name in names if name == "")
    numbered = max((int(name) for name in names if name.isdigit()), default=-1) + 1
    return [_Field()] * max(automatic, numbered), {name: _Field() for name in names if name and not name.isdigit()}


def width(text, code):
    """Estimated drawn width in em units for the UI font with its CJK and Arabic fallbacks."""
    total = 0.0
    for ch in text.replace(BREAK, ""):
        if WIDE.match(ch):
            total += 1.0
        elif "฀" <= ch <= "๿":
            total += 0.5
        elif "ऀ" <= ch <= "ॿ":
            total += 0.62
        elif "؀" <= ch <= "ۿ" or "ﭐ" <= ch <= "﻿":
            total += 0.5
        elif ch in "iIl.,:;|!'’ ":
            total += 0.3
        elif ch.isupper() or ch in "mwMW@":
            total += 0.72
        else:
            total += 0.55
    return total


def break_problem(text):
    """Why a ZERO WIDTH SPACE in ``text`` is misplaced, or None (a line may break after one: see ``text_layout``)."""
    for match in re.finditer(BREAK, text):
        before, after = text[match.start() - 1:match.start()], text[match.end():match.end() + 1]
        if not before or not after or before.isspace() or after.isspace() or BREAK in (before, after):
            return "U+200B at an edge or next to a space"
        if unicodedata.category(after) in ("Mn", "Mc", "Me") or before in "เแโใไ":
            return "U+200B splits a Thai syllable (before a mark or after a leading vowel)"
        if not ("\u0e00" <= before <= "\u0e7f" or "\u0e00" <= after <= "\u0e7f"):
            return "U+200B outside Thai text"
    return None


def budget(source, code):
    """Allowed width ratio of a translation to its source: tighter for short labels."""
    length = len(source)
    if code in ("ja", "ko", "zh", "zh-TW"):
        return 1.3 if length > 24 else 1.9 if length > 10 else 2.6
    if length > 60:
        return 1.45
    if length > 24:
        return 1.6
    return 1.9 if length > 10 else 2.4


def check_entry(language, source, translation, glossary=None):
    """Issues for one translated entry; ``source`` and ``translation`` are ``po.Entry``."""
    issues, key = [], source.key
    text, english = translation.str, source.id

    def add(severity, message):
        issues.append(Issue(severity, language.code, key, message))

    if "fuzzy" in translation.flags:
        add("error", "marked fuzzy: msgfmt would drop it")
    if not text.strip():
        return issues
    if placeholders(english) != placeholders(text):
        add("error", f"placeholders differ: {placeholders(english)} vs {placeholders(text)}")
    else:
        try:
            if fields(english):
                positional, named = format_arguments(english)
                text.format(*positional, **named)
        except (KeyError, IndexError, ValueError) as error:
            add("error", f"does not format with the source's fields ({error})")
    if english.count("\n") != text.count("\n"):
        add("error", "line breaks differ")
    if BREAK in text and break_problem(text):
        add("error", break_problem(text))
    for product in languages.PRODUCT_NAMES:
        if re.search(rf"\b{product}\b", english) and product not in text:
            add("error", f"product name {product} missing")
    for shortcut in SHORTCUT.findall(english):
        if shortcut not in text:
            add("error", f"shortcut {shortcut!r} missing")
    if text == english and re.search(r"[A-Za-z]{4,}", english) and not re.fullmatch(r"[\W\d{}]*(?:%s )?", english):
        stripped = english
        for product in languages.PRODUCT_NAMES:
            stripped = stripped.replace(product, "")
        if re.search(r"[a-z]{4,}", stripped) and language.code not in LATIN_COGNATES:
            add("warning", "identical to the English source")
    ratio = width(text, language.code) / max(width(english, language.code), 0.1)
    if ratio > budget(english, language.code):
        add("warning", f"estimated {ratio:.1f}x as wide as the English (budget {budget(english, language.code):.1f}x)")
    if glossary:
        for term, forms in glossary.items():
            variants = forms.get(language.code)
            # German compounds ("Media-Studio-Bibliothek") and Thai word breaks do not hide a term.
            folded = text.replace(BREAK, "").casefold().replace("-", " ")
            if variants and re.search(rf"\b{re.escape(term)}\b", english, re.I) and not any(
                    v.casefold().replace("-", " ") in folded for v in variants):
                add("warning", f"glossary: {term!r} should read one of {variants}")
    return issues


def check_catalog(language, header, entries, template, glossary=None, logical=None):
    """Check a whole catalogue against the template; ``logical`` is the reading-order source (Arabic)."""
    issues = []
    fields_ = {}
    for line in header.split("\n"):
        if ":" in line:
            name, value = line.split(":", 1)
            fields_[name.strip()] = value.strip()
    if "charset=UTF-8" not in fields_.get("Content-Type", ""):
        issues.append(Issue("error", language.code, ("", ""), "header: Content-Type must declare charset=UTF-8"))
    if fields_.get("Language", "") != language.po:
        issues.append(Issue("error", language.code, ("", ""), f"header: Language must be {language.po!r}"))
    if not status_ok(fields_.get(STATUS_FIELD, "")):
        issues.append(Issue("error", language.code, ("", ""),
                            f"header: {STATUS_FIELD} must say '{STATUS_VALUE}' "
                            "or 'reviewed by <name> on <YYYY-MM-DD>'"))
    sources_ = {entry.key: entry for entry in template}
    translated = {entry.key: entry for entry in entries if not entry.obsolete}
    for key in translated:
        if key not in sources_:
            issues.append(Issue("warning", language.code, key, "not in the template (stale entry)"))
    missing = 0
    for key, source in sources_.items():
        translation = translated.get(key)
        needed = key[0] == "" or not key[0].startswith("plural:") or key[0][7:] in language.plurals
        if not needed:
            continue
        if translation is None or not translation.str.strip():
            missing += 1
            issues.append(Issue("error", language.code, key, "not translated"))
            continue
        issues += check_entry(language, source, translation, glossary)
    return issues, missing


def coverage(language, entries, template):
    """(translated, required) counting only the entries this language needs."""
    translated = {entry.key for entry in entries if entry.str.strip() and not entry.obsolete}
    required = [e for e in template if not e.ctxt.startswith("plural:") or e.ctxt[7:] in language.plurals]
    return sum(1 for e in required if e.key in translated), len(required)
