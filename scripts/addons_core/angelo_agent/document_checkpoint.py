# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One native request snapshot of the open document into a snapshot store, with or without an OS window.

Angelo's window (``ui.capture_checkpoint``) and a worker Blender (``worker_host``) both capture through this:
the window passes its main window, a worker its background window record (``Angelo --background`` keeps the
factory start-up file's window with no OS window). Main thread only.
"""


def capture(bpy, store, window, original, *, capture_mode, purpose, retain_native=False, load=None):
    """``checkpoint_capture.capture_checkpoint`` with the capture's own preference holds (checkpoint_policy.rst).

    ``load``: the document's load serial (``ui.document_key()``); with it the capture keeps what each scene is called
    now by its session uid (``checkpoint_scene_map``, beside the checkpoint), so a Restore rebinds each chat to its
    own scene even after scenes were renamed. Kept only when no add-on's save handler is registered at any point
    of the capture (``handler_owner.bundled``: Blender's and Angelo's own code never rename scenes), because an add-on
    can rename scenes around the write and reorder or add handlers during the dispatch, so no read outside the native
    write can prove the written names (break-it r2m4c F4, round 3 M-3). Then the names are read where the file is
    written (a ``save_pre`` handler added last and a ``save_post`` one put first) and before the capture; reads that
    differ keep no map. Without a map Restore rebinds by name. A map that cannot be kept never fails the capture.

    Private recovery files need the complete document, not a camera thumbnail, and automatic thumbnails also create
    GPU contexts from a raw timer or approval popup; a capture never writes the person's image files nor packs
    external files into their document ("Unsaved image pixels"). The preferences are restored even when native
    saving fails; nothing saves application preferences.
    """
    prefs = bpy.context.preferences
    filepaths = prefs.filepaths
    preview_type, save_images = filepaths.file_preview_type, filepaths.save_modified_images
    dirty = prefs.is_dirty
    dirty_editable = not prefs.bl_rna.properties['is_dirty'].is_readonly
    from .checkpoint_images import capture_packs
    autopack = bpy.data.use_autopack and capture_packs()
    try:
        filepaths.file_preview_type, filepaths.save_modified_images = 'NONE', 'NEVER_SAVE'
        if autopack:   # Older builds pack unless held off.
            bpy.data.use_autopack = False
        with bpy.context.temp_override(window=window):
            from .checkpoint_capture import capture_checkpoint as capture_native
            from . import checkpoint_scene_map
            from .handler_owner import bundled
            reads = [checkpoint_scene_map.capture(load, bpy.data.scenes)]
            before, after = bpy.app.handlers.save_pre, bpy.app.handlers.save_post

            def foreign():   # An add-on's save handler is (or was) there: it may rename scenes around the write.
                return any(handler is not at_save and not bundled(bpy, handler) for handler in (*before, *after))

            def at_save(*_arguments):
                reads.append(None if foreign() else checkpoint_scene_map.capture(load, bpy.data.scenes))
            if foreign():
                reads.append(None)
            before.append(at_save)
            after.insert(0, at_save)
            try:
                result = capture_native(store, original, capture_mode=capture_mode, purpose=purpose,
                                        retain_native=retain_native)
            finally:
                for handlers in (before, after):
                    while at_save in handlers:
                        handlers.remove(at_save)
            if foreign():
                reads.append(None)
            saved = reads[1:] or reads
            checkpoint_scene_map.save(store, (result[0] if retain_native else result)["id"],
                                      saved[0] if all(value == saved[0] for value in saved) else None)
            return result
    finally:
        filepaths.file_preview_type, filepaths.save_modified_images = preview_type, save_images
        if autopack:
            bpy.data.use_autopack = True
        if dirty_editable:
            prefs.is_dirty = dirty
