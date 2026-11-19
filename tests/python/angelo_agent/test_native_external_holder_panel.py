# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A second Angelo build that turns access on says which build has it and what to do; it never takes it.

The 2.0.1 internal test (2.0.0 held the endpoint) left 2.0.1's status a raw English error naming no build
(acd0d465ee9). Each test names that behaviour. Contract: doc/angelo/external_mcp.rst, "Two Angelo builds".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent import external_build  # noqa: E402
from angelo_agent.external_cli import TransportUnavailable  # noqa: E402


class HostHolderTests(unittest.TestCase):
    def test_the_host_keeps_who_holds_the_endpoint_and_clears_it_once_listening(self):
        from angelo_agent.external_host import ExternalHost

        class Env:
            def __init__(self, state):
                self.state = state

            def state_directory(self):
                return self.state

        root = Path(tempfile.mkdtemp(prefix="smis-host-"))
        self.addCleanup(shutil.rmtree, root, True)
        attempts = []

        class Server:
            def __init__(self, *args, **options):
                pass

            def start(self):
                attempts.append(1)
                if len(attempts) == 1:
                    raise TransportUnavailable(external_build.holder_note("2.0.0", 55405), holder=("2.0.0", 55405))

            def stop(self):
                pass

        host = ExternalHost(Env(str(root / "state")), server_factory=Server, environ={}, runtime_directory=root)
        with patch.object(host, "publish", lambda: None):
            self.assertFalse(host.start())
            self.assertEqual(host.holder, ("2.0.0", 55405))
            self.assertTrue(host.status.startswith("Unavailable: Angelo 2.0.0 (PID 55405)"))
            host._retry_after = 0.0
            self.assertTrue(host.start())
        self.assertIsNone(host.holder)


class PanelTests(unittest.TestCase):
    """The panel says which build holds access; entries: test_native_external_entry_elsewhere."""

    @classmethod
    def setUpClass(cls):
        from blender_stand_in import StandIn
        cls.stand_in = StandIn(("ui", "external_ui", "external_clients", "external_setup", "providers_page_external",
                                "managed_policy", "external_host"))
        cls.addClassCleanup(cls.stand_in.__exit__, None, None, None)
        cls.m = cls.stand_in.__enter__()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name) / "state"
        policy = self.m["managed_policy"]
        policy.reset(policy.Policy())
        self.addCleanup(policy.reset)

    def access_row(self, holder):
        page, host = self.m["providers_page_external"], self.m["external_host"]
        ui = SimpleNamespace(state_directory=lambda: str(self.state))
        prefs = SimpleNamespace(allow_local_cli=True)
        with patch.object(host, "status_text", lambda: "Unavailable: whatever"), \
                patch.object(host, "holder", lambda: holder), patch.object(sys, "platform", "darwin"):
            return page.section(ui, prefs, self.m["managed_policy"].current())["rows"][0]

    def test_access_held_by_another_build_says_which_and_what_to_do(self):
        """Failed on acd0d465ee9: the chip showed the raw English error, with no build named."""
        row = self.access_row(("2.0.0", 55405))
        self.assertEqual(row["chip"][0], "Status: Waiting for Angelo 2.0.0")
        self.assertIn("Angelo 2.0.0 (PID 55405) has access for external apps for your user. Quit it, or turn off "
                      "access there; this Angelo takes over once it is free.", row["lines"])
        self.assertTrue(row["on"], "the switch stays as the person set it")

    def test_an_unknown_holder_is_named_by_pid(self):
        row = self.access_row((None, 55405))
        self.assertEqual(row["chip"][0], "Status: Waiting for another Angelo")
        self.assertIn("Another Angelo (PID 55405) has access", " ".join(row["lines"]))


if __name__ == "__main__":
    unittest.main()
