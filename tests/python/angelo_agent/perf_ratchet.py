# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Instruction-count ceilings that only move down.

``perf_ceilings.json`` holds one ceiling per measured operation and platform
(for example ``darwin-arm64``). A benchmark run writes a ``ratchet`` section
with the median retired-instruction count of each operation (see
``benchmark_fixtures.Ratchet``). This checker compares the two:

* ``regressed``: measured > ceiling * (1 + tolerance). The check fails.
* ``improved``: measured < ceiling * (1 - tolerance). ``--update`` lowers the
  ceiling to the measured value.
* ``within``: anything in between. Nothing changes, so noise inside the band
  can neither fail the check nor ratchet the ceiling down step by step.
* ``new``: measured, but no ceiling yet. ``--update`` records it.
* ``unmeasured``: a ceiling the run did not measure (for example a quick run).
  Reported, never a failure here and never removed.
* ``rejected``: the run measured the operation, but every sample had page-ins
  or swap (``perf_counters.rejection``), so it has no valid value.
* ``noisy``: the operation's own samples spread by more than a third of its
  tolerance (``NOISY_FRACTION``). One run cannot then tell a change from
  noise, so the absolute ceiling does not decide it: an interleaved ABAB
  against the last stamped build does (``perf_abab``), giving ``abab_pass``
  or ``abab_regressed``. Spread is the relative range of the run's valid
  samples; an operation that reports only a median has no spread and stays
  ceiling-gated. These rules were set on 2026-10-21, before any run used them.

Operations have a unit: instructions (default) or ``bytes``, an exact size
such as the bytes a capture writes. A byte ceiling is recorded with zero
tolerance and never counts as noisy.

The merge gate is stricter (``gate_problems``): ``test_blender_perf_ratchet``
runs every ratchet benchmark against the compiled app and fails on any
regressed, unmeasured or new operation. ``--stamp`` then records that passing
run in ``perf_ceilings.json`` as the platform's ``last_measured`` entry: date,
commit, binary and a digest of the measured sources (the add-on and the
benchmark harness, ``sources_digest``). The host suite, which CI runs on Linux
where no instruction counter exists, fails while that digest differs from the
checkout's, so a change to either cannot merge without a new measurement.

A ceiling only rises with ``--update --allow-raise``, an explicit decision that
belongs in review with the reason for the extra work. Counts are compared only
for the same platform key, because instruction counts differ between CPU
architectures and builds; a run for an unknown platform fails with exit 2.

Usage::

    python3 tests/python/angelo_agent/perf_ratchet.py RESULTS.json
    python3 tests/python/angelo_agent/perf_ratchet.py RESULTS.json --update
    python3 tests/python/angelo_agent/perf_ratchet.py RESULTS.json --update --allow-raise
    python3 tests/python/angelo_agent/perf_ratchet.py PERF-RATCHET-RECEIPT.json --stamp

``RESULTS.json`` is a benchmark report (its ``ratchet`` section is used) or a
bare ``{"platform": ..., "operations": {...}}`` object. Exit codes: 0 pass,
1 regression (or a refused update), 2 unusable input.
"""

import argparse
import copy
import datetime
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CEILINGS_PATH = HERE / "perf_ceilings.json"
METRIC = "instructions"
DEFAULT_TOLERANCE = 0.05
MAX_TOLERANCE = 0.5
GATE_SUITE = "test_blender_perf_ratchet"
# What the ceilings measure besides the add-on itself: the harness that defines the operations.
MEASURED_HARNESS = ("perf_counters.py", "benchmark_fixtures.py", "benchmark_blender_agent_execution.py",
                    "benchmark_blender_snapshot_cost.py", "benchmark_blender_stage_compression.py",
                    "benchmark_blender_view_scene_check.py", "benchmark_blender_long_session.py",
                    "benchmark_blender_session_journal.py", "snapshot_fixtures.py", "benchmark_blender_native_reads.py",
                    "bench_native_reads.py", "bench_native_reads_paths.py", "heavy_scene_blend.py", "heavy_plan.py",
                    "heavy_scene.py", "castle_kit.py", "scenario_build_kit.py", "benchmark_blender_chat_waiting.py",
                    "snapshot_paired.py")
GATE_ADVICE = {"regressed": "find the extra work, or raise with --update --allow-raise and say why in review",
               "abab_regressed": "the interleaved ABAB against the last stamped build found more work; find it",
               "noisy": "its spread exceeds a third of its tolerance; decide it with an interleaved ABAB against "
                        "the last stamped build (test_blender_perf_ratchet --baseline-binary, or perf_abab.py)",
               "rejected": "every sample had page-ins or swap; rerun on a quiet machine with free memory",
               "unmeasured": "a ceilinged operation was not measured; run every benchmark in full",
               "new": "record it with --update in the same change"}
UNITS = ("instructions", "bytes")
QUIET_LOAD_DEFAULT = 2.0   # perf_counters.QUIET_LOAD (not imported: this module stays importable without ctypes counters)
NOISY_FRACTION = 1 / 3


class RatchetError(ValueError):
    """Input that cannot be compared; never treated as a pass."""


def _count(value, where):
    if type(value) is not int or value <= 0:
        raise RatchetError(where + " must be a positive integer instruction count")
    return value


def _tolerance(value, where):
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= MAX_TOLERANCE:
        raise RatchetError(where + " tolerance must be between 0 and %g" % MAX_TOLERANCE)
    return float(value)


def load_results(value):
    """Validated ``(platform, {operation: instructions})`` from a report or ratchet section."""
    section = value.get("ratchet", value) if isinstance(value, dict) else None
    if not isinstance(section, dict) or section.get("metric", METRIC) != METRIC:
        raise RatchetError("Results need a ratchet section with median instruction counts")
    platform, operations = section.get("platform"), section.get("operations")
    if not isinstance(platform, str) or not platform:
        raise RatchetError("Results must name their platform key")
    if not isinstance(operations, dict) or not operations:
        raise RatchetError("Results contain no measured operations; instruction counters may be unavailable")
    return platform, {str(name): _count(count, "Result " + str(name)) for name, count in operations.items()}


def load_details(value):
    """Per-operation spread, unit and rejection from a results section (all optional, older runs have none)."""
    section = value.get("ratchet", value) if isinstance(value, dict) else {}
    details = {}
    for key in ("spread", "units", "rejected"):
        entry = section.get(key) or {}
        if not isinstance(entry, dict):
            raise RatchetError("Results' %s must be an object" % key)
        details[key] = entry
    for name, unit in details["units"].items():
        if unit not in UNITS:
            raise RatchetError("Result %s has an unknown unit %r" % (name, unit))
    return details


def empty_ceilings():
    return {"version": 1, "metric": METRIC, "default_tolerance": DEFAULT_TOLERANCE, "platforms": {}}


def load_ceilings(value):
    """Validate the ceilings document; returns it unchanged."""
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("metric") != METRIC:
        raise RatchetError("Ceilings must be version 1 instruction ceilings")
    _tolerance(value.get("default_tolerance"), "Default")
    platforms = value.get("platforms")
    if not isinstance(platforms, dict):
        raise RatchetError("Ceilings need a platforms object")
    for platform, entry in platforms.items():
        operations = entry.get("operations") if isinstance(entry, dict) else None
        if not isinstance(operations, dict):
            raise RatchetError("Platform " + str(platform) + " needs an operations object")
        for name, ceiling in operations.items():
            if not isinstance(ceiling, dict):
                raise RatchetError("Ceiling " + str(name) + " must be an object")
            _count(ceiling.get("ceiling"), "Ceiling " + str(name))
            if "tolerance" in ceiling:
                _tolerance(ceiling["tolerance"], "Ceiling " + str(name))
            if ceiling.get("unit", "instructions") not in UNITS:
                raise RatchetError("Ceiling " + str(name) + " has an unknown unit")
    return value


def compare(ceilings, platform, measured, details=None, abab=None):
    """Classify every operation; pure function used by the CLI and tests.

    ``details`` is ``load_details`` of the run; ``abab`` maps an operation to
    its ``perf_abab`` verdict ("pass" or "regressed") for noisy operations.
    """
    details, abab = details or {}, abab or {}
    spreads, units, rejected = details.get("spread", {}), details.get("units", {}), details.get("rejected", {})
    default = ceilings["default_tolerance"]
    known = ceilings["platforms"].get(platform, {}).get("operations", {})
    rows = []
    for name in sorted(set(known) | set(measured) | set(rejected)):
        entry, value, unit = known.get(name), measured.get(name), units.get(name, "instructions")
        if entry is not None and value is not None and entry.get("unit", "instructions") != unit:
            raise RatchetError("Operation %s was measured in %s but its ceiling is in %s"
                               % (name, unit, entry.get("unit", "instructions")))
        if value is None and name in rejected:
            rows.append({"operation": name, "status": "rejected", "validity": rejected[name]})
            continue
        if entry is None:
            rows.append(dict({"operation": name, "status": "new", "measured": value},
                             **({"unit": unit} if unit != "instructions" else {})))
            continue
        ceiling, tolerance = entry["ceiling"], entry.get("tolerance", default)
        row = {"operation": name, "ceiling": ceiling, "tolerance": tolerance, "measured": value}
        if unit != "instructions":
            row["unit"] = unit
        if value is None:
            row["status"] = "unmeasured"
        else:
            row["ratio"] = round(value / ceiling, 5)
            row["status"] = ("regressed" if value > ceiling * (1 + tolerance)
                             else "improved" if value < ceiling * (1 - tolerance) else "within")
            spread = spreads.get(name)
            if isinstance(spread, (int, float)):
                row["spread"] = spread
                if unit == "instructions" and spread > tolerance * NOISY_FRACTION:
                    row["ceiling_status"] = row["status"]
                    verdict = abab.get(name)
                    row["status"] = {"pass": "abab_pass", "regressed": "abab_regressed"}.get(verdict, "noisy")
        rows.append(row)
    return rows


def update(ceilings, platform, rows, *, allow_raise=False, source=""):
    """A new ceilings document: lower improvements, add new operations, raise only if allowed.

    Raises RatchetError when a regression would need a raise that was not allowed,
    so a partial update is never written.
    """
    regressed = [row["operation"] for row in rows if row["status"] in {"regressed", "abab_regressed"}]
    if regressed and not allow_raise:
        raise RatchetError("Refusing to raise ceilings without --allow-raise: " + ", ".join(regressed))
    result = copy.deepcopy(ceilings)
    entry = result["platforms"].setdefault(platform, {"operations": {}})
    operations = entry["operations"]
    for row in rows:
        if row["status"] in {"improved", "new", "regressed", "abab_regressed"} and row.get("measured") is not None:
            previous = operations.get(row["operation"], {})
            extra = {"unit": row["unit"], "tolerance": 0.0} if row.get("unit") == "bytes" and not previous else {}
            operations[row["operation"]] = dict(previous, ceiling=row["measured"], **extra)
    entry["operations"] = dict(sorted(operations.items()))
    if source:
        entry["updated_from"] = source
    return result


def measured_sources(root=ROOT):
    """Every file whose change can move a ceiling, in a stable order (C++ is the binary's, not listed)."""
    addon = Path(root) / "scripts/addons_core/angelo_agent"
    files = sorted(path for path in addon.rglob("*.py") if "__pycache__" not in path.parts)
    return files + [Path(root) / "tests/python/angelo_agent" / name for name in MEASURED_HARNESS]


def sources_digest(root=ROOT):
    """SHA-256 over the measured sources' paths and bytes (line endings normalized)."""
    digest = hashlib.sha256()
    for path in measured_sources(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


LC_UUID = 0x1B
CPU_TYPE_ARM64 = 0x0100000C


def binary_uuid(path):
    """The Mach-O linker UUID (LC_UUID) of an executable, or None (not Mach-O, or none recorded).

    Code signing rewrites the file, so a signed release app's SHA-256 differs from the build that was measured,
    but not its UUID: the stamp records both, and an ABAB baseline may match either (perf_abab). A universal
    binary's arm64 slice is read, else its first.
    """
    try:
        with open(path, "rb") as stream:
            head = stream.read(8)
            offset = 0
            if head[:4] == b"\xca\xfe\xba\xbe":   # Fat header, big-endian: nfat_arch, then (cputype, subtype, offset, size, align).
                count = struct.unpack(">I", head[4:8])[0]
                slices = [struct.unpack(">iiIII", stream.read(20)) for _ in range(min(count, 16))]
                chosen = next((item for item in slices if item[0] == CPU_TYPE_ARM64), slices[0] if slices else None)
                if chosen is None:
                    return None
                offset = chosen[2]
            stream.seek(offset)
            header = stream.read(32)
            if header[:4] != b"\xcf\xfa\xed\xfe":   # MH_MAGIC_64, little-endian.
                return None
            ncmds, sizeofcmds = struct.unpack("<II", header[16:24])
            commands = stream.read(min(sizeofcmds, 1 << 22))
    except OSError:
        return None
    position = 0
    for _ in range(ncmds):
        if position + 8 > len(commands):
            return None
        cmd, size = struct.unpack("<II", commands[position:position + 8])
        if cmd == LC_UUID and size >= 24:
            return str(uuid.UUID(bytes=commands[position + 8:position + 24]))
        if size < 8:
            return None
        position += size
    return None


def gate_problems(rows):
    """Why a merge-gate run fails: any regressed, unmeasured or new operation (empty when it passes)."""
    problems = []
    for status, advice in GATE_ADVICE.items():
        names = [row["operation"] for row in rows if row["status"] == status]
        if names:
            problems.append("%s: %s (%s)" % (status, ", ".join(names), advice))
    return problems


def stamp(ceilings, receipt, *, root=ROOT, today=None):
    """A new ceilings document recording a passing gate receipt as the platform's last measurement."""
    if receipt.get("suite") != GATE_SUITE or receipt.get("success") is not True:
        raise RatchetError("Only a passing %s receipt can be stamped" % GATE_SUITE)
    platform, measured = load_results(receipt)
    if platform not in ceilings["platforms"]:
        raise RatchetError("No ceilings for platform %s; record them with --update first" % platform)
    rows = compare(ceilings, platform, measured, load_details(receipt), receipt.get("abab_verdicts"))
    problems = gate_problems(rows)
    if problems:
        raise RatchetError("The receipt does not pass against these ceilings: " + "; ".join(problems))
    current = sources_digest(root)
    if receipt.get("sources_sha256") != current:
        raise RatchetError("The receipt measured other sources than this checkout; rerun %s" % GATE_SUITE)
    highest = max(rows, key=lambda row: row["ratio"])
    result = copy.deepcopy(ceilings)
    last = {"date": today or datetime.date.today().isoformat(), "commit": receipt.get("commit"),
            "binary_sha256": receipt.get("binary_sha256"), "binary_uuid": receipt.get("binary_uuid"),
            "sources_sha256": current, "operations": len(rows),
            "highest_ratio": {"operation": highest["operation"], "ratio": highest["ratio"]}}
    spreads = {row["operation"]: row["spread"] for row in rows if "spread" in row}
    if spreads:  # Each operation's own spread in this run, for the next noisy-or-not comparison.
        last["spreads"] = dict(sorted(spreads.items()))
        last["abab_decided"] = sorted(row["operation"] for row in rows if row["status"] == "abab_pass")
    if receipt.get("loads"):
        last["loads"] = receipt["loads"]
    if receipt.get("max_load") not in (None, QUIET_LOAD_DEFAULT):   # A recorded exception to the quiet-machine rule.
        last["max_load"], last["conditions"] = receipt["max_load"], receipt.get("conditions")
    result["platforms"][platform]["last_measured"] = last
    return result


def _read_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RatchetError("Cannot read %s %s: %s" % (what, path, error)) from error


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check benchmark instruction counts against ratcheting ceilings.")
    parser.add_argument("results", help="benchmark results.json (its ratchet section) or a bare ratchet object")
    parser.add_argument("--ceilings", default=str(CEILINGS_PATH))
    parser.add_argument("--update", action="store_true", help="lower improved ceilings and record new operations")
    parser.add_argument("--allow-raise", action="store_true", help="with --update, also raise regressed ceilings")
    parser.add_argument("--stamp", action="store_true",
                        help="record a passing %s receipt as the last measurement" % GATE_SUITE)
    args = parser.parse_args(argv)
    if args.allow_raise and not args.update:
        parser.error("--allow-raise only applies together with --update")
    if args.stamp and args.update:
        parser.error("--stamp records a passing run; update the ceilings first, then rerun the gate")
    try:
        results = _read_json(args.results, "results")
        if args.stamp:
            path = Path(args.ceilings)
            stamped = stamp(load_ceilings(_read_json(path, "ceilings")), results)
            path.write_text(json.dumps(stamped, indent=2) + "\n", encoding="utf-8")
            print("Stamped", path, json.dumps(stamped["platforms"][results["ratchet"]["platform"]]["last_measured"]))
            return 0
        platform, measured = load_results(results)
        path = Path(args.ceilings)
        ceilings = load_ceilings(_read_json(path, "ceilings")) if path.exists() else empty_ceilings()
        if not args.update and platform not in ceilings["platforms"]:
            raise RatchetError("No ceilings for platform %s; record them with --update" % platform)
        rows = compare(ceilings, platform, measured, load_details(results), results.get("abab_verdicts"))
        for row in rows:
            print(json.dumps(row, sort_keys=True))
        if args.update:
            updated = update(ceilings, platform, rows, allow_raise=args.allow_raise, source=Path(args.results).name)
            path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
            print("Updated", path)
            return 0
    except RatchetError as error:
        print("perf_ratchet:", error, file=sys.stderr)
        return 1 if "Refusing to raise" in str(error) else 2
    regressed = [row["operation"] for row in rows if row["status"] in {"regressed", "abab_regressed"}]
    if regressed:
        print("Instruction count regressed beyond tolerance: " + ", ".join(regressed), file=sys.stderr)
        return 1
    undecided = [row["operation"] for row in rows if row["status"] in {"noisy", "rejected"}]
    if undecided:
        print("Not decided by this run (noisy or rejected): " + ", ".join(undecided), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
