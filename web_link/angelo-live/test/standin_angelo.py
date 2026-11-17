# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: MIT
"""A stand-in for Angelo's side of the web live link, for the package's contract and end-to-end tests.

It runs Angelo's REAL link server (``angelo_agent.engine_link_server.LinkServer``, imported from an Angelo checkout
given with ``--angelo``), writes the endpoint record to ``--record`` (what ``ANGELO_LINK_RECORD`` names), and is
driven by JSON lines on stdin; it reports JSON lines on stdout. Only the server is Angelo's: the replies (``pending``,
``opened``) are what Angelo's main thread would send, played here so no Blender is needed.

Commands (stdin): ``{"cmd": "send", "client": id or null (all), "header": {...}, "payload_file": path}``,
``{"cmd": "auto", "open": "accept" | "refuse" | "none"}``, ``{"cmd": "capture", "client", "header", "timeout",
"save"}``, ``{"cmd": "bench", ...}`` (latency: frames and their ``previewed`` acks timed here, next to the
server), ``{"cmd": "disconnect", "client"}``, ``{"cmd": "clients"}``, ``{"cmd": "stop"}``.
Events (stdout): ``ready``, ``frame`` (each item the server delivered), ``capture``, ``bench``, ``clients``.
"""

import argparse
import json
import os
import queue
import secrets
import sys
import threading
import time
import zlib


def emit(event, **fields):
    sys.stdout.write(json.dumps(dict(fields, event=event)) + "\n")
    sys.stdout.flush()


def png_pixels(data):
    """(width, height, rows of RGBA tuples) of an 8-bit RGB or RGBA PNG; enough to tell a picture from a blank."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    at, idat, width, height, color = 8, b"", 0, 0, 0
    while at < len(data):
        length = int.from_bytes(data[at:at + 4], "big")
        kind = data[at + 4:at + 8]
        body = data[at + 8:at + 8 + length]
        if kind == b"IHDR":
            width, height = int.from_bytes(body[0:4], "big"), int.from_bytes(body[4:8], "big")
            depth, color = body[8], body[9]
            if depth != 8 or color not in (2, 6):
                raise ValueError("only 8-bit RGB/RGBA")
        elif kind == b"IDAT":
            idat += body
        at += 12 + length
    channels = 4 if color == 6 else 3
    raw = zlib.decompress(idat)
    stride = width * channels
    rows, previous = [], bytearray(stride)
    for y in range(height):
        line = raw[y * (stride + 1):(y + 1) * (stride + 1)]
        kind, row = line[0], bytearray(line[1:])
        for x in range(stride):
            left = row[x - channels] if x >= channels else 0
            up = previous[x]
            corner = previous[x - channels] if x >= channels else 0
            if kind == 1:
                row[x] = (row[x] + left) & 255
            elif kind == 2:
                row[x] = (row[x] + up) & 255
            elif kind == 3:
                row[x] = (row[x] + ((left + up) >> 1)) & 255
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                row[x] = (row[x] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        rows.append(bytes(row))
        previous = row
    return width, height, channels, rows


def picture_facts(data):
    """Size and whether the picture shows something: how many distinct colours a grid of samples holds."""
    width, height, channels, rows = png_pixels(data)
    colours = set()
    for y in range(0, height, max(1, height // 40)):
        for x in range(0, width, max(1, width // 40)):
            colours.add(rows[y][x * channels:x * channels + 3])
    return {"width": width, "height": height, "colours": len(colours), "nonblank": len(colours) > 8}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--angelo", required=True, help="Angelo checkout (has scripts/addons_core/angelo_agent)")
    parser.add_argument("--record", required=True)
    args = parser.parse_args()
    sys.path.insert(0, os.path.join(args.angelo, "scripts", "addons_core"))
    from angelo_agent import engine_link_protocol as protocol, engine_link_server  # noqa: E402

    token = secrets.token_bytes(32)
    server = engine_link_server.LinkServer(token, {"name": "Angelo stand-in"})
    protocol.write_record(args.record, {"protocol": protocol.PROTOCOL, "port": server.port, "pid": os.getpid(),
                                        "token": token.hex(), "app": "", "binary": ""})
    commands = queue.Queue()

    def read_commands():
        for line in sys.stdin:
            if line.strip():
                commands.put(json.loads(line))
        commands.put({"cmd": "stop"})

    threading.Thread(target=read_commands, daemon=True).start()
    emit("ready", port=server.port, pid=os.getpid(), capture=hasattr(server, "capture"))
    auto = "accept"
    waiting = {}   # (client, kind, number) -> threading.Event, for bench
    acks = {}

    def bench(command):
        client, asset = command["client"], command["asset"]
        files = [open(path, "rb").read() for path in command["files"]]
        version = command.get("start_version", 1000)
        times = []
        for index in range(command.get("untimed", 2) + command.get("timed", 15)):
            version += 1
            done = threading.Event()
            waiting[(client, "previewed", version)] = done
            started = time.monotonic()
            header = {"type": "scene", "asset": asset, "version": version, "export_ms": 0, **command.get("extra", {})}
            if command.get("delta"):
                header.update(kind="delta", base=version - 1, removed=[])
            server.send(client, header, files[index % len(files)])
            if not done.wait(command.get("timeout", 60)):
                emit("bench", error="no previewed ack for version %d" % version, times=times)
                return
            if index >= command.get("untimed", 2):
                times.append((acks[(client, "previewed", version)] - started) * 1000)
        emit("bench", times=times, last_version=version)

    try:
        while True:
            for client, header in server.drain():
                now = time.monotonic()
                emit("frame", client=client, header=header, info=server.info_of(client), t=now)
                if header.get("type") == "status":
                    number = header.get("version") if header.get("code") == "previewed" else header.get("save")
                    key = (client, header.get("code"), number)
                    acks[key] = now
                    if key in waiting:
                        waiting.pop(key).set()
                if header.get("type") == "open" and auto != "none":
                    reply = {"id": header.get("id"), "asset": header.get("asset")}
                    if auto == "accept":
                        server.send(client, dict(reply, type="pending"))
                        server.send(client, dict(reply, type="opened"))
                    else:
                        server.send(client, dict(reply, type="refused", code="declined",
                                                 message="The person declined in Angelo"))
            try:
                command = commands.get(timeout=0.001)
            except queue.Empty:
                continue
            kind = command.get("cmd")
            if kind == "stop":
                break
            if kind == "auto":
                auto = command["open"]
            elif kind == "disconnect":
                server.disconnect(command["client"])
                emit("disconnected", client=command["client"])
            elif kind == "clients":
                emit("clients", clients={str(key): value for key, value in server.clients().items()})
            elif kind == "send":
                payload = open(command["payload_file"], "rb").read() if command.get("payload_file") else b""
                if command.get("client") is None:
                    count = server.broadcast(command["header"], payload)
                else:
                    count = int(server.send(command["client"], command["header"], payload))
                emit("sent", count=count, type=command["header"].get("type"), t=time.monotonic())
            elif kind == "capture":
                if not hasattr(server, "capture"):
                    emit("capture", unsupported=True)
                    continue

                def run(command=command):
                    started = time.monotonic()
                    png, report = server.capture(command["client"], command["header"], command.get("timeout", 8.0))
                    facts = {}
                    if png:
                        if command.get("save"):
                            with open(command["save"], "wb") as file:
                                file.write(png)
                        facts = picture_facts(png)
                    emit("capture", bytes=len(png or b""), report=report, facts=facts,
                         elapsed=time.monotonic() - started)
                threading.Thread(target=run, daemon=True).start()
            elif kind == "bench":
                threading.Thread(target=bench, args=(command,), daemon=True).start()
    finally:
        server.close()
        protocol.remove_record(args.record)
        emit("stopped")


if __name__ == "__main__":
    main()
