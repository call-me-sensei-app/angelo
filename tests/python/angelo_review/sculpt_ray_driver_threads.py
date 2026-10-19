# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Queries from other threads and from Python drivers.

1. A Python thread: angelo_sculpt_query must refuse ("Query the sculpt surface on the main
   thread"). It does.
2. Python drivers that query the sculpt object while its geometry is re-evaluated each frame (a
   driven Displace modifier). The depsgraph runs drivers as TBB tasks, sometimes on the main
   thread itself (TaskPool::tbb_task_pool_work_and_wait), where BLI_thread_is_main() is true and
   the query is allowed while a worker rebuilds the same object's PBVH and its ray indices. On
   the change this crashes in LeafRayIndices::ensure_for (3 of 3 runs); without the re-evaluated
   object it runs, but the same driver is refused or answered depending on the thread TBB picked
   (1,513 and 1,202 of 2,400 refused in two runs). The previous build deadlocks in the same scene (drivers on workers
   wait for the GIL inside the query while the main thread waits for them), so this is not a
   regression of a working case; it shows the main-thread check is not "no concurrent writer".

  Angelo -b FIXTURE.blend --enable-autoexec --python sculpt_ray_driver_threads.py [-- nodisplace]

Run it under a time limit (`perl -e 'alarm 150; exec @ARGV' Angelo ...`): the previous build hangs. The fixture
is made by sculpt_ray_far_dump.py MAKE.
"""
import sys
import threading

import bpy

NO_DISPLACE = 'nodisplace' in sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else False
obj = bpy.data.objects['monkey']
bpy.context.view_layer.objects.active = obj
bpy.ops.object.mode_set(mode='SCULPT')

out = {}


def worker():
    try:
        out['answer'] = obj.angelo_sculpt_query((0, -3, 0), direction=(0, 1, 0), distance=10)
    except Exception as error:  # noqa: BLE001 - the refusal is the expected result
        out['refused'] = str(error).strip()


thread = threading.Thread(target=worker)
thread.start()
thread.join()
print('THREAD', out, flush=True)

calls = {'n': 0, 'refused': 0, 'ok': 0}


def probe(_frame):
    calls['n'] += 1
    try:
        hit = bpy.data.objects['monkey'].angelo_sculpt_query((0, -3, 0), direction=(0, 1, 0),
                                                             distance=10)
        calls['ok'] += 1
        return float(hit[1][1])
    except Exception:  # noqa: BLE001 - counted
        calls['refused'] += 1
        return 0.0


bpy.app.driver_namespace['probe'] = probe
for i in range(8):
    empty = bpy.data.objects.new('driven%d' % i, None)
    bpy.context.collection.objects.link(empty)
    curve = empty.driver_add('location', 0)
    curve.driver.expression = 'probe(frame)'
if not NO_DISPLACE:
    displace = obj.modifiers.new('D', 'DISPLACE')
    displace.strength = 0.0
    curve = displace.driver_add('strength')
    curve.driver.expression = 'frame * 0.0'
for frame in range(1, 301):
    bpy.context.scene.frame_set(frame)
print('DRIVER', calls, flush=True)
