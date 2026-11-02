# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fakes shared by the ``unsent_loss`` suites (``test_native_runtime_unsent_loss*``): no tests here."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import protocol  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess, RpcError  # noqa: E402


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def ended_process():
    """A real ``JsonRpcProcess`` whose process has exited and whose reader saw it: the dead runtime."""
    rpc = JsonRpcProcess([sys.executable, "-c", "pass"])
    assert wait_for(rpc.closed.is_set), "the stand-in process did not end"
    return rpc


class ClosingProcess:
    """A shared app-server's process (``Runtime.rpc``): answers until it ends, then refuses every write."""

    def __init__(self, on_event, on_request):
        self.on_event, self.closed, self.stderr_tail, self.methods = on_event, threading.Event(), [], []

    def request(self, method, params=None, timeout=60):
        self.write({"method": method})
        return {"account": {"type": "chatgpt"}} if method == "account/read" else {"userAgent": "fake/1.0"}

    def write(self, message):
        if self.closed.is_set():
            raise getattr(protocol, "TransportClosed", RpcError)("Agent process has disconnected")
        self.methods.append(message.get("method"))

    def notify(self, method, params=None):
        self.write({"method": method})

    def respond(self, request_id, result):
        self.write({"id": request_id})

    def close(self):
        self.closed.set()
