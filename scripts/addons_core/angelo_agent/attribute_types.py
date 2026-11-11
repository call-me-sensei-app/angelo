# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How each Blender attribute type's values are read in bulk (``foreach_get``): the one table every reader uses.

``scene_geometry`` (a typed program's exact read), ``operator_state`` (an operator's before/after state) and
``export_baked_reads`` derive theirs from it, each with its own buffer types (``codes``). Three copies drifted before:
tangent-space custom normals (INT16_2D, carried by most imported assets) were missing from the exact read, so every
typed program targeting such a mesh refused, and FLOAT4 from all three. Every type Blender defines is in ``FIELDS``
or ``UNREAD`` (test_native_attribute_types.py reads the source's enum; the custom-normals suite the running
Blender's).

No ``bpy``.
"""

# Type -> (property, components, kind). A kind is how a reader stores the values: "float", "int", "int8" (8-bit
# integers, which readers have stored differently) or "bool".
FIELDS = {"FLOAT": ("value", 1, "float"), "INT": ("value", 1, "int"), "INT8": ("value", 1, "int8"),
          "BOOLEAN": ("value", 1, "bool"), "FLOAT_VECTOR": ("vector", 3, "float"), "FLOAT2": ("vector", 2, "float"),
          "FLOAT4": ("vector", 4, "float"), "FLOAT_COLOR": ("color", 4, "float"), "BYTE_COLOR": ("color", 4, "float"),
          "QUATERNION": ("value", 4, "float"), "INT16_2D": ("value", 2, "int"), "INT32_2D": ("value", 2, "int"),
          "FLOAT4X4": ("value", 16, "float")}
UNREAD = {"STRING": "variable-length text has no bulk read"}


def codes(by_kind):
    """``{type: (property, code, components)}`` with each kind's buffer type taken from ``by_kind``."""
    return {name: (prop, by_kind[kind], width) for name, (prop, width, kind) in FIELDS.items()}
