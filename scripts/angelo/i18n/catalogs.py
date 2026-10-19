# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Maintain ``locale/angelo/po/<language>.po`` from ``locale/angelo/angelo.pot``.

* ``update`` adds new template entries (empty, to be translated), keeps existing
  translations of unchanged entries and retires removed ones as obsolete ``#~``
  entries, so a refresh after the strings moved never loses work.
* ``build_arabic`` writes the build file ``po/ar.po`` (shaped, visual order)
  from the reading-order source ``src/ar.po`` that translators edit.
* ``compile_catalog`` runs the repository's own ``msgfmt`` (or gettext's) so the
  .mo the app loads can be inspected without a full build.
"""

import json
import subprocess
import sys
from pathlib import Path

from i18n import languages, po, sources, validate

sys.path.insert(0, str(sources.ADDON.parent))
from angelo_agent import rtl_text  # noqa: E402

PO_DIR = sources.LOCALE_DIR / "po"
SRC_DIR = sources.LOCALE_DIR / "src"  # Reading-order source of right-to-left languages.
GLOSSARY = sources.LOCALE_DIR / "glossary.json"


def header(language, template_header, status=validate.STATUS_VALUE):
    """A catalogue header; ``status`` is the review status the catalogue already had (kept across updates)."""
    lines = [line for line in template_header.split("\n") if line and not line.startswith(("Language", "X-Angelo"))]
    lines.append(f"Language: {language.po}")
    lines.append(f"{validate.STATUS_FIELD}: {status if validate.status_ok(status) else validate.STATUS_VALUE}")
    lines.append(f"Language-Team: {language.english} ({language.native})")
    return "\n".join(lines)


def load_template():
    return po.read(str(sources.POT))


def load_glossary():
    return json.loads(GLOSSARY.read_text(encoding="utf-8")) if GLOSSARY.exists() else {}


def catalog_path(language):
    return (SRC_DIR if language.rtl else PO_DIR) / f"{language.po}.po"


def update(language, template_entries, template_header):
    """Merge the template into one language's catalogue; returns (added, retired)."""
    path = catalog_path(language)
    previous, status = {}, validate.STATUS_VALUE
    if path.exists():
        old_header, old = po.read(str(path))
        previous = {entry.key: entry for entry in old}
        status = po.header_fields(old_header).get(validate.STATUS_FIELD, status)
    merged, added = [], 0
    current = {entry.key for entry in template_entries}
    for source in template_entries:
        old = previous.get(source.key)
        translation = old.str if old else ""  # A message that comes back reuses its retired translation.
        if not translation:
            added += 1
        merged.append(po.Entry(source.id, translation, source.ctxt, comments=old.comments if old else (),
                               extracted=source.extracted))
    retired = [entry for key, entry in previous.items() if key not in current and entry.str]
    for entry in retired:
        entry.obsolete, entry.refs, entry.extracted = True, [], []
        merged.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    po.write(str(path), header(language, template_header, status), merged)
    return added, len(retired)


def visual(entry):
    """The Arabic build entry: placeholders survive because the shaper keeps them as left-to-right runs."""
    return po.Entry(entry.id, rtl_text.to_visual(entry.str) if entry.str else "", entry.ctxt, extracted=entry.extracted)


def build_arabic():
    language = languages.BY_CODE["ar"]
    head, entries = po.read(str(catalog_path(language)))
    out = [visual(entry) for entry in entries if not entry.obsolete]
    PO_DIR.mkdir(parents=True, exist_ok=True)
    po.write(str(PO_DIR / "ar.po"), head + "\nX-Angelo-Form: visual order, shaped; generated from src/ar.po", out)
    return len(out)


def build_catalog_path(language):
    return PO_DIR / f"{language.po}.po"


def compile_catalog(po_path, mo_path):
    """Compile with the in-tree msgfmt when ``MSGFMT`` is given by the build, else gettext's."""
    command = [sources.MSGFMT or "msgfmt", str(po_path), str(mo_path)]
    if not sources.MSGFMT:
        command = ["msgfmt", "-o", str(mo_path), str(po_path)]
    subprocess.run(command, check=True, capture_output=True)


def check_all(only=None):
    """Validate every catalogue; returns {code: (issues, missing, translated, required)}."""
    template_header, template = load_template()
    glossary = load_glossary()
    results = {}
    for language in languages.LANGUAGES:
        if only and language.code not in only:
            continue
        path = catalog_path(language)
        if not path.exists():
            results[language.code] = ([validate.Issue("error", language.code, ("", ""), "catalogue missing")], len(template), 0, len(template))
            continue
        head, entries = po.read(str(path))
        issues, missing = validate.check_catalog(language, head, entries, template, glossary.get("terms"))
        if language.rtl:
            built_head, built = po.read(str(build_catalog_path(language))) if build_catalog_path(language).exists() else ("", [])
            expected = {entry.key: rtl_text.to_visual(entry.str) for entry in entries if entry.str and not entry.obsolete}
            actual = {entry.key: entry.str for entry in built}
            for key, text in expected.items():
                if actual.get(key) != text:
                    issues.append(validate.Issue("error", language.code, key, "po/ar.po is not the visual form of src/ar.po (run build-arabic)"))
        translated, required = validate.coverage(language, entries, template)
        results[language.code] = (issues, missing, translated, required)
    return results
