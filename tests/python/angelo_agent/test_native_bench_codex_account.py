# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows bench machine learns the expected ChatGPT account from the operator's kit, never from the repository.

provider_accounts no longer records the evaluation account (maintainer, 2026-11-11): every billed start reads it from
``ANGELO_TEST_CODEX_ACCOUNT`` or the opt-in's field, and refuses without one. The gates' billed runs start on a GCP
Windows machine (``windows_final_runs.ps1``: the sign-in check, then the measurement session), so the Mac's
``gcp_windows_validate.sh bench`` must hand the value over, as instance metadata, and the machine must put it in the
environment every one of its runs inherits. The host suite has no PowerShell: the scripts are read.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
ROOT = HERE.parents[2]
VALIDATE = ROOT / "scripts/angelo/gcp_windows_validate.sh"
FINAL_RUNS = ROOT / "scripts/angelo/windows_final_runs.ps1"
KEY = "angelo-bench-codex-account"
import provider_accounts  # noqa: E402


class HandOver(unittest.TestCase):
    def test_the_mac_side_requires_the_variable_and_sends_it_without_printing_it(self):
        text = VALIDATE.read_text(encoding="utf-8")
        bench = text[text.index("bench prep|dry|run"):]
        self.assertIn('"${%s:?' % provider_accounts.CODEX_ACCOUNT_ENV, bench)   # A send without it stops.
        sent = bench.index('EXTRA_META+=",%s=' % KEY)
        dry = bench.index('if [[ "${ANGELO_GCP_DRY_RUN:-0}" == "1" ]]')
        self.assertGreater(sent, bench.index("rm -rf \"$work\"; return 0", dry),
                           "added after the dry run's metadata listing, which would print the email")
        self.assertLess(sent, bench.index("redo bench"), "on the metadata the send writes")

    def test_the_machine_exports_it_to_every_run_before_any_starts(self):
        text = FINAL_RUNS.read_text(encoding="utf-8")
        found = re.search(r'Get-Meta \'%s\'' % KEY, text)
        self.assertIsNotNone(found)
        self.assertIn("$env:%s = " % provider_accounts.CODEX_ACCOUNT_ENV, text)
        export = text.index("$env:%s = " % provider_accounts.CODEX_ACCOUNT_ENV)
        for start in ("if ($SignIn) {", "if ($Session) {"):
            self.assertLess(export, text.index(start), "before the %s block" % start)
        self.assertGreater(export, text.index("function Get-Meta"))

    def test_neither_script_names_an_account(self):
        for path in (VALIDATE, FINAL_RUNS):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\b[0-9a-f]{8}\|[^\s\"']+@", text), path.name)


if __name__ == "__main__":
    unittest.main()
