# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The page's report of what it frames (doc/angelo/web_export.rst, "Framing"), checked before Angelo keeps it.

The live preview's page posts it whenever what it looks at changes: the frame mode (``camera``, ``selection`` or
``all``; None before anything was framed), Follow, the objects it framed by name, the scene camera it took, the focus
version it used and its view's position and target (Three.js coordinates, Y up). Angelo keeps the latest one for the
agent (``angelo_view`` WEB facts, ``view_web.page_facts``) and the GUI scenario asserts it. Only this shape is kept:
names, finite numbers, a bounded list. Standard library only, no ``bpy``."""

import json
import math

MAX_BYTES = 4 * 1024
MODES = ("camera", "selection", "all", None)
MAX_NAMES = 64
KEYS = frozenset({"mode", "follow", "framed", "camera", "focus_version", "position", "target"})


def _refuse(value):
    raise ValueError("not a finite number: %s" % value)


def _point(value):
    return (isinstance(value, list) and len(value) == 3 and all(type(v) in (int, float) and math.isfinite(v)
                                                                 for v in value))


def parse(body):
    """The report in ``body`` (JSON bytes) as Angelo keeps it, or None when it isn't exactly this shape."""
    try:
        report = json.loads(body.decode("utf-8"), parse_constant=_refuse)
    except (UnicodeError, ValueError):
        return None
    if not isinstance(report, dict) or set(report) != KEYS:
        return None
    framed, camera = report["framed"], report["camera"]
    if (report["mode"] not in MODES or type(report["follow"]) is not bool or type(report["focus_version"]) is not int
            or not isinstance(framed, list) or len(framed) > MAX_NAMES or not all(isinstance(n, str) for n in framed)
            or not (camera is None or isinstance(camera, str)) or not _point(report["position"])
            or not _point(report["target"])):
        return None
    return {key: report[key] for key in ("mode", "follow", "framed", "camera", "focus_version", "position", "target")}
