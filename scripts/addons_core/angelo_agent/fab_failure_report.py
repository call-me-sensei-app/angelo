# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where an unexpected Fab failure came from, for the person's own console (fab_host._reason).

An unexpected exception becomes SERVICE_UNAVAILABLE ("Fab couldn't finish this action"); this names its type, its
closed code and the frames that raised it (file, line, function), never its message, which may carry a URL or
account text. No bpy.
"""

import traceback

UNEXPECTED = 'SERVICE_UNAVAILABLE'


def reported(error, code, reason):
    """``reason``, after saying where ``error`` came from: every failure's closed code, and for a browser failure its
    producer record (closed phase, type, reason and module frames, fab_browser_failure), else the raising frames."""
    produced = getattr(error, 'producer_diagnostic', None)
    if type(produced) is dict:
        trace = " <- ".join("%s:%s %s" % (row.get('module'), row.get('line'), row.get('function'))
                            for row in reversed(produced.get('trace') or []))
        print("Angelo: Fab %s (%s): %s %s/%s at %s" % (reason, code if isinstance(code, str) and code.isupper() else "?",
              produced.get('phase'), produced.get('type'), produced.get('reason'), trace or "unknown"))
        return reason
    if reason == UNEXPECTED:
        frames = traceback.extract_tb(getattr(error, '__traceback__', None))[-6:]
        where = " <- ".join("%s:%d %s" % (frame.filename.replace('\\', '/').rsplit('/', 1)[-1], frame.lineno, frame.name)
                            for frame in reversed(frames))
        print("Angelo: Fab couldn't finish an action: %s%s at %s" % (
            type(error).__name__, " (%s)" % code if isinstance(code, str) and code.isupper() else "", where or "unknown"))
    return reason
