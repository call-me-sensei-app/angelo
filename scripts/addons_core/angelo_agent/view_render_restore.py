# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a RENDERED look sets for its render job, and how it is put back exactly (``view_render``).

``LookSettings`` extends the background jobs' ``Overrides`` (job_settings): the
values a look sets are recorded on the scene first, together with every value
of the image format and the video settings, because Blender changes those by
itself when the file type changes (a 32-bit depth does not survive PNG, a video
type has its own file format, the colour space follows the type). The restore
writes them back in dependency order, twice, and reports any value Blender
would not take back instead of assuming it did.

``ResultSlot`` keeps the person's last render: a look renders into its own slot
of Blender's Render Result image. Main thread only.
"""

import bpy

from .job_settings import TAG, Overrides, get, put, rna_key

SLOT = "Angelo"  # The product's name: the render slot looks use. Python can add a slot but not remove one.
_PLAIN_TYPES = frozenset({"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"})
# Embedded ImageFormatData members with native direct getters, not arbitrary RNA pointer traversal.
_FORMAT_SETTINGS = {"stereo_3d_format": "Stereo3dFormat", "view_settings": "ColorManagedViewSettings",
                    "display_settings": "ColorManagedDisplaySettings",
                    "linear_colorspace_settings": "ColorManagedInputColorspaceSettings"}


def _scalar_paths(owner, prefix, depth=2):
    """Native writable scalars and admitted embedded settings, without following user-defined getters.

    A read-only pointer cannot be replaced, but its child settings can still be
    writable. Admit only the known direct ImageFormatSettings pointers before
    checking leaf writability; never traverse curves, ID pointers or collections.
    """
    rna = owner.bl_rna
    nested = _FORMAT_SETTINGS if rna.identifier == "ImageFormatSettings" else {}
    for prop in rna.properties:
        name = prop.identifier
        if name == "rna_type" or getattr(prop, "is_runtime", False) or getattr(prop, "is_array", False):
            continue
        if prop.type in _PLAIN_TYPES and not prop.is_readonly:
            yield prefix + name
        elif prop.type == "POINTER" and depth and name in nested and nested[name] == prop.fixed_type.identifier:
            # An inaccessible admitted native field aborts capture before apply mutates settings.
            value = getattr(owner, name)
            if value is not None and not isinstance(value, bpy.types.ID):
                yield from _scalar_paths(value, prefix + name + ".", depth - 1)


def picture_values(scene):
    """What makes Blender write an 8-bit RGBA PNG in the colours its render window shows: ``(values, exact)``.

    The values are for ``LookSettings.apply``. A scene whose output overrides
    the colour management writes its files in another view transform than the
    one its renders are shown in. The override itself is left on (switching it
    back on would copy the scene's settings over its own, curves included);
    its plain values are set to the scene's for the write instead. ``exact`` is
    False when the scene's view uses custom curves, which cannot be set that
    way.
    """
    settings, values = scene.render.image_settings, {}
    if hasattr(settings, "media_type"):
        values["render.image_settings.media_type"] = "IMAGE"
    values.update({"render.image_settings.file_format": "PNG", "render.image_settings.color_mode": "RGBA",
                   "render.image_settings.color_depth": "8", "render.image_settings.compression": 0})
    exact = True
    if getattr(settings, "color_management", "FOLLOW_SCENE") == "OVERRIDE":
        for name, source in (("display_settings", scene.display_settings), ("view_settings", scene.view_settings)):
            prefix = "render.image_settings.%s." % name
            paths = sorted(_scalar_paths(getattr(settings, name), prefix, depth=0),
                           key=lambda path: not path.endswith(".view_transform"))  # A look belongs to its transform.
            for path in paths:
                values[path] = getattr(source, path[len(prefix):])
        exact = not scene.view_settings.use_curve_mapping
        if not exact and settings.view_settings.curve_mapping is None:
            # Enabling this would allocate an identity CurveMapping that disabling cannot remove.
            # Leave the same identity approximation without changing the saved scene structure.
            values["render.image_settings.view_settings.use_curve_mapping"] = False
    return values, exact


def _watched(scene, values):
    """The image-format and video settings a file type change can alter, besides ``values`` themselves."""
    render = scene.render
    return [path for owner, prefix in ((render.image_settings, "render.image_settings."),
                                       (render.ffmpeg, "render.ffmpeg."))
            for path in _scalar_paths(owner, prefix) if path not in values]


def written_keys(scene, values):
    """``{(struct, property)}`` that ``LookSettings`` may write for ``values``, restore included, and the
    animation mute of ``hold``: what those writes publish to ``bpy.msgbus`` (render_extensions)."""
    keys = {("FCurve", "mute")}
    for path in [*_watched(scene, values), *values]:
        try:
            keys.add(rna_key(scene, path))
        except (AttributeError, KeyError, TypeError, ValueError):
            pass  # A setting this scene lacks is never written.
    return keys


class LookSettings(Overrides):
    """A look's temporary settings, with every value a format change can alter recorded beside them."""

    def apply(self, values):
        scene = self.scene
        watched = _watched(scene, values)
        originals = {}
        # Restores run in reverse (Overrides, job_settings.restore_tags): the values of ``values`` first and
        # in their own order, so the file type is back before the modes and depths that depend on it.
        for path in watched + list(reversed(list(values))):
            try:
                value = get(scene, path)
            except (AttributeError, TypeError, ValueError):
                continue
            if isinstance(value, (str, bool, int, float)):
                originals[path] = value
        self.originals = originals
        self.hold(list(values))  # Records the tag; animation of a look's settings is muted until restore.
        for path, value in values.items():
            if get(scene, path) != value:
                put(scene, path, value)

    def restore(self):
        """Put every recorded value back; return the paths Blender would not take back (none, normally)."""
        originals, left = dict(self.originals), []
        try:
            for _pass in range(2):  # A file type applies its own defaults when it is set: its values go second.
                for path, value in reversed(list(originals.items())):
                    try:
                        if get(self.scene, path) != value:
                            put(self.scene, path, value)
                    except (AttributeError, TypeError, ValueError):
                        pass
            for path, value in originals.items():
                try:
                    if get(self.scene, path) != value:
                        left.append(path)
                except (AttributeError, TypeError, ValueError):
                    left.append(path)
            left += self.release()
            if TAG in self.scene:
                del self.scene[TAG]
        except ReferenceError:
            pass  # The scene was removed or its file closed; nothing is left to restore.
        self.originals = {}
        return left


class ResultSlot:
    """Looks render into their own slot of the Render Result image and put the person's slot back."""

    def __init__(self):
        self.existed, self.active = False, None

    @staticmethod
    def _image():
        return next((image for image in bpy.data.images if image.type == "RENDER_RESULT"), None)

    def enter(self):
        image = self._image()
        self.existed = image is not None
        if image is None:
            return  # Blender makes the image for this render; leave() removes it again.
        slots = image.render_slots
        self.active = slots.active_index
        index = next((index for index, slot in enumerate(slots) if slot.name == SLOT), None)
        if index is None:
            slots.new(name=SLOT)
            index = len(slots) - 1
        if index != self.active:
            slots.active_index = index

    def leave(self):
        try:
            image = self._image()
            if image is None:
                return
            if self.existed:
                if self.active is not None and image.render_slots.active_index != self.active:
                    image.render_slots.active_index = self.active
            elif not image.users and not any(
                    getattr(area.spaces.active, "image", None) == image
                    for window in bpy.context.window_manager.windows for area in window.screen.areas):
                bpy.data.images.remove(image)
        except (ReferenceError, RuntimeError, AttributeError):
            pass  # The document closed under the job.
