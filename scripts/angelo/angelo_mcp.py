#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Launcher for Angelo's MCP server (stdio) for external AI apps.

An MCP client starts it with Angelo's bundled Python in isolated mode, so
PYTHONPATH and user site-packages cannot change what runs::

    <bundled python3.13> -I scripts/angelo/angelo_mcp.py --client claude-code

Angelo writes this command into an app's MCP settings when the person clicks
Connect (Preferences > AI Providers > External AI apps). The server talks to
the running Angelo over its private local socket; it opens no network port.
See doc/angelo/external_mcp.rst.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons_core"))

from angelo_agent.external_mcp import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
