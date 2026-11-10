# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned native metadata fixtures: no frameworks, screenshot, browser, titles or authentication data."""
import suite_isolation  # noqa: F401
import sys,threading
from contextlib import nullcontext
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/addons_core'))
from angelo_agent import fab_browser_manual_window as window
from angelo_agent.fab_browser_process import BrowserProcess,BrowserUnavailable

class Number:
 def __init__(self,value):self.value=value
class FakeCF:
 def __init__(self):self.reads=[];self.released=[]
 def CFArrayGetCount(self,a):return len(a)
 def CFArrayGetValueAtIndex(self,a,i):return a[i]
 def CFDictionaryGetValue(self,d,key):
  self.reads.append((d.get('fixture_pid'),key));return d[key]
 def CFGetTypeID(self,v):return 'number'if type(v)is Number else'dictionary'
 def CFNumberGetTypeID(self):return 'number'
 def CFDictionaryGetTypeID(self):return 'dictionary'
 def CFNumberGetValue(self,value,kind,out):out._obj.value=value.value;return True
 def CFRelease(self,value):self.released.append(value)
class FakeCG:
 def __init__(self,rows):self.rows=rows
 def CGWindowListCopyWindowInfo(self,flags,unused):return self.rows
 def CGRectMakeWithDictionaryRepresentation(self,value,out):
  r=out._obj;r.origin.x=value['left'];r.origin.y=value['top'];r.size.width=value['width'];r.size.height=value['height'];return True

class ManualWindowTests(unittest.TestCase):
 def fixture(self):
  b=BrowserProcess.__new__(BrowserProcess);b._lock=threading.RLock();b._manual_login=True;b._manual_auth_active=False;b._closed=False;b._pid=41
  b._leader_identity=(41,'start','/Applications/Fixture.app/Contents/MacOS/Chrome')
  row=dict(pid=41,parent=40,group=41,start='start',program=b._leader_identity[2],zombie=False)
  keys={key:key for key in('kCGWindowOwnerPID','kCGWindowNumber','kCGWindowLayer','kCGWindowBounds')}
  foreign={'fixture_pid':42,'kCGWindowOwnerPID':Number(42),'private_title':'never read'}
  owned={'fixture_pid':41,'kCGWindowOwnerPID':Number(41),'kCGWindowNumber':Number(501),'kCGWindowLayer':Number(0),
    'kCGWindowBounds':{'left':0,'top':25,'width':1024,'height':768},'private_title':'never read'}
  foreign['kCGWindowLayer']=Number(25)
  cf=FakeCF();cg=FakeCG([foreign,owned]);return b,row,keys,cg,cf
 def test_foreign_pid_filtered_before_any_bounds_or_title_and_only_owned_native_id_returned(self):
  b,row,keys,cg,cf=self.fixture()
  with patch.object(window,'snapshot',return_value={41:row}),patch.object(window,'_frameworks',return_value=(cg,cf,keys)):
   result=window.owned_manual_windows(b)
  self.assertEqual(result,({'native_window_id':501,'bounds':{'left':0.0,'top':25.0,'width':1024.0,'height':768.0}},))
  self.assertEqual([k for pid,k in cf.reads if pid==42],['kCGWindowOwnerPID'])
  self.assertFalse(any('title'in k.lower()or'name'in k.lower()for pid,k in cf.reads));self.assertEqual(cf.released,[cg.rows])
 def test_authentication_guard_refuses_before_native_metadata_access(self):
  b,row,keys,cg,cf=self.fixture()
  with patch.object(window,'foreground_manual_window',return_value={'foreground_verified':True}):b.begin_manual_auth()
  with patch.object(window,'_frameworks')as frameworks,self.assertRaisesRegex(BrowserUnavailable,'MANUAL_WINDOW_UNAVAILABLE'):
   window.owned_manual_windows(b)
  frameworks.assert_not_called()
 def test_owned_identity_change_after_native_query_refuses_and_releases_array(self):
  b,row,keys,cg,cf=self.fixture();changed=dict(row,start='reused')
  with patch.object(window,'snapshot',side_effect=[{41:row},{41:changed}]),patch.object(window,'_frameworks',return_value=(cg,cf,keys)),self.assertRaisesRegex(BrowserUnavailable,'MANUAL_OWNERSHIP_UNPROVED'):
   window.owned_manual_windows(b)
  self.assertEqual(cf.released,[cg.rows])

 def test_exact_pid_activation_and_visible_native_window_proof_precede_auth_guard(self):
  b,row,keys,cg,cf=self.fixture();calls=[]
  def activate(pid):
   self.assertFalse(b._manual_auth_active);calls.append(pid);return True
  with patch.object(window,'snapshot',return_value={41:row}),patch.object(window,'_frameworks',return_value=(cg,cf,keys)),patch.object(window,'_activation_api',return_value=nullcontext(activate)):
   ready=b.begin_manual_auth()
  self.assertEqual(calls,[41]);self.assertEqual(ready['native_window_id'],501)
  self.assertTrue(ready['foreground_verified']);self.assertTrue(b._manual_auth_active)
 def test_system_activation_refusal_does_not_enter_human_phase(self):
  b,row,keys,cg,cf=self.fixture()
  with patch.object(window,'snapshot',return_value={41:row}),patch.object(window,'_frameworks',return_value=(cg,cf,keys)),patch.object(window,'_activation_api',return_value=nullcontext(lambda pid:False)),self.assertRaisesRegex(BrowserUnavailable,'MANUAL_FOREGROUND_UNPROVED'):
   b.begin_manual_auth()
  self.assertFalse(b._manual_auth_active)
 def test_foreign_frontmost_pid_never_counts_as_ready(self):
  b,row,keys,cg,cf=self.fixture()
  cg.rows[0]['kCGWindowLayer']=Number(0)
  with patch.object(window,'snapshot',return_value={41:row}),patch.object(window,'_frameworks',return_value=(cg,cf,keys)),patch.object(window,'_activation_api',return_value=nullcontext(lambda pid:True)),self.assertRaisesRegex(BrowserUnavailable,'MANUAL_FOREGROUND_UNPROVED'):
   window.foreground_manual_window(b,seconds=.001)
  self.assertFalse(b._manual_auth_active)

 def test_front_stack_reads_foreign_pid_and_layer_only_skipping_system_layer(self):
  b,row,keys,cg,cf=self.fixture()
  with patch.object(window,'snapshot',return_value={41:row}),patch.object(window,'_frameworks',return_value=(cg,cf,keys)),patch.object(window,'_activation_api',return_value=nullcontext(lambda pid:True)):
   self.assertTrue(window.foreground_manual_window(b)['foreground_verified'])
  foreign_keys=[key for pid,key in cf.reads if pid==42]
  self.assertIn('kCGWindowLayer',foreign_keys)
  self.assertTrue(set(foreign_keys)<={'kCGWindowOwnerPID','kCGWindowLayer'})

 def test_activation_pool_drains_on_success_and_caller_failure(self):
  for fail in (False,True):
   with self.subTest(fail=fail):
    calls=[]
    def getclass(name):return {b'NSRunningApplication':11,b'NSAutoreleasePool':12,b'NSThread':13}[name]
    def selector(name):return name
    objc=SimpleNamespace(objc_getClass=getclass,sel_registerName=selector,objc_msgSend=object())
    def dispatch(obj,sel,*args):
     calls.append((obj,sel,args))
     return {b'alloc':21,b'init':22,b'runningApplicationWithProcessIdentifier:':31,b'activateWithOptions:':True,b'drain':None,b'isMainThread':True,b'isMultiThreaded':True}[sel]
    with patch.object(window.C,'CDLL',return_value=objc),patch.object(window.C,'cast',return_value=SimpleNamespace(value=99)),patch.object(window.C,'CFUNCTYPE',return_value=lambda address:dispatch),patch.object(window.sys,'platform','darwin'):
     if fail:
      with self.assertRaisesRegex(RuntimeError,'fixture failure'):
       with window._activation_api()as activate:
        self.assertTrue(activate(41));raise RuntimeError('fixture failure')
     else:
      with window._activation_api()as activate:self.assertTrue(activate(41))
    self.assertEqual(calls[-1],(22,b'drain',()))
    self.assertIn((11,b'runningApplicationWithProcessIdentifier:',(41,)),calls)
    self.assertIn((31,b'activateWithOptions:',(0,)),calls)

 def test_unsupported_cocoa_thread_refuses_before_pool_allocation(self):
  calls=[]
  def getclass(name):return {b'NSRunningApplication':11,b'NSAutoreleasePool':12,b'NSThread':13}[name]
  def selector(name):return name
  objc=SimpleNamespace(objc_getClass=getclass,sel_registerName=selector,objc_msgSend=object())
  def dispatch(obj,sel,*args):calls.append(sel);return False
  with patch.object(window.C,'CDLL',return_value=objc),patch.object(window.C,'cast',return_value=SimpleNamespace(value=99)),patch.object(window.C,'CFUNCTYPE',return_value=lambda address:dispatch),patch.object(window.sys,'platform','darwin'),self.assertRaisesRegex(BrowserUnavailable,'MANUAL_COCOA_THREAD_UNSUPPORTED'):
   with window._activation_api():self.fail('unsupported thread entered activation scope')
  self.assertEqual(calls,[b'isMainThread',b'isMultiThreaded'])

 def test_normal_quit_selector_uses_exact_pid_and_local_pool_without_force(self):
  calls=[]
  def getclass(name):return {b'NSRunningApplication':11,b'NSAutoreleasePool':12,b'NSThread':13}[name]
  def selector(name):return name
  objc=SimpleNamespace(objc_getClass=getclass,sel_registerName=selector,objc_msgSend=object())
  def dispatch(obj,sel,*args):
   calls.append((obj,sel,args))
   return {b'isMainThread':True,b'alloc':21,b'init':22,b'runningApplicationWithProcessIdentifier:':31,b'terminate':True,b'drain':None}[sel]
  with patch.object(window.C,'CDLL',return_value=objc),patch.object(window.C,'cast',return_value=SimpleNamespace(value=99)),patch.object(window.C,'CFUNCTYPE',return_value=lambda address:dispatch),patch.object(window.sys,'platform','darwin'):
   with window._application_api()as operations:self.assertTrue(operations[1](41))
  self.assertIn((11,b'runningApplicationWithProcessIdentifier:',(41,)),calls)
  self.assertIn((31,b'terminate',()),calls)
  self.assertEqual(calls[-1],(22,b'drain',()))
  self.assertFalse(any(selector==b'forceTerminate'for _,selector,_ in calls))


class InitialWindowReadinessTests(unittest.TestCase):
    # Reuse native metadata model construction only; no inherited/replayed test methods.
    fixture = ManualWindowTests.fixture

    def test_delayed_owned_window_requires_real_proof_with_fixed_startup_and_foreground_deadlines(self):
        browser, row, keys, cg, cf = self.fixture()
        owned = cg.rows.pop()
        clock = [10.0]
        deadlines, activations = [], []
        phase = ['startup']
        def monotonic():
            value = clock[0]
            clock[0] += .001  # Real work elapses between caller and helper deadline reads.
            return value
        def advance(seconds):
            clock[0] += seconds
            if owned not in cg.rows:
                cg.rows.append(owned)
        def snapshot(deadline):
            deadlines.append((phase[0], deadline))
            return {41: row}
        def activate(pid):
            self.assertIn(owned, cg.rows)
            self.assertFalse(browser._manual_auth_active)
            activations.append(pid)
            phase[0] = 'foreground'
            return True
        completed, ready = False, None
        with patch.object(window, 'time', SimpleNamespace(monotonic=monotonic, sleep=advance)), patch.object(window, 'snapshot', side_effect=snapshot), patch.object(window, '_frameworks', return_value=(cg, cf, keys)), patch.object(window, '_activation_api', return_value=nullcontext(activate)):
            try:
                ready = browser.begin_manual_auth()
                completed = True
            except BrowserUnavailable as error:
                if error.code != 'MANUAL_WINDOW_NOT_READY':
                    raise
        self.assertTrue(completed, 'initial absent window must wait for real owned metadata within the original deadline')
        self.assertEqual(ready['native_window_id'], 501)
        self.assertTrue(ready['foreground_verified'])
        self.assertTrue(browser._manual_auth_active)
        self.assertEqual(activations, [41])
        self.assertGreater(clock[0], 10.0)
        initial = [value for stage, value in deadlines if stage == 'startup']
        foreground = [value for stage, value in deadlines if stage == 'foreground']
        self.assertTrue(initial and all(abs(value - 25.0) < 1e-9 for value in initial))
        self.assertTrue(foreground and all(value == foreground[0] for value in foreground))
        self.assertLessEqual(foreground[0], clock[0] + 2.0)
        self.assertGreaterEqual(len(cf.released), 3)  # Initially absent, then real before and after activation.
        self.assertFalse(any('title' in key.lower() or 'name' in key.lower() for pid, key in cf.reads))

    def test_persistent_absent_window_refuses_at_original_deadline_without_activation_or_auth(self):
        browser, row, keys, cg, cf = self.fixture()
        cg.rows.pop()
        clock = [10.0]
        deadlines = []
        def advance(seconds):
            clock[0] += seconds
        def snapshot(deadline):
            deadlines.append(deadline)
            return {41: row}
        with patch.object(window, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=advance)), patch.object(window, 'snapshot', side_effect=snapshot), patch.object(window, '_frameworks', return_value=(cg, cf, keys)), patch.object(window, '_activation_api') as activation:
            with self.assertRaises(BrowserUnavailable) as caught:
                browser.begin_manual_auth()
        self.assertEqual(caught.exception.code, 'MANUAL_WINDOW_NOT_READY')
        self.assertEqual(clock[0], 25.0)
        self.assertTrue(all(value <= 25.0 for value in deadlines))
        self.assertFalse(browser._manual_auth_active)
        activation.assert_not_called()

    def test_identity_error_is_immediate_and_never_treated_as_window_startup_delay(self):
        browser, row, keys, cg, cf = self.fixture()
        clock = [10.0]
        sleep = unittest.mock.Mock()
        changed = dict(row, start='reused-lifetime')
        with patch.object(window, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)), patch.object(window, 'snapshot', return_value={41: changed}), patch.object(window, '_frameworks') as frameworks, patch.object(window, '_activation_api') as activation:
            with self.assertRaises(BrowserUnavailable) as caught:
                browser.begin_manual_auth()
        self.assertEqual(caught.exception.code, 'MANUAL_OWNERSHIP_UNPROVED')
        self.assertFalse(browser._manual_auth_active)
        sleep.assert_not_called()
        frameworks.assert_not_called()
        activation.assert_not_called()


class StartupBudgetTests(unittest.TestCase):
    fixture = ManualWindowTests.fixture

    def test_cold_initial_scan_over_two_seconds_requires_full_proof_inside_fixed_startup_budget(self):
        browser, row, keys, cg, cf = self.fixture()
        clock, stage, calls = [0.0], ['startup'], [0]
        deadlines, activated = [], []
        def native_array(flags, unused):
            clock[0] += 3.0 if calls[0] == 0 else .1
            calls[0] += 1
            return cg.rows
        def snapshot(deadline):
            deadlines.append((stage[0], deadline))
            return {41: row}
        def activate(pid):
            self.assertFalse(browser._manual_auth_active)
            activated.append(pid)
            stage[0] = 'foreground'
            return True
        cg.CGWindowListCopyWindowInfo = native_array
        complete, ready = False, None
        with patch.object(window, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))), patch.object(window, 'snapshot', side_effect=snapshot), patch.object(window, '_frameworks', return_value=(cg, cf, keys)), patch.object(window, '_activation_api', return_value=nullcontext(activate)):
            try:
                ready = browser.begin_manual_auth()
                complete = True
            except BrowserUnavailable as error:
                if error.code != 'TIMEOUT':
                    raise
        self.assertTrue(complete, 'a valid cold initial scan must have its explicit startup budget before the strict foreground proof')
        self.assertEqual(ready['native_window_id'], 501)
        self.assertTrue(ready['foreground_verified'])
        self.assertTrue(browser._manual_auth_active)
        self.assertEqual(activated, [41])
        self.assertTrue(all(deadline == 15.0 for phase, deadline in deadlines if phase == 'startup'))
        self.assertTrue(any(phase == 'foreground' for phase, deadline in deadlines))
        self.assertTrue(all(deadline == 5.0 for phase, deadline in deadlines if phase == 'foreground'))
        self.assertEqual(len(cf.released), 2)
        self.assertFalse(any('title' in key.lower() or 'name' in key.lower() for pid, key in cf.reads))

    def test_cancel_before_probe_never_reads_native_metadata_or_sets_auth_flag(self):
        browser, row, keys, cg, cf = self.fixture()
        stop = threading.Event()
        stop.set()
        with patch.object(window, 'snapshot') as snapshot, patch.object(window, '_frameworks') as frameworks, patch.object(window, '_activation_api') as activate:
            with self.assertRaises(BrowserUnavailable) as caught:
                browser.begin_manual_auth(stop=stop)
        self.assertEqual(caught.exception.code, 'CANCELLED')
        self.assertFalse(browser._manual_auth_active)
        snapshot.assert_not_called()
        frameworks.assert_not_called()
        activate.assert_not_called()

    def test_cancel_during_initial_array_releases_it_without_activation_or_auth(self):
        browser, row, keys, cg, cf = self.fixture()
        stop = threading.Event()
        def native_array(flags, unused):
            stop.set()
            return cg.rows
        cg.CGWindowListCopyWindowInfo = native_array
        with patch.object(window, 'snapshot', return_value={41: row}), patch.object(window, '_frameworks', return_value=(cg, cf, keys)), patch.object(window, '_activation_api') as activate:
            with self.assertRaises(BrowserUnavailable) as caught:
                browser.begin_manual_auth(stop=stop)
        self.assertEqual(caught.exception.code, 'CANCELLED')
        self.assertFalse(browser._manual_auth_active)
        self.assertEqual(cf.released, [cg.rows])
        activate.assert_not_called()

    def test_cancel_after_proof_return_still_prevents_auth_flag(self):
        browser, row, keys, cg, cf = self.fixture()
        stop = threading.Event()
        def proof(current, *, stop):
            stop.set()
            return {'foreground_verified': True, 'native_window_id': 501}
        with patch.object(window, 'foreground_manual_window', side_effect=proof):
            with self.assertRaises(BrowserUnavailable) as caught:
                browser.begin_manual_auth(stop=stop)
        self.assertEqual(caught.exception.code, 'CANCELLED')
        self.assertFalse(browser._manual_auth_active)

    def test_foreground_proof_keeps_two_second_deadline_after_cold_startup(self):
        browser, row, keys, cg, cf = self.fixture()
        clock, calls, stage = [0.0], [0], ['startup']
        deadlines = []
        def native_array(flags, unused):
            clock[0] += 3.0 if calls[0] == 0 else 2.1
            calls[0] += 1
            return cg.rows
        def snapshot(deadline):
            deadlines.append((stage[0], deadline))
            return {41: row}
        def activate(pid):
            stage[0] = 'foreground'
            return True
        cg.CGWindowListCopyWindowInfo = native_array
        with patch.object(window, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: None)), patch.object(window, 'snapshot', side_effect=snapshot), patch.object(window, '_frameworks', return_value=(cg, cf, keys)), patch.object(window, '_activation_api', return_value=nullcontext(activate)):
            with self.assertRaises(BrowserUnavailable) as caught:
                browser.begin_manual_auth()
        self.assertEqual(caught.exception.code, 'TIMEOUT')
        self.assertFalse(browser._manual_auth_active)
        self.assertTrue(any(phase == 'foreground' for phase, deadline in deadlines))
        self.assertTrue(all(deadline == 5.0 for phase, deadline in deadlines if phase == 'foreground'))
        self.assertEqual(len(cf.released), 2)

    def test_public_metadata_and_foreground_proof_limits_remain_two_seconds(self):
        browser, row, keys, cg, cf = self.fixture()
        with patch.object(window, 'snapshot') as snapshot, patch.object(window, '_frameworks') as frameworks:
            for operation in (window.owned_manual_windows, window.foreground_manual_window):
                with self.subTest(operation=operation.__name__), self.assertRaises(BrowserUnavailable) as caught:
                    operation(browser, seconds=15)
                self.assertEqual(caught.exception.code, 'WINDOW_LIMIT')
        self.assertFalse(browser._manual_auth_active)
        snapshot.assert_not_called()
        frameworks.assert_not_called()

    def test_manual_connection_passes_only_per_call_combined_stop_without_retaining_job_stop(self):
        from angelo_agent import fab_browser_platform as platform_owner
        from angelo_agent.fab_browser_manual_connection import ManualConnection
        browser, row, keys, cg, cf = self.fixture()
        lifetime, job = threading.Event(), threading.Event()
        with patch.object(platform_owner, '_platform_name', return_value='darwin'):   # A macOS browser fixture.
            owner = ManualConnection(browser, SimpleNamespace(), lifetime)
        captured = []
        def proof(current, *, stop):
            captured.append(stop)
            self.assertFalse(stop.is_set())
            return {'foreground_verified': True}
        with patch.object(platform_owner, 'begin_manual_auth', side_effect=proof) as begin:
            owner.begin_login(job)
            job.set()
            self.assertTrue(captured[0].is_set())
            self.assertIs(owner._stop, lifetime)
            self.assertFalse(owner._stop.is_set())
            owner.begin_login()
            self.assertFalse(captured[1].is_set())
            lifetime.set()
            with self.assertRaises(BrowserUnavailable) as caught:
                owner.begin_login()
        self.assertEqual(caught.exception.code, 'CANCELLED')
        self.assertEqual(begin.call_count, 2)


class LateSnapshotCompletionTests(unittest.TestCase):
    fixture = ManualWindowTests.fixture

    def test_late_final_initial_snapshot_cannot_start_new_foreground_budget_or_activation(self):
        browser, row, keys, cg, cf = self.fixture()
        clock, queries = [0.0], [0]
        def native_array(flags, unused):
            queries[0] += 1
            return cg.rows
        def snapshot(deadline):
            if queries[0] == 1:
                clock[0] = 15.1  # The final original initial-scan snapshot completes too late.
            return {41: row}
        cg.CGWindowListCopyWindowInfo = native_array
        with patch.object(window, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: None)), patch.object(window, 'snapshot', side_effect=snapshot), patch.object(window, '_frameworks', return_value=(cg, cf, keys)), patch.object(window, '_activation_api') as activate:
            with self.assertRaises(BrowserUnavailable) as caught:
                browser.begin_manual_auth()
        self.assertEqual(caught.exception.code, 'TIMEOUT')
        self.assertFalse(browser._manual_auth_active)
        self.assertEqual(cf.released, [cg.rows])
        activate.assert_not_called()

    def test_late_final_foreground_snapshot_cannot_publish_ready_or_auth_flag(self):
        browser, row, keys, cg, cf = self.fixture()
        clock, queries, phase = [0.0], [0], ['startup']
        activated = []
        def native_array(flags, unused):
            queries[0] += 1
            return cg.rows
        def snapshot(deadline):
            if phase[0] == 'foreground' and queries[0] == 2:
                clock[0] = 2.1  # The second scan finishes after its original foreground deadline.
            return {41: row}
        def activate(pid):
            phase[0] = 'foreground'
            activated.append(pid)
            return True
        cg.CGWindowListCopyWindowInfo = native_array
        with patch.object(window, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: None)), patch.object(window, 'snapshot', side_effect=snapshot), patch.object(window, '_frameworks', return_value=(cg, cf, keys)), patch.object(window, '_activation_api', return_value=nullcontext(activate)):
            with self.assertRaises(BrowserUnavailable) as caught:
                browser.begin_manual_auth()
        self.assertEqual(caught.exception.code, 'TIMEOUT')
        self.assertEqual(activated, [41])
        self.assertFalse(browser._manual_auth_active)
        self.assertEqual(len(cf.released), 2)

if __name__=='__main__':unittest.main()
