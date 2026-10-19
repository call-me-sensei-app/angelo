# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Effective render-layer sampling for production jobs and bounded looks.

Cycles USE replaces the scene count, BOUNDED takes the smaller nonzero count,
and IGNORE uses the scene count. EEVEE has no policy: a nonzero layer count
replaces the scene's, as USE does (``eevee_sampling.cc``). Zero layer samples
always inherit the scene. The look preserves lower layer counts and the
person's IGNORE policy, caps every rendered layer, and enables both denoising
switches. Returned setting paths use the existing scene restore tag, including
recovery after file load.

``settings`` writes the sampling policy and every rendered layer's count, even
those it leaves unchanged, so the job holds them all: animation of any of them
is muted for the job (``job_animated_settings``), and what renders on every
frame is what admission measured. ``reader`` gives admission the greatest value
an animated setting takes on the job's frames when the job does not set it.
"""

from . import job_animated_settings as animated
from .job_settings import get, scene_value

SAMPLES = {"CYCLES": "cycles.samples", "BLENDER_EEVEE": "eevee.taa_render_samples",
           "BLENDER_EEVEE_NEXT": "eevee.taa_render_samples"}
POLICY = "cycles.use_layer_samples"


def layers(window):
    scene = window.scene
    if getattr(scene.render, "use_single_layer", False):
        return [window.view_layer]
    return [layer for layer in scene.view_layers if layer.use]


def _mode(scene, read):
    """The layer-sample policy of the scene's engine: Cycles' own, USE for EEVEE, else IGNORE."""
    engine = read("render.engine")
    if engine == "CYCLES":
        return read(POLICY) if hasattr(scene.cycles, "use_layer_samples") else "IGNORE"
    return "USE" if engine in SAMPLES else "IGNORE"


def reader(window, frames=None):
    """``read(path)``: the person's own value, or with ``frames`` its greatest value on them when animated.

    Raises ``job_animated_settings.AnimationUnbounded`` when an animated value
    cannot be known in advance, and for an animated sampling policy, whose
    enumeration has no greatest value.
    """
    scene = window.scene

    def read(path):
        value = scene_value(scene, path)
        if frames is None or isinstance(value, str):
            if frames is not None and animated.channels(scene, (path,)):
                raise animated.AnimationUnbounded(
                    "The scene's %s is animated, so a job cannot know it on every frame before rendering. Give "
                    "samples in the request; the job then holds the sampling for every frame" % path,
                    code="UNSUPPORTED")
            return value
        return animated.bound(scene, path, value, frames)
    return read


def counts(window, scene_count, *, requested=None, preserve_inherited=False, read=None):
    """``[(layer, effective samples)]`` for the actual UI render selection.

    Explicit production samples replace the scene count and cap nonzero layer
    overrides. A look additionally preserves a lower original inherited count.
    Original values come from the restore tag when another job is running;
    ``read`` (``reader``) bounds the values the job will not hold.
    """
    scene = window.scene
    read = read or reader(window)
    count = scene_count if requested is None else requested
    mode = _mode(scene, read)
    result = []
    for layer in layers(window):
        effective = count
        if mode != "IGNORE":
            own = read(layer.path_from_id() + ".samples")
            effective = own or (scene_count if preserve_inherited else count)
            if mode == "BOUNDED" or requested is not None:
                effective = min(effective, count)
        result.append((layer, effective))
    return result


def scene_samples(window):
    """Greatest effective layer count of the scene's engine before a look changes settings."""
    path = SAMPLES.get(scene_value(window.scene, "render.engine"))
    if path is None:
        return None
    count = scene_value(window.scene, path)
    return max((value for _layer, value in counts(window, count)), default=count)


def settings(window, cap, *, look=False):
    """Temporary layer settings beside an explicit scene sample count ``cap``.

    Denoising belongs to the look policy only; production retains the artist's
    switches. RNA owns name escaping through each layer's path_from_id(). Every
    rendered layer's count and the Cycles policy are written so the job holds
    them; EEVEE, which has no Bounded policy, gets each nonzero count capped.
    """
    scene = window.scene
    values = {"cycles.use_denoising": True} if look and scene.render.engine == "CYCLES" else {}
    mode = _mode(scene, lambda path: get(scene, path))
    if scene.render.engine == "CYCLES" and hasattr(scene.cycles, "use_layer_samples"):
        values[POLICY] = "BOUNDED" if mode == "USE" else mode
    inherited = get(scene, SAMPLES[scene.render.engine]) if scene.render.engine in SAMPLES else cap
    for layer in layers(window):
        if mode != "IGNORE":  # Ignored layer counts render nothing; the others are held, capped where needed.
            own = layer.samples
            # Raising the scene count to the cap must not raise a layer that inherits a lower count with it.
            if look and mode == "USE" and not own and inherited < cap:
                own = inherited
            elif scene.render.engine != "CYCLES" and own:
                own = min(own, cap)
            values[layer.path_from_id() + ".samples"] = own
        if look and scene.render.engine == "CYCLES" and hasattr(layer, "cycles"):
            values[layer.path_from_id() + ".cycles.use_denoising"] = True
    return values
