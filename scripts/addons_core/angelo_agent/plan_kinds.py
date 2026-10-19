# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What each plan form states exactly and what it only names: tools, action kinds, modifier types and checks.

Claude's typed plan (``plan_schema``), a Codex chat thread's program reference
(``codex_reference``) and a Codex plan's strict output (``codex_plan``) keep
exact schemas only for what most turns use. Everything else is named in one
generic variant and read on demand (``plan_schema.lookup``), or comes back with
a rejected plan. These lists are that policy, each with the measured reason for
it; ``plan_schema`` builds the schemas from them and re-exports every name here.
Deferral moves only where a model reads a schema: ``tool_specs.validate_call``
still validates every operation exactly.

Pure Python; no Blender access.
"""

from .action_schemas import MODIFIER_SETTINGS, OPERATION_SCHEMAS
from .flow_spec import FLOW_NAMES
from .job_spec import JOB_NAMES
from .sculpt_spec import SCULPT_NAMES
from .studio_spec import STUDIO_NAMES
from .team_library_spec import TEAM_NAMES
from .validation_spec import CHECK_VARIANTS


# Exact in the plan: the kinds and checks of ordinary modeling, material,
# modifier, parenting and keyframe edits. A family deferred here costs one
# lookup round the first time a session uses it.
PLAN_CORE_ACTIONS = ("create_primitive", "transform", "modifier_add", "modifier_configure",
                     "material_principled", "parent_set", "keyframe_insert")
# Exact in Claude's plan only: small kinds ordinary requests used. In recorded
# everyday requests (doc/angelo/performance.rst, "Everyday kinds") each first use
# cost a lookup round of about 3.5 s. Codex declarations keep them deferred.
CLAUDE_EVERYDAY_ACTIONS = ("object_delete", "object_rename", "modifier_apply", "collection_link", "object_join",
                           "world_configure", "object_duplicate", "marker_edit", "camera_configure", "property_set",
                           "light_configure", "armature_bind", "mesh_extrude", "camera_create", "light_create",
                           "uv_smart_project", "material_assign", "mesh_bend")
# Exact in Claude's plan, with batch_spec.PLACEMENT_NOTE as its description: every recorded Opus footbridge plan
# (10 of 10) wrote one create_primitive per post and rail instead of looking batch up; re-encoded, they are about
# 220 output tokens shorter for 2.7 KB of cached schema (doc/angelo/performance.rst, "Plan output tokens").
CLAUDE_REPEAT_ACTIONS = tuple(kind for kind in ("batch",) if kind in OPERATION_SCHEMAS)
CLAUDE_CORE_ACTIONS = PLAN_CORE_ACTIONS + CLAUDE_EVERYDAY_ACTIONS + CLAUDE_REPEAT_ACTIONS
# Exact in a Codex chat thread's program reference (codex_reference), where a kind is a line of TypeScript
# rather than a JSON Schema: Claude's everyday kinds and create_object. In recorded bridge sessions
# a Codex thread spent a lookup round on each family it first used: 8 rounds and 41 KB of schemas
# in nine turns. The strict plan schema keeps the short core; the others travel as JSON text (codex_plan).
# transform_apply: in recorded runs GPT models applied a scaled cube's scale before its first
# bevel after a lookup round for this three-boolean kind: GPT-6.1-Sol in 7 of 10 bevel_and_shade turns (5 of 5
# through Blender MCP), GPT-6 Astra in 3 of 3 bevel_03 turns. Claude never did, so its plan keeps it deferred.
# mesh_bend: in recorded arched-footbridge one-shots GPT-6.1-Sol made the arch with a reviewed script in 5 of 5
# trials; a Codex plan cannot end with Python (codex_plan.IN_TURN_ONLY), so the script, an API lookup before it, the
# program run in-turn after it and a view of the scripted shape took five model requests (40.3 s median) where
# the flat footbridge took one. Claude's plan states it too (BEND_NOTE as its description, core_operation): named only,
# Opus scripted the arch; stated, it bent it in 5 of 5 A/B trials, 21.0 -> 13.0 s (performance.rst, "One-shot rounds").
CODEX_REFERENCE_ACTIONS = CLAUDE_CORE_ACTIONS + tuple(  # batch: where the build has it, a repeated unit is everyday too.
    kind for kind in ("create_object", "batch", "transform_apply", "mesh_bend")
    if kind in OPERATION_SCHEMAS and kind not in CLAUDE_CORE_ACTIONS)
PLAN_CORE_CHECKS = ("dimensions", "transform", "object_count", "object_type", "object_name",
                    "finite_coordinates", "mesh_counts", "material_assignment", "material_values",
                    "smooth_shading_graph", "parent", "animation", "modifier")
# The modifier types modifier_add can create; other modifier checks are deferred.
PLAN_CORE_MODIFIER_CHECKS = ("BEVEL", "SUBSURF", "SOLIDIFY", "MIRROR")
# Modifier types exact inside the core modifier_add/modifier_configure kinds.
# Other types are named in one generic parameter variant, like deferred kinds,
# so adding a modifier type never grows the always-sent schema by more than
# its name; a lookup of modifier_add or modifier_configure returns every type.
PLAN_CORE_MODIFIERS = ("BEVEL", "SUBSURF", "SOLIDIFY", "MIRROR", "BOOLEAN")
# The Codex reference also states ARRAY (rows of posts and lamps cost a 10.8 KB lookup of modifier_add).
CODEX_REFERENCE_MODIFIERS = PLAN_CORE_MODIFIERS + ("ARRAY",)
DEFERRED_MODIFIERS = tuple(kind for kind in MODIFIER_SETTINGS if kind not in PLAN_CORE_MODIFIERS)
MODIFIER_KINDS = ("modifier_add", "modifier_configure")
# Whole tool families that most turns never use. New tools stay exact by default.
DEFERRED_TOOLS = frozenset(SCULPT_NAMES | STUDIO_NAMES | FLOW_NAMES | TEAM_NAMES | JOB_NAMES
                           | {"angelo_memory_update", "angelo_expand_recipe", "angelo_scene_settings", "angelo_script",
                              # A file's hygiene is asked for rarely; the data-path reader stays exact, because
                              # a lookup round before its first use would cost what it saves (read_spec).
                              "angelo_blend_file"})


def core_check(variant):
    """Whether a check variant is exact in the plan (the rest are named only)."""
    properties = variant["properties"]
    kind = properties["kind"]["const"]
    if kind not in PLAN_CORE_CHECKS:
        return False
    return kind != "modifier" or properties["type"]["const"] in PLAN_CORE_MODIFIER_CHECKS


DEFERRED_ACTIONS = tuple(kind for kind in OPERATION_SCHEMAS if kind not in PLAN_CORE_ACTIONS)
CLAUDE_DEFERRED_ACTIONS = tuple(kind for kind in OPERATION_SCHEMAS if kind not in CLAUDE_CORE_ACTIONS)
DEFERRED_CHECKS = tuple(sorted({variant["properties"]["kind"]["const"] for variant in CHECK_VARIANTS
                                 if variant["properties"]["kind"]["const"] not in PLAN_CORE_CHECKS}))
DEFERRED_MODIFIER_CHECKS = tuple(variant["properties"]["type"]["const"] for variant in CHECK_VARIANTS
                                 if variant["properties"]["kind"]["const"] == "modifier" and not core_check(variant))
