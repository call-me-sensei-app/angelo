# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The add-on modules that declare typed wrapped operations (``WRAPPED_OPS``), which ``wrapped_ops`` registers.

A lane adds its module's name here with its declarations. A host test (``test_native_wrapped_ops.py``) fails when a
module declares ``WRAPPED_OPS`` and is not listed, so no declaration ships unchecked. Listing them, rather than
reading every add-on file at startup, keeps registration's cost to the declaring modules.
"""

MODULES = (
    "scatter_ops",   # Scatter (scatter_ui).
    "look_ops",   # Camera Look (look_ui).
    "shape_cut_ops",   # Shape Cut (shape_cut_tool).
    "uv_layout_ops",   # UV Layout (uv_layout_ui).
    "rigging_wrapped_ops",   # Rigging (rigging_ops, ik_controls).
)
