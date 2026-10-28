# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a reviewed script's text alone decides, worked out once per distinct text.

A script call is validated where the controller admits it, at each precondition (dispatch, the approval, the
commit's fresh check) and twice as it runs, and the executor then compiles it: each validation compiles the text and
walks its syntax tree for names Blender 5.2 removed (``script_api_removed``), about 1.8 ms on a 3.5 KB script, six
times in one worker call. Both are pure functions of the text, so the last ``SCRIPTS`` texts keep their results: the
text itself is the key, so a hit can never be another script's. Code objects are immutable and every run gets its
own namespace, so one serves every run. Text that does not compile keeps nothing and raises again each time.

Standard library only: validation runs outside Blender too (the external service, plan checks, host tests).
"""

import functools

from . import script_api_removed

SCRIPT_FILE = "<angelo-reviewed-script>"   # The file name a script's tracebacks and line numbers carry.
SCRIPTS = 8   # Distinct texts kept: one call validates and runs one text; a few calls overlap at most.


@functools.lru_cache(maxsize=SCRIPTS)
def compiled(code):
    """``compile(code, SCRIPT_FILE, "exec")``, kept per text; SyntaxError (or ValueError) as compile raises it."""
    return compile(code, SCRIPT_FILE, "exec", dont_inherit=True)


@functools.lru_cache(maxsize=SCRIPTS)
def _removed(code):
    return tuple(script_api_removed.problems(code))


def removed(code):
    """``script_api_removed.problems(code)``, kept per text: a new list each call, so no caller can change it."""
    return list(_removed(code))
