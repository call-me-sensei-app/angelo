# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One bake of a texture set on Blender's own bake job: its scratch image, the temporary nodes, and the pixels read.

Contract: doc/angelo/long_jobs.rst, "Texture sets". Each ``bake_maps_plan.Step``
bakes into a scratch float image of its own (one tile per UDIM tile the target
uses), named ``Angelo Bake <n>`` and never saved: the target's materials point at
it for the bake (``job_bake_targets.TargetNodes``), and an EMIT step adds the
channel's temporary graph to the materials it reads (``bake_maps_graph.emit``).
When the bake ends the pixels are read, one tile at a time (Python reaches only
an image's first tile, so a read tile is removed from the scratch image), and
everything the step added is removed. What a step changed is kept as plain data
in ``notes`` the runner records on the scene (``job_settings``), so a file saved
mid-bake is put back by ``restore_notes`` when it is next opened.

Every bake runs with the scene values in ``SCENE_VALUES`` (film exposure and
sample clamping would change an Emit bake's values), and a step counts only when
its image was written (``wrote``): Blender reports a bake it refused as complete.
Texels no face covers keep the channel's neutral value (``NEUTRAL``) instead of
the cleared black, so filtering and mipmaps never pull black into a map's edges.
Main thread only.
"""

import bpy
import numpy

from . import bake_maps_emit as graph
from . import job_bake_targets as targets

SCRATCH = "Angelo Bake"
NOTES_KEY = "bake_maps"
# The scene settings every texture-set bake runs with (restored after the job): Cycles, and no film exposure or
# sample clamping, which Cycles applies to an Emit bake's values as to a render's emission (pass.cpp: the emission
# pass uses exposure; clamp direct clips emission seen directly).
SCENE_VALUES = {"render.engine": "CYCLES", "cycles.film_exposure": 1.0, "cycles.sample_clamp_direct": 0.0,
                "cycles.sample_clamp_indirect": 0.0}
# Bytes one bake holds from its start until its scratch image is freed (``step_bytes``), measured as the process's
# peak footprint above the footprint before it (macOS phys_footprint; Cycles bakes of 512 to 4096 texels a side on
# CPU and Metal, one to six UDIM tiles; emit, packed, normal and from-sources bakes), with about a fifth of
# headroom: per texel of every tile, the float scratch image with Blender's bake pixel and result arrays (measured
# 62), more from sources (their hit pixels, measured 37); per texel of one tile, the Cycles session's buffers
# (measured at most 252 on CPU, 391 on Metal). Read-back runs once the session is freed, inside the same peak.
# Not counted: a GPU session's path states, a fixed block per session that Cycles sizes against the GPU memory it
# finds free (3.6 to 7.5 GB on a 128 GB Mac with Metal), which lives in the memory the budget leaves
# (``job_bake_maps.MEMORY_SHARE``) as it does for Blender's own bakes.
BAKE_IMAGE_BYTES, BAKE_SOURCE_BYTES = 72, 44
BAKE_SESSION_BYTES = {False: 300, True: 470}   # On CPU, on a GPU.
ARRAY_BYTES = 4   # A component of an array a bake keeps.
# Neutral values for texels outside every face: a flat tangent normal, no occlusion, opaque.
NEUTRAL = {"NORMAL": (0.5, 0.5, 1.0), "AO": (1.0, 1.0, 1.0), "TRANSPARENCY": (0.0, 0.0, 0.0),
           "WEIGHT": (1.0, 1.0, 1.0)}
GEOMETRY = "GEOMETRY"
# Components a step's key keeps.
WIDTHS = {"METALLIC": 1, "ROUGHNESS": 1, "TRANSPARENCY": 1, "WEIGHT": 1, "HEIGHT": 1, "AO": 1}


def new_notes():
    return {"graphs": [], "nodes": [], "images": []}


def restore_notes(notes):
    """Put back everything ``notes`` records (a step that ended, or one a saved file caught mid-bake)."""
    targets.restore(notes.get("nodes") or [])
    for record in reversed(notes.get("graphs") or []):
        try:
            graph.restore(record)
        except (AttributeError, KeyError, TypeError, ReferenceError, RuntimeError):
            pass   # The others are still put back.
    for name in notes.get("images") or []:
        image = bpy.data.images.get(name)
        if image is not None and image.library is None:
            bpy.data.images.remove(image)
    notes.update(new_notes())


def _kept(step):
    """Components a step's read-back keeps per texel."""
    if isinstance(step.emits, tuple) and step.emits[0] == GEOMETRY:
        return 4
    return 3 if isinstance(step.key, tuple) else WIDTHS.get(step.key, 3)


def step_bytes(plan, step, size, gpu=False):
    """Bytes one bake holds from its start until its scratch image is freed: Blender's bake (the image, its pixel
    arrays, the Cycles session on the CPU or a ``gpu``) and the arrays read back."""
    tile = size[0] * size[1]
    texels = tile * max(1, len(plan.tiles))
    image = BAKE_IMAGE_BYTES + (BAKE_SOURCE_BYTES if step.from_sources else 0) + ARRAY_BYTES * _kept(step)
    return texels * image + tile * BAKE_SESSION_BYTES[bool(gpu)]


def on_gpu(scene):
    """Whether the scene's Cycles bakes run on a GPU (its device and a GPU backend chosen in the preferences)."""
    if getattr(getattr(scene, "cycles", None), "device", "CPU") != 'GPU':
        return False
    addon = bpy.context.preferences.addons.get("cycles")
    return addon is not None and getattr(addon.preferences, "compute_device_type", "NONE") not in ("NONE", "")


def memory_needed(plans, size, image_format="PNG", gpu=False):
    """Bytes the largest target needs at its peak when nothing else runs, as the writing queue runs it alone: each
    bake beside the arrays its target's earlier bakes kept, then those arrays while they are made into channels
    (``bake_maps_write.prepare_bytes``), then the channels while the widest file is written."""
    from . import bake_maps_write as write
    from .bake_maps_spec import WIDTH, files
    peak = 0
    for plan in plans:
        texels = size[0] * size[1] * max(1, len(plan.tiles))
        held = 0
        for step in plan.steps:
            peak = max(peak, held + step_bytes(plan, step, size, gpu))
            held += texels * ARRAY_BYTES * _kept(step)
        channels = texels * ARRAY_BYTES * sum(WIDTH[channel] for channel in plan.channels)
        peak = max(peak, max(held, channels) + texels * write.PREPARE_TEXEL_BYTES)
        widest = max([len(slots) for _name, slots, _colour in files("SEPARATE", plan.channels)] + [4])
        peak = max(peak, channels + write.band_bytes(size[0], size[1], widest, image_format))
    return peak


def scratch_image(size, tiles):
    """A float, non-colour image for one bake: ``size`` pixels a side pair, with ``tiles`` (or none)."""
    width, height = size
    image = bpy.data.images.new(SCRATCH, width, height, alpha=True, float_buffer=True, is_data=True,
                                tiled=bool(tiles))
    if tiles:
        image.tiles[0].number = tiles[0]
        len(image.pixels)   # The first tile's buffer: Angelo fills further tiles only once it exists.
        with bpy.context.temp_override(edit_image=image):
            for number in tiles[1:]:
                bpy.ops.image.tile_add(number=number, count=1, fill=True, generated_type='BLANK', width=width,
                                       height=height, float=True, alpha=True, color=(0.0, 0.0, 0.0, 0.0))
        if [tile.number for tile in image.tiles] != list(tiles):
            name = image.name
            bpy.data.images.remove(image)
            raise RuntimeError("Angelo could not make the UDIM tiles of %s" % name)
    return image


class StepRun:
    """A step's Blender-side state from ``prepare`` to ``read`` and ``undo``."""

    def __init__(self, plan, step, size, notes, request_samples=None):
        self.plan, self.step, self.size, self.notes = plan, step, size, notes
        self.request_samples = request_samples   # The request's samples: AO's ray count when given.
        self.image, self.nodes = None, []

    def prepare(self):
        """Make the scratch image, point the target's materials at it and add the step's graph (recorded first)."""
        self.image = scratch_image(self.size, self.plan.tiles)
        self.notes["images"].append(self.image.name)
        node_set = targets.TargetNodes(self.plan.target, self.image)
        self.notes["nodes"] = node_set.records   # The same list: apply() appends as it goes.
        node_set.apply()
        self.nodes = list(node_set.records)
        if self.step.bake_type == "EMIT":
            from .bake_maps_plan import ao_settings
            scene = bpy.context.scene
            ao = ao_settings(scene, self.request_samples, self.step.samples)
            for material in self.plan.step_materials(self.step):
                record = graph.new_record(material)
                self.notes["graphs"].append(record)
                graph.emit(material, self.step.emits, record, ao)
        # Adding nodes may move a tree's active node: the bake reads the image node that is active.
        for record in self.nodes:
            nodes = bpy.data.materials[record["material"]].node_tree.nodes
            node = nodes.get(record["node"])
            nodes.active, node.select = node, True

    def operator_properties(self, request, margin):
        """``object.bake`` properties: everything the job's own, nothing read from the bake panel."""
        sources = self.step.from_sources
        properties = {"type": self.step.bake_type, "use_clear": True, "target": 'IMAGE_TEXTURES',
                      "save_mode": 'INTERNAL', "margin": margin, "margin_type": 'ADJACENT_FACES',
                      "use_selected_to_active": sources}
        if self.step.bake_type == "NORMAL":
            properties.update(normal_space='TANGENT', normal_r='POS_X', normal_g='POS_Y', normal_b='POS_Z')
        if sources:
            cage = self.plan.cage
            properties.update(use_cage=cage is not None, cage_object=cage.name if cage else "",
                              cage_extrusion=request["extrusion"], max_ray_distance=request["ray_distance"])
        return properties

    def objects(self):
        target = self.plan.target
        return [*self.plan.sources, target] if self.step.from_sources else [target]

    def wrote(self):
        """Whether the bake wrote the scratch image. Blender reports a bake it refused inside its job (an object it
        could not bake, a device that failed) as complete too; only a written image marks itself changed."""
        try:
            return bool(self.image.is_dirty)
        except (AttributeError, ReferenceError):
            return False

    def read(self):
        """``{key: [(tile, array)]}``: each tile's pixels per sum the bake held, ``(h, w, k)`` float32, uncovered
        texels neutral. A packed bake gives each of its keys one component."""
        key, image = self.step.key, self.image
        # A position or normal for a high-to-low height keeps its coverage (alpha) and is never filled.
        geometry = isinstance(self.step.emits, tuple) and self.step.emits[0] == "GEOMETRY"
        packed = isinstance(key, tuple)
        width = 4 if geometry else 3 if packed else WIDTHS.get(key, 3)
        if packed:
            neutral = numpy.asarray([NEUTRAL.get(part, (0.0,))[0] for part in key] + [0.0] * (3 - len(key)),
                                    dtype=numpy.float32)
        else:
            neutral = numpy.asarray(NEUTRAL.get(key, (0.0, 0.0, 0.0))[:min(width, 3)], dtype=numpy.float32)
        tiles = []
        while True:
            number = image.tiles[0].number if self.plan.tiles else None
            height, row = image.size[1], image.size[0]
            pixels = numpy.empty(len(image.pixels), dtype=numpy.float32)
            image.pixels.foreach_get(pixels)
            pixels = pixels.reshape(height, row, 4)
            values = pixels[..., :width].copy()
            if not geometry:
                values[pixels[..., 3] < 0.5] = neutral
            tiles.append((number, values))
            if not self.plan.tiles or len(image.tiles) == 1:
                break
            image.tiles.remove(image.tiles[0])   # The next tile becomes the first, which Python reads.
        if packed:
            return {part: [(number, values[..., index:index + 1]) for number, values in tiles]
                    for index, part in enumerate(key)}
        return {key: tiles}

    def undo(self):
        """Remove what ``prepare`` added: the graphs, the image nodes, the scratch image."""
        restore_notes(self.notes)
        self.image, self.nodes = None, []
