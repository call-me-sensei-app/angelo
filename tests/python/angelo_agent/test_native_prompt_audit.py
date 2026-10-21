# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The prompt audit (scripts/angelo/prompt_audit.py, doc/angelo/prompt_audit.rst) on checked-in recordings.

``prompt_audit_fixtures`` holds recorded-request shapes in the formats trials keep (Claude Code ``api-request-shape``
transcripts, Codex rollouts, launch records) with invented content: one per route kind, a Claude plan recorded on two
builds with and without a fact, runtime text with a personal value in it, and a session that is not Angelo's.
"""

import copy
import io
import itertools
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().with_name("prompt_audit_fixtures")
sys.path.insert(0, str(ROOT / "scripts/angelo"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import prompt_audit  # noqa: E402
import prompt_recordings  # noqa: E402

# Facts for the fixture's invented texts; the routes, markers and aliases are the real registry's.
TEST_FACTS = [
    {"id": "test.sky", "anchors": ["sky_type is SINGLE_SCATTERING, MULTIPLE_SCATTERING"], "routes": ["agent"],
     "review": ["mcp"], "stale": ["\\bNISHITA\\b"]},
    {"id": "test.engine", "anchors": ["render.engine is BLENDER_EEVEE"], "routes": [], "review": ["agent"],
     "stale": ["\\bBLENDER_EEVEE_NEXT\\b"]},
    {"id": "test.identity", "anchors": ["You are Angelo"], "routes": ["agent"]},
    {"id": "test.handles", "anchors": [["handles or $aliases, never names", "Target = o<n> handle or $alias"]],
     "routes": ["short-ids"]},
]


def fixture_registry():
    registry = copy.deepcopy(prompt_audit.load_registry())
    registry["facts"] = copy.deepcopy(TEST_FACTS)
    registry["turn_facts"] = [{"id": "test.image_note", "when": "image", "anchors": ["Attached images:"],
                               "routes": ["agent"]}]
    registry["foreign_absent"] = []
    return registry


def fixture_rows(registry=None):
    registry = registry or fixture_registry()
    return prompt_audit.audit(prompt_recordings.recordings([FIXTURES], registry["openings"]), registry)


def by_path(rows):
    return {str(Path(row["recording"]["path"]).relative_to(FIXTURES)): row for row in rows}


class Registry(unittest.TestCase):
    def test_the_registry_is_well_formed(self):
        registry = prompt_audit.load_registry()
        self.assertEqual(prompt_audit.registry_problems(registry), [])
        self.assertIn("blender.sky_type_values", {fact["id"] for fact in registry["facts"]})

    def test_a_malformed_registry_is_refused(self):
        registry = fixture_registry()
        registry["facts"].append(dict(TEST_FACTS[0]))
        registry["facts"].append({"id": "test.nowhere", "anchors": ["x"], "routes": ["no-such-route"]})
        problems = prompt_audit.registry_problems(registry)
        self.assertIn("fact test.sky is listed 2 times", problems)
        self.assertIn("fact test.nowhere names unknown route no-such-route", problems)
        registry["own_text_only"] = ["no-such-route"]
        self.assertIn("own_text_only names unknown route no-such-route", prompt_audit.registry_problems(registry))

    def test_every_anchor_is_stated_by_some_current_text(self):
        # A reworded sentence must update its fact here, or the audit would fail on a fact no text states any more.
        texts = current_texts()
        for fact in prompt_audit.load_registry()["facts"]:
            with self.subTest(fact=fact["id"]):
                self.assertTrue(any(prompt_audit.fact_present(fact, text) for text in texts))


def current_texts():
    """Every route's text and tools as the source assembles them today, in the audit's received form."""
    import prompt_sources
    return [prompt_audit.received(prompt_sources.recording(text, tools))
            for _route, _variant, text, tools in prompt_sources.assemble()]


class Routes(unittest.TestCase):
    def test_each_recording_is_classified_from_what_it_holds(self):
        routes = {path: row["route"] for path, row in by_path(fixture_rows()).items()}
        self.assertEqual(routes, {
            "claude-plan/angelo-trial-01-transcript.jsonl": "claude-plan",
            "claude-plan/angelo-trial-02-transcript.jsonl": "claude-plan",
            "claude-direct/angelo-trial-01-transcript.jsonl": "claude-direct",
            "codex/angelo-trial-01-rollouts/rollout-2026-10-20T02-00-00-aaaa.jsonl": "codex-deferred-plan",
            "voice/angelo-trial-01-rollouts/rollout-2026-10-20T02-00-00-bbbb.jsonl": "voice",
            "studio/angelo-trial-01-transcript.jsonl": "studio-claude",
            "studio/angelo-trial-02-transcript.jsonl": "studio-claude",
            "mcp-client/mcp-trial-01-transcript.jsonl": "mcp-claude-code",
            "probe/mcp-claude-code/requests.jsonl": "mcp-claude-code",
            "probe/mcp-codex/requests.jsonl": "mcp-codex",   # holds nothing of Angelo's: the probe names its route
            "probe/codex-full/requests.jsonl": "codex-full-chat-long",
            "probe/collaborator-claude/requests.jsonl": "collaborator-claude",   # Angelo's text + the delegation's
        })   # other/: not an Angelo request, skipped; probe/codex-full's rollout: the body beside it is read instead

    def test_the_build_is_the_launch_record_s_commit_or_add_on_digest(self):
        rows = by_path(fixture_rows())
        self.assertEqual(rows["claude-plan/angelo-trial-01-transcript.jsonl"]["recording"]["build"], "1111111aaaa")
        self.assertEqual(rows["codex/angelo-trial-01-rollouts/rollout-2026-10-20T02-00-00-aaaa.jsonl"]
                         ["recording"]["build"], "addon:c0dexd1gest0")
        self.assertIsNone(rows["studio/angelo-trial-01-transcript.jsonl"]["recording"]["build"])


class Facts(unittest.TestCase):
    def test_a_route_missing_a_required_fact_fails_naming_route_fact_and_recording(self):
        rows = by_path(fixture_rows())
        self.assertEqual(rows["claude-plan/angelo-trial-01-transcript.jsonl"]["missing"], ["test.sky"])
        self.assertEqual(rows["claude-plan/angelo-trial-02-transcript.jsonl"]["missing"], [])
        # Wrapped at another column in the recording: whitespace never decides.
        self.assertEqual(rows["codex/angelo-trial-01-rollouts/rollout-2026-10-20T02-00-00-aaaa.jsonl"]["missing"], [])
        # A fact for no route of this kind is not required: Studio's editor and the MCP client fail nothing.
        self.assertEqual(rows["studio/angelo-trial-01-transcript.jsonl"]["missing"], [])
        self.assertEqual(rows["mcp-client/mcp-trial-01-transcript.jsonl"]["review"], ["test.sky"])
        with tempfile.TemporaryDirectory() as folder:
            registry = Path(folder) / "registry.json"
            registry.write_text(json.dumps(fixture_registry()))
            err, out = io.StringIO(), io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                code = prompt_audit.main(["check", str(FIXTURES), "--registry", str(registry)])
        self.assertEqual(code, 1)
        self.assertIn("claude-plan @ 1111111aaaa lacks test.sky in 1 recordings (1 trials), e.g. ", err.getvalue())
        self.assertIn("claude-plan/angelo-trial-01-transcript.jsonl#0", err.getvalue())

    def test_a_build_filter_audits_only_that_build(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = Path(folder) / "registry.json"
            registry.write_text(json.dumps(fixture_registry()))
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(prompt_audit.main(["check", str(FIXTURES), "--registry", str(registry),
                                                    "--build", "2222222"]), 0)

    def test_a_route_that_reads_only_its_own_text_fails_on_another_route_s_fact(self):
        # Studio's editor once received the chat agent's plan text: every registered fact
        # meant only for other routes is a leak there, whichever fact it is.
        rows = by_path(fixture_rows())
        self.assertEqual(rows["studio/angelo-trial-02-transcript.jsonl"]["leaked"], ["test.sky", "test.handles"])
        self.assertEqual(rows["studio/angelo-trial-01-transcript.jsonl"]["leaked"], [])
        # Only own-text routes: a chat route holding a fact meant for another route is not a leak.
        self.assertEqual([path for path, row in rows.items() if row["leaked"] and not path.startswith("studio/")], [])
        with tempfile.TemporaryDirectory() as folder:
            registry = Path(folder) / "registry.json"
            registry.write_text(json.dumps(fixture_registry()))
            err, out = io.StringIO(), io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                code = prompt_audit.main(["check", str(FIXTURES / "studio"), "--registry", str(registry)])
        self.assertEqual(code, 1)
        self.assertIn("studio-claude @ unknown reads only its own text but received test.sky in 1 recordings",
                      err.getvalue())

    def test_stale_names_in_scripts_are_counted_as_evidence(self):
        rows = by_path(fixture_rows())
        self.assertEqual(rows["claude-plan/angelo-trial-01-transcript.jsonl"]["stale"], {"test.sky": ["NISHITA"]})
        codex = rows["codex/angelo-trial-01-rollouts/rollout-2026-10-20T02-00-00-aaaa.jsonl"]
        self.assertEqual(codex["stale"], {"test.engine": ["BLENDER_EEVEE_NEXT"]})
        self.assertEqual(codex["review"], ["test.engine"])
        table, control = prompt_audit.gaps(fixture_rows(), "missing")
        entry = table[("claude-plan", "1111111aaaa", "test.sky")]
        self.assertEqual((entry["recordings"], entry["scripted"], entry["stale"]), (1, 1, 1))
        self.assertEqual(control[("claude-test-1", "test.sky")], [2, 0])   # trial 02 and direct: had it, no NISHITA


class ProbeRequests(unittest.TestCase):
    def test_an_mcp_client_s_server_section_is_angelo_s_text_and_the_rest_is_foreign(self):
        recording = by_path(fixture_rows())["probe/mcp-claude-code/requests.jsonl"]["recording"]
        self.assertTrue(recording["angelo_text"].startswith("Angelo (Hyperbond Studio) is a 3D app"))
        self.assertTrue(recording["angelo_text"].rstrip().endswith("never names."))   # up to the next server
        self.assertEqual([tool["name"] for tool in recording["angelo_tools"]], ["mcp__angelo__angelo_view"])
        self.assertEqual([tool["name"] for tool in recording["foreign_tools"]], ["Bash"])
        self.assertIn("<Angelo's server instructions>", recording["injected"][0])
        self.assertIn("Another server's text.", recording["injected"][0])
        self.assertEqual(recording["build"], "3333333cccc")

    def test_a_codex_request_body_splits_developer_text_and_the_runtime_s_tools(self):
        rows = by_path(fixture_rows())
        full = rows["probe/codex-full/requests.jsonl"]
        self.assertTrue(full["recording"]["angelo_text"].startswith("You are Angelo"))
        self.assertEqual(full["missing"], [])   # the fact wraps at another column
        self.assertEqual([tool["name"] for tool in full["recording"]["angelo_tools"]], ["angelo_execute_python"])
        self.assertEqual([tool["name"] for tool in full["recording"]["foreign_tools"]], ["request_user_input"])
        client = rows["probe/mcp-codex/requests.jsonl"]
        self.assertEqual(client["recording"]["angelo_text"], "")
        self.assertEqual([tool["name"] for tool in client["recording"]["foreign_tools"]], ["collaboration.spawn_agent"])
        self.assertIn("You are the runtime's own agent.", client["recording"]["foreign_blocks"])
        self.assertEqual(client["review"], ["test.sky"])   # nothing of Angelo's reached it: every MCP fact reported


class SourceAndRelease(unittest.TestCase):
    def test_every_route_assembled_from_source_has_its_facts_and_no_other_route_s(self):
        # Per route, not "some text states it": a Codex-only fact fails every Claude route.
        problems = prompt_audit.audit_sources(prompt_audit.load_registry())
        self.assertEqual(["%s (%s) %s %s" % problem for problem in problems], [])

    def test_runtime_text_angelo_turns_off_fails_where_it_still_arrives(self):
        registry = fixture_registry()
        registry["foreign_absent"] = [{"id": "test.advisor", "runtime": "claude", "routes": ["agent"], "tools": ["advisor"]}]
        rows = by_path(fixture_rows(registry))
        self.assertEqual(rows["claude-plan/angelo-trial-01-transcript.jsonl"]["foreign"], ["test.advisor"])
        self.assertEqual(rows["probe/mcp-codex/requests.jsonl"]["foreign"], [])   # an MCP client's runtime is the person's
        self.assertEqual(rows["probe/collaborator-claude/requests.jsonl"]["foreign"], [])

    def test_a_turn_fact_is_required_only_when_its_condition_holds(self):
        rows = by_path(fixture_rows())
        self.assertIn("test.image_note", rows["probe/collaborator-claude/requests.jsonl"]["missing"])   # an image, no note
        self.assertNotIn("test.image_note", rows["claude-plan/angelo-trial-02-transcript.jsonl"]["missing"])

    def test_a_release_route_with_no_recording_fails(self):
        registry = fixture_registry()
        registry["release_routes"] = ["claude-plan", "voice", "studio-codex"]
        rows = fixture_rows(registry)
        self.assertEqual(prompt_audit.coverage(rows, registry, []), [("studio-codex", None)])
        self.assertEqual(prompt_audit.coverage(rows, registry, ["1111111"]),
                         [("studio-codex", "1111111"), ("voice", "1111111")])


class ForeignText(unittest.TestCase):
    def test_claude_code_s_own_prompt_tools_and_injected_context_are_foreign(self):
        recording = by_path(fixture_rows())["claude-plan/angelo-trial-01-transcript.jsonl"]["recording"]
        self.assertTrue(recording["angelo_text"].startswith("You are Angelo"))
        foreign = "\n".join(recording["foreign_blocks"])
        self.assertIn("Runtime identity line", foreign)
        self.assertIn("Runtime environment paragraph.", foreign)   # same block, before Angelo's text
        self.assertNotIn("You are Angelo", foreign)
        self.assertEqual([tool["name"] for tool in recording["foreign_tools"]], ["WebSearch", "StructuredOutput", "advisor"])
        self.assertEqual(recording["angelo_tools"][0]["input_schema"]["description"],
                         "Angelo plan schema: object handle or $alias.")
        self.assertIn("pat@example.com", recording["injected"][0])

    def test_codex_developer_and_context_messages_are_foreign(self):
        rows = by_path(fixture_rows())
        recording = rows["codex/angelo-trial-01-rollouts/rollout-2026-10-20T02-00-00-aaaa.jsonl"]["recording"]
        self.assertIn("you may start helper agents", recording["foreign_blocks"][0])
        self.assertTrue(recording["injected"][0].startswith("<environment_context>"))
        self.assertEqual([tool["name"] for tool in recording["angelo_tools"]], ["angelo_execute_python"])

    def test_foreign_text_is_diffed_per_runtime_version_with_personal_values_masked(self):
        versions = prompt_audit.foreign_inventory(fixture_rows())
        old, new = versions[("claude", "9.9.1")], versions[("claude", "9.9.2")]
        self.assertEqual([p for p in new["paragraphs"] if p not in old["paragraphs"]],
                         ["Runtime paragraph added in 9.9.2."])
        text = json.dumps({key: list(value["paragraphs"]) + list(value["injected"]) for key, value in
                           ((str(k), v) for k, v in versions.items())})
        self.assertNotIn("pat@example.com", text)
        self.assertNotIn("/Users/pat", text)
        self.assertNotIn("Users\\\\pat", text)
        self.assertIn("<email>", text)
        with tempfile.TemporaryDirectory() as folder:
            report, foreign = Path(folder) / "report.md", Path(folder) / "foreign.txt"
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                prompt_audit.main(["check", str(FIXTURES), "--report", str(report), "--foreign", str(foreign)])
            for path in (report, foreign):
                self.assertNotIn("pat@example.com", path.read_text())
            self.assertIn("1 paragraphs added, 0 removed", report.read_text())
            self.assertIn("===== claude 9.9.3: system text", foreign.read_text())   # the latest version only
            self.assertIn("===== codex 9.9.3: system text", foreign.read_text())
            self.assertNotIn("9.9.1", foreign.read_text())


class Digests(unittest.TestCase):
    def test_each_route_s_received_text_is_digested_per_build_and_drift_shows(self):
        table = prompt_audit.digests(fixture_rows())
        plan = table["claude-plan"]
        self.assertEqual(sorted(plan), ["1111111aaaa", "2222222bbbb"])
        self.assertNotEqual(set(plan["1111111aaaa"]["angelo_text"]), set(plan["2222222bbbb"]["angelo_text"]))
        self.assertEqual(prompt_audit.drift(table, table), [])
        baseline = json.loads(json.dumps(table))
        baseline["claude-plan"]["2222222bbbb"]["foreign"] = {"000000000000": 1}
        del baseline["voice"]
        lines = prompt_audit.drift(table, baseline)
        self.assertIn("new: voice @ unknown", lines)
        self.assertTrue(any(line.startswith("drift: claude-plan @ 2222222bbbb foreign") for line in lines))


class ReleaseCheck(unittest.TestCase):
    def test_release_checks_runs_the_audit(self):
        import release_checks
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            # The real registry: the fixture's invented texts lack its facts, so the check fails.
            self.assertEqual(release_checks.main(["prompt-audit", str(FIXTURES)]), 1)


if __name__ == "__main__":
    unittest.main()
