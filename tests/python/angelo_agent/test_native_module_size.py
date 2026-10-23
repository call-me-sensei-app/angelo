# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every angelo_agent module stays at or under 500 lines.

The listed exceptions predate the limit's enforcement: the chat controller,
provider adapters, UI drawing, Studio and sculpt modules are single cohesive
surfaces that have not been split yet, and ``identifiers.py`` grew past the
limit with the agent-stack integration that owns it. Each is pinned at its
current size so it cannot grow. Splitting code out of one (as chat titles,
plan validation, the runtime launch environment and the stream-event reader
already were) lowers or removes its entry.
"""

import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
LIMIT = 500
EXCEPTIONS = {"controller.py": 2626, "ui.py": 2269, "providers.py": 1388, "chat_view.py": 1292,
              "sculpt.py": 753, "telemetry.py": 617, "identifiers.py": 540}


class ModuleSizeTests(unittest.TestCase):
    def test_modules_stay_within_the_limit(self):
        for path in sorted(ADDON.rglob("*.py")):
            name = path.relative_to(ADDON).as_posix()
            with self.subTest(module=name):
                lines = len(path.read_text(encoding="utf-8").splitlines())
                self.assertLessEqual(lines, EXCEPTIONS.get(name, LIMIT))

    def test_exceptions_are_still_needed(self):
        for name, pinned in EXCEPTIONS.items():
            with self.subTest(module=name):
                self.assertGreater(len((ADDON / name).read_text(encoding="utf-8").splitlines()), LIMIT)


if __name__ == "__main__":
    unittest.main()
