# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Codex launch contract (doc/angelo/gpt_launch.rst): offline, no subprocess or inference.

The program reference in a Codex thread's instructions, its dynamic tools, the
usage-limit error text, the context hooks the provider forwards under the
request fence, the host summary of a reply-less edit and the exact schemas a
rejected deferred call gets back.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.action_properties import PROPERTY_PATHS, SCENE
from angelo_agent.action_schemas import OPERATION_SCHEMAS
from angelo_agent.codex_reference import JOB_TOOL, SCENE_PATHS, program_reference, typescript
from angelo_agent.controller import Controller
from angelo_agent.plan_schema import (CLAUDE_CORE_ACTIONS, CODEX_REFERENCE_ACTIONS, CODEX_REFERENCE_MODIFIERS,
                                      DEFERRED_ACTIONS, DEFERRED_CHECKS, DEFERRED_TOOLS, PLAN_CORE_ACTIONS,
                                      PLAN_CORE_CHECKS, deferred_references, rejection_prompt)
from angelo_agent.providers import CodexProvider, _codex_error_text, codex_instructions
from angelo_agent.tool_specs import (CODEX_DEFERRED_NAMESPACE, CODEX_REFERENCED_ITEMS, TOOL_MAP, TOOLS,
                                      codex_tools, validate_call)
from test_native_request_fences import RecordingRPC
from test_native_round_savings import committed, program
from program_completion_fixtures import register_completion


class ProgramReferenceTests(unittest.TestCase):
    def test_bytes_are_deterministic(self):
        for short in (True, False):
            self.assertEqual(program_reference(short_identifiers=short), program_reference(short_identifiers=short))
        self.assertEqual(codex_instructions(short_identifiers=True, deferred=True),
                         codex_instructions(short_identifiers=True, deferred=True))

    def test_every_reference_kind_is_typed_and_every_other_is_named(self):
        text = program_reference()
        typed, others = text.split("Other action kinds", 1)
        # Claude's exact kinds, create_object (the one more the sessions looked up), transform_apply (GPT-6.1-Sol's
        # lookup before a bevel), mesh_bend (its arches were scripts, which a Codex plan cannot end with) and, in a
        # build that has it, batch.
        self.assertEqual(set(PLAN_CORE_ACTIONS) | set(CLAUDE_CORE_ACTIONS)
                         | ({"create_object", "batch", "transform_apply", "mesh_bend"} & set(OPERATION_SCHEMAS)),
                         set(CODEX_REFERENCE_ACTIONS))
        for kind in CODEX_REFERENCE_ACTIONS:
            self.assertIn('kind:"%s"' % kind, typed)
        for kind in PLAN_CORE_CHECKS:
            self.assertIn('kind:"%s"' % kind, typed)
        named = others.split("\n")[0].split("): ", 1)[1].rstrip(".").split(", ")
        for kind in OPERATION_SCHEMAS:
            self.assertEqual(kind in CODEX_REFERENCE_ACTIONS, ('kind:"%s"' % kind) in typed, kind)
            self.assertEqual(kind not in CODEX_REFERENCE_ACTIONS, kind in named, kind)
        for kind in DEFERRED_CHECKS:
            self.assertIn(kind, others)
        self.assertNotIn("unknown|", text, "No union collapsed to unknown")

    def test_the_reference_states_what_the_recorded_sessions_looked_up(self):
        """Recorded GPT-6 Astra bridge turns and one-shots spent capability lookups on these kinds."""
        text = program_reference()
        for fragment in ('{kind:"light_create";parameters:', '{kind:"camera_create";parameters:',
                         '{kind:"world_configure";target?:Target|null;', '{kind:"object_delete";target:Target;',
                         'type:"EMPTY";display?:', 'type:"ARRAY";settings?:{count?:number;'):
            self.assertIn(fragment, text)
        # A target that can only be null is not written, and the scene's own kind needs none.
        self.assertNotIn("target:null", text)
        self.assertNotIn("target?:null", text)
        # property_set's values are an open map: its other keys are stated, not dropped.
        self.assertIn("parameters:{values:{camera?:Obj|null;[k:string]:boolean|number|string|", text)
        self.assertIn("type Obj=string|{object:string;};", text)  # Every object pointer (action_schema_types).
        self.assertIn("ARRAY", CODEX_REFERENCE_MODIFIERS)
        self.assertEqual(text.count('type:"ARRAY"'), 2, "modifier_add and modifier_configure")
        self.assertNotIn('"ARRAY"|', text.split("Other action kinds")[0].split('kind:"modifier_configure"')[0].split(
            'kind:"modifier_add"')[1].split("{type:")[-1], "ARRAY left the by-name-only list")

    def test_an_arch_needs_no_script_and_no_api_lookup(self):
        """GPT-6.1-Sol arched footbridges ran an API lookup, a script, an in-turn program, a view and a reply.

        A Codex plan cannot end with Python (codex_plan.IN_TURN_ONLY), so the script cost a round of its own and
        the program and a view of the scripted shape two more; the lookup was of the from_pydata call the
        instructions already state. The reference now states mesh_bend with what its numbers mean, and the
        instructions ask for a lookup only of an API the model is unsure of.
        """
        from angelo_agent.action_schemas import BEND_NOTE
        from angelo_agent.providers import CLAUDE_INSTRUCTIONS, INSTRUCTIONS
        for plan in (False, True):
            text = program_reference(plan=plan)
            self.assertIn('{kind:"mesh_bend";target:Target;parameters:{rise:number;', text)
            self.assertIn(BEND_NOTE, text)
        for instructions in (INSTRUCTIONS, codex_instructions(short_identifiers=True, deferred=True, plan=True)):
            # Facts-only text: what from_pydata is and what angelo_blender_api answers
            # (Python-layer members such as Mesh.from_pydata included), in place of when to look one up.
            flat = " ".join(instructions.split())
            self.assertIn("A generated mesh is bpy.data.meshes.new(name).from_pydata(vertices, edges, faces).", flat)
            self.assertIn("angelo_blender_api answers by type name from the installed Blender", flat)
            self.assertIn("Python-layer members such as Mesh.from_pydata included", flat)
            self.assertNotIn("before writing unsupported Python", instructions)
        # A Claude session reads the Claude per-model text (claude_text): when to look an API up.
        self.assertIn("Look up an RNA/BMesh API you are unsure of with angelo_blender_api; from_pydata needs no "
                      "lookup.", CLAUDE_INSTRUCTIONS)
        self.assertNotIn("before writing unsupported Python", CLAUDE_INSTRUCTIONS)
        # What a typed edit's read-back states and what a look at it costs, in place of when to look.
        thread = " ".join(codex_instructions(short_identifiers=True, deferred=True, plan=True).split())
        self.assertIn("A typed edit's read-back and checks state what it made (sizes, a bend's rise, materials, counts); "
                      "looking at an edit in the same turn costs two more requests", thread)
        self.assertNotIn("Look at your own edit", CLAUDE_INSTRUCTIONS)  # Opus does not look at its edits unasked.

    def test_scene_paths_and_the_job_tool_need_no_lookup(self):
        text = program_reference()
        for path in SCENE_PATHS:
            self.assertEqual(PROPERTY_PATHS[path][0], SCENE, path)
        self.assertIn('Scene settings are property_set values with no target: render.engine ("BLENDER_EEVEE", "CYCLES"), '
                      + ", ".join(SCENE_PATHS[1:]), text)
        self.assertEqual(SCENE_PATHS[0], "render.engine")
        self.assertIn('{kind:"property_set";target?:Target|null;', text)
        self.assertIn("cycles.use_denoising", SCENE_PATHS)
        self.assertEqual(JOB_TOOL in TOOL_MAP, (JOB_TOOL + " arguments") in text)
        if JOB_TOOL in TOOL_MAP:
            self.assertIn('{kind:"RENDER_STILL"|"RENDER_ANIMATION"|', text)
            self.assertIn("resolution?:[number,number];samples?:number;", text)

    def test_the_reference_stays_within_its_budget(self):
        """Every request of a Codex chat thread carries it (cached after the first): growth is a decision.

        12,850 -> 12,870: property_set's ``shared?:"ALL_USERS"`` (+20 B, measured 12,850 -> 12,870), the opt-in
        without which a material other objects use could only be refused (agent_execution.rst, "Generic settings,
        modifiers, constraints and nodes").
        A plan thread's instructions: 25,760 -> 22,114 (toolset 39, measured): its shorter program reference
        saves 3,643 B (performance.rst, "Fixed opening of a new chat") and ``shared`` adds 20 (25,737 -> 22,114).
        12,870 -> 12,958 and 22,114 -> 22,440 (measured): transform_apply stated (+88 B) and the plan guidance
        saying that each operation is bound past the earlier ones' commits (+238 B). Without them GPT-6.1-Sol spent
        a lookup round and an exec round on them in bevel-and-shade turns (agent_integration.rst, "Codex typed
        plans").
        12,958 -> 13,220 and 22,440 -> 22,702 (measured): what batch's placements mean (+262 B,
        batch_spec.PLACEMENT_NOTE), which its TypeScript cannot say; read as offsets, they misplace a one-shot
        footbridge.
        13,220 -> 13,237 and 22,702 -> 22,719 (measured): the scene camera's
        ``camera?:string|{object:string;}|null`` (+17 B), the pointer form models also write there, which
        Claude Code's schema check otherwise refuses in camera one-shots (agent_integration.rst).
        13,237 -> 13,223 and 22,719 -> 22,703 (measured): every object pointer takes the handle or
        {object:...}, declared once as ``type Obj`` and written ``Obj`` (-254 B against writing the union at each
        pointer), and no check requires a tolerance (agent_execution.rst, "Referring to objects, data and values").
        13,223 -> 13,277 and 22,703 -> 22,757 (measured): the batch note counts as copies() does,
        "n-1 more instances (2n-1 with mirror), each copying every source" (+54 B); "n-1 copies" is wrong with
        mirror or several sources.
        13,277 -> 13,294 (measured): keyframe_insert's union by channel, whose scale key is
        bounded as a transform's scale; a plan thread's shorter reference and instructions are unchanged.
        13,294 -> 13,666 and 22,757 -> 23,129 (measured): camera_create and light_create's orbit
        (+118 B of TypeScript) and its note, which TypeScript drops (+254 B, action_camera_light_spec.ORBIT_NOTE):
        a placement in the scene camera's frame the host resolves before approval. Without it, Opus plans put the
        rim light at world +Y, not behind the cube as the camera sees it.
        13,666 -> 13,806 and 23,129 -> 23,269 (measured): the orbit line first says when to use it
        (placement relative to the view: key, fill or rim, behind, camera-left; world coordinates are not
        camera-relative; +140 B, action_camera_light_spec.ORBIT_REFERENCE_NOTE). GPT-6 Astra and GPT-6.1 Sol
        trials that used orbit placed the rim correctly, and the ones that placed it in world coordinates did not;
        Opus reads the note at the field.
        13,806 -> 14,044 and 23,269 -> 23,507 (measured): the batch note says what copies are named and
        aliased and that a unit's material and modifiers go before the batch (+238 B, batch_spec.PLACEMENT_NOTE),
        the order program_rules.linked_edits enforces. Without it a GPT-6.1-Sol arched-footbridge turn gave the
        unit its material after the batch, was refused (LINKED_EDIT) and wrote the program again.
        14,044 -> 14,433 and 23,507 -> 24,230 (measured): mesh_bend stated
        (+117 B of TypeScript) with its note, which TypeScript drops (+272 B, action_schemas.BEND_NOTE), the
        API-lookup sentence (+6 B, shared with every runtime's instructions) and when to look at one's own edit
        (+328 B, providers.CODEX_TOOLS_GUIDANCE). Without mesh_bend GPT-6.1-Sol scripted arched-footbridge arches;
        a plan cannot end with Python, so the script, a lookup of the from_pydata the instructions already state,
        an in-turn program and a view took five model requests; with mesh_bend GPT-6 Astra still ran the program
        in-turn to look at it, two requests that changed nothing (performance.rst, "One-shot rounds").
        24,230 -> 24,320 (measured; the reference unchanged): two code-mode facts only Codex reads
        (+90 B, providers.CODEX_TOOLS_GUIDANCE). image()'s second argument, if given, is a detail string:
        views.forEach(image) passes each index there and fails (the tool's own fix waits in the Codex fork).
        angelo_job_start is named as tools.more__angelo_job_start with no lookup: without that, Sol looked it up
        and searched ALL_TOOLS for its name (gpt_launch.rst, "Deferred families").
        14,433 -> 14,471 and 24,320 -> 24,358 (measured): the job tool's reference line says its output
        may also be one this chat's earlier job wrote (+38 B), as the host has allowed since a job replaces its own
        chat's output (job_outputs, JOB_GUIDANCE); "a new workspace-relative path" alone had become untrue.
        14,471 -> 14,647 and 24,358 -> 24,874 (measured, the facts-only variant): its text states facts and
        constraints only; the reference's orbit line says which camera orbit is measured from and when a program
        cannot also use it (+176 B), and the shared base grows by the Blender 5.2 API facts and plain statements of what each tool does.
        14,647 -> 14,666 and 24,874 -> 24,893 (measured): the job line states the rule that a job never
        overwrites and a taken name gets the next free one, which the result names (+19 B; job_output_names).
        24,893 -> 24,928 (measured; the reference unchanged): the plan text says a field left out takes its
        default or keeps its setting where it has none (+35 B), as the Claude per-model text does, since the
        configure kinds keep the settings a call leaves out.
        24,928 -> 24,939 (measured; the reference unchanged): the view text shared by every runtime names the
        free camera among views and drops "1-3", since six reference pairs fit a call (+11 B, providers.VIEW_GUIDANCE;
        performance.rst, "Free cameras").
        14,666 -> 14,682 and 24,939 -> 24,955 (measured): the batch note says a copy of a name without a
        number takes Blender's suffix ("Pier, Pier.001"; +16 B, batch_spec.PLACEMENT_NOTE), which batch gives
        instead of 'Pier 2'. 'Name 2' is another name than the one asked for: Codex-model bridges named a second
        abutment 'Bridge Abutments 2'.
        24,955 -> 24,958 (measured; the reference unchanged): the shared base states a script rerun's edits as
        "find/replace pairs", not the inline [{find, replace}], since no instruction shows a tool's arguments as JSON
        outside the {tool, arguments} wrapper (+3 B; test_native_claude_plan_guidance.NoInlineArgumentJsonTests).
        24,958 -> 25,258 (measured; the reference unchanged): the shared Blender facts state sun_rotation's frame (a
        bearing clockwise from +Y) and that MULTIPLE_SCATTERING's sky is the bluer one (+300 B,
        agent_guidance.BLENDER_API_FACTS; test_native_sky_facts.py): Sol set every recorded bridge's sky sun from an
        azimuth measured from +X, 158 degrees from its Sun light.
        """
        self.assertLessEqual(len(program_reference().encode()), 14682)
        self.assertLessEqual(len(codex_instructions(short_identifiers=True, deferred=True, plan=True, team=True).encode()),
                             25258)

    def test_a_correction_repeats_no_schema_the_reference_states(self):
        operations = [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
            {"kind": "light_create", "target": None, "parameters": {}},
            {"kind": "modifier_add", "target": "o1", "parameters": {"type": "ARRAY"}},
            {"kind": "modifier_add", "target": "o1", "parameters": {"type": "SCREW"}},
            {"kind": "shape_key_add", "target": "o1", "parameters": {}}]}}]
        self.assertEqual(deferred_references(operations)["ACTIONS"], ["light_create", "modifier_add", "shape_key_add"])
        reference = (CODEX_REFERENCE_ACTIONS, CODEX_REFERENCE_MODIFIERS)
        self.assertEqual(deferred_references(operations, *reference)["ACTIONS"], ["modifier_add", "shape_key_add"],
                         "SCREW is still read on demand")
        del operations[0]["arguments"]["operations"][2:]
        self.assertEqual(deferred_references(operations, *reference), {})
        self.assertNotIn("Exact schemas", rejection_prompt(["operations[0]: a problem"], operations, *reference))
        self.assertIn("Exact schemas", rejection_prompt(["operations[0]: a problem"], operations))

    def test_target_wording_follows_the_identifier_form(self):
        self.assertIn("o<n> handle", program_reference(short_identifiers=True))
        self.assertIn("object handle", program_reference(short_identifiers=False))
        self.assertIn("target:Target", program_reference())

    def test_keys_that_are_not_identifiers_are_quoted(self):
        schema = {"type": "object", "properties": {"plain": {"type": "string"}, "two words": {"type": "integer"},
                                                   "a-b": {"const": 1}}, "required": ["plain"]}
        self.assertEqual(typescript(schema), '{plain:string;"two words"?:number;"a-b"?:1;}')

    def test_shapes(self):
        self.assertEqual(typescript({"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}),
                         "[number,number,number]")
        self.assertEqual(typescript({"type": ["string", "null"]}), "string|null")
        self.assertEqual(typescript({"type": "array", "items": {"enum": ["A", "B"]}}), '("A"|"B")[]')
        self.assertEqual(typescript({"type": "object"}), "{[k:string]:unknown;}")
        shared = typescript({"oneOf": [
            {"type": "object", "properties": {"kind": {"const": "a"}, "at": {"type": "number"}}, "required": ["kind"]},
            {"type": "object", "properties": {"kind": {"const": "b"}, "at": {"type": "number"}}, "required": ["kind"]}]})
        self.assertEqual(shared, '{at?:number;}&({kind:"a";}|{kind:"b";})')


class CodexToolsTests(unittest.TestCase):
    def test_deferred_families_load_on_demand_in_one_namespace(self):
        tools = codex_tools()
        namespaces = [tool for tool in tools if tool.get("type") == "namespace"]
        self.assertEqual(len(namespaces), 1)
        self.assertEqual(namespaces[0]["name"], CODEX_DEFERRED_NAMESPACE)
        deferred = namespaces[0]["tools"]
        self.assertEqual({tool["name"] for tool in deferred}, {t["name"] for t in TOOLS} & DEFERRED_TOOLS)
        self.assertTrue(all(tool["deferLoading"] is True for tool in deferred))
        core = {tool["name"] for tool in tools if tool.get("type") == "function"}
        self.assertFalse(core & DEFERRED_TOOLS)
        self.assertEqual(core | {tool["name"] for tool in deferred}, {tool["name"] for tool in TOOLS})

    def test_external_connections_get_every_tool_up_front(self):
        tools = codex_tools(defer=False)
        self.assertFalse([tool for tool in tools if tool.get("type") == "namespace"])
        self.assertFalse([tool for tool in tools if tool.get("deferLoading")])
        self.assertEqual(len(tools), len(TOOLS))

    def test_allowed_filters_both_groups(self):
        allowed = {"angelo_scene_search", sorted(DEFERRED_TOOLS)[0]}
        tools = codex_tools(allowed)
        names = {tool["name"] for tool in tools if tool.get("type") == "function"}
        names |= {inner["name"] for tool in tools if tool.get("type") == "namespace" for inner in tool["tools"]}
        self.assertEqual(names, allowed)

    def test_program_items_point_at_the_reference_on_openai_connections_only(self):
        def items(tools, name, field):
            return next(t for t in tools if t.get("name") == name)["inputSchema"]["properties"][field]
        for name, fields in CODEX_REFERENCED_ITEMS.items():
            for field, kind in fields.items():
                referenced = items(codex_tools(), name, field)
                self.assertEqual(referenced["items"], {"type": "object", "properties": {"kind": {"type": "string"}},
                                                       "required": ["kind"]})
                self.assertIn("Items: %s from the Program reference" % kind, referenced["description"])
                self.assertNotEqual(items(codex_tools(defer=False), name, field)["items"], referenced["items"],
                                    "Function-calling connections keep the exact schemas")
        self.assertIn("never restate it", items(codex_tools(), "angelo_scene_program", "expected_checks")["description"])
        self.assertEqual(TOOL_MAP["angelo_scene_program"]["inputSchema"]["properties"]["operations"]["minItems"], 1,
                         "The original schema is unchanged")

    def test_external_connections_declare_programs_like_voice_and_claudes_plan(self):
        """Their instructions (CODEX_DEFERRED_GUIDANCE) say the program and verify tools defer the same kinds."""
        from angelo_agent.plan_schema import codex_arguments, plan_arguments
        from angelo_agent.schema_compaction import compact_schema
        for short, project in ((False, codex_arguments), (True, plan_arguments)):
            tools = {tool["name"]: tool for tool in codex_tools(defer=False, short_identifiers=short)}
            for name in CODEX_REFERENCED_ITEMS:
                with self.subTest(tool=name, short=short):
                    self.assertEqual(tools[name]["inputSchema"], compact_schema(project(TOOL_MAP[name])))
            operations = str(tools["angelo_scene_program"]["inputSchema"])
            self.assertIn(DEFERRED_ACTIONS[0], operations)
            self.assertNotIn("'shape_key_add', 'target'", operations, "deferred kinds are named, not exact")
        self.assertEqual(next(t for t in codex_tools(defer=False) if t["name"] == "angelo_scene_search")["inputSchema"],
                         next(t for t in codex_tools() if t["name"] == "angelo_scene_search")["inputSchema"])

    def test_the_recipe_tool_shows_its_ranges(self):
        """GPT gates: a model guessed fit-dimensions' maximum_scale_ratio past 100 (code mode shows no bounds)."""
        for tools in (codex_tools(), codex_tools(defer=False)):
            description = next(t for t in tools if t["name"] == "angelo_apply_recipe")["description"]
            self.assertIn("maximum_scale_ratio 1..100", description)
            self.assertIn("bevel-and-shade width 1e-06..100, segments 1..8", description)
            self.assertIn("base_color[4] 0..1", description)
        self.assertNotIn("Parameter ranges", TOOL_MAP["angelo_apply_recipe"]["description"], "Claude's schema has bounds")

    def test_a_kind_only_declaration_never_widens_validation(self):
        with self.assertRaises(ValueError):
            validate_call("angelo_scene_program", {"mode": "APPLY", "operations": [{"kind": "anything"}]})

    def test_short_identifiers_make_inferred_fields_optional(self):
        def required(tools, name):
            return set(next(t for t in tools if t.get("name") == name)["inputSchema"].get("required", ()))
        self.assertIn("read_set", required(codex_tools(short_identifiers=False), "angelo_scene_verify"))
        self.assertNotIn("read_set", required(codex_tools(short_identifiers=True), "angelo_scene_verify"))


class RejectionFeedbackTests(unittest.TestCase):
    """Regression: a GPT-6.1 Sol recipe call guessed an unseen bound and printed only success."""

    def test_a_bound_error_states_the_range(self):
        arguments = {"mode": "APPLY", "recipe": "fit-dimensions", "target": "scn:%s:159" % ("a" * 32),
                     "revision": "b" * 64, "parameters": {"dimensions": [4, 1, 3], "minimum_scale": 0.001,
                                                          "maximum_scale": 1000, "maximum_scale_ratio": 1000}}
        with self.assertRaisesRegex(ValueError, r"maximum_scale_ratio is outside its bounded range \(1 to 100\)"):
            validate_call("angelo_apply_recipe", arguments)

    def test_codex_is_told_to_print_rejections(self):
        text = " ".join(codex_instructions(short_identifiers=True, deferred=True).split())
        # Facts-only text: what a rejected call's result holds, in place of "always text() them".
        self.assertIn("a rejected call's result holds only error and exact_schemas, which say what to fix", text)

    def test_codex_parses_the_first_line_and_shows_views(self):
        """Code mode joins a dynamic tool's content items into one string, text then each image's data URL."""
        text = codex_instructions(short_identifiers=True, deferred=True)
        self.assertIn('const [json, ...views] = r.split("\\n"); const v = JSON.parse(json)', text)
        self.assertIn("image(url)", text)


class LimitErrorTests(unittest.TestCase):
    def test_limits_name_themselves(self):
        for info in ("usageLimitExceeded", "rateLimitExceeded", "sessionBudgetExceeded"):
            text = _codex_error_text({"message": "Try again later.", "codexErrorInfo": info})
            self.assertEqual(text, "Provider usage limit reached (%s): Try again later." % info)

    def test_other_errors_keep_their_message(self):
        self.assertEqual(_codex_error_text({"message": "Bad request", "codexErrorInfo": "badRequest"}), "Bad request")
        self.assertEqual(_codex_error_text({"codexErrorInfo": "other"}), "Provider error")
        self.assertIsNone(_codex_error_text("not a dict"))


class ProviderFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = Controller(self.temp.name)
        self.c.set_document(("factory-runtime", 17))
        self.c.state["native_id"] = "native-thread"
        self.context = self.next_request()
        self.p = CodexProvider(self.context, {})
        self.p.rpc = RecordingRPC()
        self.p.thread_id = "native-thread"
        self.p.bind_request(self.context)
        self.p.turn_id = "turn-a"
        self.p._scope.bind_turn("turn-a")

    def tearDown(self):
        self.p.close()
        self.c.close()
        self.temp.cleanup()

    def next_request(self):
        self.c._begin_metrics()
        self.c.busy, self.c.cancelled = True, False
        self.c._turn_round_inputs, self.c._turn_compacted = [], False
        return self.c.runtime_context()


class ContextHookTests(ProviderFixture):
    def usage(self, tokens, thread="native-thread"):
        self.p._event("thread/tokenUsage/updated", {"threadId": thread, "turnId": "turn-a", "tokenUsage": {
            "last": {"inputTokens": tokens, "cachedInputTokens": 0, "outputTokens": 10, "reasoningOutputTokens": 0,
                     "totalTokens": tokens + 10},
            "total": {"inputTokens": tokens, "cachedInputTokens": 0, "outputTokens": 10, "reasoningOutputTokens": 0,
                      "totalTokens": tokens + 10}}})

    def test_prompt_sizes_and_compaction_reach_the_controller(self):
        self.usage(40000)
        self.usage(9000, thread="another-thread")
        self.assertEqual(self.c._turn_round_inputs, [40000])
        self.p._event("thread/compacted", {"threadId": "native-thread", "turnId": "turn-a"})
        self.assertTrue(self.c._turn_compacted)

    def test_a_stale_request_cannot_record(self):
        old = self.context
        self.c.cancelled = True
        self.next_request()
        old.record_context_tokens(1234)
        old.record_context_compaction()
        self.assertEqual(self.c._turn_round_inputs, [])
        self.assertFalse(self.c._turn_compacted)

    def test_malformed_sizes_are_ignored(self):
        for value in (-1, 1.5, "100", True, None):
            self.c.record_context_tokens(value)
        self.assertEqual(self.c._turn_round_inputs, [])


class ToolCallTests(ProviderFixture):
    def test_an_unknown_namespace_is_rejected_before_admission(self):
        self.p._request("ns", "item/tool/call", {"threadId": "native-thread", "turnId": "turn-a", "callId": "ns",
                                                 "namespace": "elsewhere", "tool": "angelo_scene_search",
                                                 "arguments": {"query": "Cube"}})
        (_, payload), = self.p.rpc.responses
        self.assertIs(payload["success"], False)
        self.assertIn("namespace", payload["contentItems"][0]["text"])
        self.assertFalse(self.c.pending)

    def test_a_rejected_deferred_kind_returns_its_exact_schema(self):
        replies = []
        arguments = program()["arguments"]
        arguments["operations"] = [{"kind": DEFERRED_ACTIONS[0], "target": "$cube"}]
        self.c.request_tool("deferred", "angelo_scene_program", arguments, replies.append)
        (reply,) = replies
        self.assertIn("error", reply)
        self.assertEqual(list(reply["exact_schemas"]), ["ACTIONS"])
        self.assertIn(DEFERRED_ACTIONS[0], str(reply["exact_schemas"]["ACTIONS"]))

    def test_a_rejected_core_call_returns_its_structured_validation_error(self):
        replies = []
        self.c.request_tool("core", "angelo_scene_program", {"mode": "APPLY"}, replies.append)
        (reply,) = replies
        self.assertEqual(set(reply), {"error", "error_code"})
        self.assertEqual(reply["error_code"], "VALIDATION")
        self.assertTrue(reply["error"])
        self.assertFalse(self.c.pending)


class CommittedRevisionTests(ProviderFixture):
    """Regression: a verification right after an edit, with its read set omitted, bound the
    pre-edit revision ("Verification target changed since observation") because Codex reads tool results
    within one turn without a fresh observation."""

    HANDLE, OLD, NEW = "scn:%s:159" % ("a" * 32), "b" * 64, "c" * 64

    def table(self):
        from angelo_agent import identifiers
        table = identifiers.IdentifierTable()
        table.compact({"handle": self.HANDLE})
        table.observe({"objects": [{"handle": self.HANDLE, "object_revision": self.OLD}]})
        self.c._identifiers = table
        return table

    def committed(self, status="COMMITTED"):
        return {"success": True, "status": "committed", "live_revisions": {"status": status, "objects": [
            {"handle": self.HANDLE, "name": "Eval Cube", "type": "MESH", "role": "WRITTEN", "object_revision": self.NEW}]}}

    def test_a_committed_result_updates_the_binding_a_later_verification_infers(self):
        from angelo_agent import identifiers
        from angelo_agent.providers import tool_content
        table = self.table()
        self.assertTrue(self.context.short_identifiers)
        tool_content(self.context, "angelo_scene_program", self.committed())
        self.assertEqual(table.observed(self.HANDLE), self.NEW)
        expanded = identifiers.expand_call("angelo_scene_verify", {"checks": [{"kind": "object_type", "target": "o1",
                                                                              "expected": "MESH"}]}, table)
        self.assertEqual(expanded["read_set"], [{"handle": self.HANDLE, "revision": self.NEW}])

    def test_staged_failed_or_voice_results_change_nothing(self):
        from angelo_agent.providers import tool_content
        table = self.table()
        tool_content(self.context, "angelo_scene_program", self.committed("STAGED"))
        tool_content(self.context, "angelo_scene_program", {"error": "rejected"})
        self.assertEqual(table.observed(self.HANDLE), self.OLD)
        self.context.voice = object()
        tool_content(self.context, "angelo_scene_program", self.committed())
        self.assertEqual(table.observed(self.HANDLE), self.OLD, "voice keeps long forms and no table")


class HostSummaryTests(ProviderFixture):
    def summary(self, reply, calls):
        for operation, result in calls:
            register_completion(self.context, operation["tool"], operation["arguments"], result)
        self.p._turn_reply, self.p._turn_calls = reply, calls
        self.context.emit = Mock()
        self.p._emit_host_summary(self.context, self.p._scope.fence)
        return [call.args[1]["text"] for call in self.context.emit.call_args_list if call.args[0] == "message"]

    def test_a_reply_less_verified_edit_ends_with_the_host_summary(self):
        (text,) = self.summary("", [(program(), committed())])
        self.assertTrue(text.startswith("Host-verified result"))

    def test_a_reply_or_an_unproven_edit_stands_as_it_is(self):
        self.assertEqual(self.summary("Done, and I kept it centred.", [(program(), committed())]), [])
        self.assertEqual(self.summary("", [(program(), committed(checks=[]))]), [])
        self.assertEqual(self.summary("", []), [])

    def test_a_stale_fence_emits_nothing(self):
        operation, result = program(), committed()
        register_completion(self.context, operation["tool"], operation["arguments"], result)
        self.p._turn_reply, self.p._turn_calls = "", [(operation, result)]
        self.context.emit = Mock()
        self.p._emit_host_summary(self.context, object())
        self.context.emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
