# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Claude Code and Codex discovery on Linux, where a desktop launcher's PATH is as short as a Finder launch's.

Covered: Claude Code's own per-user install (``~/.claude/local``), Homebrew on Linux and Nix's per-user profile are
searched like the other per-user folders, Homebrew's ``HOMEBREW_PREFIX`` (its shellenv sets it) like the other
package-manager prefixes, and the shared Linux places (Snap, Nix) are in the fixed list; what to install and how to
look for an install name what exists on Linux (Claude Code's installer or npm, never a Homebrew cask, which Linux
lacks), while macOS keeps its own wording. Nothing starts a runtime.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import cli_runtime, providers  # noqa: E402
from test_native_cli_runtime import FinderLaunch, executable  # noqa: E402


class LinuxLayouts(FinderLaunch):
    def test_per_user_linux_installs_are_found_off_path(self):
        layouts = {"claude code local": self.home / ".claude/local", "homebrew on linux": self.home / ".linuxbrew/bin",
                   "nix profile": self.home / ".nix-profile/bin"}
        for name in ("claude", "codex"):
            for label, folder in layouts.items():
                with self.subTest(name=name, layout=label):
                    expected = executable(folder / name)
                    self.assertEqual(providers.find_executable(name), str(expected))
                    Path(expected).unlink()

    def test_homebrews_prefix_is_searched(self):
        prefix = self.root / "brew-prefix"
        with patch.dict(os.environ, {"HOMEBREW_PREFIX": str(prefix)}):
            expected = executable(prefix / "bin/claude")
            self.assertEqual(providers.find_executable("claude"), str(expected))


class ProcessEnvironment(FinderLaunch):
    """Break-it review NIT-1/NIT-2: prefixes come from the process environment, and PATH keeps absolute folders only."""

    def test_only_absolute_path_entries_are_searched(self):
        with patch.object(cli_runtime, "_process_env", return_value="/usr/bin::relative:/opt/bin:"):
            self.assertEqual(cli_runtime._process_path(), "/usr/bin:/opt/bin")

    def test_prefixes_are_read_from_the_process_environment(self):
        prefix = self.root / "c-env-prefix"
        (prefix / "bin").mkdir(parents=True)
        values = {"HOMEBREW_PREFIX": str(prefix)}
        with patch.object(cli_runtime, "_process_env", side_effect=lambda name: values.get(name, "")):
            self.assertIn(str(prefix / "bin"), cli_runtime.known_directories())
        self.assertNotIn(str(prefix / "bin"), cli_runtime.known_directories(), "Python's copy alone is not read")


class LinuxWording(unittest.TestCase):
    def test_the_shared_linux_places_are_searched(self):
        for folder in ("/snap/bin", "/nix/var/nix/profiles/default/bin", "/run/current-system/sw/bin"):
            self.assertIn(folder, cli_runtime.SYSTEM_DIRECTORIES)

    def test_not_found_names_linux_installers(self):
        for name, command in (("claude", "curl -fsSL https://claude.ai/install.sh | bash"),
                              ("codex", "npm install -g @openai/codex")):
            with self.subTest(name=name):
                text = cli_runtime.not_found_message(name, searched=["/usr/bin"], platform="linux")
                self.assertIn(command, text)
                self.assertIn(f"`which {name}` in a terminal", text)
                self.assertNotIn("brew", text, "Linux has no Homebrew casks")
        mac = cli_runtime.not_found_message("claude", platform="darwin")
        self.assertIn("brew install --cask claude-code", mac)
        self.assertIn("`which claude` in Terminal", mac)

    def test_too_old_names_linux_updates(self):
        text = cli_runtime.too_old_message("claude", (2, 0, 0), platform="linux")
        self.assertIn("claude update", text)
        self.assertNotIn("brew", text)
        self.assertNotIn("brew", cli_runtime.too_old_message("codex", None, platform="linux"))
        self.assertIn("brew upgrade --cask codex", cli_runtime.too_old_message("codex", None, platform="darwin"))


if __name__ == "__main__":
    unittest.main()
