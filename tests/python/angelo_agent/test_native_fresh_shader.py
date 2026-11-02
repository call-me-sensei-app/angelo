# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A timing that claims a new (uncompiled) shader gets code no earlier run compiled (``fresh_shader``).

test_blender_view_warm's reference shader was "new in every run" by a run number modulo 9973, but a node's unlinked
value is a uniform, not code, and its structure came from 102 possible chains, so on a machine that had run the suite
the driver's shader cache held it: 49.9 ms against 349-855 ms before, and the check failed on Mac 2 (1.0.8 cut
00110f294d4). Without Blender.
"""

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fresh_shader  # noqa: E402


class FreshShaderTests(unittest.TestCase):
    def test_every_run_has_its_own_chain_and_roles_of_a_run_are_equally_long(self):
        tokens = [i.to_bytes(2, "big") + b"run" for i in range(2000)]
        chains = {tuple(fresh_shader.operations(24, "reference", token)) for token in tokens}
        self.assertEqual(len(chains), len(tokens), "two runs never share the code")
        self.assertEqual(fresh_shader.operations(24, "reference", b"a"), fresh_shader.operations(24, "reference", b"a"))
        ahead, reference = (fresh_shader.operations(68, role, b"one run") for role in ("ahead", "reference"))
        self.assertEqual(len(ahead), len(reference))
        self.assertNotEqual(ahead, reference, "two roles of one run are two shaders")
        self.assertTrue(set(ahead) <= set(fresh_shader.OPERATIONS))
        self.assertEqual(len(fresh_shader.TOKEN), 16)

    def test_a_suite_that_claims_a_cold_shader_cache_builds_its_shaders_fresh(self):
        # The class: a run-number salt, or a value that is only a uniform, repeats code a driver keeps across runs.
        for path in sorted(HERE.glob("*.py")):
            if path.name in {"fresh_shader.py", Path(__file__).name}:
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"shader cache", text, re.IGNORECASE) and "ShaderNode" in text:
                with self.subTest(file=path.name):
                    self.assertIn("fresh_shader.chain(", text)
                    self.assertIsNone(re.search(r"time\.time\(\)[^\n]*%\s*\d+", text), "a run-number salt repeats")


if __name__ == "__main__":
    unittest.main()
