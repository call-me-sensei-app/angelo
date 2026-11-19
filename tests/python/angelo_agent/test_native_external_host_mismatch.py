# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Two Angelo builds for one user: the launcher and the host say which build they meet, and what to do.

Reproduces the 2.0.1 internal test (two builds open, one profile): 2.0.0 held the endpoint, 2.0.1's launcher got
"Unknown command", 2.0.1 said nothing useful, and Codex's entry still started 2.0.0's launcher. Each test names
the behaviour that failed on acd0d465ee9; an app entry that starts another build's launcher is tested in
test_native_external_entry_elsewhere. Real AF_UNIX sockets under /tmp, a fresh profile each, never the real runtime
directory or an app's real settings. Contract: doc/angelo/external_mcp.rst, "Two Angelo builds".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import plistlib
import secrets
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from host_test_policy import environment_skip  # noqa: E402
from angelo_agent import app_version, cli_client, external_build, external_cli  # noqa: E402
from angelo_agent import external_cli_protocol as protocol  # noqa: E402
from angelo_agent.external_cli import LocalCliServer, TransportUnavailable  # noqa: E402

UNIX_TRANSPORT = unittest.skipIf(sys.platform == "win32", environment_skip(
    "Windows has no CLI transport yet: the server refuses to listen there (doc/angelo/external_cli.rst)"))
OLD = {}   # What 2.0.0 publishes about its build: nothing (no version, no agent protocol).
MINE = app_version.current()


def echo(calls):
    def handle(request, client):
        calls.append(request)
        return protocol.envelope("ok", "echo", result={"command": request.get("command")})
    return handle


@UNIX_TRANSPORT
class HandshakeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="smis-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state, self.runtime, self.calls, self.key = self.root / "state", self.root / "run", [], os.urandom(32)

    def host(self, published=None):
        """A listening host; ``published`` stands in for what an older or newer build says about itself."""
        server = LocalCliServer(self.state, echo(self.calls), runtime_directory=self.runtime,
                                apps=lambda: {"codex": self.key})
        if published is None:
            server.start()
        else:
            with patch.object(external_build, "identity", return_value=published):
                server.start()
            patcher = patch.object(external_build, "identity", return_value=published)   # The welcome too.
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(server.stop)
        return server

    def send(self, key=None):
        return cli_client.run({"command": "agent", "args": {"action": "settings"}}, self.state, "codex", 5,
                              key=self.key if key is None else key)

    def test_this_build_publishes_its_version_and_command_set_where_2_0_0_s_reader_accepts_them(self):
        server = self.host()
        record = json.loads(server.endpoint_path.read_text())
        self.assertEqual((record["angelo_version"], record["agent_protocol"]), (MINE, external_build.AGENT_PROTOCOL))
        self.assertEqual(record["cli_protocol"], 2, "framing unchanged: an older launcher still reaches this host")
        self.assertEqual(protocol.read_endpoint(server.endpoint_path)["pid"], os.getpid())
        self.assertEqual(self.send()["code"], "ok")

    def test_a_launcher_meeting_2_0_0_names_it_and_says_what_to_do_never_unknown_command(self):
        """Failed on acd0d465ee9: the request reached 2.0.0, which answered invalid "Unknown command"."""
        self.host(OLD)
        with patch.object(external_build, "process_version", return_value="2.0.0"):
            envelope = self.send()
        self.assertEqual(envelope["code"], "incompatible")
        self.assertIn(f"Angelo 2.0.0 (PID {os.getpid()}) has access for external apps", envelope["message"])
        self.assertIn(f"quit it, or turn off access there (Preferences > AI Providers > External AI apps), then "
                      f"turn it on in Angelo {MINE}", envelope["message"])
        self.assertEqual(self.calls, [], "no command reaches a host that cannot run it")

    def test_an_unknown_older_build_is_still_named_by_its_pid(self):
        self.host(OLD)
        with patch.object(external_build, "process_version", return_value=None):
            envelope = self.send()
        self.assertEqual(envelope["code"], "incompatible")
        self.assertIn(f"Another Angelo (PID {os.getpid()})", envelope["message"])

    def test_a_newer_host_asks_for_connect_there(self):
        self.host({"angelo_version": "9.0.0", "agent_protocol": external_build.AGENT_PROTOCOL + 1})
        envelope = self.send()
        self.assertEqual(envelope["code"], "incompatible")
        self.assertIn("Angelo 9.0.0", envelope["message"])
        self.assertIn("press Connect for this app in Angelo 9.0.0", envelope["message"])
        self.assertEqual(self.calls, [])

    def raw_hello(self, server, **fields):
        """A launcher's hello, as an older (no ``agent_protocol``) or newer one sends it."""
        record = protocol.read_endpoint(server.endpoint_path)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(record["socket"])
            challenge = protocol.recv_frame(sock, 4096)
            nonce = secrets.token_hex(32)
            token = bytes.fromhex(record["token"])
            hello = {"type": "hello", "cli_protocol": protocol.CLI_PROTOCOL, "client": "codex", "client_nonce": nonce,
                     "proof": protocol.client_proof(token, challenge["server_nonce"], nonce),
                     "app_proof": protocol.app_proof(self.key, challenge["server_nonce"], nonce), **fields}
            protocol.send_frame(sock, hello, 16 * 1024)
            return protocol.recv_frame(sock, 65536)

    def test_2_0_0_s_launcher_reaching_this_host_is_told_to_press_connect_here(self):
        """Failed on acd0d465ee9: an older launcher was admitted and its tool calls refused without saying why."""
        server = self.host()
        answer = self.raw_hello(server)   # 2.0.0's hello carries no agent protocol.
        self.assertEqual(answer["type"], "error")
        self.assertEqual(answer["envelope"]["code"], "incompatible")
        self.assertIn("starts an older Angelo's MCP server", answer["envelope"]["message"])
        self.assertIn(f"press Connect for this app in Angelo {MINE}", answer["envelope"]["message"])
        newer = self.raw_hello(server, agent_protocol=external_build.AGENT_PROTOCOL + 1)
        self.assertIn("starts a newer Angelo's MCP server", newer["envelope"]["message"])
        self.assertEqual(self.raw_hello(server, agent_protocol=external_build.AGENT_PROTOCOL)["type"], "welcome")
        self.assertEqual(self.calls, [])

    def test_a_keyless_status_reader_without_a_command_set_still_reads_status(self):
        """The plain ``angelo status`` of any build reads status only; it is not an app's launcher."""
        server = self.host()
        record = protocol.read_endpoint(server.endpoint_path)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(record["socket"])
            challenge = protocol.recv_frame(sock, 4096)
            nonce = secrets.token_hex(32)
            protocol.send_frame(sock, {"type": "hello", "cli_protocol": protocol.CLI_PROTOCOL, "client": "tester",
                                       "client_nonce": nonce, "proof": protocol.client_proof(
                                           bytes.fromhex(record["token"]), challenge["server_nonce"], nonce)}, 16384)
            self.assertEqual(protocol.recv_frame(sock, 65536)["type"], "welcome")

    def test_a_second_build_learns_who_holds_the_endpoint_and_never_takes_it(self):
        """Failed on acd0d465ee9: "Another Angelo process (PID n) provides local CLI access for this profile"."""
        first = self.host(OLD)
        record_before = first.endpoint_path.read_bytes()
        second = LocalCliServer(self.state, echo(self.calls), runtime_directory=self.runtime)
        with patch.object(external_cli, "process_alive", return_value=True), \
                patch.object(external_build, "process_version", return_value="2.0.0"), \
                patch.object(external_cli.os, "getpid", return_value=os.getpid() + 1):
            with self.assertRaises(TransportUnavailable) as raised:
                second.start()
        self.assertEqual(raised.exception.holder, ("2.0.0", os.getpid()))
        self.assertIn(f"Angelo 2.0.0 (PID {os.getpid()}) has access for external apps for your user. Quit it, or "
                      "turn off access there; this Angelo takes over once it is free.", str(raised.exception))
        self.assertEqual(first.endpoint_path.read_bytes(), record_before, "the other build's record is untouched")
        self.assertTrue(first.listening)



@UNIX_TRANSPORT
class LauncherAgainstAnOlderHostTests(unittest.TestCase):
    """The 2.0.1 acceptance run's step 6B (acd0d465ee9): with 2.0.0 holding access, 2.0.1's launcher listed its tools,
    look/window/state/usage answered "Use agent send, read, status…" and send "This chat's file was never saved"."""

    def setUp(self):
        from angelo_agent.external_clients import AppRegistry
        self.root = Path(tempfile.mkdtemp(prefix="smis-l-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state, self.calls = self.root / "state", []
        registry = AppRegistry(self.state)
        self.name = registry.connect("claude_code")
        key = registry.key(self.name)
        server = LocalCliServer(self.state, echo(self.calls), runtime_directory=self.root / "run",
                                apps=lambda: {self.name: key})
        for patcher in (patch.object(external_build, "identity", return_value=OLD),   # The host is 2.0.0-shaped.
                        patch.object(external_build, "process_version", return_value="2.0.0")):
            patcher.start()
            self.addCleanup(patcher.stop)
        server.start()
        self.addCleanup(server.stop)

    def launcher(self):
        import io
        from angelo_agent.external_mcp import Server
        return Server(self.name, str(self.state), stdout=io.StringIO())

    def test_every_tool_answers_which_build_holds_access_and_what_to_do(self):
        from angelo_agent.external_mcp_hidden import HIDDEN_NAMES
        from angelo_agent.external_mcp_tools import AGENT_NAMES
        launcher = self.launcher()
        for tool in sorted(set(AGENT_NAMES) - set(HIDDEN_NAMES)):
            with self.subTest(tool=tool):
                arguments = {"message": "x"} if tool == "angelo_send" else {"id": "x"} if tool == "angelo_image" else {}
                result = launcher.call(1, {"name": tool, "arguments": arguments})
                self.assertTrue(result["isError"], result)
                text = json.dumps(result)
                self.assertIn("incompatible", text)
                self.assertIn(f"Angelo 2.0.0 (PID {os.getpid()}) has access for external apps", text)
                self.assertNotIn("Use agent send", text)
        self.assertEqual(self.calls, [], "no tool's command reached the other build")

    def test_initialize_says_it_before_any_call_and_tools_stay_listed(self):
        launcher = self.launcher()
        instructions = launcher.answer("initialize", {})["instructions"]
        self.assertTrue(instructions.startswith(f"Angelo 2.0.0 (PID {os.getpid()}) has access"), instructions)
        self.assertTrue(launcher.answer("tools/list", {})["tools"], "listed: they work once the person fixes it")
        self.assertEqual(self.calls, [], "initialize reads the endpoint record only, never the socket")

    def test_a_record_of_a_dead_process_says_nothing_and_this_build_s_says_nothing(self):
        record = {"agent_protocol": None, "pid": 2 ** 22 + 12345}
        with patch("angelo_agent.engine_link_protocol.process_alive", return_value=False):
            self.assertEqual(external_build.record_mismatch(record), "")
        self.assertEqual(external_build.record_mismatch({"agent_protocol": external_build.AGENT_PROTOCOL,
                                                         "pid": os.getpid()}), "")

class BundleVersionTests(unittest.TestCase):
    def test_a_process_is_named_by_its_app_bundle_s_short_version(self):
        root = Path(tempfile.mkdtemp(prefix="smis-app-"))
        self.addCleanup(shutil.rmtree, root, True)
        contents = root / "Angelo.app" / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "2.0.0"}))
        self.assertEqual(external_build.bundle_version(contents / "MacOS" / "Angelo"), "2.0.0")
        self.assertIsNone(external_build.bundle_version(root / "elsewhere" / "python3"))
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "${VERSION}"}))
        self.assertIsNone(external_build.bundle_version(contents / "MacOS" / "Angelo"))
        self.assertEqual(external_build.holder_version({"angelo_version": "2.0.1", "pid": 1}), "2.0.1")

    @unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"),
                         environment_skip("reads the executable through libproc or /proc"))
    def test_this_process_s_executable_is_found(self):
        self.assertTrue(external_build.executable(os.getpid()))


if __name__ == "__main__":
    unittest.main()
