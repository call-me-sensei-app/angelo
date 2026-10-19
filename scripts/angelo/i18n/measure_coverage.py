# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Glyph coverage of Angelo's catalogues in the fonts that draw them (run INSIDE Angelo; no window needed).

    Angelo --background --factory-startup --python scripts/angelo/i18n/measure_coverage.py -- [--out PATH]
        [--no-system-font] [code ...]

Blender draws one glyph per character and falls back through its bundled fonts
when the requested font lacks one. For every distinct character of a language's
shipped catalogue (``locale/angelo/po``, so Arabic in its shaped presentation
forms) this draws the character with each candidate font, with fallback switched
off (``blf.NO_FALLBACK``), into a CPU image (``blf.bind_imbuf``). A font that lacks
the character draws its ``.notdef`` glyph, exactly as it draws a private-use
character, so the two images are identical. Candidates, in the order they are
tried: the chat canvas's font (the macOS system font, as ``chat_view`` loads it),
Blender's interface font, then every bundled fallback font (the monospaced one last).
``--no-system-font`` leaves the macOS font out, as on Windows and Linux, where the
chat canvas draws with Blender's interface font.

The report gives, per language, the characters no font covers and how many
characters each font serves. Coverage says nothing about shaping: Blender has
none, so Devanagari conjuncts and the pre-base vowel sign, and Thai mark
stacking, are judged on screenshots (``doc/angelo/localization.rst``).
"""

import json
import sys
import unicodedata
from pathlib import Path

import blf
import bpy
import imbuf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from i18n import languages, po, sources  # noqa: E402

PROBE = "\U000F0001"  # Supplementary private use: no font here has it, so it draws .notdef.
CHAT_FONTS = ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf")


def candidates(system_font=True):
    fonts = [("chat: " + Path(path).name, path) for path in CHAT_FONTS[:1] if system_font and Path(path).is_file()]
    bundled = Path(bpy.utils.system_resource('DATAFILES', path="fonts"))
    names = sorted(path.name for path in bundled.glob("*.woff2"))
    # Blender's interface font first; the monospaced font last (Blender never falls back to it for proportional text).
    names.sort(key=lambda name: (name != "Inter.woff2", "Mono" in name, name))
    return fonts + [(name, str(bundled / name)) for name in names]


def render(font, character):
    image = imbuf.new((48, 48))
    with blf.bind_imbuf(font, image):
        blf.size(font, 24)
        blf.position(font, 10, 14, 0)
        blf.color(font, 1, 1, 1, 1)
        blf.draw_buffer(font, character)
    with image.with_buffer() as pixels:
        return bytes(pixels)


def characters(language):
    _, entries = po.read(str(sources.LOCALE_DIR / "po" / f"{language.po}.po"))
    used = set()
    for entry in entries:
        if not entry.obsolete:
            # Format characters (Thai's ZERO WIDTH SPACE word breaks) have no glyph to draw.
            used.update(ch for ch in entry.str if not ch.isspace() and ord(ch) > 0x7E
                        and unicodedata.category(ch) != "Cf")
    return sorted(used)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("l10n-coverage.json")
    codes = [arg for arg in argv if not arg.startswith("--") and arg != str(out)] or [l.code for l in languages.LANGUAGES]
    fonts = []
    for name, path in candidates("--no-system-font" not in argv):
        font = blf.load(path)
        if font < 0:
            continue
        blf.enable(font, blf.NO_FALLBACK)
        fonts.append((name, font, render(font, PROBE)))
    report = {"fonts": [name for name, _, _ in fonts], "languages": {}}
    for code in codes:
        language = languages.BY_CODE[code]
        served, missing = {}, []
        for character in characters(language):
            name = next((name for name, font, notdef in fonts if render(font, character) != notdef), None)
            if name is None:
                missing.append(f"U+{ord(character):04X} {character}")
            else:
                served[name] = served.get(name, 0) + 1
        report["languages"][code] = {"characters": sum(served.values()) + len(missing), "missing": missing,
                                     "served_by": dict(sorted(served.items(), key=lambda item: -item[1]))}
        print(f"{code:6s} {report['languages'][code]['characters']:5d} characters, {len(missing)} without a glyph; "
              f"served by {report['languages'][code]['served_by']}", flush=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", out)


main()
