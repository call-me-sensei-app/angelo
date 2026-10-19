# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pre-rename names that saved data may still contain, and how Angelo reads them.

The product was called ToonLab and then Sensei before it became Angelo
(2026-10-07). Chats, task memory and .blend files saved before that keep the old
names. Only read-compatibility and migration code imports this module; new code,
tests and documentation use the Angelo names. The complete list of retained
names is "Legacy names" in ``doc/angelo/native_editor_build.rst``.

Standard library only (no ``bpy``): ID data-blocks are used only through
``get(key)``, item assignment and deletion. An ID property cannot hold
``None``, so ``get`` returning ``None`` means the key is absent.
"""

TOOL_PREFIX = "angelo_"
LEGACY_TOOL_PREFIX = "toonlab_"

# (current key, pre-rename key) of custom properties that Angelo saves in .blend files.
WORKSPACE_DIRECTOR = ("angelo_director", "sensei_director")
MANUAL_WORKSPACE = ("angelo_manual_workspace", "sensei_manual_workspace")
NATIVE_GRAPH = ("_angelo_native_graph_v1", "_sensei_native_graph_v1")
SCULPT_REGIONS = ("_angelo_sculpt_regions_v1", "_sensei_sculpt_regions_v1")
PROGRAM_STAGE = ("_angelo_program_stage", "_sensei_program_stage")


def current_tool_name(name):
    """The current name of a tool recorded under its pre-rename name; any other value is returned as is.

    For reading history only (display, handoff, evidence lookup). Execution never
    accepts a pre-rename name: ``tool_specs.validate_call`` rejects it.
    """
    if isinstance(name, str) and name.startswith(LEGACY_TOOL_PREFIX):
        return TOOL_PREFIX + name[len(LEGACY_TOOL_PREFIX):]
    return name


def present_key(owner, keys):
    """The key of ``keys`` (current, legacy) that ``owner`` has, preferring the current one; else None."""
    current, legacy = keys
    if owner.get(current) is not None:
        return current
    return legacy if owner.get(legacy) is not None else None


def read_property(owner, keys, default=None):
    """The saved value under the current key, else under the pre-rename key, else ``default``."""
    key = present_key(owner, keys)
    return default if key is None else owner.get(key)


def write_property(owner, keys, value):
    """Save under the current key and drop the pre-rename copy, so one value remains."""
    current, legacy = keys
    owner[current] = value
    if owner.get(legacy) is not None:
        del owner[legacy]
