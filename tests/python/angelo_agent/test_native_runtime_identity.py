# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A live eval's plan names the Codex file its app runs, by digest (runtime_identity). Nothing is launched."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import runtime_identity  # noqa: E402

VARIABLE = "ANGELO_AGENT_RUNTIME"


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root))

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_the_bundled_runtime_beside_the_app_on_each_platform(self):
        mac = self.write("Angelo.app/Contents/Helpers/codex", b"mac runtime")
        win = self.write("Angelo/agent-runtime/codex.exe", b"windows runtime")
        cases = [
            (self.root / "Angelo.app/Contents/MacOS/Angelo", "darwin", mac, b"mac runtime"),
            (self.root / "Angelo/angelo.exe", "win32", win, b"windows runtime"),
        ]
        for binary, platform, path, content in cases:
            self.assertEqual(runtime_identity.identity(binary, environ={}, platform=platform),
                             {"kind": "bundled", "path": str(path), "sha256": hashlib.sha256(content).hexdigest()})

    def test_an_override_path_off_and_a_missing_file(self):
        override = self.write("dev/codex", b"dev build")
        binary = self.root / "Angelo.app/Contents/MacOS/Angelo"
        self.assertEqual(runtime_identity.identity(binary, environ={VARIABLE: str(override)}, platform="darwin"),
                         {"kind": "override", "path": str(override),
                          "sha256": hashlib.sha256(b"dev build").hexdigest()})
        self.assertEqual(runtime_identity.identity(binary, environ={VARIABLE: "OFF"}, platform="darwin"),
                         {"kind": "own", "path": None, "sha256": None})
        missing = runtime_identity.identity(binary, environ={}, platform="darwin")
        self.assertEqual((missing["kind"], missing["sha256"]), ("bundled", None))
        self.assertEqual(runtime_identity.identity("", environ={}), {"kind": "unknown", "path": None, "sha256": None})

    def test_the_plan_part_names_an_app_or_an_override_only(self):
        binary = self.root / "Angelo.app/Contents/MacOS/Angelo"
        self.assertEqual(runtime_identity.plan("", environ={}), {})
        self.assertEqual(runtime_identity.plan(binary, environ={})["agent_runtime"]["kind"], "bundled")
        self.assertEqual(runtime_identity.plan("", environ={VARIABLE: "off"}),
                         {"agent_runtime": {"kind": "own", "path": None, "sha256": None}})

    def test_the_eval_plan_carries_it(self):
        source = (HERE / "eval_live_agent.py").read_text(encoding="utf-8")
        self.assertIn("**runtime_identity.plan(args.blender)", source)
        self.assertLess(source.index("**runtime_identity.plan(args.blender)"), source.index('print("PLANNED"'),
                        "named in the plan, before any trial")


if __name__ == "__main__":
    unittest.main()
