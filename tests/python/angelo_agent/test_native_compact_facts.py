# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every compact form of a result keeps the result's facts (``result_facts.commit_facts``, one list), and the
5,000-character excerpt keeps the facts its cut would lose (``result_facts.bounded_excerpt``).

Route parity for data (scripts/angelo/route_parity.py) found three forms that lost facts: the terse receipt's own key
list (the World and motion facts), a Codex plan correction's tool-and-status receipt (a script's facts), and the
handoff's excerpt (a fact placed after a long read-back). Three break-it reviews of reordering the excerpt found that
any reorder pushes some other content later than before (live handles, a script's result, its changes or World, a
read-back), so the excerpt keeps the result's own order and only the facts its cut would lose follow it.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import program_completion, result_facts  # noqa: E402
from angelo_agent.claude_outcomes import compact_receipt, terse_receipt  # noqa: E402

WORLD = {"shows": "SKY", "sun_lights": [{"name": "Key Sun", "from_sky_deg": 2.0}]}
MOTION = {"frames": [1, 60], "moving": [{"name": "Gate", "at": [[0, 0, 0], [0, 2, 0]]}]}
EXCERPT = program_completion.history_result_text


def certified_program():
    row = {"kind": "dimensions", "target": "h:1", "passed": True, "status": "pass", "observed": {"dimensions": [2, 2, 2]}}
    return {"success": True, "status": "committed", "final": True, "undoable": True,
            "checks": [{"passed": True, "status": "pass", "checks": [row]}],
            "changes": {"added": [{"name": "Gate", "type": "MESH"}]},
            "live_revisions": {"status": "COMMITTED", "objects": [{"handle": "h:1", "name": "Gate", "object_revision": "r1"}]},
            "world": WORLD, "motion": MOTION}


def big_world():
    return dict(WORLD, sky={"type": "SINGLE_SCATTERING", "sun_bearing_deg": 325.0, "sun_elevation_deg": 5.0},
                brightness={"samples": [{"where": "zenith %d" % index, "linear": [0.11, 0.17, 0.24],
                                         "shown": [0.36, 0.45, 0.52]} for index in range(6)]})


def big_motion(groups=6, parts=8, name_len=64):
    """At motion_facts' documented bounds (6 groups, 8 parts named in up to 64 characters, 5 frames)."""
    return {"frames": [1, 30, 60, 90, 120], "moving": [
        {"handle": "h:m%d" % group, "name": "Moving %d" % group,
         "parts": [("Part %d.%d " % (group, part) + "x" * name_len)[:name_len] for part in range(parts)],
         "origin": [[float(frame), 0.0, 1.0] for frame in range(5)],
         "bounds": [[[frame - 1.0, -1.0, 0.0], [frame + 1.0, 1.0, 2.0]] for frame in range(5)]} for group in range(groups)]}


def written_program(objects, **facts):
    """A committed program result as program_commit orders it: live handles early, read-backs after, facts last."""
    rows = [{"handle": "h:%d" % index, "name": "Plank %02d" % index, "object_revision": "%064x" % index}
            for index in range(objects)]
    result = {"success": True, "status": "committed", "final": True,
              "verified": {"object_count": objects, "write_count": objects},
              "live_revisions": {"status": "COMMITTED", "objects": rows},
              "changes": {"modified": [{"name": row["name"], "fields": ["location"]} for row in rows]},
              "checks": [{"passed": False, "status": "fail", "checks": [
                  {"kind": "dimensions", "target": row["handle"], "status": "fail",
                   "observed": {"dimensions": [1, 2, 3]}} for row in rows]}]}
    result.update(facts)
    return result


def tail(text):
    """The facts an excerpt states after its cut, or None."""
    return json.loads(text[text.rindex("…") + 1:]) if "…" in text else None


class CompactFacts(unittest.TestCase):
    def test_the_terse_receipt_keeps_the_world_and_motion(self):
        entry = terse_receipt("angelo_scene_program", certified_program())
        self.assertEqual(entry["world"], WORLD)
        self.assertEqual(entry["motion"], MOTION)

    def test_a_codex_plan_correction_keeps_a_scripts_facts(self):
        script = {"success": True, "output": "", "after": {"objects": []}, "world": WORLD, "motion": MOTION}
        receipt = program_completion.continuation_receipt({"tool": "angelo_execute_python", "arguments": {}}, script)
        self.assertEqual(receipt, {"tool": "angelo_execute_python", "status": None, "world": WORLD,
                                   "motion": MOTION})


class Excerpt(unittest.TestCase):
    def test_an_excerpt_whose_cut_loses_no_fact_reads_as_before(self):
        rows = [{"name": "Post %02d" % index, "data_users_before": 3} for index in range(40)]
        for result in ({"success": True, "output": "", "world": big_world(), "motion": big_motion(3, 4, 14)},
                       written_program(3, isolated_shared_data=rows, world=big_world()),   # Whole: all 40 rows.
                       {"success": True, "after": {"objects": []}},                         # No facts.
                       dict(written_program(60), world=None)):                              # Cut, but no fact lost.
            encoded = json.dumps(result, ensure_ascii=False)
            self.assertEqual(EXCERPT(result), encoded[:5000])

    def test_a_fact_the_cut_would_lose_follows_it_whole(self):
        script = {"success": True, "after": {"objects": [{"name": "Plank %d" % index, "bounds": [[0, 0, 0], [1, 1, 1]]}
                                                         for index in range(200)]}, "motion": MOTION}
        text = EXCERPT(script)
        self.assertEqual(len(text), 5000)
        self.assertTrue(json.dumps(script).startswith(text[:text.rindex("…")]), "the prefix is the result's own")
        self.assertEqual(tail(text), {"motion": MOTION})

    def test_a_long_print_no_longer_cuts_the_world(self):
        # Before the excerpt kept facts, a script that printed 4,800 characters lost the World after it.
        script = {"success": True, "result": None, "output": "x" * 4800, "world": big_world()}
        self.assertEqual(tail(EXCERPT(script)), {"world": big_world()})

    def test_the_facts_take_back_a_bounded_share_and_name_the_rest(self):
        rows = [{"name": "Post %02d" % index, "data_users_before": 3} for index in range(40)]
        result = written_program(60, world=big_world(), motion=big_motion(), isolated_shared_data=rows)
        text = EXCERPT(result)
        self.assertEqual(len(text), 5000)
        facts = tail(text)
        self.assertLessEqual(len(text) - text.rindex("…"), result_facts.EXCERPT_FACTS + 64)
        self.assertEqual(facts["isolated_shared_data"], rows)   # In COMMIT_FACTS order, whole, while they fit.
        self.assertEqual(facts["facts_omitted"], ["world", "motion"])

    def test_the_result_loses_at_most_the_facts_share_of_its_own_order(self):
        # Every earlier design moved something later than before; now the excerpt is the result's own encoding, and a
        # cut that would lose facts gives back at most their share at its end.
        for objects in range(10, 64, 3):
            result = written_program(objects, world=big_world(), motion=MOTION)
            text, encoded = EXCERPT(result), json.dumps(result, ensure_ascii=False)
            prefix = text[:text.rindex("…")] if "…" in text else text
            self.assertTrue(encoded.startswith(prefix), objects)
            self.assertGreaterEqual(len(prefix), min(len(encoded), 5000 - result_facts.EXCERPT_FACTS - 64), objects)

    def test_an_operations_own_fact_past_the_cut_follows_it(self):
        slots = {"objects": ["Bolt"], "note": "These have no faces: their checks read only their slots."}
        result = written_program(60, operation_results=[{"success": True}, {"success": True, "also_slots_only": slots}])
        facts = tail(EXCERPT(result))
        self.assertEqual(facts["operation_facts"], [{"operation": 1, "also_slots_only": {"objects": ["Bolt"]}}])
        self.assertEqual(facts["operation_fact_notes"], {"also_slots_only": slots["note"]})

    def test_no_fact_is_ever_lost_silently(self):
        # Break-it round 4: the settling cut swung between two states and ended with the World in neither the prefix,
        # the tail nor facts_omitted (a 5-handle commit with the World and four 6-part motion groups). Over sizes that
        # put each fact on either side of the cut, with both separator styles: every fact is whole in the prefix,
        # stated after the cut or named there, the prefix is the encoding's own, and nothing exceeds 5,000 characters.
        for separators in (None, (",", ":")):
            for live in range(0, 60, 5):
                for groups in range(0, 7):
                    for name_len in (8, 24, 64):
                        result = written_program(live, world=big_world(), motion=big_motion(groups, 6, name_len) if groups
                                                 else None)
                        encoded = json.dumps(result, ensure_ascii=False, separators=separators)
                        text = result_facts.bounded_excerpt(result, encoded, separators)
                        self.assertLessEqual(len(text), 5000)
                        prefix = text[:text.rindex("…")] if "…" in text else text
                        self.assertTrue(encoded.startswith(prefix))
                        facts = tail(text) or {}
                        # Never worse than the cut before: a tail always states a fact that cut did not show whole
                        # (break-it round 5: a World restated after a give-back, with motion only named).
                        gained = [key for key in ("world", "motion") if key in facts and json.dumps(key) + (
                            separators or (", ", ": "))[1] + json.dumps(result[key], ensure_ascii=False,
                                                                        separators=separators) not in encoded[:5000]]
                        # Without one, the cut is the one before, less a marker naming the facts that cut loses.
                        marker = set(facts) == {"facts_omitted"} and text == encoded[:5000 - (len(text) - len(prefix))] \
                            + text[len(prefix):] and len(text) - len(prefix) <= 64
                        self.assertTrue(gained or text == encoded[:5000] or marker, (separators, live, groups, name_len))
                        for key in ("world", "motion"):
                            if result.get(key) is None:
                                continue
                            item = json.dumps(key) + (separators or (", ", ": "))[1] + json.dumps(
                                result[key], ensure_ascii=False, separators=separators)
                            self.assertTrue(item in prefix or facts.get(key) == result[key]
                                            or key in facts.get("facts_omitted", ()), (separators, live, groups, name_len, key))

    def test_partial_verification_still_heads_the_excerpt(self):
        verification = {"status": "partial", "unknowns": [{"operation": 3, "reason": "unchecked_geometry_edit"}] * 30}
        result = written_program(20, verification=verification, world=big_world(), motion=big_motion())
        self.assertTrue(EXCERPT(result).startswith('{"verification": {"status": "partial", "unknown_count": 30'))


class OneList(unittest.TestCase):
    def test_every_compact_form_keeps_every_commit_fact(self):
        facts = {"unchecked_dependents": [{"name": "Rail", "uses": "Post"}], "unobservable_checks": ["contacts"],
                 "unverified_effects": [{"operation": None, "root": {"target": "Gate"}, "reason": "unchecked_geometry_edit"}],
                 "world": WORLD, "motion": MOTION}
        result = dict(certified_program(), **facts)
        self.assertEqual(set(facts) - set(result_facts.COMMIT_FACTS), set())
        for form in (terse_receipt("angelo_scene_program", result), compact_receipt("angelo_scene_program", result),
                     program_completion.continuation_receipt({"tool": "angelo_execute_python", "arguments": {}}, facts)):
            for key, value in facts.items():
                self.assertEqual(form[key], value, key)

    def test_a_capped_list_states_its_length(self):
        rows = [{"object": "Post %02d" % index} for index in range(40)]
        entry = terse_receipt("angelo_scene_program", dict(certified_program(), isolated_shared_data=rows))
        self.assertEqual(len(entry["isolated_shared_data"]), result_facts.FACT_ROWS)
        self.assertEqual(entry["isolated_shared_data_count"], 40)

    def test_host_sentences_stay_out_of_the_compact_forms(self):
        # A moved frame and unkeyed animated values arrive as sentences telling the model what to do: facts reach every
        # form, instructions do not (they stay in the full result, as before).
        result = dict(certified_program(), frame_moved="This edit moved the current frame: bind a later program to "
                      "revisions observed after it.", unkeyed_writes={"channels": [], "count": 0})
        entry = terse_receipt("angelo_scene_program", result)
        self.assertNotIn("frame_moved", entry)
        self.assertNotIn("unkeyed_writes", entry)

    def test_an_operations_own_fact_is_lifted_and_its_repeated_note_stated_once(self):
        note = "These have no faces: their checks read only their slots."
        rows = [{"success": True}] + [{"success": True, "also_slots_only": {"objects": ["Bolt %d" % index], "note": note}}
                                      for index in range(3)]
        entry = terse_receipt("angelo_scene_program", dict(certified_program(), operation_results=rows))
        self.assertEqual(entry["operation_facts"], [{"operation": index + 1, "also_slots_only": {"objects": ["Bolt %d" % index]}}
                                                    for index in range(3)])
        self.assertEqual(entry["operation_fact_notes"], {"also_slots_only": note})
        self.assertEqual(json.dumps(entry).count(note), 1)


if __name__ == "__main__":
    unittest.main()
