# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Runs inside a published app for update_lines_shipped_client.py: its own update check, its own code, over HTTP."""

import base64
import dataclasses
import json
import os
import tempfile
from pathlib import Path

from angelo_agent import app_version, update_check, update_feed

key = base64.b64decode(os.environ["ANGELO_LINES_KEY"])
with tempfile.TemporaryDirectory() as folder:
    checker = update_check.Checker(folder, keys={os.environ["ANGELO_LINES_KEY_ID"]: key}, platform="macos-arm64")
    result = checker.check(manual=True)
    state = checker.state()
    answer = {"running": app_version.current(), "kind": result.kind, "detail": result.detail,
              "last_sequence": state.last_sequence}
    feed = update_check.cached_feed(state, checker.keys)
    if feed is not None:
        answer["feed_read"] = {name: getattr(feed, name) for name in feed.__dataclass_fields__ if name != "latest"}
        answer["feed_read"]["latest"] = dataclasses.asdict(feed.latest)
    if result.offer is not None:
        answer.update(offered=result.offer.release.version, critical=result.offer.critical,
                      automatic_install=result.offer.release.automatic_install)
        answer["installable"] = update_feed.installable(result.offer.download.url)
Path(os.environ["ANGELO_LINES_OUT"]).write_text(json.dumps(answer, indent=2, default=str))
