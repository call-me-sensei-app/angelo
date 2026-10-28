# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The headless child for Angelo's MCP server: an app's calls on a .blend with no Angelo window.

Only the MCP launcher (``angelo_mcp.py``) runs this, inside ``Angelo --background``, with the two pipes it talks
over; see ``angelo_agent.headless_main`` and doc/angelo/external_mcp.rst, "Headless". With ``--worker`` first, it
is one of Angelo's own worker Blenders instead, started by ``angelo_agent.worker_process``
(``angelo_agent.worker_main``).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons_core"))

arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if arguments[:1] == ["--worker"]:
    from angelo_agent.worker_main import main  # noqa: E402
    main(arguments[1:])
else:
    from angelo_agent.headless_main import main  # noqa: E402
    main(arguments)
