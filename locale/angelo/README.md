<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Angelo interface catalogues

Angelo's own interface text (the agent add-on, the startup scripts and the few
strings Angelo adds in C++) in the 20 languages besides English that Angelo 1.0
ships: `ar de es fr hi id it ja ko nl pl pt pt-BR ru sv th tr vi zh zh-TW`.
Blender's own menus and editors keep Blender's catalogues (`locale/po`); this
folder is the separate **Angelo text domain**. The design, the right-to-left and
script limits and how to continue are in
[doc/angelo/localization.rst](../../doc/angelo/localization.rst).

| File | What it is |
| --- | --- |
| `angelo.pot` | The template: every message, with its Blender context and source files. Generated, never edited by hand. |
| `po/<language>.po` | One catalogue per language, named like Blender's (`ja`, `pt_BR`, `zh_HANS`, `zh_HANT`). Compiled to `angelo.mo` by the build. |
| `src/ar.po` | Arabic in **reading order** (what a translator edits). `po/ar.po` is generated from it (`cli.py build-arabic`). |
| `glossary.json`, `GLOSSARY.md` | Fixed terms, what they mean, and their agreed rendering per language. |
| `REVIEW.md` | What a native reviewer should check first, per language. |

**Status: every catalogue is machine-produced and pending native review.** Each
header says so (`X-Angelo-Translation: machine-produced; pending native review`).
When a native reviewer signs a language off, set it to
`reviewed by <name> on <YYYY-MM-DD>`; refreshes keep it. Review status is never
recorded with the gettext `fuzzy` flag: the build's `msgfmt` silently drops fuzzy
entries.

## How the text gets on screen

* `.po` compiles to `datafiles/locale/<language>/LC_MESSAGES/angelo.mo` beside
  Blender's `blender.mo` (`source/creator/CMakeLists.txt`). `BLT_pgettext` asks
  Blender's catalogue first and Angelo's second, so a message Blender already
  translates keeps Blender's wording and the rest falls through to this folder.
* Python text goes through `angelo_agent/ui_text.py` (`n_`, `iface_`, `tip_`,
  `rpt_`, `plural_`, `notice`); Blender-native widgets translate their literals
  themselves. C++ uses `IFACE_`/`TIP_`/`RPT_`.
* Only a build with `WITH_INTERNATIONAL=ON` translates anything
  (`scripts/angelo/build_release_macos.sh` does; the lite development build does not).

## Refreshing after strings change

```bash
# 1. Regenerate the template inside an app whose add-on is this checkout's (Blender's own extractor).
Angelo.app/Contents/MacOS/Angelo --background --factory-startup \
  --python scripts/angelo/i18n/extract_pot.py
# 2. Merge it into every catalogue: new messages arrive empty, removed ones become obsolete (#~).
python3 scripts/angelo/i18n/cli.py update
# 3. Per language: Blender's wording for common words, work files of the empty entries, then merge.
python3 scripts/angelo/i18n/cli.py hints ja > work/ja/blender_wording.json
python3 scripts/angelo/i18n/cli.py chunks ja work/ja    # chunk-NN.json
#    translate each chunk-NN.json into chunk-NN.out.json: a JSON list of strings, same order
python3 scripts/angelo/i18n/cli.py apply ja work/ja     # validated; rejects broken entries
# Arabic: chunks/apply read and write src/ar.po; then build the shipped visual form.
python3 scripts/angelo/i18n/cli.py build-arabic
# 4. Validate.
python3 scripts/angelo/i18n/cli.py check --warnings
python3 scripts/angelo/i18n/cli.py coverage
```

`tests/python/angelo_agent/test_native_translation.py` fails when a new string
bypasses translation, when a marked message is missing from the template, or when
any catalogue is incomplete or breaks a placeholder.

## Rules for translators

1. Keep every `{placeholder}`, `%s`, key combination (`Ctrl+Alt+M`, `⌃⌥M`,
   `Shift+Enter`, `Esc`) and product name (Angelo, Blender, Claude, ChatGPT, Codex,
   OpenAI, Anthropic) exactly. "Enter a message" or "Return to Angelo" are words:
   translate them.
2. Follow `GLOSSARY.md`; where Blender already has an established word (Scene,
   Preview, Cancel, Undo) use Blender's (`cli.py hints <code>`; prefer its `button`
   form for actions).
3. Counted phrases come as `plural:<category>` entries (CLDR categories: zero, one,
   two, few, many, other). Translate the categories your language uses.
4. `Operator` context entries are button and menu labels: keep them short.
5. Keep the register of a software interface: short, neutral, no slang. Do not
   translate user data or model output, only the interface around it.
6. A sentence that names a control uses that control's exact translated label.
7. A control label has little room to grow: stay within about 1.5 times the English.
8. Thai: put ZERO WIDTH SPACE (U+200B) between words. Angelo's canvases may break a line
   there and nowhere else inside a Thai run (Blender has no Thai dictionary); the validator
   rejects one at an edge, beside a space, before a vowel or tone mark, or after a leading vowel.
