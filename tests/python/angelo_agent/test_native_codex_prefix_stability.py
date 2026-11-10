# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Codex chat's requests only ever append, picture turns included: the prompt-cache prefix stays byte-stable.

Regression guard for the 2026-11-04 root cause (gate_spec_110's cache section): picture-turn cache misses were the
provider's, because Angelo and the bundled Codex keep every byte of the earlier request. ``codex_image_prefix_probe``
drives a real ``codex app-server`` against a local fake Responses endpoint with Angelo's launch flags and picture
forwarding; two turns call ``angelo_view`` (a picture each), then a plain turn. Unbilled; nothing leaves 127.0.0.1.

Runs where a codex is available (``ANGELO_TEST_CODEX``, else the agent runtime); skipped otherwise, as on GitHub CI,
which carries no runtime. Required at every cut against the exact bundled runtime: the Windows release stage runs it
on the MSI's codex.exe (windows_release_validate.ps1, a skip fails there) and Mac 2's chain on the app's codex.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from host_test_policy import environment_skip  # noqa: E402


def _codex_binary():
    """A codex executable to drive for real: ``ANGELO_TEST_CODEX``, else the agent runtime when installed."""
    candidate = os.environ.get("ANGELO_TEST_CODEX")
    if not candidate:
        try:
            from angelo_agent import agent_runtime
            candidate = agent_runtime.bundled()
        except Exception:  # noqa: BLE001 - no runtime: the class is skipped.
            candidate = None
    return candidate if candidate and os.access(candidate, os.X_OK) else None


class RequiredAtEveryCutTests(unittest.TestCase):
    """Runs everywhere, CI included: the cut's own checks run this test on the exact bundled runtime."""

    def test_the_windows_release_stage_requires_it_on_the_msis_codex(self):
        stage = (HERE.parents[2] / "scripts/angelo/windows_release_validate.ps1").read_text(encoding="utf-8")
        block = stage[stage.index('"release: the MSI\'s codex keeps a chat\'s request prefix'):]
        block = block[:block.index("Test-Check ", 1)]
        for part in ('$runtime.files."codex.exe"', "$env:ANGELO_TEST_CODEX = $codex", "test_native_codex_prefix_stability.py",
                     '-match "skipped"'):
            self.assertIn(part, block)


@unittest.skipUnless(_codex_binary(), environment_skip("no codex executable (set ANGELO_TEST_CODEX to the agent runtime's codex)"))
class PrefixStabilityTests(unittest.TestCase):
    def run_probe(self, size):
        import codex_image_prefix_probe as probe
        with tempfile.TemporaryDirectory() as out:
            probe.main([out, "--codex", _codex_binary(), "--size", size])
            report = json.loads(Path(out, "report.json").read_text(encoding="utf-8"))
            pictures = [Path(out, "request-%02d.json" % index).read_text(encoding="utf-8").count('"input_image"')
                        for index in range(report["requests"])]
        return report, pictures

    def test_every_request_keeps_the_earlier_one_byte_for_byte_with_pictures(self):
        for size in ("16x16", "1024x768"):
            with self.subTest(size=size):
                report, pictures = self.run_probe(size)
                self.assertEqual(report["requests"], 5, "five model requests (two picture turns, then a plain one): %s"
                                 % report["stderr_tail"])
                self.assertGreaterEqual(max(pictures), 2, "both pictures reached the model input: %s" % pictures)
                for pair in report["pairs"]:
                    # Every failure names the pair, what the check looked for and what changed, so a release stage's
                    # log alone says why (the full report is printed above it).
                    seen = json.dumps(pair, sort_keys=True)
                    with self.subTest(pair=pair["pair"]):
                        self.assertIsNone(pair["first_changed_item"], "an earlier input item changed: " + seen)
                        # The later input starts with the earlier one up to its closing bracket.
                        self.assertEqual(pair["kept_prefix_bytes"], pair["earlier_input_bytes"] - 1,
                                         "the later input does not start with the earlier one: " + seen)
                        changed = sorted(name for name, same in pair["other_fields_equal"].items() if not same)
                        self.assertLessEqual(set(changed), {"client_metadata"},
                                             "only per-turn metadata may change, but %s did: %s" % (changed, seen))


if __name__ == "__main__":
    unittest.main()
