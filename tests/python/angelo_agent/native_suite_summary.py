# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The native suite run's summary: ``summary.json``'s counts and ``summary.txt`` (run_native_suites.py).

Standard library only; pure functions over the runner's per-suite results.
"""

STATUS_ORDER = ("error", "timeout", "crashed", "failed", "passed")


def summarize(results, skipped, settings):
    counts = {status: sum(r["status"] == status for r in results) for status in reversed(STATUS_ORDER)}
    counts["skipped"] = len(skipped)
    summary = dict(settings, success=bool(results) and all(r["status"] == "passed" for r in results),
                   counts=counts, suites=results,
                   skipped=[{"name": s.name, "class": s.kind, "reason": reason} for s, reason in skipped])
    if settings["jobs"] > 1:
        summary["timing_note"] = "Durations measured with up to %d suites running concurrently." % settings["jobs"]
    return summary


def human_summary(summary):
    lines = ["%-8s %9s  %-10s  %s" % ("STATUS", "SECONDS", "MODE", "SUITE")]
    for result in summary["suites"]:
        lines.append("%-8s %9.1f  %-10s  %s" % (result["status"].upper(), result["duration_seconds"],
                                                result["mode"], result["name"]))
        lines += ["         - " + reason for reason in result["reasons"][:8]]
        if len(result["reasons"]) > 8:
            lines.append("         - ... %d more in summary.json" % (len(result["reasons"]) - 8))
        for path, tail in (result.get("crash_logs") or {}).items():
            lines.append("         crash log %s, last lines:" % path)
            lines += ["           | " + line for line in tail.splitlines()]
    counts = summary["counts"]
    lines.append("%d run: %s; %d skipped. %s" % (
        len(summary["suites"]), ", ".join("%d %s" % (counts[s], s) for s in reversed(STATUS_ORDER) if counts[s]),
        counts["skipped"], "PASSED" if summary["success"] else "FAILED"))
    if summary.get("timing_note"):
        lines.append(summary["timing_note"])
    lines.append("Output: " + summary["output"])
    return "\n".join(lines) + "\n"
