# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests of the question grader: answer forms, parsing, evidence and every outcome.

The grader is fed scripted chat rows: an empty reply, a refusal, a wrong
answer, the right answer and the right answer with no observation that could
support it. Nothing here starts Blender, a provider or a model.
"""

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _path in (str(ROOT / "scripts/addons_core"), str(ROOT / "scripts/angelo"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import eval_answers as answers  # noqa: E402
from angelo_agent import claude_evidence, view_spec  # noqa: E402
from i18n import languages  # noqa: E402

VISUAL = {"form": "yes_no", "evidence": answers.SCENE_CAMERA}
# A receipt path proving a scene-camera view reached the model (test_native_visual_answers holds the rest).
DELIVERED = {"edits": 0, "images_delivered": 1, "images": [[2, 0, {"view:SOLID:SCENE_CAMERA": 1}]],
             "image_delivery": "confirmed"}


def said(text):
    return {"role": "assistant", "text": text}


def view(source=answers.SCENE_CAMERA, tool="angelo_view", status="completed", size=4096, reused=False):
    camera = {"source": source, "projection": "PERSPECTIVE", "matrix_world": [[1, 0, 0, 0]] * 4}
    if reused:
        camera["reused_from"] = "0c9a2a52-6d0b-4f0e-9a38-3f6d2d1c5b10"
    image = {"bytes": size, "path": "/private/chat/reference.png", "camera": camera}
    return {"role": "activity", "tool": tool, "status": status, "arguments": {"views": ["SCENE_CAMERA"]},
            "result": {"success": True, "views": [image]}}


class FormTests(unittest.TestCase):
    def test_every_form_has_a_label_and_a_sentence_that_quotes_it(self):
        self.assertEqual(set(answers.FORMS), {"yes_no", "counts", "names", "render", "shortcut", "mesh_names", "box"})
        for form, (label, sentence) in answers.FORMS.items():
            with self.subTest(form=form):
                self.assertTrue(label.isupper() and label.isascii())
                self.assertIn("'%s: " % label, sentence)
                self.assertTrue(answers.prompt("Is it?", form).startswith("Is it? End your reply with one line"))

    def test_the_line_is_found_whatever_surrounds_it(self):
        for text in ("ANSWER: yes", "It is.\nANSWER: yes\n", "**ANSWER:** yes", "> answer: Yes.", "- `ANSWER: yes`",
                     "ANSWER：yes", "ＡＮＳＷＥＲ：ｙｅｓ", "ANSWER: no\nOn a second look:\nANSWER: yes",
                     "Answer :  YES  "):
            with self.subTest(text=text):
                self.assertEqual(answers.parse("yes_no", answers.answer_line(text, "ANSWER")), "yes")
        for text in ("", "Yes, it is in frame.", "The answer: yes", "ANSWERS: yes", "My ANSWER would be yes"):
            with self.subTest(text=text):
                self.assertIsNone(answers.answer_line(text, "ANSWER"))
        windows = "Two objects.\r\nOBJECTS: Cube, Camera\r\n"
        self.assertEqual(answers.parse("names", answers.answer_line(windows, "OBJECTS")), ["Camera", "Cube"])
        self.assertEqual(answers.answer_lines("ANSWER: no\nANSWER: yes", "ANSWER"), ["no", "yes"])

    def test_yes_and_no_in_every_language_the_app_ships(self):
        self.assertEqual(set(answers.YES_NO_WORDS), {"en"} | {language.code for language in languages.LANGUAGES})
        for code, (yes, no) in answers.YES_NO_WORDS.items():
            with self.subTest(language=code):
                self.assertEqual((answers.parse("yes_no", yes), answers.parse("yes_no", no)), ("yes", "no"))
                self.assertEqual(answers.parse("yes_no", yes.upper()), "yes")
        self.assertEqual([answers.parse("yes_no", word) for word in ("si", "Não.", "hayir")], ["yes", "no", "no"],
                         "accents and a final full stop are optional")
        # The value is the answer: a sentence that only starts with the word is not it
        # (test_native_answer_forms.py: what a clause after the answer may and may not be).
        for unclear in ("maybe", "yes and no", "no, yes", "I cannot see the image", "", "partly", "not sure", "nope",
                        "No idea", "No way to tell from this view", "No image was returned", "nie wiem", "no sé",
                        "Não consigo ver", "No part of Eval Cube is cut off", "non-cropped", "n. a."):
            with self.subTest(value=unclear):
                self.assertIsNone(answers.parse("yes_no", unclear))
        self.assertEqual([answers.parse("yes_no", word) for word in ("네", "是的", "不是", "ไม่")],
                         ["yes", "yes", "no", "no"])

    def test_counts_names_render_and_shortcut_are_canonical(self):
        for text in ("12 20 10", "12, 20, 10", "12,20,10", "vertices 12, edges 20, faces 10", "12 / 20 / 10",
                     "１２ ２０ １０", "١٢ ٢٠ ١٠"):
            with self.subTest(text=text):
                self.assertEqual(answers.parse("counts", text), [12, 20, 10])
        self.assertEqual(answers.parse("counts", "1,024 2,048 1,024"), [1024, 2048, 1024])
        for bad in ("12 20", "twelve twenty ten", "12 20 10 4", "12.0 20.0 10.0", "1,2 3 4"):
            with self.subTest(value=bad):
                self.assertIsNone(answers.parse("counts", bad), "a decimal point is not a thousands separator")
        self.assertEqual(answers.parse("names", "Light, `Cube`, Brass Marker; Camera."),
                         ["Brass Marker", "Camera", "Cube", "Light"])
        self.assertEqual(answers.parse("names", "Cube、Camera،Light"), ["Camera", "Cube", "Light"])
        self.assertIsNone(answers.parse("names", " , "))
        for text in ("EEVEE 1600x900", "Blender EEVEE, 1600 × 900", "BLENDER_EEVEE_NEXT 1600x900",
                     "Eevee Next 1600*900"):
            with self.subTest(text=text):
                self.assertEqual(answers.parse("render", text), ["EEVEE", 1600, 900])
        self.assertEqual(answers.parse("render", "Cycles 900x1600"), ["CYCLES", 900, 1600])
        self.assertEqual(answers.parse("render", "EEVEE, 1600 x 900 at 100%"), ["EEVEE", 1600, 900])
        self.assertEqual(answers.parse("render", "EEVEE 1600X900"), ["EEVEE", 1600, 900])
        self.assertEqual(answers.parse("render", "EEVEE 1,600x900"), ["EEVEE", 1600, 900])
        self.assertEqual(answers.parse("render", "Hydra Storm 1600x900"), ["HYDRASTORM", 1600, 900])
        for bad in ("1600x900", "EEVEE", "EEVEE 1600 or 900", "EEVEE or Cycles 1600x900"):
            self.assertIsNone(answers.parse("render", bad))
        for text in ("Ctrl+B", "ctrl + b", "CTRL-B", "Control+B", "Strg+B", "⌃ B", "⌃B", "Ctrl B key",
                     "Ctrl + B (Cmd + B on a Mac)", "Ctrl+B or Cmd+B", "Ctrl+B, then drag"):
            with self.subTest(text=text):
                self.assertEqual(answers.parse("shortcut", text), "ctrl+b")
        self.assertEqual((answers.parse("shortcut", "⌘B"), answers.parse("shortcut", "⇧⌘B")),
                         ("cmd+b", "cmd+shift+b"))
        self.assertEqual(answers.parse("shortcut", "Shift+Ctrl+B"), "ctrl+shift+b")
        self.assertEqual(answers.parse("shortcut", "Cmd+B"), "cmd+b")
        for bad in ("Ctrl", "Ctrl+B then S", ""):
            self.assertIsNone(answers.parse("shortcut", bad))
        with self.assertRaises(ValueError):
            answers.parse("essay", "anything")

    def test_engine_names_compare_bare(self):
        spellings = ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "Eevee", "eevee next", "Blender's EEVEE renderer")
        self.assertEqual({answers.engine_name(name) for name in spellings}, {"EEVEE"})
        self.assertEqual(answers.engine_name("Cycles or EEVEE"), "", "two engines name none")
        self.assertEqual((answers.engine_name("CYCLES"), answers.engine_name("BLENDER_WORKBENCH")),
                         ("CYCLES", "WORKBENCH"))


class EvidenceTests(unittest.TestCase):
    def test_image_tools_are_the_add_ons_own(self):
        self.assertEqual(answers.IMAGE_TOOLS, claude_evidence.VISUAL_IMAGE_TOOLS)
        self.assertIn(view_spec.TOOL_NAME, answers.IMAGE_TOOLS)
        self.assertEqual(answers.SCENE_CAMERA, view_spec.SCENE_CAMERA)

    def test_only_completed_image_tools_with_bytes_count_and_nothing_else_is_kept(self):
        rows = [said("Looking"), view(), view("AXIS:FRONT"), view(reused=True),
                view(tool="angelo_sculpt_view", source=None), view(status="failed"), view(size=0),
                view(tool="angelo_scene_object"),
                {"role": "activity", "tool": "angelo_view", "status": "completed", "result": {"error": "no viewport"}},
                {"role": "activity", "tool": "angelo_view", "status": "completed"}, "not a row"]
        evidence = answers.visual_evidence(rows)
        self.assertEqual(evidence, {"images": 4, "sources": {"SCENE_CAMERA": 2, "AXIS:FRONT": 1, "OTHER": 1},
                                    "reused": 1, "current_scene_camera": 1})
        self.assertNotIn("reference.png", str(evidence))
        self.assertEqual(answers.visual_evidence(rows, before=1)["images"], 0)
        self.assertEqual(answers.visual_evidence([])["images"], 0)

    def test_what_supports_an_answer(self):
        camera, front, stale = (answers.visual_evidence([row])
                                for row in (view(), view("AXIS:FRONT"), view(reused=True)))
        self.assertTrue(answers.supports(camera, answers.SCENE_CAMERA))
        self.assertFalse(answers.supports(front, answers.SCENE_CAMERA), "a front view does not show the camera's frame")
        self.assertFalse(answers.supports(stale, answers.SCENE_CAMERA), "a reused view shows an earlier camera")
        self.assertTrue(answers.supports(front, answers.ANY_IMAGE))
        self.assertTrue(answers.supports(answers.visual_evidence([]), None), "a question that needs no image")


class GradeTests(unittest.TestCase):
    def outcome(self, rows, accept=("yes",), spec=VISUAL):
        return answers.grade(spec, list(accept), rows, delivery=DELIVERED)

    def test_an_empty_reply_fails(self):
        for rows in ([], [said("")], [said("   \n")], [view()], [{"role": "user", "text": "ANSWER: yes"}]):
            with self.subTest(rows=rows):
                result = self.outcome(rows)
                self.assertEqual((result["outcome"], result["answered"], result["supported"]), ("empty", None, False))

    def test_a_refusal_or_i_cannot_see_fails(self):
        for text in ("I cannot see an image of the scene.", "I'm not able to look through the camera.",
                     "ANSWER: I cannot tell", "ANSWER:", "ANSWER: No idea", "ANSWER: No image was returned",
                     "ANSWER: nie wiem", "ANSWER: No way to tell from this view"):
            with self.subTest(text=text):
                for truth in ("yes", "no"):   # Whatever the truth is, a refusal is not an answer.
                    self.assertEqual(self.outcome([view(), said(text)], accept=(truth,))["outcome"], "no_answer")

    def test_a_wrong_answer_fails_even_with_the_image(self):
        result = self.outcome([view(), said("It is cut off.\nANSWER: no")])
        self.assertEqual((result["outcome"], result["answered"], result["expected"]), ("wrong", "no", ["yes"]))

    def test_the_right_answer_with_its_observation_passes(self):
        result = self.outcome([said("Let me look."), view(), said("All of it is visible.\nANSWER: yes")])
        self.assertEqual([result[key] for key in ("outcome", "supported", "image_captured", "images_before_answer")],
                         ["right", True, True, 1])

    def test_the_right_answer_with_no_observation_fails(self):
        result = self.outcome([said("ANSWER: yes")])
        self.assertEqual([result[key] for key in ("outcome", "supported", "image_captured")],
                         ["unsupported", False, False])
        for rows in ([view("AXIS:FRONT"), said("ANSWER: yes")], [view(reused=True), said("ANSWER: yes")],
                     [view(status="failed"), said("ANSWER: yes")], [said("ANSWER: yes"), view()]):
            with self.subTest(rows=rows):
                self.assertEqual(self.outcome(rows)["outcome"], "unsupported")
        late = self.outcome([said("ANSWER: yes"), view()])
        self.assertEqual((late["image_captured"], late["images_before_answer"]), (True, 0),
                         "an image captured after the answer was not what the answer rested on")

    def test_a_later_answer_replaces_an_earlier_one(self):
        rows = [said("ANSWER: no"), view(), said("Now that I have looked:\nANSWER: yes"), said("Anything else?")]
        self.assertEqual(self.outcome(rows)["outcome"], "right")

    def test_a_question_that_needs_no_image_is_graded_on_the_answer_alone(self):
        spec = {"form": "counts"}
        self.assertEqual(answers.grade(spec, [[12, 20, 10]], [said("COUNTS: 12 20 10")])["outcome"], "right")
        self.assertEqual(answers.grade(spec, [[12, 20, 10]], [said("COUNTS: 8 12 6")])["outcome"], "wrong")
        either = answers.grade(spec, [[8, 12, 6], [26, 48, 24]], [said("COUNTS: 26 48 24")])
        self.assertEqual(either["outcome"], "right", "an equivalent right answer (the evaluated mesh's counts)")
        shortcut = answers.grade({"form": "shortcut"}, ["ctrl+b"], [said("Press it.\nSHORTCUT: Ctrl+B")])
        self.assertEqual(shortcut["outcome"], "right")
        extra = answers.grade({"form": "names"}, [["Camera", "Cube"]], [said("OBJECTS: Cube, Camera, Light")])
        self.assertEqual(extra["outcome"], "wrong", "an extra name is a wrong answer, unlike the older check")

    def test_a_watched_turn_is_timed_by_its_samples_not_by_row_order(self):
        # The Claude runtime keeps one reply row per turn and rewrites it each round: the final answer sits
        # above the image it was written after, so row order alone would call it unsupported.
        looking, final = [said("Let me look.")], [said("All of it.\nANSWER: yes"), view()]
        self.assertEqual(self.outcome(final)["outcome"], "unsupported", "what row order alone would say")
        watch = answers.Watch("ANSWER")
        for rows in (looking, looking + [view()], final):
            watch.observe(rows)
        result = answers.grade(VISUAL, ["yes"], final, watch, delivery=DELIVERED)
        self.assertEqual((result["outcome"], result["images_before_answer"], result["evidence_timing"]),
                         ("right", 1, "sampled while the turn ran"))

    def test_an_image_in_the_same_sample_as_the_answer_or_after_it_is_not_its_support(self):
        answered, seen = said("ANSWER: yes"), view()
        cases = {
            "the image arrives in the same sample as the answer": ([], [answered, seen]),
            "the answer comes first": ([answered], [answered, seen], [answered, seen]),
            "more is said after the image, but not the answer": (
                [answered], [answered, seen], [answered, seen, said("Let me know if you want it reframed.")]),
            "the one reply row is only rewritten with a trailing newline": (
                [answered], [answered, seen], [said("ANSWER: yes\n"), seen])}
        for label, samples in cases.items():
            with self.subTest(case=label):
                watch = answers.Watch("ANSWER")
                for rows in samples:
                    watch.observe(rows)
                result = answers.grade(VISUAL, ["yes"], list(samples[-1]), watch, delivery=DELIVERED)
                self.assertEqual((result["outcome"], result["image_captured"], result["images_before_answer"]),
                                 ("unsupported", True, 0))
        unsampled = answers.grade(VISUAL, ["yes"], [view(), said("ANSWER: yes")], answers.Watch("ANSWER"),
                                  delivery=DELIVERED)
        self.assertEqual((unsampled["outcome"], unsampled["evidence_timing"]), ("right", "row order"),
                         "a watch that saw nothing leaves scripted rows in their own order")

    def test_an_answer_stated_again_after_looking_is_supported_when_it_is_a_new_line(self):
        first, seen = said("ANSWER: yes"), view()
        again, changed = answers.Watch("ANSWER"), answers.Watch("ANSWER")
        for rows in ([first], [first, seen], [first, seen, said("Having looked:\nANSWER: yes")]):
            again.observe(rows)   # A second row, as Codex writes one per message.
        for rows in ([said("ANSWER: no")], [said("ANSWER: no"), seen], [said("I was wrong.\nANSWER: yes"), seen]):
            changed.observe(rows)   # One row rewritten with another value, as Claude's next round does.
        for watch, rows in ((again, [first, seen, said("Having looked:\nANSWER: yes")]),
                            (changed, [said("I was wrong.\nANSWER: yes"), seen])):
            self.assertEqual(answers.grade(VISUAL, ["yes"], rows, watch, delivery=DELIVERED)["outcome"], "right")

    def test_the_same_answer_to_both_variants_is_constant(self):
        yes = self.outcome([view(), said("ANSWER: yes")], accept=("yes",))
        also_yes = self.outcome([view(), said("ANSWER: yes")], accept=("no",))
        no = self.outcome([view(), said("ANSWER: no")], accept=("no",))
        silent = self.outcome([said("")], accept=("no",))
        self.assertTrue(answers.constant_pair(yes, also_yes))
        self.assertFalse(answers.constant_pair(yes, no))
        self.assertFalse(answers.constant_pair(yes, silent), "no answer is not the same answer")
        self.assertFalse(answers.constant_pair(yes, yes), "the same ground truth is not a pair of variants")

    def test_the_older_names_check_reports_what_it_can(self):
        both = answers.includes_outcome(["Audit B", "Audit D"], [said("Audit B and Audit D")])
        self.assertEqual(both["outcome"], "right")
        self.assertEqual(answers.includes_outcome(["Audit B", "Audit D"], [said("Audit B")]),
                         {"form": "includes", "outcome": "wrong", "missing": ["Audit D"]})
        self.assertEqual(answers.includes_outcome(["Audit B"], [said("")])["outcome"], "empty")


if __name__ == "__main__":
    unittest.main()
