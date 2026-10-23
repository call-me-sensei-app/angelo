# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Headless sessions apply the team's app policy from an explicit record the window writes, never from a file's absence.

Break-it review (2026-10-23, HEADLESS-POLICY-BREAKIT-9): the launcher read ``<connections>/team-org-policy.json`` and
took a missing file as "no organization", so one failed write, another connections folder, a deleted file or a dangling
link left headless with no team policy (S1). Now the window records the policy it applies, "none" included, in
``<state>/mcp/organization.json`` from ``pump`` (retried until written, only on change), and the launcher counts
anything but a readable record as the strictest policy. Also: signing out records none and removes another console's
copy (M1); every reader opens only a small regular file without following a link (M2); and the child's real
``headless_main.main`` installs ``--organization`` as its organization source (M5).
Contract: doc/angelo/external_mcp.rst, "Organization policy".
"""

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import external_clients, managed_policy, team_org_policy  # noqa: E402
from angelo_agent.external_clients import (ORGANIZATION_FILE, read_access, read_organization,  # noqa: E402
                                           write_access, write_organization)
from angelo_agent.external_host import ExternalHost, UiEnvironment  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402
from angelo_agent.team_view import OFFLINE, RESTORING, SIGNED_IN, SIGNED_OUT, AccountView, org_policy  # noqa: E402
import test_native_headless_mcp as harness  # noqa: E402  (a module: its tests are not collected again here)
from test_native_external_cli_host import Environment  # noqa: E402

STRICTEST = org_policy(team_org_policy.STRICTEST)
TEAM = {"allowLocalCliAccess": True, "requireAskForEdits": True, "adminContact": "it@example.com"}


class Case(unittest.TestCase):
    def setUp(self):
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        self.root = Path(tempfile.mkdtemp(prefix="orgrec-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state = str(self.root / "state")
        self.record = Path(self.state) / "mcp" / ORGANIZATION_FILE

    def host(self, organization):
        environment = Environment(self.state)
        environment.organization = organization
        host = ExternalHost(environment, environ={}, runtime_directory=self.root / "run")
        self.addCleanup(host.stop)
        return host


class WindowRecordTests(Case):
    def test_pump_records_the_policy_the_window_applies(self):
        applied = [dict(TEAM)]
        host = self.host(lambda: applied[0])
        with patch.object(host, "wanted", lambda: (False, "Off")):
            host.pump()
        self.assertEqual(read_organization(self.state), org_policy(TEAM), "pump's own step, also with access off")
        self.assertEqual(stat.S_IMODE(self.record.stat().st_mode), 0o600)
        applied[0] = None
        with patch.object(host, "wanted", lambda: (False, "Off")):
            host.pump()
        self.assertIsNone(read_organization(self.state), "no organization is written, not left as a missing file")
        self.assertEqual(json.loads(self.record.read_text()), {"version": 1, "organization": None})

    def test_a_failed_write_is_retried_and_headless_fails_closed_meanwhile(self):
        host = self.host(lambda: None)
        real, attempts = external_clients.write_organization, []

        def failing(state, policy):
            attempts.append(policy)
            raise PermissionError("read-only state folder")
        with patch.object(external_clients, "write_organization", failing):
            host.record_organization()
        self.assertEqual(attempts, [None])
        self.assertFalse(self.record.exists())
        self.assertEqual(read_organization(self.state), STRICTEST, "never written: the strictest policy, not none")
        with patch.object(external_clients, "write_organization", lambda state, policy: (attempts.append(policy),
                                                                                         real(state, policy))):
            host.record_organization()
        self.assertEqual(attempts, [None, None], "the next tick writes it")
        self.assertIsNone(read_organization(self.state))

    def test_it_writes_only_when_the_policy_differs(self):
        applied = [dict(TEAM)]
        host = self.host(lambda: applied[0])
        real, writes = external_clients.write_organization, []
        with patch.object(external_clients, "write_organization",
                          lambda state, policy: (writes.append(policy), real(state, policy))):
            for _ in range(3):
                host.record_organization()
            applied[0] = dict(TEAM)   # An equal policy in a new dict is no change.
            host.record_organization()
            applied[0]["requireAskForEdits"] = False   # Mutating the source's dict is a change, never aliased.
            host.record_organization()
            host.record_organization()
        self.assertEqual(len(writes), 2, writes)
        self.assertFalse(read_organization(self.state)["requireAskForEdits"])

    def test_a_host_without_an_organization_source_or_in_a_background_blender_records_nothing(self):
        self.host(None).record_organization()
        bare = ExternalHost.__new__(ExternalHost)
        bare.env = SimpleNamespace(access_requested=lambda: True)
        bare.record_organization()

        def background():
            raise LookupError("no window")
        self.host(background).record_organization()
        self.assertFalse(self.record.exists())

    def test_the_window_environment_reports_its_organization_source(self):
        environment = UiEnvironment(ui=None)
        managed_policy.set_organization_source(lambda: dict(TEAM))
        with patch.dict(sys.modules, {"bpy": SimpleNamespace(app=SimpleNamespace(background=False))}):
            self.assertEqual(environment.organization(), TEAM)
            managed_policy.set_organization_source(None)
            self.assertIsNone(environment.organization(), "no team sign-in: no organization")
        managed_policy.set_organization_source(lambda: dict(TEAM))
        with patch.dict(sys.modules, {"bpy": SimpleNamespace(app=SimpleNamespace(background=True))}):
            with self.assertRaises(LookupError, msg="a background Blender never restores a sign-in"):
                environment.organization()


class LauncherReaderTests(Case):
    def write_raw(self, data):
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_bytes(data)

    def test_only_an_explicit_record_lifts_the_strictest_policy(self):
        self.assertEqual(read_organization(self.state), STRICTEST, "missing")
        write_organization(self.state, None)
        self.assertIsNone(read_organization(self.state), "the window recorded no organization")
        write_organization(self.state, TEAM)
        self.assertEqual(read_organization(self.state), org_policy(TEAM))
        write_organization(self.state, {"allowLocalCliAccess": False, "extra": "ignored"})
        self.assertEqual(read_organization(self.state), org_policy({"allowLocalCliAccess": False}))

    def test_anything_but_a_readable_record_is_the_strictest_policy(self):
        cases = {"malformed": b"{not json", "not utf-8": b"\xff\xfe", "a list": b"[]",
                 "no version": json.dumps({"organization": None}).encode(),
                 "another version": json.dumps({"version": 2, "organization": None}).encode(),
                 "no organization key": json.dumps({"version": 1}).encode(),
                 "a policy of the wrong type": json.dumps({"version": 1, "organization": "none"}).encode(),
                 "oversized": json.dumps({"version": 1, "organization": None,
                                          "pad": "x" * team_org_policy.MAX_FILE}).encode()}
        for name, data in cases.items():
            with self.subTest(name):
                self.write_raw(data)
                self.assertEqual(read_organization(self.state), STRICTEST)

    def test_an_unreadable_name_counts_as_the_strictest_policy_and_never_blocks(self):
        write_organization(self.state, None)
        os.chmod(self.record, 0)
        if not os.access(self.record, os.R_OK):   # Root reads it anyway.
            self.assertEqual(read_organization(self.state), STRICTEST, "unreadable")
        self.record.unlink()
        os.mkfifo(self.record)
        self.assertEqual(read_organization(self.state), STRICTEST, "a FIFO: neither none nor a hang")
        self.record.unlink()
        self.record.mkdir()
        self.assertEqual(read_organization(self.state), STRICTEST, "a directory")
        self.record.rmdir()
        allowed = self.root / "elsewhere.json"
        allowed.write_text(json.dumps({"version": 1, "organization": None}))
        self.record.symlink_to(allowed)
        self.assertEqual(read_organization(self.state), STRICTEST, "a link, even to a valid record")
        self.record.unlink()
        self.record.symlink_to("/dev/zero")
        self.assertEqual(read_organization(self.state), STRICTEST, "a link to /dev/zero")
        self.record.unlink()
        self.record.symlink_to(self.root / "missing.json")
        self.assertEqual(read_organization(self.state), STRICTEST, "a dangling link")

    def test_the_access_switch_reader_is_as_careful(self):
        """Break-it M2: access.json is read the same way (not a regular file, a link or oversized: never recorded)."""
        write_access(self.state, True)
        self.assertIs(read_access(self.state), True)
        path = Path(self.state) / "mcp" / external_clients.ACCESS_FILE
        target = self.root / "on.json"
        path.replace(target)
        path.symlink_to(target)
        self.assertIsNone(read_access(self.state), "a link")
        path.unlink()
        os.mkfifo(path)
        self.assertIsNone(read_access(self.state), "a FIFO")
        path.unlink()
        path.write_text(json.dumps({"version": 1, "allow_local_cli": True, "pad": "x" * team_org_policy.MAX_FILE}))
        self.assertIsNone(read_access(self.state), "oversized")

    def test_the_saved_window_copy_uses_the_same_bounded_reader(self):
        folder = self.root / "connections"
        folder.mkdir()
        (folder / team_org_policy.FILE).symlink_to("/dev/zero")
        self.assertEqual(team_org_policy.read_saved(folder), STRICTEST)


class SignOutTests(Case):
    """Signing out records "no organization" (M1), and the window's copy from another console goes too."""

    def account(self, console, view):
        return SimpleNamespace(console_url=console, view=lambda: view[0])

    def test_sign_out_records_no_organization_after_a_console_switch(self):
        connections = self.root / "connections"
        connections.mkdir()
        view = [AccountView(status=SIGNED_IN, org_policy=org_policy(TEAM))]
        first = team_org_policy.OrganizationPolicy(self.account("https://a", view), str(connections))
        first.remember(view[0])
        host = self.host(first.current)
        host.record_organization()
        self.assertEqual(read_organization(self.state), org_policy(TEAM))
        self.assertTrue((connections / team_org_policy.FILE).exists())

        # The person switches to console B: A's copy is not B's to apply, and B sends no policy.
        view[0] = AccountView(status=RESTORING)
        second = team_org_policy.OrganizationPolicy(self.account("https://b", view), str(connections))
        self.assertIsNone(second.known)
        host.env.organization = second.current
        view[0] = AccountView(status=SIGNED_IN, org_policy=None)
        second.remember(view[0])
        host.record_organization()
        self.assertIsNone(read_organization(self.state), "B has no policy: none, recorded")
        view[0] = AccountView(status=SIGNED_OUT)
        second.remember(view[0])
        host.record_organization()
        self.assertIsNone(read_organization(self.state))
        self.assertFalse((connections / team_org_policy.FILE).exists(), "A's copy is removed at sign-out")

    def test_signing_out_replaces_the_recorded_policy_with_none(self):
        connections = self.root / "connections"
        connections.mkdir()
        view = [AccountView(status=SIGNED_IN, org_policy=org_policy(TEAM))]
        organization = team_org_policy.OrganizationPolicy(self.account("https://a", view), str(connections))
        organization.remember(view[0])
        host = self.host(organization.current)
        host.record_organization()
        view[0] = AccountView(status=OFFLINE)
        organization.remember(view[0])
        host.record_organization()
        self.assertEqual(read_organization(self.state), org_policy(TEAM), "offline never lifts it")
        view[0] = AccountView(status=SIGNED_OUT)
        organization.remember(view[0])
        host.record_organization()
        self.assertIsNone(read_organization(self.state))
        self.assertFalse((connections / team_org_policy.FILE).exists())


class ChildWiringTests(Case):
    """M5: the real ``headless_main.main`` parses ``--organization`` and installs it as the organization source."""

    def run_main(self, organization_argument):
        from angelo_agent import headless_main
        private = Path(tempfile.mkdtemp(prefix=headless_main.PRIVATE_PREFIX, dir=str(self.root)))
        os.chmod(private, 0o700)
        argv = ["--in", "0", "--out", "1", "--file", str(self.root / "scene.blend"), "--client", "claude-code",
                "--app", "claude_code", "--levels", "read,edit", "--private", str(private)]
        if organization_argument is not None:
            argv += ["--organization", organization_argument]
        environ = {"ANGELO_STATE_DIR": str(private / "state"), "ANGELO_HEADLESS": "1"}
        seen = []
        with patch.object(headless_main, "_serve", lambda bpy, options, server: seen.append(
                (options.organization, managed_policy.current()))), \
                patch.dict(sys.modules, {"bpy": SimpleNamespace(app=SimpleNamespace(background=True))}):
            headless_main.main(argv, environ)
        self.assertFalse(private.exists(), "the child removes its private folder")
        return seen[0]

    def test_the_launchers_organization_becomes_the_childs_source(self):
        options, policy = self.run_main(json.dumps({"allowLocalCliAccess": True, "requireAskForEdits": True,
                                                    "allowAutoApproval": False}))
        self.assertTrue(options["requireAskForEdits"])
        self.assertTrue(policy.require_ask_for_edits)
        self.assertFalse(policy.allow_auto_approval)

    def test_an_unusable_argument_is_the_strictest_and_none_installs_nothing(self):
        _, policy = self.run_main("not json")
        self.assertFalse(policy.allow_local_cli_access)
        self.assertTrue(policy.require_ask_for_edits)
        managed_policy.reset(Policy())
        options, policy = self.run_main(None)
        self.assertIsNone(options)
        self.assertEqual(policy, Policy())


@unittest.skipIf(sys.platform == "win32", "Headless sessions are macOS and Linux for now")
class EndToEndTests(unittest.TestCase):
    """The real launcher's reader (as ``external_mcp.main`` builds it) over the harness's in-process child."""

    tick, call, open = harness.HeadlessTests.tick, harness.HeadlessTests.call, harness.HeadlessTests.open
    start_child = harness.HeadlessTests.start_child

    def setUp(self):
        harness.HeadlessTests.setUp(self)
        self.server.organization, self.server.recorded = self.mains_reader()

    def mains_reader(self):
        """The ``organization`` and ``recorded`` readers that ``external_mcp.main`` gives its Server for this state
        folder."""
        from angelo_agent import external_mcp
        built = {}

        class Recorder:
            def __init__(self, client, state, stdout=None, headless=None, organization=None, machine=None):
                built.update(state=state, organization=organization, machine=machine, server=self)
                self.headless = SimpleNamespace(close=lambda: None)

            def serve(self, stdin):
                return None
        stdout = sys.stdout
        try:
            with patch.object(external_mcp, "Server", Recorder), patch.object(sys, "stdin", io.StringIO("")):
                external_mcp.main(["--client", self.name, "--state-dir", self.state], environ={})
        finally:
            sys.stdout = stdout
        self.assertEqual(str(built["state"]), self.state)
        return built["organization"], built["server"].recorded

    def test_a_missing_record_refuses_and_says_to_open_angelo_once(self):
        """Fails closed, and says why: never "your organization" to a person who has none (re-check FR2-L4)."""
        self.assertFalse((Path(self.state) / "mcp" / ORGANIZATION_FILE).exists())
        result = self.call("angelo_open_file", {"file": str(self.source)})
        self.assertTrue(result["isError"], result)
        self.assertIn("Open Angelo once", json.dumps(result))
        self.assertNotIn("organization turned off access", json.dumps(result))
        self.assertIsNone(self.host, "no child was started")

    def test_a_recorded_organization_that_turns_access_off_says_so(self):
        environment = Environment(self.state)
        environment.organization = lambda: {"allowLocalCliAccess": False}
        window = ExternalHost(environment, environ={}, runtime_directory=self.root / "run")
        self.addCleanup(window.stop)
        window.record_organization()
        result = self.call("angelo_open_file", {"file": str(self.source)})
        self.assertIn("organization turned off access", json.dumps(result))
        managed_policy.reset(Policy(allow_local_cli_access=False))   # The machine policy turns it off too.
        os.remove(Path(self.state) / "mcp" / ORGANIZATION_FILE)
        result = self.call("angelo_open_file", {"file": str(self.source)})
        managed_policy.reset(Policy())
        self.assertNotIn("Open Angelo once", json.dumps(result), "the machine's own refusal is not the record's")

    def test_the_windows_record_governs_the_launcher_and_the_child(self):
        applied = [None]
        environment = Environment(self.state)
        environment.organization = lambda: applied[0]
        window = ExternalHost(environment, environ={}, runtime_directory=self.root / "run")
        self.addCleanup(window.stop)
        window.record_organization()
        self.open()
        self.assertNotIn("--organization", self.child_options, "no organization: none handed to the child")
        self.call("angelo_close_file")
        applied[0] = {"requireAskForEdits": True}
        window.record_organization()
        self.open()
        self.assertEqual(self.child_options.get("--approval"), "ASK")
        self.assertTrue(json.loads(self.child_options["--organization"])["requireAskForEdits"])
        edit = self.call("angelo_create_cube", harness.CUBE)
        self.assertTrue(edit["isError"])
        self.assertIn("needs_gui", json.dumps(edit))
        os.remove(Path(self.state) / "mcp" / ORGANIZATION_FILE)   # Deleted under an open session.
        result = self.call("angelo_inspect_scene")
        self.assertTrue(result["isError"])
        self.assertIn("Open Angelo once", json.dumps(result), "the next call applies the strictest, and says why")


if __name__ == "__main__":
    unittest.main()
