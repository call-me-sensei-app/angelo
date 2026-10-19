# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A plan's program operations as each one is complete, read from the plan's JSON as it streams.

``plan_progress`` names a plan's steps from single fields as they arrive (a
kind, a name), which is enough until a step's label needs the whole operation:
a ``batch`` is labelled by its unit's name and how many instances it makes
("Post ×4", ``batch_spec.instance_count``), which only its placement says.
``ClosedOps`` follows the JSON's structure (objects, arrays, strings and their
escapes) and returns each object of an ``operations`` list inside a tool's
``arguments`` (a program's operations) once its closing brace has arrived,
decoded. A Codex plan's parameters written as JSON text stay text here.

Display only and bounded: an operation longer than ``MAX_CHARS`` is skipped,
text that is not JSON yields nothing, and nothing here decides what runs.
"""

import json

MAX_CHARS = 65536
MAX_KEY = 64


class ClosedOps:
    def __init__(self):
        self._stack = []  # Per open container: [bracket, key that opened it].
        self._in_string = self._escape = False
        self._string, self._last, self._key = None, None, None
        self._capture = None  # The text of the program operation being written, from its "{".
        self._depth = 0  # The stack depth of that operation's object.

    def _program_operation(self):
        """Whether the object just opened is an item of a program's ``operations`` (a tool's ``arguments``)."""
        stack = self._stack
        return len(stack) >= 3 and stack[-2] == ["[", "operations"] and stack[-3][1] == "arguments"

    def feed(self, text):
        """The program operations this text completes, decoded, in order."""
        done = []
        if not isinstance(text, str):
            return done
        for character in text:
            if self._capture is not None:
                self._capture.append(character)
                if len(self._capture) > MAX_CHARS:
                    self._capture = None  # Too long to show anything of: skipped.
            if self._in_string:
                if self._escape:
                    self._escape = False
                elif character == "\\":
                    self._escape = True
                elif character == '"':
                    self._in_string = False
                    self._last = "".join(self._string) if self._string is not None else None
                elif self._string is not None:
                    if len(self._string) < MAX_KEY:
                        self._string.append(character)
                    else:
                        self._string = None  # Longer than any key.
                continue
            if character == '"':
                self._in_string, self._string = True, []
            elif character == ":":
                self._key = self._last
            elif character == ",":
                self._key = None
            elif character in "{[":
                parent_is_object = bool(self._stack) and self._stack[-1][0] == "{"
                self._stack.append([character, self._key if parent_is_object else None])
                self._key = None
                if character == "{" and self._capture is None and self._program_operation():
                    self._capture, self._depth = ["{"], len(self._stack)
            elif character in "}]":
                if character == "}" and self._capture is not None and len(self._stack) == self._depth:
                    try:
                        value = json.loads("".join(self._capture))
                    except ValueError:
                        value = None
                    if isinstance(value, dict):
                        done.append(value)
                    self._capture = None
                if self._stack:
                    self._stack.pop()
        return done
