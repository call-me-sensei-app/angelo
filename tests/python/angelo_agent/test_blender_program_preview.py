# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Isolated native preview pixels and artist-state restoration; no providers."""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy
import gpu

assert '--factory-startup' in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-program-preview-test'))
OUT.mkdir(parents=True,exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT='1',ANGELO_CONNECTIONS_DIR=str(OUT/'accounts'),ANGELO_STATE_DIR=str(OUT/'state'))
sys.path.insert(0,str(ROOT/'scripts/addons_core'))
from angelo_agent.program_preview import capture
from angelo_agent.references import ReferenceStore

report = {'checks':{},'paid_requests':0}


def check(name, condition):
    report['checks'][name] = bool(condition)
    assert condition, name


def run():
    scene = None
    try:
        window = bpy.context.window_manager.windows[0]
        area = next(a for a in window.screen.areas if a.type=='VIEW_3D')
        region = next(r for r in area.regions if r.type=='WINDOW')
        (OUT/'recovery').mkdir(exist_ok=True)
        bpy.context.preferences.filepaths.temporary_directory = str(OUT/'recovery')
        refs = ReferenceStore(OUT,'57d05753-0f4d-4b23-aa55-4d9d7d0c89d5')
        source = window.scene.objects['Cube']
        scene = bpy.data.scenes.new('Isolated program preview fixture')
        staged = source.copy()
        staged.data = source.data.copy()
        scene.collection.objects.link(staged)
        staged.scale = (2,.5,1)
        staged.show_in_front = True
        shading = area.spaces.active.shading
        shading.show_xray = True
        shading.show_backface_culling = True
        area.spaces.active.region_3d.use_clip_planes = True

        def state():
            return {'scene':window.scene.as_pointer(), 'active':window.view_layer.objects.active.as_pointer(),
                    'selection':[o.name for o in window.view_layer.objects if o.select_get()],
                    'source_vertices':[tuple(v.co) for v in source.data.vertices],
                    'source_scale':tuple(source.scale),'staged_front':staged.show_in_front,
                    'shading':(shading.type,shading.light,shading.color_type,shading.show_xray,shading.show_backface_culling,tuple(shading.background_color)),
                    'clip':area.spaces.active.region_3d.use_clip_planes,'overlays':area.spaces.active.overlay.show_overlays,
                    'gpu_viewport':tuple(gpu.state.viewport_get()),'gpu_scissor':tuple(gpu.state.scissor_get()),
                    'images':len(bpy.data.images)}

        with bpy.context.temp_override(window=window,area=area,region=region):
            before = state()
            result = capture(window,scene,refs)
            report['view'] = result['views'][0]
            check('actual_staged_pixels',Path(result['views'][0]['path']).stat().st_size > 1000)
            check('private_png_permissions',os.name == 'nt' or Path(result['views'][0]['path']).stat().st_mode & 0o777 == 0o600)
            check('artist_state_exactly_restored',state()==before)
            try:
                refs.observation_camera(result['views'][0]['id'])
            except ValueError:
                check('preview_is_not_mutation_target_authority',True)
            else:
                check('preview_is_not_mutation_target_authority',False)
            with patch.object(refs,'new_observation',side_effect=RuntimeError('injected reference failure')):
                try:
                    capture(window,scene,refs)
                except RuntimeError as error:
                    check('capture_failure_propagates',str(error)=='injected reference failure')
            check('failure_restores_artist_state',state()==before)
            try:
                capture(window,window.scene,refs)
            except RuntimeError:
                check('live_scene_cannot_be_presented_as_staging',True)
            else:
                check('live_scene_cannot_be_presented_as_staging',False)
            mesh = staged.data
            bpy.data.objects.remove(staged,do_unlink=True)
            bpy.data.meshes.remove(mesh)
            bpy.data.scenes.remove(scene)
            scene = None
            check('isolated_stage_cleanup',not any(s.name=='Isolated program preview fixture' for s in bpy.data.scenes))
        report['success'] = True
    except Exception:
        report.update(success=False,error=traceback.format_exc())
    (OUT/'results.json').write_text(json.dumps(report,indent=2))
    print('ANGELO_PROGRAM_PREVIEW',json.dumps(report),flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run,first_interval=1)
