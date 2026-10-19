# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The 21 languages Angelo 1.0 ships, and how each maps onto Blender's language system.

Blender names a language by an ISO locale (``ja_JP``, ``zh_HANS``) that selects
``datafiles/locale/<name>/LC_MESSAGES``; the folder name is the ``.po`` file
name in ``locale/po`` and in Angelo's own ``locale/angelo/po``. English is the
source language and has no catalogue.
"""

from collections import namedtuple

Language = namedtuple("Language", "code locale po english native plurals rtl")

ONE_OTHER = ("one", "other")
LANGUAGES = (
    Language("ar", "ar_EG", "ar", "Arabic", "العربية", ("zero", "one", "two", "few", "many", "other"), True),
    Language("de", "de_DE", "de", "German", "Deutsch", ONE_OTHER, False),
    Language("es", "es", "es", "Spanish", "Español", ONE_OTHER, False),
    Language("fr", "fr_FR", "fr", "French", "Français", ONE_OTHER, False),
    Language("hi", "hi_IN", "hi", "Hindi", "हिन्दी", ONE_OTHER, False),
    Language("id", "id_ID", "id", "Indonesian", "Bahasa Indonesia", ("other",), False),
    Language("it", "it_IT", "it", "Italian", "Italiano", ONE_OTHER, False),
    Language("ja", "ja_JP", "ja", "Japanese", "日本語", ("other",), False),
    Language("ko", "ko_KR", "ko", "Korean", "한국어", ("other",), False),
    Language("nl", "nl_NL", "nl", "Dutch", "Nederlands", ONE_OTHER, False),
    Language("pl", "pl_PL", "pl", "Polish", "Polski", ("one", "few", "many"), False),
    Language("pt", "pt_PT", "pt", "Portuguese (Portugal)", "Português europeu", ONE_OTHER, False),
    Language("pt-BR", "pt_BR", "pt_BR", "Portuguese (Brazil)", "Português brasileiro", ONE_OTHER, False),
    Language("ru", "ru_RU", "ru", "Russian", "Русский", ("one", "few", "many"), False),
    Language("sv", "sv_SE", "sv", "Swedish", "Svenska", ONE_OTHER, False),
    Language("th", "th_TH", "th", "Thai", "ภาษาไทย", ("other",), False),
    Language("tr", "tr_TR", "tr", "Turkish", "Türkçe", ONE_OTHER, False),
    Language("vi", "vi_VN", "vi", "Vietnamese", "Tiếng Việt", ("other",), False),
    Language("zh", "zh_HANS", "zh_HANS", "Chinese (Simplified)", "简体中文", ("other",), False),
    Language("zh-TW", "zh_HANT", "zh_HANT", "Chinese (Traditional)", "繁體中文", ("other",), False),
)
BY_CODE = {language.code: language for language in LANGUAGES}
BY_PO = {language.po: language for language in LANGUAGES}
PLURAL_CATEGORIES = ("zero", "one", "two", "few", "many", "other")

# Names that are never translated: products and companies the interface refers to.
PRODUCT_NAMES = ("Angelo", "Blender", "Claude", "ChatGPT", "Codex", "OpenAI", "Anthropic")
