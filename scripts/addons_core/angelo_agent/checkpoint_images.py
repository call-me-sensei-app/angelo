# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unsaved image pixels and snapshots: what Restore and Undo restore cannot bring back, said plainly.

A snapshot is a private save of the document (``checkpoint_capture``). A save
keeps an image's pixels only as its own file or its packed data holds them,
so pixel edits that were not saved (painting, a bake, a script writing
``Image.pixels``) are in no snapshot: a capture never writes the person's image
files, whatever their Save Modified Images preference (a normal Save may), nor
packs external files into the live document, whatever Automatically Pack
Resources says (a normal Save does). An image kept in its own file is
referenced by path, as in the live document, and opens as that file is now. Restore replaces the document, so the unsaved pixel
edits of that moment are discarded, and the safety snapshot taken first does
not hold them either: Undo restore cannot bring them back. This is honest
partial recovery, not a pixel backup.

``unsaved_images`` names the images whose pixels are unsaved (``is_dirty``),
not the Render Result or the Viewer, which no save keeps. ``restore_review``
prepares what the Restore dialog says before it draws (``draw_restore``):
the rule, the images that would lose pixel edits now, and for Undo restore
the images whose unsaved edits its safety snapshot never held (recorded on
the restore notice as ``unsaved_images``). ``restore_notices`` are the chat
notices after a restore that replaced the document. Main thread only.
"""

from .ui_text import iface_, n_, notice

MAX_NAMED = 6
NOT_PIXELS = frozenset({"RENDER_RESULT", "COMPOSITING"})
SNAPSHOT_RULE = n_("Snapshots don't hold image pixel edits that were unsaved when they were taken, and an image "
                   "kept in its own file opens as that file is now.")
LOSE_NOW = n_("Unsaved pixel edits in {images} are in no snapshot: restoring discards them and Undo restore can't "
              "bring them back. Save those images first to keep the edits.")
NEVER_HELD = n_("When this safety snapshot was taken, {images} had unsaved pixel edits; it doesn't hold them.")
DISCARDED = n_("Unsaved pixel edits in {images} were in no snapshot and were discarded; Undo restore can't bring "
               "them back.")


def capture_packs():
    """Whether this build's private capture still packs external files into the live document when
    Automatically Pack Resources is on (``ui.capture_checkpoint`` then holds that setting off around it).

    Builds whose native module says ``capture_never_packs`` skip the packing and keep the person's
    setting in the snapshot, where external files are referenced by absolute path, as live.
    """
    try:
        import _bpy
    except ImportError:
        return True
    return not getattr(getattr(_bpy, "_angelo_checkpoint", None), "capture_never_packs", False)


def unsaved_images(bpy):
    """Sorted names of the images with unsaved pixel edits (none for a stand-in without images)."""
    images = getattr(getattr(bpy, "data", None), "images", ())
    return sorted(image.name for image in images if image.type not in NOT_PIXELS and image.is_dirty)


def names(images):
    shown = ", ".join(images[:MAX_NAMED])
    return shown if len(images) <= MAX_NAMED else "%s, … +%d" % (shown, len(images) - MAX_NAMED)


def recorded_unsaved(messages, checkpoint_id):
    """The images a restore notice recorded as unsaved when it took the safety snapshot ``checkpoint_id``."""
    for message in reversed(messages):
        if message.get("restore_checkpoint") == checkpoint_id:
            return [name for name in message.get("unsaved_images") or () if isinstance(name, str)]
    return []


def restore_review(bpy, controller, checkpoint_id):
    """``checkpoint_review`` plus the image facts the dialog states (read once, before drawing)."""
    from .checkpoint_review_ui import checkpoint_review
    messages = controller.state["messages"]
    review = checkpoint_review(controller.checkpoints.load(checkpoint_id), messages)
    review["unsaved_now"] = unsaved_images(bpy)
    review["unsaved_then"] = recorded_unsaved(messages, checkpoint_id) if review["scope"] == "before_restore" else []
    return review


def draw_restore(layout, review):
    """The Restore and Undo restore dialog: what is restored, what is not, and the pixels at stake."""
    from . import chat_view
    from .checkpoint_review_ui import checkpoint_review
    review = review or checkpoint_review({})

    def paragraph(text, icon='NONE'):
        indent = 'NONE' if icon == 'NONE' else 'BLANK1'  # Wrapped lines line up under the first one's text.
        for index, line in enumerate(chat_view.wrap(text, 440, 12, chat_view._font)):  # Translate, then wrap.
            layout.label(text=line[0], translate=False, icon=icon if index == 0 else indent)

    layout.label(text=review["question"])
    paragraph(iface_(review["note"]))
    layout.label(text="This affects the scene used by all chats. History stays intact.")
    layout.label(text="A safety snapshot of the current document is saved first.")
    layout.label(text="Scoped file-tool writes are reverted; arbitrary Python/network effects are not.")
    layout.label(text="Recovery opens a working copy. Your original project is not overwritten.")
    paragraph(iface_(SNAPSHOT_RULE))
    if review.get("unsaved_then"):
        paragraph(iface_(NEVER_HELD, images=names(review["unsaved_then"])), 'INFO')
    if review.get("unsaved_now"):
        paragraph(iface_(LOSE_NOW, images=names(review["unsaved_now"])), 'ERROR')


def restore_notices(discarded, never_held):
    """Chat notices after a restore replaced the document (English text, translated where the chat draws it)."""
    notices = []
    if never_held:
        notices.append({"role": "system", **notice(NEVER_HELD, images=names(never_held)),
                        "unsaved_images_not_restored": list(never_held)})
    if discarded:
        notices.append({"role": "system", **notice(DISCARDED, images=names(discarded)),
                        "unsaved_images_discarded": list(discarded)})
    return notices
