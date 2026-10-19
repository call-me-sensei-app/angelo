# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pictures Blender saves for a view, never writing the scene: display copies and multilayer OpenEXRs.

``Image.save_render`` takes its file format from the scene's render output, so
the Render Result, its passes and every scene-linear file used to be shown by
setting the scene's image format for one save and putting it back
(``view_render_restore.LookSettings``). Those writes notify add-ons'
``bpy.msgbus`` subscribers and update the depsgraph, which runs their
depsgraph-update handlers, so the Read-only and Ask modes, Ask being the
default, refused these views whenever an add-on had such a handler
(``view_policy``), and the agent could not see renders.

``_bpy._angelo_picture.save_copy`` runs the same save (``BKE_image_save``, a
copy) with an image format of its own: the scene's output format, then the
settings below set on that copy without an RNA update, and with ``display``
the scene's own view and display, curves included. A file is read into an
image outside the document and freed. Nothing is written, published, tagged,
added or removed, so these views run in every approval mode and run no add-on
Python; the pixels are the earlier route's (``test_blender_view_sources``,
"Pictures saved without writing the scene"). A build without that helper
keeps the earlier route, with its approval-mode rule (``view_policy``).
Main thread only.
"""

from contextlib import contextmanager

PICTURE = {"media_type": "IMAGE", "file_format": "PNG", "color_mode": "RGBA", "color_depth": "8", "compression": 0}
MULTILAYER = {"media_type": "MULTI_LAYER_IMAGE", "file_format": "OPEN_EXR_MULTILAYER", "color_depth": "32",
              "exr_codec": "ZIP"}


def native():
    """``_bpy._angelo_picture``, or None in a build without it."""
    try:
        from _bpy import _angelo_picture
    except ImportError:
        return None
    return _angelo_picture if hasattr(_angelo_picture, "save_copy") else None


def display_copy(scene, path, *, image=None, source=None):
    """Save ``image``, or the file ``source``, at ``path`` as an 8-bit RGBA PNG in the scene's view transform,
    look and exposure, as Blender's render window shows it: ``(unrestored, exact, multilayer)``."""
    picture = native()
    if picture is not None:
        saved = picture.save_copy(scene, str(path), image=image, source=None if source is None else str(source),
                                  settings=dict(PICTURE), display=True)
        return [], True, saved["multilayer"]
    from . import view_policy
    from .view_render_restore import picture_values
    values, exact = picture_values(scene)
    view_policy.before_settings(scene, values)  # Before loading: a loaded file is a data-block added too.
    with _loaded(image, source) as loaded:
        unrestored = _by_settings(scene, values, loaded, path)
        return unrestored, exact, loaded.type == "MULTILAYER"


def multilayer_copy(scene, image, path):
    """Save ``image`` (the Render Result) at ``path`` as a 32-bit multilayer OpenEXR with every pass: the
    settings the earlier route could not put back (none with the helper)."""
    picture = native()
    if picture is not None:
        picture.save_copy(scene, str(path), image=image, settings=dict(MULTILAYER))
        return []
    from . import view_policy
    values = {"render.image_settings." + name: value for name, value in MULTILAYER.items()
              if name != "media_type" or hasattr(scene.render.image_settings, "media_type")}
    view_policy.before_settings(scene, values)
    return _by_settings(scene, values, image, path)


@contextmanager
def _loaded(image, source):
    """The earlier route's image: ``image``, or ``source`` loaded as a data-block and removed afterwards."""
    import bpy
    if source is None:
        yield image
        return
    loaded = bpy.data.images.load(str(source), check_existing=False)
    try:
        yield loaded
    finally:
        bpy.data.images.remove(loaded)


def _by_settings(scene, values, image, path):
    """The earlier route, after ``view_policy`` admitted it: the scene's image format set for the save and put
    back; the paths Blender did not take back."""
    from .view_render_restore import LookSettings
    settings = LookSettings(scene, "view")
    try:
        settings.apply(values)
        image.save_render(str(path), scene=scene)
    finally:
        unrestored = settings.restore()
    return unrestored
