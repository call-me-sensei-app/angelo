# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A plan's provisional steps in the live row while it is written: decoded, grouped, carried, never kept.

Dependency-free: the stream readers (``claude_stream.RoundStream``, ``codex_plan.PlanTurns``), the decoder
(``plan_progress.PlanProgress``), ``plan_steps`` and the controller's event pump; no Blender, process or model.
The fixture is a recorded Opus 5.5 plan for the arched footbridge one-shot (its script shortened): about 9 s
of plan after 5 s of thinking, with nothing of it on screen until its reply.
"""

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import claude_stream, codex_plan, plan_progress, plan_steps  # noqa: E402
from angelo_agent.claude_stream import STATUS_PLAN, STATUS_THINKING, RoundStream  # noqa: E402
from angelo_agent.plan_progress import PlanProgress  # noqa: E402
from angelo_agent.strict_schema import encode  # noqa: E402


def post(name, alias, x, y):
    return {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": name, "alias": alias, "size": 1,
                                                       "location": [x, y, 0.7], "scale": [0.15, 0.15, 1]}}


FOOTBRIDGE = {"operations": [
    {"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
        post("Post_FL", "p1", -1.9, -0.55), post("Post_FR", "p2", 1.9, -0.55), post("Post_BL", "p3", -1.9, 0.55),
        post("Post_BR", "p4", 1.9, 0.55), post("Rail_Front", "r1", 0, -0.55), post("Rail_Back", "r2", 0, 0.55),
        {"kind": "material_principled", "target": "$p1", "parameters": {
            "name": "Grey Stone", "inputs": {"Base Color": [0.42, 0.42, 0.4, 1], "Roughness": 0.9},
            "also": ["$p2", "$p3", "$p4", "$r1", "$r2"]}}],
        "expected_checks": [{"kind": "object_name", "target": "$p1", "expected": "Post_FL"}]}},
    {"tool": "angelo_execute_python", "arguments": {
        "description": "Create a 4 m x 1.2 m slightly arched stone deck mesh at the origin and assign Grey Stone.",
        "code": "import bpy, math\nme = bpy.data.meshes.new('Deck')\nob = {\"kind\": \"transform\", \"name\": \"X\"}\n"
                "result = 'Deck'"}}],
    "reply": "I'm building the footbridge at the origin, so it will overlap the default Cube."}
TEXT = json.dumps(FOOTBRIDGE)
ROWS = [("create", "create_primitive", "Post", 4), ("create", "create_primitive", "Rail", 2),
        ("material", "material_principled", "Grey Stone", 1),
        ("script", "angelo_execute_python",
         "Create a 4 m x 1.2 m slightly arched stone deck mesh at the origin and assign Grey Stone.", 1)]
# A Codex plan runs no script (reviewed Python is called in-turn), so its plan is the program alone.
CODEX = dict(FOOTBRIDGE, operations=FOOTBRIDGE["operations"][:1], reply="")
KEY = "sk-ant-fake-7Hq2-Wn9c-Rt4x-Lp8z"  # Not a credential: a made-up key the chat must never draw.


def strict_text(plan):
    """The plan as a Codex model writes it (reply first; kinds the format does not spell out as JSON text)."""
    spelled = json.loads(json.dumps(plan))
    for item in spelled["operations"]:
        for operation in item["arguments"].get("operations", ()):
            operation.setdefault("target", None)
    return json.dumps(encode(spelled, codex_plan.ordinary_schema()), separators=(",", ":"))


def fed(text, size):
    progress = PlanProgress()
    for start in range(0, len(text), size):
        progress.feed(text[start:start + size])
    return progress


def final_names(text):
    return [step[1] for step in fed(text, len(text)).steps]


class DecoderTests(unittest.TestCase):
    def test_every_fragment_boundary_gives_the_same_steps(self):
        for label, text, rows in (("claude", TEXT, ROWS), ("codex", strict_text(CODEX), ROWS[:3])):
            whole = fed(text, len(text))
            self.assertEqual(plan_steps.groups(whole.steps), rows, label)
            for cut in range(1, len(text)):
                progress = PlanProgress()
                progress.feed(text[:cut])
                progress.feed(text[cut:])
                self.assertEqual(progress.steps, whole.steps, "%s cut at %d" % (label, cut))
            for size in (1, 2, 3, 5, 7, 13, 64):
                with self.subTest(runtime=label, size=size):
                    self.assertEqual(fed(text, size).steps, whole.steps)

    def test_no_prefix_ever_shows_part_of_a_name(self):
        """A value is adopted only once its closing quote arrived: every shown row is final or a count of them."""
        for label, text in (("claude", TEXT), ("codex", strict_text(CODEX))):
            final, progress, shown = final_names(text), PlanProgress(), []
            for character in text:
                progress.feed(character)
                for index, step in enumerate(progress.steps):
                    self.assertIn(step[1], (None, final[index]), label)
                shown.append(plan_steps.groups(progress.steps))
            labels = {row[2] for rows in shown for row in rows}
            self.assertLessEqual(labels, {row[2] for row in ROWS} | {"Post_FL", "Rail_Front", "Post"}, label)
            first = next(index for index, rows in enumerate(shown) if rows)
            self.assertEqual(shown[first], [("create", "create_primitive", "Post_FL", 1)],
                             "The first row is the first object, by its own name")

    def test_checks_lookups_and_code_are_never_steps(self):
        steps = [step[0] for step in fed(TEXT, 9).steps]
        self.assertEqual(steps, ["create_primitive"] * 6 + ["material_principled", "angelo_execute_python"],
                         "Not the expected check, nor the JSON inside the script's code")
        lookup = json.dumps({"operations": [{"tool": "angelo_agent_capabilities", "arguments": {"family": "CHECKS"}},
                                            {"tool": "angelo_scene_verify", "arguments": {"checks": []}}]})
        self.assertEqual(fed(lookup, 4).steps, [], "A lookup and a verification are not steps of what a plan makes")

    def test_a_kind_without_a_name_shows_at_once_and_a_named_one_waits_for_its_name(self):
        edit = json.dumps({"operations": [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
            {"kind": "transform", "target": "o3", "parameters": {"location": [0, 0, 1], "name": "not a step's name"}}]}}]})
        before_target = edit[:edit.index('"target"')]
        self.assertEqual(plan_steps.groups(fed(before_target, 5).steps), [("edit", "transform", None, 1)],
                         "A transform has no name to wait for; a deeper setting's name never names it")
        self.assertEqual(plan_steps.groups(fed(edit, 5).steps), [("edit", "transform", None, 1)])
        named = TEXT[:TEXT.index('"Post_FL"')]
        self.assertEqual(plan_steps.groups(fed(named, 5).steps), [], "Its name is still coming")
        recipe = json.dumps({"operations": [{"tool": "angelo_apply_recipe", "arguments": {"recipe": "bevel_and_shade"}}]})
        self.assertEqual(plan_steps.groups(fed(recipe, 3).steps), [("edit", "angelo_apply_recipe", None, 1)])

    def test_a_long_value_is_cut_at_a_space_never_inside_a_word(self):
        description = "Build the deck " + "and its rails " * 10 + KEY + " then the posts."
        self.assertNotIn(KEY, plan_progress.label(json.dumps(description)[1:-1]))
        self.assertTrue(plan_progress.label(json.dumps(description)[1:-1]).endswith("…"))
        for cut in range(len(description) - 40, len(description)):  # Wherever the cut would fall, never a key part.
            shown = plan_progress.label(json.dumps(description[:cut])[1:-1])
            self.assertTrue(KEY in shown or not any(KEY[i:i + 5] in shown for i in range(len(KEY) - 4)), cut)
        self.assertIsNone(plan_progress.label(json.dumps("x" * 400)[1:-1]), "No space: dropped whole")
        self.assertEqual(plan_progress.label(json.dumps("Grey \"Stone\"\n  slab")[1:-1]), 'Grey "Stone" slab')

    def test_the_steps_are_bounded(self):
        many = json.dumps({"operations": [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
            post("Lamp %03d" % index, "l%d" % index, index, 0) for index in range(200)]}}]})
        progress = fed(many, 4096)
        self.assertEqual((len(progress.steps), progress.more_steps), (plan_progress.MAX_STEPS, 200 - plan_progress.MAX_STEPS))
        self.assertEqual(plan_steps.groups(progress.steps), [("create", "create_primitive", "Lamp", 64)])
        self.assertEqual(plan_steps.rows(plan_progress.status(STATUS_PLAN, "", plan_steps.encode(progress))),
                         ([("create", "create_primitive", "Lamp", 64)], 136))


def batch(sources, **placement):
    return {"kind": "batch", "parameters": dict(sources=sources, **placement)}


# The footbridge as Claude's plan writes it once batch is exact: one unit and a batch.
BATCHED = {"operations": [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
    post("Deck", "d", 0, 0), post("Post", "p", -1.9, -0.55), post("Rail_Front", "r", 0, -0.55),
    {"kind": "material_principled", "target": "$d", "parameters": {"name": "Grey Stone", "inputs": {},
                                                                    "also": ["$p", "$r"]}},
    batch(["$p"], at=[[-1.9, -0.55, 0.7], [1.9, -0.55, 0.7], [-1.9, 0.55, 0.7], [1.9, 0.55, 0.7]]),
    batch(["$r"], mirror="Y")]}}]}
BATCHED_ROWS = [("create", "create_primitive", "Deck", 1), ("create", "create_primitive", "Post", 4),
                ("create", "create_primitive", "Rail", 2), ("material", "material_principled", "Grey Stone", 1)]


class BatchTests(unittest.TestCase):
    def test_a_batch_counts_on_its_units_row_once_complete_at_every_fragment_boundary(self):
        text = json.dumps(BATCHED)
        whole = plan_steps.groups(fed(text, len(text)).steps)
        self.assertEqual(whole, BATCHED_ROWS)
        for cut in range(1, len(text)):
            progress = PlanProgress()
            progress.feed(text[:cut])
            progress.feed(text[cut:])
            self.assertEqual(plan_steps.groups(progress.steps), BATCHED_ROWS, cut)
        progress, seen = PlanProgress(), []
        for character in text:
            progress.feed(character)
            seen.append(plan_steps.groups(progress.steps))
        posts = {row[3] for rows in seen for row in rows if row[2] in ("Post", "Post")}
        self.assertEqual(posts, {1, 4}, "The unit shows alone until its batch is complete, then counted")
        self.assertFalse(any(row[1] == "batch" for rows in seen for row in rows), "A folded batch never shows itself")

    def test_a_batch_of_several_sources_or_of_an_existing_object_shows_on_its_own(self):
        plan = {"operations": [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
            post("Lamp Post", "l", 0, 0), post("Lamp Head", "h", 0, 0),
            batch(["$l", "$h"], array=[{"step": [2, 0, 0], "count": 5}]),
            batch(["o5"], along={"points": [[0, 0, 0], [10, 0, 0]], "count": 3})]}}]}
        rows = plan_steps.groups(fed(json.dumps(plan), 7).steps)
        self.assertEqual(rows, [("create", "create_primitive", "Lamp Post", 1), ("create", "create_primitive", "Lamp Head", 1),
                                ("create", "batch", "Lamp Post", 5), ("create", "batch", None, 3)])
        from angelo_agent import chat_rows
        self.assertEqual([chat_rows.planned_label(kind, name, count) for _, kind, name, count in rows],
                         ["Lamp Post", "Lamp Head", "Lamp Post ×5", "Batch ×3"])

    def test_a_codex_batch_written_as_json_text_counts_the_same(self):
        plan = json.loads(json.dumps(BATCHED))
        for operation in plan["operations"][0]["arguments"]["operations"]:
            if operation["kind"] == "batch":
                operation["parameters"] = json.dumps(operation["parameters"])
        self.assertEqual(plan_steps.groups(fed(json.dumps(dict(plan, reply="")), 5).steps), BATCHED_ROWS)
        self.assertEqual(plan_progress.batch_count('{"sources":["$p"],"at":[[0,0,0],[1,0,0]]}'), 2)
        self.assertIsNone(plan_progress.batch_count('{"sources":["$p"],"along":{"points":"bad"}}'))
        self.assertIsNone(plan_progress.batch_count("not json"))

    def test_codex_calls_that_end_early_still_show(self):
        """A recipe call may end after its mode and a transform after its scale (strict_shapes)."""
        text = ('{"reply":"","operations":[{"tool":"angelo_apply_recipe","arguments":{"recipe":"bevel-and-shade",'
                '"target":"o3","parameters":{"width":0.02},"mode":"APPLY"}},{"tool":"angelo_scene_program",'
                '"arguments":{"mode":"APPLY","operations":[{"kind":"transform","target":"o3","parameters":'
                '{"location":null,"rotation":null,"scale":[1,1,2]}},{"kind":"mesh_bend","target":"o4",'
                '"parameters":{"rise":0.3}}]}}]}')
        for size in (1, 4, len(text)):
            rows = plan_steps.groups(fed(text, size).steps)
            self.assertEqual([row[1] for row in rows][:2], ["angelo_apply_recipe", "transform"], size)
        from angelo_agent import chat_rows
        self.assertEqual(chat_rows.planned_label("mesh_bend", None, 1), "Mesh bend")


class GroupingTests(unittest.TestCase):
    def test_copies_of_one_thing_share_a_stem(self):
        for name, stem in (("Post_FL", "Post"), ("Rail_Front", "Rail"), ("Bridge Rail Left", "Bridge Rail"),
                           ("Lamp 01", "Lamp"), ("Lamp01", "Lamp"), ("Pillar.A", "Pillar"), ("Bridge Deck", None),
                           ("Deck", None), ("", None), (None, None), ("42", None)):
            self.assertEqual(plan_steps.stem(name), stem, name)

    def test_only_consecutive_steps_of_one_family_group(self):
        steps = [["create_primitive", "Post_A", True], ["material_principled", "Post_B", True],
                 ["create_primitive", "Post_C", True], ["create_primitive", "Post_D", True],
                 ["transform", None, False], ["transform", None, False], ["modifier_add", "Bevel", True]]
        self.assertEqual(plan_steps.groups(steps), [
            ("create", "create_primitive", "Post_A", 1), ("material", "material_principled", "Post_B", 1),
            ("create", "create_primitive", "Post", 2), ("edit", "transform", None, 2),
            ("edit", "modifier_add", "Bevel", 1)])

    def test_the_live_row_shows_at_most_five_rows_and_counts_the_rest(self):
        progress = SimpleNamespace(steps=[["create_primitive", name, True] for name in ("Arch", "Beam", "Cable", "Deck")]
                                   + [["create_primitive", "Bridge Deck", True]] * 2, more_steps=3)
        rows, more = plan_steps.rows("x" + plan_progress.SEPARATOR + plan_steps.encode(progress))
        self.assertEqual([name for _, _, name, _ in rows], ["Arch", "Beam", "Cable", "Deck"])
        self.assertEqual(more, 2 + 3, "The two bridge decks and the three steps only counted")
        self.assertEqual(plan_steps.rows("Thinking"), ([], 0))
        self.assertEqual(plan_steps.rows(plan_progress.status("Thinking", "Planning the bridge")), ([], 0))


class CarryTests(unittest.TestCase):
    def test_the_steps_ride_on_the_status_after_its_detail(self):
        progress = fed(TEXT, 50)
        text = plan_progress.status(STATUS_PLAN, progress.detail(), plan_steps.encode(progress))
        self.assertEqual(plan_progress.split(text), (STATUS_PLAN, "Post_FL, Post_FR, Post_BL +4"),
                         "The detail never includes the steps")
        self.assertEqual(plan_steps.rows(text), (ROWS, 0))
        self.assertEqual(plan_progress.status(STATUS_PLAN, "", ""), STATUS_PLAN)

    def test_the_live_block_draws_the_rows_under_the_plain_label(self):
        from angelo_agent import chat_rows
        progress = fed(TEXT, 50)
        controller = SimpleNamespace(voice=None, voice_status="", status=plan_progress.status(
            STATUS_PLAN, progress.detail(), plan_steps.encode(progress)))
        role, message, _, height, key, _ = chat_rows.live_block(controller)
        self.assertEqual((role, key, message["text"]), ("live", "live", "Preparing the next steps"),
                         "The rows show the names: the label drops them")
        self.assertEqual(message["planned"], tuple(ROWS))
        self.assertEqual(height, chat_rows.LIVE + 4 * chat_rows.PLANNED + 6)
        self.assertEqual([chat_rows.planned_label(kind, name, count) for _, kind, name, count in ROWS],
                         ["Post ×4", "Rail ×2", "Grey Stone", ROWS[3][2]])
        self.assertEqual(chat_rows.planned_label("transform", None, 2), "Transform ×2")
        self.assertEqual(chat_rows.planned_label("angelo_apply_recipe", None, 1), "Recipe edit")
        controller.status = plan_progress.status(STATUS_PLAN, "Bridge Deck +2")
        self.assertEqual(chat_rows.live_block(controller)[1]["text"], "Preparing the next steps · Bridge Deck +2")
        self.assertEqual(chat_rows.live_block(controller)[3], chat_rows.LIVE, "No rows: the row as before")
        controller.voice, controller.voice_status = object(), "Listening"
        self.assertEqual(chat_rows.live_block(controller)[1]["planned"], ())


def events(stream, payloads, plans=None):
    out = []
    for payload in payloads:
        event = payload if "type" in payload and payload["type"] in ("user", "result", "assistant") else {
            "type": "stream_event", "event": payload}
        out += stream.events(event, plans if plans is not None else {})
    return out


def plan_call(index, tool_id):
    return {"type": "content_block_start", "index": index,
            "content_block": {"type": "tool_use", "name": "StructuredOutput", "id": tool_id}}


def deltas(index, text, size):
    return [{"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta",
                                                                       "partial_json": text[start:start + size]}}
            for start in range(0, len(text), size)]


class ClaudeStreamTests(unittest.TestCase):
    def statuses(self, emits):
        return [data["text"] for kind, data in emits if kind == "status"]

    def test_the_first_row_shows_at_once_and_later_changes_at_most_twice_a_second(self):
        stream, clock = RoundStream(), [100.]
        with patch.object(claude_stream.time, "monotonic", side_effect=lambda: clock[0]):
            shown = self.statuses(events(stream, [{"type": "message_start", "message": {"id": "m1", "usage": {}}},
                                                  plan_call(0, "t0")]))
            for delta in deltas(0, TEXT, 12):
                clock[0] += 0.05  # About 240 characters a second, as Opus 5.5 wrote this plan.
                shown += self.statuses(events(stream, [delta]))
        self.assertEqual(shown[:2], [STATUS_THINKING, STATUS_PLAN])
        rows = [plan_steps.rows(text)[0] for text in shown[2:]]
        self.assertEqual(rows[0], [("create", "create_primitive", "Post_FL", 1)], "The first object, at once")
        self.assertEqual(rows[-1], ROWS)
        self.assertLessEqual(len(rows), len(TEXT) / 12 * 0.05 / claude_stream.THINKING_SECONDS + 2)

    def test_a_refused_plan_call_takes_its_rows_away_and_a_later_call_starts_over(self):
        stream, plans = RoundStream(), {}
        emits = events(stream, [{"type": "message_start", "message": {"id": "m1", "usage": {}}}, plan_call(0, "t0")]
                       + deltas(0, TEXT, 40) + [{"type": "content_block_stop", "index": 0}], plans)
        self.assertTrue(plan_steps.rows(self.statuses(emits)[-1])[0])
        events(stream, [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "StructuredOutput", "id": "t0", "input": FOOTBRIDGE}]}}], plans)
        refused = events(stream, [{"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t0", "is_error": True,
             "content": "Output does not match required schema: /operations: bad"}]}}], plans)
        self.assertEqual(self.statuses(refused), [STATUS_THINKING], "Nothing of a refused plan stays on screen")
        self.assertEqual(plans, {})
        lookup = json.dumps({"operations": [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
            {"kind": "light_create", "parameters": {"name": "Key Light"}}]}}]})
        later = self.statuses(events(stream, [plan_call(1, "t1")] + deltas(1, lookup, 400), plans))
        self.assertEqual(later[0], STATUS_PLAN)
        self.assertEqual(plan_steps.rows(later[-1])[0], [("create", "light_create", "Key Light", 1)],
                         "A later call's rows start over")
        self.assertEqual(self.statuses(events(stream, [{"type": "result", "subtype": "success"}], plans)),
                         [STATUS_THINKING], "The round's end ends them in any case")

    def test_another_tools_error_leaves_the_rows(self):
        stream, plans = RoundStream(), {}
        events(stream, [{"type": "message_start", "message": {"id": "m1", "usage": {}}}, plan_call(0, "t0")]
               + deltas(0, TEXT[:400], 40), plans)
        emits = events(stream, [{"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "web1", "is_error": True, "content": "fetch failed"}]}}], plans)
        self.assertEqual(self.statuses(emits), [])


class CodexStreamTests(unittest.TestCase):
    def test_rows_stream_from_the_message_and_go_when_it_is_complete(self):
        turns, emitted, clock = codex_plan.PlanTurns(), [], [50.]
        strict = strict_text(CODEX)

        def emit(kind, data):
            emitted.append((kind, data))
        with patch.object(codex_plan.time, "monotonic", side_effect=lambda: clock[0]):
            for start in range(0, len(strict), 30):
                clock[0] += 0.12
                turns.reply_delta("m1", strict[start:start + 30], emit)
            self.assertEqual(turns.message_completed("m1", strict, emit), "")
        statuses = [data["text"] for kind, data in emitted if kind == "status"]
        self.assertEqual(plan_steps.rows(statuses[0])[0], [("create", "create_primitive", "Post_FL", 1)])
        self.assertEqual(plan_steps.rows(statuses[-2])[0], ROWS[:3])
        self.assertEqual(statuses[-1], STATUS_THINKING, "A complete message's steps become rows only when they run")
        quiet = []
        turns.reply_delta("m2", '{"reply":"Hi","operations":[]}', lambda *item: quiet.append(item))
        turns.message_completed("m2", '{"reply":"Hi","operations":[]}', lambda *item: quiet.append(item))
        self.assertEqual(quiet, [], "An answer shows no rows and takes nothing away")


class ControllerTests(unittest.TestCase):
    def setUp(self):
        from angelo_agent.controller import Controller
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name, session_id=str(uuid.uuid4()))
        self.c.set_document(("plan-steps", 1))

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def test_a_key_in_a_step_is_never_drawn_and_nothing_of_the_rows_is_kept(self):
        self.c.api_key = KEY
        thread = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
        keyed = json.dumps({"operations": [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
            {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "Sign " + KEY}}]}}]})
        progress, states = PlanProgress(), []
        for start in range(0, len(keyed), 5):  # Every state of the key's arrival: only a whole value is carried.
            progress.feed(keyed[start:start + 5])
            states.append(plan_progress.status(STATUS_PLAN, progress.detail(), plan_steps.encode(progress)))
            self.assertTrue(KEY in states[-1] or not any(KEY[i:i + 5] in states[-1] for i in range(len(KEY) - 4)), start)
        self.assertIn(KEY, states[-1])
        with patch("angelo_agent.controller.threading.Thread", return_value=thread):
            self.c.send("Add a sign")
            for text in states:
                self.c.emit("status", {"text": text})
                self.c.pump_events()
                self.assertFalse(any(KEY[i:i + 5] in self.c.status for i in range(len(KEY) - 4)))
            self.assertEqual(plan_steps.rows(self.c.status)[0], [("create", "create_primitive", "Sign [REDACTED]", 1)])
            self.assertEqual(plan_progress.split(self.c.status)[1], "Sign [REDACTED]")
            self.c.emit("delta", {"item": "reply", "text": "Done."})
            self.c.emit("finished", {"status": "completed"})
            self.c.pump_events()
        self.assertNotIn("Sign", json.dumps(self.c.state), "Nothing of the rows is ever stored")
        self.assertEqual(plan_steps.rows(self.c.status), ([], 0), "The request's end ends them")


class ProgressWatchTests(unittest.TestCase):
    """The live-eval session's polls of the status the live row draws (eval_receipts.ProgressWatch)."""

    def test_each_first_is_kept_from_the_send(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from eval_receipts import ProgressWatch
        watch, progress = ProgressWatch(10.0), fed(TEXT, 300)
        for now, status in ((10.4, "Thinking"), (10.9, "Provider busy — retrying" + plan_progress.SEPARATOR + "9"),
                            (13.0, STATUS_PLAN), (13.25, plan_progress.status(STATUS_PLAN, progress.detail(),
                                                                              plan_steps.encode(progress))),
                            (14.0, plan_progress.status(STATUS_PLAN, "Bridge Deck")), (15.0, "Thinking")):
            watch.observe(status, now)
        watch.observe("Thinking", 15.5, rows=[{"role": "user"}, {"role": "activity", "tool": "angelo_scene_program"}])
        self.assertEqual(watch.record(), {"first_plan_ms": 3000.0, "first_rows_ms": 3250.0, "first_progress_ms": 3250.0,
                                          "first_step_ms": 5500.0, "first_plan_row_ms": 3250.0,
                                          "longest_still_ms": 2100.0})
        titled = ProgressWatch(0.0)
        titled.observe(plan_progress.status("Thinking", "Planning the bridge"), 2.5)
        self.assertEqual(titled.record(), {"first_progress_ms": 2500.0, "longest_still_ms": 2500.0})


if __name__ == "__main__":
    unittest.main()
