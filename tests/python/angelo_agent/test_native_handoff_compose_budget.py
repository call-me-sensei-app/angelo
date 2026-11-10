# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Inactive whole composer: bounded raw inspection and exact all-fit presentation."""
import suite_isolation  # noqa: F401
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.handoff_compose import compose
from angelo_agent.identifiers import IdentifierTable
from angelo_agent.routing import HANDOFF_HEADER, handoff
from test_native_handoff_receipts_budget import committed


def records(text):
    return [json.loads(line) for line in text[len(HANDOFF_HEADER):].splitlines()]


class ComposeBudget(unittest.TestCase):
    def test_all_fit_ordinary_roles_match_actual_production(self):
        messages = [{"role": role, "text": "café\n" + role} for role in ("user", "assistant", "system", "error")]
        messages.insert(1, {"role": "thinking", "text": "private"})
        for short in (False, True):
            old, new = (IdentifierTable(), IdentifierTable()) if short else (None, None)
            self.assertEqual(compose(messages, 0, 24000, header=HANDOFF_HEADER, identifiers=new),
                             handoff(messages, identifiers=old))

    def test_request_kind_and_steer_notes_keep_the_current_owner_words(self):
        messages = [{"role": "user", "text": "Accept", "request_kind": "native_preview_accept"},
                    {"role": "user", "text": "Make it smaller", "steer": True}]
        self.assertEqual(compose(messages, 0, 24000, header=HANDOFF_HEADER), handoff(messages))

    def test_certified_and_unchecked_program_rows_preserve_all_fit_bytes(self):
        messages = [{"role": "user", "text": "Create"}, {"role": "activity", "status": "completed",
            "tool": "angelo_scene_program", "result": committed(), "call_id": "checked"},
            {"role": "system", "host_summary": True, "text": "Host completion"},
            {"role": "activity", "status": "completed", "tool": "angelo_scene_program",
             "result": committed(checks=[], undoable=True), "call_id": "unchecked"}]
        old, new = IdentifierTable(), IdentifierTable()
        self.assertEqual(compose(messages, 0, 24000, header=HANDOFF_HEADER, identifiers=new),
                         handoff(messages, identifiers=old))
        self.assertEqual(new.to_dict("native"), old.to_dict("native"))

    def test_raw_filtered_tail_is_bounded_and_omitted_honestly(self):
        text, ids = compose([{"role": "thinking"}] * 100000, 0, 512, header=HANDOFF_HEADER)
        report = next(row for row in records(text) if row.get("role") == "handoff_omission")
        self.assertGreater(report["unexamined_transcript_records"], 0)
        self.assertTrue(report["work_exhausted"])
        self.assertEqual(ids, frozenset())
        self.assertLessEqual(len(text), 512)

    def test_control_character_prefix_is_complete_json_inside_total_limit(self):
        text, ids = compose([{"role": "user", "text": "\x01\n\"" * 100000}], 0, 512, header=HANDOFF_HEADER)
        rows = records(text)
        self.assertTrue(next(row for row in rows if row.get("role") == "user")["truncated"])
        self.assertLessEqual(len(text), 512)
        self.assertEqual(ids, frozenset())

    def test_unfunded_older_graph_cannot_consume_newer_admitted_output(self):
        messages = [{"role": "activity", "status": "completed", "tool": "angelo_scene_object",
                     "result": {"huge": "x" * 1000000}, "call_id": "unexamined"},
                    {"role": "user", "text": "Recent"}]
        text, ids = compose(messages, 0, 2048, header=HANDOFF_HEADER, identifiers=IdentifierTable())
        self.assertIn("Recent", [row.get("text") for row in records(text)])
        self.assertNotIn("unexamined", ids)
        self.assertLessEqual(len(text), 2048)

    def test_colliding_message_key_never_executes_equality(self):
        class Collision:
            def __hash__(self):
                return hash("role")
            def __eq__(self, other):
                raise AssertionError("custom key equality")
        text, ids = compose([{Collision(): "user"}], 0, 2048, header=HANDOFF_HEADER)
        self.assertTrue(records(text))
        self.assertEqual(ids, frozenset())

    def test_too_small_budget_is_an_explicit_refusal(self):
        with self.assertRaises(ValueError):
            compose([{"role": "user", "text": "hello"}], 0, 1, header=HANDOFF_HEADER)

    def test_custom_table_cannot_receive_currency_callbacks(self):
        class Custom:
            def observed(self, *args, **kwargs):
                raise AssertionError("custom table callback")
        with self.assertRaises(ValueError):
            compose([{"role": "user", "text": "hello"}], 0, 2048,
                    header=HANDOFF_HEADER, identifiers=Custom())

    def test_currency_cutoff_never_presents_an_unproven_newer_read_as_current(self):
        handle, before, after = "scn:" + "a" * 32 + ":1", "b" * 64, "c" * 64
        messages = [{"role": "activity", "status": "completed", "tool": "angelo_execute_python",
            "result": {"status": "committed", "live_revisions": {"status": "COMMITTED", "objects": [
                {"handle": handle, "object_revision": before}]}}, "call_id": "older-commit"},
            {"role": "user", "text": "x" * 1000000},
            {"role": "activity", "status": "completed", "tool": "angelo_scene_object", "result": {
                "after": {"objects": [{"handle": handle, "object_revision": after}]}}, "call_id": "read"}]
        # The public facade uses this same bounded composer; it must not
        # recover currency by an unbudgeted eager scan through the huge row.
        previous, previous_ids = handoff(messages, limit=4096, identifiers=IdentifierTable())
        self.assertNotIn("read", previous_ids)
        self.assertFalse(any(row.get("tool") == "angelo_scene_object" for row in records(previous)))
        text, ids = compose(messages, 0, 4096, header=HANDOFF_HEADER, identifiers=IdentifierTable())
        rows = records(text)
        report = next(row for row in rows if row.get("role") == "handoff_omission")
        self.assertGreater(report["unexamined_transcript_records"], 0)
        self.assertGreater(report["omitted_candidates"], 0)
        self.assertNotIn("read", ids)
        self.assertFalse(any(row.get("tool") == "angelo_scene_object" for row in rows))

    def test_complete_requested_suffix_does_not_require_transcript_origin(self):
        handle = "scn:" + "a" * 32 + ":1"
        messages = [{"role": "user", "text": "Earlier"}, {"role": "activity", "status": "completed",
            "tool": "angelo_scene_object", "result": {"after": {"objects": [
                {"handle": handle, "object_revision": "b" * 64}]}}, "call_id": "read"}]
        old, new = IdentifierTable(), IdentifierTable()
        self.assertEqual(compose(messages, 1, 24000, header=HANDOFF_HEADER, identifiers=new),
                         handoff(messages, start=1, identifiers=old))
        self.assertEqual(new.to_dict("native"), old.to_dict("native"))

    def test_filtered_nonprogram_notice_does_not_keep_a_redundant_summary(self):
        messages = [{"role": "user", "text": "Create"}, {"role": "activity", "status": "completed",
            "tool": "angelo_scene_object", "notice": True, "result": {}},
            {"role": "activity", "status": "completed", "tool": "angelo_scene_program", "result": committed(),
             "call_id": "program"}, {"role": "system", "host_summary": True, "text": "Host completion"}]
        old, new = IdentifierTable(), IdentifierTable()
        self.assertEqual(compose(messages, 0, 24000, header=HANDOFF_HEADER, identifiers=new),
                         handoff(messages, identifiers=old))
        self.assertEqual(new.to_dict("native"), old.to_dict("native"))


if __name__ == "__main__":
    unittest.main()
