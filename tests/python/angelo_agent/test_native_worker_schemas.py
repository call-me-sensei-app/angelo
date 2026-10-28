# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The schemas a lead's chat looked up in the job go with each worker's brief (``worker_schemas``), without Blender.

Pinned here, through the brief ``worker_sessions._adopt`` sends: a lookup the lead made in the job that started the
part, after the start too, reaches the worker as its own lookup would return it (the model's copy, compact JSON, ASCII
escaped on Codex and Claude's plan, unescaped on Claude's direct-tools route), one lookup per family in the order the
lead resolved the names; a lookup of the person's earlier or later request, a failed one, a collaborator's, a name the
lead's answer did not know, and a name the worker is not offered (the worker family, Python, a core tool already
declared to it) never does; the block stays within ``MAX_BYTES``, taking names in order while it fits, so one too large
on its own never crowds out the rest. The worker Blender's own lookup runs the same function the block's answers come
from. Evidence: the 2026-10-25 dev check, where both workers spent their first request on a lookup and worker 1 looked
up ``shader_nodes``, which its lead had looked up in the same job.
"""

import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import parallel_access, parallel_sessions, worker_routing, worker_sessions  # noqa: E402
from angelo_agent.plan_schema import lookup  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402

CAPABILITIES = "angelo_agent_capabilities"
PART = {"name": "Living room seating", "brief": "Build the sofa and two armchairs."}
RUNTIME = {"runtime_features": {"opensubdiv": True}, "unavailable_in_this_build": []}   # 71 B, as the dev check's.


def capabilities(family, names=None):
    """``blender_tools.agent_capabilities`` for names, outside Blender, plus a timing field a model never reads."""
    answer = {"toolset_version": TOOLSET_VERSION, "blender_version": "5.2.2 LTS", "scene_changed": False,
              "permissions": "Discovery does not grant permission; the configured host approval policy still applies.",
              **lookup(family, names), "native_exec_ms": 4}
    return dict(answer, runtime=RUNTIME) if family == "ACTIONS" else answer


def form(family, names, ascii_text=True):
    """A worker's own lookup as its model reads it: the call, then the answer without the host's timing."""
    answer = {key: value for key, value in capabilities(family, names).items() if key != "native_exec_ms"}
    return "\n%s %s\n%s" % (CAPABILITIES, json.dumps({"family": family, "names": names}, separators=(",", ":")),
                            json.dumps(answer, ensure_ascii=ascii_text, separators=(",", ":")))


def user(text):
    return {"role": "user", "text": text}


def activity(tool, arguments, result=None, status="completed", **extra):
    return dict({"role": "activity", "call_id": str(uuid.uuid4()), "text": "", "tool": tool, "arguments": arguments,
                 "status": status, "result": result if result is not None else {"success": True}}, **extra)


def looked_up(family, names, status="completed", **extra):
    result = dict(capabilities(family, names), native_dispatch_wait_ms=1)
    if status != "completed":
        result = {"error": "The host stopped it", "error_code": "CANCELLED"}
    return activity(CAPABILITIES, {"family": family, "names": names}, result, status, **extra)


def started(*parts):
    return activity("angelo_workers_start", {"parts": [dict(part) for part in parts]}, {"success": True, "starting": []})


def lead_of(rows):
    return SimpleNamespace(state={"id": "lead-1", "provider": "codex_subscription", "messages": rows},
                           references=None)


def brief(lead, part=PART, provider="codex_subscription", environ=None, answers=capabilities):
    """The text ``worker_sessions._adopt`` sends as the worker's first message."""
    sent = {}
    worker = SimpleNamespace(state={"id": "worker-1", "provider": provider, "messages": []}, attachments=[])
    item = {"worker": SimpleNamespace(part="/nowhere/part.blend"), "lead": lead, "part": part, "number": 1,
            "error": None, "references": []}
    with mock.patch.object(worker_sessions, "_new_chat", return_value=worker), \
            mock.patch.object(worker_routing, "adopt"), mock.patch.object(parallel_sessions, "note_active"), \
            mock.patch.object(worker_sessions, "_send", side_effect=lambda chat, text, key: sent.update(text=text)), \
            mock.patch.object(parallel_access, "enabled", return_value=True), \
            mock.patch.dict(worker_sessions._started, clear=False), mock.patch.dict(worker_sessions._parts), \
            mock.patch.dict(parallel_sessions.names), mock.patch.dict(os.environ, environ or {}), \
            mock.patch.dict(sys.modules, {"angelo_agent.blender_tools": SimpleNamespace(agent_capabilities=answers)}):
        worker_sessions._started.pop("lead-1", None)
        worker_sessions._adopt(item)
    return sent["text"]


class JobLookupTests(unittest.TestCase):
    def assertHas(self, needle, text):   # Says what is missing without printing the whole brief.
        self.assertTrue(needle in text, "missing %r... in a brief ending %r" % (needle[:90], text[-160:]))

    def test_a_lead_lookup_of_the_job_reaches_the_worker_brief_as_the_workers_own_lookup_returns_it(self):
        # The dev check's lead: the worker family's tools (never a worker's), its start, then shader_nodes.
        lead = lead_of([user("A furnished living room. Split it across workers."),
                        looked_up("TOOLS", ["angelo_workers_start", "angelo_workers_status", "angelo_workers_merge"]),
                        started(PART), looked_up("ACTIONS", ["shader_nodes"])])
        text = brief(lead)
        expected = ("\n\nThe lookups already made in this job (each angelo_agent_capabilities call and its answer):"
                    + form("ACTIONS", ["shader_nodes"]))
        self.assertHas(expected, text)
        self.assertTrue(text.endswith(expected))
        self.assertNotIn("native_exec_ms", text)   # The model's copy (model_results), never the host's timing.
        self.assertNotIn("angelo_workers_start", text.split("The lookups already made")[1])
        self.assertLessEqual(len(expected.encode()), 24000)

    def test_only_the_job_that_started_the_part_counts_never_an_earlier_or_later_request(self):
        lead = lead_of([user("Make a chair."), looked_up("ACTIONS", ["modifier_add"]), {"role": "assistant"},
                        user("A living room."), started(PART), looked_up("ACTIONS", ["shader_nodes"]),
                        user("Now make it blue."), looked_up("ACTIONS", ["material_principled"])])
        text = brief(lead)
        self.assertHas(form("ACTIONS", ["shader_nodes"]), text)
        for name in ("modifier_add", "material_principled"):   # The person's earlier and next requests.
            self.assertNotIn('"%s"' % name, text)

    def test_a_failed_a_collaborators_an_unknown_and_an_unoffered_lookup_never_go(self):
        deferred_check = "aim"
        lead = lead_of([user("A living room."), started(PART),
                        looked_up("ACTIONS", ["geometry_nodes"], status="failed"),
                        looked_up("ACTIONS", ["nla_edit"], parent_call="delegate-1", collaborator="Claude"),
                        looked_up("ACTIONS", ["shader_nodes", "no_such_kind"]),
                        looked_up("TOOLS", ["angelo_execute_python", "angelo_view", "angelo_scene_settings"]),
                        looked_up("CHECKS", [deferred_check]), looked_up("ACTIONS", ["shader_nodes"])])
        text = brief(lead)
        self.assertHas("\n\nThe lookups already made in this job", text)
        block = text.split("\n\nThe lookups already made in this job")[1]
        self.assertEqual(block, " (each angelo_agent_capabilities call and its answer):"
                         + form("ACTIONS", ["shader_nodes"]) + form("TOOLS", ["angelo_scene_settings"])
                         + form("CHECKS", [deferred_check]))
        for name in ("geometry_nodes", "nla_edit", "no_such_kind", "angelo_execute_python"):
            self.assertNotIn('"%s"' % name, block)

    def test_a_start_not_yet_in_the_rows_reads_the_leads_current_request(self):
        # A background Blender adopts inside the start call, before its row is stored.
        lead = lead_of([user("Make a chair."), looked_up("ACTIONS", ["modifier_add"]),
                        user("A living room."), looked_up("ACTIONS", ["shader_nodes"])])
        text = brief(lead)
        self.assertHas(form("ACTIONS", ["shader_nodes"]), text)
        self.assertNotIn('"modifier_add"', text)

    def test_nothing_looked_up_leaves_the_brief_as_it_was(self):
        lead = lead_of([user("A living room."), started(PART)])
        self.assertNotIn("The lookups already made", brief(lead))

    def test_each_runtime_reads_the_answer_in_its_own_form(self):
        def answers(family, names=None):
            return dict(capabilities(family, names), permissions="Discovery \u00b7 never permission")
        lead = lead_of([user("A living room."), started(PART), looked_up("ACTIONS", ["shader_nodes"])])
        escaped = brief(lead, answers=answers)   # Codex: json.dumps' default.
        self.assertHas("Discovery \\u00b7 never permission", escaped)
        plan = brief(lead, provider="claude_subscription", environ={"ANGELO_CLAUDE_DIRECT_TOOLS": "0"},
                     answers=answers)   # Claude's typed plan: its continuation is json.dumps' default too.
        self.assertHas("Discovery \\u00b7 never permission", plan)
        direct = brief(lead, provider="claude_subscription", environ={"ANGELO_CLAUDE_DIRECT_TOOLS": "1"},
                       answers=answers)   # Claude's direct-tools route: ensure_ascii=False.
        self.assertHas("Discovery \u00b7 never permission", direct)

    def test_the_block_stays_within_its_bound_taking_names_in_order_while_it_fits(self):
        from angelo_agent.action_schemas import ACTION_SCHEMAS
        kinds = list(ACTION_SCHEMAS)
        lead = lead_of([user("A living room."), started(PART), looked_up("ACTIONS", kinds[:32]),
                        looked_up("ACTIONS", kinds[32:64]), looked_up("TOOLS", ["angelo_expand_recipe"])])
        text = brief(lead)
        self.assertHas("\n\nThe lookups already made", text)
        block = text[text.index("\n\nThe lookups already made"):]
        self.assertLessEqual(len(block.encode()), 24000)
        calls = [json.loads(line.split(" ", 1)[1]) for line in block.splitlines() if line.startswith(CAPABILITIES)]
        names = calls[0]["names"]
        self.assertEqual(names, [kind for kind in kinds if kind in names])   # In the order the lead resolved them.
        self.assertLess(len(names), 32)

    def test_one_name_too_large_alone_never_crowds_out_the_rest(self):
        from angelo_agent import worker_schemas
        order = [("TOOLS", "angelo_scene_program"), ("ACTIONS", "shader_nodes")]   # The first about 122 KB alone.
        with mock.patch.dict(sys.modules, {"angelo_agent.blender_tools":
                                           SimpleNamespace(agent_capabilities=capabilities)}):
            text = worker_schemas.render(order, True, limit=12000)
        self.assertEqual(text, worker_schemas.HEADER + form("ACTIONS", ["shader_nodes"]))
        self.assertLessEqual(len(text.encode()), 12000)

    def test_the_answers_come_from_the_function_the_worker_blender_runs_for_its_own_lookup(self):
        executor = (ADDON / "blender_tools.py").read_text(encoding="utf-8")
        self.assertIn("agent_capabilities(arguments['family'],arguments.get('names'),self.studio_context)", executor)
        self.assertIn("from .blender_tools import agent_capabilities", (ADDON / "worker_schemas.py").read_text())


if __name__ == "__main__":
    unittest.main()
