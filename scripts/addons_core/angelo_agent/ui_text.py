# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Translatable interface text for Angelo's own UI, following Blender's conventions.

Source strings are English and written whole: a sentence or a template with
named placeholders (``"Usage limit reached · resets {time}"``), never a
sentence assembled from fragments, so a translator can reorder every part.

* ``n_("...")`` marks a constant for extraction and returns it unchanged.
* A message that Blender's own catalogue also translates, with another meaning
  ("Keys" as keyframes, "Manual" as the handbook, "Prompt" as the cursor), takes
  the message context ``"Angelo"`` as a second, literal argument where it is
  marked: ``n_("Keys", "Angelo")``, ``iface_("Close", "Angelo")``. Blender's
  catalogue is asked first for every lookup, so only a context it does not know
  reaches Angelo's wording; ``iface_``/``tip_``/``rpt_`` therefore try the
  ``"Angelo"`` context first, and a value marked that way is translated with
  Angelo's meaning wherever it is drawn (one extra dictionary lookup).
  Translate it where it is shown, with ``iface_`` (or a native ``text=``,
  ``bl_label`` or enum item, which Blender translates itself).
* ``iface_("...", **fields)`` translates button, label and card text at display
  time through ``bpy.app.translations.pgettext_iface`` and then fills named
  placeholders. ``tip_`` and ``rpt_`` do the same for tooltips and reports.
* ``plural_(count, one, other, **fields)`` picks the grammatical number form of
  the active language (Blender's catalogues have no plural forms, so each form
  is its own entry under the message context ``plural:<category>``).
* ``notice(template, **fields)`` builds a chat message whose stored ``text`` stays
  English, because chat history is persisted and parts of it are model-visible,
  and records the template so the canvas can translate it when it draws.

Blender's message extractor (``_bl_i18n_utils``) recognises these names, so the
literals reach the translation catalogue (``locale/angelo``, see its README).
Translate only at display time: never chat history, provider text, stored state
or anything a model sees. No ``bpy`` at import, so the rules stay host-testable
(text is returned as-is).
"""

import math

DOMAIN_CONTEXT = None
RTL_LANGUAGES = frozenset({"ar", "fa", "he", "ur"})

_pgettext = {}


def _gettext(kind, message, context=None):
    function = _pgettext.get(kind)
    if function is None:
        try:
            from bpy.app import translations
            function = {"iface": translations.pgettext_iface, "tip": translations.pgettext_tip,
                        "rpt": translations.pgettext_rpt}[kind]
        except ImportError:  # Host tests: no Blender, English text.
            function = _identity
        _pgettext[kind] = function
    if context is None:
        translated = function(message, ANGELO)  # A message whose Blender meaning differs (see the module notes).
        return translated if translated != message else function(message)
    return function(message, context)


def _identity(message, context=None):
    return message


ANGELO = "Angelo"  # The context of messages whose Blender meaning differs (documented above).


def n_(message, msgctxt=None):
    return message


def _fill(translated, message, fields):
    if not fields:
        return translated
    try:
        if _visual_rtl(translated):
            return _fill_visual(translated, fields)
        return translated.format(**fields)
    except (KeyError, IndexError, ValueError):
        # A catalogue entry with a wrong placeholder must never break a draw callback:
        # show the English template instead (the catalogue validator rejects such entries).
        return message.format(**fields)


def _visual_rtl(text):
    return any("\ufb50" <= ch <= "\ufeff" for ch in text)   # Arabic presentation forms: a pre-shaped catalogue entry.


def _fill_visual(translated, fields):
    """Fill a right-to-left template in reading order, then put the whole sentence in visual order.

    The shipped Arabic is pre-shaped and in visual order, where a run of placeholders joined by
    neutral characters ("{job} · {title}: {state}") reads left to right; filling Arabic into it there
    would put the fields in the wrong order. Reading order is exact both ways (``rtl_text``).
    """
    from . import rtl_text
    logical = {name: rtl_text.to_logical(value) if isinstance(value, str) and _visual_rtl(value) else value
               for name, value in fields.items()}
    return rtl_text.to_visual(rtl_text.to_logical(translated).format(**logical))


def join(parts, separator=" · "):
    """Translated pieces joined into one line in reading order (``" · ".join`` reverses them in Arabic).

    Arabic catalogue text is in visual order, so pieces concatenated left to right read last-first and
    their numbers merge across the separator. Pieces are joined in reading order and the line is put in
    visual order as a whole. Other languages get the plain join.
    """
    parts = [str(part) for part in parts]
    if not any(_visual_rtl(part) for part in parts):
        return separator.join(parts)
    from . import rtl_text
    return rtl_text.to_visual(separator.join(rtl_text.to_logical(part) if _visual_rtl(part) else part
                                             for part in parts))


def iface_(message, msgctxt=None, /, **fields):
    return _fill(_gettext("iface", message, msgctxt), message, fields)


def tip_(message, **fields):
    return _fill(_gettext("tip", message), message, fields)


def rpt_(message, **fields):
    return _fill(_gettext("rpt", message), message, fields)


def language():
    """The active interface language: ``"ja"``, ``"de"``, ``"pt_BR"``, ``"zh_HANT"``..., ``"en"`` when unknown."""
    try:
        from bpy.app import translations
        locale = translations.locale
    except ImportError:
        return "en"
    if not locale:
        return "en"
    for tagged in ("zh_HANS", "zh_HANT", "pt_BR"):
        if locale.startswith(tagged):
            return tagged
    return locale.split("_")[0]


def is_rtl():
    """True when the interface language is written right to left (Arabic here; Persian, Hebrew, Urdu for Blender)."""
    return language() in RTL_LANGUAGES


def plural_category(code, count):
    """CLDR plural category of an integer ``count`` for the language ``code``."""
    n = abs(int(count))
    if code in {"ja", "ko", "zh", "zh_HANS", "zh_HANT", "th", "vi", "id"}:
        return "other"
    if code == "ar":
        if n == 0:
            return "zero"
        if n == 1:
            return "one"
        if n == 2:
            return "two"
        if 3 <= n % 100 <= 10:
            return "few"
        return "many" if 11 <= n % 100 <= 99 else "other"
    if code == "ru":
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    if code == "pl":
        if n == 1:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    if code in {"fr", "pt_BR", "hi"}:
        return "one" if n in (0, 1) else "other"
    return "one" if n == 1 else "other"


def plural_(count, one, other, **fields):
    """Translate a counted phrase: ``plural_(n, "{count} file", "{count} files")``.

    ``one`` and ``other`` are the English singular and plural templates. Each
    grammatical form of the active language is a catalogue entry whose message
    context is ``plural:<category>``; a missing entry falls back to English
    (singular for one, plural otherwise) so a gap never shows a wrong form.
    """
    category = plural_category(language(), count)
    english = one if count == 1 else other
    message = one if category == "one" else other
    translated = _gettext("iface", message, "plural:" + category)
    if translated == message and category not in ("one", "other"):
        message = other
        translated = _gettext("iface", other, "plural:other")
    if translated == message:
        translated = english
    return _fill(translated, english, dict(fields, count=count))


def notice(template, **fields):
    """A chat message body: ``text`` is the English sentence, ``i18n`` the template for display.

    The stored ``text`` is what history, restore and any model-visible summary
    keep; ``display_notice`` re-renders the template in the active language.
    Field values the recipe cannot hold as JSON (paths, errors, NaN) become
    their ``str``; a recipe past the size bounds is left out (English only).
    """
    message = {"text": template.format(**fields) if fields else template}
    recipe = clean_recipe([template, {key: _scalar(value) for key, value in fields.items()}])
    if recipe is not None:
        message["i18n"] = recipe
    return message


_SCALARS = (str, int, float, bool, type(None))
RECIPE_MAX_FIELDS, RECIPE_MAX_CHARS = 16, 16000


def _scalar(value):
    if type(value) is float:
        return value if math.isfinite(value) else str(value)
    return value if type(value) in _SCALARS else str(value)


def clean_recipe(recipe, redact=None):
    """``recipe`` as a validated ``[template, fields]``, else ``None``: the single gate for a stored ``i18n``.

    The template is a string, the fields a small dict of identifier names to
    JSON scalars (finite numbers, strings, booleans, null), all within a size
    bound, so a recipe can never break a transcript save. ``redact`` filters
    every string field, as the message text is filtered.
    """
    if not (isinstance(recipe, (list, tuple)) and len(recipe) == 2 and isinstance(recipe[0], str)
            and isinstance(recipe[1], dict)) or len(recipe[1]) > RECIPE_MAX_FIELDS:
        return None
    size, fields = len(recipe[0]), {}
    for key, value in recipe[1].items():
        if (type(key) is not str or not key.isidentifier() or len(key) > 48 or type(value) not in _SCALARS
                or type(value) is float and not math.isfinite(value)):
            return None
        fields[key] = redact(value) if redact is not None and type(value) is str else value
        size += len(key) + (len(fields[key]) if type(value) is str else 24)
    return [recipe[0], fields] if size <= RECIPE_MAX_CHARS else None


def attach_recipe(message, data, redact=None):
    """Carry ``data["i18n"]`` onto a stored system or error ``message`` when it is a valid recipe; no other role keeps one."""
    recipe = clean_recipe(data.get("i18n"), redact) if message.get("role") in ("system", "error") else None
    if recipe is not None:
        message["i18n"] = recipe
    return message


def stored_recipe(message):
    """A message's valid ``i18n`` recipe if it still renders, in English, to exactly the stored ``text``, else ``None``.

    The recipe is only a view of the text: a row that was rewritten after it
    was built, or redacted differently, keeps its stored text as it is.
    """
    recipe = clean_recipe(message.get("i18n")) if isinstance(message, dict) else None
    if recipe is not None:
        try:
            if (recipe[0].format(**recipe[1]) if recipe[1] else recipe[0]) == message.get("text"):
                return recipe
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            pass
    return None


def display_notice(message):
    """The text to draw for a chat message: its template in the active language, else its stored text."""
    recipe = stored_recipe(message)
    return message["text"] if recipe is None else iface_(recipe[0], **recipe[1])
