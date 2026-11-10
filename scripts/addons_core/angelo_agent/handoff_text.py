# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete JSON text entries with pre-reserved truncation metadata and closing quotes."""

from dataclasses import dataclass

from .handoff_json import OUTPUT_LIMIT, UNSUPPORTED, _ESCAPES, admit_mapping, encode_json


@dataclass(frozen=True)
class TextResult:
    text: str | None = None
    reason: str | None = None
    truncated: bool = False
    work_exhausted: bool = False


def fit_text_entry(entry, text, *, budget, max_chars, compact=False, text_limit=6000, truncated_max_chars=None):
    """Same all-fit bytes; a cut is explicit and always closes a valid JSON object.

    Both wrappers are encoded before examining raw text. A work cut can thus
    return the examined prefix with its already-reserved truncation marker;
    no fresh budget, raw-tail slice, or custom string conversion is needed.
    """
    if (type(text) is not str or type(compact) is not bool or type(max_chars) is not int or max_chars < 1
            or type(text_limit) is not int or text_limit < 1):
        return TextResult(reason=UNSUPPORTED)
    if (reason := admit_mapping(entry, budget)) is not None:
        return TextResult(reason=reason)
    if truncated_max_chars is None:
        truncated_max_chars = max_chars
    if type(truncated_max_chars) is not int:
        return TextResult(reason=UNSUPPORTED)
    normal = encode_json(dict(entry, text=""), budget=budget, compact=compact,
                         max_chars=max_chars, track_members=True)
    if normal.reason:
        return TextResult(reason=normal.reason)
    trimmed = encode_json(dict(entry, text="", truncated=True), budget=budget, compact=compact,
                          max_chars=truncated_max_chars, track_members=True)
    if trimmed.reason and trimmed.reason != OUTPUT_LIMIT:
        return TextResult(reason=trimmed.reason)
    capacity, used, chunks, scanned = max_chars - len(normal.text), 0, [], 0
    truncated, exhausted = len(text) > text_limit, False
    for index in range(min(len(text), text_limit)):
        if not budget.scan():
            truncated, exhausted = True, True
            break
        character = text[index]
        code = ord(character)
        escaped = _ESCAPES.get(character)
        if escaped is None:
            escaped = "\\u%04x" % code if code < 32 or 0xD800 <= code <= 0xDFFF else character
        if used + len(escaped) > capacity:
            truncated = True
            break
        chunks.append(escaped)
        used += len(escaped)
        scanned = index + 1
    if scanned < min(len(text), text_limit):
        truncated = True
    wrapper = trimmed if truncated else normal
    final_limit = truncated_max_chars if truncated else max_chars
    if wrapper.reason:
        return TextResult(reason=wrapper.reason)
    while chunks and used + len(wrapper.text) > final_limit:
        used -= len(chunks.pop())  # Removing output never refunds inspection credit.
    if used + len(wrapper.text) > final_limit:
        return TextResult(reason=OUTPUT_LIMIT)
    position = wrapper.member_ends["text"] - 1
    encoded = wrapper.text[:position] + "".join(chunks) + wrapper.text[position:]
    return TextResult(text=encoded, truncated=truncated, work_exhausted=exhausted)
