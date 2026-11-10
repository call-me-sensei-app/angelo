# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A picture pasted into the chat's composer becomes an image attachment, as a dropped picture does.

The composer's paste (``chat_view``, Ctrl+V or Cmd+V) asks ``paste`` first and inserts the clipboard's text only
when ``paste`` says it took nothing. The picture is read by the native ``_bpy._angelo_clipboard`` module
(``source/blender/python/intern/bpy_angelo_clipboard.cc``), saved as a PNG and attached through
``ReferenceStore.import_images``, the path every attached or dropped file takes, so it becomes an ordinary image
chip with the same limits. A build without the module, X11 (no picture clipboard in Blender) and a background
session paste text only, as before.

Text and a picture on the clipboard together: the text wins when it is something written (a paragraph, a
spreadsheet cell, a word), because office and design applications put a rendered picture of copied text beside it.
The picture wins when the text only says where the picture came from: nothing, one web address (a browser's
"Copy Image" carries the picture's address on some systems) or one path or name of an image file (a file copied
in Finder). ``names_the_picture`` decides from the text alone, so an ordinary text paste never asks the operating
system for a picture.

The PNG is written into an owned ``private_roots`` scratch folder (mode 0700, registered before use)
and removed when ``paste`` returns, on every path: attached (the
attachment is a copy in the chat's state folder), refused or failed. The operating system is asked only on the
paste key, never per frame. No ``bpy`` at import: host-testable.
"""

import re
from pathlib import Path

from . import display_text, private_roots, references
from .ui_text import n_, notice

# The attachment's name, as the model and the chip see it: data, never translated (as ``draft_overflow.FILE_NAME``).
# ASCII, so it is a valid file name on every system, and numbered so several pasted pictures stay apart.
FILE_NAME = "Pasted image {number}.png"   # i18n: a file name the model reads, like draft_overflow.FILE_NAME
# The longest side a pasted picture may have, checked before it is encoded: Anthropic's API refuses an image whose
# side is longer than 8000 px, so a larger picture could never be sent, and encoding one would hold Blender's main
# thread for seconds. The file must then fit the attachment limit (``ReferenceStore.image_limit``, 5 MiB).
MAX_SIDE = 8000
# PNG compression (0 to 100): Blender's own default (fast). The native save encodes once more at the strongest
# compression, from the same pixels (the clipboard is read once), only when the file is past the byte limit.
FAST = 15

BUSY = n_("Wait for this turn to finish before attaching references")   # The Attach button's words (ui.py).
FULL = n_("This message has as many attachments as it can carry. Send it or remove an attachment to paste the "
          "picture.")
TOO_WIDE = n_("This picture is too large to attach ({width} × {height} px). Each side can be at most {limit} px, "
              "so paste a smaller picture.")
TOO_HEAVY = n_("This picture is too large to attach: even compressed, it is over {limit} MB. Paste a smaller or "
               "cropped picture.")
FAILED = n_("Couldn't paste the picture. Copy it again and paste once more.")

_ADDRESS = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*://|data:image/)\S+")


def binding():
    """The native clipboard module, or None in a build without it (text-only paste)."""
    try:
        from _bpy import _angelo_clipboard
    except ImportError:
        return None
    if callable(getattr(_angelo_clipboard, "image_available", None)) and callable(
            getattr(_angelo_clipboard, "image_save", None)):
        return _angelo_clipboard
    return None


def image_suffixes():
    """File-name endings of pictures: every format Blender reads (``bpy.path.extensions_image``) and every one an
    attachment takes."""
    try:
        from bpy.path import extensions_image
        found = frozenset(str(suffix).lower() for suffix in extensions_image)
    except (ImportError, TypeError):
        found = frozenset()
    return found | frozenset(references.MIME)


def names_the_picture(text):
    """Whether clipboard ``text`` only says where a picture came from (so the picture beside it is what was copied):
    empty, one web or data address, or one path or name of an image file. Anything written is the paste."""
    value = (text or "").strip()
    if not value:
        return True
    if "\n" in value or "\r" in value:
        return False
    if _ADDRESS.fullmatch(value):
        return True
    if "://" in value:   # A sentence with an address in it is something written.
        return False
    return Path(value.strip("\"'")).suffix.lower() in image_suffixes()


def file_name(attachments):
    """The first ``FILE_NAME`` no attachment of this message already has."""
    taken = {str(reference.get("name", "")) for reference in attachments}
    number = 1
    while FILE_NAME.format(number=number) in taken:
        number += 1
    return FILE_NAME.format(number=number)


def _refuse(controller, template, **fields):
    controller.emit("message", {"role": "error", **notice(template, **fields)})
    return True


def paste(controller, text):
    """Attach the clipboard's picture to the message being written. True when the paste was the picture (attached,
    or refused with a notice saying why); False when the composer should insert ``text`` (the clipboard's text)."""
    native = binding()
    if native is None or not names_the_picture(text):
        return False
    try:
        if not native.image_available():
            return False
    except RuntimeError:   # Not on the main thread: never a paste from the composer.
        return False
    if getattr(controller, "busy", False):
        return _refuse(controller, BUSY)
    if len(controller.attachments) >= references.MAX_IMAGES:
        return _refuse(controller, FULL)
    store = controller.references
    with private_roots.TemporaryDirectory(prefix="angelo-assets-", ignore_cleanup_errors=True) as folder:
        path = Path(folder) / file_name(controller.attachments)
        try:
            size = native.image_save(str(path), compression=FAST, max_side=MAX_SIDE, max_bytes=store.image_limit)
            if size is None:
                # Seen but not read: gone since, or data the system could not decode (a damaged or unsupported
                # file). With text beside it, that text is the paste; with none, the person learns why nothing came.
                return False if (text or "").strip() else _refuse(controller, FAILED)
            if size["refused"] == "side":
                return _refuse(controller, TOO_WIDE, width=size["width"], height=size["height"], limit=MAX_SIDE)
            if size["refused"] == "bytes":
                return _refuse(controller, TOO_HEAVY, limit=store.image_limit // (1024 * 1024))
            if not size["saved"]:
                return _refuse(controller, FAILED)
            attached = store.import_images([str(path)], controller.attachments)
        except ValueError as error:   # import_images' own refusal, as for a dropped file (chat_drop).
            controller.emit("message", {"role": "error", **display_text.error_notice(error)})
            return True
        except (OSError, RuntimeError):
            return _refuse(controller, FAILED)
    controller.attachments.extend(attached)
    controller.version += 1
    return True
