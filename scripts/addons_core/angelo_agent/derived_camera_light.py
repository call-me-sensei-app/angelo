# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checks ``camera_create`` and ``light_create`` state themselves (``derived_checks``).

A new camera or light is fully described by its operation: canonical
parameters hold every setting of its projection or light type (the catalog
fills the type's defaults before approval), its location, and either an
explicit rotation or the point it looks at. Until these were derived, a model
that wanted a host-verified result had to declare them, and first read the
``aim`` and ``camera_settings`` check schemas: one lookup round and about
300 bytes of checks for each of two turns of a recorded bridge session.

Derived, in the order a summary reads best:

* ``object_name`` when the model chose one (the schema default is suffixed
  by Blender, like any other default name);
* ``transform`` of the location, and of an explicit non-zero rotation (an
  ``orbit`` has a location only once program_orbits resolved it, before
  approval: the program the approval shows and commit checks has it);
* ``camera_settings`` or ``light_settings`` with every canonical setting;
* ``aim`` at ``look_at``, except for a point light, which has no axis. The
  kernel already refuses a staged aim worse than ``AIM_TOLERANCE``.

Only creations are covered: their data is new, so it is neither animated nor
a custom light graph, the two cases the settings predicate cannot decide.
The ``*_KEEPS`` sets list the later kinds on the same object that leave each
check true; any other kind drops it (``derived_checks.derive_checks``).

Pure Python; no Blender access.
"""

import copy

from .action_camera_light_spec import AIM_TOLERANCE, CAMERA_LIGHT_CREATE_KINDS, family_of

DEFAULT_NAMES = {"camera_create": "Angelo Camera", "light_create": "Angelo Light"}
# Kinds that touch neither the object's data nor anything that animates it.
SETTINGS_KEEPS = frozenset({"transform", "parent_set", "constraint_add", "keyframe_insert", "collection_link",
                            "object_rename", "visibility_set"})
# Kinds that cannot move the object or change where its -Z axis points.
AIM_KEEPS = frozenset({"collection_link", "object_rename", "visibility_set"})
# Ten times the kernel's own bound: float32 storage of the rotation it computed, never a looser aim.
AIM_CHECK_TOLERANCE = AIM_TOLERANCE * 10


def created_checks(target, operation):
    """``(check, kinds that keep it)`` pairs for one canonical camera_create or light_create."""
    from .derived_checks import NAME_KEEPS, TRANSFORM_KEEPS
    kind, params = operation["kind"], operation["parameters"]
    if kind not in CAMERA_LIGHT_CREATE_KINDS:
        return []
    checks = []
    if params["name"] != DEFAULT_NAMES[kind]:
        checks.append(({"kind": "object_name", "target": target, "expected": params["name"]}, NAME_KEEPS))
    if "orbit" not in params:  # An orbit's location exists once the host resolved it (program_orbits).
        location = [float(value) for value in params["location"]]
        checks.append(({"kind": "transform", "target": target, "property": "location", "expected": location},
                       TRANSFORM_KEEPS))
    look_at = params.get("look_at")
    rotation = [float(value) for value in params["rotation"]]
    if look_at is None and any(rotation):
        checks.append(({"kind": "transform", "target": target, "property": "rotation_euler", "expected": rotation},
                       TRANSFORM_KEEPS))
    settings = copy.deepcopy(params["settings"])
    numbers = [item for value in settings.values() for item in (value if isinstance(value, list) else [value])
               if type(item) in (int, float)]
    checks.append(({"kind": family_of(kind) + "_settings", "target": target, "values": settings}, SETTINGS_KEEPS))
    if look_at is not None and settings.get("light_type") != "POINT":
        checks.append(({"kind": "aim", "target": target, "point": [float(value) for value in look_at],
                        "tolerance": AIM_CHECK_TOLERANCE}, AIM_KEEPS))
    return checks
