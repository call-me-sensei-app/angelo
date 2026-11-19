# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One of Angelo's own worker Blenders, started by ``angelo_agent.worker_process`` inside ``Angelo --background``
(``angelo_agent.worker_main``). Until 2.0.1 the MCP launcher also started a headless child here; that child is gone
(doc/angelo/external_mcp.rst, "What changed")."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons_core"))

arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if arguments[:1] != ["--worker"]:
    sys.exit("angelo_headless.py runs only Angelo's own worker Blenders (--worker)")
from angelo_agent.worker_main import main  # noqa: E402
main(arguments[1:])
