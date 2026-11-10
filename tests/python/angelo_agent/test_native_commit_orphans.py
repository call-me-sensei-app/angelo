# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a commit frees the data its swaps replaced (``commit_orphans``), pinned without Blender.

The behaviour itself is measured through Blender: ``test_blender_commit_orphans_production.py`` (the production
path, the native right) and ``test_blender_commit_orphans.py`` (the fixtures' path). These pins keep the parts a
refactor could silently drop: the native right and its authority, the preview stop, the seal before the first
write, the counted refusals and the note.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import commit_orphans  # noqa: E402
from angelo_agent.program_commit_report import LEFTOVER_NOTE, NOT_FREED_NOTE, ProgramCommitReportMixin  # noqa: E402

UNDO = (ROOT / "source/blender/python/intern/bpy_angelo_undo.cc").read_text(encoding="utf-8")
RIGHTS = (ROOT / "source/blender/editors/undo/angelo_commit_rights.cc").read_text(encoding="utf-8")
COMMIT = (ROOT / "scripts/addons_core/angelo_agent/program_commit.py").read_text(encoding="utf-8")


class NativeRightTests(unittest.TestCase):
    def test_the_bridge_offers_the_seal_and_the_right(self):
        for name in ("seal_replaceable", "delete_replaced_data"):
            self.assertIn('{"%s", undo_%s, METH_VARARGS' % (name, name), UNDO)

    def test_the_authority_is_the_sealed_closure_never_a_python_list(self):
        self.assertIn("case AngeloCommitDeletePurpose::ReplacedData:\n      authorized = scope.replaceable;", UNDO)
        self.assertIn("ED_angelo_commit_replaceable(", UNDO)

    def test_every_preview_job_stops_before_the_kernel_frees_anything(self):
        self.assertNotIn("ED_preview_kill_jobs_for_id", RIGHTS, "a mesh, Key or settings has no preview of its own")
        for body in (RIGHTS[RIGHTS.index("bool ED_angelo_commit_delete("):RIGHTS.index("void ED_angelo_commit_delete_free")],
                     RIGHTS[RIGHTS.index("bool ED_angelo_commit_free_replaced("):]):
            self.assertLess(body.index("kill_preview_jobs(C"), body.index("const LiveIDs"),
                            "the scope and the proof are read after the jobs stop (stopping can release the GIL)")
            self.assertLess(body.index("kill_preview_jobs(C"), body.index("BKE_id_multi_delete("))
        self.assertIn("ED_preview_kill_jobs(wm, main)", RIGHTS)

    def test_only_roots_are_freed_never_what_they_released(self):
        body = RIGHTS[RIGHTS.index("bool ED_angelo_commit_free_replaced("):]
        self.assertNotIn("released", body.split("Data the replaced blocks merely used")[1][:400])
        self.assertIn("deleting.add(root.pointer)", body)
        self.assertIn('"The kernel freed IDs outside the proven replaced-data closure"', body)
        self.assertIn("prove_replaced_candidates(scope, candidates, authorized, live, admitted, freeing, refused)", body)
        helper = RIGHTS[RIGHTS.index("static void prove_replaced_candidates("):RIGHTS.index("}  // namespace", RIGHTS.index("static void prove_replaced_candidates("))]
        self.assertIn("alone.size() > 1 && prove_deletion(", helper, "roots refused alone are tried together")
        self.assertIn("freeing.extend(alone)", helper)

    def test_the_filters_are_checked_natively(self):
        rule = RIGHTS[RIGHTS.index("static bool unused_replaceable"):RIGHTS.index("static bool purpose_allows")]
        for part in ("is_local_owned_id(id)", "id->us == 0", "ID_FLAG_FAKEUSER", "ID_IS_ASSET(id)"):
            self.assertIn(part, rule)

    def test_the_seal_comes_before_the_first_write(self):
        self.assertLess(COMMIT.index("commit_orphans.seal(scope, stage, self.scene)"), COMMIT.index("journals.append("))
        self.assertLess(COMMIT.index("self._remove_stage_ids(stage, keep)"),
                        COMMIT.index("commit_orphans.free_replaced(replaced, scope)"))


class Block:
    def __init__(self, name, users=0, kind="MESH"):
        self.name, self.users, self.id_type = name, users, kind
        self.use_fake_user = self.is_embedded_data = False
        self.library = self.override_library = self.asset_data = None

    def as_pointer(self):
        return id(self)


class PathTests(unittest.TestCase):
    def test_a_binary_without_the_right_frees_nothing_and_says_so(self):
        reasons = []
        scope = SimpleNamespace(frees_replaced_data=lambda: False)
        self.assertEqual(commit_orphans._free_native(scope, [Block("Cube")], reasons), 0)
        self.assertEqual(reasons, ["this Angelo binary has no native right to free replaced data"])

    def test_every_refusal_pass_limit_error_and_stray_deletion_is_reported(self):
        receipt = {"success": False, "removed": [], "refused": [((1, 2, "MESH"), "A surviving ID now depends"),
                                                               ((3, 4, "NODETREE"), "retained")],
                   "passes": 16, "unfinished": True, "unexpected": 2, "error": "outside the proof"}
        reasons = []
        scope = SimpleNamespace(frees_replaced_data=lambda: True, delete_replaced=lambda roots, passes: receipt)
        self.assertEqual(commit_orphans._free_native(scope, [Block("Cube")], reasons), 2)
        self.assertEqual(reasons, ["A surviving ID now depends", "retained", "freeing stopped after 16 passes",
                                   "outside the proof"])

    def test_the_public_path_never_frees_while_a_preview_renders(self):
        app = SimpleNamespace(is_job_running=lambda kind: kind == "RENDER_PREVIEW")
        fake = SimpleNamespace(app=app, data=SimpleNamespace(batch_remove=mock.Mock()))
        reasons = []
        with mock.patch.object(commit_orphans, "_bpy", lambda: fake):
            commit_orphans._free_public([("meshes", "Cube", 1, 2)], reasons)
        self.assertEqual(reasons, ["a preview render was running"])
        fake.data.batch_remove.assert_not_called()

    def test_only_unused_local_plain_blocks_of_a_swapped_family_qualify(self):
        self.assertTrue(commit_orphans._freeable(Block("Cube")))
        for change in ({"users": 1}, {"use_fake_user": True}, {"library": object()}, {"asset_data": object()},
                       {"id_type": "IMAGE"}, {"id_type": "KEY"}):
            block = Block("Cube")
            for key, value in change.items():
                setattr(block, key, value)
            self.assertFalse(commit_orphans._freeable(block), change)
        block = Block("Cube")
        self.assertFalse(commit_orphans._freeable(block, block), "an object whose data was not swapped keeps it")

    def test_scoped_commits_that_swap_seal_their_written_objects_and_scene(self):
        scope = SimpleNamespace(seal_replaceable=mock.Mock(return_value=True))
        cube, scene = object(), object()
        stage = SimpleNamespace(originals={"o1": cube}, binding={"writes": ["o1"], "scene": {"write": True}},
                                changes={"o1": {"kind:transform_apply", "mesh"}})
        self.assertTrue(commit_orphans.seal(scope, stage, scene))
        scope.seal_replaceable.assert_called_once_with([cube, scene])
        self.assertFalse(commit_orphans.seal(None, stage, scene), "nothing to seal without a native scope")

    def test_nothing_unused_never_reaches_the_native_right(self):
        scope = SimpleNamespace(frees_replaced_data=mock.Mock(return_value=True), delete_replaced=mock.Mock())
        used = Block("Cube.001", users=1)
        with mock.patch.object(commit_orphans, "_scene_busy", lambda: False), \
                mock.patch.object(commit_orphans, "_alive", lambda identity: used if identity else None), \
                mock.patch.object(commit_orphans, "_identity", lambda block: ("meshes", block.name, 1, 2)):
            _remaining, kept = commit_orphans.free_replaced([(used, Block("Cube"))], scope=scope)
        scope.delete_replaced.assert_not_called()
        self.assertEqual(kept, {})

    def test_an_unreadable_wrapper_is_skipped_counted_and_never_read_again(self):
        class Gone:
            def __eq__(self, other):
                raise ReferenceError("StructRNA of type Mesh has been removed")
            __hash__ = object.__hash__

            def __getattr__(self, name):
                raise ReferenceError("StructRNA of type Mesh has been removed")
        with mock.patch.object(commit_orphans, "_identity", lambda block: () if isinstance(block, Gone) else None):
            _remaining, kept = commit_orphans.free_replaced([(Gone(), None)], scope=None)
        self.assertIn("1 replaced blocks could no longer be read", kept["reasons"])

    def test_a_commit_that_swaps_nothing_never_seals(self):
        scope = SimpleNamespace(seal_replaceable=mock.Mock(return_value=True))
        stage = SimpleNamespace(originals={"o1": object()}, binding={"writes": ["o1"]},
                                changes={"o1": {"kind:transform", "transforms"}})
        self.assertFalse(commit_orphans.seal(scope, stage, object()))
        scope.seal_replaceable.assert_not_called()

    def test_a_running_render_keeps_everything_and_says_so(self):
        replaced = [(Block("Cube.001"), Block("Cube"))]
        with mock.patch.object(commit_orphans, "_scene_busy", lambda: True), \
                mock.patch.object(commit_orphans, "_alive", lambda identity: replaced[0][0] if identity else None), \
                mock.patch.object(commit_orphans, "_identity", lambda block: ("meshes", block.name, 1, 2)):
            remaining, kept = commit_orphans.free_replaced(replaced, scope=object())
        self.assertEqual((len(remaining), kept["count"]), (1, 1))
        self.assertIn("a render, bake or other job was evaluating the scene", kept["reasons"])


class NoteTests(unittest.TestCase):
    def test_the_note_says_what_can_still_stay(self):
        for part in ("deleted object", "points at", "could not free", "does not write it"):
            self.assertIn(part, LEFTOVER_NOTE)
        self.assertNotIn("earlier World", LEFTOVER_NOTE)
        self.assertNotIn("released", LEFTOVER_NOTE, "released data is not listed, as after Blender's own edit")

    def test_unfreed_data_is_counted_and_says_why(self):
        report = ProgramCommitReportMixin._leftovers([(Block("Cube.001"), None)], [],
                                                     {"count": 1, "reasons": ["a preview render was running"]})
        report = report["orphan_data"]
        self.assertEqual((report["not_freed"], report["not_freed_reasons"], report["note"]),
                         (1, ["a preview render was running"], LEFTOVER_NOTE + NOT_FREED_NOTE))
        self.assertNotIn("not_freed", ProgramCommitReportMixin._leftovers([(Block("Cube.001"), None)], [])["orphan_data"])


if __name__ == "__main__":
    unittest.main()
