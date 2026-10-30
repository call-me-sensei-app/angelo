# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine live link's wire format, proofs, endpoint record and loopback server, without Blender
(doc/angelo/engine_link.rst).

Covered: frames round-trip whole and byte by byte, oversized or malformed ones are refused; the two proofs differ and
need the token; the record is owner-only, never followed through a link, replaced when its process is gone and never
over a live one; the server accepts only a client that proves the token, answers ping, refuses payloads and unknown
requests, delivers requests and disconnections in order, keeps frame order while a newer scene replaces an unsent
older one, and closes every client when it stops; it keeps, bounded, the engine an editor names, that engine's
version and what the editor can show.
"""

import json
import os
import socket
import stat
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import engine_link_protocol as protocol, engine_link_server  # noqa: E402

TOKEN = bytes(range(32))


class FrameTests(unittest.TestCase):
    def test_round_trip_whole_and_byte_by_byte(self):
        data = protocol.encode({"type": "scene", "asset": "res://a.glb"}, b"glTF" + bytes(300))
        for chunks in ([data], [data[index:index + 1] for index in range(len(data))]):
            reader = protocol.Reader()
            frames = []
            for chunk in chunks:
                reader.feed(chunk)
                frames += reader.frames()
            self.assertEqual(len(frames), 1)
            header, payload = frames[0]
            self.assertEqual((header["type"], header["bytes"], payload[:4]), ("scene", 304, b"glTF"))

    def test_bad_frames_are_refused(self):
        for data in (struct.pack(">I", 0), struct.pack(">I", protocol.MAX_HEADER + 1),
                     struct.pack(">I", 2) + b"[]", struct.pack(">I", 3) + b"{x}",
                     self._frame({"type": "x", "bytes": -1}),
                     self._frame({"type": "x", "bytes": protocol.MAX_PAYLOAD + 1}),
                     self._frame({"type": "x", "bytes": 1.5}), self._frame({"bytes": 0})):
            with self.subTest(data=data[:20]):
                reader = protocol.Reader()
                reader.feed(data)
                with self.assertRaises(protocol.FrameError):
                    reader.frames()
        with self.assertRaises(protocol.FrameError):
            protocol.encode({"type": "x", "value": float("nan")})
        with self.assertRaises(protocol.FrameError):
            protocol.encode({"type": "x", "text": "a" * protocol.MAX_HEADER})

    @staticmethod
    def _frame(header):
        body = json.dumps(header).encode()
        return struct.pack(">I", len(body)) + body

    def test_proofs_need_the_token_and_differ_by_side(self):
        server_nonce, client_nonce = protocol.new_nonce(), protocol.new_nonce()
        client = protocol.client_proof(TOKEN, server_nonce, client_nonce)
        self.assertNotEqual(client, protocol.server_proof(TOKEN, server_nonce, client_nonce))
        self.assertNotEqual(client, protocol.client_proof(bytes(32), server_nonce, client_nonce))
        self.assertTrue(protocol.proof_matches(client, client))
        self.assertFalse(protocol.proof_matches(client, client.upper()))
        self.assertFalse(protocol.valid_nonce(server_nonce.upper()))
        with self.assertRaises(ValueError):
            protocol.client_proof(b"short", server_nonce, client_nonce)


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.path = self.folder / "nested" / protocol.RECORD_FILE
        self.record = {"protocol": protocol.PROTOCOL, "port": 4242, "pid": os.getpid(), "token": TOKEN.hex()}

    def test_written_owner_only_and_read_back(self):
        protocol.write_record(self.path, self.record)
        self.assertEqual(protocol.read_record(self.path), self.record)
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertTrue(protocol.remove_record(self.path))
        self.assertFalse(self.path.exists())

    def test_a_live_owner_is_never_replaced_and_a_dead_one_is(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        protocol.write_record(self.path, dict(self.record, pid=os.getppid(), port=port))
        with self.assertRaises(FileExistsError):
            protocol.write_record(self.path, self.record)
        self.assertFalse(protocol.remove_record(self.path), "another process's record stays")
        os.unlink(self.path)
        protocol.write_record(self.path, dict(self.record, pid=self._dead_pid()))
        protocol.write_record(self.path, self.record)
        self.assertEqual(protocol.read_record(self.path)["pid"], os.getpid())
        os.unlink(self.path)
        listener.close()   # A crash left the record; its process ID now belongs to another program: no listener.
        protocol.write_record(self.path, dict(self.record, pid=os.getppid(), port=port))
        protocol.write_record(self.path, self.record)
        self.assertEqual(protocol.read_record(self.path)["pid"], os.getpid())

    @staticmethod
    def _dead_pid():
        pid = 99999
        while protocol.process_alive(pid):
            pid -= 1
        return pid

    @unittest.skipIf(sys.platform == "win32", "file modes and symbolic links")
    def test_unsafe_records_are_ignored(self):
        self.path.parent.mkdir(parents=True)
        target = self.folder / "elsewhere.json"
        target.write_text(json.dumps(self.record))
        os.chmod(target, 0o600)
        os.symlink(target, self.path)
        self.assertIsNone(protocol.read_record(self.path), "a link is never followed")
        os.unlink(self.path)
        self.path.write_text(json.dumps(self.record))
        os.chmod(self.path, 0o644)
        self.assertIsNone(protocol.read_record(self.path), "readable by others")
        os.chmod(self.path, 0o600)
        for broken in (dict(self.record, token="zz" * 32), dict(self.record, port=0), dict(self.record, protocol="x")):
            self.path.write_text(json.dumps(broken))
            self.assertIsNone(protocol.read_record(self.path))

    def test_record_path_override_and_default(self):
        self.assertEqual(protocol.record_path({"ANGELO_LINK_RECORD": "/tmp/r.json"}), Path("/tmp/r.json"))
        default = protocol.record_path({})
        self.assertEqual(default.name, protocol.RECORD_FILE)
        self.assertEqual(default.parent.name, "Angelo")


class Client:
    """A minimal client speaking the plugin's side of the protocol."""

    def __init__(self, port, token=TOKEN, info=None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.reader = protocol.Reader()
        self.token, self.info = token, info or {"project": "/p", "godot": "4.7"}

    def next(self, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            frames = self.reader.frames()
            if frames:
                self.pending = frames[1:]
                return frames[0]
            self.sock.settimeout(max(0.01, deadline - time.monotonic()))
            data = self.sock.recv(65536)
            if not data:
                return None
            self.reader.feed(data)

    def send(self, header, payload=b""):
        self.sock.sendall(protocol.encode(header, payload))

    def handshake(self):
        hello, _ = self.next()
        nonce = protocol.new_nonce()
        self.send({"type": "auth", "nonce": nonce, "proof": protocol.client_proof(self.token, hello["nonce"], nonce),
                   "client": self.info})
        welcome, _ = self.next()
        return hello, nonce, welcome


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = engine_link_server.LinkServer(TOKEN, {"name": "Angelo"})
        self.addCleanup(self.server.close)

    def connect(self, **options):
        client = Client(self.server.port, **options)
        self.addCleanup(client.sock.close)
        return client

    def wait_for(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.01)
        self.fail("timed out")

    def test_mutual_proof_then_requests_reach_the_main_thread(self):
        client = self.connect()
        hello, nonce, welcome = client.handshake()
        self.assertEqual((hello["type"], hello["protocol"], hello["app"]["name"]), ("hello", protocol.PROTOCOL, "Angelo"))
        self.assertEqual(welcome["type"], "welcome")
        self.assertEqual(welcome["proof"], protocol.server_proof(TOKEN, hello["nonce"], nonce))
        clients = self.wait_for(self.server.clients)
        self.assertEqual(list(clients.values())[0]["project"], "/p")
        self.assertEqual(self.wait_for(self.server.drain)[0][1], {"type": "connected"}, "the indicators hear of it")
        client.send({"type": "ping"})
        self.assertEqual(client.next()[0]["type"], "pong")
        client.send({"type": "open", "id": "1", "asset": "res://a.glb"})
        items = self.wait_for(self.server.drain)
        self.assertEqual(items[0][1]["type"], "open")
        client.sock.close()
        left = self.wait_for(self.server.drain)
        self.assertEqual(left[0][1], {"type": "disconnected"})

    def test_a_wrong_proof_is_refused_and_nothing_is_delivered(self):
        client = self.connect(token=bytes(32))
        _hello, _nonce, answer = client.handshake()
        self.assertEqual((answer["type"], answer["code"]), ("error", "auth"))
        self.assertIsNone(client.next())
        self.assertEqual(self.server.drain(), [])
        self.assertEqual(self.server.clients(), {})

    def test_a_silent_connection_is_dropped(self):
        original = engine_link_server.AUTH_SECONDS
        engine_link_server.AUTH_SECONDS = 0.3
        self.addCleanup(setattr, engine_link_server, "AUTH_SECONDS", original)
        client = self.connect()
        client.next()   # hello
        self.assertIsNone(client.next(timeout=3))

    def test_an_announced_payload_ends_the_connection_before_any_of_it_is_read(self):
        client = self.connect()
        client.next()   # hello
        body = json.dumps({"type": "auth", "bytes": protocol.MAX_PAYLOAD}).encode()
        client.sock.sendall(struct.pack(">I", len(body)) + body + b"x" * 1024)
        self.assertIsNone(client.next(), "refused at the header, never buffered")

    def test_silent_connections_cant_keep_an_editor_out(self):
        silent = [self.connect() for _ in range(engine_link_server.MAX_PENDING + 2)]
        for item in silent:
            item.next()   # hello, then nothing
        editor = self.connect()
        _hello, _nonce, welcome = editor.handshake()
        self.assertEqual(welcome["type"], "welcome")
        self.assertIsNone(silent[0].next(timeout=2), "the oldest waiting connection made way")

    def test_close_lets_a_queued_frame_leave_first(self):
        client = self.connect()
        client.handshake()
        ident = self.wait_for(lambda: list(self.server.clients()))[0]
        self.server.send(ident, {"type": "ended", "reason": "stopped"})
        self.server.close()
        frames = [client.next()]
        self.assertEqual(frames[0][0]["type"], "ended")

    def test_clients_send_only_small_known_requests(self):
        for header, payload in (({"type": "scene"}, b""), ({"type": "open"}, b"x")):
            with self.subTest(header=header):
                client = self.connect()
                client.handshake()
                client.send(header, payload)
                self.assertIsNone(client.next())
        self.assertEqual([item for item in self.server.drain()
                          if item[1]["type"] not in {"connected", "disconnected"}], [])

    def test_order_is_kept_and_only_the_newest_scene_waits(self):
        client = engine_link_server._Client(99, None, ("127.0.0.1", 0))
        client.authenticated = True
        self.server._clients[99] = client   # No writer thread: the queue is what it would send, in order.
        self.server.send(99, {"type": "scene", "version": 1}, b"a")
        self.server.send(99, {"type": "saved"})
        self.server.send(99, {"type": "scene", "version": 2}, b"b")
        self.server.send(99, {"type": "scene", "version": 3}, b"c")
        self.assertEqual([(header["type"], header.get("version")) for header, _ in client.queue],
                         [("saved", None), ("scene", 3)])
        del self.server._clients[99]

    def test_an_editor_that_leaves_right_after_a_request_is_still_known_for_it(self):
        client = self.connect(info={"engine": "unreal", "unreal": "5.8.2", "project": "/p"})
        client.handshake()
        client.send({"type": "close", "id": "1", "asset": "/Game/A"})
        client.sock.close()   # The editor quits as soon as it has sent its close.
        items = []
        self.wait_for(lambda: items.extend(self.server.drain()) or any(h["type"] == "disconnected" for _c, h in items))
        ident = [c for c, h in items if h["type"] == "close"][0]
        self.assertEqual(self.server.info_of(ident)["engine"], "unreal", "its last requests are read as its own")
        self.server.drain()
        self.assertEqual(self.server.info_of(ident), {}, "forgotten once that batch is handled")

    def test_broadcast_filters_and_close_ends_every_client(self):
        first, second = self.connect(info={"project": "/a"}), self.connect(info={"project": "/b"})
        first.handshake()
        second.handshake()
        self.wait_for(lambda: len(self.server.clients()) == 2)
        sent = self.server.broadcast({"type": "scene", "asset": "res://x.glb"}, b"glb",
                                     only=lambda info: info.get("project") == "/a")
        self.assertEqual(sent, 1)
        header, payload = first.next()
        self.assertEqual((header["type"], payload), ("scene", b"glb"))
        self.server.close()
        self.assertIsNone(first.next())
        self.assertIsNone(second.next())


class ClientInfoTests(unittest.TestCase):
    def test_the_engine_its_version_and_capabilities_are_kept(self):
        info = engine_link_server.client_info({"engine": "unreal", "unreal": "5.8.2", "project": "/p",
                                               "capabilities": ["durable"], "token": "never kept"})
        self.assertEqual(info, {"engine": "unreal", "version": "5.8.2", "project": "/p", "capabilities": ["durable"]})

    def test_an_editor_that_names_no_engine_is_godot_and_says_nothing_it_shows(self):
        info = engine_link_server.client_info({"godot": "4.7.2", "project": "/p"})
        self.assertEqual(info, {"engine": "godot", "version": "4.7.2", "project": "/p"})
        self.assertNotIn("capabilities", info, "absent: the engine's adapter decides")

    def test_everything_is_bounded(self):
        info = engine_link_server.client_info({"engine": "x" * 99, "capabilities": ["y" * 99] * 50,
                                               "project": "p" * 999})
        self.assertEqual((len(info["engine"]), len(info["project"])), (40, 400))
        self.assertEqual(len(info["capabilities"]), engine_link_server.MAX_CAPABILITIES)
        self.assertEqual({len(item) for item in info["capabilities"]}, {32})
        self.assertEqual(engine_link_server.client_info("not a dict"), {"engine": "godot"})
        self.assertNotIn("capabilities", engine_link_server.client_info({"capabilities": "live"}))


if __name__ == "__main__":
    unittest.main()
