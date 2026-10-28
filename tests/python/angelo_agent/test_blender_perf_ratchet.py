# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The instruction-count ratchet as a native merge gate (BACKGROUND, serial host driver).

Runs the eight ratchet benchmarks against the compiled app one after another,
each in its own ``--background --factory-startup`` process with the runner's
isolated environment and a fresh ``ANGELO_QA_DIR`` at a fixed depth
(``SHORT_ROOT``, ``native_suite_paths.short_temp_root()``; its report and log
are then kept in this suite's QA directory), merges their ``ratchet``
sections and compares them with ``perf_ceilings.json``. It passes only when no
operation regressed, every ceiling was measured and no operation lacks a
ceiling (``perf_ratchet.gate_problems``). The receipt
``fixture/perf-ratchet.json`` carries the merged counts, the comparison rows,
the binary's SHA-256 and the digest of the measured sources; after a pass,
``perf_ratchet.py <receipt> --stamp`` records it in ``perf_ceilings.json``,
which the host suite requires for the current sources.

Every benchmark process starts only once the 1-minute load average is below
2.0 (``perf_counters.wait_for_quiet``, up to ``--quiet-wait-s``); otherwise
the suite fails with the load it saw. Operations whose run spread exceeds a
third of their tolerance are ``noisy`` (``perf_ratchet``): with
``--baseline-binary`` (the last stamped build) the suite decides them with an
interleaved ABAB (``perf_abab``); without it they fail the gate with that
advice. The receipt keeps each operation's spread, validity, unit, source
benchmark and the values reported beside the ratcheted ones, never compared (gross
counts, the snapshot paired net).

Counters exist on macOS only (``proc_pid_rusage``); elsewhere the suite fails
closed with that reason. The runner starts serial suites alone, after the
parallel ones, as the benchmarks require::

    python3 tests/python/angelo_agent/run_native_suites.py --binary <Angelo> \\
        --include test_blender_perf_ratchet
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import native_suite_checks  # noqa: E402
import native_suite_paths  # noqa: E402
import perf_abab  # noqa: E402
import perf_counters  # noqa: E402
import perf_ratchet  # noqa: E402

# (benchmark, extra arguments, report file in its ANGELO_QA_DIR, completion marker)
BENCHMARKS = (
    ("benchmark_blender_agent_execution.py", (), "results.json", "EXECUTION_BENCHMARK_DONE"),
    # The deduplicated-storage phase reports storage, not ratchet operations.
    ("benchmark_blender_snapshot_cost.py", ("--dedup-mb", "0"), "snapshot-cost.json", "SNAPSHOT_BENCHMARK_DONE"),
    ("benchmark_blender_stage_compression.py", (), "stage-compression.json", None),
    ("benchmark_blender_view_scene_check.py", (), "view-scene-check.json", "VIEW_CHECK_BENCHMARK_DONE"),
    # Exits non-zero when per-edit cost grows over its 500-edit session (benchmark_blender_long_session).
    ("benchmark_blender_long_session.py", (), "long-session.json", "LONG_SESSION_BENCHMARK_DONE"),
    # Exits non-zero when a conversation save grows over its 500-edit session (benchmark_blender_session_journal).
    ("benchmark_blender_session_journal.py", (), "session-journal.json", "SESSION_JOURNAL_BENCHMARK_DONE"),
    # Every native read compared with stock Blender (bench_native_reads), on the heavy castle scene.
    ("benchmark_blender_native_reads.py", (), "native-reads.json", "NATIVE_READS_RATCHET_DONE"),
    # The chat's marble wait: per-frame and per-tick main-thread work while a turn plans.
    ("benchmark_blender_chat_waiting.py", (), "chat-waiting.json", "CHAT_WAITING_BENCHMARK_DONE"),
)
BENCHMARK_TIMEOUT = 900
# Snapshot storage anchors every ancestor directory of its folder on each write
# (checkpoint_filesystem), about 1.6 million instructions per extra path
# component per capture. Each benchmark therefore runs in a fresh folder at the
# depth of its own default (``/private/tmp/<name>`` on macOS, the only platform
# with counters), where the ceilings were recorded, and its files are moved into
# the suite's QA directory afterwards. Its temporary folder (stage bundles) is
# inside that folder, for the same reason.
SHORT_ROOT = native_suite_paths.short_temp_root()


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def commit():
    try:
        return subprocess.run(["git", "-C", str(perf_ratchet.ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=20).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


QUIET = {"limit": perf_counters.QUIET_LOAD, "timeout_s": 1200}
LOADS = {}  # script (or ABAB run label) -> 1-minute load average when it started


def run_benchmark(binary, out, script, extra, report, marker, label=None):
    """One benchmark process; returns (ratchet section, problem or None)."""
    kept = out / script[:-3]
    kept.mkdir(parents=True, exist_ok=True)
    quiet, load = perf_counters.wait_for_quiet(QUIET["limit"], QUIET["timeout_s"])
    LOADS[label or script] = load
    if not quiet:
        return None, "%s did not start: 1-minute load average %.2f stayed at or above %.1f for %d s" % (
            script, load, QUIET["limit"], QUIET["timeout_s"])
    qa = Path(tempfile.mkdtemp(prefix="angelo-perf-", dir=SHORT_ROOT))
    try:
        return _run_in(binary, qa, kept, script, extra, report, marker)
    finally:
        for name in (report, "benchmark.log"):
            if (qa / name).is_file():
                shutil.move(str(qa / name), str(kept / name))
        shutil.rmtree(qa, ignore_errors=True)


def _run_in(binary, qa, kept, script, extra, report, marker):
    command = [str(binary), "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
               "--python", str(HERE / script)] + (["--", *extra] if extra else [])
    # Stage bundles go to tempfile's folder: keep it at a fixed depth as well.
    (qa / "tmp").mkdir()
    env = dict(os.environ, ANGELO_QA_DIR=str(qa), TMPDIR=str(qa / "tmp"), TMP=str(qa / "tmp"), TEMP=str(qa / "tmp"))
    env.pop("ANGELO_JOURNAL_VERIFY", None)  # Benchmarks measure the shipped configuration (sessions.VERIFY_JOURNAL).
    started = time.monotonic()
    with open(qa / "benchmark.log", "w", encoding="utf-8") as stream:
        try:
            code = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                  timeout=BENCHMARK_TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            return None, "%s timed out after %ds" % (script, BENCHMARK_TIMEOUT)
    print("PERF_RATCHET_BENCHMARK", script, "exit", code, "%.1fs" % (time.monotonic() - started), flush=True)
    text = (qa / "benchmark.log").read_text(encoding="utf-8", errors="replace")
    findings = native_suite_checks.scan_log(text)  # The same native/Python failure markers as every suite.
    if code != 0 or findings or (marker and marker not in text):
        return None, "%s failed (exit %s%s); see %s" % (script, code, "".join(
            ", %s: %s" % (finding["kind"], finding["line"]) for finding in findings[:3]), kept / "benchmark.log")
    try:
        return json.loads((qa / report).read_text(encoding="utf-8"))["ratchet"], None
    except (OSError, ValueError, KeyError) as error:
        return None, "%s wrote no ratchet section: %s" % (script, error)


def merge_details(merged, section, script, sources):
    """Fold one benchmark's spread, validity, units, rejections and reported-only values into the receipt's section."""
    for key in ("spread", "validity", "units", "rejected", "gross", "reported"):
        merged.setdefault(key, {}).update(section.get(key) or {})
    for name in list(section.get("operations", {})) + list(section.get("rejected") or {}):
        sources[name] = script


def abab_for(out, baseline, candidate, receipt, noisy):
    """Decide ``noisy`` operations ({name: tolerance}) with perf_abab's interleaved runs."""
    entries = {entry[0]: entry for entry in BENCHMARKS}
    operations = {name: receipt["sources"][name] for name in noisy}

    def run(binary, script, label):
        _, extra, report, marker = entries[script][0:4]
        return run_benchmark(binary, out / "abab" / label, script, extra, report, marker, label="abab " + label)[0]
    return perf_abab.run_abab(run, baseline, candidate, sorted(set(operations.values())), operations, noisy)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--baseline-binary", help="the last stamped build, to decide noisy operations by ABAB")
    parser.add_argument("--baseline-approval", default="",
                        help="who approved a baseline that is not the stamped build (matched by SHA-256 or UUID), and why")
    parser.add_argument("--quiet-wait-s", type=int, default=QUIET["timeout_s"])
    parser.add_argument("--max-load", type=float, default=perf_counters.QUIET_LOAD,
                        help="1-minute load average each benchmark waits for (default %.1f); any other value is recorded "
                             "in the receipt and the stamp, with --conditions" % perf_counters.QUIET_LOAD)
    parser.add_argument("--conditions", default="", help="how the machine was used, recorded with a non-default --max-load")
    args = parser.parse_args(argv)
    if args.max_load != perf_counters.QUIET_LOAD and not args.conditions.strip():
        parser.error("a non-default --max-load needs --conditions (for example \"desktop app open\")")
    QUIET["timeout_s"], QUIET["limit"] = args.quiet_wait_s, args.max_load
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    binary = Path(args.binary)
    receipt = {"suite": perf_ratchet.GATE_SUITE, "success": False, "binary": str(binary),
               "binary_sha256": sha256(binary), "binary_uuid": perf_ratchet.binary_uuid(binary), "commit": commit(),
               "sources_sha256": perf_ratchet.sources_digest(),
               "problems": [], "loads": LOADS, "sources": {}, "max_load": args.max_load,
               "conditions": args.conditions or None}
    operations, platforms, details = {}, set(), {}
    if sys.platform != "darwin":
        receipt["problems"].append("Instruction counters are macOS-only (proc_pid_rusage); the ratchet cannot run here")
    else:
        for script, extra, report, marker in BENCHMARKS:
            section, problem = run_benchmark(binary, out, script, extra, report, marker)
            if problem:
                receipt["problems"].append(problem)
                continue
            platforms.add(section.get("platform"))
            duplicate = sorted(set(operations) & set(section.get("operations", {})))
            if duplicate:
                receipt["problems"].append("Operations measured twice: " + ", ".join(duplicate))
            operations.update(section.get("operations", {}))
            merge_details(details, section, script, receipt["sources"])
    if len(platforms) > 1:
        receipt["problems"].append("Benchmarks reported different platforms: " + ", ".join(sorted(map(str, platforms))))
    if operations and not receipt["problems"]:
        platform = platforms.pop()
        receipt["ratchet"] = dict({key: dict(sorted(value.items())) for key, value in details.items()},
                                  metric=perf_ratchet.METRIC, statistic="median", platform=platform,
                                  operations=dict(sorted(operations.items())))
        try:
            ceilings = perf_ratchet.load_ceilings(json.loads(perf_ratchet.CEILINGS_PATH.read_text(encoding="utf-8")))
            if platform not in ceilings["platforms"]:
                raise perf_ratchet.RatchetError("No ceilings for platform " + platform)
            measured, run_details = perf_ratchet.load_results(receipt)[1], perf_ratchet.load_details(receipt)
            rows = perf_ratchet.compare(ceilings, platform, measured, run_details)
            noisy = {row["operation"]: row["tolerance"] for row in rows if row["status"] == "noisy"}
            if noisy and args.baseline_binary:
                problem = perf_abab.baseline_problem(ceilings, platform, sha256(args.baseline_binary),
                                                     perf_ratchet.binary_uuid(args.baseline_binary), args.baseline_approval)
                receipt["abab_baseline"] = {"binary": args.baseline_binary, "sha256": sha256(args.baseline_binary),
                                            "uuid": perf_ratchet.binary_uuid(args.baseline_binary),
                                            "approval": args.baseline_approval or None}
                if problem:
                    receipt["problems"].append("ABAB refused: " + problem)
                else:
                    receipt["abab"] = abab_for(out, Path(args.baseline_binary), binary, receipt, noisy)
                    receipt["abab_verdicts"] = {name: entry["verdict"] for name, entry in receipt["abab"].items()}
                    rows = perf_ratchet.compare(ceilings, platform, measured, run_details, receipt["abab_verdicts"])
        except (OSError, ValueError) as error:
            receipt["problems"].append("Cannot compare with the ceilings: %s" % error)
        else:
            receipt["rows"] = rows
            receipt["problems"] += perf_ratchet.gate_problems(rows)
            for row in rows:
                print("PERF_RATCHET", json.dumps(row, sort_keys=True), flush=True)
    receipt["success"] = not receipt["problems"]
    (out / "perf-ratchet.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    for problem in receipt["problems"]:
        print("PERF_RATCHET_PROBLEM", problem, flush=True)
    if receipt["success"]:
        print("ANGELO_PERF_RATCHET_OK", len(operations), "operations within their ceilings; stamp with:",
              "python3 tests/python/angelo_agent/perf_ratchet.py", out / "perf-ratchet.json", "--stamp", flush=True)
    return 0 if receipt["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
