# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Make Video from This Shot and the Video tab's camera hook (``shot_capture_ui``). No Blender needed.

The Video tab's contract (v108-fal-video, studio_video_ui, 2026-10-30): ``set_camera_source(source)`` with
``source(which, aspect, deliver)`` returning at once and calling ``deliver(path)`` or ``deliver(None, reason)`` (an
``n_`` sentence) later; ``which`` is "first" or "last", and a last frame's aspect is "". ``show_shot(window, first,
last, video, name)`` opens the tab with the shot and returns its state for ``choose_options``. Here: the hook asks
``shot_capture`` for one frame, never broadcast, and a last frame takes the first frame's shape; a finished shot opens
in the tab with its aspect, or goes to the library without one; a tab that refuses the shot is said in the chat.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
import angelo_agent  # noqa: E402

STAND_INS = {"bpy": MagicMock(), "bpy.types": MagicMock(), "bpy.props": MagicMock(), "bpy.app": MagicMock(),
             "bpy.app.handlers": MagicMock()}
with patch.dict(sys.modules, STAND_INS):
    from angelo_agent import shot_capture, shot_capture_ui  # noqa: E402


def shot(**fields):
    values = dict(camera="Cam", aspect="9:16", size=(720, 1280), folder="shots/Cam-1")
    values.update(fields)
    return shot_capture.Shot(**values)


class CameraSource(unittest.TestCase):
    def setUp(self):
        saved = dict(shot_capture_ui.settings)
        self.addCleanup(lambda: shot_capture_ui.settings.update(saved))

    def source(self, which, aspect, start):
        delivered = []
        with patch.object(shot_capture, "start", start), patch.dict(sys.modules, STAND_INS):
            shot_capture_ui.camera_source(which, aspect, lambda *args: delivered.append(args),
                                          window=object(), controller=object())
        return delivered

    def test_one_frame_is_rendered_and_its_file_delivered(self):
        asked = {}

        def start(window, controller, **kwargs):
            asked.update(kwargs)
            kwargs["on_done"](shot(files={"first": Path("/w/f.png")}))
        self.assertEqual(self.source("first", "9:16", start), [(str(Path("/w/f.png")),)])   # A native file path.
        self.assertEqual((asked["parts"], asked["aspect"], asked["broadcast"]), (("first",), "9:16", False))

    def test_a_last_frame_takes_the_first_frames_shape(self):
        asked = []

        def start(window, controller, **kwargs):
            asked.append(kwargs["aspect"])
        self.source("first", "21:9", start)
        self.source("last", "", start)
        self.assertEqual(asked, ["21:9", "21:9"])

    def test_a_failed_frame_and_a_refusal_deliver_a_translatable_reason(self):
        def failed(window, controller, **kwargs):
            kwargs["on_done"](shot(errors={"last": "cancelled"}))
        self.assertEqual(self.source("last", "", failed), [(None, shot_capture_ui.NO_FRAME)])

        def refused(window, controller, **kwargs):
            raise shot_capture.ShotError("This scene has no active camera; add one or set one as active")
        self.assertEqual(self.source("first", "16:9", refused),
                         [(None, "This scene has no active camera; add one or set one as active")])


class ShotReady(unittest.TestCase):
    def ready(self, finished, tab):
        posted = []
        with patch.object(shot_capture_ui, "_post", lambda session, rows: posted.extend(rows)), \
                patch.object(shot_capture_ui, "_window", lambda held: held()), \
                patch.object(shot_capture_ui, "_to_library", MagicMock()) as library, \
                patch.object(shot_capture_ui, "_video_tab", lambda: tab):   # None: a build without the tab.
            shot_capture_ui.shot_ready(finished, "chat", lambda: "window")
        return posted, library

    def test_frames_open_in_the_video_tab_with_the_shots_aspect(self):
        tab = SimpleNamespace(show_shot=MagicMock(return_value="state"), choose_options=MagicMock())
        posted, library = self.ready(shot(files={"first": Path("/w/f.png"), "last": Path("/w/l.png")}), tab)
        tab.show_shot.assert_called_once_with("window", first=str(Path("/w/f.png")), last=str(Path("/w/l.png")),
                                              video=None, name="Cam")
        tab.choose_options.assert_called_once_with("state", aspect="9:16")
        library.assert_not_called()
        self.assertEqual([row["i18n"][0] for row in posted], [shot_capture_ui.READY])

    def test_the_ready_row_opens_the_video_tab_and_never_shows_the_folder(self):
        tab = SimpleNamespace(show_shot=MagicMock(return_value="state"), choose_options=MagicMock())
        posted, _ = self.ready(shot(files={"first": Path("/w/f.png")}), tab)
        self.assertEqual(posted[0]["open_studio"], "video")   # chat_rows draws its Open Video tab button.
        self.assertIn("Cam", posted[0]["text"])
        self.assertNotIn("shots/", posted[0]["text"])

    def test_each_part_has_a_card_title_in_the_persons_words(self):
        self.assertEqual(set(shot_capture.JOB_TITLES), set(shot_capture.PARTS))
        self.assertTrue(all(not title.startswith("Rendering") for title in shot_capture.JOB_TITLES.values()))

    def test_a_camera_move_opens_as_the_reference_video(self):
        tab = SimpleNamespace(show_shot=MagicMock(return_value="state"), choose_options=MagicMock())
        self.ready(shot(files={"motion": Path("/w/m.mp4")}), tab)
        tab.show_shot.assert_called_once_with("window", first=None, last=None, video=str(Path("/w/m.mp4")), name="Cam")

    def test_a_tab_that_refuses_is_said_in_the_chat(self):
        tab = SimpleNamespace(show_shot=MagicMock(side_effect=ValueError("not a PNG")))
        posted, _ = self.ready(shot(files={"first": Path("/w/f.png")}), tab)
        self.assertEqual([row["i18n"][0] for row in posted], [shot_capture_ui.READY, shot_capture_ui.NOT_OPENED])

    def test_without_a_video_tab_the_frames_go_to_the_library(self):
        posted, library = self.ready(shot(files={"first": Path("/w/f.png")}, errors={"last": "cancelled"}), None)
        library.assert_called_once()
        self.assertEqual([row["i18n"][0] for row in posted], [shot_capture_ui.READY, shot_capture_ui.NOT_MADE])

    def test_nothing_made_says_why_and_opens_nothing(self):
        tab = SimpleNamespace(show_shot=MagicMock())
        posted, _ = self.ready(shot(errors={"first": "x", "last": "y"}), tab)
        tab.show_shot.assert_not_called()
        self.assertEqual(len(posted), 2)


class Binding(unittest.TestCase):
    def test_the_hook_is_bound_only_when_the_tab_exists(self):
        tab = SimpleNamespace(set_camera_source=MagicMock())
        with patch.dict(sys.modules, {"angelo_agent.studio_video_ui": tab}), \
                patch.object(angelo_agent, "studio_video_ui", tab, create=True):
            self.assertIs(shot_capture_ui._video_tab(), tab)
        # A build without the tab: nothing to bind. The package attribute goes too: an earlier test's real import
        # leaves it, and `from . import` answers from it before sys.modules (only in a full-suite run).
        with patch.dict(sys.modules, {"angelo_agent.studio_video_ui": None}), patch.dict(vars(angelo_agent)):
            vars(angelo_agent).pop("studio_video_ui", None)
            self.assertIsNone(shot_capture_ui._video_tab())

    def test_each_entry_names_its_parts(self):
        self.assertEqual(shot_capture_ui.MODES, {"FRAMES": ("first", "last"), "MOTION": ("motion",)})


if __name__ == "__main__":
    unittest.main()
