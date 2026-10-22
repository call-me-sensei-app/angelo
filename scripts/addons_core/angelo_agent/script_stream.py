# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The code of a script being written, read from the call's text as it streams.

A script plan shows its step (the script's own description, ``plan_steps``)
as soon as the plan starts, and then nothing more until the whole call is
written: 35-44 s for the 8-11k characters of the recorded Opus 5.5 footbridge
builds of the 1.0.0 scene campaign, the longest still stretch of a one-shot
after the model's thinking. The code is already streaming through the host,
so the live row can show it being written, the way an editor shows code being
typed: how many lines so far and the last few of them (``script_overlay``).

``ScriptText.feed`` takes the call's JSON text as it arrives: a Claude plan's
``input_json_delta`` (the ``code`` of an ``angelo_execute_python`` step,
``operations[i].arguments.code``) or, on the direct route (``claude_direct``),
the ``input_json_delta`` of an ``angelo_execute_python`` call itself (its
top-level ``code``). Only that one value is read; every other string, key,
number and container is skipped by structure, so a ``"code"`` inside another
value (a setting, a reply, the script's own text) never counts. A step whose
``tool`` is written first and is not the script tool shows nothing; a script
written before its ``tool`` key still shows, since only the script tool has a
``code`` argument (``tool_specs``). A patched rerun (``base`` and ``edits``)
has no code to show and shows none.

Escapes are decoded once complete (a surrogate pair waits for its second
half); text that is not valid JSON ends the scan. The text kept is bounded
(``MAX_TAIL``): the line count and the last lines, never the whole script.

Display only: nothing here is stored, saved, sent to a model or decides what
runs. The script the host reviews and runs is the complete call its validator
accepts. No ``bpy``: host-testable, and safe on the provider's reader thread.
"""

import json

SCRIPT_TOOL = "angelo_execute_python"
CODE = "code"
SHOWN_LINES = 5       # The newest lines the live row draws (script_overlay).
MAX_LINE = 240        # Characters of one drawn line, cut once redacted; the live row elides it to its width.
MAX_TAIL = 4096       # Characters of the newest code kept; more than the live row ever draws.
MAX_KEY = 64          # Longer than any key of a plan; a longer one is never "code", "tool" or "arguments".
MAX_DEPTH = 64        # Deeper text is skipped whole (no plan nests this deep).
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_HEX = frozenset("0123456789abcdefABCDEF")


class ScriptText:
    """The code of the script a streaming call is writing: ``lines`` written so far and the newest ``tail``.

    ``direct``: the text is one ``angelo_execute_python`` call's own input (its top-level ``code``), not a plan.
    ``scripts`` counts the scripts whose code started; a later one starts the tail and count over.
    """

    def __init__(self, direct=False):
        self.direct, self.done = direct, False
        self.scripts, self.lines, self.tail = 0, 0, ""
        self.closed = False  # The current script's code string has ended: it is written, no longer being written.
        # One entry per open container: [kind "{" or "[", its current key, its "tool" value].
        self._stack = []
        self._string = None      # Inside a string: "key", "tool" (a value to keep), "code" or "skip".
        # The current string's raw characters (a list: one fragment can be long) and how many there are.
        self._raw, self._count, self._escape, self._expect_key = [], 0, False, False

    def feed(self, text):
        """Scan more of the call's text; returns whether the shown code changed."""
        if self.done or not isinstance(text, str) or not text:
            return False
        before = (self.scripts, self.lines, len(self.tail), self.tail[-1:], self.closed)
        for character in text:
            if self._string is not None:
                self._in_string(character)
            else:
                self._structure(character)
            if self.done:
                break
        if self._string == "code":
            self._flush(final=False)
        return (self.scripts, self.lines, len(self.tail), self.tail[-1:], self.closed) != before

    # Structure ---------------------------------------------------------------------------------------------------

    def _structure(self, character):
        if character == '"':
            self._raw, self._count = [], 0
            if self._expect_key and self._stack and self._stack[-1][0] == "{":
                self._string = "key"
            else:
                self._string = self._value_kind()
                if self._stack and self._stack[-1][0] == "{":
                    self._stack[-1][1] = None  # The key's value is this string: a second one is no value of it.
                if self._string == "code":
                    self._start_script()
            self._expect_key = False
            return
        if character in "{[":
            if len(self._stack) >= MAX_DEPTH:
                self.done = True
                return
            self._stack.append([character, None, None])
            self._expect_key = character == "{"
        elif character in "}]":
            if self._stack:
                self._stack.pop()
            self._expect_key = False
        elif character == ",":
            self._expect_key = bool(self._stack) and self._stack[-1][0] == "{"
            if self._expect_key:
                self._stack[-1][1] = None

    def _value_kind(self):
        """What a string value about to start is: the script's code, an operation's tool, or text to skip."""
        if not self._stack or self._stack[-1][0] != "{":
            return "skip"
        key = self._stack[-1][1]
        if key == "tool":
            return "tool"
        if key != CODE:
            return "skip"
        if self.direct:
            return "code" if len(self._stack) == 1 else "skip"
        # A plan: {"operations": [{"tool": ..., "arguments": {"code": ...}}]}.
        if ([entry[0] for entry in self._stack] != ["{", "[", "{", "{"] or self._stack[0][1] != "operations"
                or self._stack[2][1] != "arguments"):
            return "skip"
        tool = self._stack[2][2]
        return "code" if tool in (None, SCRIPT_TOOL) else "skip"

    def _start_script(self):
        self.scripts, self.lines, self.tail, self.closed = self.scripts + 1, 0, "", False

    # Strings -----------------------------------------------------------------------------------------------------

    def _in_string(self, character):
        if character == '"' and not self._escape:
            kind, self._string = self._string, None
            raw = "".join(self._raw)
            if kind == "key":
                if self._stack:
                    self._stack[-1][1] = _decoded(raw) if self._count <= MAX_KEY else None
            elif kind == "tool":
                if self._stack:
                    self._stack[-1][2] = _decoded(raw) if self._count <= MAX_KEY else ""
            elif kind == "code":
                self._flush(final=True)
                self.closed = not self.done
            self._raw, self._count = [], 0
            return
        self._escape = character == "\\" and not self._escape
        if self._string == "skip":
            return
        if self._string in ("key", "tool") and self._count > MAX_KEY:
            return  # Too long to be one of the keys or tools read here; its end still counts.
        self._raw.append(character)
        self._count += 1

    def _flush(self, final):
        """Decode the code's complete escapes into the tail; an incomplete one waits for the next fragment."""
        out, raw, index = [], "".join(self._raw), 0
        while index < len(raw):
            character = raw[index]
            if character != "\\":
                out.append(character)
                index += 1
                continue
            if index + 1 >= len(raw):
                break
            if raw[index + 1] != "u":
                out.append(_ESCAPES.get(raw[index + 1], raw[index + 1]))
                index += 2
                continue
            if index + 6 > len(raw):
                break
            if not set(raw[index + 2:index + 6]) <= _HEX:
                self.done = True
                break
            code = int(raw[index + 2:index + 6], 16)
            if 0xD800 <= code < 0xDC00:
                if index + 12 > len(raw):
                    if not final:
                        break  # Its low surrogate may still arrive.
                    out.append("\ufffd")  # A lone high surrogate at the string's end; what follows it stays.
                    index += 6
                    continue
                low = int(raw[index + 8:index + 12], 16) if (raw[index + 6:index + 8] == "\\u"
                                                             and set(raw[index + 8:index + 12]) <= _HEX) else -1
                if 0xDC00 <= low < 0xE000:
                    out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                    index += 12
                    continue
                code = 0xFFFD
            elif 0xDC00 <= code < 0xE000:
                code = 0xFFFD  # A lone low surrogate: no text can be drawn (or encoded) with it.
            out.append(chr(code))
            index += 6
        self._raw = [raw[index:]] if index < len(raw) else []
        self._count = len(raw) - index
        added = "".join(out)
        if added:
            self.lines += added.count("\n")
            self.tail = (self.tail + added)[-MAX_TAIL:]

    # Reading back ------------------------------------------------------------------------------------------------

    def shown_lines(self, count):
        """The newest ``count`` lines of the code, the one being written last; ``[]`` before any code."""
        if not self.scripts or count <= 0:
            return []
        lines = self.tail.split("\n")
        if len(lines) > 1 and not lines[-1]:
            lines = lines[:-1]  # A line just ended: the next has not started.
        if len(self.tail) >= MAX_TAIL:  # The first kept line may be cut at its start, perhaps inside a key.
            if len(lines) > 1:
                lines = lines[1:]
            else:  # One line longer than the tail: from after its first space, so no key is ever cut in two.
                first = next((index for index, character in enumerate(lines[0]) if character.isspace()), None)
                lines = [lines[0][first + 1:]] if first is not None else []
        return lines[-count:]

    def preview(self):
        """The ``script_preview`` event's data: the line count and the newest lines, whole.

        Lines are cut to ``MAX_LINE`` only once redacted (``script_overlay.shown``): a cut first could leave
        part of a key that redaction no longer recognises.
        """
        return {"lines": self.line_count(), "text": "\n".join(self.shown_lines(SHOWN_LINES))}

    def line_count(self):
        """Lines of the current script so far: one once any code arrived, then one per line break after it."""
        if not self.scripts or not self.tail and not self.lines:
            return 0
        return self.lines + (0 if self.tail.endswith("\n") else 1)


def _decoded(raw):
    try:
        value = json.loads('"' + raw + '"')
    except ValueError:
        return None
    return value if isinstance(value, str) else None
