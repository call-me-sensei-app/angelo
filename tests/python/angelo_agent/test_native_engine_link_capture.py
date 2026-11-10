# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one payload a link client may send: a PNG in reply to a capture Angelo asked for (doc/angelo/engine_link.rst,
"Transport"; doc/angelo/web_link.rst). Real sockets, no Blender.

Covered: a client that said it can capture gets the request and its PNG comes back; a refusal comes back with its
message; nothing else can carry a payload: a payload before any request, after the request was answered or timed out,
under another id, with another code, from a client that never said it can capture, larger than the bound, or one
that isn't a PNG ends the connection (or is never accepted); one capture per client at a time; the reply is never
delivered to the main thread as a request.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[2] / "scripts/addons_core"))
from angelo_agent import engine_link_protocol as protocol, engine_link_server  # noqa: E402
from test_native_engine_link_protocol import TOKEN, Client  # noqa: E402

PNG = engine_link_server.PNG + b"\x00" * 64
CAPTURE_INFO = {"engine": "web", "project": "/p", "capabilities": ["durable", "live", "acks", "capture"]}


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.server = engine_link_server.LinkServer(TOKEN, {"name": "Angelo"})
        self.addCleanup(self.server.close)

    def connect(self, info=CAPTURE_INFO):
        client = Client(self.server.port, info=dict(info))
        self.addCleanup(client.sock.close)
        client.handshake()
        clients = self.wait_for(self.server.clients)
        return client, next(iter(clients))

    def wait_for(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.01)
        self.fail("timed out")

    def ask(self, ident, timeout=3):
        """Run ``server.capture`` on a thread, as the main thread would; returns a holder filled when it returns."""
        holder = {}
        thread = threading.Thread(target=lambda: holder.update(
            result=self.server.capture(ident, {"asset": "public/a.glb", "max": 1280}, timeout)))
        thread.start()
        self.addCleanup(thread.join, 10)
        return holder, thread

    def test_a_requested_png_comes_back(self):
        client, ident = self.connect()
        holder, thread = self.ask(ident)
        request, _ = client.next()
        self.assertEqual((request["type"], request["asset"], request["max"]), ("capture", "public/a.glb", 1280))
        client.send({"type": "status", "asset": "public/a.glb", "code": "captured", "id": request["id"]}, PNG)
        thread.join(5)
        png, report = holder["result"]
        self.assertEqual(png, PNG)
        self.assertEqual(report["code"], "captured")
        self.assertFalse([header for _id, header in self.server.drain() if header.get("type") == "status"],
                         "a capture reply is never a request for the main thread")
        client.send({"type": "ping"})
        self.assertEqual(client.next()[0]["type"], "pong", "the connection goes on")

    def test_a_refusal_comes_back_with_its_message(self):
        client, ident = self.connect()
        holder, thread = self.ask(ident)
        request, _ = client.next()
        client.send({"type": "status", "code": "capture_failed", "id": request["id"],
                     "message": "capture is off on this page"})
        thread.join(5)
        self.assertEqual(holder["result"], (None, {"code": "capture_failed",
                                                    "message": "capture is off on this page"}))

    def test_no_answer_in_time_is_none_and_a_late_payload_ends_the_connection(self):
        client, ident = self.connect()
        holder, thread = self.ask(ident, timeout=0.3)
        request, _ = client.next()
        thread.join(5)
        self.assertEqual(holder["result"], (None, None))
        client.send({"type": "status", "code": "captured", "id": request["id"]}, PNG)
        self.assertIsNone(client.next(), "no capture is outstanding: any payload ends the connection")

    def test_a_payload_nobody_asked_for_ends_the_connection(self):
        client, _ident = self.connect()
        client.send({"type": "status", "code": "captured", "id": "0123456789abcdef"}, PNG)
        self.assertIsNone(client.next())

    def test_only_the_outstanding_id_and_the_captured_code_may_carry_one(self):
        for header in ({"type": "status", "code": "captured", "id": "another"},
                       {"type": "status", "code": "previewed", "id": None},
                       {"type": "open", "id": None}):
            with self.subTest(header=header):
                client, ident = self.connect()
                self.ask(ident)
                request, _ = client.next()
                if header["id"] is None:
                    header = dict(header, id=request["id"])
                client.send(header, PNG)
                self.assertIsNone(client.next())

    def test_more_than_the_bound_ends_the_connection_before_any_of_it_is_read(self):
        client, ident = self.connect()
        self.ask(ident)
        request, _ = client.next()
        body = json.dumps({"type": "status", "code": "captured", "id": request["id"],
                           "bytes": engine_link_server.MAX_CAPTURE + 1}).encode()
        client.sock.sendall(struct.pack(">I", len(body)) + body + b"x" * 1024)
        self.assertIsNone(client.next())

    def test_anything_but_a_png_ends_the_connection(self):
        client, ident = self.connect()
        self.ask(ident)
        request, _ = client.next()
        client.send({"type": "status", "code": "captured", "id": request["id"]}, b"GIF89a" + b"\x00" * 64)
        self.assertIsNone(client.next())

    def test_a_client_that_cant_capture_is_never_asked(self):
        _client, ident = self.connect({"engine": "godot", "project": "/p", "capabilities": ["durable", "live"]})
        self.assertEqual(self.server.capture(ident, {"asset": "a"}, 0.2), (None, None))

    def test_one_capture_at_a_time(self):
        client, ident = self.connect()
        _holder, _thread = self.ask(ident)
        client.next()
        png, report = self.server.capture(ident, {"asset": "a"}, 0.2)
        self.assertIsNone(png)
        self.assertEqual(report["code"], "capture_failed")

    def test_a_reply_is_accepted_once(self):
        client, ident = self.connect()
        holder, thread = self.ask(ident)
        request, _ = client.next()
        reply = {"type": "status", "code": "captured", "id": request["id"]}
        client.send(reply, PNG)
        thread.join(5)
        self.assertEqual(holder["result"][0], PNG)
        client.send(reply, PNG)
        self.assertIsNone(client.next(), "the answered request allows no second payload")

    def test_two_answers_in_one_write_end_the_connection(self):
        """Break-it F1: the reader read the second answer's header before the first was handled, so both were
        allowed a payload."""
        for first_code in ("captured", "capture_failed"):
            with self.subTest(first=first_code):
                client, ident = self.connect()
                holder, thread = self.ask(ident)
                request, _ = client.next()
                first = protocol.encode({"type": "status", "code": first_code, "id": request["id"]},
                                        PNG if first_code == "captured" else b"")
                second = protocol.encode({"type": "status", "code": "captured", "id": request["id"]}, PNG)
                client.sock.sendall(first + second)
                thread.join(5)
                self.assertEqual(holder["result"][1]["code"], first_code, "the first answer counts")
                self.assertIsNone(client.next(), "the second payload ends the connection")

    def test_a_payload_still_arriving_when_the_wait_ends_ends_the_connection(self):
        """Break-it F2: a header announced in time let its payload be buffered after nobody waited for it."""
        client, ident = self.connect()
        holder, thread = self.ask(ident, timeout=0.5)
        request, _ = client.next()
        frame = protocol.encode({"type": "status", "code": "captured", "id": request["id"]}, PNG + b"\x00" * 4096)
        client.sock.sendall(frame[:len(frame) // 2])
        thread.join(5)
        self.assertEqual(holder["result"], (None, None))
        self.assertIsNone(client.next(timeout=3), "the half-sent capture's connection is closed")

    def test_the_handshake_still_takes_no_payload(self):
        client = Client(self.server.port, info=dict(CAPTURE_INFO))
        self.addCleanup(client.sock.close)
        hello, _ = client.next()
        nonce = protocol.new_nonce()
        client.send({"type": "auth", "nonce": nonce, "proof": protocol.client_proof(TOKEN, hello["nonce"], nonce),
                     "client": CAPTURE_INFO}, PNG)
        self.assertIsNone(client.next())
        self.assertEqual(self.server.clients(), {})


if __name__ == "__main__":
    unittest.main()
