# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Serial native audit benchmark. Run alone in --factory-startup; no billed calls."""
import bpy, json, math, os, sys, tempfile, time, traceback, statistics, resource
import numpy as np
from pathlib import Path
assert '--factory-startup' in sys.argv
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/addons_core'))
OUT=Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(),'angelo-sculpt-fixes-performance'));OUT.mkdir(parents=True,exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT='1',ANGELO_CONNECTIONS_DIR=str(OUT/'accounts'),ANGELO_STATE_DIR=str(OUT/'state'))
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.controller import Controller
from angelo_agent.references import ReferenceStore
from angelo_agent import sculpt
from angelo_agent.sculpt_workflow import polygon_mask
report={'paid_requests':0,'cases':[],'note':'Serial timings in disposable GUI; other native audit instances stopped. No inference/network time. One warmup per variant excluded.'}

def summary(values):
    return {'median_ms':round(statistics.median(values),3),'min_ms':round(min(values),3),'max_ms':round(max(values),3),'samples_ms':[round(x,3) for x in values]}

def reset():
    if bpy.context.object and bpy.context.object.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)

def main():
    w=bpy.context.window_manager.windows[0];a=next(a for a in w.screen.areas if a.type=='VIEW_3D');r=next(r for r in a.regions if r.type=='WINDOW')
    with bpy.context.temp_override(window=w,area=a,region=r):
        (OUT/'recovery').mkdir(exist_ok=True);bpy.context.preferences.filepaths.temporary_directory=str(OUT/'recovery')
        bpy.context.preferences.edit.use_global_undo=True
        for label,segments,rings,levels in [('base_130k',512,256,0),('base_523k',1024,512,0),('multires_48k',32,16,3),('multires_555k',32,16,5)]:
            reset();bpy.ops.mesh.primitive_uv_sphere_add(segments=segments,ring_count=rings)
            obj=bpy.context.active_object;obj.name='Audit target'
            if levels:
                m=obj.modifiers.new('Audit Multires','MULTIRES')
                for _ in range(levels):bpy.ops.object.multires_subdivide(modifier=m.name,mode='CATMULL_CLARK')
            ex=BlenderExecutor(w);ex.observation_store=ReferenceStore(OUT/label,'92447c82-6f6e-464b-8497-1d65b1bf1cb2')
            def state():return ex.execute('angelo_inspect_sculpt',{'object':obj.name,'probes':[]})
            initial=state();prepared=ex.execute('angelo_prepare_sculpt',{'object':obj.name,'mesh_revision':initial['mesh_revision'],'add_levels':0})
            assert prepared.get('success'),prepared
            bpy.ops.ed.undo_push(message='Audit baseline')
            paths=[]
            for row in range(4):
                for col in range(8):
                    x,z=-.45+col*.9/7,-.3+row*.2
                    paths.append([{'position':[x,-math.sqrt(1-x*x-z*z),z],'pressure':.7}])
            step={'brush':'DRAW','radius':.18,'strength':.15,'direction':'ADD','symmetry':'NONE','normal':[0,-1,0],'front_faces_only':True,'spacing':.2,'paths':paths}
            steps=[dict(step,paths=paths[i*4:i*4+4],brush='DRAW' if i%2==0 else 'INFLATE') for i in range(8)]
            times={name:[] for name in ('batch','program','separate_8','controller_batch')}
            controller=Controller(OUT/(label+'-controller'));doc=('audit',label);controller.set_document(doc)
            controller.state['options']['approval']='AUTO_BOUNDED'
            c={'name':label,'working_points':prepared['verified']['vertices'],'faces':prepared['verified']['faces'],'max_dabs':prepared['verified']['max_dabs_this_mesh']}
            for repeat in range(6):
                order=list(times)
                order=order[repeat%4:]+order[:repeat%4]
                for method in order:
                    current=state();undo_count=1
                    args=dict(step,object=obj.name,mesh_revision=current['mesh_revision'])
                    started=time.perf_counter()
                    if method=='batch':result=ex.execute('angelo_sculpt_batch',args)
                    elif method=='program':result=ex.execute('angelo_sculpt_program',{'object':obj.name,'mesh_revision':current['mesh_revision'],'steps':steps})
                    elif method=='separate_8':
                        undo_count=8
                        for one in steps:
                            result=ex.execute('angelo_sculpt_batch',dict(one,object=obj.name,mesh_revision=current['mesh_revision']))
                            assert result.get('success'),result
                            current=result['verified']
                    else:
                        answers=[];controller.busy=True;controller.cancelled=False
                        controller.request_tool('audit-'+str(repeat),'angelo_sculpt_batch',args,answers.append)
                        controller.pump_tools(ex,doc,apply_mutation=lambda call:controller.execute_tool(call,ex,doc))
                        assert len(answers)==1,answers
                        result=answers[0];controller.busy=False
                    elapsed=(time.perf_counter()-started)*1000
                    assert result.get('success'),result
                    if repeat:times[method].append(elapsed)
                    for _ in range(undo_count):bpy.ops.ed.undo()
                    obj=w.scene.objects['Audit target']
            c['timings']={name:summary(values) for name,values in times.items()}
            # Capture/review costs are outside the published brush-only benchmark.
            captures=[];reviews=[]
            baseline=ex.execute('angelo_sculpt_view',{'object':obj.name,'views':['FRONT'],'resolution':768})
            outline=[[.5+.43*math.cos(i*math.tau/128),.5+.43*math.sin(i*math.tau/128)] for i in range(128)]
            for repeat in range(6):
                started=time.perf_counter();view=ex.execute('angelo_sculpt_view',{'object':obj.name,'views':['FRONT','RIGHT','THREE_QUARTER'],'resolution':768});capture_ms=(time.perf_counter()-started)*1000
                if repeat: captures.append(capture_ms)
                started=time.perf_counter();review=ex.execute('angelo_sculpt_review',{'object':obj.name,'baseline':baseline['views'][0]['id'],'outline':outline,'landmarks':[]});review_ms=(time.perf_counter()-started)*1000
                if repeat: reviews.append(review_ms)
                assert review.get('success'),review
            c['capture_3x768']=summary(captures);c['review_768_outline128']=summary(reviews)
            if label=='base_130k':
                current=state();noop=dict(step,object=obj.name,mesh_revision=current['mesh_revision'],paths=[[{'position':[0,-1,0],'pressure':0.0}]])
                answers=[];controller.busy=True;controller.cancelled=False
                for i in range(32):controller.request_tool('queue-'+str(i),'angelo_sculpt_program',{'object':obj.name,'mesh_revision':current['mesh_revision'],'steps':[{k:v for k,v in noop.items() if k not in ('object','mesh_revision')}]},answers.append)
                started=time.perf_counter();controller.pump_tools(ex,doc,apply_mutation=lambda call:controller.execute_tool(call,ex,doc))
                c['one_timer_queue32']={'ms':round((time.perf_counter()-started)*1000,3),'completed':len(answers),'successes':sum(bool(x.get('success')) for x in answers),'errors':[x.get('error') for x in answers if x.get('error')]}
                tick_times=[c['one_timer_queue32']['ms']]
                while len(answers)<32:
                    previous=len(answers);started=time.perf_counter()
                    controller.pump_tools(ex,doc,apply_mutation=lambda call:controller.execute_tool(call,ex,doc))
                    tick_times.append((time.perf_counter()-started)*1000)
                    assert len(answers)-previous==1, 'Each pump must yield after one native execution'
                c['queue32_pump_steps']={'ticks':len(tick_times),'times':summary(tick_times),'successes':sum(bool(x.get('success')) for x in answers),
                    'note':'Per-pump timing, not a wall-clock UI responsiveness measurement; no redraw between these explicit calls'}
                controller.busy=False
            controller.close()
            c['max_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            report['cases'].append(c)
            (OUT/'results.json').write_text(json.dumps(report,indent=2))
            print('AUDIT_CASE',json.dumps(c),flush=True)
        values=[]
        for _ in range(5):
            started=time.perf_counter();polygon_mask(outline,768);values.append((time.perf_counter()-started)*1000)
        report['polygon_mask_only']=summary(values)
        report['success']=True

def run():
    try:main()
    except Exception:report.update(success=False,error=traceback.format_exc())
    (OUT/'results.json').write_text(json.dumps(report,indent=2));print('AUDIT_DONE',json.dumps(report),flush=True)
    bpy.ops.wm.quit_blender();return None
bpy.app.timers.register(run,first_interval=1)
