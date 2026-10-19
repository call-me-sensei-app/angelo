# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background-job outputs without Blender: anchored staging, set publication and link refusal.

Regressions in the job code:

* a USD export's ``textures/`` was deleted with the staging folder, so the
  published file referenced missing textures (``test_dependencies_are_published_
  with_the_file``; the reference check itself runs in ``test_blender_long_jobs``);
* replacing the output's folder with a link after admission sent staging outside
  the workspace (``test_a_link_before_start_is_refused``, ``..._before_publish_...``).

Every test runs twice: on this host's POSIX descriptors and on the Windows
handle backend through ``fake_nt_files.FakeNtApi`` (a junction there is a link).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import checkpoint_filesystem as cf  # noqa: E402
from angelo_agent import job_outputs  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.job_spec import JobRequestError  # noqa: E402
from fake_nt_files import FakeNtApi  # noqa: E402


def tree(root):
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


# On Windows the real backend holds each folder of the chain open without delete sharing, so moving one away
# fails: the swap these tests stage cannot happen there (the stand-in below still checks the refusal logic).
HELD_FOLDERS_CANNOT_MOVE = os.name == "nt"


class PosixOutputs(unittest.TestCase):
    windows = False

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-job-outputs-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.root, self.outside = base / "workspace", base / "outside"
        self.root.mkdir()
        self.outside.mkdir()
        self.workspace = FileWorkspace(self.root)
        if self.windows:
            patcher = patch.object(cf, "_windows_api", return_value=FakeNtApi())
            patcher.start()
            self.addCleanup(patcher.stop)

    def move_held(self, folder, moved):
        """Move a folder the job holds: True when it moved; on Windows' real backend the move must be refused."""
        if HELD_FOLDERS_CANNOT_MOVE and not self.windows:
            with self.assertRaises(PermissionError):
                os.rename(folder, moved)
            return False
        os.rename(folder, moved)
        return True

    def staging(self, relative, identity):
        output = job_outputs.admit_output(self.workspace, relative)
        return job_outputs.Staging(self.root, output, identity).create()

    def test_output_is_a_visible_path_in_the_workspace_and_a_taken_one_is_no_refusal(self):
        self.assertEqual(job_outputs.admit_output(self.workspace, "renders/a.png"), self.root / "renders/a.png")
        (self.root / "taken.png").write_bytes(b"x")   # A job writes under the next free name instead.
        self.assertEqual(job_outputs.admit_output(self.workspace, "taken.png"), self.root / "taken.png")
        for relative, message in ((".hidden/a.png", "non-hidden"), ("../a.png", "non-hidden"),
                                  (".", "not the workspace itself")):
            with self.subTest(relative=relative):
                with self.assertRaises(JobRequestError) as caught:
                    job_outputs.admit_output(self.workspace, relative)
                self.assertIn(message, str(caught.exception))

    def test_staging_is_private_marked_and_only_its_own_is_removed(self):
        staging = self.staging("renders/anim", "j4")
        self.assertEqual(staging.path, self.root / "renders/.angelo-job-j4")
        if os.name != "nt":  # Windows has no mode bits; the folder is private by its location there.
            self.assertEqual(staging.path.stat().st_mode & 0o777, 0o700)
        self.assertTrue(job_outputs.is_staging(staging.path, "j4"))
        self.assertFalse(job_outputs.is_staging(staging.path, "j5"))
        (staging.path / "frame_0001.png").write_bytes(b"png")
        (staging.path / "frame_0002.tmp").write_bytes(b"partial")
        self.assertEqual(staging.files((".png",)), ["frame_0001.png"])
        staging.discard()
        self.assertEqual(tree(self.root), ["renders"])
        foreign = self.root / ".angelo-job-j6"
        foreign.mkdir()
        self.assertFalse(job_outputs.discard_path(foreign, "j6"))  # No marker: not a job's folder.
        self.assertTrue(foreign.is_dir())

    def test_a_crash_leftover_is_removed_only_with_its_marker(self):
        staging = self.staging("renders/anim", "j7")
        (staging.path / "textures").mkdir()
        (staging.path / "textures/a.png").write_bytes(b"png")
        staging._chain.close()  # The process ended: nothing is held any more.
        self.assertFalse(job_outputs.discard_path(staging.path, "j8"))
        self.assertTrue(job_outputs.discard_path(staging.path, "j7"))
        self.assertEqual(tree(self.root), ["renders"])

    def test_a_folder_output_is_published_whole_and_never_overwrites(self):
        staging = self.staging("renders/anim", "j2")
        for number in range(1, 4):
            (staging.path / ("frame_%04d.png" % number)).write_bytes(b"png")
        self.assertEqual(staging.publish_folder(), self.root / "renders/anim")
        self.assertEqual((staging.linked, staging.relative_output()), (3, "renders/anim"))
        staging.close()
        self.assertEqual(sorted(os.listdir(self.root / "renders/anim")), ["frame_0001.png", "frame_0002.png",
                                                                          "frame_0003.png"])
        (self.root / "renders/later").mkdir()
        again = job_outputs.Staging(self.root, self.root / "renders/later", "j3").create()
        (again.path / "frame_0001.png").write_bytes(b"new")
        self.assertEqual(again.publish_folder(), self.root / "renders/later-j3")  # Taken meanwhile: never merged.
        again.close()
        self.assertEqual(os.listdir(self.root / "renders/later"), [])

    def test_dependencies_are_published_with_the_file(self):
        """Regression: the exporter's textures/ must arrive beside the file, layout kept."""
        staging = self.staging("exports/asset.usda", "j1")
        (staging.path / "asset.usda").write_text("asset inputs:file = @./textures/a.png@")
        (staging.path / "textures").mkdir()
        (staging.path / "textures/a.png").write_bytes(b"png")
        (staging.path / "textures/b.exr").write_bytes(b"exr")
        self.assertEqual(staging.publish_file("asset.usda"), self.root / "exports/asset.usda")
        self.assertEqual(staging.linked, 3)
        staging.close()
        self.assertEqual(tree(self.root), ["exports", "exports/asset.usda", "exports/textures",
                                           "exports/textures/a.png", "exports/textures/b.exr"])

    def test_a_taken_name_moves_the_whole_set_so_references_still_resolve(self):
        (self.root / "exports/textures").mkdir(parents=True)
        (self.root / "exports/textures/mine.png").write_bytes(b"person's")
        staging = self.staging("exports/asset.usda", "j5")
        (staging.path / "asset.usda").write_text("@./textures/a.png@")
        (staging.path / "textures").mkdir()
        (staging.path / "textures/a.png").write_bytes(b"png")
        published = staging.publish_file("asset.usda")
        self.assertEqual((published, staging.relative_output()), (self.root / "exports/asset-j5/asset.usda",
                                                                  "exports/asset-j5/asset.usda"))
        staging.close()
        self.assertTrue((self.root / "exports/asset-j5/textures/a.png").is_file())
        self.assertEqual(os.listdir(self.root / "exports/textures"), ["mine.png"])  # Untouched.

    def test_a_single_file_falls_back_to_its_own_name(self):
        staging = self.staging("renders/still.png", "j6")
        (staging.path / "frame_0001.png").write_bytes(b"png")
        (self.root / "renders/still.png").write_bytes(b"taken meanwhile")
        self.assertEqual(staging.publish_file("frame_0001.png"), self.root / "renders/still-j6.png")
        staging.close()
        self.assertEqual((self.root / "renders/still.png").read_bytes(), b"taken meanwhile")

    def test_a_failed_publication_removes_only_what_it_created(self):
        staging = self.staging("exports/asset.usda", "j9")
        (staging.path / "asset.usda").write_text("x")
        (staging.path / "textures").mkdir()
        (staging.path / "textures/a.png").write_bytes(b"png")
        staging.publish_file("asset.usda")
        staging.unpublish()
        self.assertEqual(tree(self.root / "exports"), [".angelo-job-j9", ".angelo-job-j9/.angelo-job",
                                                       ".angelo-job-j9/asset.usda", ".angelo-job-j9/textures",
                                                       ".angelo-job-j9/textures/a.png"])
        staging.discard()
        self.assertEqual(tree(self.root), ["exports"])

    def test_a_link_in_staging_is_never_published_or_followed(self):
        staging = self.staging("exports/asset.usda", "j10")
        (staging.path / "asset.usda").write_text("x")
        (self.outside / "secret.txt").write_text("outside")
        (staging.path / "textures").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(job_outputs.OutputMoved):
            staging.publish_file("asset.usda")
        staging.discard()
        self.assertEqual((tree(self.root), os.listdir(self.outside)), (["exports"], ["secret.txt"]))

    def test_a_link_before_start_is_refused(self):
        """Regression: the output's folder became a link between admission and start."""
        output = job_outputs.admit_output(self.workspace, "renders/image.png")
        (self.root / "renders").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(job_outputs.OutputMoved) as caught:
            job_outputs.Staging(self.root, output, "j2").create()
        self.assertEqual(str(caught.exception), job_outputs.MOVED)
        self.assertEqual(os.listdir(self.outside), [])

    def test_a_link_before_publish_is_refused_and_nothing_leaves(self):
        """Regression: the output's folder was swapped for a link while the job ran."""
        staging = self.staging("renders/image.png", "j3")
        (staging.path / "frame_0001.png").write_bytes(b"png")
        if not self.move_held(self.root / "renders", self.root / "moved"):
            staging.discard()
            return
        (self.root / "renders").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(job_outputs.OutputMoved):
            staging.verify()
        with self.assertRaises(job_outputs.OutputMoved):
            staging.publish_file("frame_0001.png")
        staging.discard()
        self.assertEqual(os.listdir(self.outside), [])
        if not self.windows:  # The stand-in acts on a handle's opening path, not on the object (fake_nt_files).
            self.assertEqual(os.listdir(self.root / "moved"), [])

    def test_a_moved_intermediate_folder_is_refused(self):
        staging = self.staging("a/b/c.png", "j4")
        if not self.move_held(self.root / "a", self.root / "a-old"):
            staging.discard()
            return
        (self.root / "a/b").mkdir(parents=True)  # Same path, different folders.
        with self.assertRaises(job_outputs.OutputMoved):
            staging.verify()
        staging.discard()


@unittest.skipIf(os.name == "nt", "The POSIX stand-in opens links with O_PATH or O_SYMLINK; on Windows "
                                   "windows_ntfs_checks.py runs the real backend")
class WindowsOutputs(PosixOutputs):
    """The same contract through ``anchored_windows`` (a stand-in junction is a link)."""
    windows = True


if __name__ == "__main__":
    unittest.main()
