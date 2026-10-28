# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` ``image: "WEB"``: the live web preview as the person's browser shows it (doc/angelo/web_export.rst).

This is how the agent verifies a web-shaded object: through the browser's own Three.js, not Blender's viewport,
which shows the material's nodes as the stand-in. Angelo asks the open page for a picture over the preview's
event stream and the page sends a PNG of its canvas back to the loopback server; the result's facts carry the
page's report on each web shader (compiled or the compiler's error). No page open, no picture: the result says so.
"""

CAPTURE_SECONDS = 8.0
THEN = (" It shows the live web preview as the person's browser draws it now (Three.js), not Blender's viewport "
        "or a render.")


def source(window, checked, references, controller):
    """The ``view_finished.Source`` of ``WEB``."""
    from . import web_preview
    from .view_finished import Source
    server = web_preview._state["server"]
    if server is None:
        raise RuntimeError("The live web preview isn't running: start it with angelo_web_preview START")
    if server.streams < 1:
        raise RuntimeError("No browser shows the live web preview now; ask the person to open %s" % server.url)
    png = server.capture(CAPTURE_SECONDS)
    if png is None:
        raise RuntimeError("The browser did not send a picture of the web preview within %d s (a hidden or "
                           "background tab draws nothing); ask the person to bring it to the front"
                           % CAPTURE_SECONDS)
    reference = references.new_observation("Web preview")
    try:
        with open(reference["path"], "wb") as handle:
            handle.write(png)
    except Exception:
        references.discard_observation(reference)
        raise
    feedback = dict(server.feedback)
    facts = {"image": "WEB", "preview_version": server.version,
             "page_version": feedback.get("version"), "web_shaders": feedback.get("shaders", [])[:32]}
    note = ""
    if facts["page_version"] is not None and facts["page_version"] != server.version:
        note = " The page was still loading the latest export when it drew this."
    return Source(reference["path"], reference["name"], "The live web preview in the browser", facts, reference,
                  then=THEN, note=note, framing="WEB_PREVIEW")
