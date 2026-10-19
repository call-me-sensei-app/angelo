# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stand-in scene animation for host tests: a slotted Action, NLA strips and drivers, as Blender 5 lays them out.

``Curve.evaluate`` answers from a table of frames (the last key holds after
it), which is all ``job_animated_settings.bound`` asks of an F-Curve.
"""

from types import SimpleNamespace


class Curve:
    def __init__(self, path, keys=None, index=0, mute=False, driver=None):
        self.data_path, self.array_index, self.mute, self.keys, self.driver = path, index, mute, dict(keys or {}), driver

    def evaluate(self, frame):
        before = [key for key in sorted(self.keys) if key <= frame]
        return self.keys[before[-1]] if before else self.keys[min(self.keys)]


class Bag:
    def __init__(self, curves):
        self.fcurves = list(curves)


class Strip:
    """A keyframe strip of an Action's layer: ``channelbag(slot)`` answers for the slot it holds channels of."""

    def __init__(self, slot, curves):
        self.bag, self.slot = Bag(curves), slot

    def channelbag(self, slot):
        return self.bag if slot is self.slot else None


def action(slot, curves):
    return SimpleNamespace(layers=[SimpleNamespace(strips=[Strip(slot, curves)])])


def animate(scene, *, keyed=(), drivers=(), nla=(), blend="REPLACE"):
    """Give ``scene`` animation data: keyed curves in its Action, drivers, and NLA strips holding other curves."""
    slot = SimpleNamespace(name="SCScene")
    tracks = []
    for index, curves in enumerate(nla):
        strip_action = action(slot, curves)
        strip = SimpleNamespace(name="Strip %d" % index, action=strip_action, action_slot=slot, strips=())
        tracks.append(SimpleNamespace(name="Track %d" % index, strips=[strip]))
    scene.animation_data = SimpleNamespace(action=action(slot, keyed) if keyed else None, action_slot=slot,
                                           drivers=list(drivers), nla_tracks=tracks, action_blend_type=blend)
    return scene.animation_data


def driver(expression="var * 2", kind="SCRIPTED", simple=False, use_self=False, variables=("var",)):
    return SimpleNamespace(type=kind, expression=expression, is_simple_expression=simple, use_self=use_self,
                           variables=[SimpleNamespace(name=name) for name in variables])
