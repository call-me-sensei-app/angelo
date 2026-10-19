# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with ``image: "EDITOR"`` or ``"EDITOR:<type>"``: what the person sees, as they see it.

"Look at what I'm seeing" needs the person's own editors: the Image or UV
Editor with its zoom and overlays, a node tree, the Properties of what they
selected. ``EDITOR`` is the bound window; ``EDITOR:<type>`` the largest open
editor whose ``ui_type`` or ``type`` is ``<type>`` (bound window first, then
Blender's other windows). doc/angelo/view_sources.rst, "What the person sees".

* **Blender's own pixels.** ``screen.screenshot`` redraws the window into an
  off-screen buffer (Metal, Vulkan) or reads it, and writes the private file;
  no Undo step, no data-block, nothing opened, switched or scrolled.
* **No code or credentials editors.** Editors that can show them are filled
  with a flat grey before anything leaves this module and listed in
  ``masked``: text editors, consoles (the chat included), the Info log,
  Preferences and the file browser (the asset browser is shown), and the
  status bar, whose reports can carry paths. Asking for one of them is refused.
* **Parity, disclosed**. Other editors are sent as drawn,
  as other screenshot tools send them, so a file path drawn in one (an output
  path in Properties, an image path in the Image Editor's sidebar) goes with
  the picture. Each capture the model is given is announced in the chat with a
  host-only notice naming the editors (``disclose``), never an approval; a
  conversation that cannot show the notice gets no capture.

Needs a window (not background mode) and no modal interaction. Main thread;
model-facing text is English; the notice is translatable.
"""

from .input_policy import interaction_active
from .ui_text import n_, notice
from .view_pixels import box_downscale
from .view_png import MAX_DIMENSION, encode_png

MASK = 72  # The flat grey of a masked editor.
SENSITIVE = {"TEXT_EDITOR": "text editors", "CONSOLE": "consoles", "INFO": "the Info log",
             "PREFERENCES": "Preferences", "FILE_BROWSER": "the file browser"}
LISTED = 16  # Editors a result or refusal names.
THEN = " It shows the interface now, as drawn for the person: not render pixels."
# The chat notices of a capture (host-only rows: the person sees them, no model does).
WINDOW_NOTICE = n_("The agent was sent a picture of your window as you see it ({editors}), with any file paths shown "
                   "there; text editors, consoles, the file browser and the status bar were greyed out.")
EDITOR_NOTICE = n_("The agent was sent a picture of your {editor} as you see it, with any file paths shown in it.")
TOLD = " The person is told in the chat that this picture was sent to you."
# Editor names as the Editor Type menu shows them; another type is named by its identifier.
NAMES = {"VIEW_3D": "3D Viewport", "IMAGE_EDITOR": "Image Editor", "UV": "UV Editor", "ShaderNodeTree": "Shader Editor",
         "CompositorNodeTree": "Compositor", "GeometryNodeTree": "Geometry Node Editor",
         "TextureNodeTree": "Texture Node Editor", "SEQUENCE_EDITOR": "Video Sequencer", "CLIP_EDITOR": "Movie Clip Editor",
         "DOPESHEET": "Dope Sheet", "TIMELINE": "Timeline", "FCURVES": "Graph Editor", "DRIVERS": "Drivers",
         "NLA_EDITOR": "Nonlinear Animation", "PROPERTIES": "Properties", "OUTLINER": "Outliner",
         "SPREADSHEET": "Spreadsheet", "ASSETS": "Asset Browser"}


def editor_name(kind):
    return NAMES.get(kind, kind)


def disclose(controller, facts):
    """Tell the person in the chat that this capture was sent to the model: a host-only notice, no approval."""
    if facts["editor"] == "WINDOW":
        shown = [editor_name(kind) for kind in facts["editors"] if kind not in facts["masked"]]
        message = notice(WINDOW_NOTICE, editors=", ".join(shown))
    else:
        message = notice(EDITOR_NOTICE, editor=editor_name(facts["editor"]))
    controller.emit("message", {"role": "system", "notice": True, **message})


def sensitive(area):
    """Whether an editor can show code, credentials or file paths (the asset browser shows assets only)."""
    if area.type == "FILE_BROWSER":
        return getattr(area, "ui_type", "") != "ASSETS"
    return area.type in SENSITIVE


def _kind(area):
    return getattr(area, "ui_type", "") or area.type


def _editors(window):
    seen = []
    for area in window.screen.areas:
        if _kind(area) not in seen:
            seen.append(_kind(area))
    return seen[:LISTED]


def find_area(window, name):
    """``(window, area)``: the largest editor of type ``name``, in the bound window first (RuntimeError otherwise)."""
    import bpy
    windows = [window] + [other for other in bpy.context.window_manager.windows if other != window]
    for candidate in windows:
        matches = [area for area in candidate.screen.areas if name in (_kind(area), area.type)]
        if matches:
            return candidate, max(matches, key=lambda area: area.width * area.height)
    raise RuntimeError("No %s editor is open. Open editors: %s" % (name[:64], ", ".join(_editors(window)) or "none"))


def screenshot(window, path):
    """Blender's own screenshot of ``window`` into ``path``: RGBA rows, bottom row first (uint8)."""
    import bpy
    import imbuf
    import numpy
    with bpy.context.temp_override(window=window):
        finished = bpy.ops.screen.screenshot(filepath=str(path), check_existing=False)
    if "FINISHED" not in finished:
        raise RuntimeError("Blender could not capture the window")
    buffer = imbuf.load(str(path))
    try:
        with buffer.with_buffer() as view:
            rows = numpy.array(view, dtype=numpy.uint8)
    finally:
        buffer.free()
    if rows.shape[-1] == 3:
        rows = numpy.concatenate([rows, numpy.full(rows.shape[:2] + (1,), 255, numpy.uint8)], axis=2)
    return rows


def _scale(window, width):
    """Screenshot pixels per area unit: editors are laid out in the window's native pixels, 1 normally."""
    extent = max((area.x + area.width for area in window.screen.areas), default=width)
    return width / float(extent) if extent > 0 else 1.0


def _box(area, scale, height, width):
    """An editor's rows and columns in bottom-first ``rows``: ``(bottom, top, left, right)``, clipped."""
    bottom, left = max(0, int(area.y * scale)), max(0, int(area.x * scale))
    top = min(height, int(round((area.y + area.height) * scale)))
    right = min(width, int(round((area.x + area.width) * scale)))
    return bottom, top, left, right


def mask(window, rows):
    """Grey out every sensitive editor and the status bar of a whole-window capture; returns what was masked."""
    height, width = rows.shape[:2]
    scale = _scale(window, width)
    masked = []
    for area in window.screen.areas:
        if sensitive(area):
            bottom, top, left, right = _box(area, scale, height, width)
            rows[bottom:top, left:right, :3] = MASK
            if _kind(area) not in masked:
                masked.append(_kind(area))
    lowest = min((area.y for area in window.screen.areas), default=0)
    if lowest > 0:  # The status bar lies below every editor of the screen.
        rows[:int(lowest * scale), :, :3] = MASK
        masked.append("STATUSBAR")
    return masked


def source(window, checked, references, controller):
    """The ``view_finished.Source`` of ``EDITOR``: a private PNG of the window or one editor, masked."""
    import bpy
    from .view_finished import Source
    if bpy.app.background:
        raise RuntimeError("Blender runs without a window here, so there is no editor to capture")
    if not callable(getattr(controller, "emit", None)):
        raise RuntimeError("Editor captures are announced to the person in the chat, and this conversation has none "
                           "to show it in")
    name = checked.get("item")
    shown, area = window, None
    if name is not None:
        shown, area = find_area(window, name)
        if sensitive(area):
            raise RuntimeError("%s is not captured: %s can show code, credentials or file paths"
                               % (name[:64], SENSITIVE[area.type]))
    if interaction_active(shown):
        raise RuntimeError("Finish the current Blender interaction before capturing an editor")
    reference = references.new_observation(("Editor %s" % name[:64]) if name else "Window")
    try:
        rows = screenshot(shown, reference["path"])
        height, width = rows.shape[:2]
        facts = {"editor": _kind(area) if area is not None else "WINDOW", "window_size": [width, height]}
        if area is None:
            facts.update(editors=_editors(shown), masked=mask(shown, rows))
        else:
            bottom, top, left, right = _box(area, _scale(shown, width), height, width)
            if top - bottom < 8 or right - left < 8:
                raise RuntimeError("The %s editor is too small to show (%dx%d pixels)" % (name[:64], right - left,
                                                                                           top - bottom))
            rows = rows[bottom:top, left:right]
            facts["area_size"] = [right - left, top - bottom]
        factor = -(-max(rows.shape[:2]) // MAX_DIMENSION)  # Whole steps down to what the encoder takes.
        if factor > 1:  # A display wider than the encoder takes (a 5K or 6K screen): halved, or more.
            rows = (box_downscale(rows.astype("float32"), factor) + 0.5).astype("uint8")
        data = encode_png(rows.tobytes(), rows.shape[1], rows.shape[0], bottom_up=True)
        with open(reference["path"], "wb") as handle:
            handle.write(data)
    except Exception:
        references.discard_observation(reference)
        raise
    what = ("The %s editor as the person sees it" % facts["editor"]) if area is not None else (
        "The Blender window as the person sees it")
    note = ""
    if facts.get("masked"):
        note = " Greyed out: %s." % ", ".join(facts["masked"])
    return Source(reference["path"], reference["name"], what, facts, reference, then=THEN, note=note + TOLD,
                  framing="EDITOR", on_shown=lambda result: disclose(controller, facts))
