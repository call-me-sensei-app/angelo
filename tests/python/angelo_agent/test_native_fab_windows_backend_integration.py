# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows backend composition model; no Windows DLL, Chrome or profile-auth proof."""
import suite_isolation  # noqa: F401
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/addons_core'))
from angelo_agent import fab_browser_process_windows as backend


class Lease:
    def __init__(self,path,events):
        self.path,self.events=path,events
        self._browser=None
        self._released=False
        self.generation='profile-epoch'
    def claim(self,browser):
        self.events.append('claim')
        assert browser._profile_lease is self
        self._browser=browser
        return self.path
    def verify(self):self.events.append('verify')
    def finish_drained(self,browser,*,retain=False):
        assert self._browser is browser and browser._pipes is None and browser._drained
        self.events.append(('finish',retain))
        self._released=True
    def abort_claim_before_spawn(self,browser):
        assert browser._spawn_attempted is False and browser._pipes is None
        self.events.append('abort')
        self._released=True
    def cancel_pending(self):raise AssertionError('claimed lease canceled as unclaimed')


class Pipes:
    def __init__(self,events):
        self.events=events
        self.fail=False
        self.chunks=[]
        self.writes=[]
    @contextlib.contextmanager
    def spawn_kwargs(self):
        yield ['--remote-debugging-pipe'],{'close_fds':True}
    def close(self):
        self.events.append('pipes_close')
        if self.fail:raise OSError('fixture uncertain pipes')
    def read(self):return self.chunks.pop(0) if self.chunks else b''
    def write(self,data):self.writes.append(data);return len(data)


class Child:
    def __init__(self,events):self.events=events;self.passive_fail=False;self.force_fail=False
    def poll(self):return None
    def drain_job(self,deadline):
        self.events.append('force')
        if self.force_fail:raise OSError('fixture job query')
    def finish_job_passive(self,deadline):
        self.events.append('passive')
        if self.passive_fail:raise OSError('fixture normal exit unproved')


class WindowsIntegrationTests(unittest.TestCase):
    def test_runtime_profile_stack_is_present_beyond_the_fake_lease_fixture(self):
        # The composition must ship actual runtime modules even though lifecycle
        # fixtures replace native API boundaries. A fake class cannot satisfy this.
        directory=Path(backend.__file__).resolve().parent
        pinned={
            'fab_browser_profile_session_windows.py':'0c70e92be4f1306d6e317c917d3ad69c6bad9039a47a5912746391fd7e87ee4a',
            'fab_browser_profile_metadata_windows.py':'f52459c0af9950d1ed5e5508260c9c812f2c45a9ff5823133df1d7c82c298bd7',
            'fab_browser_profile_windows.py':'52a4a71c2c31a6370a1acbd9ef1e9849efd7aad59630db352331d8154b2cff42',
            'fab_browser_download_file_windows.py':'b87209a45aadf2d5890b39751dc71e47e23086ec98e5e7048768e76a5ffda6b1',
        }
        for name,digest in pinned.items():
            with self.subTest(module=name):
                self.assertEqual(hashlib.sha256((directory/name).read_bytes()).hexdigest(),digest)
        for name in ('fab_browser_windows_proof.py','child_process.py','fab_browser_pipes_windows.py',
                     'private_roots.py','storage.py','team_library_files.py','fab_browser_download.py'):
            with self.subTest(module=name):
                self.assertTrue((directory/name).is_file())

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.program=Path(self.folder.name)/'chrome.exe'
        self.program.write_bytes(b'fixture; never executed')
        self.events=[]
        self.lease=Lease(Path(self.folder.name)/'profile',self.events)
        self.pipes=Pipes(self.events)
        self.child=Child(self.events)
        self.arguments=None
        module=ModuleType('angelo_agent.fab_browser_profile_session_windows')
        module.WindowsProfileLease=Lease
        proof=ModuleType('angelo_agent.fab_browser_windows_proof')
        class NormalCloseRequestUncertain(RuntimeError):
            def __init__(self,owner,request):self.owner,self.request=owner,request
        proof.NormalCloseRequestUncertain=NormalCloseRequestUncertain
        self.prove=mock.Mock(return_value=object())
        proof.prove=self.prove
        proof.request_normal_close=lambda owner,deadline:self.events.append('normal_close')
        proof.require_query_cleanup=mock.Mock()
        self.proof_module=proof
        self.modules=mock.patch.dict(sys.modules,{module.__name__:module,proof.__name__:proof})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        proof_attribute=mock.patch.object(sys.modules['angelo_agent'],'fab_browser_windows_proof',proof,create=True)
        proof_attribute.start()
        self.addCleanup(proof_attribute.stop)

    def create(self,*,manual=False,child=None,pipes=None):
        def spawn(arguments,**kwargs):
            self.arguments=(arguments,kwargs)
            self.assertTrue(self.lease._browser._spawn_attempted)
            self.assertIs(arguments,self.lease._browser._launch_arguments)
            self.assertIs(type(arguments),tuple)
            self.assertIs(type(self.lease._browser._runtime_generation),object)
            return self.child
        return backend.WindowsBrowserProcess(program=str(self.program),profile_lease=self.lease,
            manual_login=manual,_pipes=pipes or (lambda:self.pipes),_child=child or spawn)

    def test_manual_has_no_cdp_flags_or_pipe_construction(self):
        pipes=mock.Mock(side_effect=AssertionError('manual constructed CDP pipes'))
        owner=self.create(manual=True,pipes=pipes)
        args,kwargs=self.arguments
        self.assertFalse(any('remote-debugging' in arg for arg in args))
        self.assertEqual(args[-1],'https://www.fab.com/')
        self.assertIs(kwargs['retain_job'],True)
        with self.assertRaises(backend.BrowserUnavailable) as error:owner.call('Browser.getVersion')
        self.assertEqual(error.exception.code,'MANUAL_BROWSER_NO_RPC')
        pipes.assert_not_called()
        owner.close()

    def test_known_pipe_and_job_closure_precede_profile_finish(self):
        owner=self.create();self.events.clear();owner.close()
        self.assertEqual(self.events,['pipes_close','force',('finish',False)])
        self.assertIsNone(owner.profile)

    def test_pipe_failure_still_drains_job_but_keeps_profile_lease(self):
        owner=self.create();self.pipes.fail=True
        with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close()
        self.assertIn('force',self.events)
        self.assertNotIn(('finish',False),self.events)
        self.assertIs(owner._profile_lease,self.lease)
        self.assertIsNotNone(owner.profile)

    def test_manual_done_uses_only_passive_job_and_retains_after_success(self):
        owner=self.create(manual=True);self.events.clear();owner.close(retain_profile=True)
        self.assertEqual(self.events,['normal_close','passive',('finish',True)])
        self.assertNotIn('force',self.events)

    def test_failed_passive_done_never_force_or_retains(self):
        owner=self.create(manual=True);self.child.passive_fail=True
        with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close(retain_profile=True)
        self.assertNotIn('force',self.events)
        self.assertNotIn(('finish',True),self.events)
        self.assertFalse(self.lease._released)

    def test_cancel_latches_before_force_and_later_done_cannot_retain(self):
        owner=self.create(manual=True);self.child.force_fail=True
        with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close()
        self.assertTrue(owner._retention_revoked)
        self.child.force_fail=False
        with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close(retain_profile=True)
        owner.close()
        self.assertNotIn(('finish',True),self.events)

    def test_definitely_pre_spawn_failure_aborts_claim_after_pipe_cleanup(self):
        def broken():raise ValueError('fixture pipe preparation')
        with self.assertRaises(backend.BrowserUnavailable):self.create(pipes=broken)
        self.assertIn('abort',self.events)
        self.assertIsNone(self.arguments)

    def test_lost_native_return_retains_claim_instead_of_pre_spawn_abort(self):
        def lost(*args,**kwargs):raise RuntimeError('fixture lost child owner')
        with self.assertRaises(backend.WindowsBrowserCleanupUnavailable) as error:self.create(child=lost)
        self.assertTrue(error.exception.owner._spawn_attempted)
        self.assertNotIn('abort',self.events)
        self.assertFalse(self.lease._released)

    def test_reply_before_event_is_observed_on_next_serialized_call(self):
        owner=self.create();seen=[];owner._set_download_sink(lambda method,params:seen.append((method,params)))
        self.pipes.chunks=[b'{"id":1,"result":{}}\0'
            b'{"method":"Browser.downloadProgress","params":{"guid":"fixture"}}\0',
            b'{"id":2,"result":{}}\0']
        owner.call('Fixture.activate')
        self.assertEqual(seen,[])
        owner.call('Browser.getVersion')
        self.assertEqual(seen,[('Browser.downloadProgress',{'guid':'fixture'})])
        owner.close()

    def test_any_sink_exception_permanently_taints_until_drain(self):
        owner=self.create()
        def bad(*args):raise KeyboardInterrupt('fixture sink')
        owner._set_download_sink(bad)
        self.pipes.chunks=[b'{"method":"Browser.downloadProgress","params":{}}\0']
        with self.assertRaises(KeyboardInterrupt):owner.call('Browser.getVersion')
        self.assertTrue(owner._download_uncertain)
        owner._set_download_sink(None)
        with self.assertRaises(backend.BrowserUnavailable) as error:owner._set_download_sink(lambda *args:None)
        self.assertEqual(error.exception.code,'DOWNLOAD_CLEANUP_UNPROVED')
        owner.close()

    def test_actual_selected_cancel_ack_after_reply_is_pumped_without_stopped_token(self):
        from angelo_agent.fab_browser_download import SelectedDownload
        owner=self.create()
        tracker=SelectedDownload('frame','asset.glb',maximum_bytes=64)
        tracker.accept('Browser.downloadWillBegin',{'guid':'chosen','frameId':'frame',
                                                   'suggestedFilename':'asset.glb'})
        owner._set_download_sink(tracker.accept)
        stop=threading.Event();stop.set()
        self.pipes.chunks=[b'{"id":1,"result":{}}\0'
            b'{"method":"Browser.downloadProgress","params":{"guid":"chosen","state":"canceled",'
            b'"receivedBytes":0,"totalBytes":0}}\0',b'{"id":2,"result":{}}\0']
        observed=owner._poll_selected_download(tracker,stop=stop)
        self.assertEqual(observed.state,'canceled')
        self.assertFalse(observed.complete)
        commands=[json.loads(raw[:-1])['method'] for raw in self.pipes.writes]
        self.assertEqual(commands,['Browser.cancelDownload','Browser.getVersion'])
        owner.close()

    def test_actual_selected_foreign_progress_taints_and_cannot_be_rebound(self):
        from angelo_agent.fab_browser_download import SelectedDownload
        owner=self.create()
        tracker=SelectedDownload('frame','asset.glb',maximum_bytes=64)
        owner._set_download_sink(tracker.accept)
        self.pipes.chunks=[b'{"method":"Browser.downloadProgress","params":{"guid":"foreign",'
            b'"state":"inProgress","receivedBytes":1,"totalBytes":1}}\0']
        with self.assertRaises(backend.BrowserUnavailable):owner._poll_selected_download(tracker)
        self.assertTrue(owner._download_uncertain)
        owner._set_download_sink(None)
        with self.assertRaises(backend.BrowserUnavailable):owner._set_download_sink(tracker.accept)
        owner.close()

    def test_native_proof_refusal_never_returns_account_or_foreground_success(self):
        owner=self.create()
        self.prove.side_effect=backend.BrowserUnavailable('PHYSICAL_BINDING_UNPROVED')
        with self.assertRaises(backend.BrowserUnavailable) as error:owner._account_binding()
        self.assertEqual(error.exception.code,'PHYSICAL_BINDING_UNPROVED')
        self.assertEqual(self.pipes.writes,[])
        owner.close()

    def test_private_account_epochs_follow_fresh_native_proof_not_token_alone(self):
        owner=self.create()
        before=self.prove.call_count
        binding=owner._account_binding()
        self.assertEqual(self.prove.call_count,before+1)
        self.assertIs(binding[0],self.lease)
        self.assertEqual(binding[1],self.lease.generation)
        self.assertEqual(binding[2],owner._runtime_epoch_token)
        self.assertEqual(len(binding[2]),32)
        owner.close()

    def test_manual_foreground_is_proved_before_privacy_phase(self):
        owner=self.create(manual=True)
        self.prove.side_effect=backend.BrowserUnavailable('MANUAL_WINDOW_UNVERIFIED')
        with self.assertRaises(backend.BrowserUnavailable):owner.begin_manual_auth()
        self.assertFalse(owner._manual_auth_active)
        self.prove.side_effect=None
        self.assertTrue(owner.begin_manual_auth())
        self.prove.assert_called_with(owner,foreground=True)
        self.assertTrue(owner._manual_auth_active)
        owner.close()

    def test_managed_done_sends_browser_close_then_requires_passive_proof_after_eof(self):
        owner=self.create();self.events.clear()
        self.pipes.chunks=[b'']  # Normal reply loss is not itself a drain certificate.
        owner.close(retain_profile=True)
        self.assertEqual(json.loads(self.pipes.writes[0][:-1])['method'],'Browser.close')
        self.assertEqual(self.events,['pipes_close','passive',('finish',True)])

    def test_managed_close_reply_loss_with_unproved_job_does_not_retain(self):
        owner=self.create();self.child.passive_fail=True
        with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close(retain_profile=True)
        self.assertNotIn(('finish',True),self.events)
        self.assertNotIn('force',self.events)

    def test_manual_lost_post_return_requires_real_passive_outcome_not_message_ack(self):
        owner=self.create(manual=True)
        receipt=object()
        def lost(browser,deadline):
            browser._windows_close_request=receipt
            raise self.proof_module.NormalCloseRequestUncertain(browser,receipt)
        self.proof_module.request_normal_close=lost
        owner.close(retain_profile=True)
        self.assertIs(owner._windows_close_request,receipt)
        self.assertTrue(owner._normal_close_request_uncertain)
        self.assertIn(('finish',True),self.events)
        self.assertIn('passive',self.events)
        self.assertNotIn('force',self.events)

    def test_manual_request_proof_failure_is_not_relabelled_as_lost_post(self):
        owner=self.create(manual=True)
        def refuse(*args):raise backend.BrowserUnavailable('PHYSICAL_BINDING_UNPROVED')
        self.proof_module.request_normal_close=refuse
        with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close(retain_profile=True)
        self.assertFalse(owner._normal_close_request_uncertain)
        self.assertFalse(self.lease._released)
        with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close(retain_profile=True)
        owner.close()
        self.assertNotIn(('finish',True),self.events)

    def test_old_request_drain_failure_then_fresh_proof_refusal_never_retains(self):
        owner=self.create(manual=True)
        receipt=object()
        def lost(browser,deadline):
            browser._windows_close_request=receipt
            raise self.proof_module.NormalCloseRequestUncertain(browser,receipt)
        self.proof_module.request_normal_close=lost
        self.child.passive_fail=True
        with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close(retain_profile=True)
        self.assertIs(owner._windows_close_request,receipt)
        self.assertFalse(owner._retention_revoked)
        self.child.passive_fail=False
        def refused(*args):raise backend.BrowserUnavailable('PHYSICAL_BINDING_UNPROVED')
        self.proof_module.request_normal_close=refused
        with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close(retain_profile=True)
        self.assertTrue(owner._drained)
        self.assertTrue(owner._retention_revoked)
        self.assertNotIn(('finish',True),self.events)
        with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close(retain_profile=True)
        owner.close()
        self.assertNotIn(('finish',True),self.events)

    def test_typed_uncertainty_for_other_owner_or_request_does_not_grant_retention(self):
        for wrong_owner in (False,True):
            with self.subTest(wrong_owner=wrong_owner):
                self.events.clear()
                self.lease=Lease(Path(self.folder.name)/'profile',self.events)
                self.child=Child(self.events)
                owner=self.create(manual=True)
                receipt=object()
                def wrong(browser,deadline):
                    browser._windows_close_request=receipt
                    raise self.proof_module.NormalCloseRequestUncertain(
                        object() if wrong_owner else browser,receipt if wrong_owner else object())
                self.proof_module.request_normal_close=wrong
                with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close(retain_profile=True)
                self.assertTrue(owner._retention_revoked)
                self.assertNotIn(('finish',True),self.events)
                owner.close()

    def test_unknown_proof_query_owner_blocks_profile_finish_even_after_owned_job_drains(self):
        for retain in (False,True):
            with self.subTest(retain=retain):
                self.events.clear()
                self.lease=Lease(Path(self.folder.name)/'profile',self.events)
                self.child=Child(self.events)
                owner=self.create(manual=True)
                query=object()
                owner._windows_wts_query=query
                self.proof_module.require_query_cleanup.side_effect=backend.BrowserUnavailable('QUERY_OWNER_UNPROVED')
                with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close(retain_profile=retain)
                self.assertTrue(owner._drained)
                self.assertIs(owner._windows_wts_query,query)
                self.assertFalse(self.lease._released)
                self.assertIsNotNone(owner.profile)
                self.assertNotIn(('finish',retain),self.events)
                with self.assertRaises(backend.WindowsBrowserCleanupUnavailable):owner.close(retain_profile=retain)
                self.proof_module.require_query_cleanup.side_effect=None
                self.assertIs(owner._windows_wts_query,query)  # Never retry/free or erase an unknown owner.

    def test_strict_boolean_manual_mode_refuses_before_native_resource(self):
        with self.assertRaises(backend.BrowserUnavailable):
            backend.WindowsBrowserProcess(manual_login=1)


    def test_manual_cancel_before_native_proof_does_not_start_privacy_phase(self):
        owner = self.create(manual=True)
        self.prove.reset_mock()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(backend.BrowserUnavailable) as caught:
            owner.begin_manual_auth(stop=stop)
        self.assertEqual(caught.exception.code, 'CANCELLED')
        self.assertFalse(owner._manual_auth_active)
        self.prove.assert_not_called()
        owner.close()

    def test_manual_cancel_during_native_proof_cannot_publish_ready_or_new_privacy_flag(self):
        owner = self.create(manual=True)
        self.prove.reset_mock()
        stop = threading.Event()
        def prove(current, **kwargs):
            stop.set()
            return object()
        self.prove.side_effect = prove
        with self.assertRaises(backend.BrowserUnavailable) as caught:
            owner.begin_manual_auth(stop=stop)
        self.assertEqual(caught.exception.code, 'CANCELLED')
        self.assertFalse(owner._manual_auth_active)
        self.prove.assert_called_once_with(owner, foreground=True)
        owner.close()

    def test_manual_cancel_preserves_prior_active_privacy_guard(self):
        owner = self.create(manual=True)
        owner._manual_auth_active = True
        self.prove.reset_mock()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(backend.BrowserUnavailable):
            owner.begin_manual_auth(stop=stop)
        self.assertTrue(owner._manual_auth_active)
        self.prove.assert_not_called()
        owner.close()

if __name__=='__main__':unittest.main()
