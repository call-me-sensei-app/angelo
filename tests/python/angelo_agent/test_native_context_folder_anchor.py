# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's attached folder is judged by its own grant in C at every walk, never by C's ``may_write``
(``context_folder_root.granted_root``, the class-level root step ``ReadOnlyFolder._open_root``), and nothing else is.

``may_write`` refuses a person's project folder once any agent script has run in the process (C narrows after the first
script region), so before this every LIST, READ, SEARCH and VIEW of the attached folder failed for the rest of a
session in which a script had run (found in the app: the context-folders break-it review's third read, SHOULD 1). Here
``require_anchorable`` refuses as C does after a script; no Blender. The shared walk always asks it: no attribute set on
a workspace (the fourth read's MUST 1) and no root step borrowed by another workspace opens a folder C did not grant.
The Windows backend runs through ``fake_nt_files.FakeNtApi`` off Windows and for real on it.
"""

import suite_isolation  # noqa: F401
from host_waits import CAP_S  # noqa: E402

from contextlib import nullcontext
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import checkpoint_filesystem  # noqa: E402
from angelo_agent import context_folder as cf, context_folder_host as host, context_folder_root, files  # noqa: E402
from angelo_agent import grant_authority as ga  # noqa: E402
import angelo_guard_twin  # noqa: E402
from fake_nt_files import FakeNtApi  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
angelo_guard_twin.stand_in_for_the_person(globals())

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


class Chat:
    def __init__(self, state, references):
        self.state = {"id": "chat-1"}
        self.store = SimpleNamespace(directory=state)
        self.references = SimpleNamespace(directory=references)
        self.delegate_jobs, self.events, self.files = {}, [], None
        self.version = 0
        self.done = threading.Event()

    def emit(self, kind, data):
        self.events.append((kind, data))
        if kind == "delegate_finished":
            self.done.set()


def after_a_script():
    """C's ``may_write`` as it answers once any agent script has run: a person's folder is refused."""
    def refused(path, within=None):
        raise PermissionError("Angelo writes only where it made something, where you chose, or in its own folders")
    return patch.object(checkpoint_filesystem, "require_anchorable", refused)


class AnchorTests(unittest.TestCase):
    def setUp(self):
        angelo_guard_twin.strict_for(self)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(os.path.realpath(scratch.name))
        self.root, self.other = self.base / "Castle Game", self.base / "elsewhere"
        (self.root / "art").mkdir(parents=True)
        self.other.mkdir()
        (self.root / "README.md").write_text("Castle game\nWalls are 4 m tall.\n", encoding="utf-8")
        (self.root / "art" / "wall.png").write_bytes(PNG)
        (self.other / "secret.md").write_text("not attached", encoding="utf-8")
        host._grants.clear()
        host._manifests.clear()
        self.addCleanup(host._grants.clear)
        self.chat = Chat(self.base / "state", self.base / "references")
        host.attach(self.chat, self.root)

    def run_call(self, **arguments):
        self.chat.done.clear()
        call = SimpleNamespace(id="call-%d" % len(self.chat.events), arguments=cf.validate(arguments), approval=True)
        host.start(self.chat, call)
        self.assertTrue(self.chat.done.wait(CAP_S))
        self.chat.delegate_jobs[call.id].thread.join(CAP_S)
        return self.chat.events[-1][1]["result"]

    def grant_key(self):
        """The C key the chat's grant for the folder is held under, whatever its form."""
        return next(key for key, value in angelo_guard_twin.items(ga.CONTEXT_FOLDERS)
                    if value == cf.folder_digest(self.root))

    def test_every_read_works_after_an_agent_script(self):
        with after_a_script():
            listed = self.run_call(action="LIST")
            read = self.run_call(action="READ", path="README.md")
            found = self.run_call(action="SEARCH", query="walls")
            viewed = self.run_call(action="VIEW", path="art/wall.png")
        self.assertEqual(sorted(entry["name"] for entry in listed["entries"]), ["README.md", "art"])
        self.assertIn("Walls are 4 m tall.", read["text"])
        self.assertEqual([hit["path"] for hit in found["hits"]], ["README.md"])
        self.assertEqual(viewed["views"][0]["mime"], "image/png")

    def test_another_folder_is_never_read_under_the_grant(self):
        reader = cf.ReadOnlyFolder(self.other, grant=self.grant_key())
        with self.assertRaises(ValueError) as caught:
            reader.list_page(".")
        self.assertEqual(str(caught.exception), cf.NOT_GRANTED)

    def test_once_the_grant_ends_the_folder_is_never_read(self):
        reader = cf.ReadOnlyFolder(self.root, grant=self.grant_key())
        self.assertTrue(reader.list_page(".")["entries"])
        host.detach(self.chat)
        with self.assertRaises(ValueError):
            reader.list_page(".")

    def test_a_folder_replaced_at_its_path_is_never_read(self):
        reader = cf.ReadOnlyFolder(self.root, grant=self.grant_key())
        self.root.rename(self.base / "moved")
        self.root.mkdir()
        (self.root / "planted.md").write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            reader.list_page(".")

    def test_without_a_grant_may_write_judges_as_before(self):
        with after_a_script(), self.assertRaises(PermissionError):
            cf.ReadOnlyFolder(self.root).list_page(".")

    def test_no_attribute_on_a_plain_workspace_skips_may_write(self):
        """Every instance attribute a script could set on the file tools' workspace, the old judge's name among them:
        the root step is the class's, so C's ``may_write`` still judges the folder."""
        workspace = files.FileWorkspace(self.root)
        workspace._anchor = workspace._granted = lambda folder: folder
        workspace._open_root = lambda: nullcontext(-1)   # Used, it would fail on the descriptor, not as refused.
        workspace.grant = self.grant_key()
        with after_a_script(), self.assertRaises(PermissionError):
            with workspace.folder(self.root):
                pass

    def test_the_granted_step_on_another_folder_still_needs_c(self):
        """The granted root step borrowed by a workspace of another kind opens only a folder C granted, whatever its
        root: the decision is C's record for the very folder opened."""
        key = self.grant_key()
        with after_a_script(), patch.object(files.FileWorkspace, "_open_root",
                                            lambda workspace: context_folder_root.granted_root(workspace.root, key)):
            with self.assertRaises(ValueError) as caught, files.FileWorkspace(self.other).folder(self.other):
                pass
            self.assertEqual(str(caught.exception), context_folder_root.NOT_GRANTED)
            for name in ("_anchor", "grant"):
                planted = cf.ReadOnlyFolder(self.other, grant=key)
                setattr(planted, name, key)
                with self.assertRaises(ValueError):
                    planted.list_page(".")


@unittest.skipIf(os.name == "nt", environment_skip("The POSIX stand-in for the Windows backend; on Windows the real "
                                                   "backend runs AnchorTests itself"))
class WindowsBackendAnchorTests(AnchorTests):
    """Every case above through ``anchored_windows``' verified handles (``fake_nt_files.FakeNtApi``)."""

    def setUp(self):
        patcher = patch.object(checkpoint_filesystem, "_windows_api", return_value=FakeNtApi())
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()


if __name__ == "__main__":
    unittest.main()
