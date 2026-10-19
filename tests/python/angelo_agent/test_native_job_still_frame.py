# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A RENDER_STILL's frame as models write it (job_spec.still_frame), and nothing more.

On the bridge one-shot, whose brief asks for frame 60, a still's start was refused twice before it ran: for
``frame`` (not in the schema), then for ``frame_end`` equal to ``frame_start`` (a still takes no end), costing
6-14 s a trial. Both name the one frame a still renders, so the host reads them so when the frame is whole and in
range; every other frame field and value, and every other kind, is left as written and refused as before.
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent.job_spec import JobRequestError, still_frame, validate_job  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

STILL = {"kind": "RENDER_STILL", "output": "renders/bridge-final.png"}


def start(**fields):
    return validate_job("angelo_job_start", dict(STILL, **fields))


class StillFrameTests(unittest.TestCase):
    def test_frame_alone_is_frame_start_and_an_equal_end_is_the_same_frame(self):
        expected = start(frame_start=60)
        self.assertEqual(expected["frame_start"], 60)
        for fields in ({"frame": 60}, {"frame_start": 60, "frame_end": 60}, {"frame": 60, "frame_end": 60}):
            with self.subTest(fields=fields):
                self.assertEqual(start(**fields), expected)
                self.assertEqual(validate_call("angelo_job_start", dict(STILL, **fields)), expected)
        self.assertEqual(start(frame=0)["frame_start"], 0)          # Frame 0 and negative frames are frames.
        self.assertEqual(start(frame=-5, frame_end=-5)["frame_start"], -5)

    def test_every_other_frame_field_or_value_is_refused_with_the_same_text(self):
        # Review N1: only whole frames in range are read; anything else is left as written, so its refusal is unchanged.
        cases = [
            ({"frame_start": 60, "frame_end": 61}, "RENDER_STILL does not take: frame_end"),
            ({"frame_end": 60}, "RENDER_STILL does not take: frame_end"),
            ({"frame": 60, "frame_start": 60}, "RENDER_STILL does not take: frame"),
            ({"frame": 60, "frame_end": 61}, "RENDER_STILL does not take: frame"),
            ({"frame": "60"}, "RENDER_STILL does not take: frame"),
            ({"frame": 60.0}, "RENDER_STILL does not take: frame"),
            ({"frame": True}, "RENDER_STILL does not take: frame"),
            ({"frame": None}, "RENDER_STILL does not take: frame"),
            ({"frame": 2 ** 40}, "RENDER_STILL does not take: frame"),
            ({"frame_start": 2 ** 40, "frame_end": 2 ** 40}, "RENDER_STILL does not take: frame_end"),
            ({"frame_start": 60, "frame_end": 60.0}, "RENDER_STILL does not take: frame_end"),
            ({"frame_start": True, "frame_end": 1}, "RENDER_STILL does not take: frame_end"),
        ]
        for fields, text in cases:
            with self.subTest(fields=fields):
                with self.assertRaises(JobRequestError) as caught:
                    start(**fields)
                self.assertIn(text, str(caught.exception))
                if "frame" in fields:   # The schema check refuses it first, as before.
                    with self.assertRaises(ValueError) as caught:
                        validate_call("angelo_job_start", dict(STILL, **fields))
                    self.assertEqual(str(caught.exception), "Unexpected tool argument")

    def test_the_schema_check_still_refuses_frame_where_it_is_no_still_s_frame(self):
        for args in (dict(STILL, frame=60, frame_start=60),
                     {"kind": "RENDER_ANIMATION", "output": "renders/walk/", "frame": 60},
                     {"kind": "EXPORT_USD", "output": "scene.usdc", "frame": 1},
                     {"output": "a.png", "frame": 60}):
            with self.subTest(args=args):
                with self.assertRaises(ValueError) as caught:
                    validate_call("angelo_job_start", args)
                self.assertEqual(str(caught.exception), "Unexpected tool argument")

    def test_other_kinds_keep_their_frames_and_the_input_is_never_changed(self):
        animation = {"kind": "RENDER_ANIMATION", "output": "renders/walk/", "frame_start": 1, "frame_end": 1}
        self.assertEqual(validate_job("angelo_job_start", dict(animation))["frame_end"], 1)
        args = dict(STILL, frame=60, frame_end=60)
        self.assertIsNot(still_frame(args), args)
        self.assertEqual(args, dict(STILL, frame=60, frame_end=60))
        self.assertIs(still_frame(STILL), STILL)
        for value in (None, [], "RENDER_STILL"):
            self.assertIs(still_frame(value), value)


if __name__ == "__main__":
    unittest.main()
