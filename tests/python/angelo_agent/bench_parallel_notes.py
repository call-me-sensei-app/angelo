# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The parallel report's reported-only lines (``bench_parallel_report.markdown``): memory (Amendments B2, E), how
the lead and workers communicated (B3), the worker-done events Angelo sent (G, and its timeout stop), the prompt
cache per chat, and each arm's closed checks side by side (the maintainer's report-format note of 2026-10-25: "Counts
per arm"). Never read by the verdict. Moved out of the report to keep it within its size limit.
"""
import json

import bench_memory
import bench_parallel_comms as comms


def reported(result):
    """Memory, communication and worker-done event lines, each preceded by a blank line."""
    lines = []
    machines = sorted({json.dumps(row.get("machine"), sort_keys=True) for rows in result["arms"].values()
                       for row in rows if row.get("memory_sampled")})
    lines += ["", "Memory (reported only, never in the verdict; phys_footprint of the lead and every worker Blender, "
              "sampled every 2 s from outside the app; growth from the send for the lead and from its ready for each "
              "worker, Amendment E), median per arm: " + "; ".join(
                  bench_memory.line(arm, rows) for arm, rows in result["arms"].items())
              + ". Measured on: %s." % (", ".join(machines) or "nothing sampled")]
    lines += ["", "Communication (reported only, never in the verdict; from each trial's Codex rollouts): " + "; ".join(
        "parallel %d: %s" % (row["trial"], comms.line(row["communication"])) for row in result["arms"]["parallel"])]
    lines += ["", "Worker-done events Angelo sent the lead (reported only, never in the verdict; Amendment G): " + "; ".join(
        "parallel %d: %s" % (row["trial"], row.get("worker_done_events")) for row in result["arms"]["parallel"])
        + ". Trials whose event fired after a timeout stop (reported only; Clarification of G): %d" % sum(
            1 for rows in result["arms"].values() for row in rows if (row.get("events_after_stop") or 0) > 0)]
    return lines


def cache(result):
    """The prompt cache per chat line, preceded by a blank line."""
    return ["", "Prompt cache per chat (first call's read; calls 2+ share), by trial: " + "; ".join(
                  "%s %d: %s" % (arm, row["trial"], ", ".join("%s/%s" % (t["first_call_cache_read"], t["calls_2plus_share"])
                                                             for t in row["cache_by_thread"]) or "no rollouts")
                  for arm, rows in result["arms"].items() for row in rows)]


def closed_checks(result):
    """Each arm's closed checks on the saved scene, side by side: passed of total per trial and which failed. The
    maintainer (2026-10-25): "Report each arm's checks passed (e.g. 281/297) and which failed, side by side; the
    all-pass/fail line stays but is no longer the headline. Ship rule unchanged, same for both arms." Reported only."""
    lines = ["", "Closed checks on the saved scene, per arm (reported only; the ship rule reads the grade):", "",
             "| trial | " + " | ".join(result["arms"]) + " |", "|---|" + "---|" * len(result["arms"])]
    trials = sorted({row["trial"] for rows in result["arms"].values() for row in rows})
    by_arm = {arm: {row["trial"]: row for row in rows} for arm, rows in result["arms"].items()}
    for trial in trials:
        cells = []
        for arm in result["arms"]:
            row = by_arm[arm].get(trial)
            cells.append("-" if row is None or row.get("checks_total") is None else "%s/%s" % (
                row.get("checks_passed"), row.get("checks_total")))
        lines.append("| %d | %s |" % (trial, " | ".join(cells)))
    for arm, rows in result["arms"].items():
        for row in rows:
            if row.get("checks_failed"):
                lines.append("- %s %d failed: %s" % (arm, row["trial"], "; ".join(row["checks_failed"])))
    return lines
