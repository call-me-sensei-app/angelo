# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic private-profile lifetime tests. No browser, network or account sign-in."""
import suite_isolation  # noqa: F401
from contextlib import contextmanager
import json,os
from pathlib import Path
import shutil,sys,tempfile,threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/addons_core'))
import host_test_policy  # noqa: E402
if sys.platform != 'darwin':   # The macOS profile store (fcntl, Chrome's macOS bundle); Windows has its own.
    raise unittest.SkipTest(host_test_policy.environment_skip(
        'the macOS Fab profile store; Windows runs test_native_fab_windows_*, Linux has no Fab browser'))
from angelo_agent import fab_browser_profile as profile,private_roots
from angelo_agent.fab_browser_process import BrowserProcess,BrowserUnavailable
from angelo_agent.fab_browser_ownership import OwnershipUnavailable
# A short real folder: macOS's /private/tmp (Chrome's profile socket paths are length-limited); elsewhere the
# platform's own temporary folder (there is no /private on Linux or Windows).
SHORT_TMP = '/private/tmp' if sys.platform == 'darwin' else None
PROGRAM='/Applications/Fixture.app/Contents/MacOS/Chrome'

class FabProfileTests(unittest.TestCase):
 @contextmanager
 def store(self):
  with tempfile.TemporaryDirectory(dir=SHORT_TMP) as folder:
   root=Path(folder)
   with patch.object(profile,'snapshot',return_value={}):
    yield profile.ProfileStore(root/'connections',scratch=root/'scratch')
   private_roots.prune()
 def process(self,lease):
  process=BrowserProcess.__new__(BrowserProcess)
  process._lock=threading.RLock();process._pid=None;process._read=process._write=None;process._closed=False
  process._profile_lease=lease;process._program=PROGRAM;process._owner=None
  process._marker='a'*32;process._leader_identity=None;process._record_failure=None
  process.profile=lease.claim(process,PROGRAM);process._profile_identity=lease.identity
  return process
 def test_done_drains_then_retains_and_reopens_same_private_identity(self):
  with self.store() as store:
   lease=store.create();path=lease.path;before=lease.identity
   process=self.process(lease);process.close(retain_profile=True)
   self.assertIsNone(process.profile);self.assertTrue(path.exists());self.assertEqual(store._binding()['state'],'retained')
   reopened=store.resume();self.assertEqual(reopened.identity,before);self.assertEqual(reopened.path,path)
   self.process(reopened).close();self.assertFalse(path.exists());self.assertIsNone(store._binding())
 def test_default_unleased_close_still_erases(self):
  with tempfile.TemporaryDirectory(dir=SHORT_TMP) as name:
   folder=Path(name)/'profile';folder.mkdir();process=BrowserProcess.__new__(BrowserProcess)
   process._lock=threading.RLock();process._pid=None;process._read=process._write=None;process._closed=False
   process.profile=folder;info=folder.stat();process._profile_identity=info.st_dev,info.st_ino
   process.close();self.assertFalse(folder.exists());self.assertIsNone(process.profile)
 def test_retention_without_coherent_lease_refuses(self):
  process=BrowserProcess.__new__(BrowserProcess);process._lock=threading.RLock()
  with self.assertRaises(BrowserUnavailable) as caught:process.close(retain_profile=True)
  self.assertEqual(caught.exception.code,'PROFILE_RETENTION_UNAVAILABLE')
 def test_exclusive_profile_lock_prevents_second_reopen(self):
  with self.store() as store:
   lease=store.create();self.process(lease).close(retain_profile=True)
   first=store.resume()
   with self.assertRaises(profile.ProfileUnavailable) as caught:store.resume()
   self.assertEqual(caught.exception.code,'PROFILE_REOPEN_INCOMPLETE');self.assertIsNotNone(caught.exception.owner)
   first.cancel_pending(PROGRAM)
 def test_cancel_and_timeout_purge_keep_other_native_registry_entries(self):
  for action in ('cancel','timeout'):
   with self.subTest(action=action),self.store() as store:
    other=store.directory/'unrelated-native';other.mkdir()
    self.assertTrue(private_roots.remember_profiles(store.directory,[{'native_profile':str(other)}]))
    lease=store.create();path=lease.path;self.process(lease).close()
    self.assertFalse(path.exists());self.assertEqual(private_roots.retained_profiles(store.directory),[str(other)])
 def test_changed_owner_token_refuses_without_erasing_profile(self):
  with self.store() as store:
   lease=store.create();self.process(lease).close(retain_profile=True)
   marker=lease.path/private_roots.OWNER;original=marker.read_text();marker.write_text('b'*32)
   with self.assertRaises(profile.ProfileUnavailable):store.resume()
   self.assertTrue(lease.path.exists());marker.write_text(original)
   store.resume().cancel_pending(PROGRAM)
 def test_changed_directory_identity_refuses_without_erasing_either_folder(self):
  with self.store() as store:
   lease=store.create();self.process(lease).close(retain_profile=True)
   original=lease.path;renamed=original.with_name(original.name+'-saved');original.rename(renamed);original.mkdir(mode=0o700)
   with self.assertRaises(profile.ProfileUnavailable):store.resume()
   self.assertTrue(renamed.exists());self.assertTrue(original.exists())
   original.rmdir();renamed.rename(original);store.resume().cancel_pending(PROGRAM)
 def test_profile_symlink_replacement_never_reads_or_erases_outside_folder(self):
  with self.store() as store:
   lease=store.create();self.process(lease).close(retain_profile=True)
   original=lease.path;saved=original.with_name(original.name+'-saved');original.rename(saved)
   outside=store.directory/'outside';outside.mkdir();sentinel=outside/'preserve';sentinel.write_text('unrelated')
   original.symlink_to(outside,target_is_directory=True)
   with self.assertRaises(profile.ProfileUnavailable):store.resume()
   self.assertEqual(sentinel.read_text(),'unrelated');original.unlink();saved.rename(original)
   store.resume().cancel_pending(PROGRAM)
 def test_saved_current_uid_mismatch_refuses_without_erasing(self):
  with self.store() as store:
   lease=store.create();self.process(lease).close(retain_profile=True)
   binding=store.directory/profile.BINDING;original=binding.read_text();row=json.loads(original);row['uid']=os.getuid()+1;binding.write_text(json.dumps(row))
   with self.assertRaises(profile.ProfileUnavailable):store.resume()
   self.assertTrue(lease.path.exists());binding.write_text(original);store.resume().cancel_pending(PROGRAM)
 def test_lifetime_lock_replacement_refuses_until_exact_held_file_restored(self):
  with self.store() as store:
   lease=store.create();lock=lease.path/profile.PROFILE_LOCK;saved=lock.with_name(lock.name+'-saved');lock.rename(saved)
   lock.write_text('');lock.chmod(0o600)
   with self.assertRaises(profile.ProfileUnavailable) as caught:lease.validate()
   self.assertEqual(caught.exception.code,'PROFILE_LOCK_CHANGED');self.assertIsNotNone(lease._fd)
   lock.unlink();saved.rename(lock);lease.cancel_pending(PROGRAM)
 def test_singleton_or_live_profile_user_refuses_before_signal(self):
  with self.store() as store:
   lease=store.create();singleton=lease.path/'SingletonLock';singleton.write_text('fixture')
   with patch('os.kill') as kill:
    with self.assertRaises(profile.ProfileUnavailable):lease.claim(object(),PROGRAM)
   kill.assert_not_called();singleton.unlink()
   row=dict(pid=41,parent=1,group=41,start='fixture',program=PROGRAM,zombie=False)
   with patch.object(profile,'snapshot',return_value={41:row}),patch.object(profile,'marker_and_profile',return_value=(False,True)),patch('os.kill') as kill:
    with self.assertRaises(profile.ProfileUnavailable):lease.claim(object(),PROGRAM)
   kill.assert_not_called();lease.cancel_pending(PROGRAM)
 def test_partial_browser_constructor_error_retains_exact_owner(self):
  with self.store() as store:
   lease=store.create();process=BrowserProcess.__new__(BrowserProcess)
   with patch('pathlib.Path.is_file',return_value=True),patch('os.chmod',side_effect=PermissionError('private detail')),patch('os.posix_spawn') as spawn:
    with self.assertRaises(BrowserUnavailable) as caught:BrowserProcess.__init__(process,program=PROGRAM,profile_lease=lease)
   self.assertEqual(caught.exception.code,'PROFILE_START_FAILED');self.assertIs(caught.exception.owner,process);spawn.assert_not_called()
   self.assertTrue(lease.path.exists());process.close();self.assertFalse(lease.path.exists())
 def test_unknown_native_drain_keeps_lease_and_profile(self):
  with self.store() as store:
   lease=store.create();process=self.process(lease);process._pid=41
   def refuse():raise OwnershipUnavailable('Browser helper ownership unknown')
   process._owner=SimpleNamespace(drain=refuse)
   with patch('os.waitpid') as wait,patch('os.kill') as kill:
    with self.assertRaises(BrowserUnavailable) as caught:process.close(retain_profile=True)
   self.assertIs(caught.exception.owner,process);self.assertTrue(lease.path.exists());self.assertIsNotNone(lease._fd)
   wait.assert_not_called();kill.assert_not_called()
   process._pid=None;process.close()
 def test_uncertain_pipe_close_retains_owner_and_never_retries_descriptor_number(self):
  with self.store() as store:
   lease=store.create();process=self.process(lease);read,write=os.pipe();process._read=read
   try:
    with patch('os.close',side_effect=OSError('uncertain')) as close:
     with self.assertRaises(BrowserUnavailable) as caught:process.close()
     self.assertIs(caught.exception.owner,process)
    with patch('os.close') as close:
     with self.assertRaises(BrowserUnavailable):process.close()
     close.assert_not_called()
    self.assertTrue(lease.path.exists())
   finally:
    os.close(read);os.close(write);process._read=None;process._pipe_close_failed=False;process.close()
 def test_connection_handoff_activates_exact_target_without_dom_or_credentials(self):
  from angelo_agent.fab_browser_connection import Connection
  connection=Connection.__new__(Connection);connection._lock=threading.RLock();connection._target='owned-target'
  browser=SimpleNamespace(call=__import__('unittest.mock',fromlist=['Mock']).Mock(return_value={}))
  connection._browser=browser
  self.assertEqual(connection.begin_login(),dict(state='user_sign_in_pending',account_verified=False,downloads_enabled=False))
  browser.call.assert_called_once_with('Target.activateTarget',{'targetId':'owned-target'},stop=None)
 def test_active_human_login_blocks_dom_and_search_and_done_is_not_account_proof(self):
  from angelo_agent.fab_browser_connection import Connection
  from unittest.mock import Mock
  connection=Connection.__new__(Connection);connection._lock=threading.RLock();connection._target='owned-target';connection._browser=SimpleNamespace(call=Mock(return_value={}))
  connection.begin_login();connection._browser.call.reset_mock()
  for operation in (connection.state,connection.page_facts,lambda:connection.start_search('rug')):
   with self.assertRaises(BrowserUnavailable) as caught:operation()
   self.assertEqual(caught.exception.code,'USER_SIGN_IN_PENDING')
  connection._browser.call.assert_not_called()
  result=connection.acknowledge_user_login(completed=True)
  self.assertFalse(result['account_verified']);self.assertFalse(result['downloads_enabled'])
  self.assertEqual(result['state'],'user_reported_complete')
 def test_connection_retention_forwards_only_explicit_policy(self):
  from angelo_agent.fab_browser_connection import Connection
  with self.store() as store:
   lease=store.create();process=self.process(lease)
   connection=Connection.__new__(Connection);connection._lock=threading.RLock();connection._browser=process;connection._session='session';connection._target='target'
   connection.close(retain_profile=True);self.assertIsNone(connection._browser);self.assertTrue(lease.path.exists())
   store.resume().cancel_pending(PROGRAM)
 def test_lock_close_that_succeeds_then_raises_never_touches_reused_same_inode_fd(self):
  with self.store() as store:
   lease=store.create();process=self.process(lease);original_close=os.close;original_fd=lease._fd;replacement=[]
   def uncertain(fd):
    if fd!=original_fd:return original_close(fd)
    original_close(fd)
    reopened=os.open(lease.path/profile.PROFILE_LOCK,os.O_RDONLY)
    if reopened!=fd:os.dup2(reopened,fd);original_close(reopened)
    replacement.append(fd)
    raise OSError('close completed then failed')
   try:
    with patch('os.close',side_effect=uncertain):
     with self.assertRaises(BrowserUnavailable) as caught:process.close(retain_profile=True)
    self.assertIs(caught.exception.owner,process);self.assertTrue(lease._fd_uncertain)
    with patch('os.close') as close,patch('os.fstat') as fstat:
     with self.assertRaises(BrowserUnavailable):process.close(retain_profile=True)
     close.assert_not_called();fstat.assert_not_called()
    self.assertEqual(os.fstat(replacement[0]).st_ino,lease._lock_identity[1])
   finally:
    for fd in replacement:original_close(fd)
    lease._fd=None;lease._released=True
 def test_factory_already_cancelled_never_allocates_or_probes_browser(self):
  from angelo_agent import fab_browser_connection as connection
  from unittest.mock import Mock
  stop=threading.Event();stop.set();store=Mock()
  with patch.object(connection,'chrome_program') as chrome:
   with self.assertRaises(BrowserUnavailable) as caught:connection.create_owned_connection(stop=stop,store=store)
  self.assertEqual(caught.exception.code,'CANCELLED');chrome.assert_not_called();self.assertEqual(store.mock_calls,[])
 def test_connection_lifetime_stop_combines_with_each_operation_stop(self):
  from angelo_agent.fab_browser_connection import Connection
  class Fake:
   def __init__(self,root):self.calls=[]
   def call(self,method,params=None,**kwargs):
    self.calls.append((method,params,kwargs))
    if method=='Target.createTarget':return {'targetId':'target'}
    if method=='Target.attachToTarget':return {'sessionId':'session'}
    if method=='Runtime.evaluate':return {'result':{'value':{'state':'site_visible'}}}
    return {}
   def close(self):pass
  lifetime=threading.Event();operation=threading.Event();connection=Connection(browser_factory=Fake,stop=lifetime)
  self.assertTrue(all(call[2]['stop'] is lifetime for call in connection._browser.calls))
  connection.state(stop=operation);combined=connection._browser.calls[-1][2]['stop'];self.assertFalse(combined.is_set())
  lifetime.set();self.assertTrue(combined.is_set());lifetime.clear();operation.set();self.assertTrue(combined.is_set())
  connection.close()
 def test_metadata_close_uncertainty_retains_partial_profile_owner_without_retry(self):
  with self.store() as store:
   original_close=os.close;failed=[False]
   def uncertain(fd):
    info=os.fstat(fd);lock=store.directory/'connections.lock'
    if lock.exists() and (info.st_dev,info.st_ino)==(lock.stat().st_dev,lock.stat().st_ino) and not failed[0]:
     failed[0]=True;original_close(fd);raise OSError('metadata close completed then failed')
    return original_close(fd)
   with patch('os.close',side_effect=uncertain):
    with self.assertRaises(profile.ProfileUnavailable) as caught:store.create()
   lease=caught.exception.owner;self.assertIs(type(lease),profile.ProfileLease);self.assertTrue(lease.path.exists())
   try:
    with patch('os.open') as opened:
     with self.assertRaises(profile.ProfileUnavailable):
      with store._transaction():pass
     opened.assert_not_called()
   finally:
    if lease._fd is not None:original_close(lease._fd);lease._fd=None;lease._released=True
 def test_public_search_bridge_returns_only_existing_unverified_metadata(self):
  from angelo_agent.fab_browser_connection import Connection
  from unittest.mock import Mock
  connection=Connection.__new__(Connection);connection._lock=threading.RLock();connection._stop=None
  expected=dict(items=[{'id':'fab:05d847c0-7ab9-435c-8ca6-d18216b01c17','access':'unverified','import_ready':False}],metadata_only=True,account_verified=False,import_ready=False,scene_changed=False)
  connection.start_search=Mock();connection.public_results=Mock(return_value=expected)
  self.assertIs(connection.public_search('rug',8,None),expected)
  self.assertEqual(connection.start_search.call_args.args,('rug',));self.assertEqual(connection.public_results.call_args.kwargs['limit'],8)
 def test_closed_owner_refuses_account_listing_download_before_producer_entry(self):
  from angelo_agent.fab_browser_connection import Connection
  from angelo_agent import fab_browser_identity, fab_browser_listing_owner, fab_browser_selected
  connection=Connection.__new__(Connection)
  # These producers now exist. An unacquired/closed owner must refuse before entering any of them.
  with patch.object(fab_browser_identity,'read_current_account') as account, \
       patch.object(fab_browser_listing_owner,'listing') as listing, \
       patch.object(fab_browser_selected,'open_selected') as download:
   for operation in (lambda:connection.current_account(None),lambda:connection.listing('uuid',None),lambda:connection.open_selected(None,None,None,None,256*1024*1024)):
    with self.assertRaises(BrowserUnavailable) as caught:operation()
    self.assertEqual(caught.exception.code,'CLOSED')
   account.assert_not_called();listing.assert_not_called();download.assert_not_called()
 def test_privacy_registration_failure_retains_partial_owner_for_cancel(self):
  with self.store() as store:
   with patch.object(private_roots,'remember_profiles',return_value=False):
    with self.assertRaises(profile.ProfileUnavailable) as caught:store.create()
   lease=caught.exception.owner;self.assertIs(type(lease),profile.ProfileLease);self.assertTrue(lease.path.exists())
   lease.cancel_pending(PROGRAM);self.assertFalse(lease.path.exists())
if __name__=='__main__':unittest.main()
