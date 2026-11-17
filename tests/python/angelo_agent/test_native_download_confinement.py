# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An agent script reads an unsaved document's downloads but never changes them (doc/angelo/downloads.rst, "Where the
file lands"; script_confinement's tool-output folders).

``angelo_download`` saves an unsaved document's file under ``download_folders.unsaved_base``; the confinement lists
that folder among the tool-output folders (``script_confinement_blender.tool_output_folders``), which a script may
read and list but never write, rename into or out of, or delete. Otherwise a script could swap a downloaded file
before it is imported (Fusion audit trial, 508360d287d). The child below installs the confinement from the real
registration (not a stand-in folder), places a file as the download tool does, and runs agent code against it.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

CHILD = r'''
import json, os, sys, textwrap
from pathlib import Path
from types import SimpleNamespace
WORK, ROOT = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
HOME = WORK / "home"
os.environ["HOME"] = str(HOME)
PROFILE = {"USERPROFILE": "", "APPDATA": "AppData/Roaming", "LOCALAPPDATA": "AppData/Local"}
for name, below in PROFILE.items():   # Windows' own home and data folders: never the real ones.
    os.environ[name] = str(HOME / below)
os.environ["ANGELO_STATE_DIR"] = str(HOME / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = str(HOME / "connections")
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(ROOT / "tests/python/angelo_agent")]
from angelo_agent import download_folders, script_confinement as confinement
from angelo_agent import script_confinement_blender as blender_side
from confinement_attacks_admit import admit

class Scene(dict):
    library = override_library = None

session = WORK / "tmp" / "blender_1"
session.mkdir(parents=True)
# A stand-in bpy module for the modules tool_output_folders imports (settings_import_apply), and for the calls.
import types
bpy = sys.modules["bpy"] = types.ModuleType("bpy")
bpy.app = SimpleNamespace(tempdir=str(session) + os.sep, version=(5, 3, 0))
bpy.data = SimpleNamespace(filepath="", scenes=[Scene()])
bpy.utils = SimpleNamespace(resource_path=lambda kind: str(WORK / "user"),
                            user_resource=lambda kind: str(WORK / "user"))
report = {"tool_output": blender_side.tool_output_folders(bpy),
          "unsaved_base": str(download_folders.unsaved_base(bpy))}
folder = download_folders.root(bpy) / "assets" / "downloads"   # Where download_fetch places it (FOLDER).
assert folder.is_dir(), "root makes the downloads folder itself (owned_paths.make_dir_beside)"
placed = folder / "brick.png"
placed.write_bytes(b"\x89PNG downloaded")
plain = WORK / "plain"
plain.mkdir()
(plain / "swap.png").write_bytes(b"swapped")
Path(os.environ["ANGELO_STATE_DIR"]).mkdir(parents=True)
Path(os.environ["ANGELO_CONNECTIONS_DIR"]).mkdir(parents=True)
private, families = blender_side.folders(os.environ["ANGELO_STATE_DIR"], os.environ["ANGELO_CONNECTIONS_DIR"], [],
                                         dict(os.environ))
confinement.install(private=private, families=families, trusted=[sys.prefix, sys.base_prefix,
                    str(ROOT / "scripts/addons_core")], tool_output=report["tool_output"])
if confinement.native():
    confinement._guard.install_wrappers()

def run(name, source):
    text = textwrap.dedent(source)
    result = {"refused": False}
    try:
        admit(name, text)
        names = {"__name__": "__angelo_script__", "FILE": str(placed), "FOLDER": str(folder), "PLAIN": str(plain),
                 "BASE": report["unsaved_base"], "SCENE": bpy.data.scenes[0], "PROPERTY": download_folders.PROPERTY}
        confinement.run_confined(text, names, None, name)
    except confinement.ScriptConfined as error:
        result.update(refused=True, reason=confinement.reason_of(error))
    except Exception as error:
        result["error"] = repr(error)[:200]
    report[name] = result

run("read", "open(FILE, 'rb').read()")
run("list", "import os\nos.listdir(FOLDER)")
run("write", "open(FILE, 'wb').write(b'x')")
run("append", "open(FILE, 'ab').write(b'x')")
run("create", "import os\nopen(os.path.join(FOLDER, 'new.png'), 'wb').write(b'x')")
run("rename_out", "import os\nos.rename(FILE, os.path.join(PLAIN, 'out.png'))")
run("rename_in", "import os\nos.replace(os.path.join(PLAIN, 'swap.png'), FILE)")
run("delete", "import os\nos.remove(FILE)")
# Devin audit N3: a script may set the document's downloads id (scene data), but cannot prepare the folder it names.
run("prepare_folder", "import os\nos.makedirs(os.path.join(BASE, 'f' * 32, 'assets', 'downloads'))")
run("steer", "SCENE[PROPERTY] = 'f' * 32")
steered = download_folders.root(bpy)
report["steered"] = {"under_base": steered.resolve().parent == Path(report["unsaved_base"]).resolve(),
                     "name": steered.name, "owner_only": os.name == "nt" or (steered.stat().st_mode & 0o077) == 0}
report["intact"] = (placed.read_bytes() == b"\x89PNG downloaded"
                    and sorted(p.name for p in folder.iterdir()) == ["brick.png"])
print(json.dumps(report))
'''


class UnsavedDownloadsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="download-confinement-") as work:
            done = subprocess.run([sys.executable, "-I", "-c", CHILD, work, str(ROOT)], capture_output=True,
                                  text=True, timeout=240, cwd=work)
            if done.returncode != 0:
                raise AssertionError("child failed:\n" + done.stdout[-3000:] + done.stderr[-5000:])
            cls.report = json.loads(done.stdout.strip().splitlines()[-1])

    def test_the_unsaved_downloads_folder_is_a_tool_output_folder(self):
        self.assertIn(self.report["unsaved_base"], self.report["tool_output"])

    def test_a_script_reads_and_lists_a_download(self):
        for name in ("read", "list"):
            with self.subTest(operation=name):
                self.assertEqual(self.report[name], {"refused": False}, self.report[name])

    def test_a_script_can_name_a_downloads_folder_but_never_prepare_one(self):
        """The id a script may set only names a folder under the read-only root; Angelo makes that folder itself
        (owner-only), so a download never lands in a folder a script prepared (Devin audit N3)."""
        from angelo_agent.script_confinement import TOOL_OUTPUT
        self.assertTrue(self.report["prepare_folder"]["refused"], self.report["prepare_folder"])
        self.assertEqual(self.report["prepare_folder"]["reason"], TOOL_OUTPUT)
        self.assertEqual(self.report["steer"], {"refused": False}, self.report["steer"])
        self.assertEqual(self.report["steered"], {"under_base": True, "name": "f" * 32, "owner_only": True})

    def test_a_script_never_writes_renames_or_deletes_there(self):
        from angelo_agent.script_confinement import TOOL_OUTPUT
        for name in ("write", "append", "create", "rename_out", "rename_in", "delete"):
            with self.subTest(operation=name):
                self.assertTrue(self.report[name]["refused"], self.report[name])
                self.assertEqual(self.report[name]["reason"], TOOL_OUTPUT, self.report[name])
        self.assertTrue(self.report["intact"], "the downloaded file changed")


sys.path.insert(0, str(ROOT / "scripts/addons_core"))


class RegistrationTests(unittest.TestCase):
    """The read-only roots the confinement installs at registration (script_confinement_blender.install_app) always
    hold the unsaved downloads folder, or registration fails loudly: never a quiet list without it (013c161cf3e
    returned without it when download_folders failed to import, and a script overwrote a download in the app; Devin
    audit D2)."""

    def bpy(self, failing_resource=False):
        import types
        from types import SimpleNamespace
        stub = types.ModuleType("bpy")
        work = Path(tempfile.mkdtemp(prefix="download-roots-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(work, ignore_errors=True))

        def resource(kind):
            if failing_resource:
                raise OSError("no user folder")
            return str(work / "user")
        stub.app = SimpleNamespace(tempdir=str(work / "blender_1") + "/", version=(5, 3, 0))
        stub.data = SimpleNamespace(filepath="", scenes=[])
        stub.utils = SimpleNamespace(resource_path=resource, user_resource=resource)
        return stub

    # Modules that bind ``bpy`` when imported: each call imports them again, against its own stand-in.
    FRESH = ("settings_import_apply", "prefs_reload")

    def roots(self, bpy, missing=()):
        from unittest import mock
        import angelo_agent
        names = tuple(missing) + self.FRESH
        hidden = {name: getattr(angelo_agent, name) for name in names if hasattr(angelo_agent, name)}
        for name in hidden:
            delattr(angelo_agent, name)
        fresh = {"angelo_agent." + name: None for name in missing}
        try:
            with mock.patch.dict(sys.modules, dict({"bpy": bpy}, **fresh)):
                for name in self.FRESH:
                    sys.modules.pop("angelo_agent." + name, None)
                from angelo_agent import script_confinement_blender as blender_side
                return blender_side.tool_output_folders(bpy)
        finally:
            for name in names:   # The stand-in's modules leave with it; the originals come back.
                if hasattr(angelo_agent, name):
                    delattr(angelo_agent, name)
            for name, module in hidden.items():
                setattr(angelo_agent, name, module)

    def test_the_unsaved_downloads_folder_is_among_the_roots(self):
        bpy = self.bpy()
        with __import__("unittest").mock.patch.dict(sys.modules, {"bpy": bpy}):
            from angelo_agent import download_folders
            expected = str(download_folders.unsaved_base(bpy))
        self.assertIn(expected, self.roots(bpy))

    def test_a_download_module_that_fails_to_import_fails_registration(self):
        with self.assertRaises(ImportError):
            self.roots(self.bpy(), missing=("download_folders",))

    def test_a_protected_folder_that_cannot_be_found_fails_registration(self):
        with self.assertRaises(OSError):
            self.roots(self.bpy(failing_resource=True))


if __name__ == "__main__":
    unittest.main()
