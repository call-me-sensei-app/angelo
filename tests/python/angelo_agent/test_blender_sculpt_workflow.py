# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled sculpt programs, live PBVH, regions, working levels and reference metrics."""
import json,math,os,sys,tempfile,traceback
from pathlib import Path
import bpy
import numpy as np
assert '--factory-startup' in sys.argv
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/addons_core'))
OUT=Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(),'angelo-sculpt-workflow-qa'));OUT.mkdir(parents=True,exist_ok=True)
os.environ.update(ANGELO_CONNECTIONS_DIR=str(OUT/'accounts'),ANGELO_STATE_DIR=str(OUT/'state'),ANGELO_DISABLE_DEFAULT_CHAT='1')
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.references import ReferenceStore
from angelo_agent import sculpt
from angelo_agent.sculpt_workflow import resolve_region,regions,REGIONS_KEY,polygon_mask
report={'checks':{},'paid_requests':0,'brushes':{}}

def check(name,value):
    report['checks'][name]=bool(value)
    assert value,name

def points(obj):
    out=np.empty(len(obj.data.vertices)*3,dtype=np.float32);obj.data.vertices.foreach_get('co',out)
    return out.reshape(-1,3)

def action(brush='DRAW'):
    return {'brush':brush,'radius':.35,'strength':.22,'direction':'ADD','symmetry':'NONE','normal':[0,-1,0],
            'front_faces_only':True,'spacing':.2,'falloff':'SMOOTH','automasking':[],
            'paths':[[{'position':[-.12,-.98,.08],'pressure':.5,'radius_scale':.8,'tilt':[.2,.1]},
                      {'position':[.12,-.98,.08],'pressure':.8,'radius_scale':1.2,'tilt':[.4,.2]}]]}

def main():
    window=bpy.context.window_manager.windows[0];area=next(a for a in window.screen.areas if a.type=='VIEW_3D');region=next(r for r in area.regions if r.type=='WINDOW')
    with bpy.context.temp_override(window=window,area=area,region=region):
        (OUT/'recovery').mkdir(exist_ok=True);bpy.context.preferences.filepaths.temporary_directory=str(OUT/'recovery')
        bpy.context.preferences.edit.use_global_undo=True
        bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=96,ring_count=48)
        obj=bpy.context.active_object;obj.name='Workflow Sphere'
        attr=obj.data.attributes.new('.sculpt_face_set','INT','FACE')
        attr.data.foreach_set('value',np.array([2 if p.center.x>0 else 1 for p in obj.data.polygons],dtype=np.int32))
        executor=BlenderExecutor(window);executor.observation_store=ReferenceStore(OUT,'92447c82-6f6e-464b-8497-1d65b1bf1cb2')
        def inspect(target=obj.name): return executor.execute('angelo_inspect_sculpt',{'object':target,'probes':[]})
        state=inspect();executor.execute('angelo_prepare_sculpt',{'object':obj.name,'mesh_revision':state['mesh_revision'],'add_levels':0})
        observed=sculpt.snapshot(window,obj)
        check('bulk_positions_equal_rna_coordinates',np.array_equal(observed['positions'],points(obj)))
        packed=np.frombuffer(obj.data.angelo_sculpt_topology,dtype=np.int32)
        corners=np.empty(len(obj.data.loops),dtype=np.int32);obj.data.loops.foreach_get('vertex_index',corners)
        sizes=np.empty(len(obj.data.polygons),dtype=np.int32);obj.data.polygons.foreach_get('loop_total',sizes)
        offsets=packed[len(corners):]
        check('bulk_topology_equals_rna_indices_and_face_sizes',np.array_equal(packed[:len(corners)],corners) and offsets[0]==0 and np.array_equal(np.diff(offsets),sizes))
        saved=observed['positions'].copy()
        check('bulk_snapshot_owns_immutable_memory',not observed['positions'].flags.writeable)
        bpy.ops.ed.undo_push(message='Prepared workflow')
        for brush in ('CLAY_STRIPS','DRAW_SHARP','SCRAPE','ELASTIC_DEFORM','POSE'):
            before=points(obj).copy();args=action(brush)
            if brush in ('ELASTIC_DEFORM','POSE'):
                args['paths']=[[{'position':[.25,-.96,0],'pressure':1},{'position':[.33,-1.1,.1],'pressure':1}]]
            result=executor.execute('angelo_sculpt_batch',dict(args,object=obj.name,mesh_revision=inspect()['mesh_revision']))
            report['brushes'][brush]=result
            check(brush+'_deforms_native_geometry',result.get('success') and result['changed_points']>0 and np.max(np.abs(points(obj)-before))>1e-7)
            if brush in ('ELASTIC_DEFORM','POSE'):
                check(brush+'_back_faces_protected',np.max(np.abs(points(obj)[before[:,1]>.4]-before[before[:,1]>.4]))<1e-7)
            bpy.ops.ed.undo();obj=window.scene.objects['Workflow Sphere']
            check(brush+'_one_undo',np.array_equal(points(obj),before))
        check('owned_snapshot_survives_edits_and_undo',np.array_equal(observed['positions'],saved))
        check('queries_use_live_pbvh_without_secondary_index',isinstance(sculpt.surface(window,obj,sculpt.snapshot(window,obj)),sculpt.NativeSurface))
        before=points(obj).copy();state=inspect()
        result=executor.execute('angelo_sculpt_program',{'object':obj.name,'mesh_revision':state['mesh_revision'],
                                 'steps':[action('CLAY'),action('DRAW_SHARP'),action('SMOOTH')]})
        report['program']=result
        check('mixed_brush_program_changes_surface',result.get('success') and result['passes']==3 and result['changed_points']>0)
        bpy.ops.ed.undo();obj=window.scene.objects['Workflow Sphere']
        check('one_undo_restores_whole_program',np.array_equal(points(obj),before))
        # A later pass can fail against a surface moved away by an earlier pass.
        # Retain partial-change evidence and a single program Undo.
        state=inspect();before=points(obj).copy()
        pull=action('GRAB');pull.update(radius=.5,paths=[[{'position':[0,-1,0],'pressure':1},{'position':[0,-1.5,0],'pressure':1}]])
        tiny=action();tiny.update(radius=.03,paths=[[{'position':[0,-1,0],'pressure':1}]])
        partial=executor.execute('angelo_sculpt_program',{'object':obj.name,'mesh_revision':state['mesh_revision'],'steps':[pull,tiny]})
        report['partial_program']=partial
        check('late_program_failure_reports_partial_edit',partial.get('error') and partial.get('partial_changes_possible') and not np.array_equal(points(obj),before))
        bpy.ops.ed.undo();obj=window.scene.objects['Workflow Sphere']
        check('partial_program_one_undo',np.array_equal(points(obj),before))
        state=inspect();region_args={'object':obj.name,'mesh_revision':state['mesh_revision'],'name':'Right cheek','position':[.3,-.95,0],'radius':.45,'face_set':2}
        defined=executor.execute('angelo_sculpt_region',region_args)
        check('persistent_region_definition',defined['success'] and 'Right cheek' in regions(obj))
        bpy.ops.ed.undo_push(message='Saved cheek region')
        state=inspect();anchor=resolve_region(obj,sculpt.snapshot(window,obj),'Right cheek');before=points(obj).copy()
        args=action();args.update(region='Right cheek',radius=.45,paths=[[{'position':[0,0,0],'pressure':1}]])
        edited=executor.execute('angelo_sculpt_batch',dict(args,object=obj.name,mesh_revision=state['mesh_revision']))
        check('regional_brush_uses_face_set_mask',edited.get('success') and edited['changed_points']>0 and np.max(np.abs(points(obj)[before[:,0]<-.05]-before[before[:,0]<-.05]))<1e-7)
        moved=resolve_region(obj,sculpt.snapshot(window,obj),'Right cheek')
        check('landmark_follows_deformation',math.dist(anchor['position'],moved['position'])>1e-5)
        moving=action('GRAB');moving.update(region='Right cheek',radius=.3,strength=1.0,
                     paths=[[{'position':[0,0,0],'pressure':1},{'position':[0,-.25,0],'pressure':1}]])
        refine=action();refine.update(region='Right cheek',radius=.04,paths=[[{'position':[0,0,0],'pressure':1}]])
        before=points(obj).copy()
        followed=executor.execute('angelo_sculpt_program',{'object':obj.name,'mesh_revision':inspect()['mesh_revision'],'steps':[moving,refine]})
        check('program_follows_moved_region_between_passes',followed.get('success') and followed['changed_points']>0)
        bpy.ops.ed.undo();obj=window.scene.objects['Workflow Sphere']
        check('regional_program_single_undo',np.array_equal(points(obj),before))
        # The exact revision also binds region metadata, not just vertex positions.
        stale=inspect()['mesh_revision'];old=obj[REGIONS_KEY];data=json.loads(old);data['Right cheek']['radius']=.3;obj[REGIONS_KEY]=json.dumps(data)
        try: executor.execute('angelo_sculpt_batch',dict(args,object=obj.name,mesh_revision=stale))
        except RuntimeError: check('region_metadata_invalidates_pending_edits',True)
        else: check('region_metadata_invalidates_pending_edits',False)
        obj[REGIONS_KEY]=old
        # Native target-only silhouette must not leak changes to viewport or neighbor visibility.
        old_view=tuple(v for row in area.spaces.active.region_3d.view_matrix for v in row)
        old_display=obj.display_type
        baseline=executor.execute('angelo_sculpt_view',{'object':obj.name,'views':['FRONT'],'resolution':384})
        ref=baseline['views'][0]['id'];camera=executor.observation_store.observation_camera(ref)
        check('actual_silhouette_mask',camera['silhouette'].sum()>1000 and not camera['silhouette'][0].any())
        check('capture_restores_artist_view',old_view==tuple(v for row in area.spaces.active.region_3d.view_matrix for v in row) and obj.display_type==old_display)
        review=executor.execute('angelo_sculpt_review',{'object':obj.name,'baseline':ref,
            'outline':[[.5+.44*math.cos(i*math.tau/64),.5+.44*math.sin(i*math.tau/64)] for i in range(64)],
            'landmarks':[{'region':'Right cheek','pixel':[.65,.5]}]})
        report['review']=review
        check('fixed_frame_review_returns_pixels_and_metrics',review.get('success') and review['review']['baseline_iou']>.99 and len(review['views'])==1)
        check('review_reference_targets_are_measured',review['review']['reference_iou_after']>.7 and len(review['review']['landmarks'])==1)
        fresh=executor.observation_store.observation_camera(review['views'][0]['id'])
        check('comparison_camera_is_identical',all(camera[k]==fresh[k] for k in ('center','radius','direction','camera_distance')))
        close=executor.execute('angelo_sculpt_view',{'object':obj.name,'views':['FRONT'],'resolution':384,'region':'Right cheek'})
        check('regional_closeup_has_tighter_framing',close['views'][0]['half_width_world']<baseline['views'][0]['half_width_world'])
        # A separate small Multires fixture verifies detail survival across working levels.
        bpy.ops.object.mode_set(mode='OBJECT');bpy.ops.mesh.primitive_uv_sphere_add(segments=16,ring_count=8)
        multi=bpy.context.active_object;multi.name='Workflow Multires'
        state=inspect(multi.name);prepared=executor.execute('angelo_prepare_sculpt',{'object':multi.name,'mesh_revision':state['mesh_revision'],'add_levels':3})
        check('multires_fixture_prepared',prepared['success'])
        args=action();args.update(radius=.4)
        edited=executor.execute('angelo_sculpt_batch',dict(args,object=multi.name,mesh_revision=inspect(multi.name)['mesh_revision']))
        check('high_level_detail_added',edited.get('success') and edited['changed_points']>0)
        high=sculpt.snapshot(window,multi)['positions'].copy()
        state=inspect(multi.name)
        defined=executor.execute('angelo_sculpt_region',{'object':multi.name,'mesh_revision':state['mesh_revision'],'name':'Detail','position':[0,-1,0],'radius':.4,'face_set':0})
        level_args={'object':multi.name,'mesh_revision':defined['verified']['mesh_revision'],'mode':'EXPLICIT','stage':'FORMS','radius':.5,'level':1}
        low=executor.execute('angelo_sculpt_resolution',level_args)
        check('existing_working_level_selected',low.get('success') and low['working_level']==1 and multi.modifiers[0].total_levels==3)
        check('level_change_marks_region_stale',low['regions'][0].get('stale'))
        restored=executor.execute('angelo_sculpt_resolution',dict(level_args,mesh_revision=low['verified']['mesh_revision'],stage='DETAIL',level=3))
        check('higher_detail_survives_level_switch',restored.get('success') and np.allclose(high,sculpt.snapshot(window,multi)['positions'],atol=2e-6,rtol=0))
        auto=executor.execute('angelo_sculpt_resolution',dict(level_args,mesh_revision=restored['verified']['mesh_revision'],mode='AUTO',radius=1.0))
        check('auto_uses_lower_level_for_broad_forms',auto.get('success') and auto['working_level']<3)
        # Real chat approval must preserve Undo for metadata and level changes,
        # as well as the native program. No account or inference is started.
        import addon_utils
        from angelo_agent import ui
        ui._authentication.start=lambda *a,**kw:None
        ui.refresh_connected_models=lambda *a,**kw:None
        addon_utils.enable('angelo_agent',default_set=False,persistent=False)
        controller=ui.get_controller();controller.busy=True
        def approved(name,args):
            answers=[]
            controller.busy=True
            controller.request_tool('workflow-'+str(len(report['checks'])),name,args,answers.append)
            controller.pump_tools(executor,ui.document_key())
            call=next(iter(controller.pending.values()))
            bpy.ops.angelo.approve('EXEC_DEFAULT',True,call_id=call.id,session_id=controller.state['id'])
            controller.busy=False
            assert answers, 'Approval did not return a result'
            return answers[0]
        before_metadata=multi.get(REGIONS_KEY)
        bpy.ops.ed.undo_push(message='Workflow metadata baseline')
        probe=executor.execute('angelo_inspect_sculpt',{'object':multi.name,'probes':[[0,-1,0]]})['probes'][0]['position']
        defined=approved('angelo_sculpt_region',{'object':multi.name,'mesh_revision':inspect(multi.name)['mesh_revision'],
                         'name':'UI landmark','position':probe,'radius':.4,'face_set':0})
        check('region_created_through_chat_approval',defined.get('success') and 'UI landmark' in regions(multi))
        bpy.ops.ed.undo();multi=window.scene.objects['Workflow Multires']
        check('region_metadata_has_real_undo',multi.get(REGIONS_KEY)==before_metadata)
        old_level=multi.modifiers[0].sculpt_levels
        bpy.ops.ed.undo_push(message='Workflow level baseline')
        new_level=1 if old_level!=1 else 2
        switched=approved('angelo_sculpt_resolution',dict(level_args,mesh_revision=inspect(multi.name)['mesh_revision'],level=new_level))
        check('working_level_through_chat_approval',switched.get('success') and multi.modifiers[0].sculpt_levels==new_level)
        bpy.ops.ed.undo();multi=window.scene.objects['Workflow Multires']
        check('working_level_has_real_undo',multi.modifiers[0].sculpt_levels==old_level)
        before=sculpt.snapshot(window,multi)['positions'].copy()
        program=approved('angelo_sculpt_program',{'object':multi.name,'mesh_revision':inspect(multi.name)['mesh_revision'],'steps':[action(),action('SMOOTH')]})
        check('program_through_chat_approval',program.get('success'))
        bpy.ops.ed.undo();multi=window.scene.objects['Workflow Multires']
        check('program_chat_approval_one_undo',np.array_equal(before,sculpt.snapshot(window,multi)['positions']))
        addon_utils.disable('angelo_agent',default_set=False)
        # Large existing assets must be able to lower their working level without
        # exporting over-budget grids or getting a geometry-edit authorization.
        bpy.ops.object.mode_set(mode='OBJECT')
        modifier=multi.modifiers[0]
        for _ in range(4):
            bpy.ops.object.multires_subdivide(modifier=modifier.name,mode='SIMPLE')
        modifier.sculpt_levels=modifier.total_levels
        bpy.ops.object.mode_set(mode='SCULPT')
        dense=inspect(multi.name)
        check('dense_multires_returns_settings_only_revision',dense.get('revision_scope')=='MULTIRES_CONTROLS_ONLY' and dense['estimated_working_points']>1000000)
        try:executor.execute('angelo_sculpt_batch',dict(action(),object=multi.name,mesh_revision=dense['mesh_revision']))
        except sculpt.SculptPreflightError:check('settings_revision_cannot_authorize_sculpt',True)
        else:check('settings_revision_cannot_authorize_sculpt',False)
        recovered=executor.execute('angelo_sculpt_resolution',dict(level_args,mesh_revision=dense['mesh_revision'],mode='AUTO',radius=1.0))
        check('dense_asset_recovers_to_bounded_working_level',recovered.get('success') and recovered['verified']['vertices']<=1000000 and modifier.total_levels==7)
        report['success']=True

def run():
    try:main()
    except Exception:report.update(success=False,error=traceback.format_exc())
    (OUT/'sculpt-workflow.json').write_text(json.dumps(report,indent=2))
    print('ANGELO_SCULPT_WORKFLOW',json.dumps(report),flush=True)
    bpy.ops.wm.quit_blender()
    return None
bpy.app.timers.register(run,first_interval=1)
