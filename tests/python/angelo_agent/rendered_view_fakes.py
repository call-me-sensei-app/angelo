# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in Blender for the host tests of ``view_render``: settings, a render job and the job manager.

Only what a RENDERED look touches is modelled, and the parts that matter are
modelled as Blender behaves: settings structs answer RNA-style introspection,
and the image format changes its dependent values when its type changes (a
32-bit depth does not survive PNG, a video type has its own file format), which
is what an exact restore has to undo. The render job is driven by the test:
``Blender.progress`` is what ``_bpy._angelo_jobs.progress`` returns and
``Blender.write`` is Blender writing the still, at the size its settings give.

``picture_fakes`` stands in for ``imbuf``, so the pixels a model gets are the
real ``view_picture`` code's.
"""

import contextlib
import copy
import itertools
from pathlib import Path
from types import ModuleType, SimpleNamespace

from picture_fakes import imbuf_module, png_bytes


class FakeID:
    """bpy.types.ID: a pointer to one is another data-block, never a nested setting."""


def flatten(value):
    """An array, vector or matrix as its flat values, as ``foreach_get`` writes them; a scalar alone."""
    if isinstance(value, str) or not hasattr(value, "__iter__"):
        return [value]
    return [item for row in value for item in flatten(row)]


def msgbus_listing(entries):
    """``_angelo_jobs.msgbus_subscribers`` over ``entries`` (``(struct, property or None, function)``), matching
    a written setting as Blender publishes it: that property, any property of its struct, or the struct."""
    def listing(keys):
        keys = list(keys)
        return [(struct, prop, function) for struct, prop, function in entries
                if any(struct == want and (wanted is None or prop is None or prop == wanted) for want, wanted in keys)]
    return listing


class Collection(list):
    """A bpy collection: ``foreach_get`` reads one property of every item into a flat buffer, ``keys`` names them."""

    def foreach_get(self, name, buffer):
        values = [item for element in self for item in flatten(getattr(element, name))]
        if len(values) != len(buffer):
            raise RuntimeError("foreach_get: a buffer of %d items for %d values" % (len(buffer), len(values)))
        buffer[:] = type(buffer)(buffer.typecode, values) if hasattr(buffer, "typecode") else values  # Or numpy.

    def keys(self):
        return [element.name for element in self]


class Struct:
    """Settings with RNA-style introspection; nested ``Struct`` values are pointer properties."""

    def __init__(self, _rna_type=None, **values):
        object.__setattr__(self, "values", dict(values))
        object.__setattr__(self, "readonly", set())
        object.__setattr__(self, "rna_identifier", _rna_type)

    def __getattr__(self, name):
        try:
            return object.__getattribute__(self, "values")[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        if name not in self.values:
            raise AttributeError(name)
        self.assign(name, value)

    def assign(self, name, value):
        self.values[name] = value

    def rna_struct_identifier(self):
        """RNA names each struct type once; a stand-in without a type is named by its settings and their kinds."""
        return self.rna_identifier or "%s(%s)" % (type(self).__name__, ",".join(
            "%s:%s" % (name, type(value).__name__) for name, value in sorted(self.values.items())))

    @property
    def bl_rna(self):
        def kind(value):
            if value is None or isinstance(value, (Struct, FakeID)):
                return "POINTER"
            return "COLLECTION" if isinstance(value, list) else {bool: "BOOLEAN", int: "INT", float: "FLOAT", str: "STRING"}.get(type(value), "FLOAT")
        properties = [SimpleNamespace(identifier="rna_type", type="POINTER", is_readonly=True, is_array=False)]
        properties += [SimpleNamespace(identifier=name, type=kind(value), is_readonly=name in self.readonly or isinstance(value, Struct),
                                       is_array=isinstance(value, tuple), array_length=len(flatten(value)),
                                       is_runtime=False, fixed_type=SimpleNamespace(identifier=(
                                           value.rna_struct_identifier() if isinstance(value, Struct) else "ID")))
                       for name, value in self.values.items()]
        return SimpleNamespace(identifier=self.rna_struct_identifier(), properties=properties)

    def snapshot(self):
        return {name: value.snapshot() if isinstance(value, Struct) else copy.deepcopy(value)
                for name, value in self.values.items() if not isinstance(value, FakeID)}


class ImageSettings(Struct):
    """The image format as Blender keeps it consistent: a type change resets what the new type cannot hold."""

    DEPTHS = {"PNG": ("8", "16"), "OPEN_EXR": ("16", "32"), "JPEG": ("8",), "FFMPEG": ("8",)}
    MEDIA = {"IMAGE": ("PNG", "OPEN_EXR", "JPEG"), "VIDEO": ("FFMPEG",)}

    def __init__(self, **values):
        for name, identifier in (("view_settings", "ColorManagedViewSettings"),
                                 ("display_settings", "ColorManagedDisplaySettings"),
                                 ("linear_colorspace_settings", "ColorManagedInputColorspaceSettings")):
            if isinstance(values.get(name), Struct):
                object.__setattr__(values[name], "rna_identifier", identifier)
        if isinstance(values.get("view_settings"), Struct):
            values["view_settings"].values.setdefault("curve_mapping", None)
        super().__init__(_rna_type="ImageFormatSettings", **values)

    def assign(self, name, value):
        values = self.values
        if name == "media_type":
            values[name] = value
            if values["file_format"] not in self.MEDIA[value]:
                self.assign("file_format", self.MEDIA[value][0])
            return
        if name == "file_format":
            if value not in self.MEDIA[values["media_type"]]:
                raise TypeError("enum %r not found for this media type" % value)
            values[name] = value
            if values["color_depth"] not in self.DEPTHS[value]:
                values["color_depth"] = self.DEPTHS[value][0]
            if value in ("JPEG", "FFMPEG") and values["color_mode"] == "RGBA":
                values["color_mode"] = "RGB"
            values["linear_colorspace_settings"].values["name"] = "Linear Rec.709" if value == "OPEN_EXR" else "sRGB"
            return
        if name == "color_depth" and value not in self.DEPTHS[values["file_format"]]:
            raise TypeError("enum %r not found for this file format" % value)
        values[name] = value


class Scene(dict):
    """A scene: custom properties as a mapping, settings as attributes (never compared as a dict)."""

    __hash__ = object.__hash__

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other

    def __init__(self, engine="CYCLES", image=None):
        super().__init__()
        self.name, self.session_uid, self.frame_current, self.frame_start, self.frame_end = "Scene", 7, 12, 1, 250
        self.frame_step = 1   # Blender's default; an animation job renders on it (VFX audit A2).
        self.camera, self.compositing_node_group = SimpleNamespace(name="Camera", type="CAMERA"), None
        image = image or dict(media_type="IMAGE", file_format="OPEN_EXR", color_mode="RGBA", color_depth="32")
        self.render = Struct(
            "RenderSettings",
            engine=engine, resolution_x=1920, resolution_y=1080, resolution_percentage=50, filepath="//shots/final_",
            use_file_extension=True, use_render_cache=True, use_auto_generate_texture_cache=True,
            use_multiview=False, use_compositing=True, use_lock_interface=False, pixel_aspect_x=1.0,
            use_border=False, use_crop_to_border=False, border_min_x=0.0, border_min_y=0.0, border_max_x=1.0,
            border_max_y=1.0,
            image_settings=ImageSettings(
                exr_codec="DWAA", compression=90, quality=75, color_management="FOLLOW_SCENE",
                view_settings=Struct(view_transform="Standard", look="None", exposure=-0.5, gamma=1.0,
                                     use_curve_mapping=False),
                display_settings=Struct(display_device="sRGB"),
                linear_colorspace_settings=Struct(name="Linear Rec.709"),
                unrelated_block=FakeID(), **image),
            ffmpeg=Struct(codec="H264", format="MPEG4", constant_rate_factor="HIGH"))
        self.render.image_settings.linear_colorspace_settings.values["name"] = (
            "Linear Rec.709" if image["file_format"] == "OPEN_EXR" else "sRGB")
        self.cycles = Struct(samples=4096, use_denoising=False, device="GPU", time_limit=0.0)
        self.eevee = Struct(taa_render_samples=64)
        self.view_settings = Struct(view_transform="AgX", look="None", exposure=0.0, gamma=1.0, use_curve_mapping=False)
        self.display_settings = Struct(display_device="sRGB")
        layer = Struct(name="ViewLayer", use=True, use_pass_combined=True, use_pass_z=False, samples=0)
        layer.values["path_from_id"] = lambda: 'view_layers["ViewLayer"]'
        self.view_layers = [layer]
        self.pixels = (120, 130, 140, 255)  # What Blender renders: one RGBA colour, bottom row first like any other.

    def path_resolve(self, path):
        """RNA paths to a view layer by name, as ``ViewLayer.path_from_id`` writes them."""
        return next(layer for layer in self.view_layers if path == 'view_layers["%s"]' % layer.name)

    def snapshot(self):
        return {"custom": dict(self), "frame": self.frame_current, "camera": self.camera,
                "render": self.render.snapshot(), "cycles": self.cycles.snapshot(), "eevee": self.eevee.snapshot(),
                "view": self.view_settings.snapshot(), "display": self.display_settings.snapshot()}


class Slot:
    def __init__(self, name):
        self.name = name


class Slots(list):
    active_index = 0

    def new(self, name=""):
        self.append(Slot(name))
        return self[-1]


class Image:
    """bpy.types.Image: the Render Result, or a file Blender loaded."""

    uids = itertools.count(1000)

    def __init__(self, blender, kind="IMAGE", size=(1920, 1080), slots=None, colour=None, has_pixels=True):
        self.blender, self.type, self.size, self.users = blender, kind, size, 0
        self.name = "Render Result" if kind == "RENDER_RESULT" else "Image"
        self.session_uid = next(Image.uids)
        self.render_slots, self.colour, self.has_pixels = slots, colour, has_pixels

    def save_render(self, filepath, scene=None):
        self.blender.save_render(self, filepath, scene)


class Blender:
    """The stand-in application: fake ``bpy``/``_bpy`` modules and one render job the test drives."""

    def __init__(self, scene=None, render_result=True):
        self.scene = Scene() if scene is None else scene  # An empty mapping is still a scene.
        self.progress = None        # What _angelo_jobs.progress answers: None once no render job runs.
        self.invoked, self.stops, self.during = [], 0, None
        self.blender_jobs = {}      # bpy.app.is_job_running answers for the person's own jobs.
        self.other_jobs = {}        # _angelo_jobs.progress answers for other job types.
        self.start_result = {"RUNNING_MODAL"}
        result = Image(self, "RENDER_RESULT", slots=Slots(Slot("Slot %d" % n) for n in range(1, 9)))
        self.images = [result] if render_result else []
        self.files = {}  # path -> what bpy.data.images.load makes of it (``Image`` keywords).
        self.msgbus = []  # bpy.msgbus subscribers: (struct, property or None, function).
        self.copies = []  # What _angelo_picture.save_copy saved (picture_helper): image, settings, display.
        self.preferences = SimpleNamespace(
            view=SimpleNamespace(render_display_type="WINDOW"), is_dirty=False,
            bl_rna=SimpleNamespace(properties={"is_dirty": SimpleNamespace(is_readonly=False)}))
        self.window = SimpleNamespace(scene=self.scene, view_layer=SimpleNamespace(update=lambda: None),
                                      screen=SimpleNamespace(areas=[]), modal_operators=[])
        self.bpy, self.native, self.imbuf = self._bpy(), self._native(), imbuf_module()
        self.saved = []  # (image, settings snapshot) of each Image.save_render.

    def _render(self, mode, **options):
        """bpy.ops.render.render: record what the scene and preferences were while Blender started the job."""
        scene = self.scene
        self.invoked.append(dict(options, mode=mode))
        self.during = {"scene": scene.snapshot(), "display": self.preferences.view.render_display_type,
                       "slot": self.images[0].render_slots.active_index if self.images else None,
                       "slots": [slot.name for slot in self.images[0].render_slots] if self.images else None}
        if "RUNNING_MODAL" in self.start_result:
            self.progress = 0.0
            if not self.images:  # Blender makes the Render Result image for a file that had none.
                self.images.append(Image(self, "RENDER_RESULT", slots=Slots([Slot("Slot 1")])))
        return set(self.start_result)

    def picture_size(self):
        """The size of what Blender writes with the scene's settings as they are: the frame, or its cropped region."""
        render = self.scene.render
        width = render.resolution_x * render.resolution_percentage // 100
        height = render.resolution_y * render.resolution_percentage // 100
        if render.use_border and render.use_crop_to_border:
            width = round(render.border_max_x * width) - round(render.border_min_x * width)
            height = round(render.border_max_y * height) - round(render.border_min_y * height)
        return width, height

    def write(self, data=None, path=None):
        """Blender writes the still where the job's settings say (also for a stopped render)."""
        import numpy
        if data is None:
            width, height = self.picture_size()
            mode = self.scene.render.image_settings.color_mode
            pixels = numpy.empty((height, width, 4), dtype=numpy.uint8)
            pixels[...] = self.scene.pixels
            data = png_bytes(pixels if mode == "RGBA" else pixels[..., :3])
        Path(path or self.scene.render.filepath).write_bytes(data)

    def save_render(self, image, filepath, scene=None):
        """Image.save_render: Blender writes ``image`` with the scene's image format as it is now."""
        if not getattr(image, "has_pixels", True):
            raise RuntimeError("Image does not have any image data")
        self.saved.append((image, self.scene.snapshot()))
        width, height = getattr(image, "size", (1920, 1080))
        import numpy
        pixels = numpy.empty((height, width, 4), dtype=numpy.uint8)
        pixels[...] = getattr(image, "colour", None) or self.scene.pixels
        self.write(png_bytes(pixels), filepath)

    def save_copy(self, scene, filepath, *, image=None, source=None, settings=None, display=False):
        """``_angelo_picture.save_copy``: Blender's save with an image format of its own; the scene is never
        written, and a ``source`` file is read into an image outside ``bpy.data``."""
        if source is not None:
            image = Image(self, **self._file(source))
        before = self.scene.snapshot()
        self.copies.append({"image": image, "settings": dict(settings or {}), "display": display})
        self.save_picture(image, filepath, dict(settings or {}))
        assert self.scene.snapshot() == before, "save_copy wrote the scene"
        return {"multilayer": getattr(image, "type", "") == "MULTILAYER"}

    def save_picture(self, image, filepath, settings):
        """What ``save_copy`` writes: a PNG of the image's colour (tests replace it for other formats)."""
        if not getattr(image, "has_pixels", True):
            raise RuntimeError("Image '%s' does not have any image data" % getattr(image, "name", "Image"))
        width, height = getattr(image, "size", (1920, 1080))
        import numpy
        pixels = numpy.empty((height, width, 4), dtype=numpy.uint8)
        pixels[...] = getattr(image, "colour", None) or self.scene.pixels
        self.write(png_bytes(pixels), filepath)

    def picture_helper(self):
        """Model a build with ``_bpy._angelo_picture``."""
        self.native._angelo_picture = SimpleNamespace(save_copy=self.save_copy)
        return self

    def _file(self, filepath):
        """What Blender makes of a file: the planted ``files`` entry, found by path or by its bytes."""
        properties = self.files.get(str(filepath))
        if properties is None:  # A stable private source copy carries the same planted bytes.
            data = Path(filepath).read_bytes()
            properties = next((value for name, value in self.files.items()
                               if Path(name).is_file() and Path(name).read_bytes() == data), {})
        return properties

    def end(self, written=True):
        if written:
            self.write()
        self.progress = None

    def _bpy(self):
        bpy = ModuleType("bpy")
        bpy.types = SimpleNamespace(ID=FakeID)
        bpy.app = SimpleNamespace(is_job_running=lambda kind: bool(self.blender_jobs.get(kind)))
        bpy.data = SimpleNamespace(images=_Images(self))
        bpy.context = SimpleNamespace(temp_override=lambda **_: contextlib.nullcontext(), preferences=self.preferences,
                                      window_manager=SimpleNamespace(windows=[self.window]))
        bpy.ops = SimpleNamespace(render=SimpleNamespace(render=self._render))
        return bpy

    def _native(self):
        module = ModuleType("_bpy")

        def progress(scene, kind):
            return self.progress if kind == "RENDER" else self.other_jobs.get(kind)

        def stop(scene, kind):
            self.stops += 1
        module._angelo_jobs = SimpleNamespace(progress=progress, stop=stop, notify=lambda: None,
                                              msgbus_subscribers=msgbus_listing(self.msgbus))
        return module


class _Images:
    """bpy.data.images over the stand-in's list."""

    def __init__(self, blender):
        self.blender = blender

    def __iter__(self):
        return iter(self.blender.images)

    def __len__(self):
        return len(self.blender.images)

    def foreach_get(self, name, buffer):
        Collection(self.blender.images).foreach_get(name, buffer)

    def keys(self):
        return Collection(self.blender.images).keys()

    def remove(self, image):
        self.blender.images.remove(image)

    def load(self, filepath, check_existing=False):
        image = Image(self.blender, **self.blender._file(filepath))
        self.blender.images.append(image)
        return image
