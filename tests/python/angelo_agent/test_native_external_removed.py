# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""2.0.0's direct tool access for connected apps is gone, with nothing left to reach it (doc/angelo/external_mcp.rst,
"What changed").

A connected app works through Angelo's agent and never calls a tool. No source, script or test of this tree may name
what only the Tools level used: the per-app levels, the app's tool-call request, its script files and their
approvals, the Tasks-level tools and 2.0.0's headless MCP child. A name that comes back is either dead code or a way
back to direct access.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

REMOVED = ("external_work", "external_apps", "external_records", "launcher_records", "script_files",
           "angelo_run_script_file", "angelo_task_status", "angelo_task_stop", "script_file_allow",
           "offers_script_file_allow", "send_external_task", "open_external_work", "set_levels", "tools_for",
           "DEFAULT_LEVELS", "SCRIPT_FILE_TOOL", "HEADLESS_TOOLS", "external_app_forget_scripts",
           # 2.0.0's headless MCP child (direct tools on a .blend without the window). Its file tools' names return as
           # agent-path tools that open a hidden-window Angelo, so they are not listed.
           "headless_session", "headless_host", "headless_answers", "headless_files", "headless_working_copy",
           "HeadlessSession", "HeadlessHost",
           "angelo_task", "send_external_task", "ExternalWork", "ExternalApps")
PATTERN = re.compile(r"\b(%s)\b" % "|".join(map(re.escape, REMOVED)))
FOLDERS = ("scripts/addons_core/angelo_agent", "scripts/angelo", "tests/python/angelo_agent", "source/blender/python")
SUFFIXES = (".py", ".cc", ".hh", ".h", ".c")


class NothingReachesTheRemovedPaths(unittest.TestCase):
    def test_no_source_or_test_names_what_only_direct_tool_access_used(self):
        found = []
        for folder in FOLDERS:
            for path in sorted((ROOT / folder).rglob("*")):
                relative = path.relative_to(ROOT).as_posix()
                if path.suffix not in SUFFIXES or path.name == Path(__file__).name:
                    continue
                for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if PATTERN.search(line):
                        found.append("%s:%d: %s" % (relative, number, line.strip()[:100]))
        self.assertEqual(found, [])

    def test_an_app_is_offered_no_angelo_tool(self):
        from angelo_agent.external_mcp_tools import AGENT_NAMES
        from angelo_agent.tool_specs import TOOL_MAP
        self.assertFalse(AGENT_NAMES & set(TOOL_MAP))

    def test_the_cli_service_has_no_app_tool_call_command(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/external_service.py").read_text(encoding="utf-8")
        self.assertNotIn('"app_call"', source)
        self.assertNotIn('command == "task"', source)


if __name__ == "__main__":
    unittest.main()
