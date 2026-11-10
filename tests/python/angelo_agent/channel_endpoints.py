# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker channel endpoint over one end of ``socket.socketpair()``, as each platform's workers have it.

macOS and Linux: the Unix socket that can pass descriptors (``worker_channel.socket_endpoint``, which needs
``recvmsg``). Windows sockets have no ``recvmsg``; its workers talk over two named pipes (``connect_pipe``), plain
byte streams, so there the same ``Endpoint`` runs over the socket's streams and the client's and reader's logic is
tested on every platform.
"""

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import worker_channel  # noqa: E402


def endpoint(sock, limit=1 << 20):
    if hasattr(socket.socket, "recvmsg"):
        return worker_channel.socket_endpoint(sock, limit, limit)

    def shut():   # Unblocks the reader thread before the close, as socket_endpoint's does.
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    return worker_channel.Endpoint(sock.makefile("rb", buffering=0), sock.makefile("wb", buffering=0), limit, limit,
                                   closers=(shut, sock.close))
