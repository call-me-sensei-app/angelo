# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Measure every translation with Blender's real fonts and flag layout overflow (run INSIDE a full build).

    Angelo --background --factory-startup --python scripts/angelo/i18n/measure_widths.py -- [--out report.json]

For each shipped language the active interface language is switched to it, each
catalogue message is looked up through ``pgettext_iface`` (so the compiled
``angelo.mo`` is what is measured) and drawn-width is taken with ``blf`` in the
default UI font and its fallbacks (CJK, Arabic, Thai, Devanagari). A message is
flagged when it is wider than the English by more than the budget for its length
(``validate.budget``): short control labels get little room, sentences wrap.
Only a build with ``WITH_INTERNATIONAL`` can run this.
"""

import json
import sys
from pathlib import Path

import blf
import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from i18n import languages, po, sources, validate  # noqa: E402


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("l10n-widths.json")
    if not bpy.app.build_options.international:
        raise SystemExit("This build has WITH_INTERNATIONAL off: nothing is translated, nothing to measure.")
    _, template = po.read(str(sources.POT))
    view = bpy.context.preferences.view
    view.use_translate_interface = True
    translations = bpy.app.translations
    font = 0
    blf.size(font, 14)
    english = {e.key: blf.dimensions(font, e.id)[0] for e in template if e.id.strip()}
    report = {}
    for language in languages.LANGUAGES:
        view.language = language.locale
        # The loader reads the new catalogue lazily: the first lookup of the session picks it up.
        flagged, missing, widths = [], 0, []
        for entry in template:
            ctxt = entry.ctxt or translations.contexts.default
            text = translations.pgettext_iface(entry.id, ctxt)
            if text == entry.id and not entry.ctxt.startswith("plural:") and language.code not in ("en",):
                missing += 1
                continue
            blf.size(font, 14)
            width = blf.dimensions(font, text.split("\n")[0])[0]
            base = max(english[entry.key], 1.0)
            ratio = width / base
            widths.append(ratio)
            if ratio > validate.budget(entry.id, language.code) and len(entry.id) < 60:
                flagged.append({"msgid": entry.id, "ctxt": entry.ctxt, "text": text, "ratio": round(ratio, 2),
                                "budget": validate.budget(entry.id, language.code)})
        report[language.code] = {"locale": language.locale, "active": translations.locale, "missing_at_runtime": missing,
                                 "mean_ratio": round(sum(widths) / max(len(widths), 1), 3), "flagged": flagged}
        print(f"{language.code:6s} locale={translations.locale:8s} loaded={len(widths):5d} missing={missing:4d} "
              f"mean x{report[language.code]['mean_ratio']:.2f} flagged={len(flagged)}", flush=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", out)


main()
