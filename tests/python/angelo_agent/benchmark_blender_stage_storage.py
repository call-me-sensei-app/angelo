# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compare actual staged program latency using a fixed instruction work clock.

Run isolated factory-startup/background processes, sequentially, alternating
the preserved baseline and candidate binaries. --addon can select a preserved
copy of the baseline's Python package; C++ always comes from the tested binary.
--storage bundle measures the serialized fallback, native enables private Main.
No test removes verification, changes limits or accesses a provider.
"""

import argparse
import importlib.util
import itertools
import json
import os
from pathlib import Path
import sys

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
parser = argparse.ArgumentParser()
parser.add_argument("--addon", type=Path)
parser.add_argument("--storage", choices=("bundle", "native"), default="native")
parser.add_argument("--samples", type=int, default=7)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
assert 1 <= args.samples <= 30
if args.addon:
    assert "angelo_agent" not in sys.modules, "Package loaded before selecting the measured version"
    spec = importlib.util.spec_from_file_location("angelo_agent", args.addon / "__init__.py",
                                                submodule_search_locations=[str(args.addon)])
    package = importlib.util.module_from_spec(spec)
    sys.modules["angelo_agent"] = package
    spec.loader.exec_module(package)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures
import perf_counters
from angelo_agent import native_programs as native, program_storage as storage, scene_queries as queries
from angelo_agent.program_dependencies import SuspendedStage

OUT = fixtures.isolate(os.environ["ANGELO_QA_DIR"])
window = bpy.context.window
report = dict(fixtures.environment(), success=False, storage=args.storage,
              package=storage.__file__, samples=args.samples, cohorts={})


def instrument(calls):
    def configure(job):
        job.backend.memory_staging = args.storage == "native"
        suspend, resume = job.backend.suspend_stage, job.backend.resume_stage

        def measured_suspend(stage, **kwargs):
            if isinstance(stage, SuspendedStage):
                return suspend(stage, **kwargs)
            result, metrics = perf_counters.measure(lambda: suspend(stage, **kwargs))
            calls.append({"phase": "suspend", "native": getattr(result, "native_owner", None) is not None,
                          "bundle_bytes": Path(result.bundle).stat().st_size if result.bundle else 0,
                          "metrics": metrics})
            return result

        def measured_resume(stage):
            if not isinstance(stage, SuspendedStage):
                return resume(stage)
            result, metrics = perf_counters.measure(lambda: resume(stage))
            calls.append({"phase": "resume", "metrics": metrics})
            return result

        job.backend.suspend_stage, job.backend.resume_stage = measured_suspend, measured_resume
    return configure


try:
    for cohort in ("default", "many_objects", "deep_collections", "large_target"):
        target = fixtures.default_fixture(window) if cohort == "default" else fixtures.cohort_fixture(
            window, cohort, target_segments=315 if cohort == "large_target" else 128)
        handle = queries.object_handle(target)
        scene = window.scene.as_pointer()
        offsets, samples, all_calls = itertools.count(), [], []
        for index in range(args.samples + 1):
            before = set(native._main_ids())
            calls = []
            result, metrics = perf_counters.measure(lambda: fixtures.staged_program(
                window, target, handle, offset=next(offsets), on_job=instrument(calls),
                work_clock=fixtures.instruction_work_clock))
            assert set(native._main_ids()) == before and window.scene.as_pointer() == scene
            assert not native.runtime_is_staging() and not native.has_active_programs()
            if index:
                samples.append({"job": result, "metrics": metrics})
                all_calls.extend(calls)
        entry = {"samples": samples, "calls": all_calls,
                 "summary": perf_counters.summarize([sample["metrics"] for sample in samples]),
                 "fixture": fixtures.fixture_counts(window)}
        report["cohorts"][cohort] = entry
        print("STORAGE", cohort, entry["summary"]["wall_ms"]["median"], flush=True)
    report["success"] = True
finally:
    native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
