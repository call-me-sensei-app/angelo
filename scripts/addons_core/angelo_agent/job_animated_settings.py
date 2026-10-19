# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scene animation that would replace a job's own settings: held still for the job, and bounded in advance.

A job sets its resolution, samples and other settings on the scene for its own
run (``job_settings.Overrides``). A setting that is animated (keyframes of the
scene's Action, an NLA strip's Action) or driven is set again by Blender each
time it evaluates the scene's animation, which a render does for every frame:
a requested 8 samples would render the keyed 64 and 128, and a look's 32-sample
cap would not hold. Two things follow:

* ``hold`` mutes, for the job, the F-Curves on the scene that write a setting
  the job records (matched by the exact RNA path from the scene, as the job's
  settings are), and ``release`` unmutes the ones it muted. The muted channels
  are recorded in the job's restore tag, so a file saved while the job ran gets
  them back when it is opened. A channel that was already muted is left alone.
* ``bound`` gives the greatest value an animated setting the job does not set
  takes on the job's frames, so admission bounds what really renders. Only
  keyframes of the scene's own Action with the Replace blend can be evaluated
  in advance; a driver, an NLA strip or another blend refuses
  (``AnimationUnbounded``) and the refusal asks for an explicit value, which
  the job then holds.

Paths are those of the scene ID only; other data-blocks' animation is not a
job setting. Model-facing English; no ``bpy`` import; main thread only.
"""

from .job_spec import JobRequestError

HELD = "held_channels"  # Restore-tag extras key: the channels a job muted.


class AnimationUnbounded(JobRequestError):
    """An animated setting whose values on the job's frames cannot be known before it renders."""


def _channelbag(action, slot):
    if action is None or slot is None:
        return None
    for layer in action.layers:
        for strip in layer.strips:
            bag = strip.channelbag(slot)
            if bag is not None:
                return bag
    return None


def _nla_strips(strips):
    for strip in strips:
        yield strip
        yield from _nla_strips(getattr(strip, "strips", ()))  # A meta strip holds others.


def _sources(scene):
    """``(where, F-Curves)`` for every set of channels that animates ``scene`` itself."""
    animation = getattr(scene, "animation_data", None)
    if animation is None:
        return
    yield {"source": "drivers"}, animation.drivers
    bag = _channelbag(animation.action, getattr(animation, "action_slot", None))
    if bag is not None:
        yield {"source": "action"}, bag.fcurves
    for track in animation.nla_tracks:
        for strip in _nla_strips(track.strips):
            bag = _channelbag(getattr(strip, "action", None), getattr(strip, "action_slot", None))
            if bag is not None:
                yield {"source": "nla", "track": track.name, "strip": strip.name}, bag.fcurves


def channels(scene, paths):
    """``[(where, F-Curve)]`` that write one of ``paths`` (RNA paths from the scene)."""
    wanted = set(paths)
    return [(where, curve) for where, curves in _sources(scene) for curve in curves if curve.data_path in wanted]


def hold(scene, paths):
    """Mute the unmuted channels that write ``paths``; return what to record for ``release``."""
    held = []
    for where, curve in channels(scene, paths):
        if not curve.mute:
            curve.mute = True
            held.append(dict(where, path=curve.data_path, index=curve.array_index))
    return held


def _find(scene, record):
    for where, curves in _sources(scene):
        if all(where.get(key) == record.get(key) for key in ("source", "track", "strip")):
            for curve in curves:
                if curve.data_path == record.get("path") and curve.array_index == record.get("index"):
                    return curve
    return None


def release(scene, held):
    """Unmute what ``hold`` muted; return the paths of channels no longer found (reported, never silent)."""
    missing = []
    for record in held or ():
        if not isinstance(record, dict):
            continue
        try:
            curve = _find(scene, record)
            if curve is not None:
                curve.mute = False
                continue
        except (AttributeError, ReferenceError, TypeError):
            pass
        missing.append("animation of %s" % str(record.get("path", "?"))[:200])
    return missing


def bound(scene, path, current, frames):
    """The greatest value ``path`` takes on ``frames`` (inclusive): ``current`` when it is not animated.

    Raises AnimationUnbounded for a driver, an NLA strip or a non-Replace blend.
    With Replace and any influence, every evaluated value lies between the
    stored value and the keyed one, so their maximum is a true bound.
    """
    found = [(where, curve) for where, curve in channels(scene, (path,)) if not curve.mute]
    if not found:
        return current
    animation = scene.animation_data
    if (any(where["source"] != "action" for where, _curve in found)
            or getattr(animation, "action_blend_type", "REPLACE") != "REPLACE"):
        raise AnimationUnbounded(
            "The scene's %s is driven, on an NLA strip or blended, so a job cannot know its value on every frame "
            "before rendering. Give that value in the request; the job then holds it for every frame" % path,
            code="UNSUPPORTED")
    start, end = frames
    keyed = (curve.evaluate(frame) for _where, curve in found for frame in range(start, end + 1))
    return max(current, max((round(value) for value in keyed), default=current))
