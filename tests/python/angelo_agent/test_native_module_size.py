# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every angelo_agent module, and every Angelo module Blender loads from ``scripts/modules`` and
``scripts/startup`` (``angelo_*``), stays at or under 500 lines.

The listed exceptions predate the limit's enforcement: the chat controller,
provider adapters, UI drawing, Studio and sculpt modules are single cohesive
surfaces that have not been split yet, and ``identifiers.py`` grew past the
limit with the agent-stack integration that owns it. Each is pinned at its
current size so it cannot grow. Splitting code out of one (as chat titles,
plan validation, the runtime launch environment and the stream-event reader
already were) lowers or removes its entry.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
LIMIT = 500
EXCEPTIONS = {"controller.py": 2571, "ui.py": 2250, "providers.py": 1388, "chat_view.py": 1286,
              "sculpt.py": 753, "telemetry.py": 617, "identifiers.py": 540}


class ModuleSizeTests(unittest.TestCase):
    def test_modules_stay_within_the_limit(self):
        for path in sorted(ADDON.rglob("*.py")):
            name = path.relative_to(ADDON).as_posix()
            with self.subTest(module=name):
                lines = len(path.read_text(encoding="utf-8").splitlines())
                self.assertLessEqual(lines, EXCEPTIONS.get(name, LIMIT))

    def test_angelo_modules_beside_blender_s_stay_within_the_limit(self):
        """Angelo's own modules among Blender's (``angelo_*`` files and packages), which no exception covers."""
        found = []
        for folder in ("scripts/modules", "scripts/startup"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                parts = path.relative_to(ROOT / folder).parts
                if not parts[0].startswith("angelo"):
                    continue   # Blender's own modules keep upstream's shape.
                found.append(path)
                with self.subTest(module=path.relative_to(ROOT).as_posix()):
                    self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), LIMIT)
        self.assertTrue(found)

    def test_exceptions_are_still_needed(self):
        for name, pinned in EXCEPTIONS.items():
            with self.subTest(module=name):
                self.assertGreater(len((ADDON / name).read_text(encoding="utf-8").splitlines()), LIMIT)


if __name__ == "__main__":
    unittest.main()
