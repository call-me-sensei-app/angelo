# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a structured output stopped being JSON: plain facts for the model that wrote it, never a repair.

Recorded (Opus 5.5): a StructuredOutput plan with one closing brace missing, on
both instruction texts (14 of 25 and 1 of 25 trials). Claude Code answers each attempt with the first 200 bytes and
"Common causes: ..." but no position; after five attempts the turn fails, and the model's thinking chased
apostrophes and Claude Code's {"raw": ...} echo while it resent the same structure. ``facts(raw, length)`` states
what Python's JSON parser reports (as strict as JavaScript's: no NaN or Infinity), the first closer that does not
match its opener, the brackets still open there with the JSON path to the innermost one (``operations[0]``), and a
short excerpt with a caret. Claude Code keeps only the first 2,048 characters of an unparsed input (``raw``) beside
its full JavaScript length (``len``, UTF-16 units); an error the cut may have caused (a string, number, literal or
escape that runs to the end, or nothing after it) is never stated, only that the kept part holds none. Every part
is bounded. It guesses no intent and changes nothing. Pure Python, no Blender.
"""

import json
import re

EXCERPT = 60  # Characters on each side of the error in the excerpt.
# What the cut may have left unfinished: the error's position to the end is blank, or one token (a number,
# literal or escape fragment) that runs to the end; a string the cut left open is the parser's "Unterminated string".
UNFINISHED = re.compile(r"\s*[^\s,:\[\]{}\"]*\Z")
MAX_OPEN = 16  # Open brackets listed, innermost last.
MAX_KEY = 48  # Characters of one key in a path.
MAX_PATH = 160  # Characters of a path (its end kept).
MAX_MESSAGE = 1600  # Characters of the whole message.
CLOSERS = {"{": "}", "[": "]"}


def facts(raw, length=None):
    """``{"error", "line", "column", "byte", "open", "path", "closes", "excerpt"}`` for ``raw`` (``length``: the
    whole input's length, when ``raw`` may be its first part), ``{"kept", "length", "clean"}`` when the kept part
    of a cut input holds no error, or None when it parses (or is not text)."""
    if not isinstance(raw, str):
        return None
    cut = isinstance(length, int) and length > _units(raw)  # Claude Code's len counts UTF-16 units.
    message, position = _parse_error(raw)
    stack, keys, index, mismatch = _scan(raw, len(raw) if position is None else position)
    if mismatch is not None:  # A closer for another bracket: the first place the structure is wrong.
        position, wanted, found = mismatch
        message = "%r expected before %r" % (wanted, found)
    if position is None or (cut and mismatch is None and _the_cuts(raw, message, position)):
        # The kept part holds no error the cut did not cause; every number in UTF-16 units, as Claude Code's len.
        return {"kept": _units(raw), "length": length, "clean": _units(raw)} if cut else None
    line = raw.count("\n", 0, position) + 1
    column = position - (raw.rfind("\n", 0, position) + 1) + 1
    found = {"error": message, "line": line, "column": column,
             "byte": len(raw[:position].encode("utf-8", "surrogatepass")),  # A cut may split a character.
             "excerpt": _excerpt(raw, position)}
    if stack:
        found["open"] = ("…%d more " % (len(stack) - MAX_OPEN) if len(stack) > MAX_OPEN else "") + "".join(
            stack[-MAX_OPEN:])
        found["path"] = _path(stack, keys, index) or "(top)"
        closes = "".join(CLOSERS[bracket] for bracket in reversed(stack))
        found["closes"] = closes if len(closes) <= MAX_OPEN else closes[:MAX_OPEN] + " and %d more" % (
            len(closes) - MAX_OPEN)
    if cut:
        found["kept"], found["length"] = _units(raw), length
    return found


def text(raw, length=None):
    """One plain message for the model, at most ``MAX_MESSAGE`` characters, or None (``facts``)."""
    found = facts(raw, length)
    if found is None:
        return None
    parts = ["Nothing of this plan ran. Claude Code showed your input back as {\"raw\": ...} only because it "
             "could not parse it; you sent no \"raw\" field, and the plan goes in StructuredOutput as before."]
    if "error" not in found:
        parts.append("Claude Code kept only the first %d of your %d characters (UTF-16 units), and they hold no "
                     "JSON error the cut did not cause: the error is in the rest, which Angelo does not see." % (
                         found["kept"], found["length"]))
    else:
        parts.append("Your StructuredOutput input is not valid JSON%s: %s (line %d, column %d, byte %d)." % (
            " (%s characters)" % length if isinstance(length, int) else "", found["error"], found["line"],
            found["column"], found["byte"]))
        if found.get("open"):
            parts.append("Still open there: %s, the innermost at %s; closing them all takes %s." % (
                found["open"], found["path"], found["closes"]))
        parts.append("Text around it (^ marks the position):\n" + found["excerpt"])
    parts.append("Send the whole plan again as valid JSON.")
    message = "\n".join(parts)
    return message if len(message) <= MAX_MESSAGE else message[:MAX_MESSAGE - 1] + "…"


def after_give_up(provider, turn, owner):
    """The message for the round's last plan call when Claude Code could not parse it and gave up on the structured
    output, or None; once a request (``owner``'s epoch and generation, kept on ``provider``): a second such failure
    is the runtime's own error."""
    unparsed, request = turn.stream.unparsed, (owner.epoch, owner.generation)
    gave_up = (str(turn.result.get("subtype", "")) + " " + str(turn.result.get("errors", ""))).replace("_", " ")
    if (not isinstance(unparsed, dict) or getattr(provider, "_json_position_request", None) == request
            or "structured output" not in gave_up.lower()):
        return None
    found = text(unparsed.get("raw"), unparsed.get("len"))
    if found:
        provider._json_position_request = request
    return found


def _the_cuts(raw, message, position):
    """Whether the cut at the end of ``raw`` may have caused the parser's error at ``position``: a string it left
    open, an escape it cut short (a ``\\`` or ``\\u`` with fewer than four digits at the end; any other escape is wrong
    whatever follows), or a token running to the end (``UNFINISHED``)."""
    if message.startswith("Unterminated string"):
        return True
    if message.startswith("Invalid \\"):
        rest = raw[position:]
        rest = rest[1:] if rest.startswith("\\") else rest  # Python places \\uXXXX errors at the u, others at the \\.
        return not rest or (rest[0] == "u" and len(rest) < 5 and all(c in "0123456789abcdefABCDEF" for c in rest[1:]))
    return bool(UNFINISHED.match(raw, position))


def _units(raw):
    """UTF-16 code units of ``raw``, as JavaScript's ``length`` counts them; a lone surrogate (a character the cut
    split) is one."""
    return len(raw) + sum(1 for char in raw if ord(char) > 0xFFFF)


def _reject(name):
    raise ValueError(name)


def _parse_error(raw):
    """``(message, position)`` of the first JSON error in ``raw`` as a strict (JavaScript) parser sees it, or
    ``(None, None)``."""
    try:
        json.loads(raw, parse_constant=_reject)
        return None, None
    except json.JSONDecodeError as error:
        return error.msg, error.pos
    except RecursionError:
        return "nesting deeper than the parser reads", len(raw)
    except ValueError as error:  # NaN or Infinity: JSON has no such literal.
        return "%s is not a JSON value" % error, _bare(raw, str(error))


def _bare(raw, name):
    """The position of the first ``name`` outside strings."""
    string = escaped = False
    for position, char in enumerate(raw):
        if string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                string = False
        elif char == '"':
            string = True
        elif raw.startswith(name, position):
            return position
    return len(raw)


def _scan(raw, limit):
    """``(open brackets, key per level, index per level, mismatch)`` at ``limit`` (strings skipped): the first
    closer up to ``limit`` that does not match its opener is ``(position, wanted, found)``. Linear: the path is
    built once, for the innermost level only (``_path``)."""
    stack, keys, index, string, escaped, key, start = [], [], [], False, False, None, 0
    for position in range(min(limit + 1, len(raw))):
        char = raw[position]
        if position == limit and (string or char not in "}]"):
            break  # The parser's own position: only a wrong closer there is news.
        if string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                string = False
                if stack and stack[-1] == "{" and keys[-1] is None:
                    key = raw[start + 1:min(position, start + 1 + MAX_KEY)] + (
                        "…" if position - start - 1 > MAX_KEY else "")
            continue
        if char == '"':
            string, start = True, position
        elif char == ":" and stack and stack[-1] == "{":
            keys[-1], key = key, None
        elif char == "," and stack:
            if stack[-1] == "[":
                index[-1] += 1
            else:
                keys[-1] = None
        elif char in "{[":
            stack.append(char)
            keys.append(None)
            index.append(0)
        elif char in "}]":
            if not stack:
                return stack, keys, index, None  # A closer after the whole value: the parser's message says so.
            if CLOSERS[stack[-1]] != char:
                return stack, keys, index, (position, CLOSERS[stack[-1]], char)
            stack.pop()
            keys.pop()
            index.pop()
    return stack, keys, index, None


def _path(stack, keys, index):
    """The JSON path of the innermost open bracket (``operations[0].arguments``), its last ``MAX_PATH``
    characters."""
    path = ""
    for depth in range(len(stack) - 1):
        if stack[depth] == "[":
            path += "[%d]" % index[depth]
        elif keys[depth] is not None:
            path += ("." if path else "") + keys[depth]
        if len(path) > 4 * MAX_PATH:
            path = path[-MAX_PATH:]
    return path if len(path) <= MAX_PATH else "…" + path[-MAX_PATH:]


def _excerpt(raw, position):
    """The line around ``position`` (at most ``EXCERPT`` characters each side) and a caret under it."""
    low, high = max(0, position - EXCERPT), min(len(raw), position + EXCERPT)
    before, after = raw[low:position], raw[position:high]
    before = before[before.rfind("\n") + 1:]
    after = after.split("\n", 1)[0]
    shown = ("…" if low and len(before) == position - low else "") + before
    return shown + after + ("…" if high < len(raw) and "\n" not in raw[position:high] else "") + "\n" + \
        " " * len(shown) + "^"
