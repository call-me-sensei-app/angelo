# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's Workflows tab: run stages, part rows and sheet outlines (studio_workflow_view), without Blender."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import parts_check, studio_workflow_view as wv
from angelo_agent.flow_templates import CHARACTER_SLOTS, CHARACTER_STEP, PARTS_STEP, SHEET_STEP, SPLIT_STEP

PARTS = [{"id": "body", "slot": "body", "kind": "BODY", "item": 0, "numbered": False, "box": [-200, 20, 200, 940], "asset": "c-body"},
         {"id": "headwear", "slot": "headwear", "kind": "HEADWEAR", "item": 0, "numbered": False, "box": [-210, 50, 210, 270],
          "asset": "c-hat"},
         {"id": "accessories-1", "slot": "accessories", "kind": "ACCESSORY", "item": 0, "numbered": True,
          "box": [-100, 200, -30, 280], "asset": "c-ring"}]
SPLIT = {"key": "k", "sheet": "sheet", "parts": PARTS, "cell_height": 960,
         "grid": {"x": [i * 480 for i in range(9)], "y": [0, 960, 1920]}, "size": [3840, 1920]}


def flow(expanded=True):
    nodes = [{"id": SHEET_STEP, "type": "GENERATE_IMAGE"}, {"id": SPLIT_STEP, "type": "SPLIT_SHEET"},
             {"id": CHARACTER_STEP, "type": "PLACE_CHARACTER"}]
    if expanded:
        nodes += [{"id": f"{PARTS_STEP}/{p['id']}", "type": "GENERATE_3D", "fanout": PARTS_STEP, "part": p["id"]} for p in PARTS]
    else:
        nodes.append({"id": PARTS_STEP, "type": "GENERATE_3D_EACH"})
    return {"nodes": nodes, "links": []}


def states(sheet="READY", split=True, parts=None, character="WAITING"):
    value = {SHEET_STEP: {"state": sheet, "chosen": "sheet" if sheet == "READY" else None},
             SPLIT_STEP: {"state": "READY", "value": SPLIT} if split else {"state": "WAITING", "value": None},
             CHARACTER_STEP: {"state": character}}
    for part, state in (parts or {}).items():
        value[f"{PARTS_STEP}/{part}"] = {"state": state, "chosen": f"m-{part}" if state == "READY" else None}
    return value


class StageTests(unittest.TestCase):
    def test_stages_follow_the_run(self):
        self.assertEqual(wv.view(None, None)["stage"], wv.FILL)
        self.assertEqual(wv.view(flow(False), states(sheet="MISSING", split=False))["stage"], wv.FILL)
        self.assertEqual(wv.view(flow(False), states(sheet="RUNNING", split=False))["stage"], wv.SHEET)
        self.assertEqual(wv.view(flow(False), states(split=False))["stage"], wv.SHEET, "ready, being split")
        self.assertEqual(wv.view(flow(), states(parts={p["id"]: "MISSING" for p in PARTS}))["stage"], wv.REVIEW)
        self.assertEqual(wv.view(flow(), states(parts={"body": "RUNNING"}))["stage"], wv.PARTS)
        self.assertEqual(wv.view(flow(), states(parts={"body": "FAILED"}))["stage"], wv.PARTS)
        placed = wv.view(flow(), states(parts={"body": "READY", "headwear": "FAILED"}, character="READY"))
        self.assertEqual(placed["stage"], wv.PLACE, "the body is enough to place; failures stay listed")
        blocked = {p["id"]: p for p in wv.view(flow(), states(parts={"body": "READY", "headwear": "BLOCKED",
                                                                     "accessories-1": "FAILED"}))["parts"]}
        self.assertEqual((blocked["headwear"]["retry"], blocked["accessories-1"]["retry"]), (False, True),
                         "only a failed step can be made again; a blocked one (no account) can't")
        making = wv.view(flow(), states(parts={"body": "READY", "headwear": "RUNNING"}, character="READY"))
        self.assertEqual(making["stage"], wv.PARTS, "not 'ready to place' while a part is still being made")

    def test_the_body_is_required_and_parts_are_named_by_kind(self):
        rows = {p["id"]: p for p in wv.view(flow(), states(parts={}))["parts"]}
        self.assertEqual([k for k, p in rows.items() if p["required"]], ["body"], "Place Character needs the body")
        self.assertEqual((rows["headwear"]["title"], rows["headwear"]["number"]), ("Headwear", None))
        self.assertEqual((rows["accessories-1"]["title"], rows["accessories-1"]["number"]), ("Accessory", 1))
        self.assertEqual(rows["accessories-1"]["label"], "accessories 1", "the flow's own names stay English")

    def test_part_rows_carry_status_models_and_checks(self):
        shown = wv.view(flow(), states(parts={"body": "READY", "headwear": "RUNNING", "accessories-1": "BLOCKED"}))
        rows = {p["id"]: p for p in shown["parts"]}
        self.assertEqual(rows["body"]["status"], wv.DONE)
        self.assertEqual(rows["body"]["model"], "m-body")
        self.assertEqual(rows["headwear"]["status"], wv.MAKING)
        self.assertEqual(rows["accessories-1"]["status"], wv.FAILED)
        self.assertEqual([p["id"] for p in shown["parts"] if p["check"]], ["headwear", "accessories-1"])
        self.assertEqual(shown["counts"], {wv.DONE: 1, wv.MAKING: 1, wv.WAITING: 0, wv.FAILED: 1})


class InputTests(unittest.TestCase):
    def test_the_description_takes_paragraphs_like_studios_prompt(self):
        import re
        from angelo_agent.studio_state import MULTILINE_FIELDS
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/studio_workflows.py").read_text()
        key = re.search(r'^INPUT_KEY = "([^"]+)"', source, re.M).group(1)
        self.assertIn(key, MULTILINE_FIELDS, "a character description is cut at 200 characters otherwise")
        self.assertIn(f"s.field(state, (px, top, cw, 88), INPUT_KEY,", source)


class GeometryTests(unittest.TestCase):
    def test_contain_centres_the_sheet(self):
        self.assertEqual(wv.contain((3840, 1920), (0, 0, 800, 800)), (0, 200, 800, 400))
        self.assertEqual(wv.contain((0, 10), (0, 0, 10, 10))[2:], (0, 0))

    def test_outlines_land_on_their_cells(self):
        drawn = (100, 50, 768, 384)  # the 3840 x 1920 sheet at 0.2
        boxes = {part: rect for part, _, rect in wv.outlines(SPLIT, CHARACTER_SLOTS, drawn)}
        x, y, w, h = boxes["body"]
        self.assertAlmostEqual(x, 100 + (240 - 200) * 0.2)
        self.assertAlmostEqual(w, 400 * 0.2)
        self.assertAlmostEqual(y + h, 50 + 384 - 20 * 0.2, msg="top edge, y up")
        hat = boxes["headwear"]  # third column: centre at 1200 px
        self.assertAlmostEqual(hat[0] + hat[2] / 2, 100 + 1200 * 0.2)
        ring = boxes["accessories-1"]  # last column: centre at 3600 px, box left of it
        self.assertLess(ring[0] + ring[2], 100 + 3600 * 0.2)
        for _, (bx, by, bw, bh) in boxes.items():
            self.assertTrue(drawn[0] <= bx and bx + bw <= drawn[0] + drawn[2] and drawn[1] <= by <= by + bh <= drawn[1] + drawn[3])

    def test_no_outlines_without_a_grid(self):
        self.assertEqual(wv.outlines(dict(SPLIT, grid=None), CHARACTER_SLOTS, (0, 0, 100, 50)), [])


class TileStatusTests(unittest.TestCase):
    def test_a_part_left_out_says_off_until_something_makes_it(self):
        waiting, done, failed = ({"id": "hair", "status": status} for status in (wv.WAITING, wv.DONE, wv.FAILED))
        self.assertEqual(wv.tile_status(waiting, {"hair"}), wv.OFF)
        self.assertEqual(wv.tile_status(waiting, {"outer"}), wv.WAITING)
        self.assertEqual(wv.tile_status(done, {"hair"}), wv.DONE, "made before it was left out: its model is shown")
        self.assertEqual(wv.tile_status(failed, {"hair"}), wv.FAILED)


class FitCountTests(unittest.TestCase):
    def test_the_fit_line_never_counts_more_parts_than_it_places(self):
        """1.0.6 slot, held-out c8 with Hair left out: "Fitted to your image: 6 of 5 parts" (Hair's box was counted)."""
        parts = [{"id": identity, "kind": kind} for identity, kind in
                 (("body", "BODY"), ("hair", "HAIR"), ("outer", "OUTER"), ("accessories-1", "ACCESSORY"))]
        boxes = {"hair": [0, 0, 1, 1], "outer": [0, 0, 1, 1], "accessories-1": [0, 0, 1, 1]}
        self.assertEqual(wv.fit_count(parts, boxes, {"hair"}), (2, 2))
        self.assertEqual(wv.fit_count(parts, boxes, set()), (3, 3))
        self.assertEqual(wv.fit_count(parts, {"outer": [0, 0, 1, 1]}, set()), (1, 3), "a part the fit missed")
        self.assertEqual(wv.fit_count(parts, dict(boxes, gone=[0, 0, 1, 1]), set()), (3, 3), "a stale box: neither")


class StudioNamesTests(unittest.TestCase):
    def test_every_name_the_workflows_modules_call_on_studio_exists_there(self):
        """1.0.6 slot: fit_offered called ``s.is_coding`` (``s = _s()``, the studio module), which studio doesn't have:
        AttributeError while the card drew, whenever AI Enhance was bound. Read statically, as nothing imports bpy here."""
        import ast

        def top_level(tree):
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                        names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
            return names
        addon = ROOT / "scripts/addons_core/angelo_agent"
        studio = top_level(ast.parse((addon / "studio.py").read_text()))
        for name in ("studio_workflows.py", "studio_workflow_ops.py", "studio_workflow_run.py"):
            for function in ast.walk(ast.parse((addon / name).read_text())):
                if not isinstance(function, ast.FunctionDef):
                    continue
                aliases = {target.id for node in ast.walk(function) if isinstance(node, ast.Assign)
                           and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", None) == "_s"
                           for target in node.targets if isinstance(target, ast.Name)}
                for node in ast.walk(function):
                    if isinstance(node, ast.Attribute) and getattr(node.value, "id", None) in aliases:
                        with self.subTest(module=name, function=function.name):
                            self.assertTrue(node.attr in studio, f"studio.{node.attr} doesn't exist")


class CardLayoutTests(unittest.TestCase):
    """The card's step chips and footer buttons fit its column at any label width (1.0.6: in English the last chip ran
    past the card and "Edit in Generation Flow" was cut off; translations run longer)."""

    def test_chips_wrap_and_never_pass_the_column(self):
        for width in (120, 200, 286, 400):
            for widths in ([82, 74, 81, 57], [140, 150, 120, 90], [300, 20, 20, 20], [10] * 9):
                placed = wv.chip_rows(widths, width)
                with self.subTest(width=width, widths=widths):
                    self.assertEqual(len(placed), len(widths))
                    self.assertTrue(all(x >= 0 and x + w <= width for x, _, w in placed))
                    rows = [row for _, row, _ in placed]
                    self.assertEqual(rows, sorted(rows), "in order, left to right then down")
                    for (x, row, w), (nx, nrow, _) in zip(placed, placed[1:]):
                        self.assertTrue(nrow == row and nx >= x + w or nrow == row + 1 and nx == 0)
        self.assertEqual([row for _, row, _ in wv.chip_rows([82, 74, 81, 57], 286)], [0, 0, 0, 1])

    def test_a_button_pair_shares_the_row_only_when_both_labels_fit(self):
        self.assertEqual(wv.pair((100, 120), 286), (139, 139), "halves when both fit one")
        first, second = wv.pair((152, 110), 286)
        self.assertEqual((first + second + 8, first >= 152, second >= 110), (286, True, True))
        self.assertIsNone(wv.pair((200, 160), 286), "stacked: neither label is cut")
        for needs in ((40, 230), (139, 139), (180, 98), (278, 0)):
            widths = wv.pair(needs, 286)
            with self.subTest(needs=needs):
                self.assertTrue(widths and all(w >= n for w, n in zip(widths, needs)) and sum(widths) + 8 <= 286)


class PartsCheckTests(unittest.TestCase):
    def test_the_prompt_names_every_column_and_no_particular_character(self):
        text = parts_check.prompt(CHARACTER_SLOTS)
        for index, slot in enumerate(CHARACTER_SLOTS):
            self.assertIn(f"{index + 1} {slot['id']}", text)
        self.assertIn('"missing"', text)

    def test_a_reply_parses_to_items_in_known_columns(self):
        reply = ('Here you go: {"missing": [{"item": "Pipe", "column": "accessories"}, {"item": " ", "column": "x"}], '
                 '"different": [{"item": "Beanie", "column": "hats", "how": "red, not grey"}]}')
        found = parts_check.parse(reply, CHARACTER_SLOTS)
        self.assertFalse(found["complete"])
        self.assertEqual(found["missing"], [{"item": "Pipe", "column": "accessories"}])
        self.assertEqual(found["different"], [{"item": "Beanie", "column": None, "how": "red, not grey"}])

    def test_empty_lists_are_complete_and_no_json_is_an_error(self):
        self.assertTrue(parts_check.parse('{"missing": [], "different": []}', CHARACTER_SLOTS)["complete"])
        for bad in ("", "Looks complete!", '{"ok": true}', "{not json}"):
            with self.subTest(reply=bad), self.assertRaises(ValueError):
                parts_check.parse(bad, CHARACTER_SLOTS)

    def test_the_check_is_offered_only_once_measured(self):
        # Maintainer decision (2026-10-20): measure accuracy on the ten research characters before shipping it.
        self.assertEqual(parts_check.READY, bool(parts_check.MEASUREMENT))
        addon = ROOT / "scripts/addons_core/angelo_agent"
        self.assertIn("if not parts_check.READY:", (addon / "studio_workflows.py").read_text(encoding="utf-8"))
        self.assertIn("return parts_check.READY", (addon / "studio_workflow_ops.py").read_text(encoding="utf-8"))

    def test_long_replies_are_bounded(self):
        reply = '{"missing": [%s], "different": []}' % ", ".join('{"item": "%s"}' % ("x" * 200) for _ in range(40))
        found = parts_check.parse(reply, CHARACTER_SLOTS)
        self.assertEqual(len(found["missing"]), parts_check.MAX_ITEMS)
        self.assertEqual(len(found["missing"][0]["item"]), parts_check.MAX_TEXT)


if __name__ == "__main__":
    unittest.main()
