# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Launch live parallel-agent trials (BILLED): one GUI Angelo per case, a lead chat and the two workers it starts.

    python3 parallel_live_trial.py --authorize-billed-runs --binary .../Angelo --source-connections-dir DIR \\
        --connection-id ID --model MODEL --out DIR [--case bench_planter ...] [--timeout 900]

Each trial gets its own config, state, user folders and an isolated connection store holding only the one
subscription connection (``eval_live_agent.isolated_connection``: metadata only, never credentials), with
``ANGELO_ENABLE_PARALLEL_AGENTS=1``. The in-app driver is ``parallel_live_session.py``; cases and their closed checks
are ``parallel_live_cases.py``. Run only under Mac 1's billed lock (the caller's slot holds it). Prints one JSON line
per trial and writes ``parallel-live-summary.json``.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(HERE)]
import gui_launch  # noqa: E402
import parallel_live_cases as cases  # noqa: E402
import eval_diagnostics as diagnostics  # noqa: E402
from eval_live_agent import USER_FOLDERS, isolated_connection  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorize-billed-runs", action="store_true")
    parser.add_argument("--binary", required=True)
    parser.add_argument("--source-connections-dir", required=True)
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--codex-home", default="", help="ChatGPT runs: the harness's own Codex profile (never ~/.codex)")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--case", action="append", choices=sorted(cases.CASES))
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--out", required=True)
    diagnostics.add_arguments(parser)   # --debug-thinking, --ask-why-on-miss: diagnostic runs, never a proof.
    args = parser.parse_args(argv)
    diagnostics.check(parser, args)
    if not args.authorize_billed_runs:
        raise SystemExit("Live trials are billed: pass --authorize-billed-runs")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for index, case in enumerate(args.case or sorted(cases.CASES), start=1):
        folder = out / ("trial-%02d-%s" % (index, case))
        for name in ("config", "state", "qa", *("blender-user/" + n.lower() for n in USER_FOLDERS)):
            (folder / name).mkdir(parents=True)
        accounts = folder / "accounts"
        isolated_connection(args.source_connections_dir, args.connection_id, accounts, codex_home=args.codex_home)
        env = dict(os.environ, BLENDER_USER_CONFIG=str(folder / "config"), ANGELO_STATE_DIR=str(folder / "state"),
                   ANGELO_CONNECTIONS_DIR=str(accounts), ANGELO_QA_DIR=str(folder / "qa"),
                   ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_DISABLE_KEYCHAIN="1", ANGELO_EVAL_AUTHORIZED="1",
                   ANGELO_EVAL_CONNECTION_ID=args.connection_id, ANGELO_EVAL_MODEL=args.model,
                   ANGELO_EVAL_EFFORT=args.effort, ANGELO_EVAL_TASK_TIMEOUT=str(args.timeout),
                   ANGELO_PARALLEL_CASE=case, ANGELO_ENABLE_PARALLEL_AGENTS="1",
                   CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK="1", **diagnostics.environment(args))
        for name in USER_FOLDERS:
            env["BLENDER_USER_" + name] = str(folder / "blender-user" / name.lower())
        command = [args.binary, "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                   *gui_launch.window_flags("behind", True), "--window-geometry", "60", "60", "1400", "900",
                   "--python", str(HERE / "parallel_live_session.py")]
        started = time.monotonic()
        with (folder / "run.log").open("w") as log:
            try:
                code = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                      timeout=args.timeout + 180).returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        try:
            report = json.loads((folder / "qa" / "parallel-live.json").read_text())
        except (OSError, ValueError) as error:
            report = {"case": case, "success": False, "error": "no report: %s" % error}
        report.update(exit_code=code, elapsed_s=round(time.monotonic() - started, 1), folder=str(folder))
        results.append(report)
        print("PARALLEL-LIVE-TRIAL", json.dumps({k: report.get(k) for k in ("case", "success", "checks", "error",
                                                                           "elapsed_s")}), flush=True)
    (out / "parallel-live-summary.json").write_text(json.dumps(results, indent=2, default=str))
    return 0 if all(r.get("success") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
