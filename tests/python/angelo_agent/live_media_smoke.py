# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live smokes of Media Studio's paid video and 3D providers through the real job code (no Blender, billed).

Not a unit test (no ``test_`` prefix, never collected): it spends real money, a few cents to a dollar a case, and runs
only when asked. Each case is the cheapest request that exercises the real path: a first-frame upload, ONE submit,
following the provider's queue, the download and its checks. Keys come from the environment
(``ANGELO_SMOKE_FAL_KEY``, ``ANGELO_SMOKE_BYTEPLUS_KEY``) or, on macOS, a login-Keychain item (``--keychain-service``,
accounts ``FAL_DEV_KEY`` and ``BYTEPLUS_DEV_KEY``); they are held in memory only and never printed.

    python3.13 tests/python/angelo_agent/live_media_smoke.py --authorize-billed-runs --out DIR fal-video fal-trellis
    python3.13 tests/python/angelo_agent/live_media_smoke.py --authorize-billed-runs --out DIR byteplus-video byteplus-stop

With ``--gateway ORIGIN`` the same cases run as a team connection through an Angelo gateway instead, with the
signed-in member's access token from ``ANGELO_TOKEN`` (a sign-in helper provides it,
in memory only); the org's own fal and BytePlus keys pay.

A JSON line per case (status, ids, seconds, estimated price, output checks) goes to stdout and ``DIR/results.jsonl``.
"""

import argparse
import json
import os
import struct
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import byteplus_models, byteplus_video, fal_models, fal_video, flow_prices  # noqa: E402
from angelo_agent.generative import GenerationBinding  # noqa: E402
from angelo_agent.provider_registry import SPECS  # noqa: E402
from angelo_agent.studio_generation import StopSignal  # noqa: E402

PROMPT = "A red rubber ball rolls slowly across a wooden table toward the camera, soft daylight"
VIDEO_OPTIONS = {"duration": 4, "resolution": "480p", "aspect": "16:9", "audio": False}


def key(env, account, service):
    if GATEWAY:
        return ""  # the gateway applies the org's key
    if os.environ.get(env):
        return os.environ[env]
    if service:
        found = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                               capture_output=True, text=True)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    raise SystemExit(f"no key: set {env} or store {account} under the Keychain service {service!r}")


def ball_png(path, size=512):
    """A shaded red ball on white: a recognizable single object for image-to-video and image-to-3D."""
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dx, dy = (x - size / 2) / (size * 0.3), (y - size / 2) / (size * 0.3)
            d = dx * dx + dy * dy
            if d <= 1.0:
                light = max(0.15, min(1.0, (1 - d) ** 0.5 * 0.8 + (-dx - dy) * 0.25 + 0.2))
                row += bytes((int(220 * light), int(40 * light), int(35 * light)))
            else:
                row += b"\xf4\xf2\xee"
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))
    return path


class _Token:
    """A team account stand-in: one access token, never printed."""

    def __init__(self, token):
        self._token = token

    def access_token(self, stale=""):
        return self._token

    def redact(self, text):
        return str(text).replace(self._token, "[REDACTED]")


GATEWAY = ""


def binding(mode, secret):
    if not GATEWAY:
        return GenerationBinding("smoke", mode, tuple(m["id"] for m in SPECS[mode]["models"]), True, secret)
    team = {"fal_api": "fal_team", "seedream_api": "seedream_team"}[mode]
    token = os.environ.get("ANGELO_TOKEN") or sys.exit("--gateway needs ANGELO_TOKEN (scripts/app-sign-in.mjs --exec)")
    return GenerationBinding("smoke", team, tuple(m["id"] for m in SPECS[team]["models"]), True, "",
                             GATEWAY + SPECS[team]["gateway_path"], _Token(token), "smoke")


def run(name, work, directory):
    directory.mkdir(parents=True, exist_ok=True)
    events, started = [], time.monotonic()
    emit = lambda event: events.append(dict(event, at=round(time.monotonic() - started, 1)))  # noqa: E731
    result = {"case": name}
    try:
        final = work(directory, emit)
        result.update(status=final.get("status"), files=final.get("files"))
    except Exception as error:  # noqa: BLE001 - a smoke reports every outcome
        result.update(status="error", error=f"{type(error).__name__}: {error}")
    result["seconds"] = round(time.monotonic() - started, 1)
    result["ids"] = sorted({e.get("task_id") for e in events if e.get("task_id")})
    result["stages"] = [(e.get("status"), e["at"]) for e in events if e.get("status")]
    for file in result.get("files") or ():
        data = (directory / file).read_bytes()
        result[file] = {"bytes": len(data), "mp4": data[4:8] == b"ftyp", "glb": data[:4] == b"glTF",
                        "seconds": fal_video.mp4_seconds(data) if file.endswith(".mp4") else None}
    (directory / "events.json").write_text(json.dumps(events, indent=1))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("cases", nargs="+", choices=("fal-video", "fal-trellis", "byteplus-video", "byteplus-stop"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--keychain-service", default="")
    parser.add_argument("--authorize-billed-runs", action="store_true", help="required: every case is billed")
    parser.add_argument("--gateway", default="", help="an Angelo gateway origin: run as a team connection (ANGELO_TOKEN)")
    parser.add_argument("--fal-model", default=fal_models.DEFAULT_MODEL, help="the fal video model (default: the app's default)")
    parser.add_argument("--byteplus-model", default=byteplus_models.DEFAULT_MODEL, help="the BytePlus video model")
    parser.add_argument("--fal-options", type=json.loads, default=None, help="JSON video options for fal-video (default: 4 s, 480p)")
    parser.add_argument("--byteplus-options", type=json.loads, default=None, help="JSON video options for the BytePlus cases")
    args = parser.parse_args()
    global GATEWAY
    GATEWAY = args.gateway.rstrip("/")
    if not args.authorize_billed_runs:
        parser.error("every case spends real money on the provider key; pass --authorize-billed-runs")
    args.out.mkdir(parents=True, exist_ok=True)
    frame = ball_png(args.out / "ball.png")
    inputs = {"first": {"path": str(frame), "name": "ball"}, "last": None, "references": []}
    fal_model, bp_model = args.fal_model, args.byteplus_model
    fal_options, bp_options = args.fal_options or VIDEO_OPTIONS, args.byteplus_options or VIDEO_OPTIONS
    cases = {
        "fal-video": lambda d, e: fal_video.generate(binding("fal_api", key("ANGELO_SMOKE_FAL_KEY", "FAL_DEV_KEY", args.keychain_service)),
                                                     fal_model, PROMPT, fal_options, inputs, d, e, StopSignal()),
        "fal-trellis": lambda d, e: fal_video.generate_3d(binding("fal_api", key("ANGELO_SMOKE_FAL_KEY", "FAL_DEV_KEY", args.keychain_service)),
                                                          "trellis-2", {"polycount": 20000}, [{"path": str(frame), "name": "ball"}], d, e, StopSignal()),
        "byteplus-video": lambda d, e: byteplus_video.generate(binding("seedream_api", key("ANGELO_SMOKE_BYTEPLUS_KEY", "BYTEPLUS_DEV_KEY", args.keychain_service)),
                                                               bp_model, PROMPT, bp_options, inputs, d, e, StopSignal()),
    }

    def byteplus_stop(directory, emit):
        """Stop right after the create, as the person's Stop: a task still queued is cancelled; one already running
        finishes and is downloaded (both are correct outcomes; the result says which)."""
        cancel = StopSignal()
        cancel.remote = True

        def on_event(event):
            emit(event)
            if event.get("byteplus_task"):
                threading.Timer(0.5, cancel.set).start()
        return byteplus_video.generate(binding("seedream_api", key("ANGELO_SMOKE_BYTEPLUS_KEY", "BYTEPLUS_DEV_KEY", args.keychain_service)),
                                       bp_model, PROMPT, bp_options, inputs, directory, on_event, cancel)
    cases["byteplus-stop"] = byteplus_stop
    prices = {"fal-video": flow_prices.estimate("fal_api", fal_model, fal_options),
              "fal-trellis": flow_prices.estimate("fal_api", "trellis-2", {"polycount": 20000}, 1),
              "byteplus-video": flow_prices.estimate("seedream_api", bp_model, bp_options),
              "byteplus-stop": flow_prices.estimate("seedream_api", bp_model, bp_options)}
    for name in args.cases:
        result = dict(run(name, cases[name], args.out / name), estimated_usd=prices[name])
        line = json.dumps(result)
        print(line, flush=True)
        with open(args.out / "results.jsonl", "a", encoding="utf-8") as out:
            out.write(line + "\n")


if __name__ == "__main__":
    main()
