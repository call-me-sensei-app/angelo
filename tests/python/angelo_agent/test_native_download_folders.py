# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where ``angelo_download`` puts a file while the document is unsaved (doc/angelo/downloads.rst, "Where the file
goes"): one folder per unsaved document, outside every private root, so an agent script can read the file.

The chat's own folder sits under the agent state folder, which the script confinement keeps private, so a script
could not read a download there (found 2026-11-15 against v201-local-models' spec). Covered: a saved document
downloads beside its file; an unsaved one into ``<Blender's temporary folder>/angelo-downloads/<id>``, the id kept on
the document's local scenes (it survives a reload of the unsaved session, as Blender's own autosave does), the same
folder every time, never inside a private root; the folder and its parent are the user's own (made owner-only; a link
or someone else's folder is refused: the temporary folder is shared on Linux); saving later moves nothing.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import download_folders as folders  # noqa: E402


class Scene(dict):
    library = override_library = None


def fake_bpy(tempdir, filepath="", scenes=None):
    return SimpleNamespace(app=SimpleNamespace(tempdir=tempdir),
                           data=SimpleNamespace(filepath=filepath, scenes=scenes if scenes is not None else [Scene()]))


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="angelo-download-folders-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.session = self.temp / "blender_4242"
        self.session.mkdir()

    def bpy(self, **kwargs):
        return fake_bpy(str(self.session) + os.sep, **kwargs)

    def test_a_saved_document_downloads_beside_its_file(self):
        bpy = self.bpy(filepath=str(self.temp / "castle" / "castle.blend"))
        self.assertEqual(folders.where(bpy), "project")
        self.assertEqual(folders.root(bpy), self.temp / "castle")

    def test_an_unsaved_document_has_its_own_folder_in_blenders_temporary_folder(self):
        scenes = [Scene(), Scene()]
        bpy = self.bpy(scenes=scenes)
        self.assertEqual(folders.where(bpy), "unsaved")
        root = folders.root(bpy)
        key = scenes[0][folders.PROPERTY]
        self.assertEqual(root, (self.temp / folders.FOLDER / key).resolve())
        self.assertTrue(all(scene[folders.PROPERTY] == key for scene in scenes), "every local scene keeps the id")
        self.assertEqual(folders.root(bpy), root, "the same folder every time")
        self.assertNotIn(self.session, root.parents, "never Blender's per-session folder, which quitting deletes")

    def test_the_id_survives_a_reload_of_the_unsaved_session(self):
        scenes = [Scene()]
        first = folders.root(self.bpy(scenes=scenes))
        reloaded = [Scene(scenes[0])]   # Recover Last Session: the scenes come back with their properties.
        self.assertEqual(folders.root(self.bpy(scenes=reloaded)), first)

    def test_another_unsaved_document_has_another_folder(self):
        self.assertNotEqual(folders.root(self.bpy(scenes=[Scene()])), folders.root(self.bpy(scenes=[Scene()])))

    def test_a_key_that_is_not_an_id_is_never_a_path(self):
        scenes = [Scene({folders.PROPERTY: "../../etc"})]
        root = folders.root(self.bpy(scenes=scenes))
        self.assertEqual(root.parent, (self.temp / folders.FOLDER).resolve())
        self.assertRegex(root.name, r"^[0-9a-f]{32}$")

    def test_saving_later_moves_nothing(self):
        scenes = [Scene()]
        unsaved = folders.root(self.bpy(scenes=scenes))
        (unsaved / "kept.png").write_bytes(b"x")
        saved = self.bpy(filepath=str(self.temp / "saved" / "scene.blend"), scenes=scenes)
        self.assertEqual(folders.root(saved), self.temp / "saved")
        self.assertTrue((unsaved / "kept.png").is_file())

    def test_the_folder_is_outside_every_private_root(self):
        from angelo_agent import private_families
        root = folders.root(self.bpy())
        self.assertFalse(any(part.startswith(prefix) for part in root.parts
                             for prefix, _ in private_families.FAMILIES))

    @unittest.skipIf(os.name == "nt", "[environment] POSIX owners and permission bits")
    def test_the_folders_are_owner_only_and_never_a_link_or_someone_elses(self):
        root = folders.root(self.bpy())
        for folder in (root, root.parent):
            self.assertEqual(os.stat(folder).st_mode & 0o077, 0, folder)
        shutil.rmtree(self.temp / folders.FOLDER)
        elsewhere = Path(tempfile.mkdtemp(prefix="angelo-download-elsewhere-"))
        self.addCleanup(shutil.rmtree, elsewhere, True)
        (self.temp / folders.FOLDER).symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(folders.Unavailable):
            folders.root(self.bpy())
        self.assertEqual(list(elsewhere.iterdir()), [])
        (self.temp / folders.FOLDER).unlink()
        (self.temp / folders.FOLDER).mkdir(mode=0o777)
        os.chmod(self.temp / folders.FOLDER, 0o777)
        with self.assertRaises(folders.Unavailable):   # Others could swap what is inside it.
            folders.root(self.bpy())

    def test_the_base_is_named_for_the_confinement(self):
        """``unsaved_base`` is the folder the confinement lists as written only by Angelo (v200-confinement)."""
        self.assertEqual(folders.unsaved_base(self.bpy()), self.temp / folders.FOLDER)
        self.assertEqual(folders.unsaved_base(fake_bpy("")), Path(tempfile.gettempdir()) / folders.FOLDER)

    def test_what_is_vouched_for_never_follows_a_changed_module_value(self):
        """Had ``root`` read ``download_spec.FOLDER`` (or this module's FOLDER) when it made the downloads folder
        Angelo's, a script that changed one in place would have C allow writes in a folder of the person's
        (p10c-3-library, guard piece 10). The folder made or adopted (``owned_paths.make_dir_beside``, since the review
        of piece 10 no folder is vouched) stays ``<root>/assets/downloads``."""
        from angelo_agent import download_spec, owned_paths
        vouched = []
        saved = owned_paths.make_dir_beside, download_spec.FOLDER, folders.FOLDER
        self.addCleanup(setattr, owned_paths, "make_dir_beside", saved[0])
        self.addCleanup(setattr, download_spec, "FOLDER", saved[1])
        self.addCleanup(setattr, folders, "FOLDER", saved[2])
        self.assertEqual((saved[1], saved[2]), (("assets", "downloads"), "angelo-downloads"))   # The literals in root.
        owned_paths.make_dir_beside = (lambda anchor, name, mode=0o700:
                                       vouched.append((Path(anchor) / name, True)) or Path(anchor) / name)
        download_spec.FOLDER, folders.FOLDER = ("..", ".."), os.path.join("..", "..")
        saved_doc = self.bpy(filepath=str(self.temp / "castle" / "castle.blend"))
        unsaved = self.bpy()
        roots = folders.root(saved_doc), folders.root(unsaved)
        self.assertEqual(vouched, [(root / "assets" / "downloads", True) for root in roots])
        self.assertEqual(roots[1].parent, (self.temp / "angelo-downloads").resolve())


if __name__ == "__main__":
    unittest.main()
