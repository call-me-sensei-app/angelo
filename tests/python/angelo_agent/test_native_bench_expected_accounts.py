# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The expected accounts reach every billed child of a benchmark run, on every machine that runs one.

A billed start checks the profile it uses against the account the operator's kit names
(``provider_accounts.CODEX_ACCOUNT_ENV``, ``CLAUDE_ACCOUNT_ENV``) and refuses without one. Each side of a trial starts
from ``bench_mcp_trial.clean_environment``, an allowlist, so the names must be on it: dropped, every billed gate step of
a Windows release run was refused at its first trial although the machine had the value. The Windows send carries the
Claude account as it carries the Codex one, and the machine exports both to its runs. Fixture values only; no client,
model or Blender is started (one plain Python child reads its environment).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _path in (str(ROOT / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import account_fixtures  # noqa: E402
import bench_mcp_trial as mcp  # noqa: E402
import provider_accounts  # noqa: E402

CLAUDE_EXPECTED = "eval-claude@example.com"   # A made-up Claude account, as a kit names the real one.
NAMED = {provider_accounts.CODEX_ACCOUNT_ENV: account_fixtures.CODEX_EXPECTED,
         provider_accounts.CLAUDE_ACCOUNT_ENV: CLAUDE_EXPECTED}
# What the allowlist exists to drop: a route to another endpoint and a provider credential.
DROPPED = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "ANTHROPIC_API_KEY": "sk-fixture-not-a-key"}


class ExpectedAccountsReachTheChild(unittest.TestCase):
    def test_every_account_name_provider_accounts_defines_is_forwarded(self):
        names = {value for key, value in vars(provider_accounts).items()
                 if key.endswith("_ACCOUNT_ENV") and isinstance(value, str)}
        self.assertTrue(names)
        self.assertEqual(set(mcp.EXPECTED_ACCOUNT_ENV), names)

    def test_the_trial_environment_keeps_both_accounts_and_still_drops_routes_and_credentials(self):
        with mock.patch.dict(os.environ, dict(NAMED, **DROPPED)):
            environment = mcp.clean_environment()
        for name, value in NAMED.items():
            self.assertEqual(environment.get(name), value, name)
        for name in DROPPED:
            self.assertNotIn(name, environment)

    def test_an_unnamed_account_stays_unnamed(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in NAMED:
                os.environ.pop(name, None)
            environment = mcp.clean_environment()
        for name in NAMED:
            self.assertNotIn(name, environment)

    def test_a_real_child_started_with_it_reads_both(self):
        with mock.patch.dict(os.environ, NAMED):
            environment = mcp.clean_environment()
        code = "import json, os, sys; json.dump({k: os.environ.get(k) for k in sys.argv[1:]}, sys.stdout)"
        done = subprocess.run([sys.executable, "-I", "-c", code, *NAMED], env=environment,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), NAMED)


ACCOUNTS = (("codex", provider_accounts.CODEX_ACCOUNT_ENV), ("claude", provider_accounts.CLAUDE_ACCOUNT_ENV))


class TheWindowsSendCarriesBoth(unittest.TestCase):
    SEND = ROOT / "scripts/angelo/gcp_windows_validate.sh"
    MACHINE = ROOT / "scripts/angelo/windows_final_runs.ps1"

    def test_the_send_writes_each_account_key_on_every_send_after_the_dry_listing(self):
        text = self.SEND.read_text()
        dry = text.index('DRY RUN: nothing is uploaded or started.')
        for key, env in ACCOUNTS:
            line = re.search(r'EXTRA_META\+=",angelo-bench-%s-account=\$%s_account"' % (key, key), text)
            self.assertIsNotNone(line, key)
            self.assertGreater(line.start(), dry, "%s: the dry run's listing must never print the account" % key)
            self.assertIn('${%s' % env, text)

    def test_the_machine_exports_each_sent_account_to_its_runs(self):
        text = self.MACHINE.read_text()
        for key, env in ACCOUNTS:
            meta = re.search(r"\$(\w+) = \"\$\(Get-Meta 'angelo-bench-%s-account' ''\)\"\.Trim\(\)" % key, text)
            self.assertIsNotNone(meta, key)
            self.assertRegex(text, r"if \(\$%s\) \{ \$env:%s = \$%s \}" % (meta.group(1), env, meta.group(1)))
            # Top-level, before the -Session branch: the desktop session's own run of the script sets it too.
            self.assertLess(meta.start(), text.index("if ($Session) {"), key)


if __name__ == "__main__":
    unittest.main()
