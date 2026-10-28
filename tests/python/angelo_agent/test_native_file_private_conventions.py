# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Break-it r2m4c round 7 on file-tool privacy: freshness, completeness by convention, narrow fail-closed.

* M-2: a workspace consumes the registry as it is at each check, never a copy taken when it was made.
* M-1: native CLI homes the environment names (a reused sign-in) are private, and stay recorded.
* M-3: the OS-convention application-state locations (macOS ~/Library, Windows %APPDATA%/%LOCALAPPDATA%, hidden
  home entries, native profile-family folders) and every version's Angelo state are private by default.
* M-4: every scratch folder Angelo makes is registered through ``private_roots`` with a declared family.
* A registry read failure refuses only what it last listed; ordinary projects keep working, and recovery is automatic.
Synthetic folders only.
"""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDONS = ROOT / "scripts/addons_core"
sys.path.insert(0, str(ADDONS))
from angelo_agent import private_roots  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402

LIST = "retained-native-profiles.json"


def read(files, relative):
    return files.execute("angelo_read_file", {"path": relative, "start": 0, "length": 64})


def names(files, relative="."):
    return [entry["name"] for entry in files.execute("angelo_list_files", {"path": relative})["entries"]]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-private-conventions-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.state, self.accounts = self.base / "state", self.base / "accounts"
        self.accounts.mkdir()
        getattr(private_roots, "_retained_cache", {}).clear()

    def refused(self, call, *arguments):
        with self.assertRaisesRegex(ValueError, "Application profiles"):
            call(*arguments)

    @staticmethod
    def synthetic(folder, name="synthetic.txt"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text("SYNTHETIC")


class FreshRegistryTests(Fixture):
    def test_a_workspace_consumes_a_later_registration_without_being_rebuilt(self):
        """Review probe 2: A's workspace stays; B records a profile folder in it afterwards."""
        self.synthetic(self.base / "external" / "home", "auth.json")
        files = FileWorkspace(self.base, excluded=private_roots.private_roots(self.state, self.accounts, [], environ={}))
        self.assertEqual(read(files, "external/home/auth.json")["text"], "SYNTHETIC")
        private_roots.remember_profiles(self.accounts, [{"native_profile": str(self.base / "external" / "home")}])
        self.refused(read, files, "external/home/auth.json")
        self.assertEqual(names(files, "external"), [])

    def test_an_unreadable_list_refuses_what_it_listed_and_nothing_else_and_recovers(self):
        listed, later = self.base / "listed", self.base / "later"
        self.synthetic(listed)
        self.synthetic(later)
        (self.base / "public.txt").write_text("PUBLIC")
        private_roots.remember_profiles(self.accounts, [{"native_profile": str(listed)}])
        roots = private_roots.private_roots(self.state, self.accounts, [], environ={})   # Its last good read.
        listing = self.accounts / LIST
        os.chmod(listing, 0)
        self.addCleanup(os.chmod, listing, stat.S_IRUSR | stat.S_IWUSR)
        for files in (FileWorkspace(self.base, excluded=roots),
                      FileWorkspace(self.base, excluded=private_roots.private_roots(self.state, self.accounts, [],
                                                                                    environ={}))):
            self.assertEqual(read(files, "public.txt")["text"], "PUBLIC", "an ordinary project keeps working")
            self.refused(read, files, "listed/synthetic.txt")
        os.chmod(listing, stat.S_IRUSR | stat.S_IWUSR)
        private_roots.remember_profiles(self.accounts, [{"native_profile": str(later)}])
        self.refused(read, files, "later/synthetic.txt")   # The same workspace, recovered on its own.
        self.refused(read, files, "listed/synthetic.txt")


class ColdUnreadableListTests(Fixture):
    def test_a_fresh_process_with_an_unreadable_list_still_refuses_what_it_held(self):
        """Break-it r2m4c R8-M-3: a runtime home recorded earlier, its variable gone; a fresh process (no warm cache)
        finds the list unreadable or damaged. The last good copy beside it keeps the home refused."""
        home = self.base / "native-home"
        self.synthetic(home, "auth.json")
        (self.base / "public.txt").write_text("PUBLIC")
        private_roots.private_roots(self.state, self.accounts, [], environ={"CODEX_HOME": str(home)})
        code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path\n"
                "from angelo_agent import private_roots\nfrom angelo_agent.files import FileWorkspace\n"
                "base = Path(sys.argv[2])\n"
                "roots = private_roots.private_roots(base / 'state', base / 'accounts', [], environ={})\n"
                "files = FileWorkspace(base, excluded=roots)\nout = {'complete': roots.complete}\n"
                "for name in ('native-home/auth.json', 'public.txt'):\n"
                "    try:\n"
                "        files.execute('angelo_read_file', {'path': name, 'start': 0, 'length': 9}); out[name] = 'read'\n"
                "    except ValueError as error:\n"
                "        out[name] = str(error)\n"
                "print(json.dumps(out))\n")
        listing = self.accounts / LIST
        for damage in ("unreadable", "malformed"):
            with self.subTest(damage=damage):
                os.chmod(listing, stat.S_IRUSR | stat.S_IWUSR)
                if damage == "malformed":
                    listing.write_text("[not json")
                else:
                    os.chmod(listing, 0)
                    self.addCleanup(os.chmod, listing, stat.S_IRUSR | stat.S_IWUSR)
                result = subprocess.run([sys.executable, "-I", "-c", code, str(ADDONS), str(self.base)],
                                        capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr)
                outcome = json.loads(result.stdout.strip().splitlines()[-1])
                self.assertFalse(outcome["complete"])
                self.assertIn("Application profiles", outcome["native-home/auth.json"])
                self.assertEqual(outcome["public.txt"], "read")


class FormerStoreTests(Fixture):
    def test_a_former_custom_state_folder_and_account_store_stay_private(self):
        """Break-it r2m4c R8-M-2: Angelo ran with ANGELO_STATE_DIR and ANGELO_CONNECTIONS_DIR in a project folder, then
        with its usual ones; a fresh registry still refuses the old transcripts and accounts."""
        project = self.base / "Documents" / "Project"
        old_state, old_accounts = project / "chat-cache", project / "accounts-cache"
        self.synthetic(old_state / "sessions", "chat.json")
        self.synthetic(old_accounts, "connections.json")
        self.synthetic(project / "scene", "notes.txt")
        with patch.dict(os.environ, {"HOME": str(self.base)}):
            private_roots.private_roots(old_state, old_accounts, [], environ={})
            getattr(private_roots, "_retained_cache", {}).clear()   # As a fresh process.
            usual_accounts = self.base / "usual-accounts"
            usual_accounts.mkdir()
            roots = private_roots.private_roots(self.base / "usual-state", usual_accounts, [], environ={})
            files = FileWorkspace(project, excluded=roots)
            self.refused(read, files, "chat-cache/sessions/chat.json")
            self.refused(read, files, "accounts-cache/connections.json")
            self.assertEqual(names(files), ["scene"])


class RuntimeHomeTests(Fixture):
    def test_a_native_cli_home_the_environment_names_is_private_and_recorded(self):
        """Review probe 1: CODEX_HOME and CLAUDE_CONFIG_DIR inside the project, not hidden, for a reused sign-in."""
        environment = {"CODEX_HOME": str(self.base / "native-home"), "CLAUDE_CONFIG_DIR": str(self.base / "claude-home")}
        for folder in ("native-home", "claude-home"):
            self.synthetic(self.base / folder, "auth.json")
        files = FileWorkspace(self.base, excluded=private_roots.private_roots(self.state, self.accounts, [],
                                                                              environ=environment))
        self.refused(read, files, "native-home/auth.json")
        self.refused(read, files, "claude-home/auth.json")
        self.assertNotIn("native-home", names(files))
        later = FileWorkspace(self.base, excluded=private_roots.private_roots(self.state, self.accounts, [], environ={}))
        self.refused(read, later, "native-home/auth.json")   # Recorded: private after the variable is gone.


class ConventionTests(Fixture):
    def setUp(self):
        super().setUp()
        (self.base / "tmp").mkdir()   # The synthetic home is not the temporary folder (which the rule exempts).
        patcher = patch.object(tempfile, "tempdir", str(self.base / "tmp"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_os_convention_locations_are_private_under_the_home_folder(self):
        home = self.base
        self.synthetic(home / "Library" / "Application Support" / "Other", "state.json")
        self.synthetic(home / "Library" / "CloudStorage" / "Dropbox" / "project", "notes.txt")
        self.synthetic(home / "work" / "retained" / "codex-profile" / "connection", "auth.json")
        self.synthetic(home / "work" / "project", "notes.txt")
        with patch.dict(os.environ, {"HOME": str(home)}):
            files = FileWorkspace(home, excluded=[])
            convention = files.convention
        if sys.platform == "darwin":
            self.refused(read, files, "Library/Application Support/Other/state.json")
            self.assertEqual(read(files, "Library/CloudStorage/Dropbox/project/notes.txt")["text"], "SYNTHETIC")
            self.assertNotIn("Library", names(files))
        self.refused(read, files, "work/retained/codex-profile/connection/auth.json")
        self.assertEqual(names(files, "work/retained"), [])
        self.assertEqual(read(files, "work/project/notes.txt")["text"], "SYNTHETIC")
        self.assertTrue(convention.private_path(home / ".codex" / "auth.json"))
        self.assertFalse(convention.private_path(home / "work" / "project"))

    def test_windows_roaming_and_local_app_data_are_private(self):
        from angelo_agent.private_conventions import Convention
        roaming, local = self.base / "AppData" / "Roaming", self.base / "AppData" / "Local"
        convention = Convention({"APPDATA": str(roaming), "LOCALAPPDATA": str(local)}, platform="win32",
                                home=self.base)
        for path in (roaming / "Angelo" / "5.1" / "config" / "angelo-agent" / "sessions", local / "Angelo"):
            self.assertTrue(convention.private_path(path), path)
        self.assertFalse(convention.private_path(self.base / "Documents" / "project"))

    def test_the_temporary_folder_keeps_its_file_tools_on_every_platform(self):
        """A project opened from an archive in the temporary folder (Windows: %LOCALAPPDATA%\\Temp; Linux: the shared
        /tmp; macOS: the per-user TMPDIR) keeps its file tools; a profile-family folder there stays private."""
        from angelo_agent.private_conventions import Convention
        local = self.base / "AppData" / "Local"
        temporary = local / "Temp"
        temporary.mkdir(parents=True)
        with patch.object(tempfile, "tempdir", str(temporary)):
            for platform in ("win32", "linux", "darwin"):
                with self.subTest(platform=platform):
                    convention = Convention({"LOCALAPPDATA": str(local)}, platform=platform, home=self.base)
                    self.assertFalse(convention.private_path(temporary / "project.zip-extracted" / "scene"))
                    self.assertTrue(convention.private_path(temporary / "x" / "codex-profile" / "id"))
                    if platform == "win32":
                        self.assertTrue(convention.private_path(local / "Angelo"))
            self.synthetic(temporary / "Extracted project")
            files = FileWorkspace(temporary / "Extracted project", excluded=[])
            self.assertEqual(read(files, "synthetic.txt")["text"], "SYNTHETIC")

    def test_every_versions_angelo_state_is_private(self):
        """Review M-3: 5.2's state is set up; 5.1's transcripts sit beside it."""
        product = self.base / "AppData" / "Roaming" / "Angelo"
        state = product / "5.2" / "config" / "angelo-agent"
        self.synthetic(product / "5.1" / "config" / "angelo-agent" / "sessions", "chat.json")
        roots = private_roots.private_roots(state, self.accounts, [], environ={})
        self.assertIn(product, roots)
        files = FileWorkspace(self.base, excluded=roots)
        self.refused(read, files, "AppData/Roaming/Angelo/5.1/config/angelo-agent/sessions/chat.json")


class ScratchTests(Fixture):
    def test_every_scratch_folder_goes_through_private_roots(self):
        for path in sorted((ADDONS / "angelo_agent").glob("*.py")):
            if path.name == "private_roots.py":
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                for call in ("tempfile.mkdtemp(", "tempfile.TemporaryDirectory(", "storage.tempfile.mkdtemp("):
                    self.assertNotIn(call, text)
        with self.assertRaisesRegex(ValueError, "FAMILIES"):
            private_roots.allocate("angelo-undeclared-")

    def test_a_crashed_converters_scratch_is_private_in_a_fresh_process(self):
        """Review probe 4: ``angelo-assets-*/batch.json`` with a private prompt, left by a crash."""
        self.synthetic(self.base / "angelo-assets-crashed", "batch.json")
        self.synthetic(self.base / "public")
        code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path\n"
                "from angelo_agent.files import FileWorkspace\n"
                "files = FileWorkspace(Path(sys.argv[2]), excluded=[])\n"
                "out = {'listed': [e['name'] for e in files.execute('angelo_list_files', {'path': '.'})['entries']]}\n"
                "try:\n"
                "    files.execute('angelo_read_file', {'path': 'angelo-assets-crashed/batch.json', 'start': 0, 'length': 9})\n"
                "    out['read'] = 'read'\n"
                "except ValueError as error:\n"
                "    out['read'] = str(error)\n"
                "print(json.dumps(out))\n")
        environment = dict(os.environ, TMPDIR=str(self.base), TEMP=str(self.base), TMP=str(self.base))
        result = subprocess.run([sys.executable, "-I", "-c", code, str(ADDONS), str(self.base)], env=environment,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        outcome = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertNotIn("angelo-assets-crashed", outcome["listed"])
        self.assertIn("public", outcome["listed"])
        self.assertIn("Application profiles", outcome["read"])


if __name__ == "__main__":
    unittest.main()
