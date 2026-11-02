# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a texture-set bake will do for each target, decided before it starts: channels, bakes, tiles, constants.

Contract: doc/angelo/long_jobs.rst, "Texture sets". A target's channels come
from its materials (or, high to low, the sources' materials), read without
changing anything (``bake_maps_graph.analyse``):

* ``maps`` named in the request are the channels; without it every channel the
  materials use: base colour, metallic and roughness always; emission and alpha
  when they are not black or opaque everywhere; normal when a BSDF's Normal input
  is linked, a bump displacement is set, or the bake is from sources; height when
  a Displacement node is on the output, or from sources (the distance from the
  target's surface to theirs); ambient occlusion when the packing has a slot for
  it or the bake is from sources.
* A channel that folds to one constant on every material is filled, not baked
  (``Step``s are only the rest), so a plain material costs only its varying maps.
* A target whose UVs lie outside the first UDIM tile bakes every tile its faces
  lie in, one file per tile.

Main thread only (reads Blender data). Model-facing refusals are English.
"""

from dataclasses import dataclass, field

from . import bake_maps_graph as graph
from . import bake_maps_pack as pack
from . import job_bake_targets as targets
from .bake_maps_emit import PACK
from .bake_maps_spec import HIGH_POSITION, LOW_NORMAL, LOW_POSITION  # noqa: F401  (named here too)
from .bake_maps_spec import AO_SAMPLES, DATA_SAMPLES, UNUSED_WHEN, slot_channels
from .job_spec import JobRequestError

# The sums a channel is made from (bake_maps_graph.SUMS: alpha from transparency; colours divided by the weight).
SUMS_OF = {"BASE_COLOR": ("BASE_COLOR", "WEIGHT"), "METALLIC": ("METALLIC", "WEIGHT"),
           "ROUGHNESS": ("ROUGHNESS", "WEIGHT"), "EMISSION": ("EMISSION",), "ALPHA": ("TRANSPARENCY",)}
# A high-to-low height: where the sources' surface is, where the target's is, and the target's normal there.
GEOMETRY = {HIGH_POSITION: ("GEOMETRY", "Position"), LOW_POSITION: ("GEOMETRY", "Position"),
            LOW_NORMAL: ("GEOMETRY", "Normal")}
MAX_TILES = 100
COLOURS = frozenset({"BASE_COLOR", "EMISSION"})   # Three components each: a bake of their own.


def ao_settings(scene, samples, data_samples):
    """The AO node's distance (the world's AO distance, as an AO bake reads it) and samples per bake sample."""
    world = scene.world
    distance = world.light_settings.distance if world is not None else 10.0
    return float(distance), max(1, -(-(samples or AO_SAMPLES) // data_samples))


@dataclass
class Step:
    """One Cycles bake: its type, what the materials emit (EMIT), and whether it reads the sources."""
    key: object               # The sum, map or geometry it fills; a tuple of the sums a packed bake holds.
    bake_type: str            # EMIT, NORMAL or AO.
    from_sources: bool
    samples: int
    emits: object = None      # bake_maps_graph.emit's channel for EMIT steps.


@dataclass
class TargetPlan:
    target: object
    sources: list
    cage: object
    channels: list            # The channels the set holds (baked or filled).
    steps: list
    constants: dict           # {sum: RGB tuple} folded without a bake.
    tiles: list               # UDIM tile numbers, or [] for an ordinary image.
    facts: dict = field(default_factory=dict)

    @property
    def graph_materials(self):
        """The materials whose shading a bake reads: the sources' when there are sources."""
        return materials_of(self.sources) if self.sources else materials_of([self.target])

    def step_materials(self, step):
        return self.graph_materials if step.from_sources else materials_of([self.target])


def materials_of(objects):
    found = []
    for obj in objects:
        for slot in obj.material_slots:
            if slot.material is not None and slot.material not in found:
                found.append(slot.material)
    return found


def _check_materials(target, sources):
    """Every material a job reads or points at an image must be this file's own, with nodes."""
    for obj in [target, *sources]:
        if not any(slot.material for slot in obj.material_slots):
            raise JobRequestError("%s has no material; give it one before baking its maps" % obj.name)
        for slot in obj.material_slots:
            material = slot.material
            if material is None:
                raise JobRequestError("%s has an empty material slot; fill or remove it before baking" % obj.name)
            if material.library is not None or material.node_tree is None or not material.is_editable:
                raise JobRequestError("The material %s is linked or has no nodes, so its maps can't be baked"
                                      % material.name)
    targets.TargetNodes(target, None).materials()


class _EveryTile:
    """Stands in for an image with every UDIM tile, so ``covered_tiles`` lists all the tiles the faces lie in."""
    tiles = [type("Tile", (), {"number": number})() for number in range(1001, 2001)]


def uv_tiles(target):
    """The UDIM tiles the target's faces lie in; [] when they all lie in the first one (an ordinary image)."""
    tiles = targets.covered_tiles(target, _EveryTile, explicit=True)
    if not tiles:
        raise JobRequestError("None of %s's faces lie in a UV tile (its active UV map); move its UVs into 0 to 1"
                              % target.name)
    if len(tiles) > MAX_TILES:
        raise JobRequestError("%s's UVs span %d UDIM tiles; a bake does at most %d" % (target.name, len(tiles),
                                                                                       MAX_TILES))
    return [] if tiles == [1001] else tiles


def _folded(values):
    """The one constant ``values`` (one per material) share as an RGB tuple, else None."""
    if any(not isinstance(value, graph.Const) for value in values):
        return None
    return pack.constant_of([value.rgb for value in values])


def plan(target, sources, cage, request):
    """The ``TargetPlan`` of one target (raises JobRequestError for what the bake could not do)."""
    _check_materials(target, sources)
    readings = [graph.analyse(material) for material in materials_of(sources or [target])]
    values = {key: [reading["values"][key] for reading in readings] for key in readings[0]["values"]}
    folded = {key: _folded(found) for key, found in values.items() if key != "HEIGHT"}
    requested = request.get("maps")
    high_to_low = bool(sources)
    if requested is not None:
        channels = list(requested)
    else:
        channels = ["BASE_COLOR", "METALLIC", "ROUGHNESS"]
        if folded["EMISSION"] != (UNUSED_WHEN["EMISSION"],) * 3:
            channels.append("EMISSION")
        if folded["TRANSPARENCY"] != (0.0, 0.0, 0.0):
            channels.append("ALPHA")
        if high_to_low or any(reading["normal"] for reading in readings):
            channels.append("NORMAL")
        if high_to_low or any(value is not None for value in values["HEIGHT"]):
            channels.append("HEIGHT")
        if high_to_low or "AO" in slot_channels(request["packing"]):
            channels.append("AO")
        if request["packing"] == "SEPARATE":   # A separate map of a channel's unused value says nothing.
            channels = [channel for channel in channels if channel != "METALLIC"
                        or folded["METALLIC"] != (UNUSED_WHEN["METALLIC"],) * 3]
    steps, constants = [], {}
    samples = request.get("samples")
    data_samples = DATA_SAMPLES if samples is None else min(samples, DATA_SAMPLES * 2)

    def emit_step(key, emits=None, from_sources=high_to_low):
        steps.append(Step(key, "EMIT", from_sources, data_samples, emits if emits is not None else key))

    sums = []
    for channel in channels:
        for key in SUMS_OF.get(channel, ()):
            if key not in sums:
                sums.append(key)
    scalars = []   # Packed three to a bake (bake_maps_emit.PACK): every bake is a Cycles session of its own.
    for key in sums:
        if folded.get(key) is not None:
            constants[key] = folded[key]
        elif key in COLOURS:
            emit_step(key)
        else:
            scalars.append(key)
    if "AO" in channels:   # Cycles' AO node in a packed bake: as many AO rays a texel as an AO bake's samples.
        scalars.append("AO")
    facts = {"approximated": sorted({name for reading in readings for name in reading["approximated"]})}
    if "HEIGHT" in channels:
        if high_to_low:
            emit_step(HIGH_POSITION, GEOMETRY[HIGH_POSITION], True)
            emit_step(LOW_POSITION, GEOMETRY[LOW_POSITION], False)
            emit_step(LOW_NORMAL, GEOMETRY[LOW_NORMAL], False)
        elif all(value is None for value in values["HEIGHT"]):
            channels.remove("HEIGHT")
            facts["no_height"] = True
        elif _folded(values["HEIGHT"]) is not None and None not in values["HEIGHT"]:
            constants["HEIGHT"] = _folded(values["HEIGHT"])
        else:
            scalars.append("HEIGHT")
    for start in range(0, len(scalars), 3):
        keys = tuple(scalars[start:start + 3])
        emit_step(keys, (PACK, keys + (None,) * (3 - len(keys))))
    if "NORMAL" in channels:
        steps.append(Step("NORMAL", "NORMAL", high_to_low, data_samples))
    if any(reading["displacement"] == "vector" for reading in readings):
        facts["vector_displacement"] = True
    return TargetPlan(target, list(sources), cage, channels, steps, constants, uv_tiles(target), facts)
