# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an add-on's own records add to the path metrics, and what an older add-on leaves unknown (no Blender).

One harness grades several add-on versions. An add-on whose receipts have a
``path`` section, whose usage counts model requests, whose job records carry
a ``shown`` mark and whose telemetry offers ``running_usage`` is read; one
that predates them keeps every such number "unknown", never a guess. The
shapes here are the ones ``request_path`` documents:

* ``path["images"]``: ``[[provider turn, edits done by then, {kind: count}]]``
  with kinds ``view:<SHADING>:<FRAMING>``, ``job:<id>``, ``sculpt``,
  ``preview``, ``media`` and ``attachment``;
* ``path["steps"]``: ``[[kind, outcome, name, [names], repeats]]`` with kinds
  ``lookup``, ``api``, ``typed`` and ``script``.
"""

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import oneshot_metrics as metrics  # noqa: E402
import oneshot_produced as produced  # noqa: E402
import oneshot_session  # noqa: E402
import oneshot_tables as tables  # noqa: E402
import scenario_session  # noqa: E402
from test_native_oneshot_metrics import RECEIPT, angelo_record, claude_lines, mcp_record  # noqa: E402
from test_native_oneshot_session import ASTRA_USAGE, Controller, session  # noqa: E402

PATH = {
    "edits": 3, "images_delivered": 7,
    "images": [[1, 0, {"attachment": 4}], [3, 2, {"view:SOLID:SCENE_CAMERA": 1}],
               [5, 3, {"view:MATERIAL:SCENE_CAMERA": 1, "job:j1": 1}]],
    "steps": [["lookup", "ok", "modeling", ["add_mesh"], 1],
              ["typed", "invalid", "angelo_scene_program", ["add_mesh", "set_material"], 1],
              ["script", "ok", "", [], 2], ["typed", "ok", "angelo_job_start", [], 1]],
}
SHOWN = {"result_at": 1760000000.0, "image_at": 1760000004.5, "images": 1}


def job(shown="absent", kind="RENDER_STILL", output="renders/footbridge-final.png"):
    record = {"job": "j1", "kind": kind, "status": "done", "reason": None, "seconds": 8.3, "output": output}
    return record if shown == "absent" else dict(record, shown=shown)


def recorded(path=PATH, shown=SHOWN, requests=9, raised=1, runtime="claude", **extra):
    """A trial record of an add-on that records how the request got to its result."""
    receipt = copy.deepcopy(RECEIPT)
    receipt.update(path=copy.deepcopy(path), usage={"requests": requests}, runtime=runtime)
    receipt["counts"]["failure_script"] = raised
    return angelo_record(receipt=receipt, jobs=[job(shown)], **extra)


class PathFactsTests(unittest.TestCase):
    def test_an_add_on_that_records_the_path_is_read(self):
        path = metrics.angelo_path(recorded(), "claude-opus-5-5")
        looked, mutations = path["looked"], path["mutations"]
        self.assertEqual((looked["delivered_to_model"], looked["delivered_all"]), (3, 7))   # Its own; attachments apart.
        self.assertEqual(looked["delivered_per_turn"], PATH["images"])
        self.assertIs(looked["after_last_edit"], True)                 # The last pictures came after edit 3 of 3.
        self.assertIs(looked["final_render_seen"], True)               # The job's picture reached the model.
        self.assertEqual(path["model_requests"], 9)
        self.assertEqual(mutations["steps"], PATH["steps"])
        self.assertEqual(mutations["script_reasons"],
                         ["after a refused typed call (add_mesh+set_material: invalid)"] * 2)
        self.assertEqual((mutations["scripts_raised"], mutations["steps_dropped"]), (1, 0))

    def test_an_older_add_on_leaves_them_unknown(self):
        path = metrics.angelo_path(angelo_record(jobs=[job()]), "claude-opus-5-5")
        looked, mutations = path["looked"], path["mutations"]
        for key in ("delivered_to_model", "delivered_all", "delivered_per_turn", "after_last_edit"):
            self.assertIsNone(looked[key], key)
        self.assertIs(looked["final_render_seen"], False)              # Known all the same: a job, never shown in a turn.
        self.assertIsNone(mutations["steps"])
        self.assertIsNone(mutations["script_reasons"])
        self.assertIsNone(mutations["scripts_raised"])
        self.assertEqual(path["model_requests"], 6)                    # The receipt's rounds, as before.
        script_only = angelo_record()
        script_only["oneshot"] = dict(script_only["oneshot"], rows=[row for row in script_only["oneshot"]["rows"]
                                                                    if not row.get("job")])
        self.assertIsNone(metrics.angelo_path(script_only)["looked"]["final_render_seen"])

    def test_looked_before_the_last_edit_or_not_at_all(self):
        early = dict(PATH, images=PATH["images"][:2], images_delivered=5)        # Its last look was after edit 2.
        looked = metrics.angelo_path(recorded(path=early, shown={}))["looked"]
        self.assertEqual((looked["delivered_to_model"], looked["after_last_edit"], looked["final_render_seen"]),
                         (1, False, False))                                       # A mark with no time: never shown.
        blind = metrics.angelo_path(recorded(path={"edits": 2}, shown={"result_at": 1.0}))["looked"]
        self.assertEqual((blind["delivered_to_model"], blind["delivered_all"], blind["after_last_edit"]), (0, 0, False))
        self.assertIs(blind["final_render_seen"], False)                          # Told of the result, shown nothing.
        references = dict(PATH, images=[[1, 0, {"attachment": 4}]], images_delivered=4)
        only = metrics.angelo_path(recorded(path=references))["looked"]
        self.assertEqual((only["delivered_to_model"], only["delivered_all"], only["after_last_edit"]), (0, 4, False))

    def test_a_malformed_section_is_not_trusted(self):
        odd = {"edits": 1, "images": [[1, 1], "x", [2, 1, {"view:SOLID:TOP": 2}]], "steps": [["script", "ok"], 5,
                                                                                             ["script", "ok", "", [], 0]]}
        path = metrics.angelo_path(recorded(path=odd, shown="x"))
        self.assertEqual(path["looked"]["delivered_to_model"], 2)
        self.assertEqual(path["mutations"]["script_reasons"], ["straight to a script"])
        self.assertIs(path["looked"]["final_render_seen"], False)      # No readable mark: the older rule.
        self.assertFalse(metrics.path_facts({"path": "x"}, [])["recorded"])

    def test_unconfirmed_pictures_are_unknown_never_none(self):
        # A confirmed reference attachment beside tool pictures a Codex code-mode result sent unconfirmed: the
        # attachment's proof says nothing of the agent's own looks. Reading them as 0 and "no look after the last
        # edit" misreported a Codex cottage trial whose three pictures included its final render (outside review,
        # 2026-10-21).
        mixed = {"edits": 3, "images_delivered": 1, "images": [[1, None, {"attachment": 1}]],
                 "images_unconfirmed": 3, "image_delivery": "confirmed",
                 "unconfirmed_images": [[2, 1, {"view:SOLID:SCENE_CAMERA": 1}],
                                        [4, 3, {"view:MATERIAL:SCENE_CAMERA": 1}], [5, None, {"job:j1": 1}]]}
        side = metrics.angelo_path(recorded(path=mixed, shown={}, runtime="codex"))
        looked = side["looked"]
        self.assertEqual((looked["delivered_to_model"], looked["delivered_unconfirmed"], looked["delivery"]),
                         (0, 3, "unknown"))
        self.assertEqual(looked["delivered_all"], 1)                   # The attachment, proven.
        self.assertIsNone(looked["after_last_edit"])                   # Its look after edit 3 went out unconfirmed.
        self.assertIsNone(looked["final_render_seen"])                 # So did its render's picture.
        row = lambda label: next(line for line in tables.table([("Angelo 1", side, {"verdict": "passed",
                                                                                     "checks": "1 / 1"})])
                                 if line.startswith("| " + label))
        self.assertIn("reached the model: unknown (3 sent, delivery not confirmed) |", row("Looked at an image"))
        self.assertEqual(row("Looked at its result after"), "| Looked at its result after its last edit | unknown |")
        self.assertEqual(row("Saw its final render"), "| Saw its final render | unknown |")
        self.assertIn("angelo.image_delivery_unconfirmed",
                      tables.unknown_in([{"side": "angelo", "runtime": "codex", "path": side}]))
        # Only an earlier look went out unconfirmed: an edit came after it, so that stays a no.
        early = dict(mixed, unconfirmed_images=mixed["unconfirmed_images"][:1], images_unconfirmed=1)
        looked = metrics.angelo_path(recorded(path=early, shown={}, runtime="codex"))["looked"]
        self.assertEqual((looked["delivery"], looked["after_last_edit"], looked["final_render_seen"]),
                         ("unknown", False, False))
        # Proof wins over an unconfirmed copy, which is still counted apart.
        both = dict(PATH, image_delivery="confirmed", images_unconfirmed=2,
                    unconfirmed_images=[[6, 3, {"view:SOLID:TOP": 2}]])
        looked = metrics.angelo_path(recorded(path=both))["looked"]
        self.assertEqual((looked["delivered_to_model"], looked["delivered_unconfirmed"], looked["delivery"],
                          looked["after_last_edit"], looked["final_render_seen"]), (3, 2, "confirmed", True, True))
        self.assertIn("reached the model: 3 (+2 sent, delivery not confirmed) |",
                      next(line for line in tables.table([("Angelo 1", metrics.angelo_path(recorded(path=both)),
                                                           {"verdict": "passed", "checks": "1 / 1"})])
                           if line.startswith("| Looked at an image")))
        # A count whose groups were not kept may hold its own looks: unknown, not none.
        bare = {"edits": 2, "images_unconfirmed": 2, "image_delivery": "unknown"}
        looked = metrics.angelo_path(recorded(path=bare, shown={}, runtime="codex"))["looked"]
        self.assertEqual((looked["delivery"], looked["after_last_edit"], looked["final_render_seen"]),
                         ("unknown", None, None))
        # A job record's older, weaker shown claim (job_store keeps it as shown_unconfirmed) is unknown too.
        legacy = recorded(path={"edits": 1}, shown={})
        legacy["jobs"][0]["shown_unconfirmed"] = {"image_at": 1.0, "images": 1}
        self.assertIsNone(metrics.angelo_path(legacy)["looked"]["final_render_seen"])
        # Nothing sent at all is still a plain none.
        none = metrics.angelo_path(recorded(path={"edits": 2}, shown={}))["looked"]
        self.assertEqual((none["delivery"], none["after_last_edit"]), ("none", False))


class ScriptReasonTests(unittest.TestCase):
    def test_why_each_script_from_what_came_before_it(self):
        reasons = metrics.script_reasons
        self.assertIsNone(reasons(None))
        self.assertEqual(reasons([]), [])
        self.assertEqual(reasons([["script", "ok", "", [], 3]]), ["straight to a script"] * 3)
        self.assertEqual(reasons([["lookup", "ok", "modeling", [], 2], ["script", "ok", "", [], 1]]),
                         ["after a lookup"])
        self.assertEqual(reasons([["typed", "ok", "angelo_scene_program", ["add_mesh"], 4], ["script", "raised", "", [], 1],
                                  ["script", "ok", "", [], 1]]), ["after typed work"] * 2)
        refused = [["typed", "declined", "angelo_write_file", [], 1], ["script", "ok", "", [], 1]]
        self.assertEqual(reasons(refused), ["after a refused typed call (angelo_write_file: declined)"])

    def test_the_table_says_it_or_says_unknown(self):
        extra = {"verdict": "passed", "checks": "26 / 26"}
        row = lambda path, label: next(line for line in tables.table([("Angelo 1", path, extra)])
                                       if line.startswith("| " + label))
        new, old = metrics.angelo_path(recorded()), metrics.angelo_path(angelo_record(jobs=[job()]))
        self.assertEqual(row(new, "Why each script"), "| Why each script (from the request's own steps) / scripts "
                         "that raised | after a refused typed call (add_mesh+set_material: invalid) 2 / 1 |")
        self.assertEqual(row(old, "Why each script"), "| Why each script (from the request's own steps) / scripts "
                         "that raised | unknown / unknown |")
        self.assertEqual(row(new, "Looked at its result after"), "| Looked at its result after its last edit | yes |")
        self.assertEqual(row(old, "Looked at its result after"), "| Looked at its result after its last edit | unknown |")
        self.assertIn("reached the model: 3 |", row(new, "Looked at an image"))
        self.assertIn("reached the model: unknown |", row(old, "Looked at an image"))
        self.assertEqual(row(new, "Saw its final render"), "| Saw its final render | yes |")
        dropped = metrics.angelo_path(recorded(path=dict(PATH, steps_dropped=6)))
        self.assertIn("(6 later steps not recorded)", row(dropped, "Why each script"))
        other = metrics.mcp_path(mcp_record(), claude_lines(), {"messages": 1}, "claude-opus-5-5")
        self.assertEqual(row(other, "Why each script"), "| Why each script (from the request's own steps) / scripts "
                         "that raised | – |")                            # No such record on that side: not asked.

    def test_the_other_side_looked_after_its_last_script_by_the_order_of_its_calls(self):
        lines = claude_lines()
        self.assertIs(metrics.mcp_path(mcp_record(), lines)["looked"]["after_last_edit"], True)
        swapped = lines[:4] + lines[6:10] + lines[4:6] + lines[10:]     # The render script comes after both looks.
        self.assertIs(metrics.mcp_path(mcp_record(), swapped)["looked"]["after_last_edit"], False)
        self.assertIs(metrics.mcp_path(mcp_record(), lines[:6])["looked"]["after_last_edit"], False)   # Never looked.
        self.assertIsNone(metrics.mcp_path(mcp_record(), [])["looked"]["after_last_edit"])            # No stream kept.


class UnknownTests(unittest.TestCase):
    def side(self, path, side="angelo", runtime="claude"):
        return {"side": side, "runtime": runtime, "path": path}

    def test_only_what_this_run_left_unknown_is_listed(self):
        old = self.side(metrics.angelo_path(angelo_record()))
        other = self.side(metrics.mcp_path(mcp_record(), claude_lines()), "mcp")
        script_only = copy.deepcopy(old)
        script_only["path"]["looked"]["final_render_seen"] = None
        self.assertEqual(set(tables.unknown_in([old, other])),
                         {"angelo.image_delivered_to_model", "angelo.fallback_reason", "mcp.view_mode"})
        self.assertEqual(set(tables.unknown_in([script_only])),
                         {"angelo.image_delivered_to_model", "angelo.fallback_reason", "angelo.final_render_seen"})
        new = self.side(metrics.angelo_path(recorded()))
        self.assertEqual(tables.unknown_in([new]), {})
        codex = self.side(metrics.angelo_path(recorded(runtime="codex")), runtime="codex")
        self.assertEqual(set(tables.unknown_in([codex])), {"angelo.codex_rounds"})
        self.assertEqual(tables.unknown_in([{"side": "angelo", "path": None}]), {})   # It never started.
        self.assertTrue(all(tables.unknown_in([old, other]).values()))                # Each says what records it.


class AddOnRecordTests(unittest.TestCase):
    def test_running_usage_is_asked_of_the_add_on_that_offers_it(self):
        asked = []
        controller = Controller()
        controller.state["id"] = "chat-1"
        controller.telemetry = NS(running_usage=lambda chat: asked.append(chat) or dict(ASTRA_USAGE, turns=4, running=True))
        controller._request_metrics.usage = {"input_tokens": 5}           # The older place is not read then.
        usage = oneshot_session.running_usage(controller)
        self.assertEqual((asked, usage["input_tokens"], usage["turns"]), (["chat-1"], 1_000_000, 4))
        one = session(cost_cap=10)
        one.controller = controller
        one.watch()
        self.assertEqual((controller.stops, one.state["stop"]["reason"]), (1, "cost_cap"))

    def test_before_any_usage_is_known_and_on_an_older_add_on_it_is_read_as_before(self):
        controller = Controller()
        controller.state["id"] = "chat-1"
        controller.telemetry = NS(running_usage=lambda chat: None)        # Nothing known yet.
        self.assertIsNone(oneshot_session.running_usage(controller))
        controller._request_metrics.usage = dict(ASTRA_USAGE)
        self.assertEqual(oneshot_session.running_usage(controller)["input_tokens"], 1_000_000)
        older = Controller()                                               # No telemetry reader at all.
        older._request_metrics.usage = dict(ASTRA_USAGE)
        self.assertEqual(oneshot_session.running_usage(older)["output_tokens"], 100_000)
        older.telemetry = NS()
        self.assertEqual(oneshot_session.running_usage(older)["output_tokens"], 100_000)

    def records(self, *stored):
        manager = NS(store=NS(records={record["id"]: record for record in stored}))
        job_ui = NS(manager=lambda create=False: manager)       # The add-on's own module needs Blender.
        with mock.patch.dict(sys.modules, {"angelo_agent.job_ui": job_ui}):
            return scenario_session.ScenarioSession.job_records(None)

    def test_a_jobs_shown_mark_is_kept_and_its_absence_is_not_an_empty_mark(self):
        base = {"kind": "RENDER_STILL", "status": "done", "started": 10.0, "ended": 18.3,
                "request": {"output": "renders/footbridge-final.png"}}
        marked, unmarked, older = self.records(dict(base, id="j1", shown=dict(SHOWN)), dict(base, id="j2", shown={}),
                                               dict(base, id="j3"))
        self.assertEqual((marked["shown"], unmarked["shown"], older["shown"]), (SHOWN, {}, None))
        self.assertEqual((marked["job"], marked["seconds"], marked["output"]), ("j1", 8.3, "renders/footbridge-final.png"))

    def test_the_report_says_whether_a_job_was_shown_to_the_agent(self):
        text = lambda shown: produced.text([], [job(shown, output="footbridge-review.png")])
        self.assertEqual(text("absent"), "files: none; jobs: j1 RENDER_STILL done -> `footbridge-review.png`")
        self.assertTrue(text({}).endswith("`footbridge-review.png`, never shown to the agent"))
        self.assertTrue(text({"result_at": 1.0}).endswith(", its result was told to the agent but not shown"))
        self.assertTrue(text(SHOWN).endswith(", its picture was shown to the agent"))
        sentence = produced.missing_still("footbridge-final.png", [], [job({}, output="footbridge-review.png")])
        self.assertIn("j1 RENDER_STILL done -> `footbridge-review.png`, never shown to the agent", sentence)


if __name__ == "__main__":
    unittest.main()
