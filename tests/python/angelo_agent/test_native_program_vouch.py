# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A program Angelo finds or the person chooses through a symbolic link still starts after an agent script ran
(owned_paths.vouch_program, cli_runtime.find_executable; 2.0.1 MCP acceptance run).

Claude Code's official installer makes ``~/.local/bin/claude`` a link to ``~/.local/share/claude/versions/X``.
``find_executable`` vouched only the name it found, which C refuses for a link; once a script has run, C's
``may_run`` resolves the real file, which nothing vouched and whose folder is not on PATH, so every new runtime start
failed, and the refusal read as "its version could not be read". Against a model of exactly those C rules (C's own
proof is test_blender_runtime_symlink, in the app): found through a link, it starts after a script, and so does an
update that retargets the link to a new version; a link a script made or retargeted never does, nor a program changed
in place, even unvouched and found again; a refusal says what happened (changed on disk: restart Angelo).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from angelo_agent import claude_transport, cli_runtime, owned_paths
from angelo_agent.protocol import RpcError


class CRules:
    """C's rules this needs (bpy_angelo_guard_confine_writable.cc, _vouches.cc), unchanged checks included:

    * a vouch of a name whose last part is a link records nothing; a vouch keys the file by its parent-resolved name;
    * a program vouch (Scope::Run) is never a write vouch and runs only while its file is unchanged (identity, size,
      times); once a region has run a recorded program keeps what it captured; narrowing (``unvouch_path``) never
      drops it;
    * a write vouch runs a program only while its file is unchanged too;
    * before any script region every program may run, after one only a resolved file vouched as above, one whose
      folder is on PATH (no facts check), and never one a script made;
    * every path is compared as C keys it: on Windows lower-cased (``normalized``), as the file system is."""

    def __init__(self, path_folders):
        self.vouched, self.programs, self.region, self.made = {}, {}, False, set()
        self.path_folders = {self.real(folder) for folder in path_folders}

    @staticmethod
    def real(path):
        return os.path.normcase(os.path.realpath(path))

    @staticmethod
    def facts(path):
        info = os.lstat(path)
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @staticmethod
    def key(path):
        return os.path.normcase(os.path.join(os.path.realpath(os.path.dirname(os.path.abspath(path))),
                                             os.path.basename(path)))

    def vouch_program(self, path):
        if os.path.islink(path) or not os.path.isfile(path):
            return False
        key, now = self.key(path), self.facts(path)
        if key in self.programs and self.region and self.programs[key] != now:
            return False
        self.programs[key] = now
        return True

    def vouch(self, path, folder, persist):
        if os.path.islink(path):
            return False
        self.vouched[self.key(path)] = self.facts(path) if os.path.isfile(path) else None
        return True

    def unvouch(self, path, transient_only):
        self.vouched.pop(self.key(path), None)   # Never a program's record.

    def may_write(self, path):
        return not self.region or self.key(path) in self.vouched

    def program_changed(self, path):
        resolved = self.real(path)
        return resolved in self.programs and (not os.path.exists(resolved)
                                              or self.programs[resolved] != self.facts(resolved))

    def may_run(self, path):
        if not self.region:
            return True
        resolved = self.real(path)
        if os.path.normcase(path) in self.made or resolved in self.made:
            return False
        unchanged = os.path.exists(resolved) and self.facts(resolved)
        if resolved in self.programs and self.programs[resolved] == unchanged:
            return True
        if resolved in self.vouched and self.vouched[resolved] == unchanged:
            return True
        return os.path.dirname(resolved) in self.path_folders

    def script_made(self, path):
        return os.path.normcase(os.fspath(path)) in self.made

    def mark_made(self, path):   # C's change record: a script wrote, linked or named it.
        self.made.add(os.path.normcase(os.fspath(path)))


# A program's name as the platform's finder takes it: Windows finds only a PATHEXT name (``where.exe claude`` is
# Claude Code's ``%USERPROFILE%\.local\bin\claude.exe``), POSIX any executable file.
EXE = ".exe" if os.name == "nt" else ""


def program(path, version="2.1.300"):
    """A stand-in CLI that answers ``--version`` as Claude Code does (a shell script: POSIX runs it, Windows only
    finds it by name and checks it, as these tests need, apart from the version probe)."""
    path = path.with_name(path.name + EXE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo \"%s (Claude Code)\"; fi\n" % version)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class ProgramsThroughALink(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="angelo-program-vouch-"))
        self.real = program(self.root / "share/claude/versions/2.1.300")
        self.link = self.root / ("bin/claude" + EXE)
        self.link.parent.mkdir(parents=True)
        try:
            self.link.symlink_to(self.real)
        except OSError as error:   # Windows without Developer Mode or the symbolic-link privilege.
            self.skipTest("[environment] this host can't make a symbolic link: %s" % error)
        self.c = CRules([self.link.parent])
        for name, value in (("_VOUCH", self.c.vouch), ("_MAY_RUN", self.c.may_run),
                            ("_SCRIPT_MADE", self.c.script_made), ("_VOUCH_PROGRAM", self.c.vouch_program),
                            ("_MAY_WRITE", self.c.may_write), ("_UNVOUCH", self.c.unvouch),
                            ("_PROGRAM_CHANGED", self.c.program_changed)):
            patcher = mock.patch.object(owned_paths, name, value, create=True)   # create: a build before the hook.
            patcher.start()
            self.addCleanup(patcher.stop)
        path = mock.patch.dict(os.environ, {"PATH": str(self.link.parent)})   # As ~/.local/bin is on PATH (putenv).
        path.start()
        self.addCleanup(path.stop)
        if os.name == "nt":   # Claude Code on Windows needs Git Bash, which these tests are not about.
            bash = mock.patch.object(cli_runtime.cli_windows, "git_bash", return_value="C:\\Git\\bin\\bash.exe")
            bash.start()
            self.addCleanup(bash.stop)

    def test_the_link_alone_was_refused_after_a_script(self):   # The defect, under C's rules.
        owned_paths.vouch(str(self.link))
        self.c.region = True
        self.assertFalse(owned_paths.may_run(str(self.link)))

    def test_a_program_found_through_a_link_starts_after_a_script(self):
        self.assertTrue(owned_paths.vouch_program(str(self.link)))
        self.c.region = True
        self.assertTrue(owned_paths.may_run(str(self.link)))

    def test_find_executable_vouches_the_preferences_pick_through_a_link(self):
        found = cli_runtime.find_executable("claude", str(self.link))
        self.assertEqual(Path(found), self.link)
        self.c.region = True
        self.assertTrue(owned_paths.may_run(found))

    def test_a_link_a_script_retargeted_is_refused_when_found_again(self):
        cli_runtime.find_executable("claude")
        self.c.region = True
        other = program(self.root / "elsewhere/claude-other")
        self.link.unlink()
        self.link.symlink_to(other)
        self.c.mark_made(str(self.link))   # A script retargeted it (C's change record).
        found = cli_runtime.find_executable("claude")   # As a new runtime start finds it.
        self.assertFalse(owned_paths.may_run(found))

    def test_an_update_that_retargets_the_link_starts_after_a_script(self):   # The installer's versions/Y.
        cli_runtime.find_executable("claude")
        self.c.region = True
        update = program(self.root / "share/claude/versions/2.1.310", "2.1.310")
        self.link.unlink()
        self.link.symlink_to(update)
        found = cli_runtime.find_executable("claude")
        self.assertTrue(owned_paths.may_run(found))
        self.assertEqual(owned_paths.require_runnable(found), found)
        if os.name != "nt":   # [environment] The stand-in is a shell script: only POSIX can start it.
            self.assertEqual(tuple(claude_transport.require_supported_runtime(found, dict(os.environ))), (2, 1, 310))

    def test_a_link_a_script_made_is_never_vouched_through(self):
        self.c.mark_made(str(self.link))
        owned_paths.vouch_program(str(self.link))
        self.c.region = True
        self.assertFalse(owned_paths.may_run(str(self.link)))

    def test_a_program_vouch_never_makes_its_file_writable(self):   # The review of 29b385a6645, MUST-1.
        owned_paths.vouch_program(str(self.link))
        self.c.region = True
        self.assertFalse(owned_paths.may_write(str(self.real)))
        self.assertFalse(owned_paths.may_write(str(self.link)))

    def test_a_program_rewritten_in_place_after_a_script_never_runs_even_when_found_again(self):
        owned_paths.vouch_program(str(self.link))
        self.c.region = True
        with open(self.real, "a") as handle:   # In place: same inode, new size and change time.
            handle.write("echo payload\n")
        self.assertFalse(owned_paths.may_run(str(self.link)))
        self.assertFalse(owned_paths.vouch_program(str(self.link)), "found again after a script: still refused")

    def test_an_unvouched_program_found_again_after_a_change_stays_refused(self):   # The re-review's S-1.
        owned_paths.vouch_program(str(self.link))
        self.c.region = True
        with open(self.real, "a") as handle:
            handle.write("echo payload\n")
        owned_paths.unvouch(str(self.real))   # Narrowing is anyone's; a program's record stays.
        owned_paths.unvouch(str(self.link))
        found = cli_runtime.find_executable("claude")
        self.assertFalse(owned_paths.may_run(found))

    def test_a_program_changed_on_disk_says_restart_angelo(self):   # The re-review's S-2: an update in place.
        owned_paths.vouch_program(str(self.link))
        self.c.region = True
        with open(self.real, "a") as handle:
            handle.write("echo reinstalled in place\n")
        with self.assertRaises(RpcError) as caught:
            claude_transport.require_supported_runtime(str(self.link), dict(os.environ))
        message = str(caught.exception)
        self.assertIn("changed on disk", message)
        self.assertIn("Restart Angelo", message)
        self.assertNotIn("reinstall", message)

    def test_a_program_a_script_wrote_says_so(self):   # Scripts may write files outside Angelo's folders.
        owned_paths.vouch_program(str(self.link))
        self.c.region = True
        self.c.mark_made(os.path.realpath(self.real))   # C's change record: a script wrote it (as C resolves it).
        with self.assertRaises(RpcError) as caught:
            claude_transport.require_supported_runtime(str(self.link), dict(os.environ))
        message = str(caught.exception)
        self.assertIn("an agent script wrote or named", message)
        self.assertIn("Forget Agent Changes in a Folder", message)
        self.assertNotIn("Restart Angelo", message)   # C's record outlives a restart.

    def test_each_refusal_names_its_cause_for_the_caller(self):
        """A caller that reports a refused start in its own words (a web export's finding) reads why, never the
        English sentence: ``run_refusal`` is ``agent_change``, ``changed`` or ``unknown`` (the gltfpack refusal text,
        deferred from 2.0.1)."""
        self.c.region = True   # Nothing vouched.
        with self.assertRaises(PermissionError) as caught:
            owned_paths.require_runnable(str(self.link))
        self.assertEqual(caught.exception.run_refusal, owned_paths.RUN_UNKNOWN)
        owned_paths.vouch_program(str(self.link))
        with open(self.real, "a") as handle:
            handle.write("echo reinstalled in place\n")
        with self.assertRaises(PermissionError) as caught:
            owned_paths.require_runnable(str(self.link))
        self.assertEqual(caught.exception.run_refusal, owned_paths.RUN_CHANGED)
        self.c.mark_made(os.path.realpath(self.real))
        with self.assertRaises(PermissionError) as caught:
            owned_paths.require_runnable(str(self.link))
        self.assertEqual(caught.exception.run_refusal, owned_paths.RUN_AGENT_CHANGE)
        self.assertEqual(len({owned_paths.RUN_UNKNOWN, owned_paths.RUN_CHANGED, owned_paths.RUN_AGENT_CHANGE}), 3)

    def test_a_path_a_script_rewrote_in_place_is_never_searched(self):   # The review's SHOULD-2.
        planted = program(self.root / "planted/claude", "9.9.9")
        key = os.environ.encodekey("PATH")
        with mock.patch.dict(os.environ, {"PATH": str(self.link.parent)}):   # The process's PATH (putenv): a stand-in.
            before = os.environ._data[key]
            os.environ._data[key] = os.environ.encodevalue(str(planted.parent))   # In place: no putenv.
            try:
                found = cli_runtime.find_executable("claude")
            finally:
                os.environ._data[key] = before
        self.assertEqual(Path(found), self.link)

    def test_a_refused_start_says_so_never_too_old(self):
        self.c.region = True   # Nothing vouched: the probe's start is refused.
        with self.assertRaises(RpcError) as caught:
            claude_transport.require_supported_runtime(str(self.link), dict(os.environ))
        message = str(caught.exception)
        self.assertNotIn("version could not be read", message)
        self.assertNotIn("or newer", message)
        self.assertIn("starts only", message)
        self.assertIn("Choose the program in the connection's settings", message)


class RefusalsAreReported(unittest.TestCase):   # The re-review of 4be92cfb84f, N-2.
    REFUSED = PermissionError("Angelo starts only installed programs, programs on PATH or ones you chose: codex. "
                              "Choose the program in the connection's settings, or reinstall it with its official "
                              "installer.")

    def test_a_refused_runtime_keeps_the_endpoint_catalog_and_raises_the_refusal(self):
        from angelo_agent import endpoint_catalog
        home = Path(tempfile.mkdtemp(prefix="angelo-endpoint-catalog-"))
        program_file = program(home / "bin/codex")
        kept = home / endpoint_catalog.FILE_NAME
        kept.write_text('{"models": []}')
        spec = {"models": [{"id": "team-gpt", "profile": "gpt-sol"}]}
        with mock.patch.object(owned_paths, "require_runnable", side_effect=self.REFUSED), \
                mock.patch.object(owned_paths, "require_writable", side_effect=lambda path: path):
            with self.assertRaises(PermissionError) as caught:
                endpoint_catalog.write(str(program_file), spec, home)
        self.assertIn("starts only", str(caught.exception))
        self.assertTrue(kept.is_file(), "the catalog already written stays")

    def test_a_refused_sign_in_check_says_so(self):
        from angelo_agent import authentication
        state = Path(tempfile.mkdtemp(prefix="angelo-auth-refused-"))
        auth = authentication.NativeAuthentication()
        with mock.patch.object(authentication, "native_auth_command", return_value=(["codex"], {})), \
                mock.patch.object(authentication, "launch", side_effect=self.REFUSED), \
                mock.patch.object(owned_paths, "require_writable", side_effect=lambda path: path):
            auth.start("codex_subscription", {}, str(state))
            identity, status, *_ = auth.events.get(timeout=CAP_S)   # The check thread's own answer.
        self.assertEqual(status, str(self.REFUSED))
        self.assertNotIn("unavailable", status)

if __name__ == "__main__":
    unittest.main()
