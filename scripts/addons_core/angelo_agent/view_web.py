# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` ``image: "WEB"``: a page as the person's browser shows it: the developer's own Three.js page
when it shows the linked asset through the web link (``engine_link_capture``, doc/angelo/web_link.rst), else the
live web preview (doc/angelo/web_export.rst).

This is how the agent verifies a web-shaded object: through the browser's own Three.js, not Blender's viewport,
which shows the material's nodes as the stand-in. Angelo asks the open page for a picture over the preview's
event stream and the page sends a PNG of its canvas back to the loopback server; the result's facts carry the
page's report on each web shader (compiled or the compiler's error). No page open, no picture: the result says so.
"""

CAPTURE_SECONDS = 8.0
THEN = (" It shows the live web preview as the person's browser draws it now (Three.js), not Blender's viewport "
        "or a render.")
THEN_LINK = (" It shows the developer's own Three.js page, with the linked asset in their scene, as the browser draws "
             "it now: their lights, materials and camera, not Blender's viewport.")


def source(window, checked, references, controller):
    """The ``view_finished.Source`` of ``WEB``."""
    from . import engine_link_capture, web_preview
    from .view_finished import Source
    if engine_link_capture.editor() is not None:   # The developer's page shows the linked asset: it comes first.
        return _linked(references, Source, engine_link_capture)
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
    if isinstance(feedback.get("look"), dict):   # The tone mapping, exposure and World the page applied.
        facts["look"] = {key: feedback["look"][key] for key in ("toneMapping", "exposure", "outputColorSpace",
                                                               "environment", "light", "background", "pending")
                         if key in feedback["look"]}
    note = ""
    if facts["page_version"] is not None and facts["page_version"] != server.version:
        note = " The page was still loading the latest export when it drew this."
    return Source(reference["path"], reference["name"], "The live web preview in the browser", facts, reference,
                  then=THEN, note=note, framing="WEB_PREVIEW")


def _linked(references, Source, engine_link_capture):
    png, facts = engine_link_capture.capture()
    reference = references.new_observation("Web page")
    try:
        with open(reference["path"], "wb") as handle:
            handle.write(png)
    except Exception:
        references.discard_observation(reference)
        raise
    note = ""
    if facts["page_version"] is not None and facts["page_version"] != facts["live_version"]:
        note = " The page was still showing an earlier live update when it drew this."
    return Source(reference["path"], reference["name"], "The linked Three.js page in the browser", facts, reference,
                  then=THEN_LINK, note=note, framing="WEB_PREVIEW")
