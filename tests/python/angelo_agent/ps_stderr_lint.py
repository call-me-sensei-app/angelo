# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native calls whose stderr can end a Windows PowerShell 5.1 script running under ``$ErrorActionPreference = "Stop"``.

Windows PowerShell 5.1 turns each stderr line of a native program into an error record when that stderr is
redirected (``2>&1``, ``2>$null``, ``*>&1``, ``2> file``); under the Stop preference the first record ends the
script. A Windows ``bench prep`` once died that way on a cloud CLI's "Copying ..." progress line right after
SIGN-IN-READY (``windows_final_runs.ps1``). A native call whose stderr is not redirected passes it straight through
and is safe in the console host the VM's scripts run in, so only redirected calls are findings.

``stderr_hazards`` reads a script without running it (the host suite has no PowerShell): comments and string
contents are blanked, except a ``$( )`` subexpression inside a string, which is code; then the preference is followed
in source order. ``$ErrorActionPreference = "Continue"`` holds until the block it was set in closes or another
assignment; ``"Stop"`` (or restoring a saved ``$old`` that held Stop) holds for the rest of the function. A function
or ``-ScriptBlock`` body starts at Stop, its callers' preference, and never sees an enclosing Continue. That is
conservative: a Continue reaches only the code written inside its own block.
"""

import bisect
import re

ASSIGN = re.compile(r"\$ErrorActionPreference\s*=\s*(?:\"(\w+)\"|'(\w+)'|\$(\w+))", re.I)
SAVE = re.compile(r"\$(\w+)\s*=\s*\$ErrorActionPreference\b", re.I)
# Stream 2 (errors) or * (all), to a stream, a file or $null; "3>&1" and the like leave stderr alone.
REDIRECT = re.compile(r"(?<![\w$])[2*]>")
FRAME = re.compile(r"\bfunction\s+[\w-]+|-ScriptBlock\b", re.I)


def code_only(source):
    """``source`` with comments and string contents replaced by spaces (same length, newlines kept); the code of a
    ``$( )`` subexpression inside a double-quoted string stays."""
    out = list(source)

    def blank(*indexes):
        for index in indexes:
            if index < len(out) and out[index] != "\n":
                out[index] = " "

    modes, parens, i = ["code"], [0], 0
    while i < len(source):
        c, two, mode = source[i], source[i:i + 2], modes[-1]
        if mode == "code":
            if two == "<#":
                modes.append("block")
                blank(i, i + 1)
                i += 2
                continue
            if c == "#":
                while i < len(source) and source[i] != "\n":
                    blank(i)
                    i += 1
                continue
            if two in ('@"', "@'") and source[i + 2:i + 3] in ("\n", "\r"):
                closing = "\n" + two[1] + "@"
                end = source.find(closing, i)
                end = len(source) if end < 0 else end + len(closing)
                blank(*range(i, end))
                i = end
                continue
            if c == "`":   # An escaped character or a line continuation.
                i += 2
                continue
            if c == "'":
                modes.append("sq")
                blank(i)
            elif c == '"':
                modes.append("dq")
                blank(i)
            elif c == "(":
                parens[-1] += 1
            elif c == ")":
                if len(parens) > 1 and parens[-1] == 0:   # Ends a "$( )" inside a string.
                    parens.pop()
                    modes.pop()
                    blank(i)
                else:
                    parens[-1] -= 1
        elif mode == "block":
            if two == "#>":
                modes.pop()
                blank(i, i + 1)
                i += 2
                continue
            blank(i)
        elif mode == "sq":
            blank(i)
            if c == "'":
                if source[i + 1:i + 2] == "'":
                    blank(i + 1)
                    i += 2
                    continue
                modes.pop()
        else:  # dq
            if c == "`":
                blank(i, i + 1)
                i += 2
                continue
            if two == "$(":
                blank(i, i + 1)
                modes.append("code")
                parens.append(0)
                i += 2
                continue
            blank(i)
            if c == '"':
                modes.pop()
        i += 1
    return "".join(out)


def stderr_hazards(source):
    """``[(line number, line)]``: each redirected stderr written where the preference may be Stop."""
    code, lines = code_only(source), source.split("\n")
    starts = [0] + [m.end() for m in re.finditer("\n", code)]
    # An assignment's quoted value is blanked in ``code``: it is matched in the source where its name is code.
    events = [(m.start(), "assign", m) for m in ASSIGN.finditer(source)
              if code[m.start():m.start() + 2].lower() == "$e"]
    events += [(m.start(), "save", m) for m in SAVE.finditer(code)]
    events += [(m.start(), "redirect", m) for m in REDIRECT.finditer(code)]
    events += [(m.start(), "frame", m) for m in FRAME.finditer(code)]
    events += [(m.start(), m.group(), m) for m in re.finditer(r"[{}]", code)]
    frames, saved, depth, pending, hazards = [{"depth": 0, "regions": []}], {}, 0, False, []

    def current():
        regions = frames[-1]["regions"]
        return regions[-1][1] if regions else "Stop"

    for position, kind, match in sorted(events, key=lambda event: event[0]):
        if kind == "frame":
            pending = True   # The next "{" opens a body that starts at its callers' Stop.
        elif kind == "{":
            depth += 1
            if pending:
                frames.append({"depth": depth, "regions": []})
                pending = False
        elif kind == "}":
            if len(frames) > 1 and depth == frames[-1]["depth"]:
                frames.pop()
            depth -= 1
            frames[-1]["regions"] = [region for region in frames[-1]["regions"] if region[0] <= depth]
        elif kind == "save":
            saved[match.group(1).lower()] = current()
        elif kind == "assign":
            literal = match.group(1) or match.group(2)
            value = literal.capitalize() if literal else saved.get(match.group(3).lower(), "Stop")
            regions = frames[-1]["regions"]
            if value == "Continue":
                regions[:] = [region for region in regions if region[0] < depth] + [(depth, value)]
            else:
                regions.clear()
        elif kind == "redirect" and current() != "Continue":
            number = bisect.bisect_right(starts, position)
            hazards.append((number, lines[number - 1].strip()))
    return hazards
