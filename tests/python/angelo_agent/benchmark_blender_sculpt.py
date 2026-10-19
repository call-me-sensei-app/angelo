# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native 32-dab batching vs individual dispatch; no inference or MCP benchmark."""
import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import numpy as np

assert '--factory-startup' in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-sculpt-benchmark'))
OUT.mkdir(parents=True, exist_ok=True)
from angelo_agent.blender_tools import BlenderExecutor
report = {'paid_requests':0, 'description':'Same 32 control points/radius/pressure, one native pass vs 32 local tool dispatches; no network latency included'}


def main():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        (OUT/'recovery').mkdir(exist_ok=True)
        bpy.context.preferences.filepaths.temporary_directory = str(OUT/'recovery')
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=512, ring_count=256)
        obj = bpy.context.active_object
        obj.name = 'Native Sculpt Benchmark'
        executor = BlenderExecutor(window)
        state = executor.execute('angelo_inspect_sculpt', {'object':obj.name,'probes':[]})
        prepared = executor.execute('angelo_prepare_sculpt', {'object':obj.name,'mesh_revision':state['mesh_revision'],'add_levels':0})
        bpy.ops.ed.undo_push(message='Prepared benchmark')
        baseline = np.empty(len(obj.data.vertices)*3, dtype=np.float32)
        obj.data.vertices.foreach_get('co', baseline)
        paths = []
        for row in range(4):
            for column in range(8):
                x, z = -.45 + column * .9/7, -.3 + row * .2
                paths.append([{'position':[x,-math.sqrt(1-x*x-z*z),z],'pressure':.7}])
        args = {'object':obj.name,'mesh_revision':prepared['verified']['mesh_revision'],'brush':'DRAW',
                'radius':.18,'strength':.15,'direction':'ADD','symmetry':'NONE','normal':[0,-1,0],
                'front_faces_only':True,'spacing':.2,'paths':paths}
        batch = executor.execute('angelo_sculpt_batch', args)
        assert batch['changed_points'] > 0
        bpy.ops.ed.undo()
        obj = window.scene.objects['Native Sculpt Benchmark']
        restored = np.empty_like(baseline)
        obj.data.vertices.foreach_get('co', restored)
        assert np.array_equal(restored, baseline), 'Undo must restore the benchmark baseline'
        state = executor.execute('angelo_inspect_sculpt', {'object':obj.name,'probes':[]})
        total, brush_total = 0, 0
        for path in paths:
            result = executor.execute('angelo_sculpt_batch', args | {'paths':[path],'mesh_revision':state['mesh_revision']})
            assert result['success']
            total += result['native_exec_ms']
            brush_total += result['native_brush_ms']
            state = result['verified']
        # Reuse the same baseline for eight different local passes versus one
        # native program. Both reproject after each pass; no provider is involved.
        for _ in range(32):
            bpy.ops.ed.undo()
        obj = window.scene.objects['Native Sculpt Benchmark']
        restored = np.empty_like(baseline)
        obj.data.vertices.foreach_get('co',restored)
        assert np.array_equal(restored,baseline)
        steps = []
        for index in range(8):
            step = {k:v for k,v in args.items() if k not in {'object','mesh_revision'}}
            step['paths'] = paths[index*4:index*4+4]
            step['brush'] = 'DRAW' if index%2==0 else 'INFLATE'
            steps.append(step)
        state = executor.execute('angelo_inspect_sculpt',{'object':obj.name,'probes':[]})
        program = executor.execute('angelo_sculpt_program',{'object':obj.name,'mesh_revision':state['mesh_revision'],'steps':steps})
        assert program.get('success'), program
        programmed = np.empty_like(baseline)
        obj.data.vertices.foreach_get('co',programmed)
        bpy.ops.ed.undo()
        obj = window.scene.objects['Native Sculpt Benchmark']
        obj.data.vertices.foreach_get('co',restored)
        assert np.array_equal(restored,baseline), 'Whole program must have one Undo'
        state = executor.execute('angelo_inspect_sculpt',{'object':obj.name,'probes':[]})
        separate = 0
        for step in steps:
            result = executor.execute('angelo_sculpt_batch',dict(step,object=obj.name,mesh_revision=state['mesh_revision']))
            assert result.get('success'),result
            separate += result['native_exec_ms']
            state = result['verified']
        individual_geometry = np.empty_like(baseline)
        obj.data.vertices.foreach_get('co',individual_geometry)
        report['program'] = {'passes':8,'dabs':32,'total_ms':program['native_exec_ms'],
                             'native_brush_ms':program['native_brush_ms'],'separate_passes_ms':round(separate,3),
                             'local_speedup':round(separate/program['native_exec_ms'],2),'one_undo_verified':True,
                             'geometry_max_coordinate_difference':float(np.max(np.abs(programmed-individual_geometry)))}
        report.update(success=True, vertices=len(obj.data.vertices), dabs=32,
                      batched_total_ms=batch['native_exec_ms'], batched_brush_ms=batch['native_brush_ms'],
                      individual_total_ms=round(total,3), individual_brush_ms=round(brush_total,3),
                      local_batch_speedup=round(total/batch['native_exec_ms'],2), one_undo_verified=True)


def run():
    try:
        main()
    except Exception:
        report.update(success=False,error=traceback.format_exc())
    (OUT/'benchmark.json').write_text(json.dumps(report,indent=2))
    print('ANGELO_SCULPT_BENCHMARK', json.dumps(report), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
