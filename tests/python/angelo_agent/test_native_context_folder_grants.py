# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's folder grant (``context_folder.Grants``) and which folders it may be given (``context_folder_roots``),
split from test_native_context_folder.py (re-read of 23413c6bae1, N-5: that file was over 500 lines). No Blender here."""

import suite_isolation  # noqa: F401

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import context_folder as cf  # noqa: E402
from angelo_agent import context_folder_roots as roots  # noqa: E402  (what may be chosen)
from host_test_policy import environment_skip  # noqa: E402
import angelo_guard_twin  # noqa: E402
angelo_guard_twin.stand_in_for_the_person(globals())   # Its tests attach folders as the person does (grant_authority).

POSIX = os.name == "posix"


class GrantTests(unittest.TestCase):
    def setUp(self):
        angelo_guard_twin.strict_for(self)   # Grants are the guard's own rule: C exactly, as it runs alone.
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name)
        self.state, self.folder = self.base / "state", self.base / "project"
        self.folder.mkdir()

    def test_grant_and_revoke_per_chat_owner_only(self):
        grants = cf.Grants(self.state)
        grants.attach("chat-1", self.folder)
        self.assertEqual(cf.Grants(self.state).get("chat-1"), self.folder)
        self.assertIsNone(cf.Grants(self.state).get("chat-2"))
        if POSIX:
            self.assertEqual(stat.S_IMODE((self.state / cf.NAME).stat().st_mode), 0o600)
        grants.revoke("chat-1")
        self.assertIsNone(cf.Grants(self.state).get("chat-1"))

    def test_a_failed_write_of_a_second_folder_grants_it_nothing(self):
        """Break-it r4 N-6, for several folders: a folder whose grant could not be saved is never readable, in memory
        or C, and the folders before it stay as they were."""
        from angelo_agent import grant_authority as ga
        grants, other = cf.Grants(self.state), self.base / "other"
        other.mkdir()
        def held():   # C's context-folder ledger as it is (other files' chats may be in it in a whole-suite run).
            return dict(angelo_guard_twin.items(ga.CONTEXT_FOLDERS))
        first = grants.attach("chat-1", self.folder)
        before = held()
        self.assertTrue({"chat-1/" + first, "chat-1/%s/own" % first, "chat-1/working"} <= set(before))
        with patch.object(cf.Grants, "_save", side_effect=OSError("disk full")), self.assertRaises(OSError):
            grants.attach("chat-1", other)
        self.assertEqual([record["id"] for record in grants.records("chat-1")], [first])
        self.assertEqual(grants.get("chat-1"), self.folder)
        self.assertEqual(held(), before)
        self.assertNotIn(cf.folder_digest(other), held().values())

    def test_a_folder_is_readable_only_while_c_holds_the_persons_grant(self):
        """2.0.0's rule for every grant (grant_operators.rst, "Authority in C"): C's ledger holds which folder a chat
        may read (chat id -> the digest of its real path), written only in the person's grant scope; Python's copy or
        the file alone never makes a folder readable."""
        from angelo_agent import grant_authority as ga
        self.assertIn(ga.CONTEXT_FOLDERS, angelo_guard_twin._STANDARD)
        core = (Path(__file__).resolve().parents[3] / "source/blender/python/intern/bpy_angelo_guard_authority_core.cc")
        self.assertIn("define(CONTEXT_FOLDERS, Value());", core.read_text())
        angelo_guard_twin.reset()
        self.addCleanup(angelo_guard_twin.reset)
        grants = cf.Grants(self.state)
        folder_id = grants.attach("chat-1", self.folder)
        self.assertEqual(ga.value(ga.CONTEXT_FOLDERS, "chat-1/" + folder_id), cf.folder_digest(self.folder))
        self.assertEqual(ga.value(ga.CONTEXT_FOLDERS, "chat-1/working"), cf.folder_digest(self.folder))
        grants._grants["chat-2"] = dict(grants._grants["chat-1"])   # What a script could change in memory.
        self.assertIsNone(grants.get("chat-2"))
        self.assertIsNone(grants.writable("chat-2", folder_id))
        angelo_guard_twin.reset()
        self.assertIsNone(grants.get("chat-1"), "C no longer holds it")
        self.assertIsNone(grants.writable("chat-1", folder_id))
        self.assertEqual(cf.Grants(self.state).get("chat-1"), self.folder, "Angelo's own file, mirrored as it is read")
        grants.revoke("chat-1")
        self.assertIsNone(ga.value(ga.CONTEXT_FOLDERS, "chat-1/" + folder_id))
        self.assertIsNone(ga.value(ga.CONTEXT_FOLDERS, "chat-1/working"))

    def test_outside_the_persons_own_scope_no_folder_is_attached(self):
        errors = []

        def elsewhere():   # Another thread holds no grant scope.
            try:
                cf.Grants(self.state).attach("chat-1", self.folder)
            except PermissionError as error:
                errors.append(error)
        thread = threading.Thread(target=elsewhere)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIsNone(cf.Grants(self.state).get("chat-1"))
        self.assertFalse((self.state / cf.NAME).exists(), "Refused in C first: nothing is written")

    def test_only_a_real_folder_by_its_absolute_path(self):
        grants = cf.Grants(self.state)
        for bad in (Path("project"), self.base / "missing"):
            with self.assertRaises(ValueError):
                grants.attach("chat-1", bad)
        if POSIX:
            (self.base / "link").symlink_to(self.folder)
            with self.assertRaises(ValueError):
                grants.attach("chat-1", self.base / "link")


class RootTests(unittest.TestCase):
    """The chosen folder itself is vetted, not only paths inside it (the 2.1.0 break-it review, F-1): an agent can ask
    for exactly the pick that exposes keys, and the grant follows the folder, never a path (F-2)."""

    def setUp(self):
        angelo_guard_twin.strict_for(self)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(os.path.realpath(scratch.name))
        self.state, self.folder = self.base / "state", self.base / "home" / "project"
        self.folder.mkdir(parents=True)
        home = patch.object(roots, "_home", return_value=str(self.base / "home"))
        home.start()
        self.addCleanup(home.stop)
        self.grants = cf.Grants(self.state)

    def refused(self, folder, reason):
        with self.assertRaises(cf.FolderRefused) as caught:
            self.grants.attach("chat-1", folder)
        self.assertEqual(caught.exception.reason, reason)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertIsNone(self.grants.get("chat-1"))

    def test_a_folder_of_keys_or_a_repositorys_internals_is_refused_itself_or_inside(self):
        for parts in ((".ssh",), (".aws",), (".git",), (".codex",), (".config", "tool"), ("work", ".git", "hooks")):
            folder = self.base.joinpath("home", *parts)
            folder.mkdir(parents=True, exist_ok=True)
            with self.subTest(folder=folder):
                self.refused(folder, cf.PRIVATE)

    def test_the_home_folder_its_parents_and_a_whole_drive_are_too_wide(self):
        self.refused(self.base / "home", cf.TOO_WIDE)
        self.refused(self.base, cf.TOO_WIDE)
        self.refused(Path(os.path.abspath(os.sep)), cf.TOO_WIDE)
        drive = self.base / "drive"
        drive.mkdir()
        with patch.object(cf.os.path, "ismount", side_effect=lambda path: os.path.realpath(path) == str(drive)):
            self.refused(drive, cf.TOO_WIDE)

    def test_where_each_platform_keeps_apps_settings_and_private_data_is_refused(self):
        """N-4 (round 2): not only the dot convention. Each platform's per-user and machine-wide folders where apps keep
        settings, tokens and private data (macOS Library, Windows AppData and ProgramData, the XDG folders wherever
        they point, the system's settings) are refused, themselves or inside, while a project in home still attaches."""
        home = self.base / "home"
        cases = {
            "darwin": ({}, (home / "Library", home / "Library" / "Application Support" / "Tool",
                            home / "Library" / "Keychains")),
            "win32": ({"APPDATA": str(self.base / "roam"), "LOCALAPPDATA": str(self.base / "local"),
                       "PROGRAMDATA": str(self.base / "shared")},
                      (self.base / "roam", self.base / "roam" / "Tool", self.base / "local" / "Tool" / "User Data",
                       self.base / "shared" / "Tool", home / "AppData" / "Roaming" / "Other")),
            "linux": ({"XDG_CONFIG_HOME": str(self.base / "cfg"), "XDG_DATA_HOME": str(self.base / "data"),
                       "XDG_STATE_HOME": str(self.base / "st")},
                      (self.base / "cfg" / "tool", self.base / "data", self.base / "st" / "tool")),
        }
        for platform, (environ, folders) in cases.items():
            with patch.object(roots, "_platform", return_value=platform), patch.dict(roots._ENVIRON, environ, clear=True):
                for folder in folders:
                    folder.mkdir(parents=True, exist_ok=True)
                    with self.subTest(platform=platform, folder=folder):
                        self.refused(folder, cf.PRIVATE)
                with self.subTest(platform=platform, folder="project"):
                    self.grants.attach("chat-" + platform, self.folder)
                    self.assertEqual(self.grants.get("chat-" + platform), self.folder)
        with patch.object(roots, "_platform", return_value="linux"), patch.dict(roots._ENVIRON, {}, clear=True):
            for system in ("/etc", "/var/lib"):   # Judged as the folder (on macOS /etc is a link to /private/etc).
                if os.path.isdir(system):
                    with self.subTest(folder=system):
                        self.vetted(Path(system), cf.PRIVATE)

    def vetted(self, folder, reason):
        with self.assertRaises(cf.FolderRefused) as caught:
            cf.vet_root(folder)
        self.assertEqual(caught.exception.reason, reason)

    def test_a_folder_is_judged_as_the_system_spells_it_never_as_typed(self):
        """Round 3, S-2: macOS keeps a path's typed case in its real path, so a folder and the home folder are judged
        as the system spells them (``private_conventions.canonical``, F_GETPATH there), never as typed."""
        home = self.base / "home"
        (home / "Library" / "Keychains").mkdir(parents=True)

        def spelled(path, platform=None, strict=False):   # A case-insensitive volume answering with its own spelling, as F_GETPATH.
            path, found = Path(os.path.realpath(str(path))), Path(os.sep)
            for part in path.parts[1:]:
                names = {child.name.lower(): child.name for child in found.iterdir()} if found.is_dir() else {}
                found = found / names.get(part.lower(), part)
            return found
        with patch.object(roots, "_platform", return_value="darwin"), patch.dict(roots._ENVIRON, {}, clear=True), \
                patch.object(roots.private_conventions, "canonical", side_effect=spelled):
            for typed in (home / "library" / "keychains", home / "LIBRARY" / "Keychains", home / "Library"):
                with self.subTest(folder=typed):
                    self.vetted(typed, cf.PRIVATE)
            with patch.object(roots, "_home", return_value=str(self.base / "HOME")):
                self.vetted(home, cf.TOO_WIDE)
                self.vetted(home / "library", cf.PRIVATE)

    def test_a_folder_holding_a_refused_one_is_too_wide(self):
        """Round 3, NIT a: as for the home folder, a folder that holds one where apps keep private data is too wide."""
        data = self.base / "home" / "work" / "data"
        data.mkdir(parents=True)
        with patch.object(roots, "_platform", return_value="linux"), \
                patch.dict(roots._ENVIRON, {"XDG_DATA_HOME": str(data)}, clear=True):
            self.vetted(data.parent, cf.TOO_WIDE)
            self.vetted(data / "tool", cf.PRIVATE)
            (self.base / "home" / "elsewhere").mkdir()
            self.assertIsNone(cf.vet_root(self.base / "home" / "elsewhere"))

    def test_the_temporary_folder_inside_windows_appdata_stays_usable(self):
        """Round 3, NIT e: Windows keeps the temporary folder in %LOCALAPPDATA%; a project there (an archive Explorer
        opened) is attached, as the file tools' conventions keep it (``private_conventions``)."""
        local = self.base / "local"
        (local / "Temp" / "project").mkdir(parents=True)
        (local / "Tool").mkdir()
        with patch.object(roots, "_platform", return_value="win32"), \
                patch.dict(roots._ENVIRON, {"LOCALAPPDATA": str(local)}, clear=True), \
                patch.object(roots, "_TEMP", str(local / "Temp")):
            self.assertIsNone(cf.vet_root(local / "Temp" / "project"))
            self.vetted(local / "Tool", cf.PRIVATE)

    def test_where_apps_keep_data_is_read_as_angelo_started_never_as_changed_since(self):
        """Round 3, NIT f: the home folder and the variables naming apps' folders are read once, as Angelo starts, so
        code that changes the environment later (a script) moves no folder out of the refusal."""
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/context_folder_roots.py").read_text()
        body = source.split("def vet_root", 1)[1].split("\ndef ", 1)[0] + source.split("def _app_data_roots", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("os.environ", body)
        self.assertNotIn("expanduser", source.split("def _home", 1)[1].split("\ndef ", 1)[0])
        config = self.base / "config"
        (config / "tool").mkdir(parents=True)
        with patch.object(roots, "_platform", return_value="linux"), \
                patch.dict(roots._ENVIRON, {"XDG_CONFIG_HOME": str(config)}, clear=True), \
                patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.base / "elsewhere")}):
            self.vetted(config / "tool", cf.PRIVATE)

    def test_a_project_in_the_persons_own_document_stores_in_library_attaches(self):
        """macOS keeps the person's own files under Library too (iCloud Drive, the cloud providers' folders): those are
        theirs, as the file tools' conventions say (``private_conventions``), so a project there is attached."""
        home = self.base / "home"
        stores = (home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "game",
                  home / "Library" / "CloudStorage" / "Dropbox" / "game")
        with patch.object(roots, "_platform", return_value="darwin"):
            for index, folder in enumerate(stores):
                folder.mkdir(parents=True)
                with self.subTest(folder=folder):
                    self.grants.attach("chat-store-%d" % index, folder)
                    self.assertEqual(self.grants.get("chat-store-%d" % index), folder)

    def test_a_home_folder_whose_own_path_has_a_dot_still_attaches_its_projects(self):
        """F-1's vetting judges the path below the home folder: a home at /srv/.people/name is still a home."""
        home = self.base / ".people" / "name"
        project = home / "project"
        project.mkdir(parents=True)
        with patch.object(roots, "_home", return_value=str(home)):
            self.grants.attach("chat-2", project)
            self.assertEqual(self.grants.get("chat-2"), project)
            (home / ".ssh").mkdir()
            self.refused(home / ".ssh", cf.PRIVATE)

    def test_a_project_folder_in_home_is_attached(self):
        self.grants.attach("chat-1", self.folder)
        self.assertEqual(self.grants.get("chat-1"), self.folder)

    def test_the_grant_follows_the_folder_never_another_one_at_its_path(self):
        self.grants.attach("chat-1", self.folder)
        self.folder.rename(self.base / "home" / "renamed")
        self.assertIsNone(self.grants.get("chat-1"), "Renamed away: nothing at its path")
        self.folder.mkdir()   # Another folder where it was (another drive of that name, another checkout).
        self.assertIsNone(self.grants.get("chat-1"))
        self.assertIsNone(cf.Grants(self.state).get("chat-1"), "Nor after Angelo reads its file again")

    def test_a_saved_grant_without_the_folders_identity_is_never_read(self):
        self.state.mkdir()
        (self.state / cf.NAME).write_text(json.dumps({"version": 1, "chats": {
            "chat-1": {"path": str(self.folder), "at": 1.0}}}), encoding="utf-8")
        self.assertIsNone(cf.Grants(self.state).get("chat-1"))

    def test_the_picker_says_why_a_folder_was_refused(self):
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/context_folder_ui.py").read_text()
        self.assertIn("except context_folder.FolderRefused as refused:", source)
        self.assertIn("TOO_WIDE_FOLDER", source)
        self.assertIn("PRIVATE_FOLDER", source)

    @unittest.skipIf(os.name == "nt", environment_skip(
        "macOS's spelling opens the folder through fcntl, which Windows has no module for (VM CI on 70142a371dd)"))
    def test_a_folder_that_cant_be_opened_to_check_is_refused_on_macos(self):
        """Re-read of 23413c6bae1, S-1: on macOS the folder's spelling comes from the opened folder (F_GETPATH); when
        it can't be opened (privacy protection answers EPERM), the typed case stood, so ~/library/mail was accepted.
        A chosen folder that can't be opened to check is refused. ``canonical`` is not stubbed here."""
        mail = self.base / "home" / "Library" / "Mail"
        mail.mkdir(parents=True)
        real_open = roots.private_conventions.os.open

        def refusing(path, flags, *args, **kwargs):
            if "mail" in os.fspath(path).lower():
                raise PermissionError(1, "Operation not permitted")
            return real_open(path, flags, *args, **kwargs)
        with patch.object(roots, "_platform", return_value="darwin"), patch.dict(roots._ENVIRON, {}, clear=True), \
                patch.object(roots.private_conventions.os, "open", refusing):
            for typed in (self.base / "home" / "library" / "mail", mail):
                with self.subTest(folder=typed):
                    self.vetted(typed, cf.UNCHECKED)

    def test_the_per_user_cache_parent_and_where_drives_mount_are_refused(self):
        """Re-read of 23413c6bae1, N-2: macOS's per-user cache and temporary parent (/private/var/folders, every app's
        caches) was accepted, and so was the folder every drive mounts in. The temporary folder itself stays usable."""
        folders = self.base / "private" / "var" / "folders"
        (folders / "xy" / "abc" / "C" / "tool").mkdir(parents=True)
        (folders / "xy" / "abc" / "T" / "project").mkdir(parents=True)
        with patch.object(roots, "_platform", return_value="darwin"), patch.dict(roots._ENVIRON, {}, clear=True), \
                patch.object(roots, "_USER_FOLDERS", str(folders)), \
                patch.object(roots, "_TEMP", str(folders / "xy" / "abc" / "T")):
            self.vetted(folders / "xy" / "abc" / "C" / "tool", cf.PRIVATE)
            self.vetted(folders / "xy", cf.PRIVATE)
            self.assertIsNone(cf.vet_root(folders / "xy" / "abc" / "T" / "project"))

    def test_a_store_exempts_only_what_no_narrower_root_inside_it_holds(self):
        """Re-read of 1c2c4edaf48, S-2: the temporary folder (a store inside macOS's /private/var/folders) exempted
        every root under it, so a home kept inside it had its Library accepted. The narrowest of the two decides."""
        store = self.base / "T"   # The platform's own temporary folder (N-4: only a T or Temp leaf is a store).
        home = store / "home"
        (home / "Library" / "Keychains").mkdir(parents=True)
        (home / "project").mkdir()
        with patch.object(roots, "_platform", return_value="darwin"), patch.dict(roots._ENVIRON, {}, clear=True), \
                patch.object(roots, "_home", return_value=str(home)), \
                patch.object(roots, "_USER_FOLDERS", str(self.base)), patch.object(roots, "_TEMP", str(store)):
            self.vetted(home / "Library" / "Keychains", cf.PRIVATE)
            self.vetted(home / "Library", cf.PRIVATE)
            self.assertIsNone(cf.vet_root(home / "project"))

    def test_a_live_mount_table_is_read_only_on_the_system_that_keeps_it(self):
        """VM CI on 70142a371dd (win-1): the suites judge folders as macOS would on any host, and the macOS branch
        called getfsstat through ctypes.CDLL(None), which Windows refuses with a TypeError, so five tests errored there.
        A table belongs to the running system: another platform's has none to read here (the scan decides, failing
        closed), and a reader that fails in any way reads as no table."""
        from angelo_agent import private_conventions as pc

        def windows_refuses():
            raise TypeError("expected str, bytes or os.PathLike object, not NoneType")
        with patch.object(pc.sys, "platform", "win32"), patch.object(pc, "_getfsstat", windows_refuses):
            self.assertIsNone(pc.mount_points(platform="darwin"))
        with patch.object(pc.sys, "platform", "darwin"), patch.object(pc, "_getfsstat", windows_refuses):
            self.assertIsNone(pc.mount_points(platform="darwin"), "a reader that fails is no table, never a crash")
        with patch.object(pc.sys, "platform", "darwin"), patch.object(pc, "_getfsstat", lambda: ["/", "/Volumes/X"]):
            self.assertEqual(pc.mount_points(platform="darwin"), frozenset({"/", "/Volumes/X"}))

    def test_the_mount_tables_are_read_as_the_system_writes_them(self):
        """Re-read of ccdfea52edb, S-2: the mount table is parsed by one reader (private_conventions): Linux's
        mountinfo with its octal escapes and names in any script; macOS's getfsstat records."""
        from angelo_agent import private_conventions as pc
        mountinfo = ("22 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
                     "40 22 8:17 / /media/jos\\303\\251/USB\\040Stick rw - vfat /dev/sdb1 rw\n"
                     "41 22 8:18 / /run/media/\u7528\u6237/\u0414\u0438\u0441\u043a rw - ext4 /dev/sdc1 rw\n"
                     "42 22 8:1 /srv/data /home/a/bound rw - ext4 /dev/sda1 rw\n"
                     "43 22 8:19 / /media/a/odd\rname rw - vfat /dev/sdd1 rw\n")   # mountinfo leaves \r as is (N-2).
        mounts = pc.mount_points(mountinfo, platform="linux")
        self.assertIn("/media/a/odd\rname", mounts, "a carriage return inside a name never ends its line")
        self.assertIn("/media/jos\u00e9/USB Stick", mounts)
        self.assertIn("/run/media/\u7528\u6237/\u0414\u0438\u0441\u043a", mounts)
        self.assertIn("/home/a/bound", mounts)
        self.assertEqual(pc.subtree_mounts(mountinfo), frozenset({"/home/a/bound"}), "bind mounts of a subfolder only")
        record = bytearray(pc.STATFS_SIZE)
        name = "/Volumes/D\u00e9j\u00e0 vu".encode("utf-8")
        record[pc.STATFS_MNTONNAME:pc.STATFS_MNTONNAME + len(name)] = name
        self.assertEqual(pc.statfs_mounts(bytes(record) * 2, 2), ["/Volumes/D\u00e9j\u00e0 vu"] * 2)

    def test_a_scan_that_reaches_its_cap_is_too_wide(self):
        """Re-read of ccdfea52edb, S-2: where no mount table can be read, a scan that stops at its cap fails closed."""
        wide = self.base / "wide"
        for n in range(6):
            (wide / ("d%d" % n)).mkdir(parents=True)
        with patch.object(roots, "_platform", return_value="win32"), patch.dict(roots._ENVIRON, {}, clear=True), \
                patch.object(roots, "_mount_points", return_value=None), patch.object(roots, "SCAN_CAP", 4):
            self.vetted(wide, cf.TOO_WIDE)
        with patch.object(roots, "_platform", return_value="win32"), patch.dict(roots._ENVIRON, {}, clear=True), \
                patch.object(roots, "_mount_points", return_value=None):
            self.assertIsNone(cf.vet_root(wide))

    def test_a_folder_with_a_mount_point_below_it_is_too_wide(self):
        """Re-read of 1c2c4edaf48, S-1: the mount parents were listed by name (/media, /mnt, /run/media), so the
        per-user parents udisks mounts drives in (/media/<user>, /run/media/<user>) and any mount made elsewhere were
        accepted. A folder that has a mount point below it (a child or a grandchild) is too wide, wherever it is."""
        layouts = {"linux": ("media", "jack", "USB"), "linux-run": ("run", "media", "jack", "Backup"),
                   "darwin": ("Volumes", "Drive"), "home-mount": ("home", "mnt", "share")}
        for name, parts in layouts.items():
            platform = "darwin" if name == "darwin" else "linux"
            mount = self.base.joinpath("sys-" + name, *parts)
            mount.mkdir(parents=True)
            mounts = {str(mount)}
            with self.subTest(layout=name), patch.object(roots, "_platform", return_value=platform), \
                    patch.dict(roots._ENVIRON, {}, clear=True), \
                    patch.object(roots, "_mount_points", return_value=mounts if platform == "linux" else None), \
                    patch.object(roots.os.path, "ismount", side_effect=lambda path, mounts=mounts: str(path) in mounts):
                for holder in (mount.parent, mount.parent.parent):   # The mount's parent and grandparent.
                    with self.subTest(folder=holder):
                        self.vetted(holder, cf.TOO_WIDE)
                ordinary = self.base / ("plain-" + name) / "project"
                ordinary.mkdir(parents=True)
                self.assertIsNone(cf.vet_root(ordinary))


if __name__ == "__main__":
    unittest.main()
