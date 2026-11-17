# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which custom properties are Angelo's own saved records, never the person's data: ``own``.

Angelo saves records of its own as custom properties on the person's data (where a library or team item came from,
which character part an object is, a scene's merged parts, a render region) and as properties its add-on registers
on data-blocks (``Object.angelo_*``, which Blender stores beside them). They are the app's bookkeeping. An exporter
that writes custom properties wrote every one of them into the file it made (glTF extras, FBX custom properties,
USD and Alembic, the last two on by default), so a file handed to another tool or another company carried them.

The rule is the name, as ``AGENTS.md`` reserves it ("Angelo identifiers are real contracts": ``_angelo_*`` and
``angelo_*`` saved custom properties): the two prefixes, and exactly the pre-rename keys a .blend saved before
2026-10-07 may still hold (``angelo_agent.legacy_names``; a prefix there would take a person's own ``sensei_*``
key). Every exporter that writes custom properties asks it: ``io_scene_gltf2`` (``com/extras.py``, both its custom
and its registered properties), ``io_scene_fbx`` (``fbx_data_element_custom_properties``), and in C++ USD and
Alembic through its twin ``BKE_angelo_own_properties.hh``, which ``test_native_own_properties`` keeps equal to
this. What Angelo's own exports add to a glTF themselves (``web_export``, ``web_link_tree``: ``extras.angelo_uid``
and the like) is written by their hooks, not from custom properties, and is not touched.

Standard library only; no bpy.
"""

PREFIXES = ("angelo_", "_angelo_")
# The pre-rename keys of ``angelo_agent.legacy_names`` (WORKSPACE_DIRECTOR, MANUAL_WORKSPACE, NATIVE_GRAPH,
# SCULPT_REGIONS, PROGRAM_STAGE), by their exact names.
LEGACY = frozenset({"sensei_director", "sensei_manual_workspace", "_sensei_native_graph_v1",
                    "_sensei_sculpt_regions_v1", "_sensei_program_stage"})


def own(name):
    """True when the custom property ``name`` is one of Angelo's own saved records."""
    return isinstance(name, str) and (name.startswith(PREFIXES) or name in LEGACY)
