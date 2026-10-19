# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed camera/light setting and aim predicates for scene validation.

Settings come only from the action catalog's parameter -> RNA maps on the
target's original camera/light datablock. Aim reads the object's world matrix,
which the staging owner updates before validation. Animated or driven data,
custom light node graphs and point-light aims are inconclusive, never passing.
Read-only; no evaluation, frame change or depsgraph update happens here.
"""

from .action_camera_light_spec import CAMERA_RNA, LIGHT_RNA, aim_error, is_default_light_graph
from .validation_reader import Inconclusive, number_vector, values_near

CAMERA_LIGHT_CHECKS = frozenset({"camera_settings", "light_settings", "aim"})


def _animated(block):
    animation = getattr(block, "animation_data", None)
    return animation is not None and (animation.action is not None or len(animation.drivers) or len(animation.nla_tracks))


def _settings(reader, obj, check):
    family, mapping = ("CAMERA", CAMERA_RNA) if check["kind"] == "camera_settings" else ("LIGHT", LIGHT_RNA)
    if obj.type != family:
        return False, {"type": obj.type}
    data = obj.data
    if _animated(data):
        raise Inconclusive("Animated or driven " + family.lower() + " data can differ from its stored settings")
    if family == "LIGHT" and not is_default_light_graph(getattr(data, "node_tree", None)):
        raise Inconclusive("A custom light node graph can change emitted color and strength")
    reader.spend(len(check["values"]))
    values, not_applicable = {}, []
    for name in check["values"]:
        prop = mapping[name]
        if not hasattr(data, prop):
            not_applicable.append(name)  # For example spot_size on a point light.
            continue
        value = getattr(data, prop)
        values[name] = number_vector(value, 3) if name == "color" else value
    passed = not not_applicable and all(values_near(values[name], expected, check.get("tolerance"))
                                        for name, expected in check["values"].items())
    return passed, {"values": values, "not_applicable": not_applicable, "scope": "ORIGINAL_" + family + "_DATA"}


def _aim(reader, obj, check):
    if obj.type not in {"CAMERA", "LIGHT"}:
        raise Inconclusive("Aim checks use the camera/light local -Z viewing axis")
    if obj.type == "LIGHT" and obj.data.type == "POINT":
        raise Inconclusive("Point lights are omnidirectional; they have no aim axis")
    reader.spend(16)
    matrix = reader.world_matrix(obj)
    try:
        error = aim_error(matrix, check["point"])
    except ValueError as failure:
        raise Inconclusive(str(failure)) from failure
    return error <= check["tolerance"], {"angle_error": error, "scope": "WORLD_MATRIX_NEGATIVE_Z_AXIS"}


def evaluate_camera_light(reader, obj, check):
    """Return (passed, bounded metrics) for one canonical camera/light/aim check."""
    if check["kind"] == "aim":
        return _aim(reader, obj, check)
    return _settings(reader, obj, check)
