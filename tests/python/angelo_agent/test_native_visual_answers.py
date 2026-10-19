# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A visual question passes only when the model saw the picture: by its own account and by the receipt.

In a recorded survey a "Is Eval Cube fully in frame?" answer
passed although the model said it could not see an image. A visual answer
(``eval_answers.grade`` with ``evidence``) now needs all of:

* a view captured before the answer line (the chat rows, as before);
* no statement in the answering reply that the model had no image
  (``no_image_claim``): such an answer is ``unsupported``, whatever its line;
* the receipt's proof that a picture of the kind the question needs reached
  the model in a provider turn it accepted (``path["images"]``,
  ``request_path``). None delivered is ``unsupported``; delivery the runtime
  cannot prove (Codex code-mode results, a receipt without a path section)
  is ``unverified``: ungraded, never passed.

Scripted rows and receipt paths; no Blender, process or model.
"""

import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import eval_answers as answers  # noqa: E402
import eval_grading_live  # noqa: E402
import oneshot_metrics as metrics  # noqa: E402
import eval_survey_tasks as survey  # noqa: E402
import survey_fixtures  # noqa: E402
import survey_truth  # noqa: E402

VISUAL = {"form": "yes_no", "evidence": answers.SCENE_CAMERA}
VIEW = {"role": "activity", "tool": "angelo_view", "status": "completed",
        "result": {"views": [{"bytes": 512, "camera": {"source": answers.SCENE_CAMERA}}]}}
# Receipt ``path`` sections as request_path records them.
DELIVERED = {"edits": 0, "images_delivered": 1, "images": [[2, 0, {"view:SOLID:SCENE_CAMERA": 1}]],
             "image_delivery": "confirmed"}
FIT_ONLY = {"edits": 0, "images_delivered": 1, "images": [[2, 0, {"view:SOLID:FIT": 1}]],
            "image_delivery": "confirmed"}
NOTHING = {"edits": 0, "images_returned": 1, "image_delivery": "none"}
CODEX = {"edits": 0, "images_unconfirmed": 1, "unconfirmed_images": [[1, 0, {"view:SOLID:SCENE_CAMERA": 1}]],
         "image_delivery": "unknown"}


def said(text):
    return {"role": "assistant", "text": text}


def grade(rows, delivery=DELIVERED, accept=("yes",)):
    return answers.grade(VISUAL, list(accept), rows, delivery=delivery)


class ModelsOwnAccountTests(unittest.TestCase):
    def test_an_answer_that_says_it_saw_no_image_fails_whatever_its_line(self):
        for text in ("I can't see the image, but from the camera's position it should fit.\nANSWER: yes",
                     "I cannot see the render itself. Going by the numbers:\nANSWER: yes",
                     "I couldn't view the screenshot.\nANSWER: yes",
                     "The image didn't load, so I can't confirm visually.\nANSWER: yes",
                     "No image was returned by the view.\nANSWER: yes",
                     "I don't have access to the picture; based on the bounds:\nANSWER: yes",
                     "Without seeing the image, I'd say:\nANSWER: yes",
                     "I'm unable to view images.\nANSWER: yes"):
            with self.subTest(text=text):
                result = grade([VIEW, said(text)])
                self.assertEqual(result["outcome"], "unsupported", result)
                self.assertTrue(result["no_image_claim"])

    def test_ordinary_answers_about_what_is_visible_are_not_claims(self):
        for text in ("I can't see any part of it cut off.\nANSWER: yes",
                     "Nothing is outside the frame; no edge is clipped.\nANSWER: yes",
                     "The whole cube is visible in the camera view.\nANSWER: yes",
                     "I don't see any cropping.\nANSWER: yes"):
            with self.subTest(text=text):
                self.assertEqual(grade([VIEW, said(text)])["outcome"], "right")

    def test_an_earlier_claim_that_a_later_look_answered_does_not_count(self):
        rows = [said("I can't see the image yet, let me look again."), VIEW, said("Now I see it.\nANSWER: yes")]
        self.assertEqual(grade(rows)["outcome"], "right")


class ReceiptTests(unittest.TestCase):
    def test_a_view_the_receipt_never_delivered_fails(self):
        result = grade([VIEW, said("All of it is visible.\nANSWER: yes")], delivery=NOTHING)
        self.assertEqual((result["outcome"], result["delivery"]), ("unsupported", "none"))

    def test_a_delivered_view_through_another_framing_does_not_show_the_camera_frame(self):
        self.assertEqual(grade([VIEW, said("ANSWER: yes")], delivery=FIT_ONLY)["outcome"], "unsupported")

    def test_delivery_the_runtime_cannot_prove_is_unverified(self):
        for delivery in (CODEX, None):
            with self.subTest(delivery=delivery):
                result = grade([VIEW, said("All of it is visible.\nANSWER: yes")], delivery=delivery)
                self.assertEqual((result["outcome"], result["delivery"]), ("unverified", "unknown"))

    def test_a_proven_delivery_with_the_right_answer_passes(self):
        result = grade([VIEW, said("All of it is visible.\nANSWER: yes")])
        self.assertEqual((result["outcome"], result["delivery"]), ("right", "confirmed"))
        wrong = grade([VIEW, said("ANSWER: no")], delivery=NOTHING)
        self.assertEqual(wrong["outcome"], "wrong", "a wrong answer stays wrong")

    def test_a_question_that_needs_no_image_ignores_delivery(self):
        result = answers.grade({"form": "counts"}, [[12, 20, 10]], [said("COUNTS: 12 20 10")], delivery=None)
        self.assertEqual(result["outcome"], "right")


class TrialDriverTests(unittest.TestCase):
    """``LiveGrading`` reads delivery from the task's receipt; an unverified answer is ungraded, never passed."""

    def run_task(self, live, task, rows, receipt, accept):
        record = {"task": task["id"], "kind": task["kind"], "status": "completed", "receipt": receipt}
        with mock.patch.object(survey_fixtures, "apply", lambda bpy, scene, fixture: ([], None)), \
                mock.patch.object(survey_truth, "truth", lambda bpy, scene, spec: {"accept": list(accept)}):
            live.before(None, mock.Mock(objects=[]), task, record)
            record["host_check"] = {"scene_unchanged": True}
            record["host_check_passed"] = live.after(None, mock.Mock(objects=[]), task, record, True, list(rows))
        return record

    def test_the_pair_passes_only_with_delivered_views(self):
        tasks = survey.build_survey_tasks()
        by_id = {task["id"]: task for task in tasks}
        for path, expected in ((DELIVERED, (True, "right")), (NOTHING, (False, "unsupported")),
                               (CODEX, (None, "unverified")), (None, (None, "unverified"))):
            with self.subTest(path=path):
                live = eval_grading_live.LiveGrading("survey", tasks, {"tasks": []}, lambda c, o: c, lambda s, o: {})
                receipt = {"status": "completed", **({"path": copy.deepcopy(path)} if path else {})}
                first = self.run_task(live, by_id["view_check"], [VIEW, said("ANSWER: yes")], receipt, ["yes"])
                second = self.run_task(live, by_id["view_check_cropped"], [VIEW, said("ANSWER: no")], receipt, ["no"])
                self.assertEqual((second["host_check_passed"], second["host_check"]["answer"]["outcome"]), expected)
                self.assertEqual(first["host_check_passed"], expected[0])



class OneShotPathTests(unittest.TestCase):
    """The MCP side's "saw its final render" and "looked after its last edit" need a picture in the tool result."""

    def lines(self, read_result, screenshot_result):
        from test_native_oneshot_metrics import claude_lines
        lines = claude_lines()
        for line in lines:
            for block in (line.get("message") or {}).get("content") or ():
                if block.get("type") == "tool_result" and block.get("tool_use_id") in ("t4", "t5"):
                    block["content"] = read_result if block["tool_use_id"] == "t4" else screenshot_result
        return lines

    def test_a_read_or_screenshot_that_returned_no_picture_is_not_a_look(self):
        from test_native_oneshot_metrics import mcp_record
        failed = [{"type": "text", "text": "File does not exist."}]
        image = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}]
        seen = metrics.mcp_path(mcp_record(), self.lines(image, image), {"messages": 1}, "claude-opus-5-5",
                                "footbridge-final.png")["looked"]
        self.assertEqual((seen["final_render_seen"], seen["after_last_edit"]), (True, True))
        blind = metrics.mcp_path(mcp_record(), self.lines(failed, failed), {"messages": 1}, "claude-opus-5-5",
                                 "footbridge-final.png")["looked"]
        self.assertIsNot(blind["final_render_seen"], True, "Reading the file is not seeing it when no image came back")
        self.assertIs(blind["after_last_edit"], False, "A screenshot that returned no picture is no look")


if __name__ == "__main__":
    unittest.main()
