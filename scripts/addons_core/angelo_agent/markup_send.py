# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Send to Angelo from the picture viewer (2.1.0, doc/angelo/attachment_markup.rst, "What the agent gets").

The picture is flattened with its marks on the main thread, encoded within the chat's picture limit on a worker
(``picture_budget``), then written, recorded as sent (``markup_store.freeze``: the turn reads it while it is built) and
sent through the person's own send (``console.send_message``) with the marked picture and the original.

Break-it of 2298d608dd1:
* S2: a refused send (a full queue, the chat's scene not shown, no connection) lost the original from the message box
  and kept the instructions only inside the sent record. Now the original leaves the box only after the send went
  through; a refusal takes the sent record back (``markup_store.unfreeze``: the draft returns), discards the marked
  picture and leaves the viewer open on the original with the person's marks and words, saying why.
* S3: closing after a send force-saved the draft again, so a sent markup's marks came back as a draft. A sent viewer
  is marked ``sent`` and never saves its draft again.

Re-review of 4e712847d3b:
* S3: a phone photo whose file is stored sideways with an EXIF orientation is read upright (``studio_tile_previews
  .decode``), so the marked picture is upright; the original goes as an upright copy too, so the two pictures and the
  block's coordinates agree whether or not a provider applies the tag.
* S4: an exception in the encode, the write or the delivery is said in the viewer and Send is usable again; nothing
  ends a timer unseen with Send left busy.

Re-review of b5808fd5a2e: every picture is written before the markup is recorded as sent (``freeze`` last), so a
failed write keeps the draft (S4); the upright copy is the flatten's own upright pixels, never a second decode at full
size (S5), named as the PNG it is (N9), and the recorded markup names it as the original, so the block's
``original_picture`` is a picture the message carries (S3); a copy that fails to encode refuses the send (N4).
"""
from pathlib import Path

import threading
import traceback

import bpy

from . import markup_store, studio_tile_previews as tiles
from .ui_text import iface_


def send(state):
    """Flatten, encode off the main thread, then write and send (``_deliver``)."""
    from . import markup_flatten, markup_message as mm, markup_overlay as mo
    if state.sending or not state.session.can_send:
        return
    session = state.session
    session.commit_editing()
    state.sending, state.error = True, ""
    try:
        path = state.controller.references.checked_path(state.reference)
        size = mm.flat_size(session.doc.size)
        pixels, width, height = tiles.decode(path, max(size))   # Upright already (EXIF): the copy's pixels too.
        upright = _upright(path, pixels, width, height)
        texture = tiles.upload(pixels, width, height)
        rgba, width, height = markup_flatten.render(session.doc, texture)
    except (OSError, RuntimeError, ValueError, MemoryError) as error:
        return _refused(state, error)
    token, limit = state.token, state.controller.references.image_limit
    result = {}

    def encode():   # Within the chat's picture limit (picture_budget): a photo's marked picture is sent, not refused.
        from .picture_budget import encode_within
        try:
            result["png"] = encode_within(rgba, width, height, limit)[0]
            if upright is not None:
                result["upright"] = encode_within(*upright, limit)[0]
        except Exception as error:   # Any failure is said in the viewer (S4), never an empty reason.
            result["error"] = error

    worker = threading.Thread(target=encode, name="Angelo markup encode", daemon=True)  # i18n: thread name
    worker.start()

    def deliver():
        if worker.is_alive():
            return 0.05
        try:
            _deliver(token, result)
        except Exception as error:   # A timer callback: said in the viewer and Send usable again (S4).
            traceback.print_exc()
            shown = mo.current()
            if shown is not None and shown.token is token:
                _refused(shown, error)
        return None

    bpy.app.timers.register(deliver, first_interval=0.05)
    mo.redraw()


def _upright(path, pixels, width, height):
    """The original as 8-bit RGBA rows for an encode, from the flatten's own upright pixels, when its file is stored
    turned (an EXIF orientation other than 1); else None: the file itself is sent."""
    from . import picture_size
    if picture_size.orientation(path) == 1:
        return None
    import numpy as np
    rgba = (np.clip(np.asarray(pixels, dtype=np.float32), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8).tobytes()
    return rgba, width, height


def _refused(state, error):
    from . import markup_overlay as mo
    from .markup_edit_host import reason
    state.sending, state.error = False, iface_(mo.SEND_FAILED, reason=reason(error))
    mo.redraw()


def _deliver(token, result):
    from . import console, markup_overlay as mo, owned_paths
    state = mo.current()
    if state is None or state.token is not token:
        return   # Closed while encoding: nothing is written or sent.
    if "png" not in result or "error" in result:   # A copy that failed is never replaced by the turned file (N4).
        return _refused(state, result.get("error", ""))
    store, controller, original = state.controller.references, state.controller, state.reference
    written = []

    def write(name, png):
        record = store.new_observation(name)
        written.append(record)
        with open(owned_paths.require_writable(record["path"]), "wb") as handle:
            handle.write(png)
        record["bytes"] = len(png)
        return record

    def discard():
        for record in written:
            store.discard_observation(record)
    try:   # Every picture first; the markup is recorded as sent last, so a failed write keeps the draft (S4).
        marked = write(iface_(mo.MARKED_NAME, name=original.get("name", "")), result["png"])
        shown = original
        if "upright" in result:   # The original as the viewer showed it (S3); the file itself stays in the chat.
            shown = write(Path(original.get("name", "") or "picture").stem + ".png", result["upright"])
        sent = markup_store.freeze(store, state.session.doc, original, marked, shown=shown)
    except Exception as error:
        discard()
        return _refused(state, error)
    text = state.session.doc.instructions.strip()
    try:
        console.send_message(controller, text, sent)
    except Exception as error:   # Refused (S2): the record taken back, the original, the marks and the words kept.
        try:
            markup_store.unfreeze(store, state.session.doc, marked)
        except (OSError, ValueError):   # The chat's folder went with its chat: nothing to take back (S4).
            pass
        discard()
        return _refused(state, error)
    controller.attachments[:] = [r for r in controller.attachments if r.get("id") != original["id"]]
    controller.version += 1
    state.sent = True   # S3: never saved as a draft again.
    mo.close("sent")
    mo._note("sent", marked["id"])
