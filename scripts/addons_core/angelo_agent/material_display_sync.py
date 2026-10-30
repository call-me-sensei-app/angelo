# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""When Angelo writes a material's Solid-view values from its node result (``material_display.derive``).

Solid view draws ``Material.diffuse_color`` (RGB and alpha), ``.metallic`` and ``.roughness``, never the node graph.
Every Angelo path that creates, rewrites or brings in a material calls ``sync`` on it, in the same step as the
material change (on the staged copy a typed program commits, inside a script's own Undo step, before an import's
Undo push), so Undo, a refused commit and a rolled-back stage take the display values with the material. The rule,
by what the operation did to the material:

* ``CREATED`` (``material_principled`` and ``shader_nodes`` making a new material): every value the graph decides.
* ``EDITED`` (``material_principled`` or ``shader_nodes`` on a held material, ``node_edit``, a placement's material
  variant, a reviewed script's edit): only a value whose node result the edit changed. Every other display value
  stays, a person's own included, so an edit of one input changes only what that input shows (a Roughness edit moves
  ``roughness``, never the colour; an IOR edit moves nothing), as every typed edit keeps what it does not state.
* ``ADDED`` (imports and appends: team library files, generated models, base meshes, Media Studio pictures, a
  reviewed script's new material): only values still at Blender's default; what the file or its importer set stays.

Never written: a material the operation did not create, rewrite or bring in; a linked library's material; a value
the graph leaves unknown; a value a reviewed script itself changed (``ScriptPass``). ``property_set`` of a display
value is the person's or agent's explicit choice and is never followed by a sync.

Images are read only where it is cheap and safe (``image_mean``): at most ``MAX_IMAGE_PIXELS`` each and
``MAX_IMAGES_READ`` per call, each image once per call however many materials use it, and only an image already in
memory unless the caller is an import (``LOAD``). A typed program's stage holds no images
(``node_edit_commit._strip_images``), so its image textures are unknown. A reviewed script's pass reads no image:
it runs before and after every script, and reading images there cost 12.2 s before and 12.3 s after a script in a
file with 128 materials over eight 2048 x 2048 textures (each material read its images again), against 7.6 and 7.1
ms without (Blender 5.2, background, Mac 1, 2026-10-29). An image-driven value stays unknown there, so it is left as
it is.
"""

try:
    from .material_display import FIELDS, derive, identity
except ImportError:   # studio_assets_convert runs it as a plain sibling module, outside the add-on package.
    from material_display import FIELDS, derive, identity

CREATED, EDITED, ADDED = "CREATED", "EDITED", "ADDED"
DEFAULTS = {"color": (0.8, 0.8, 0.8), "alpha": 1.0, "metallic": 0.0, "roughness": 0.4}   # DNA_material_types.h
TOLERANCE = 1e-4
LOADED, LOAD = "LOADED", "LOAD"   # Images: only those in memory; or load them (imports).
MAX_IMAGE_PIXELS = 2048 * 2048
MAX_IMAGES_READ = 8
MAX_SCRIPT_MATERIALS = 128   # Materials a script's pass reads before it runs (those in use first).


def _same(a, b):
    if a is None or b is None:
        return a is b
    if isinstance(a, tuple):
        return len(a) == len(b) and all(abs(x - y) <= TOLERANCE for x, y in zip(a, b))
    return abs(a - b) <= TOLERANCE


def current(material):
    """The display values ``material`` holds now, keyed as ``material_display.FIELDS``."""
    colour = tuple(float(c) for c in material.diffuse_color)
    return {"color": colour[:3], "alpha": colour[3] if len(colour) > 3 else 1.0,
            "metallic": float(material.metallic), "roughness": float(material.roughness)}


def image_mean(image, load=False):
    """An image's mean scene-linear RGB, or None: no image, empty, above ``MAX_IMAGE_PIXELS``, not in memory (unless
    ``load``) or unreadable. A byte image in sRGB is linearized per pixel before the mean."""
    if image is None:
        return None
    try:
        width, height = image.size
        if width * height == 0 or width * height > MAX_IMAGE_PIXELS or not (image.has_data or load):
            return None
        import numpy
        channels = int(image.channels)
        pixels = numpy.empty(width * height * channels, dtype=numpy.float32)
        image.pixels.foreach_get(pixels)
        rgb = pixels.reshape(-1, channels)[:, :3] if channels >= 3 else numpy.repeat(
            pixels.reshape(-1, channels)[:, :1], 3, axis=1)
        if not image.is_float and getattr(image.colorspace_settings, "name", "") == "sRGB":
            rgb = numpy.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
        return tuple(float(c) for c in rgb.mean(axis=0))
    except (ReferenceError, RuntimeError, ValueError, TypeError, AttributeError, ImportError):
        return None


def _reader(images, read=None):
    """The ``image_colour`` callback for ``images`` (None, ``LOADED`` or ``LOAD``), bounded to ``MAX_IMAGES_READ``.
    ``read``: the means already read in this call, shared by every material it syncs."""
    if images is None:
        return None
    read = {} if read is None else read

    def colour(image):
        if image is None:
            return None
        key = identity(image)
        if key not in read:
            read[key] = image_mean(image, load=images == LOAD) if len(read) < MAX_IMAGES_READ else None
        return read[key]
    return colour


def derived(material, images=None, read=None):
    """What ``material``'s graph shows (``material_display.derive``); every value None without a node tree.
    ``read``: image means already read in this call (``_reader``)."""
    return derive(getattr(material, "node_tree", None), _reader(images, read))


def sync(material, mode, before=None, images=None, keep=(), after=None, read=None):
    """Write ``material``'s display values from its node result by ``mode``'s rule (module docstring); the fields
    written. ``before``: for ``EDITED``, ``derived`` read just before the edit. ``keep``: fields never written.
    ``after``: ``derived`` as it is now, when the caller already read it."""
    if material is None or getattr(material, "library", None) is not None:
        return []
    after, now = after or derived(material, images, read), current(material)
    before = before or dict.fromkeys(FIELDS)
    fields = []
    for field in FIELDS:
        if after[field] is None or field in keep or _same(after[field], now[field]):
            continue
        if (mode == CREATED or (mode == ADDED and _same(now[field], DEFAULTS[field]))
                or (mode == EDITED and not _same(after[field], before[field]))):
            fields.append(field)
    if "color" in fields or "alpha" in fields:
        colour = after["color"] if "color" in fields else now["color"]
        material.diffuse_color = tuple(colour) + (after["alpha"] if "alpha" in fields else now["alpha"],)
    if "metallic" in fields:
        material.metallic = after["metallic"]
    if "roughness" in fields:
        material.roughness = after["roughness"]
    return fields


def known():
    """The session UIDs of every material now: what ``sync_added`` compares with."""
    import bpy
    return {int(material.session_uid) for material in bpy.data.materials}


def sync_added(before, images=LOAD):
    """``ADDED`` for every material not in ``before`` (``known`` read before an import or append); the count synced."""
    import bpy
    changed, read = 0, {}   # One read per image for the whole import, however many materials use it.
    for material in bpy.data.materials:
        if int(material.session_uid) not in before and sync(material, ADDED, images=images, read=read):
            changed += 1
    return changed


def _materials(bpy):
    return getattr(getattr(bpy, "data", None), "materials", None) or ()


class ScriptPass:
    """A reviewed script's materials: read before it runs, synced after it succeeds, inside its own Undo step.

    A material the script made gets ``ADDED``. A material it held before is synced (``EDITED``) only when the script
    changed its node result, and never in a display value the script itself changed. Materials past
    ``MAX_SCRIPT_MATERIALS`` (those in use first) are not read before, so an edit to one is not followed. A material
    that cannot be read (freed, or a graph Blender refuses to report) is skipped: the display is never worth failing
    a script that succeeded.
    """

    SKIPPED = (ReferenceError, RuntimeError, AttributeError, TypeError, ValueError)

    def __init__(self, bpy):
        """Read ``bpy.data.materials`` (none when the module has no data: a stand-in, or no file)."""
        self.known, self.before = set(), {}
        try:
            materials = list(_materials(bpy))
            self.known = {int(m.session_uid) for m in materials}
            items = sorted((m for m in materials if getattr(m, "library", None) is None), key=lambda m: m.users == 0)
        except self.SKIPPED:
            items = []
        for material in items[:MAX_SCRIPT_MATERIALS]:
            try:
                self.before[int(material.session_uid)] = (current(material), derived(material))   # No images.
            except self.SKIPPED:
                continue

    def finish(self, bpy):
        """Sync ``bpy.data.materials`` after the script; the number of materials written."""
        changed = 0
        try:
            materials = list(_materials(bpy))
        except self.SKIPPED:
            return 0
        for material in materials:
            try:
                changed += bool(self._one(material))
            except self.SKIPPED:
                continue
        return changed

    def _one(self, material):
        if getattr(material, "library", None) is not None:
            return []
        uid = int(material.session_uid)
        if uid not in self.known:
            return sync(material, ADDED)
        if uid not in self.before:
            return []
        shown, result = self.before[uid]
        after = derived(material)
        if all(_same(result[field], after[field]) for field in FIELDS):
            return []   # Its node result is unchanged: the script did not change what it shows.
        now = current(material)
        keep = [field for field in FIELDS if not _same(now[field], shown[field])]   # The script's own values.
        return sync(material, EDITED, before=result, keep=keep, after=after)
