# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual compiled updater cancellation must release UI input and its pipe writer.

Run isolated through run_native_suites.py --gui with --enable-event-simulate.
This never authorizes an install or a committed exit: fixed updater confirmation is
patched to False. Missing native code fails; no substituted native producer.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401
import gc
import json
from pathlib import Path
import time
import traceback
from unittest import mock
import bpy
import _bpy

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/addons_core'))
from angelo_agent import update_restart, live_windows

OUT=Path(os.environ['ANGELO_QA_DIR'])
OUT.mkdir(parents=True,exist_ok=True)
report=dict(success=False,checks={},phase='starting',binary_hash=bpy.app.build_hash.decode(),
            version=bpy.app.version_string)
state=dict(seen=0,stage=0,start=time.monotonic(),reads=[],owners=[],expected=0)
native=_bpy._angelo_jobs
patch=mock.patch.object(update_restart,'_confirm_installer_quit',return_value=False)
patch.start()


class ANGELO_OT_update_cancel_probe(bpy.types.Operator):
    bl_idname='angelo.qa_update_cancel_probe'
    bl_label='Updater cancellation input probe'
    def invoke(self,context,event):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}
    def modal(self,context,event):
        if state.get('finished'):
            return {'CANCELLED'}
        if event.type=='A' and event.value=='PRESS':
            state['seen']+=1
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


def window_area():
    window=bpy.context.window
    return window,next(area for w,area in live_windows.window_areas(bpy.context.window_manager) if w==window)


def new_layout():
    """A second layout for screen_set to cycle to. screen.new switches to its copy only through its notifier (until
    then the copy shares the window's screen id, so screen_set skips it as in use): made well before it is needed."""
    window,area=window_area()
    with bpy.context.temp_override(window=window,area=area):
        assert bpy.ops.screen.new()=={'FINISHED'}


def request(mode):
    generation=native._update_quit_catalog_generation()
    assert type(generation) is int and generation>0
    read,write=os.pipe()
    os.set_blocking(read,False)
    os.set_blocking(write,False)
    state['reads'].append(read)
    try:
        if mode=='screen_changed':
            window,area=window_area()
            before_screen=window.screen.as_pointer()
            with bpy.context.temp_override(window=window,area=area):
                # The second layout was made at stage 0 (new_layout); screen_set performs ED_screen_change NOW.
                owner=native._update_quit_request(write,'c'*32,generation)
                assert bpy.ops.screen.screen_set(delta=1)=={'FINISHED'}
            assert window.screen.as_pointer()!=before_screen,'Required actual screen change did not occur'

        else:
            owner=native._update_quit_request(write,('a' if mode=='refused' else 'b')*32,
                                               generation)
    finally:
        os.close(write)
    if mode=='revoked':
        assert native._update_quit_revoke(owner) is True
        state['owners'].append(owner)
    elif mode=='collected':
        del owner
        gc.collect()  # Off-thread GC has separate source/host coverage; this is actual UI retirement.
    else:
        state['owners'].append(owner)
    state['expected']+=1
    bpy.context.window.event_simulate(type='A',value='PRESS')
    bpy.context.window.event_simulate(type='A',value='RELEASE')


def finish(error=None):
    state['finished']=True
    patch.stop()
    for read in state['reads']:
        try:os.close(read)
        except OSError:pass
    report['success']=error is None and state['seen']==4 and len(report['checks'])==6
    report['phase']='complete' if error is None else 'failed'
    if error is not None:report['error']=error
    (OUT/'update-quit-cancel-gui.json').write_text(json.dumps(report,indent=2))
    # Fixture cleanup ordinary quit has no installer process/channel authorization.
    bpy.ops.wm.quit_blender()
    return None


def tick():
    try:
        assert time.monotonic()-state['start']<30,'Native cancellation/input fixture timed out'
        if state['stage']==0:
            assert type(native._update_quit_protocol()) is int and native._update_quit_protocol()==2
            generation=native._update_quit_catalog_generation()
            assert type(generation) is int and generation>0
            # Positive out-of-range fd must refuse, not terminate through UCRT invalid-parameter handling.
            try:native._update_quit_request(2**30,'a'*32,generation)
            except (RuntimeError,ValueError,OSError):pass
            else:raise AssertionError('Invalid descriptor admitted')
            report['checks']['invalid_positive_descriptor_refused']=True
            read,write=os.pipe()
            try:
                generation=native._update_quit_catalog_generation()
                assert type(generation) is int and generation>0
                other_generation=generation-1 if generation>1 else 2
                assert 0<other_generation<=(1<<64)-1 and other_generation!=generation
                try:native._update_quit_request(write,'d'*32,other_generation)
                except RuntimeError:pass
                else:raise AssertionError('Nonmatching catalog generation admitted')
            finally:
                os.close(read)
                os.close(write)
            report['checks']['nonmatching_catalog_generation_refused']=True
            new_layout()
            bpy.utils.register_class(ANGELO_OT_update_cancel_probe)
            assert bpy.ops.angelo.qa_update_cancel_probe('INVOKE_DEFAULT')=={'RUNNING_MODAL'}
        elif state['stage']%2==0:
            assert state['seen']==state['expected'],'Retired updater handler swallowed ordinary input'
            assert os.read(state['reads'][-1],1)==b'','Cancelled handler retained its pipe writer'
            report['checks']['input_and_eof_'+str(state['expected'])]=True
        if state['stage']>=8:
            return finish()
        if state['stage']%2==0:
            request(('revoked','refused','collected','screen_changed')[state['stage']//2])
        state['stage']+=1
        return .1
    except Exception:
        return finish(traceback.format_exc())


bpy.app.timers.register(tick,first_interval=.5)
