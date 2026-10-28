# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat composer's hint as the real window draws it (``chat_composer.hint_rows``), for GUI suites.

``whole`` reads what the composer last drew; ``as_the_chat_allows`` judges it against what the drawn chat can hold;
``longest`` is the widest hint English or any shipped catalogue gives, the away hint with Blender's longest scene name
(the AWAY hint was clipped mid-sentence in the session_rail GUI capture, 2026-10-25; translations run longer than
English).

Whether a hint fits whole depends on the chat's width in layout units, which is the area's pixels over the interface
scale times the display's pixel size: the same window at 2x is half as wide on a 1x Windows display as on a Retina
Mac. A check that demands a whole hint must derive that from the drawn width, never assume it (release-106's Windows
T4 run: the widest Japanese hint at 2x in a 163-unit chat needs more than the composer's eight rows, and the composer
rightly showed its first sentence, then an ellipsis).
"""

import gettext
from pathlib import Path

SCENE_NAME = "Ground floor living room with the long bay window and fireplace"   # 63 bytes: Blender's longest name.
FIELD_HINTS = ("External client: approvals and Stop only", "Do anything")
ELLIPSIS = "…"


def drawn(area):
    from angelo_agent import chat_view
    return chat_view._views.get(area.as_pointer(), {})


def _read(value):
    """Reading order: Arabic rows and catalogue text are visual."""
    from angelo_agent import rtl_text
    return rtl_text.to_logical(value) if rtl_text.is_visual(value) else value


def _bare(value):
    return value.replace(" ", "")


def whole(area, expected):
    """(whether ``expected`` is the hint ``area``'s composer drew, every character of it on rows inside the composer's
    text field, in reading order from its first row (Arabic rows are each visual: break-it R5-L-1), and no ellipsis;
    the evidence)."""
    from angelo_agent import chat_composer
    state = drawn(area)
    rows = list(state.get("hint_rows") or ())
    text, logical = "".join(_read(row) for row in rows), _read(expected)
    field = state.get("composer") or (0, 0, 0, 0)
    line_height = state.get("font_size", 0) + 8
    ok = (_read(state.get("hint") or "") == logical and _bare(text) == _bare(logical)
          and ELLIPSIS not in text and 0 < len(rows) <= chat_composer.HINT_ROWS and field[3] >= len(rows) * line_height)
    return ok, {"hint": state.get("hint"), "rows": rows, "field": field, "line_height": line_height,
                "scale": state.get("text_scale")}


def verdict(rows, expected, wrap_rows, max_rows, sentence_ends):
    """(the regime the drawn ``rows`` must follow, whether they do) for the hint ``expected`` (as given: visual for
    Arabic), where ``wrap_rows(text)`` is how many rows ``text`` takes at the drawn width and scale:

    * ``whole``: the hint fits ``max_rows``, so every character is drawn and no ellipsis;
    * ``sentences``: it does not, so the most whole sentences that fit, then an ellipsis (never mid-sentence);
    * ``prefix``: not even its first sentence fits, so every row is used and the last ends with the ellipsis.

    Derived from the width the chat was drawn at, on every platform and at every scale alike."""
    logical = _read(expected)
    text = "".join(_read(row) for row in rows)
    if not 0 < len(rows) <= max_rows:
        return "rows", False
    if wrap_rows(expected) <= max_rows:
        return "whole", ELLIPSIS not in text and _bare(text) == _bare(logical)
    shown = text.rstrip()
    if not shown.endswith(ELLIPSIS) or not _bare(logical).startswith(_bare(shown[:-1])):
        return "cut", False
    fitting = [end for end in sentence_ends(logical) if wrap_rows(logical[:end].rstrip() + ELLIPSIS) <= max_rows]
    if fitting:
        return "sentences", _bare(shown[:-1]) == _bare(logical[:fitting[-1]])
    return "prefix", len(rows) == max_rows and bool(_bare(shown[:-1]))


def as_the_chat_allows(area, expected):
    """(whether ``area``'s composer drew the hint ``expected`` as whole as its drawn width allows (``verdict``), on
    rows inside its text field; the evidence, with the regime and the rows the whole hint needs there)."""
    from angelo_agent import canvas_wrap, chat_composer, chat_view
    state = drawn(area)
    rows = list(state.get("hint_rows") or ())
    field = state.get("composer") or (0, 0, 0, 0)
    line_height = state.get("font_size", 0) + 8
    width, size, scale = state.get("hint_width", 0), state.get("font_size", 12), state.get("text_scale") or 1.0

    def wrap_rows(text):   # Measured as the composer measured it: its font, size, width and scale.
        return len(list(canvas_wrap.iter_wrap(text, width, size, chat_view._font, True, scale)))
    regime, ok = verdict(rows, expected, wrap_rows, chat_composer.HINT_ROWS, chat_composer.sentence_ends) \
        if width else ("no width", False)
    ok = ok and _read(state.get("hint") or "") == _read(expected) and field[3] >= len(rows) * line_height
    return ok, {"hint": state.get("hint"), "rows": rows, "field": field, "line_height": line_height,
                "scale": scale, "hint_width": width, "regime": regime,
                "whole_needs_rows": wrap_rows(expected) if width else None}


def longest(size):
    """The widest composer hint at ``size``, in English or in any shipped catalogue (``angelo.mo``), the away hint
    naming ``SCENE_NAME``."""
    import bpy
    from angelo_agent import chat_view, document_scene
    msgids = (document_scene.AWAY, document_scene.GONE) + FIELD_HINTS
    texts = list(msgids)
    root = Path(bpy.utils.system_resource('DATAFILES', path="locale") or "")
    for mo in sorted(root.glob("*/LC_MESSAGES/angelo.mo")):
        with mo.open("rb") as handle:
            catalog = gettext.GNUTranslations(handle)
        for msgid in msgids:
            texts.extend(found for found in (catalog.gettext(msgid), catalog.pgettext("*", msgid)) if found != msgid)
    return max((text.replace("{scene}", SCENE_NAME) for text in texts), key=lambda text: chat_view.measure(text, size))
