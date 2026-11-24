# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where an FBX clip set may be written (``clip_paths``), with C's answers stood in for (no Blender).

Every target is checked before anything is written; a clip file that exists and C refuses (someone else's file where a
clip would go) stops the export with nothing written and nothing made; a refusal while making the clip files removes
the ones already made; names never leave the picked file's folder. In a background Blender the guard allows every path
(probed on the Linux lane VM, 2026-11-18), so this is where refusals are pinned; the real confined app's answers are
test_blender_clip_export_director_gui's.

From the 2.1.0 exports review: two actions that clean or case-fold to one file are refused by name (R-S3); an
existing clip file Angelo did not make needs the person's confirm, the same in either state of C, and is asked of C
(R-S2; the export vouches it for its one call: break-it review M2, ``NoPickOutlivesItsRun``); the model file is the
picked file when it already ends in .fbx (R-N1); the save browser starts from a clean name in the document's folder,
never a path from an object's name (R-S1, ``export_names``).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import clip_paths, owned_paths  # noqa: E402


class ClipPaths(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.model = os.path.join(self.folder, "hero.fbx")

    def refuse(self, *refused):
        def require(path):
            if os.fspath(path) in refused:
                raise PermissionError("refused: %s" % path)
            return path
        return mock.patch.object(owned_paths, "require_writable", side_effect=require)

    def test_names_stay_beside_the_picked_file(self):
        clips = clip_paths.clip_files(self.model, ["Walk", "../../etc/passwd", "a:b*c"])
        for path in clips.values():
            self.assertEqual(os.path.dirname(path), self.folder)
            self.assertTrue(os.path.basename(path).startswith("hero@"))

    def test_a_refused_existing_clip_file_stops_everything_first(self):
        clips = list(clip_paths.clip_files(self.model, ["Walk", "Run"]).values())
        Path(clips[1]).write_bytes(b"someone else's")
        with self.refuse(clips[1]), self.assertRaises(PermissionError):
            clip_paths.claim(self.model, clips)
        self.assertFalse(os.path.exists(clips[0]))   # Nothing made.
        self.assertEqual(Path(clips[1]).read_bytes(), b"someone else's")

    def test_a_refusal_while_making_removes_what_was_made(self):
        clips = list(clip_paths.clip_files(self.model, ["Walk", "Run", "Jump"]).values())
        made, real = [], owned_paths.create_beside

        def create(named, name):
            if name.endswith("Jump.fbx"):
                raise PermissionError("refused")
            path = real(named, name)
            made.append(path)
            return path
        with mock.patch.object(owned_paths, "create_beside", side_effect=create), \
                mock.patch.object(owned_paths, "owned", return_value=True), self.assertRaises(PermissionError):
            clip_paths.claim(self.model, clips)
        self.assertEqual(len(made), 2)
        self.assertFalse(any(os.path.exists(path) for path in made))

    def test_the_picked_file_is_checked_first(self):
        with self.refuse(self.model), self.assertRaises(PermissionError):
            clip_paths.claim(self.model, list(clip_paths.clip_files(self.model, ["Walk"]).values()))
        self.assertEqual(os.listdir(self.folder), [])

    # 2.1.0 exports review (R-S2, R-S3, R-N1), fail first.

    def test_two_actions_writing_one_file_are_refused_by_name(self):
        """``a/b`` and ``a_b`` clean to one name; ``Walk`` and ``walk`` are one file on macOS and Windows."""
        for names in (["a/b", "a_b"], ["Walk", "walk"], ["Run:1", "run*1", "Idle"]):
            with self.subTest(names=names), self.assertRaises(clip_paths.ClipNameClash) as caught:
                clip_paths.clip_files(self.model, names)
            self.assertTrue(set(names[:2]) <= set(caught.exception.names), caught.exception.names)
        self.assertEqual(len(clip_paths.clip_files(self.model, ["Walk", "Run", "Walk2"])), 3)

    def test_an_existing_clip_file_angelo_did_not_make_needs_the_persons_confirm(self):
        """The same whether or not C would allow it (before any script region C allows every path)."""
        clips = list(clip_paths.clip_files(self.model, ["Walk", "Run"]).values())
        Path(clips[0]).write_bytes(b"the person's clip")
        with mock.patch.object(owned_paths, "owned", return_value=False), \
                self.assertRaises(clip_paths.ReplaceUnconfirmed) as caught:
            clip_paths.claim(self.model, clips)   # C stood in as allowing every path: still asked.
        self.assertEqual(caught.exception.paths, [clips[0]])
        self.assertEqual(Path(clips[0]).read_bytes(), b"the person's clip")
        self.assertFalse(os.path.exists(clips[1]))   # Nothing made before the confirm.
        asked = []
        with mock.patch.object(owned_paths, "owned", return_value=False), \
                mock.patch.object(owned_paths, "may_write", side_effect=lambda p: asked.append(p) or True), \
                mock.patch.object(owned_paths, "vouch", side_effect=AssertionError("claim never vouches")):
            made = clip_paths.claim(self.model, clips, confirmed=[clips[0]])
        self.assertIn(clips[0], asked)   # Confirmed, then asked of C (the export vouches it for the call).
        self.assertEqual(made, [clips[1]])

    def test_a_clip_file_angelo_made_is_replaced_without_asking(self):
        clips = list(clip_paths.clip_files(self.model, ["Walk"]).values())
        Path(clips[0]).write_bytes(b"made by Angelo this session")
        with mock.patch.object(owned_paths, "owned", return_value=True):
            self.assertEqual(clip_paths.claim(self.model, clips), [])

    def test_a_confirm_that_c_will_not_record_is_refused_naming_the_file(self):
        """While a script runs C records no vouch, so C refuses the file: the refusal names it."""
        clips = list(clip_paths.clip_files(self.model, ["Walk"]).values())
        Path(clips[0]).write_bytes(b"the person's clip")
        with mock.patch.object(owned_paths, "owned", return_value=False), \
                mock.patch.object(owned_paths, "may_write", side_effect=lambda p: p != clips[0]), \
                self.assertRaises(clip_paths.ReplaceUnconfirmed) as caught:
            clip_paths.claim(self.model, clips, confirmed=[clips[0]])
        self.assertEqual(caught.exception.paths, [clips[0]])

    def test_a_folder_or_link_at_a_clip_name_is_refused_by_name_never_offered_to_replace(self):
        """Break-it review NIT: a folder or a link is never a clip file to replace, so the person is told what is in
        the way instead of being asked to confirm a replace C would refuse anyway."""
        clips = list(clip_paths.clip_files(self.model, ["Walk", "Run", "Idle"]).values())
        os.mkdir(clips[0])
        elsewhere = os.path.join(self.folder, "elsewhere.fbx")
        Path(elsewhere).write_bytes(b"the link's target")
        os.symlink(elsewhere, clips[1])
        with mock.patch.object(owned_paths, "owned", return_value=False):
            self.assertEqual(clip_paths.to_replace(clips), [])
            for confirmed in ((), clips[:2]):
                with self.subTest(confirmed=confirmed), self.assertRaises(clip_paths.NotAFile) as caught:
                    clip_paths.claim(self.model, clips, confirmed=confirmed)
                self.assertEqual(caught.exception.paths, clips[:2])
        self.assertFalse(os.path.lexists(clips[2]))   # Nothing made.
        self.assertEqual(Path(elsewhere).read_bytes(), b"the link's target")

    def test_a_file_that_appeared_after_the_browser_drew_is_asked_again(self):
        """Re-read S-D: Replace covers the files the browser showed; one that appeared since is the person's to see."""
        clips = list(clip_paths.clip_files(self.model, ["Walk", "Run"]).values())
        Path(clips[0]).write_bytes(b"shown in the browser")
        shown = list(clips[:1])
        Path(clips[1]).write_bytes(b"appeared after the browser drew")
        with mock.patch.object(owned_paths, "owned", return_value=False), \
                mock.patch.object(owned_paths, "may_write", return_value=True), \
                self.assertRaises(clip_paths.ReplaceUnconfirmed) as caught:
            theirs = clip_paths.to_replace(clips)
            clip_paths.claim(self.model, clips, confirmed=clip_paths.confirmed(shown, theirs))
        self.assertEqual(caught.exception.paths, [clips[1]])
        self.assertEqual(Path(clips[0]).read_bytes(), b"shown in the browser")

    def test_the_model_file_is_the_picked_file(self):
        for picked, model in (("Hero.FBX", "Hero.FBX"), ("hero.fbx", "hero.fbx"), ("hero.glb", "hero.fbx"),
                              ("hero", "hero.fbx")):
            with self.subTest(picked=picked):
                self.assertEqual(clip_paths.model_file(os.path.join(self.folder, picked)),
                                 os.path.join(self.folder, model))


class DefaultPath(unittest.TestCase):
    """R-S1: the save browser's starting path is never built from scene data (rigging_ops, read from source)."""

    def test_the_export_browser_starts_from_a_clean_name_only(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/rigging_ops.py").read_text(encoding="utf-8")
        start = source.index("class ANGELO_OT_export_action_clips")
        body = source[start:source.index("\nclass ", start + 10)]
        invoke = body[body.index("def invoke"):body.index("def run")]
        self.assertIn("export_names.default_export_path(", invoke)
        self.assertNotIn("os.path.join", invoke)



class NoPickOutlivesItsRun(unittest.TestCase):
    """2.1.0 clip export break-it review M2: a save browser's pick counts for that operator's run only (C's PICK
    scope). No Python caller promotes it: nothing in the clip export vouches a path for the session, the confirm to
    replace the person's clip files is a box in that same browser (never a second run that would need the pick
    carried), unticked whenever the browser opens, and the files ticked are vouched only for the export's call."""

    ADDON = ROOT / "scripts/addons_core/angelo_agent"

    def test_nothing_in_the_clip_export_vouches_for_the_session(self):
        import ast
        for name in ("rigging_ops.py", "clip_paths.py", "action_clips.py", "clip_set.py", "clip_stage.py"):
            path = self.ADDON / name
            if not path.exists():
                continue
            with self.subTest(module=name):
                calls = [n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(n, ast.Call)
                         and getattr(n.func, "attr", "") == "vouch"]
                self.assertEqual([ast.unparse(c) for c in calls], [])

    def test_the_replace_confirm_is_in_the_same_browser_and_starts_unticked(self):
        source = (self.ADDON / "rigging_ops.py").read_text(encoding="utf-8")
        self.assertNotIn("export_action_clips_replace", source)
        start = source.index("class ANGELO_OT_export_action_clips")
        body = source[start:source.index("\n\n\n", start)]
        self.assertIn("replace_existing: BoolProperty(", body)
        self.assertIn("'SKIP_SAVE'", body[body.index("replace_existing: BoolProperty("):])
        invoke = body[body.index("def invoke"):body.index("def run")]
        self.assertIn("self.replace_existing = False", invoke)
        self.assertIn("vouched_during", (self.ADDON / "action_clips.py").read_text(encoding="utf-8"))


class ReplaceOnlyWhatWasShown(unittest.TestCase):
    """The clip export re-read of 30ade86d137, S-D: the browser's box names the files Replace covers (eight, then how
    many more) and the export replaces exactly the files that box showed, never a set worked out again at Return."""

    ADDON = ROOT / "scripts/addons_core/angelo_agent"

    def test_confirmed_is_what_was_shown_and_is_still_there(self):
        a, b = "/x/hero@Walk.fbx", "/x/hero@Run.fbx"
        self.assertEqual(clip_paths.confirmed([a], [a, b]), [a])
        self.assertEqual(clip_paths.confirmed([], [a]), [])
        self.assertEqual(clip_paths.confirmed([a, b], [b]), [b])

    def test_the_box_counts_what_it_does_not_list_and_the_export_uses_what_it_drew(self):
        source = (self.ADDON / "rigging_ops.py").read_text(encoding="utf-8")
        start = source.index("class ANGELO_OT_export_action_clips")
        body = source[start:source.index("\n\n\n", start)]
        draw = body[body.index("def draw"):body.index("def invoke")]
        run = body[body.index("def run"):]
        self.assertIn("{names} and {count} more", draw)
        self.assertIn("_SHOWN[", draw)
        self.assertIn("_SHOWN.pop(", run)
        self.assertIn("clip_paths.confirmed(", run)
        self.assertNotIn("self._theirs(context) if self.replace_existing", run)


if __name__ == "__main__":
    unittest.main()
