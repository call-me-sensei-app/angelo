# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent runtime's licenses and notices ship with the app and match its pin.

Dependency-free. ``release/license/agent-runtime`` (installed with ``release/license`` into the app's text
folder) holds OpenAI Codex's Apache-2.0 ``LICENSE.txt`` and ``NOTICE.txt``, ``README.txt`` (what is
bundled, the modification, the fork's branch and commit, and the return to stock Codex) and
``THIRD-PARTY-NOTICES.txt`` written by ``scripts/angelo/agent_runtime_notices.py`` for the pinned commit.
``copyright.txt`` points at the folder. The generator's dependency walk and its V8 list are checked too;
running it needs the fork and Cargo (doc/angelo/releasing.rst, "Agent runtime").
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FOLDER = ROOT / "release/license/agent-runtime"
PIN = json.loads((ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import agent_runtime_notices as notices  # noqa: E402


def read(name):
    return (FOLDER / name).read_text(encoding="utf-8")


class ShippedNoticeTests(unittest.TestCase):
    def test_codex_license_notice_and_modification_statement(self):
        self.assertEqual(sorted(path.name for path in FOLDER.iterdir()),
                         ["LICENSE.txt", "NOTICE.txt", "README.txt", "THIRD-PARTY-NOTICES.txt"])
        license_text = read("LICENSE.txt")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)
        # Verbatim from the pinned commit (its spaces include no-break spaces).
        self.assertTrue(" ".join(read("NOTICE.txt").split()).startswith("OpenAI Codex Copyright 2025 OpenAI"))
        readme = " ".join(read("README.txt").split())
        for fact in (PIN["version"], PIN["upstream"]["tag"], PIN["upstream"]["repository"], PIN["fork"]["branch"],
                     PIN["fork"]["commit"], PIN["upstream"]["feature_request"], "Apache License, Version 2.0",
                     "cacheAffinityKey", "was modified", "unchanged", "Contents/Helpers/codex",
                     "OpenAI's own signature"):
            self.assertIn(fact, readme)
        for name in PIN["platforms"]["windows"]["required"] + PIN["platforms"]["windows"]["upstream_signed"]:
            self.assertIn(name, readme, "every Windows file, and which of them stay OpenAI's")

    def test_the_third_party_notices_are_this_pins(self):
        text = read("THIRD-PARTY-NOTICES.txt")
        self.assertIn("from the fork's Cargo.lock at %s" % PIN["fork"]["commit"], text)
        self.assertIn("OpenAI Codex %s" % PIN["version"], text)
        for platform in PIN["platforms"].values():
            self.assertIn(platform["rust_target"], text)
        crates = [line for line in text.splitlines() if line.startswith("* ") and "; in " in line]
        self.assertGreater(len(crates), 500)
        held = {name for line in crates for name in line.split("; in ", 1)[1].split(";", 1)[0].split(", ")}
        for platform in PIN["platforms"].values():
            for name in platform["required"] + platform["optional"]:
                self.assertIn(name.removesuffix(".exe"), held, "every pinned program's crates are listed")
        self.assertIn("* v8 ", text)
        for name, *_rest in notices.V8_COMPONENTS:
            self.assertIn("* %s: " % name, text)
        self.assertIn("Copyright (C) 1993 by Sun Microsystems", text)  # A notice that must be kept verbatim.
        self.assertIn("UNICODE LICENSE V3", text, "ICU's license is reproduced")

    def test_the_folder_ships_and_the_copyright_text_points_at_it(self):
        cmake = (ROOT / "source/creator/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn('install(\n    DIRECTORY ${CMAKE_SOURCE_DIR}/release/license\n    DESTINATION "${TARGETDIR_TEXT}"', cmake)
        copyright_text = " ".join((ROOT / "release/text/copyright.txt").read_text(encoding="utf-8").split())
        self.assertIn("modified build of OpenAI Codex", copyright_text)
        self.assertIn("license/agent-runtime/", copyright_text)

    def test_every_license_the_v8_list_names_has_its_text(self):
        expressions = " ".join(component[1] for component in notices.V8_COMPONENTS if component[0] != "fdlibm")
        for identifier in notices.spdx_ids(expressions):
            if identifier not in ("CC0-1.0", "Unicode-3.0"):
                self.assertTrue(notices.spdx_text_exists(identifier), identifier)


class ClosureTests(unittest.TestCase):
    def test_only_run_time_dependencies_ship(self):
        def package(name, kinds=("lib",), source="registry+https://github.com/rust-lang/crates.io-index"):
            return {"id": name, "name": name, "version": "1.0.0", "source": source,
                    "targets": [{"kind": list(kinds), "name": name}]}
        packages = [package("codex-cli", ("bin",), None), package("runtime"), package("deep"), package("builder"),
                    package("macros", ("proc-macro",)), package("macro-helper"), package("tests-only")]
        packages[0]["targets"][0]["name"] = "codex"

        def edge(target, kind=None):
            return {"pkg": target, "dep_kinds": [{"kind": kind}]}
        meta = {"packages": packages, "resolve": {"nodes": [
            {"id": "codex-cli", "deps": [edge("runtime"), edge("builder", "build"), edge("macros"),
                                         edge("tests-only", "dev")]},
            {"id": "runtime", "deps": [edge("deep")]}, {"id": "deep", "deps": []}, {"id": "builder", "deps": []},
            {"id": "macros", "deps": [edge("macro-helper")]}, {"id": "macro-helper", "deps": []},
            {"id": "tests-only", "deps": []}]}}
        self.assertEqual(sorted(notices.runtime_closure(meta, ["codex"])), ["deep", "runtime"])
        with self.assertRaises(notices.NoticeError):
            notices.runtime_closure(meta, ["codex-code-mode-host"])

    def test_spdx_expressions_old_and_new(self):
        self.assertEqual(notices.spdx_ids("MIT/Apache-2.0"), ["Apache-2.0", "MIT"])
        self.assertEqual(notices.spdx_ids("(MIT OR Apache-2.0) AND Unicode-3.0"), ["Apache-2.0", "MIT", "Unicode-3.0"])
        self.assertEqual(notices.spdx_ids("Apache-2.0 WITH LLVM-exception"), ["Apache-2.0", "LLVM-exception"])


if __name__ == "__main__":
    unittest.main()
