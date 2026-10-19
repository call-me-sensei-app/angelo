# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local CLI transport: framing, token challenge, peer checks, limits and endpoint safety.

Real AF_UNIX sockets in fresh private directories under /tmp (short enough for
macOS sun_path), so these run unchanged on Linux CI and macOS. Never the real
``$TMPDIR/angelo-<uid>`` runtime directory or a real Angelo profile.
"""

import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import cli_client, external_cli
from angelo_agent import external_cli_protocol as protocol
from angelo_agent.external_cli import LocalCliServer, Peer, TransportUnavailable

# The Unix-socket transport and its POSIX private files; Windows refuses to listen (WindowsRefusalTests).
UNIX_TRANSPORT = unittest.skipIf(sys.platform == "win32", "Windows has no CLI transport yet: the server refuses to "
                                 "listen there (doc/angelo/external_cli.rst)")


def echo_handler(calls):
    def handle(request, client):
        calls.append((request, client))
        return protocol.envelope("ok", "echo", result={"command": request.get("command"), "label": client.label})
    return handle


class FramingAndProofTests(unittest.TestCase):
    def test_frames_round_trip_and_reject_bad_payloads(self):
        left, right = socket.socketpair()
        with left, right:
            protocol.send_frame(left, {"type": "x", "text": "é"}, 1024)
            self.assertEqual(protocol.recv_frame(right, 1024), {"type": "x", "text": "é"})
            for header, payload in ((0, b""), (2048, b"")):
                left.sendall(struct.pack(">I", header) + payload)
                with self.assertRaises(protocol.FrameError):
                    protocol.recv_frame(right, 1024, timeout=0.5)
        with self.assertRaises(protocol.FrameError):
            protocol.encode_frame({"blob": "x" * 2048}, 1024)
        with self.assertRaises(protocol.FrameError):
            protocol.encode_frame({"value": float("nan")}, 1024)
        for payload in (b"[1]", b"{\"a\": NaN}", b"\xff\xfe"):
            left, right = socket.socketpair()
            with left, right:
                left.sendall(struct.pack(">I", len(payload)) + payload)
                with self.assertRaises(protocol.FrameError):
                    protocol.recv_frame(right, 1024, timeout=0.5)

    def test_slow_sender_hits_an_absolute_frame_deadline(self):
        left, right = socket.socketpair()
        with left, right:
            left.sendall(struct.pack(">I", 10) + b"{")
            started = time.monotonic()
            with self.assertRaises(protocol.FrameError):
                protocol.recv_frame(right, 1024, timeout=0.3)
            self.assertLess(time.monotonic() - started, 2)

    def test_proofs_are_directional_and_token_bound(self):
        token, other = os.urandom(32), os.urandom(32)
        server, client = "a" * 64, "b" * 64
        self.assertNotEqual(protocol.client_proof(token, server, client), protocol.server_proof(token, server, client))
        self.assertNotEqual(protocol.client_proof(token, server, client), protocol.client_proof(other, server, client))
        self.assertTrue(protocol.proof_matches(protocol.client_proof(token, server, client),
                                               protocol.client_proof(token, server, client)))
        self.assertFalse(protocol.proof_matches(protocol.client_proof(token, server, client), None))

    def test_text_escaping_neutralizes_ansi_bidi_and_forged_lines(self):
        escaped = protocol.escape_text("Cube\x1b[31m‮evil\nfake: ok")
        self.assertNotIn("\x1b", escaped)
        self.assertNotIn("‮", escaped)
        self.assertNotIn("\n", escaped)
        self.assertIn("\\u202e", escaped)

    def test_envelope_always_has_every_field_and_the_documented_exit_table(self):
        self.assertEqual(protocol.EXIT_CODES, {"ok": 0, "tool_failed": 1, "invalid": 2, "not_applied": 3, "refused": 4,
                                               "stale": 5, "pending": 6, "uncertain": 7, "unavailable": 8,
                                               "incompatible": 9, "busy": 10})
        for code in protocol.EXIT_CODES:
            value = protocol.envelope(code, "m")
            self.assertEqual(set(value), {"cli_protocol", "ok", "code", "exit", "retry", "toolset_version", "document",
                                          "conversation", "call", "result", "message"})
            self.assertEqual(value["exit"], protocol.EXIT_CODES[code])
            self.assertIn(value["retry"], {"never", "poll", "after_observe", "same_id_later"})
        self.assertEqual(protocol.envelope("pending", "m")["retry"], "poll")
        self.assertEqual(protocol.envelope("stale", "m")["retry"], "after_observe")


@UNIX_TRANSPORT
class PrivatePathTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="scli-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_default_runtime_directories(self):
        self.assertEqual(external_cli.default_runtime_directory({"TMPDIR": "/var/x/T/"}, "darwin", 501),
                         Path("/var/x/T/angelo-501"))
        self.assertEqual(external_cli.default_runtime_directory({"XDG_RUNTIME_DIR": "/run/user/7"}, "linux", 7),
                         Path("/run/user/7/angelo"))
        self.assertEqual(external_cli.default_runtime_directory({}, "linux", 7), Path("/tmp/angelo-7"))
        self.assertEqual(external_cli.default_runtime_directory({"ANGELO_CLI_RUNTIME_DIR": "/tmp/q"}, "darwin", 1),
                         Path("/tmp/q"))
        with self.assertRaises(TransportUnavailable):
            external_cli.default_runtime_directory({"ANGELO_CLI_RUNTIME_DIR": "relative"}, "linux", 1)

    def test_private_directory_rejects_wide_modes_and_links(self):
        created = external_cli.create_private_directory(self.root / "runtime")
        self.assertEqual(os.stat(created).st_mode & 0o777, 0o700)
        wide = self.root / "wide"
        wide.mkdir(mode=0o700)
        os.chmod(wide, 0o755)
        with self.assertRaises(TransportUnavailable):
            external_cli.create_private_directory(wide)
        link = self.root / "link"
        link.symlink_to(created)
        with self.assertRaises(TransportUnavailable):
            external_cli.create_private_directory(link)

    def test_endpoint_is_exclusive_owner_only_and_never_read_through_links(self):
        path = self.root / "endpoint.json"
        record = {"cli_protocol": protocol.CLI_PROTOCOL, "socket": "/tmp/x.sock", "pid": os.getpid(), "instance": "a" * 32, "token": "b" * 64}
        external_cli.write_endpoint(path, record)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            external_cli.write_endpoint(path, record)
        self.assertEqual(protocol.read_endpoint(path)["token"], "b" * 64)
        link = self.root / "link.json"
        link.symlink_to(path)
        with self.assertRaises(protocol.UnsafePath):
            protocol.read_endpoint(link)
        os.chmod(path, 0o644)
        with self.assertRaises(protocol.UnsafePath):
            protocol.read_endpoint(path)


@UNIX_TRANSPORT
class ServerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="scli-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state, self.runtime = self.root / "state", self.root / "run"
        self.calls = []

    def server(self, **options):
        server = LocalCliServer(self.state, options.pop("handler", echo_handler(self.calls)),
                                runtime_directory=self.runtime, **options)
        server.start()
        self.addCleanup(server.stop)
        return server

    def run_client(self, command="status", label="tester"):
        return cli_client.run({"command": command, "args": {}}, self.state, label, 5)

    def test_authenticated_request_round_trip_with_private_files(self):
        server = self.server()
        envelope = self.run_client()
        self.assertEqual((envelope["code"], envelope["result"]), ("ok", {"command": "status", "label": "tester"}))
        self.assertEqual(os.stat(self.runtime).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.state / "cli").st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(server.endpoint_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(server.socket_path).st_mode & 0o777, 0o600)
        self.assertEqual(server._listener.family, socket.AF_UNIX)
        record = json.loads(server.endpoint_path.read_text())
        self.assertEqual(record["pid"], os.getpid())
        self.assertEqual(self.calls[0][1].peer.uid if self.calls[0][1].peer.checked else os.geteuid(), os.geteuid())

    def test_wrong_or_previous_launch_token_is_refused_before_any_request(self):
        server = self.server()
        record = json.loads(server.endpoint_path.read_text())
        forged = self.root / "forged"
        (forged / "cli").mkdir(parents=True, mode=0o700)
        external_cli.write_endpoint(forged / "cli" / "endpoint.json", dict(record, token="0" * 64))
        envelope = cli_client.run({"command": "status", "args": {}}, forged, "tester", 5)
        self.assertEqual(envelope["code"], "unavailable")
        self.assertEqual(self.calls, [])
        self.assertEqual(server.counters["failed_authentication"], 1)

    def test_client_refuses_a_listener_that_cannot_prove_the_token(self):
        server = self.server()
        record = json.loads(server.endpoint_path.read_text())
        server.stop()
        squatter = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        squatter.bind(record["socket"])
        squatter.listen(1)
        self.addCleanup(squatter.close)

        def impostor():
            connection, _ = squatter.accept()
            with connection:
                protocol.send_frame(connection, {"type": "challenge", "cli_protocol": protocol.CLI_PROTOCOL, "server_nonce": "c" * 64}, 4096)
                hello = protocol.recv_frame(connection, 16384)
                protocol.send_frame(connection, {"type": "welcome", "server_proof": "d" * 64}, 4096)
                self.assertNotIn("token", hello)  # The token itself never crosses the socket.
        thread = threading.Thread(target=impostor)
        thread.start()
        state = self.root / "copy"
        (state / "cli").mkdir(parents=True, mode=0o700)
        external_cli.write_endpoint(state / "cli" / "endpoint.json", record)
        envelope = cli_client.run({"command": "status", "args": {}}, state, "tester", 5)
        thread.join(5)
        self.assertEqual(envelope["code"], "unavailable")
        self.assertIn("did not prove", envelope["message"])

    def test_peer_uid_mismatch_or_unreadable_credentials_close_before_any_frame(self):
        for lookup in (lambda sock: Peer(os.geteuid() + 1, 4242, True), lambda sock: (_ for _ in ()).throw(OSError())):
            with self.subTest(lookup=lookup):
                server = self.server(peer_lookup=lookup)
                envelope = self.run_client()
                self.assertEqual(envelope["code"], "unavailable")
                self.assertEqual(self.calls, [])
                self.assertGreaterEqual(server.counters["refused_peer"], 1)
                server.stop()

    def test_real_peer_credentials_report_this_user(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        with left, right:
            peer = external_cli.peer_credentials(left)
        if peer.checked:
            self.assertEqual(peer.uid, os.geteuid())
        else:
            self.assertIsNone(peer.uid)  # Documented gap: directory permissions only.

    def handshake(self, server, label="tester"):
        record = protocol.read_endpoint(server.endpoint_path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(record["socket"])
        challenge = protocol.recv_frame(sock, 4096)
        nonce = "e" * 64
        proof = protocol.client_proof(bytes.fromhex(record["token"]), challenge["server_nonce"], nonce)
        protocol.send_frame(sock, {"type": "hello", "cli_protocol": protocol.CLI_PROTOCOL, "client": label, "client_nonce": nonce,
                                   "proof": proof}, 16384)
        return sock, protocol.recv_frame(sock, 65536)

    def test_oversized_request_frame_closes_without_reaching_the_handler(self):
        server = self.server()
        sock, welcome = self.handshake(server)
        with sock:
            self.assertEqual(welcome["type"], "welcome")
            try:
                sock.sendall(struct.pack(">I", protocol.MAX_REQUEST_BYTES + 1) + b"{" * 1024)
                self.assertEqual(sock.recv(16), b"")
            except (BrokenPipeError, ConnectionResetError):
                pass
        self.assertEqual(self.calls, [])

    def test_protocol_and_label_mismatches_are_explicit(self):
        server = self.server()
        sock, response = self.handshake(server, label="bad label!")
        with sock:
            self.assertEqual(response["envelope"]["code"], "invalid")
        record = protocol.read_endpoint(server.endpoint_path)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(record["socket"])
            protocol.recv_frame(sock, 4096)
            protocol.send_frame(sock, {"type": "hello", "cli_protocol": 99}, 4096)
            self.assertEqual(protocol.recv_frame(sock, 65536)["envelope"]["code"], "incompatible")

    def test_connection_limit_and_slow_hello_release_their_slots(self):
        server = self.server(max_connections=1, frame_timeout=0.4)
        record = protocol.read_endpoint(server.endpoint_path)
        holder = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        holder.connect(record["socket"])
        protocol.recv_frame(holder, 4096)  # Hold the only slot by never sending hello.
        envelope = self.run_client()
        self.assertEqual(envelope["code"], "busy")
        self.assertEqual(server.counters["refused_capacity"], 1)
        deadline = time.monotonic() + 10  # The slow hello times out (0.4 s) and frees its slot.
        while self.run_client()["code"] != "ok":
            self.assertLess(time.monotonic(), deadline, "the timed-out connection never released its slot")
            time.sleep(0.1)
        holder.close()

    def test_stale_records_are_replaced_but_a_live_owner_is_respected(self):
        server = self.server()
        socket_path, endpoint = server.socket_path, server.endpoint_path
        record = protocol.read_endpoint(endpoint)
        server.stop()
        self.assertFalse(endpoint.exists())
        self.assertFalse(socket_path.exists())
        # A crashed instance leaves both behind; its PID is dead.
        leftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        leftover.bind(str(socket_path))
        leftover.close()
        external_cli.write_endpoint(endpoint, dict(record, pid=2 ** 22 + 12345))
        with patch.object(external_cli, "process_alive", return_value=False):
            restarted = self.server()
        self.assertEqual(self.run_client()["code"], "ok")
        restarted.stop()
        external_cli.write_endpoint(endpoint, dict(record, pid=os.getppid()))
        with self.assertRaises(TransportUnavailable):
            self.server()
        endpoint.unlink()
        endpoint.symlink_to(self.root / "elsewhere.json")
        with self.assertRaises(TransportUnavailable):
            self.server()

    def test_exit_hook_removes_only_files_of_this_instance(self):
        server = self.server()
        endpoint, socket_path = server.endpoint_path, server.socket_path
        server._remove_files()  # What the atexit hook does when Angelo quits with access on.
        self.assertFalse(endpoint.exists() or socket_path.exists())
        external_cli.write_endpoint(endpoint, {"cli_protocol": protocol.CLI_PROTOCOL, "socket": str(socket_path), "pid": 1,
                                               "instance": "other", "token": "a" * 64})
        server._remove_files()
        self.assertTrue(endpoint.exists())  # Another instance's record is never removed.

    def test_stop_withdraws_access(self):
        server = self.server()
        self.assertEqual(self.run_client()["code"], "ok")
        server.stop()
        self.assertFalse(server.listening)
        self.assertEqual(self.run_client()["code"], "unavailable")

    def test_long_socket_paths_and_windows_refuse_to_listen(self):
        deep = self.root / ("d" * 90)
        with self.assertRaises(TransportUnavailable):
            LocalCliServer(self.state, echo_handler([]), runtime_directory=deep).start()
        with patch.object(external_cli.sys, "platform", "win32"):
            with self.assertRaises(TransportUnavailable):
                LocalCliServer(self.state, echo_handler([]), runtime_directory=self.runtime).start()

    def test_transport_source_has_no_network_listener(self):
        source = Path(external_cli.__file__).read_text(encoding="utf-8")
        for forbidden in ("AF_INET", "AF_INET6", "0.0.0.0", "127.0.0.1", "localhost"):
            self.assertNotIn(forbidden, source)


class WindowsRefusalTests(unittest.TestCase):
    """On every host: Windows (real there, simulated elsewhere) refuses before creating anything."""

    def test_windows_refuses_to_listen_before_any_effect(self):
        root = Path(tempfile.mkdtemp(prefix="scli-"))
        self.addCleanup(shutil.rmtree, root, True)
        with patch.object(external_cli.sys, "platform", "win32"):
            with self.assertRaisesRegex(TransportUnavailable, "not implemented on this platform"):
                LocalCliServer(root / "state", echo_handler([]), runtime_directory=root / "run").start()
        self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
