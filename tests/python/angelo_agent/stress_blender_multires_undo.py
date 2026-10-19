# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Concurrent factory fixtures reproduce compression/Undo publication races.

Host Python: --blender /absolute/path/to/Angelo [--output DIR] (default: a new temp folder)
Runs six independent processes by default. This is correctness stress, not a
performance benchmark. Each fixture keeps its existing geometry/mask assertions.
No provider, login, network, user project or original preference is accessed.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import gui_launch

CRITICAL_MULTIRES_CHECKS = (
    'MULTIRES_GRAB_0.0_one_undo_restores_program',
    'MULTIRES_GRAB_0.07_one_undo_restores_program',
    'MULTIRES_GRAB_0.07_one_redo_restores_program',
    'MULTIRES_MASK_DRAW_one_undo_restores_program',
    'MULTIRES_DRAW_MASK_one_undo_restores_program',
    'MULTIRES_late_failure_one_undo_restores_program',
    'MULTIRES_ordinary_artist_stroke_one_undo',
)


def _read_result(report, code, log):
    error = None
    try:
        value = json.loads(report.read_text())
        if not isinstance(value, dict) or not isinstance(value.get('checks'), dict):
            raise ValueError('Expected a report object with named checks')
        checks = value['checks']
        if any(type(name) is not str or type(passed) is not bool for name, passed in checks.items()):
            raise ValueError('Named checks must contain boolean results')
    except (OSError, ValueError) as exc:
        value, checks, error = {}, {}, str(exc)
    missing = [name for name in CRITICAL_MULTIRES_CHECKS if name not in checks]
    assertions = any(marker in log for marker in ('BLI_assert', 'Assertion failed', 'Assertion `'))
    return {'success': code == 0 and value.get('success') is True and not missing
            and bool(checks) and all(checks.values()) and type(value.get('paid_requests')) is int
            and value.get('paid_requests') == 0
            and not assertions and error is None,
            'checks': len(checks), 'failed_checks': [name for name, passed in checks.items() if not passed],
            'missing_critical_checks': missing, 'native_assertions': assertions, 'report_error': error}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True)
    parser.add_argument('--output')
    parser.add_argument('--runs', type=int, default=6, choices=range(1, 13))
    parser.add_argument('--workers', type=int, default=6, choices=range(1, 7))
    args = parser.parse_args(argv)
    binary = Path(args.blender).resolve(strict=True)
    root = Path(__file__).resolve().parents[3]
    output = Path(args.output or tempfile.mkdtemp(prefix='angelo-multires-stress-')).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Never consume reports from a previous --output invocation, even after a clean early exit.
    invocation = Path(tempfile.mkdtemp(prefix='invocation-', dir=output))
    fixture = Path(__file__).with_name('test_blender_sculpt_engine_fixes.py')

    def run(index):
        folder = invocation / ('run-%02d' % index)
        folder.mkdir()
        for name in ('recovery', 'config', 'state', 'accounts'):
            (folder / name).mkdir()
        env = dict(os.environ, ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_QA_DIR=str(folder),
                   BLENDER_USER_CONFIG=str(folder / 'config'), ANGELO_STATE_DIR=str(folder / 'state'),
                   ANGELO_CONNECTIONS_DIR=str(folder / 'accounts'))
        # The fixture supports a developer-only filter. Correctness stress must run both surfaces.
        env.pop('ANGELO_ENGINE_SURFACE', None)
        command = [str(binary), '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                   *gui_launch.window_flags(input_isolation=False),
                   '--window-geometry', '60', '60', '1200', '850', '--python', str(fixture)]
        started = time.monotonic()
        with (folder / 'run.log').open('w') as log:
            try:
                process = subprocess.run(command, cwd=root, env=env, stdout=log,
                                         stderr=subprocess.STDOUT, timeout=180)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 'timeout'
            except OSError as exc:
                code = 'launch_error'
                log.write(str(exc))
        report = folder / 'sculpt-engine-fixes.json'
        log = (folder / 'run.log').read_text(errors='replace')
        result = {'run': index, 'exit_code': code, **_read_result(report, code, log),
                  'elapsed_seconds': round(time.monotonic() - started, 3),
                  'report': str(report), 'log': str(folder / 'run.log')}
        print(json.dumps(result), flush=True)
        return result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        runs = list(pool.map(run, range(1, args.runs + 1)))
    report = {'binary': str(binary), 'invocation': str(invocation), 'runs': runs, 'paid_requests': 0,
              'success': all(run['success'] for run in runs),
              'scope': 'Concurrent correctness stress; no throughput or latency claim'}
    (invocation / 'results.json').write_text(json.dumps(report, indent=2))
    (output / 'results.json').write_text(json.dumps(report, indent=2))
    raise SystemExit(0 if report['success'] else 1)


if __name__ == '__main__':
    main()
