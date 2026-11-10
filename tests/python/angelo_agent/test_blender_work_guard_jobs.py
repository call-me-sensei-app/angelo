# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Restart to Update sees Blender's own jobs that no closed list names (BACKGROUND, one factory process).

Starts real window-manager jobs and asks ``_angelo_jobs.running_work`` and ``work_guard.reasons`` what a restart
would stop: an Ocean bake (``OBJECT_SIM_OCEAN``: a bake, "render") and an Alembic import started as a background job
(``ALEMBIC_IMPORT``: "task"; the build needs Alembic, as every release build has). Neither is in the closed lists the
guard used before (``work_guard._render`` records that it misses them). In background mode a started job keeps running
until the process ends (its end is handled by the event loop), so each check is deterministic; quitting stops the
jobs as any quit does. Both jobs also hold the parallel sessions' live view and bindings (``running_scene_jobs``,
which neither caller's closed list saw for the Ocean bake). Also reads the asset catalog checks (no catalog is
changed here: none is unsaved, and the "save with the file" setting is set and put back). No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).
import json
import os
from pathlib import Path
import sys

import addon_utils
import bpy

assert '--factory-startup' in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
OUT = Path(os.environ['ANGELO_QA_DIR']).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_DISABLE_KEYCHAIN='1')
from _bpy import _angelo_jobs  # noqa: E402
from angelo_agent import parallel_sessions, program_context, scene_jobs, ui, work_guard  # noqa: E402

REPORT = {'success': False, 'checks': {}, 'steps': [], 'paid_requests': 0, 'binary': bpy.app.binary_path}


def check(name, value):
    REPORT['checks'][name] = bool(value)
    assert value, name


def record(step):
    entry = {'step': step, 'running_work': [list(job) for job in _angelo_jobs.running_work()],
             'reasons': work_guard.reasons(), 'closed_lists': work_guard._render(bpy),
             'scene_jobs': list(_angelo_jobs.running_scene_jobs()), 'live_view_held': parallel_sessions._held_now(),
             'settle_held': program_context._jobs_run(), 'settle_closed_list': scene_jobs.settle_fallback(bpy)}
    REPORT['steps'].append(entry)
    return entry


def ocean_bake():
    bpy.ops.mesh.primitive_plane_add()
    plane = bpy.context.active_object
    ocean = plane.modifiers.new('Ocean', 'OCEAN')
    ocean.resolution, ocean.frame_start, ocean.frame_end = 8, 1, 120
    ocean.filepath = str(OUT / 'ocean-cache') + os.sep
    with bpy.context.temp_override(object=plane, active_object=plane):
        return bpy.ops.object.ocean_bake(modifier=ocean.name)


def import_in_background():
    """An Alembic import started as a background job, from a file this process exports first (blocking)."""
    bpy.ops.mesh.primitive_cube_add()
    path = str(OUT / 'cube.abc')
    bpy.ops.wm.alembic_export(filepath=path, selected=True, as_background_job=False)
    return bpy.ops.wm.alembic_import(filepath=path, as_background_job=True)


def main():
    ui._authentication.start = lambda *args, **kwargs: None
    addon_utils.enable('angelo_agent', default_set=False, persistent=False)
    idle = record('idle')
    check('idle_nothing_is_work', idle['running_work'] == [] and idle['reasons'] == [])
    check('idle_nothing_holds_the_scene', idle['scene_jobs'] == [] and not idle['live_view_held']
          and not idle['settle_held'])

    check('ocean_bake_started', ocean_bake() == {'FINISHED'})
    baking = record('ocean')
    check('ocean_bake_is_work', ['OBJECT_SIM_OCEAN', 'render'] in baking['running_work'])
    check('ocean_bake_stops_the_restart', baking['reasons'] == ['render'])
    check('ocean_bake_unseen_by_the_closed_lists', baking['closed_lists'] is False)
    # Its thread fills the cache the modifier's evaluation reads: the live view and a binding leave the scene alone.
    check('ocean_bake_holds_the_scene', 'OBJECT_SIM_OCEAN' in baking['scene_jobs'] and baking['live_view_held']
          and baking['settle_held'])
    check('ocean_bake_unseen_by_the_bindings_closed_list', baking['settle_closed_list'] is False)

    # USD's import runs as a job only from its file browser; Alembic's also from Python. Every release build has it.
    check('alembic_built', bpy.app.alembic.supported)
    check('import_started', import_in_background() == {'FINISHED'})
    importing = record('import')
    check('import_is_work', ['ALEMBIC_IMPORT', 'task'] in importing['running_work'])
    check('import_and_bake_both_named', importing['reasons'] == ['render', 'task'])
    check('import_holds_the_scene', 'ALEMBIC_IMPORT' in importing['scene_jobs'] and importing['settle_held'])

    check('catalogs_none_unsaved', _angelo_jobs.catalogs_unsaved() is False)
    before = _angelo_jobs.catalogs_save_with_file()
    check('catalogs_setting_set', _angelo_jobs.catalogs_save_with_file(not before) is before
          and _angelo_jobs.catalogs_save_with_file() is (not before))
    _angelo_jobs.catalogs_save_with_file(before)
    check('catalogs_setting_put_back', _angelo_jobs.catalogs_save_with_file() is before)
    REPORT['success'] = all(REPORT['checks'].values())


try:
    main()
finally:
    (OUT / 'work-guard-jobs.json').write_text(json.dumps(REPORT, indent=1, default=str))
    print('ANGELO_WORK_GUARD_JOBS', json.dumps({'success': REPORT['success']}), flush=True)
