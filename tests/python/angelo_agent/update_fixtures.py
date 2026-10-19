# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Signed update feeds for tests: a throwaway key per run, never a product key.

The seed comes from ``os.urandom`` and exists only in memory; the product's
signing key lives in Google Cloud KMS and never reaches a test
(doc/angelo/updates_and_reports.rst, "Key ceremony"). Shared by the host tests and the GUI
suite, which serves these feeds from a loopback server.
"""

import base64
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ed25519, update_feed  # noqa: E402

KEY_ID = "test-key"


def new_key():
    seed = os.urandom(32)
    return seed, {KEY_ID: ed25519.public_key(seed)}


def payload(version="1.1.0", sequence=3, **changes):
    """A valid payload document; ``changes`` replace top-level fields, ``latest`` takes dotted overrides."""
    document = {
        "schema": 1, "channel": "stable", "sequence": sequence, "issued": "2026-10-20T09:00:00Z",
        "expires": "2027-04-20T09:00:00Z", "minimum_supported": "1.0.0",
        "latest": {
            "version": version, "released": "2026-10-20", "title": "Angelo " + version.rsplit(".", 1)[0],
            "summary": "Faster scenes, a new library and fixes.",
            "highlights": ["Edits finish in fewer steps", "A calmer first run", "Windows ARM support"],
            "notes_url": "https://angelo.studio/releases#" + version, "critical": False,
            "downloads": {
                "macos-arm64": {"url": "https://angelo.studio/downloads/Angelo-%s-macos-arm64.dmg" % version,
                                "sha256": "a" * 64, "size": 412_000_000},
                "windows-x64": {"url": "https://github.com/org/angelo/releases/download/v%s/Angelo-%s-windows-x64.msi"
                                % (version, version), "sha256": "b" * 64, "size": 301_000_000}}},
    }
    document.update(changes)
    return document


def envelope(document, seed, key_id=KEY_ID, payload_bytes=None, context=update_feed.SIGNING_CONTEXT):
    """The signed feed bytes. ``payload_bytes`` overrides the signed text (to test tampering)."""
    data = payload_bytes if payload_bytes is not None else json.dumps(document, sort_keys=True).encode("utf-8")
    return json.dumps({
        "format": update_feed.FORMAT, "version": 1, "key_id": key_id,
        "payload": base64.b64encode(data).decode("ascii"),
        "signature": base64.b64encode(ed25519.sign(seed, context + data)).decode("ascii")}).encode("utf-8")
