# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit ordinary-browser capability/lifetime fixtures. No browser, auth, native signal or network."""
import suite_isolation  # noqa: F401
from contextlib import contextmanager
import os,sys,tempfile,threading,signal
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/addons_core'))
import host_test_policy  # noqa: E402
if sys.platform != 'darwin':   # The macOS profile store (fcntl, Chrome's macOS bundle); Windows has its own.
    raise unittest.SkipTest(host_test_policy.environment_skip(
        'the macOS Fab profile store; Windows runs test_native_fab_windows_*, Linux has no Fab browser'))
from angelo_agent import fab_browser_process as process,fab_browser_profile as profile,private_roots
from angelo_agent.fab_browser_ownership import ProcessOwner,OwnershipUnavailable
# A short real folder: macOS's /private/tmp (Chrome's profile socket paths are length-limited); elsewhere the
# platform's own temporary folder (there is no /private on Linux or Windows).
SHORT_TMP = '/private/tmp' if sys.platform == 'darwin' else None
PID=100000001

class ManualBrowserTests(unittest.TestCase):
 @contextmanager
 def launch(self,manual,*,leased=False):
  with tempfile.TemporaryDirectory(dir=SHORT_TMP)as name:
   root=Path(name);program=root/'Fixture.app/Contents/MacOS/Chrome';program.parent.mkdir(parents=True);program.write_bytes(b'fixture-not-executed')
   row=dict(pid=PID,parent=os.getpid(),group=PID,start='fixture-start',program=str(program),zombie=False)
   if manual:leased=True
   store=None;browser=None;calls=[]
   def spawn(program,args,env,**kwargs):calls.append((program,args,env,kwargs));return PID
   with patch.object(private_roots,'scratch_root',return_value=root/'scratch'),patch.object(profile,'snapshot',return_value={}),patch.object(process,'snapshot',return_value={PID:row}),patch.object(process.os,'posix_spawn',side_effect=spawn),patch.object(ProcessOwner,'drain',return_value=None)as managed_drain,patch('angelo_agent.fab_browser_manual_close.drain_manual',return_value=None)as manual_drain,patch('angelo_agent.fab_browser_manual_close.retire_singletons',return_value=None),patch.object(process.os,'waitpid',return_value=(PID,0)),patch.object(process.BrowserProcess,'_record',return_value=None):
    drain=manual_drain if manual else managed_drain
    (root/'scratch').mkdir()
    if leased:store=profile.ProfileStore(root/'connections',scratch=root/'scratch');lease=store.create()
    else:lease=None
    try:
     browser=process.BrowserProcess(program=str(program),profile_lease=lease,manual_login=manual)
     yield browser,calls,drain,store
    finally:
     drain.side_effect=None
     if browser is not None:
      browser.close()
     if store is not None and store._binding()is not None:
      pending=store.resume();pending.cancel_pending(str(program))
   private_roots.prune()
 def test_explicit_manual_has_no_cdp_flag_or_descriptors_and_opens_normal_fab_url(self):
  with self.launch(True)as(browser,calls,drain,store):
   self.assertIsNone(browser._read);self.assertIsNone(browser._write)
   args=calls[0][1];actions=calls[0][3]['file_actions']
   self.assertEqual(args[2:],['--no-first-run','--no-default-browser-check','--new-window','https://www.fab.com/'])
   self.assertFalse(any(value.startswith('--remote-debugging')for value in args))
   self.assertEqual([action[2]for action in actions],[0,1,2])
   self.assertTrue(any(value.startswith('--user-data-dir=')for value in args))
 def test_default_managed_launch_recipe_and_pipe_capability_are_preserved(self):
  with self.launch(False)as(browser,calls,drain,store):
   self.assertIsNotNone(browser._read);self.assertIsNotNone(browser._write)
   args=calls[0][1];actions=calls[0][3]['file_actions']
   self.assertEqual(args[-1],'about:blank');self.assertEqual(args.count('--remote-debugging-pipe'),1)
   self.assertEqual([action[2]for action in actions],[3,4,0,1,2])
 def test_manual_rpc_refuses_before_io_or_stop_inspection(self):
  browser=process.BrowserProcess.__new__(process.BrowserProcess);browser._lock=threading.RLock();browser._manual_login=True
  with patch.object(process.os,'write')as write,patch.object(process.os,'read')as read,self.assertRaisesRegex(process.BrowserUnavailable,'MANUAL_BROWSER_NO_RPC'):
   browser.call('Runtime.evaluate',{'expression':'1'})
  write.assert_not_called();read.assert_not_called()
 def test_manual_done_retains_only_after_graceful_same_owner_drain_and_normal_exit(self):
  with self.launch(True,leased=True)as(browser,calls,drain,store):
   path=browser.profile;identity=browser._profile_lease.identity
   browser.close(retain_profile=True)
   drain.assert_called_once_with(browser,retain=True)
   self.assertTrue(browser._manual_graceful_drained);self.assertTrue(path.exists());self.assertEqual(store._binding()['state'],'retained')
   resumed=store.resume();self.assertEqual(resumed.identity,identity);resumed.cancel_pending(calls[0][0])
 def test_manual_nonzero_or_signaled_exit_never_publishes_retained_profile(self):
  with self.launch(True,leased=True)as(browser,calls,drain,store):
   path=browser.profile
   with patch.object(process.os,'waitpid',return_value=(PID,9)),self.assertRaises(process.BrowserUnavailable)as caught:
    browser.close(retain_profile=True)
   self.assertEqual(caught.exception.reason,'MANUAL_NONZERO_EXIT')
   self.assertFalse(browser._manual_graceful_drained);self.assertEqual(store._binding()['state'],'pending');self.assertTrue(path.exists())
 def test_nonzero_exit_cannot_be_retried_into_retained_state_without_new_native_proof(self):
  with self.launch(True,leased=True)as(browser,calls,drain,store):
   with patch.object(process.os,'waitpid',return_value=(PID,9)),self.assertRaises(process.BrowserUnavailable):
    browser.close(retain_profile=True)
   self.assertIsNone(browser._pid)
   with patch.object(process.os,'waitpid')as wait,self.assertRaisesRegex(process.BrowserUnavailable,'MANUAL_GRACEFUL_CLOSE_UNPROVED'):
    browser.close(retain_profile=True)
   wait.assert_not_called();self.assertEqual(drain.call_count,1);self.assertEqual(store._binding()['state'],'pending')
 def test_unproved_graceful_drain_retains_owner_and_pending_binding(self):
  with self.launch(True,leased=True)as(browser,calls,drain,store):
   drain.side_effect=OwnershipUnavailable('Browser processes did not drain')
   with self.assertRaises(process.BrowserUnavailable)as caught:browser.close(retain_profile=True)
   self.assertIs(caught.exception.owner,browser);self.assertEqual(store._binding()['state'],'pending')
   self.assertTrue(browser.profile.exists())
 def test_invalid_mode_rejected_before_native_resources(self):
  with patch.object(process,'_pipe')as pipe,patch.object(process.os,'posix_spawn')as spawn:
   for mode in('manual',None,1):
    with self.assertRaisesRegex(process.BrowserUnavailable,'LOGIN_MODE_INVALID'):process.BrowserProcess(manual_login=mode)
   pipe.assert_not_called();spawn.assert_not_called()

 def test_unleased_manual_refuses_before_any_native_acquisition(self):
  with patch.object(process,'_pipe')as pipe,patch.object(process.os,'posix_spawn')as spawn,self.assertRaisesRegex(process.BrowserUnavailable,'MANUAL_PROFILE_LEASE_REQUIRED'):
   process.BrowserProcess(manual_login=True)
  pipe.assert_not_called();spawn.assert_not_called()

 def owner(self):
  row=dict(pid=41,parent=40,group=41,start='start',program='/Applications/Fixture.app/Contents/MacOS/Chrome',zombie=False)
  owner=ProcessOwner(41,'a'*32,row['program'],'/private/owned-profile');owner.known[41]=(41,'start',row['program'])
  return owner,row
 def test_graceful_only_requests_verified_leader_then_positive_empty_observations(self):
  owner,row=self.owner()
  with patch('angelo_agent.fab_browser_ownership.snapshot',return_value={41:row}),patch('angelo_agent.fab_browser_ownership.marker_and_profile',return_value=(True,False)),patch.object(owner,'_observe',side_effect=[[],[]]),patch('os.kill')as kill:
   owner.drain(graceful_only=True)
  kill.assert_called_once_with(41,signal.SIGTERM)
 def test_graceful_unknown_marker_never_signals_or_manufactures_empty_proof(self):
  owner,row=self.owner()
  with patch('angelo_agent.fab_browser_ownership.snapshot',return_value={41:row}),patch('angelo_agent.fab_browser_ownership.marker_and_profile',return_value=(False,True)),patch('os.kill')as kill,self.assertRaises(OwnershipUnavailable):
   owner.drain(graceful_only=True)
  kill.assert_not_called()
 def test_changed_leader_identity_refuses_before_any_signal(self):
  owner,row=self.owner();row=dict(row,start='replacement')
  with patch('angelo_agent.fab_browser_ownership.snapshot',return_value={41:row}),patch('os.kill')as kill,self.assertRaises(OwnershipUnavailable):
   owner.drain(graceful_only=True)
  kill.assert_not_called()

if __name__=='__main__':unittest.main()
