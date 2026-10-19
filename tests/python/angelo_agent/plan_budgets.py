# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The byte budgets of what a model reads, with the measured reason for every change.

``test_native_plan_schema.py`` checks them. A budget moves only with a toolset
entry that states the measured size (doc/angelo/agent_integration.rst).
"""

# The Claude plan schema (compact JSON).
# Lowered 51,500 -> 50,200 with toolset 35: the plan lost its string length limits, which the host
# enforces (51,162 -> 49,859 B, measured; agent_integration.rst, "Toolset 35").
# Raised 50,200 -> 50,500 with toolset 36: angelo_read_data's exact variant with its path and depth
# notes (+565 B) and the deferred name angelo_blend_file (+20 B) took the plan 49,859 -> 50,444 B,
# measured (agent_integration.rst, "Toolset 36"). Exact because a lookup round before its first use
# costs what the read saves.
# Raised 50,500 -> 50,750 with toolset 37: angelo_view's region (four bounded numbers), image and frame
# and its LIT and RENDERED shadings took the plan 50,444 -> 50,700 B, measured (agent_integration.rst,
# "Toolset 37"). Exact because a region or a finished picture is asked for in the plan that looks; a
# lookup round before it would cost more than these 256 B.
# Unchanged with toolset 39: angelo_view's new pictures (+135 B) and property_set's shared (+32 B) took the
# plan 50,575 -> 50,742 B, measured (agent_integration.rst, "Toolset 39"); 8 B are left.
# Raised 50,750 -> 50,980 within toolset 39, measured 50,968 B:
# - bevel-and-shade's apply_scale and its one-line note (+147 B; 50,742 -> 50,889 alone). Without it a plan left a
#   non-uniformly scaled cube's bevel stretched instead of applying the scale first.
# - property_set's scene camera also takes {"object": handle}, the form of every other object pointer (+79 B;
#   50,742 -> 50,821 alone). Claude Code's schema check refused that form otherwise, each time costing a round and
#   about 3.7k written tokens of its error.
# Lowered 50,980 -> 50,810 within toolset 39 (schema consistency; widening and an equivalent rewrite only),
# measured 50,968 -> 50,807 B:
# - every typed object pointer also takes {"object": handle}, no check requires tolerance, a material_values
#   check states any of its values and the deferred-kind variant takes no target (-92 B together);
# - angelo_agent_capabilities takes names with every family, recipe ids included (+3 B);
# - tagged unions written as if/then (tagged_unions), so a mismatch names its field (-72 B): the same plans.
# agent_execution.rst, "Referring to objects, data and values"; agent_integration.rst, "Toolset 39".
# Raised 50,810 -> 51,170 within toolset 39, measured 50,807 -> 51,162 B: keyframe_insert's parameters are a
# union by channel, so a scale key is bounded as a transform's scale (1,000; action_object refuses any larger
# result) and a location or rotation key keeps 100,000 (+363 B); the simple-animation recipe keys location and
# rotation (-8 B), as its scale variant would cost 0.4-1.4 KB.
# Raised 51,170 -> 51,760 within toolset 39, measured 51,163 -> 51,749 B: camera_create and light_create take
# orbit, a placement around look_at in the scene camera's frame that the host resolves to an exact location before
# approval, with its one-line note (+586 B: the shared $def, two references and the $defs letters it moves).
# Without it plans put a rim light at world +Y, not behind the subject as the camera sees it
# (agent_integration.rst, "Toolset 39").
# Raised 51,760 -> 54,450 within toolset 39, measured 51,749 -> 54,443 B: batch is exact in Claude's plan with its
# placement note as the description (+2,748 B; plan_kinds.CLAUDE_REPEAT_ACTIONS), and fit-dimensions' scale bounds
# default to the widest a transform takes, so only dimensions is required (-54 B). Plans that had batch named only
# wrote one create_primitive per repeated part rather than look batch up; the same plans re-encoded are about 225
# output tokens shorter (performance.rst, "Plan output tokens"). The bytes are read from the prompt cache.
# Raised 54,450 -> 54,460 within toolset 39, measured 54,443 -> 54,455 B: mesh_bend's name among the deferred kinds
# (+12 B). Claude's plan carries an arch as a reviewed script in the same plan, without a round, so the kind stays
# deferred there; the Codex reference states it (performance.rst, "One-shot rounds").
# Raised 54,460 -> 55,170 within toolset 39, measured 54,455 -> 55,164 B: mesh_bend is exact in Claude's plan with
# BEND_NOTE as its parameters' description (+709 B: its schema and the 272-byte note;
# plan_kinds.CLAUDE_EVERYDAY_ACTIONS). Named only, a plan usually wrote an arch as a script; stated, it bends the
# parts with mesh_bend and no script, in fewer seconds and output tokens (performance.rst, "Claude's plan states
# mesh_bend"). Every Codex surface is unchanged. The bytes are read from the prompt cache.
# Raised 55,170 -> 55,540 within toolset 39, measured 55,164 -> 55,531 B: angelo_execute_python takes base and edits,
# a patch of an earlier script by the id its result names, with a one-line description on base (+351 B), and
# angelo_script, which returns a script's code, is named among the deferred tools (+16 B). Without them a model lost
# a script's text between calls or re-sent a whole script for a one-line fix (agent_execution.rst, "Scripts by id").
# Raised 55,540 -> 55,800 within toolset 40, measured 55,203 -> 55,797 B (+594): angelo_view's views take a free
# camera {location, look_at, lens | ortho_scale, reference} with the views field's note, up to six when each pairs
# with an attached image. Without it a model saw the scene only from axes, its viewport or the scene camera, and
# could not look from a reference picture's own viewpoint (performance.rst, "Free cameras"). Every earlier call
# still validates.
# Raised 55,800 -> 55,980 within toolset 40, measured 55,797 -> 55,975 B (+178): a reference pair's compare (OVERLAY
# over the view, OUTLINE with both outlines and their distances) and the MATCAP, CURVATURE and DEPTH looks. Without
# them a model judged a likeness from two pictures side by side and only in fixed Workbench light; the face and hair
# tests' 2 mm tolerance is 5 px at their framing (doc/angelo/oneshot_tests.rst, "Grader uncertainty").
# Raised 55,980 -> 56,080, measured 55,975 -> 56,076 B: the animated read-back (+101 B).
# Raised 56,080 -> 56,100, measured 56,076 -> 56,092 B (+16 in the note, JSON-escaped): batch_spec's PLACEMENT_NOTE
# says a copy of a name without a number takes Blender's suffix ("Pier, Pier.001"), which batch now gives instead of
# 'Pier 2'. 'Name 2' could be another name than the one the person asked for.
PLAN_BUDGET_BYTES = 56100
# Claude's instructions with the plan.
# Raised 10,680 -> 10,720: the background-job sentence took the instructions 10,617 -> 10,714 B
# (+97, measured; long_jobs.rst, "Model-visible bytes").
# Raised 10,720 -> 10,840 with toolset 35: what a script error returns and what rolled_back means
# replaced "A script error may leave partial changes" (10,714 -> 10,833 B, +119, measured).
# Raised 10,840 -> 11,110 with toolset 36: the live-read sentence took them 10,833 -> 11,101 B
# (+268, measured).
# Raised 11,110 -> 11,830 with toolset 37: the view guidance names LIT and RENDERED and says when to use
# which (+309 B); the plan sentences say that views are captured again after every edit and stale ones are
# listed, that a request keeps twelve, and what region, image and frame mean, which a plan's schema cannot
# say (+410 B): 11,101 -> 11,820 B, measured. A model that read the old sentence stopped a turn early.
# Raised 11,830 -> 11,980 within toolset 39: one plan sentence says every step is validated before the first
# runs, so a program never sees what a script of the plan makes and shared materials belong in the program,
# before the script (+162 B: 11,811 -> 11,973, measured). Without it a plan of that shape lost a round of about
# 14 s (fast_tier_reliability.rst).
# Unchanged within toolset 39: the API-lookup sentence asks for a lookup only of an API the model is unsure of and
# says from_pydata needs none (+6 B: 11,973 -> 11,979, measured). Models otherwise looked up Mesh.from_pydata, which
# the instructions already state, before an arch script.
# Raised 11,980 -> 12,060, measured 11,979 -> 12,059 B (+80): one fact, since the host changed. "Exact host
# validation rejects plans before work" was no longer true of every plan: an invalid lookup or job start is refused
# alone, after the steps before it run (plan_validation; agent_integration.rst, "Operations the host refused
# alone"), so the sentence says so.
# Raised 12,060 -> 15,600 for the facts-only text, measured 12,059 -> 15,599 B (+3,540): the text states facts and
# constraints only. Claude's plan schema carries no tool descriptions, so a prescription that hid a tool fact became
# that fact; the kit's exact surface, the Blender 5.2 API facts, what each lookup tool answers and the plan
# mechanics the host enforces are stated.
# Raised 15,600 -> 15,610, measured 15,599 -> 15,609 B (+10): the view text names the free camera among views and
# drops "1-3", since six reference pairs fit a call (performance.rst, "Free cameras"), in this text's wording (what
# the call takes, no advice).
CLAUDE_INSTRUCTION_BUDGET_BYTES = 15610
# Every tool declared in full, as voice and external Codex threads declare them.
# Raised 100,000 -> 101,200 with toolset 37: angelo_view's description states what each picture
# shows, costs and leaves out (+1,632 B) and its schema gains region, image and frame (+256 B):
# 99,205 -> 101,095 B, measured (agent_integration.rst, "Toolset 37"). Voice and external Codex
# threads declare every tool in full; an OpenAI thread defers and sends 71,506 B.
# Raised 101,200 -> 102,000 with toolset 38: angelo_job_start's production fields (sources, cage,
# extrusion, ray_distance, image and one sentence: +628 B) and angelo_view's file, with what it shows
# of a job's outputs (+212 B), took them 101,095 -> 101,935 B, measured (agent_integration.rst,
# "Toolset 38"). The job tools are deferred everywhere else; an OpenAI thread sends 72,282 B.
# Unchanged with toolset 39: angelo_view's declaration states its new pictures (+1,440 B): 98,675 -> 100,115 B,
# measured (agent_integration.rst, "Toolset 39").
# Raised 102,000 -> 102,200 within toolset 40, measured 101,556 -> 102,167 B (+611): angelo_view's free cameras and
# reference pairs (performance.rst, "Free cameras").
# Raised 102,200 -> 102,560 within toolset 40, measured 102,167 -> 102,550 B (+383): a reference pair's compare and
# the MATCAP, CURVATURE and DEPTH looks (performance.rst, "Reference comparisons").
# Raised 102,560 -> 102,850, measured 102,550 -> 102,841 B: the animated read-back (+291 B).
DECLARED_TOOLS_BUDGET_BYTES = 102850
