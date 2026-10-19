#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Launcher for the external ``angelo`` CLI (phase 1, read-only).

Run it with Angelo's bundled Python in isolated mode, so PYTHONPATH and user
site-packages cannot change what runs::

    <bundled python3.13> -I scripts/angelo/angelo_cli.py --client claude-code status

On macOS the bundled interpreter is
``Angelo.app/Contents/Resources/5.2/python/bin/python3.13``; the transport is a
Unix socket, so Windows has no listener yet.

The client is pure standard library and sits next to the add-on it talks to.
See doc/angelo/external_cli.rst for commands, envelopes and exit codes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons_core"))

from angelo_agent.cli_client import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
