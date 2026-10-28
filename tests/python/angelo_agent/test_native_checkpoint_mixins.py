# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The checkpoint store's mixins resolve their dependencies from ``checkpoints`` at call time (break-it R4-L-8).

``checkpoint_retention`` and ``checkpoint_restore_plan`` read names such as ``checkpoints.planned_entry`` only when a
removal or a Restore runs, so an import that looks unused in checkpoints.py and is removed would break them only
then. Every such name must exist on the module.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import checkpoints  # noqa: E402

MIXINS = ("checkpoint_retention.py", "checkpoint_restore_plan.py")


class MixinDependencyTests(unittest.TestCase):
    def test_every_name_a_mixin_reads_from_the_store_module_exists(self):
        for module in MIXINS:
            tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent" / module).read_text(encoding="utf-8"))
            names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                     and isinstance(node.value, ast.Name) and node.value.id == "checkpoints"}
            self.assertTrue(names, module)
            for name in sorted(names):
                with self.subTest(module=module, name=name):
                    self.assertTrue(hasattr(checkpoints, name))

    def test_the_store_has_the_mixins_methods(self):
        for method in ("restore_plan", "restore_since", "_prepare_file_restore", "_validate_file_restore",
                       "_record_restored_effect", "_require_recovery", "prune", "_remove", "inspect"):
            with self.subTest(method=method):
                self.assertTrue(callable(getattr(checkpoints.CheckpointStore, method)))


if __name__ == "__main__":
    unittest.main()
