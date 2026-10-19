.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

###########################
Localization (21 languages)
###########################

Angelo 1.0 ships its own interface in 21 languages: English (the source) and
``ar de es fr hi id it ja ko nl pl pt pt-BR ru sv th tr vi zh zh-TW``. Blender's own
menus and editors keep Blender's catalogues (``locale/po``, some only partly
translated: see "Limits"). Angelo's text lives in a separate catalogue set,
``locale/angelo``, compiled into a second text domain. This document is the
contract; ``locale/angelo/README.md`` is the working guide, ``GLOSSARY.md`` and
``glossary.json`` the agreed terms, ``REVIEW.md`` what a native reviewer checks
first. Every catalogue is machine-produced and marked as pending native review.

.. contents::
   :local:
   :depth: 1

How a string reaches the screen
===============================

There are four routes, and each has one correct spelling:

* **Blender widgets.** A literal (``text="Done"``, ``bl_label``, property names and
  descriptions, enum items) is translated by Blender itself. Blender looks an
  operator button's text up in the *operator's* context ("Operator"), everything else
  in the default context.
* **Text built at run time for a widget.** ``iface_("Connected to {name}", name=n)``
  (``tip_``, ``rpt_``, ``plural_``) from ``ui_text``, passed with ``translate=False`` so
  Blender does not look it up a second time. A constant shown later is marked with
  ``n_("...")`` where it is defined and translated with ``iface_(value)`` where it is
  drawn. An operator button given such text always passes ``translate=False`` (its
  lookup would use the wrong context). Data (object, model, provider and file names,
  conversation titles) is never looked up: it passes ``translate=False``.
* **Canvas text** (chat, cards, welcome screen, showcase, Media Studio):
  ``text(iface_("Accept"), ...)``. Nothing on a canvas is translated by itself.
* **Chat notices**: ``{"role": "system", **notice("Moved {x}", x=y)}``. The stored
  ``text`` is the English sentence; the canvas draws ``ui_text.display_notice``.
* **C++**: ``IFACE_``, ``TIP_``, ``RPT_`` and ``BKE_report``, through ``BLT_pgettext``.

Rules for the text itself:

* Whole sentences and templates with named placeholders; no fragment joining and no
  ``(s)``. Counted phrases use ``plural_(count, one, other)``. Blender's catalogues have
  no plural forms, so each CLDR category (zero, one, two, few, many, other) is its own
  entry under the message context ``plural:<category>``; a missing form falls back to
  ``other``, then to English, never to a wrong form.
* A sentence that names a control uses that control's exact label, so the translation
  can match it.

Model-visible text stays English
================================

Translation happens at display time only. Tool specifications, schemas, guidance,
observations, stored chat text, history, restore data and tool results are never
translated:

* A system or error chat row stores its English ``text`` and a template recipe,
  ``i18n: [template, fields]``. ``ui_text.clean_recipe`` is the single gate (JSON
  scalars, at most 16 fields and 16,000 characters, the row's redaction applied to
  string fields); ``stored_recipe`` ignores a recipe that no longer renders, in
  English, to the stored text, so a rewritten row shows its own text. The handoff to
  a model reads ``text`` only.
* An error raised for the person carries ``display_text.Text`` (a ``str`` holding its
  template and fields) or ``studio_state.Templated``; ``str(error)`` is the English
  sentence that logs, tool results and stored errors keep.
* The modules a model reads (``sources.MODEL_FACING``) may not import ``ui_text``.
* ``test_native_translation_leak.py`` proves it with the pseudo-locale active for
  every lookup kind: every constant of the model-facing modules, the Codex plan tools
  and schema, and the plan schema are byte-identical to an English run (two fresh
  interpreters, one installing the pseudo-locale before importing the add-on); notices,
  errors and template errors keep English text while their display is translated;
  and the handoff and context-refresh seed carry no translated byte. The
  context-refresh notice is a display template in the controller, pinned word for
  word by the leak test. Localization therefore never changes an ``angelo_*`` tool
  contract or ``TOOLSET_VERSION``.

The Angelo text domain
======================

``locale/angelo/po/<language>.po`` compiles to
``datafiles/locale/<language>/LC_MESSAGES/angelo.mo`` beside Blender's ``blender.mo``
(``source/creator/CMakeLists.txt``, a loop beside Blender's own, globbed with
``CONFIGURE_DEPENDS`` so a new or changed catalogue rebuilds only the ``locales``
target). Blender's loader (``blentranslation/intern/messages.cc``) already accepted a
list of domains; ``BLT_lang_set`` passes ``blender`` and ``angelo`` and
``BLT_pgettext`` asks them in that order. Consequences: Blender's wording wins for a
message both define (Cancel, Preferences); Angelo's catalogue fills the gaps, including
languages Blender has translated only partly; Blender's catalogues are never edited, so
an upstream catalogue update merges without conflict; Python and C++ read the same
files. Only ``WITH_INTERNATIONAL=ON`` builds translate: ``blender_release.cmake`` (and so
``build_release_macos.sh``) sets it, the lite development build does not.

Runtime
=======

* Angelo follows Blender's language preference (Preferences > Interface > Language,
  also the Language list of the first-launch setup card, which shows each language in its own name). Canvases call ``iface_`` while
  drawing and nothing is translated at import, so a language change shows at once
  (the GUI suite switches languages in one session and returns to exact English).
* Languages map to Blender locales as in ``scripts/angelo/i18n/languages.py``
  (``pt`` is Portuguese (Portugal), ``zh`` Simplified, ``zh-TW`` Traditional).
* Fonts: the canvases draw with the macOS system font; Blender's glyph fallback
  supplies what it lacks from the bundled Noto fonts (CJK, Arabic, Thai, Devanagari).
  Coverage is measured, see "Glyph coverage".

Arabic and right-to-left text
=============================

Blender 5.2 has no bidirectional layout and no contextual shaping: its
``WITH_FRIBIDI``/``WITH_HARFBUZZ`` options exist but no source file uses them, and its
own Arabic catalogue is stored pre-shaped, in visual order. Angelo does the same:

* ``locale/angelo/src/ar.po`` is the reading-order text a translator edits;
  ``po/ar.po`` is generated from it by ``rtl_text.to_visual`` (``cli.py build-arabic``):
  joined presentation forms, runs reversed, ``{placeholders}``, Latin names, numbers and
  shortcuts kept left to right. The shaper reproduces Blender's fribidi-built
  catalogue on 8,865 of 8,866 strings (the exception is a ``+`` before a number). The
  host test fails when ``po/ar.po`` is not exactly the visual form of ``src/ar.po``.
* A canvas cannot wrap visual text (the end of the sentence would land on the first
  row), so ``chat_view.wrap`` converts a right-to-left paragraph back with
  ``rtl_text.to_logical`` (an exact inverse), wraps it in reading order and returns
  visual rows. ``elide`` cuts at the end of the reading order, so a long Arabic label
  loses its last words, on the left. Raw Arabic typed by a person or written by a model
  is shaped and reordered the same way (``canvas_rtl.visual``), in every interface
  language.
* **Mirroring** (``canvas_rtl.py``). In Arabic, the chat (messages, cards, popovers,
  hover and focus states), the welcome screen, the showcase and the notice cards (update
  notice, What's New, crash dialog: ``card_host``) draw between
  ``canvas_rtl.begin(width)`` and ``end()``, which put a mirror on the GPU matrix: every
  shape, stroke, ring and icon lands mirrored with no change to the layout code. Text
  and pictures are drawn upright at the mirrored place (``upright``), which
  right-aligns text; scissor rectangles are mirrored by hand (``scissor``); input
  mirrors the pointer's x before hit-testing (``pointer_x``). Left-to-right languages
  touch no GPU state. Not mirrored, deliberately: the composer's typed text, selection
  and caret (a left-to-right text field like every Blender field), and the welcome
  screen's small diagrams of Blender's real, unmirrored screen. A notice card's scrolling
  box whose content has no right-to-left text (the English crash report, release notes)
  keeps its left-to-right layout (``card_draw``).

Hindi and Thai
==============

Blender draws one glyph per character, with no shaping engine, in every script.

* Thai needs no reordering: vowels that precede a consonant are already stored first.
  Above- and below-base vowels and tone marks are separate characters drawn at the
  position the font gives them without GPOS mark positioning; stacked marks can sit
  too close or overlap. Text is readable.
* Thai has no spaces between words and Blender no dictionary to find them, so the Thai
  catalogue marks word boundaries with ZERO WIDTH SPACE (U+200B, drawn as nothing by
  every bundled font) and ``text_layout.line_break`` breaks there as after a space.
  Without the marks a canvas breaks between any two characters (a short label can
  leave one consonant on its second line). ``validate.break_problem`` rejects a
  misplaced mark. Blender-native widgets do not wrap; they clip as for every language.
* Devanagari needs shaping that Blender cannot do: conjuncts do not form (a consonant
  cluster shows its virama), and the vowel sign ि (U+093F), stored after its consonant,
  is drawn after it instead of before. Words remain recognisable, but the text is
  visibly unshaped. Blender's own Hindi catalogue (4% translated) has the same limit.
* Glyph coverage, rendering and screenshots are in "Glyph coverage" and
  "Verification".

Glyph coverage
==============

``scripts/angelo/i18n/measure_coverage.py`` draws every distinct character of each
shipped catalogue with each font that can draw it, Blender's fallback switched off,
into a CPU image, and compares it with the font's ``.notdef`` glyph.

Measured on the full-feature macOS arm64 build: **no character of any of the 20
catalogues lacks a glyph.** Distinct characters outside ASCII, and the font that draws
them:

========== ========== =========================================================
Language   Characters Drawn by
========== ========== =========================================================
ar         139        Noto Sans Arabic 130 (presentation forms), system font 9
hi         70         Noto Sans Devanagari 60, system font 9, Noto Sans Bengali 1
                      (the danda ``।``, shared by both scripts)
th         65         Noto Sans Thai 56, system font 9
ja         582        Noto Sans CJK 576, system font 6
ko         513        Noto Sans CJK 504, system font 9
zh         757        Noto Sans CJK 748, system font 9
zh-TW      743        Noto Sans CJK 736, system font 7
ru         72         system font (Cyrillic)
vi         95         system font (precomposed Latin with diacritics)
others     9-27       system font (Latin with diacritics)
========== ========== =========================================================

The fonts are Blender's bundled fallbacks (``release/datafiles/fonts``); the system
font is the macOS font the chat canvas loads. Without it (``--no-system-font``, as on
Windows and Linux, where the canvas draws with Blender's interface font) the result is
the same: Inter takes the system font's share and nothing is missing.

Limits (all documented, none silent)
====================================

* Blender's own catalogues are partial for some of the 21 languages (percentages in
  ``locale/languages``: Hindi 4%, Thai 5%, Dutch 7%, Indonesian 21%, Arabic 23%, German
  35%). Blender's menus and editors stay English where Blender has no translation;
  Angelo's own text is complete in all 20. Blender's catalogues are Blender's to
  complete upstream; Angelo does not fork them.
* Blender-native widgets (menus, Preferences, the Properties editor, operator dialogs)
  are not mirrored by Blender; they draw Angelo's Arabic shaped and in visual order,
  left-aligned. A native label too long for its widget is clipped on the right, which
  for Arabic is the beginning of the sentence (Blender's own Arabic has the same limit).
* Typed text in Blender's own text fields and in the composer is not shaped or
  reordered for Arabic.
* Media Studio's editor is not mirrored in Arabic; its text is shaped and wrapped in
  reading order.
* Hindi and Thai: see "Hindi and Thai".
* Provider, server and model text is shown as received, in the language it came in;
  fixed frames around it are translated, the detail is not.
* Machine translation: ``locale/angelo/REVIEW.md`` lists what a native reviewer should
  check first, per language.
* **Hindi shaping.** The visible defects are the pre-base vowel sign and conjuncts. The
  first could be reordered in the shipped catalogue as Arabic is; conjuncts need a
  shaping engine (HarfBuzz in BLF), which is a Blender-wide change.
* **Tab names have no context.** Blender draws sidebar tab names (``bl_category``) with
  ``IFACE_`` in the default context, so Blender's wording wins for "Flow" (Vietnamese
  shows its physics term). A per-panel context for tab names would be a Blender change.
* **Tall marks in tight rows.** The approval card's title sits close to the card's top
  edge in every language; Thai and Devanagari marks above the line touch it.

Verification
============

Host tests (``run_host_tests.py``, no Blender):

* ``test_native_translation.py``: the bypass scanner over every UI module, startup
  script and chat notice; every marked and native literal is in the template, once,
  without padded fragments; text Angelo adds to Blender's own UI scripts
  (``sources.SHARED_PYTHON_FILES``: Preferences, the top bar and Help menu, the URL
  presets, splash and About) is in the template unless Blender's catalogues know it; the 20 catalogues are complete, keep placeholders,
  key combinations, line breaks and product names, cover every plural category of
  their language, are marked machine-produced and never fuzzy; Arabic ships in
  Blender's visual form; the second text domain is wired.
* ``test_native_translation_leak.py``: the pseudo-locale leak proof above.
* ``test_native_notices.py``: notice recipes on emit, save, load and display.
* ``test_native_canvas_rtl.py``: what is mirrored and what stays upright, paired
  ``begin``/``end``, pointer and scissor mirroring, Arabic wrapping and eliding.

On the full build (``build_release_macos.sh``):

* ``test_blender_translation.py`` (GUI, ``run_native_suites.py --gui --include
  test_blender_translation``): one process walks English, the chosen languages
  (``ANGELO_L10N_LOCALES``, Blender locale ids; default ``ja_JP,ar_EG,de_DE,zh_HANT``)
  and English again, switching the interface language at runtime; per language it
  captures the empty chat's connect card, the welcome screen, Preferences > AI
  Providers, a conversation with a notice, an error and a script approval card, and
  the crash dialog, What's New and the update notice (``translation_cards.py``), and
  checks that catalogue text reached Python, that the RTL flag matches and, in
  Arabic, that the pointer at a button's mirrored place reaches it, on the chat and on
  the crash dialog.
  ``ANGELO_L10N_PSEUDO=1`` runs the pseudo-locale (``⟦ ⟧``, accented, 40% longer):
  English on a canvas means a bypass, clipping means a translation of that length
  would overflow.
* ``measure_widths.py`` and ``measure_coverage.py`` (``--background``). On the
  full-feature macOS arm64 build, most messages ``measure_widths.py`` flags (wider
  than the English by more than their budget) are sentences that wrap (Japanese 144,
  Russian 71, the other languages 0-21); the screenshots show no button or tab
  overflowing.

Pass the languages outside the default set (for example Hindi, Thai and Vietnamese) in
``ANGELO_L10N_LOCALES`` as well, and read every screenshot: Hindi shows the unshaped
Devanagari described above, and Thai, with its word breaks, wraps between words.

How to add a string
===================

1. Write the English as a whole sentence or a template with named placeholders.
   Choose the route from "How a string reaches the screen": a literal in a Blender
   widget, ``iface_``/``tip_``/``rpt_``/``plural_`` for built text, ``n_`` for a constant
   drawn later, ``notice`` for a chat row. Text a model reads is never wrapped.
2. Run ``python3 scripts/angelo/i18n/cli.py scan`` (no findings), or the host test.
   Deliberately English text in a UI module carries ``# i18n: <reason>`` on its line.
   A new add-on module that draws must be added to ``sources.UI_MODULES`` (or
   ``NON_UI_MODULES`` with a reason); the host test says so.
3. Refresh the template and catalogues ("How to update catalogues").

A new string arrives untranslated: the scanner and the template test fail until it is
wrapped and extracted, and the catalogue test until all 20 languages translate it.
New Thai text needs its word breaks (``locale/angelo/README.md``, rule 8); without
them it still wraps, but between any two characters.

How to update catalogues
========================

.. code-block:: sh

   # 1. The template, inside an app whose add-on is this checkout's (a development app links it).
   Angelo.app/Contents/MacOS/Angelo --background --factory-startup \
     --python scripts/angelo/i18n/extract_pot.py
   # 2. Merge it into every catalogue: new messages arrive empty, removed ones become obsolete (#~).
   python3 scripts/angelo/i18n/cli.py update
   # 3. Translate the empty entries, per language, in work files a translator (person or model) fills:
   python3 scripts/angelo/i18n/cli.py hints ja > work/ja/blender_wording.json
   python3 scripts/angelo/i18n/cli.py chunks ja work/ja      # chunk-NN.json: messages still empty
   #    ... write chunk-NN.out.json (a list of strings, same order) following GLOSSARY.md ...
   python3 scripts/angelo/i18n/cli.py apply ja work/ja       # validated merge; rejects broken entries
   # Arabic: chunks/apply write src/ar.po (reading order); then
   python3 scripts/angelo/i18n/cli.py build-arabic
   # 4. Validate and measure.
   python3 scripts/angelo/i18n/cli.py check --warnings
   python3 scripts/angelo/i18n/cli.py coverage
   scripts/angelo/build_release_macos.sh                    # compiles angelo.mo (locales target only)

The extractor runs Blender's own Python extractor on the UI modules only and takes
explicit marks from every other non-model module; it drops what Blender's extractor
makes up rather than finds (lookup keys inside a translated argument, joined literals
of one argument) and prints them, and spells the default context one way, so each
message is listed once.

Native review
=============

Review goes language by language, starting with the items in ``REVIEW.md``. A reviewed
language changes its header's ``X-Angelo-Translation`` field to name the reviewer and
the date; the host test still requires the field.
