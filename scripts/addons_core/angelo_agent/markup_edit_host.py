# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Runs the viewer's picture edits and brings each result into the chat as a new picture, the original kept (2.1.0,
doc/angelo/attachment_markup.rst, "Remove BG, Erase and Resize").

* **Remove BG and Erase** (``markup_edit``): the card (``card``) names the model, its estimated cost, that it takes a
  few seconds and that the original is kept. On Run, Erase's source is drawn (``markup_flatten.erase_source``) and
  encoded on a worker; then one Media Studio image generation starts (``studio_ops.start_job``) with the picture as
  its reference. A timer follows its record: ready, its image is attached to the composer (``ReferenceStore``) and the
  viewer, if still on the same picture, switches to it; failed or stopped, the viewer says why. The result also stays
  in Media Studio's library, as every generation does.
* **Resize** (``markup_resize``): local and free. The picture is read whole, cropped and resampled (bilinear), encoded
  with its alpha on a worker (``rgba_png``) and attached as a new picture.

Main thread, except the encoders.
"""

import threading
import time
import traceback
import uuid

import bpy

from . import markup_edit as me, markup_resize, studio_tile_previews as tiles
from .ui_text import iface_, n_

POLL_S = 0.5
EDIT_NAMES = {"remove_bg": n_("{name} (background removed)"), "erase": n_("{name} (erased)")}
RESIZED_NAME = n_("{name} (resized)")
ERASE_SOURCE_NAME = n_("{name} (to erase)")
RUNNING = {"remove_bg": n_("Removing the background…"), "erase": n_("Erasing…")}
FAILED = n_("It couldn't be done: {reason}")
NOT_ATTACHED = n_("The result is in Media Studio, but couldn't be attached: {reason}")
GONE = n_("It was removed from Media Studio")
SHRUNK = n_("Made at {width} × {height} px, the largest that fits the chat's picture limit")


def _models():
    from . import studio_state, ui
    return studio_state.model_options("IMAGE", ui.generation_connections(), ui.generation_health())


def card(state, action):
    """The question for ``action`` on the viewer's picture: (title, lines, run label). ValueError without a model, or
    while the switch is off (N7: checked before any model is chosen)."""
    from . import markup_tools, studio_views
    if not markup_tools.AI_EDITS:
        raise ValueError(iface_(me.NO_MODEL))
    models = _models()
    model = me.choose(models, action, me.preferred(models, studio_views.chosen("IMAGE")))
    if model is None:
        raise ValueError(iface_(me.NO_MODEL))
    state.edit_model = model
    return me.TITLES[action], me.card_lines(action, model, state.session.doc.size), me.RUN[action]



def reason(error):
    """A failure's reason as the person reads it (break-it of 2298d608dd1, S5): an exception through the interface's
    own error sentence (``studio_state.error_text``: translated, never Python's wording); a text through its
    catalogue entry, when it has one."""
    from .studio_state import error_text
    return error_text(error) if isinstance(error, BaseException) else iface_(str(error))

def _encode_then(rgba, width, height, alpha, limit, done):
    """Encode on a worker within ``limit`` bytes (``picture_budget``), then ``done(png or None, error, size)`` on the
    main thread (``size``: the encoded width and height, smaller than asked when it had to step down to fit)."""
    from .picture_budget import encode_within
    result = {}

    def encode():
        try:
            result["png"], *result["size"] = encode_within(rgba, width, height, limit, alpha=alpha)
        except (ValueError, MemoryError) as error:
            result["error"] = error

    worker = threading.Thread(target=encode, name="Angelo picture encode", daemon=True)  # i18n: thread name
    worker.start()

    def finish():
        if worker.is_alive():
            return 0.05
        try:
            done(result.get("png"), result.get("error", ""), tuple(result.get("size", (width, height))))
        except Exception as error:   # A timer callback: said in the viewer, never an unseen traceback.
            from . import markup_overlay
            traceback.print_exc()
            state = markup_overlay.current()
            if state is not None:
                markup_overlay.say(state, iface_(FAILED, reason=reason(error)))
        return None

    bpy.app.timers.register(finish, first_interval=0.05)


def _write_picture(controller, name, png):
    """A new private picture of the chat with ``png``; its record."""
    from . import owned_paths
    record = controller.references.new_observation(name)
    try:
        with open(owned_paths.require_writable(record["path"]), "wb") as handle:
            handle.write(png)
        record["bytes"] = len(png)
    except OSError:
        controller.references.discard_observation(record)
        raise
    return record


def _attach_and_show(state, record):
    """Put ``record`` in the composer (the original stays) and show it if the viewer still shows the original."""
    from . import markup_overlay
    from .markup_message import room
    controller = state.controller
    try:
        room(controller)   # Filled meanwhile: the new picture is not kept (N2).
    except ValueError as error:
        controller.references.discard_observation(record)
        return markup_overlay.say(state, iface_(str(error)))
    controller.attachments.append(record)
    controller.version += 1
    if markup_overlay.current() is state:
        markup_overlay.open_reference(controller, record)


def run(state, action):
    """Start Remove BG or Erase on the viewer's picture (the card was confirmed); never while the switch is off."""
    from . import markup_flatten, markup_message as mm, markup_overlay, markup_tools
    if not markup_tools.AI_EDITS:
        return
    model, session = state.edit_model, state.session
    name = state.reference.get("name", "")
    if action == "remove_bg":
        _start(state, action, model, {"id": str(uuid.uuid4()), "path": state.path, "name": name})
        return
    size = mm.flat_size(session.doc.size)
    pixels, width, height = tiles.decode(state.path, max(size))
    rgba, width, height = markup_flatten.erase_source(session.doc.size, session.erase_strokes,
                                                      tiles.upload(pixels, width, height))
    token = state.token

    def encoded(png, error, _size):
        if markup_overlay.current() is not state or state.token is not token:
            return
        if png is None:
            return markup_overlay.say(state, iface_(FAILED, reason=reason(error)))
        try:
            source = _write_picture(state.controller, iface_(ERASE_SOURCE_NAME, name=name), png)
        except OSError as error:
            return markup_overlay.say(state, iface_(FAILED, reason=reason(error)))
        _start(state, action, model, {"id": str(uuid.uuid4()), "path": source["path"], "name": source["name"]},
               temporary=source)

    state.working = iface_(RUNNING[action])
    _encode_then(rgba, width, height, False, state.controller.references.image_limit, encoded)


def _start(state, action, model, reference, temporary=None):
    """Start the job on ``reference``; ``temporary``: Erase's source copy, discarded once the job ends (N1)."""
    from . import markup_overlay, studio_ops

    def ended():
        if temporary is not None:
            state.controller.references.discard_observation(temporary)
    try:
        asset = studio_ops.start_job("IMAGE", me.prompt(action), model, "", me.options(model, action,
                                     state.session.doc.size), [reference])
    except Exception as error:   # Refused before anything was billed: said in the viewer.
        ended()
        state.working = ""
        return markup_overlay.say(state, iface_(FAILED, reason=reason(error)))
    state.working = iface_(RUNNING[action])
    markup_overlay.redraw()
    started = time.monotonic()

    def follow():
        from . import studio
        record = studio.library().assets.get(asset["id"], {})
        if not record:   # Deleted in Media Studio (S11): nothing left to follow.
            ended()
            state.working = ""
            markup_overlay.say(state, iface_(FAILED, reason=iface_(GONE)))
            return None
        status = record.get("status", "")
        if status == "ready":
            ended()
            _arrived(state, action, record)
            return None
        if status in ("failed", "stopped", "cancelled"):
            ended()
            state.working = ""
            markup_overlay.say(state, iface_(FAILED, reason=record.get("error") or status))   # Server text: never translated.
            return None
        if markup_overlay.current() is state:
            state.waited = time.monotonic() - started
            markup_overlay.redraw()
        return POLL_S

    bpy.app.timers.register(follow, first_interval=POLL_S)


def _arrived(state, action, asset):
    from . import markup_overlay, studio
    state.working = ""
    try:
        path = studio.library().checked_file(asset, asset.get("thumbnail") or asset["files"][0])
        record = state.controller.references.import_images([path], existing=state.controller.attachments)[0]
    except (OSError, ValueError, IndexError, KeyError) as error:
        return markup_overlay.say(state, iface_(NOT_ATTACHED, reason=reason(error)))
    record["name"] = iface_(EDIT_NAMES[action], name=state.reference.get("name", ""))[:160]
    _attach_and_show(state, record)


def resize(state):
    """Make the cropped and scaled picture, alpha kept, as a new picture of the chat."""
    import numpy as np
    from . import markup_overlay
    plan = state.session.resize_plan()
    if not plan["changed"]:
        return
    from .markup_message import room
    try:
        room(state.controller)   # Before any work: the result goes into the message box (N2).
    except ValueError as error:
        return markup_overlay.say(state, iface_(str(error)))
    w, h = state.session.doc.size
    pixels, width, height = tiles.decode(state.path, max(w, h))
    grid = np.asarray(pixels, dtype=np.float32).reshape(height, width, 4)   # Rows bottom first.
    x, y, cw, ch = plan["crop"]
    grid = grid[height - (y + ch):height - y, x:x + cw]
    out_w, out_h = plan["size"]
    if (out_w, out_h) != (cw, ch):
        grid = markup_resize.bilinear(grid, out_w, out_h)
    rgba = (np.clip(grid, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8).tobytes()
    name, token = state.reference.get("name", ""), state.token

    def encoded(png, error, size):
        if markup_overlay.current() is not state or state.token is not token:
            return
        state.working = ""
        if png is None:
            return markup_overlay.say(state, iface_(FAILED, reason=reason(error)))
        try:
            record = _write_picture(state.controller, iface_(RESIZED_NAME, name=name), png)
        except OSError as error:
            return markup_overlay.say(state, iface_(FAILED, reason=reason(error)))
        _attach_and_show(state, record)
        if tuple(size) != (out_w, out_h):   # Stepped down to fit the chat's image limit: said, never silent.
            markup_overlay.say(state, iface_(SHRUNK, width=size[0], height=size[1]))

    state.working = iface_(markup_resize.SIZE_LABEL, width=out_w, height=out_h)
    _encode_then(rgba, out_w, out_h, True, state.controller.references.image_limit, encoded)
