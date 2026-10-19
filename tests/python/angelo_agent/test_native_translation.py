# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's interface is translatable in 21 languages, and stays that way.

Dependency-free (no ``bpy``). Pins: no user-facing string bypasses Blender's
translation system (scanner over the UI modules, chat notices everywhere);
every marked message is in ``locale/angelo/angelo.pot``; the 20 catalogues are
complete and keep placeholders, shortcuts and product names; Arabic ships in
Blender's shaped visual order; the runtime and build plumbing (second text
domain) is wired; and no translated byte can reach a model.

The refresh procedure after strings move is in ``locale/angelo/README.md``.
"""

import ast
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from i18n import catalogs, languages, po, pseudo, scan, sources, validate  # noqa: E402
from angelo_agent import rtl_text, ui_text  # noqa: E402

ADDON = sources.ADDON


def module_paths():
    return sorted(ADDON.glob("*.py"))


class BypassScannerTests(unittest.TestCase):
    def test_ui_modules_have_no_untranslatable_string(self):
        for path in module_paths():
            if path.stem not in sources.UI_MODULES:
                continue
            with self.subTest(module=path.name):
                findings, _ = scan.scan(path.read_text(encoding="utf-8"), path.name)
                self.assertEqual([], findings, "Wrap with n_/iface_/tip_/rpt_/plural_/notice, or add `# i18n: reason`")

    def test_every_module_keeps_chat_notices_translatable(self):
        for path in module_paths():
            with self.subTest(module=path.name):
                findings, _ = scan.scan(path.read_text(encoding="utf-8"), path.name, net=False)
                self.assertEqual([], findings)

    def test_startup_scripts_have_no_untranslatable_string(self):
        for relative in sources.STARTUP_FILES:
            with self.subTest(file=relative):
                findings, _ = scan.scan((ROOT / relative).read_text(encoding="utf-8"), relative)
                self.assertEqual([], findings)

    def test_exemptions_state_a_reason(self):
        for path in module_paths():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "# i18n:" in line:
                    self.assertTrue(scan.EXEMPT.search(line), f"{path.name}:{number} needs a reason after '# i18n:'")

    def test_ui_looking_modules_are_classified(self):
        indicators = ("blf.", "layout.", "bl_label", ".report(", "iface_(", "n_(", "tip_(", "rpt_(")
        for path in module_paths():
            source = path.read_text(encoding="utf-8")
            if any(token in source for token in indicators):
                with self.subTest(module=path.name):
                    self.assertTrue(path.stem in sources.UI_MODULES or path.stem in sources.NON_UI_MODULES
                                    or path.stem in MODEL_SIDE_UI_USERS,
                                    f"classify {path.name} in scripts/angelo/i18n/sources.py (UI_MODULES or NON_UI_MODULES)")

    def test_marked_strings_are_single_literals(self):
        for path in module_paths():
            with self.subTest(module=path.name):
                findings, _ = scan.scan(path.read_text(encoding="utf-8"), path.name, net=False)
                self.assertEqual([], [f for f in findings if f.kind == "fragment"])

    def test_scanner_recognises_the_bypasses_it_exists_for(self):
        cases = {
            "text('Accept', 1, 2)": "bare",
            "layout.label(text=f'Connected to {name}')": "built",
            "self.report({'INFO'}, 'Placed ' + str(n) + ' objects')": "built",
            "x = iface_('A' + 'B')": "fragment",
            "emit('message', {'role': 'system', 'text': 'Document changed.'})": "notice",
            "layout.label(text=name + ' · ' + kind)": "data",
            "layout.operator('a.b', text=iface_('Accept'))": "double",
            "layout.operator('a.b', text=n_('Use'))": "context",
            "layout.operator('a.b', text=label)": "context",
        }
        for source, kind in cases.items():
            with self.subTest(source=source):
                findings, _ = scan.scan(source, "case.py")
                self.assertIn(kind, {finding.kind for finding in findings})

    def test_scanner_accepts_the_correct_spellings(self):
        for source in ("text(iface_('Accept'), 1, 2)", "layout.label(text='Done')", "class A:\n bl_label = 'Run'",
                       "emit('message', {'role': 'system', **notice('Document changed.')})",
                       "raise ValueError('internal wording')", "text(f'{n} items', 1, 2)  # i18n: demo",
                       "layout.label(text=name + ' · ' + kind, translate=False)",
                       "layout.operator('a.b', text=iface_('Accept'), translate=False)",
                       "layout.operator('a.b', text='Save' if new else 'Continue')",
                       "layout.operator('a.b', text=name, translate=False)"):
            with self.subTest(source=source):
                self.assertEqual([], scan.scan(source, "case.py")[0])


# Modules that mention UI helpers only to build notices or model-side text; the scanner still checks their notices.
MODEL_SIDE_UI_USERS = {"controller", "providers", "codex_plan", "voice", "sessions", "cli_runtime", "external_host",
                       "snapshot_service", "external_requests", "change_feed", "turn_context", "files", "memory_projects",
                       "references", "routing", "generative", "chat_changes", "session_validation", "credential_manager_windows",
                       "studio_generation", "studio_http", "studio_state", "studio_enhance"}


class TemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header, cls.template = po.read(str(sources.POT))
        cls.keys = {entry.key for entry in cls.template}
        cls.ids = {entry.id for entry in cls.template}

    def test_every_marked_message_is_in_the_template(self):
        missing = []
        for path in module_paths():
            _, marked = scan.scan(path.read_text(encoding="utf-8"), path.name, net=False)
            missing += [f"{path.name}:{line} {msgid!r}" for msgid, line, _ in marked if msgid not in self.ids]
        self.assertEqual([], missing, "Regenerate the template: scripts/angelo/i18n/extract_pot.py (see locale/angelo/README.md)")

    def test_text_angelo_adds_to_blenders_own_scripts_is_in_the_template(self):
        """Blender's UI scripts that Angelo edits (``sources.SHARED_PYTHON_FILES``: Help menu, splash, About,
        Preferences) keep Blender's catalogues for Blender's text; what those catalogues do not know is Angelo's
        and must be extracted, or it shows in English in every language (the extractor keeps the same set)."""
        known = set()
        for name in ("zh_HANS", "ja"):  # The two most complete Blender catalogues, as the extractor uses.
            known.update(entry.id for entry in po.read(str(ROOT / "locale/po" / f"{name}.po"))[1])
        wrappers = {"iface_", "tip_", "rpt_", "pgettext_iface", "pgettext_tip", "pgettext_rpt"}
        missing = []
        for relative in sources.SHARED_PYTHON_FILES:
            for node in ast.walk(ast.parse((ROOT / relative).read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                name = scan.function_name(node)
                texts = [node.args[0]] if name in wrappers and node.args else []
                texts += [kw.value for kw in node.keywords if kw.arg == "text" and name in scan.LAYOUT_CALLS]
                for value in texts:
                    message = value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None
                    if (message and scan.human(message, single_word_ok=True) and message not in known
                            and message not in self.ids):
                        missing.append(f"{relative}:{node.lineno} {message!r}")
        self.assertEqual([], missing, "Regenerate the template: scripts/angelo/i18n/extract_pot.py (see locale/angelo/README.md)")

    def test_plural_families_cover_every_category(self):
        for entry in self.template:
            if entry.ctxt == "plural:one":
                other = [e for e in self.template if e.ctxt == "plural:other"]
                self.assertTrue(other, "a plural_ family needs its other form")
        categories = {e.ctxt for e in self.template if e.ctxt.startswith("plural:")}
        if categories:
            self.assertGreaterEqual(categories, {"plural:one", "plural:other", "plural:few", "plural:many"})

    def test_each_message_is_in_the_template_once(self):
        self.assertEqual(len(self.template), len(self.keys), "one entry per (context, message)")

    def test_template_has_no_padded_fragments(self):
        for entry in self.template:
            self.assertEqual(entry.id.strip(), entry.id, entry.id)


class CatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = catalogs.check_all()

    def test_all_twenty_languages_exist_and_english_is_the_source(self):
        self.assertEqual(20, len(languages.LANGUAGES))
        self.assertEqual({"ar", "de", "es", "fr", "hi", "id", "it", "ja", "ko", "nl", "pl", "pt", "pt-BR", "ru", "sv", "th",
                          "tr", "vi", "zh", "zh-TW"}, {language.code for language in languages.LANGUAGES})

    def test_each_catalogue_is_complete_and_valid(self):
        for code, (issues, missing, translated, required) in self.results.items():
            with self.subTest(language=code):
                errors = [issue for issue in issues if issue.severity == "error"]
                self.assertEqual([], errors[:10])
                self.assertEqual(translated, required)

    def test_catalogues_are_marked_machine_produced_pending_review(self):
        for language in languages.LANGUAGES:
            header, _ = po.read(str(catalogs.catalog_path(language)))
            self.assertTrue(validate.status_ok(po.header_fields(header).get(validate.STATUS_FIELD, "")), language.code)

    def test_review_status_is_machine_produced_or_a_named_sign_off(self):
        self.assertTrue(validate.status_ok(validate.STATUS_VALUE))
        self.assertTrue(validate.status_ok("reviewed by Aiko Tanaka on 2026-11-02"))
        for value in ("", "reviewed", "done", "machine-produced", "reviewed by  on 2026-11-02"):
            self.assertFalse(validate.status_ok(value), value)

    def test_positional_templates_from_blenders_scripts_are_checked_like_named_ones(self):
        # Blender's own UI scripts format with positional fields ("Version: {:s} {:s}" in About).
        japanese, source = languages.BY_CODE["ja"], po.Entry("Version: {:s} {:s}", "", "")

        def errors(text):
            return [i.message for i in validate.check_entry(japanese, source, po.Entry(source.id, text, ""))
                    if i.severity == "error"]
        self.assertEqual([], errors("バージョン: {:s} {:s}"))
        self.assertTrue(errors("バージョン: {:s}"))
        self.assertTrue(errors("バージョン: {:s} {name}"))
        positional, named = validate.format_arguments("{0} {1:d} {name}")
        self.assertEqual((2, ["name"]), (len(positional), list(named)))
        self.assertEqual("  ", "{0} {1:d} {name}".format(*positional, **named))

    def test_thai_word_breaks_sit_between_words_only(self):
        self.assertIsNone(validate.break_problem("ทำริก\u200bและ\u200bจัดท่า\u200bตัวละคร"))
        for text in ("\u200bตัวละคร", "ตัวละคร\u200b", "ตัว \u200bละคร", "เ\u200bปิด", "ต\u200b\u0e31ว", "Angelo\u200bApp"):
            self.assertIsNotNone(validate.break_problem(text), text)
        self.assertEqual(validate.width("ทำริกและ", "th"), validate.width("ทำริก\u200bและ", "th"))

    def test_no_catalogue_entry_is_fuzzy(self):
        for language in languages.LANGUAGES:
            _, entries = po.read(str(catalogs.catalog_path(language)))
            self.assertEqual([], [e.id for e in entries if "fuzzy" in e.flags], language.code)

    def test_blenders_loader_can_read_every_catalogue(self):
        # The in-tree msgfmt (and so the .mo) needs UTF-8, an empty-msgid header and non-empty msgstr entries.
        for language in languages.LANGUAGES:
            path = catalogs.build_catalog_path(language)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith('msgid ""\nmsgstr ""'), language.code)
            self.assertIn("charset=UTF-8", text, language.code)

    def test_every_language_translates_every_plural_category_it_needs(self):
        _, template = po.read(str(sources.POT))
        for language in languages.LANGUAGES:
            _, entries = po.read(str(catalogs.catalog_path(language)))
            done = {e.key for e in entries if e.str.strip()}
            for entry in template:
                if entry.ctxt.startswith("plural:") and entry.ctxt[7:] in language.plurals:
                    self.assertIn(entry.key, done, (language.code, entry.ctxt, entry.id))


class ArabicTests(unittest.TestCase):
    def test_build_file_is_the_shaped_visual_form_of_the_source(self):
        _, source = po.read(str(catalogs.catalog_path(languages.BY_CODE["ar"])))
        _, built = po.read(str(catalogs.build_catalog_path(languages.BY_CODE["ar"])))
        shipped = {e.key: e.str for e in built}
        for entry in source:
            if entry.str and not entry.obsolete:
                self.assertEqual(rtl_text.to_visual(entry.str), shipped.get(entry.key), entry.id)

    def test_shaping_matches_blenders_own_fribidi_built_catalogue(self):
        _, entries = po.read(str(ROOT / "locale/po/ar.po"))
        sample = [e.str.replace("\ufeff", "") for e in entries if e.str][:3000]
        same = sum(rtl_text.to_visual(rtl_text.to_logical(text)) == text for text in sample)
        self.assertGreaterEqual(same / len(sample), 0.995)

    def test_visual_form_round_trips(self):
        for text in ("تم تطبيق {count} تغييرات على Blender.", "اضغط Ctrl+Alt+D للتبديل", "(الإعدادات) 3.5%", "لا شيء"):
            self.assertEqual(text, rtl_text.to_logical(rtl_text.to_visual(text)))

    def test_a_vowel_mark_follows_its_letter_in_visual_order_as_in_blenders_catalogue(self):
        # Blender draws a mark over the glyph before it: "تخطَّ" must end visually as TAH, FATHA, SHADDA.
        visual = rtl_text.to_visual("تخطَّ هذا")
        self.assertTrue(visual.endswith("\ufec2\u064e\u0651\ufea8\ufe97"), [hex(ord(ch)) for ch in visual])
        _, entries = po.read(str(ROOT / "locale/po/ar.po"))
        marked = [e.str.replace("\ufeff", "") for e in entries if re.search("[\u064b-\u0652]", e.str)]
        self.assertGreater(len(marked), 100)
        for text in marked:  # Read back in reading order, no mark starts a word: each follows its letter.
            self.assertIsNone(re.search(r"(?:^| )[\u064b-\u0652]", rtl_text.to_logical(text)), text)
            self.assertEqual(text, rtl_text.to_visual(rtl_text.to_logical(text)))

    def test_a_template_is_filled_in_reading_order(self):
        # In visual order "{job} · {title}: {state}" is one left-to-right run; Arabic filled into it there
        # would read state, title, job. Filled in reading order, the sentence reads as written.
        template = "المهمة {job} · {title}: {state}"
        title, state = "جارٍ تصيير حركة", "أنهى Blender التصيير مبكرًا"
        catalogue = {"Job {job} · {title}: {state}": template, "Rendering": title, "Ended": state}
        lookup = lambda message, context=None: rtl_text.to_visual(catalogue.get(message, message))  # noqa: E731
        with patch.dict(ui_text._pgettext, {"iface": lookup, "tip": lookup, "rpt": lookup}):
            shown = ui_text.iface_("Job {job} · {title}: {state}", job="j3", title=ui_text.iface_("Rendering"),
                                   state=ui_text.iface_("Ended"))
        self.assertEqual("المهمة j3 · جارٍ تصيير حركة: أنهى Blender التصيير مبكرًا", rtl_text.to_logical(shown))
        self.assertEqual(shown, rtl_text.to_visual(rtl_text.to_logical(shown)))

    def test_pieces_are_joined_in_reading_order(self):
        frames, percent, eta = "72 من 240 إطارًا", "30%", "يتبقى نحو 2 د 22 ث"
        shown = ui_text.join([rtl_text.to_visual(frames), percent, rtl_text.to_visual(eta)])
        self.assertEqual(f"{frames} · {percent} · {eta}", rtl_text.to_logical(shown))
        self.assertEqual("Rendering · 30% · 2 min left", ui_text.join(["Rendering", "30%", "2 min left"]))
        for name in ("job_chat.py", "job_ui.py"):   # Translated pieces go through ui_text.join.
            source = (ROOT / "scripts/addons_core/angelo_agent" / name).read_text(encoding="utf-8")
            self.assertNotRegex(source, r'" · "\.join|\+ " · "|" · " \+', name)

    def test_every_shipped_arabic_message_reads_back_exactly(self):
        # Wrapping and browser pages read the visual form back; a lossy round trip would change the text.
        _, source = po.read(str(catalogs.catalog_path(languages.BY_CODE["ar"])))
        for entry in source:
            if entry.str and not entry.obsolete:
                self.assertEqual(entry.str, rtl_text.to_logical(rtl_text.to_visual(entry.str)), entry.id)

    def test_lam_alef_keeps_its_vowel_mark_and_drops_only_the_alef(self):
        visual = rtl_text.to_visual("أضف اتصالًا")
        self.assertEqual(2, visual.count("\u0627") + visual.count("\ufe8d") + visual.count("\ufe8e"),
                         "three alefs, the last drawn by the lam-alef ligature")
        self.assertRegex(visual, "[\ufefb\ufefc]\u064b", "the fathatan follows the ligature it sits on")

    def test_numbers_keep_their_places_and_stay_with_latin_names(self):
        visual = rtl_text.to_visual("الأطر 1–240 · 1920×1080 · 128 عينةً · PNG")
        self.assertEqual(["1–240", "1920×1080", "128"], re.findall(r"[\d–×]+", visual)[::-1],
                         "read right to left, the numbers come in reading order, each whole")
        self.assertIn("Angelo 1.1", rtl_text.to_visual("يتوفر Angelo 1.1"))

    def test_placeholders_and_names_stay_left_to_right_and_intact(self):
        visual = rtl_text.to_visual("تم تطبيق {count} تغييرات على Blender")
        self.assertIn("{count}", visual)
        self.assertIn("Blender", visual)

    def test_wrapping_happens_in_reading_order(self):
        logical = "هذه جملة طويلة جدا تحتاج إلى عدة أسطر لتظهر بالترتيب الصحيح"
        rows = rtl_text.rows_to_visual(rtl_text.to_visual(logical), lambda text: len(text) * 8.0, 160)
        self.assertGreater(len(rows), 1)
        self.assertEqual(logical.split()[0], rtl_text.to_logical(rows[0]).split()[0])


class RuntimeTests(unittest.TestCase):
    def test_plural_forms_follow_cldr_for_the_shipped_languages(self):
        expected = {("ru", 1): "one", ("ru", 3): "few", ("ru", 5): "many", ("ru", 21): "one", ("pl", 22): "few",
                    ("ar", 0): "zero", ("ar", 2): "two", ("ar", 7): "few", ("ar", 11): "many", ("ja", 5): "other",
                    ("fr", 0): "one", ("pt_BR", 1): "one", ("de", 2): "other"}
        for (code, count), category in expected.items():
            self.assertEqual(category, ui_text.plural_category(code, count), (code, count))

    def test_plural_picks_the_catalogue_form_and_falls_back_to_english(self):
        catalogue = {("plural:few", "{count} changes"): "{count} изменения", ("plural:other", "{count} changes"): "{count} изм."}
        calls = []

        def fake(message, context=None):
            calls.append(context)
            return catalogue.get((context, message), message)

        original, ui_text._pgettext["iface"] = ui_text._pgettext.get("iface"), fake
        language, ui_text.language = ui_text.language, lambda: "ru"
        try:
            self.assertEqual("3 изменения", ui_text.plural_(3, "{count} change", "{count} changes"))
            # No "many" entry: the "other" form is the next best thing, never a wrong English plural.
            self.assertEqual("5 изм.", ui_text.plural_(5, "{count} change", "{count} changes"))
            catalogue.clear()
            self.assertEqual("5 changes", ui_text.plural_(5, "{count} change", "{count} changes"))
        finally:
            ui_text.language = language
            ui_text._pgettext["iface"] = original
        self.assertEqual("1 change", ui_text.plural_(1, "{count} change", "{count} changes"))

    def test_a_message_marked_with_angelos_context_keeps_angelos_meaning(self):
        # Blender's catalogue knows "Keys" as keyframes; Angelo marks its own "Keys" (API keys) with the context.
        catalogue = {(None, "Keys"): "Puntos clave", ("Angelo", "Keys"): "Claves", (None, "Cancel"): "Cancelar"}
        original = ui_text._pgettext.get("iface")
        ui_text._pgettext["iface"] = lambda message, context=None: catalogue.get((context, message), message)
        try:
            self.assertEqual("Claves", ui_text.iface_(ui_text.n_("Keys", "Angelo")))
            self.assertEqual("Cancelar", ui_text.iface_("Cancel"), "everything else is Blender's lookup, unchanged")
            self.assertEqual("Puntos clave", catalogue[(None, "Keys")])
        finally:
            ui_text._pgettext["iface"] = original

    def test_a_bad_translation_never_breaks_a_draw(self):
        original, ui_text._pgettext["iface"] = ui_text._pgettext.get("iface"), lambda message, context=None: "{missing} {count}"
        try:
            self.assertEqual("3 files", ui_text.iface_("{count} files", count=3))
        finally:
            ui_text._pgettext["iface"] = original

    def test_notice_keeps_english_text_and_a_display_recipe(self):
        message = ui_text.notice("Moved to {place}.", place="Library")
        self.assertEqual("Moved to Library.", message["text"])
        original = ui_text._pgettext.get("iface")
        ui_text._pgettext["iface"] = lambda text, context=None: {"Moved to {place}.": "{place}に移動しました。"}.get(text, text)
        try:
            self.assertEqual("Libraryに移動しました。", ui_text.display_notice({"role": "system", **message}))
            self.assertEqual("Moved to Library.", message["text"])
        finally:
            ui_text._pgettext["iface"] = original

    def test_the_second_text_domain_is_wired(self):
        translation = (ROOT / "source/blender/blentranslation/intern/blt_translation.cc").read_text()
        language = (ROOT / "source/blender/blentranslation/intern/blt_lang.cc").read_text()
        header = (ROOT / "source/blender/blentranslation/BLT_translation.hh").read_text()
        cmake = (ROOT / "source/creator/CMakeLists.txt").read_text()
        self.assertIn('#define ANGELO_TEXT_DOMAIN_NAME "angelo"', header)
        self.assertIn("{TEXT_DOMAIN_NAME, ANGELO_TEXT_DOMAIN_NAME}", language.replace("\n", " ").replace("  ", " "))
        self.assertIn("for (const int domain : {0, 1})", translation)
        self.assertIn("RENAME angelo.mo", cmake)
        self.assertIn("locale/angelo", cmake.replace("${_locale_dir}/angelo", "locale/angelo"))
        self.assertIn("ON  CACHE BOOL", (ROOT / "build_files/cmake/config/blender_release.cmake").read_text()
                      .split("WITH_INTERNATIONAL")[1].split("\n")[0])

    def test_console_editor_names_are_translated_when_drawn(self):
        source = (ROOT / "source/blender/editors/space_console/space_console.cc").read_text()
        for name in ("Generative AI", "Coding Agent", "Python Console"):
            self.assertIn(f'IFACE_("{name}")', source)


class ModelVisibleBytesTests(unittest.TestCase):
    """Translation changes what a person reads, never what a model reads."""

    MODEL_FACING = sorted(sources.MODEL_FACING)

    def test_model_facing_modules_never_translate(self):
        for name in self.MODEL_FACING:
            path = ADDON / f"{name}.py"
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            with self.subTest(module=name):
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("ui_text"):
                        self.fail("model-facing text must not go through ui_text")
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                        self.assertNotIn(node.func.id, {"iface_", "tip_", "rpt_", "plural_", "notice", "display_notice"})

    def test_pseudo_locale_marks_only_translated_text(self):
        self.assertTrue(pseudo.pseudo("Accept").startswith(pseudo.OPEN))
        marked = ui_text._pgettext.get("iface")
        ui_text._pgettext["iface"] = lambda text, context=None: pseudo.pseudo(text)
        try:
            self.assertIn(pseudo.OPEN, ui_text.iface_("Accept"))
            stored = ui_text.notice("Moved to {place}.", place="Library")["text"]
            self.assertNotIn(pseudo.OPEN, stored, "stored notice text must stay English")
        finally:
            ui_text._pgettext["iface"] = marked


if __name__ == "__main__":
    unittest.main()
