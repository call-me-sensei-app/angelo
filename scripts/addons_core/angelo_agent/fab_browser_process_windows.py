# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows Fab browser backend using one private profile, inherited pipes and required retained Job.

Not dispatched/activated by this module. The shared browser owner adopts this backend only after actual
Windows Chrome, ACL, pipe inheritance and complete Job drain proof. No existing profiles or login data read.
"""
import json
from contextlib import nullcontext
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .child_process import ChildProcess, ChildCleanupUnavailable
from .fab_browser_pipes_windows import WindowsPipes, PipeCleanupUnavailable
from .fab_browser_profile_windows import PrivateWindowsProfile, ProfileUnavailable
from .fab_browser_process import BrowserUnavailable, MAX_MESSAGE


class WindowsBrowserCleanupUnavailable(BrowserUnavailable):
    """Retain the exact browser/pipe/profile owners; never erase an unproved live profile."""
    def __init__(self, owner, cleanup_error):
        super().__init__('DRAIN_UNPROVED')
        self.owner,self.cleanup_error=owner,cleanup_error


def chrome_program():
    if os.name!='nt':
        raise BrowserUnavailable('PLATFORM_UNVERIFIED')
    # External person-installed Chrome only. No PATH/shell command or installer download.
    for name in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
        root=os.environ.get(name)
        if not root:
            continue
        path=Path(root)/'Google/Chrome/Application/chrome.exe'
        if path.is_absolute() and path.is_file():
            return str(path)
    raise BrowserUnavailable('CHROME_REQUIRED')


class WindowsBrowserProcess:
    """Same serialized call/close/profile interface; only the native Windows transport differs."""
    def __init__(self,root=None,*,program=None,profile_lease=None,manual_login=False,
                 _pipes=None,_profile=None,_child=None,_clock=time.monotonic):
        if type(manual_login) is not bool:
            raise BrowserUnavailable('LOGIN_MODE_INVALID')
        self._lock=threading.RLock()
        self._clock=_clock
        self._process=self._pipes=self._profile_owner=None
        self._drained=False
        self._closed=False
        self._buffer=b''
        self._serial=0
        self.profile=None
        self._profile_lease=profile_lease
        self._manual_login=manual_login
        self._manual_auth_active=False
        self._spawn_attempted=False
        self._retention_revoked=False
        self._download_sink=None
        self._download_uncertain=False
        self._download_cancel_guid=None
        self._program=None
        self._launch_arguments=None
        self._runtime_generation=object()  # Private invocation identity, never native binding proof.
        self._runtime_epoch_token=uuid.uuid4().hex  # Account-record identity only after actual native proof.
        self._physical_proof=None
        self._windows_close_request=None
        self._normal_close_request_uncertain=False
        self._windows_wts_query=None
        # Factories are internal fixture seams, never UI/tool arguments.
        pipes_factory=WindowsPipes if _pipes is None else _pipes
        profile_factory=PrivateWindowsProfile if _profile is None else _profile
        child_factory=ChildProcess if _child is None else _child
        if _child is None and (os.name!='nt' or not callable(getattr(ChildProcess,'drain_job',None))):
            raise BrowserUnavailable('PLATFORM_UNVERIFIED')
        try:
            if profile_lease is not None:
                from .fab_browser_profile_session_windows import WindowsProfileLease
                if type(profile_lease) is not WindowsProfileLease or root is not None:
                    self._profile_lease=None
                    raise BrowserUnavailable('PROFILE_OWNER_REQUIRED')
                self.profile=profile_lease.claim(self)
            elif manual_login:
                raise BrowserUnavailable('PROFILE_OWNER_REQUIRED')
            else:
                self._profile_owner=profile_factory(root)
                self.profile=self._profile_owner.path
            self._verify_profile()
            program=chrome_program() if program is None else str(Path(program))
            if not Path(program).is_absolute() or not Path(program).is_file():
                raise BrowserUnavailable('CHROME_REQUIRED')
            self._program=program
            if not manual_login:
                self._pipes=pipes_factory()
            # Pin/verify profile before native creation. The helper holds all ancestors against rename.
            self._verify_profile()
            environment={key:value for key,value in os.environ.items() if key.upper() in
                         ('SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA',
                          'PATH','HOMEDRIVE','HOMEPATH','LANG','LC_ALL')}
            pipe_scope=nullcontext(([], {'close_fds':True})) if manual_login else self._pipes.spawn_kwargs()
            with pipe_scope as (pipe_arguments,spawn_options):
                managed_arguments=() if manual_login else ('--restore-last-session', '--no-startup-window')
                arguments=(program,'--user-data-dir='+str(self.profile),*managed_arguments,*pipe_arguments,
                           '--no-first-run','--no-default-browser-check','--new-window',
                           'https://www.fab.com/' if manual_login else 'about:blank')
                self._launch_arguments=arguments
                try:
                    self._spawn_attempted=True  # A lost native return is never a pre-spawn abort.
                    self._process=child_factory(arguments,retain_job=True,stdin=subprocess.DEVNULL,
                                                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                                env=environment,cwd=str(self.profile),**spawn_options)
                except ChildCleanupUnavailable as error:
                    # Capture BEFORE spawn_kwargs.__exit__: inheritance cleanup may
                    # independently fail and become the outer exception.
                    self._process=error.owner
                    raise
            if self._profile_lease is not None:
                self._prove_native_binding()
        except BaseException as primary:
            # A constructor may have acquired resources before returning its owner.
            if isinstance(primary,PipeCleanupUnavailable):self._pipes=primary.owner
            elif isinstance(primary,ChildCleanupUnavailable):self._process=primary.owner
            elif (isinstance(primary,ProfileUnavailable) and primary.owner is not None
                  and self._profile_lease is None):
                self._profile_owner=primary.owner
                self.profile=getattr(primary.owner,'path',None)
            try:
                self.close()
            except BaseException as cleanup:
                raise WindowsBrowserCleanupUnavailable(self,cleanup) from primary
            raise BrowserUnavailable('START_FAILED') from primary

    def _verify_profile(self):
        if self._profile_lease is not None:
            self._profile_lease.verify()
        elif self._profile_owner is not None:
            self._profile_owner.verify()
        else:
            raise BrowserUnavailable('PROFILE_OWNER_REQUIRED')

    def _prove_native_binding(self,*,foreground=False):
        from .fab_browser_windows_proof import prove
        self._physical_proof=prove(self,foreground=foreground)
        return self._physical_proof

    def begin_manual_auth(self,*,stop=None):
        with self._lock:
            if not self._manual_login or self._closed:
                raise BrowserUnavailable('MANUAL_WINDOW_UNAVAILABLE')
            if stop is not None and stop.is_set():
                raise BrowserUnavailable('CANCELLED')
            self._prove_native_binding(foreground=True)
            if stop is not None and stop.is_set():
                raise BrowserUnavailable('CANCELLED')
            self._manual_auth_active=True
            return True  # Actual owned-window foreground proof, never account/authentication proof.

    def _account_binding(self):
        with self._lock:
            self._check(None,self._clock()+1)
            self._verify_profile()
            if self._manual_login or self._profile_lease is None:
                raise BrowserUnavailable('PHYSICAL_BINDING_UNPROVED')
            self._prove_native_binding()
            return (self._profile_lease,self._profile_lease.generation,self._runtime_epoch_token)

    def _check(self,stop,deadline):
        if self._closed or self._process is None:
            raise BrowserUnavailable('CLOSED')
        if stop is not None and stop.is_set():
            raise BrowserUnavailable('CANCELLED')
        if self._clock()>=deadline:
            raise BrowserUnavailable('TIMEOUT')
        # A launcher exit is not a Job drain: retain owner/profile and refuse further calls.
        if self._process.poll() is not None:
            raise BrowserUnavailable('CLOSED')

    def _pause(self,stop,deadline):
        self._check(stop,deadline)
        delay=min(.01,max(0,deadline-self._clock()))
        if stop is None:time.sleep(delay)
        else:stop.wait(delay)

    def _set_download_sink(self, sink):
        """Internal physical owner only. Installing an observer never enables browser downloads."""
        with self._lock:
            if self._closed:raise BrowserUnavailable('CLOSED')
            if sink is not None and not callable(sink):raise BrowserUnavailable('DOWNLOAD_EVENT_SHAPE')
            if sink is not None and getattr(self, '_download_sink', None) is not None:
                raise BrowserUnavailable('DOWNLOAD_BUSY')
            if sink is not None and getattr(self, '_download_uncertain', False):
                raise BrowserUnavailable('DOWNLOAD_CLEANUP_UNPROVED')
            self._download_sink = sink
            self._download_cancel_guid = None

    def _advance_download_sink(self, previous, current):
        """Atomically replace a retired selected observer without an event-observation gap."""
        from .fab_browser_download import SelectedDownload
        with self._lock:
            sink=getattr(self,'_download_sink',None)
            if (type(previous)is not SelectedDownload or not previous._retired
                    or type(current)is not SelectedDownload or current.observation().state!='waiting'
                    or getattr(sink,'__self__',None)is not previous
                    or getattr(sink,'__func__',None)is not SelectedDownload.accept
                    or getattr(self,'_download_uncertain',False)):
                raise BrowserUnavailable('DOWNLOAD_OWNER_REQUIRED')
            self._download_sink=current.accept;self._download_cancel_guid=None

    def _poll_selected_download(self, tracker, *, stop=None, seconds=1):
        """One bounded serialized event-pump step; neither completion nor cancel request is a file/auth proof.

        Events arriving after an activation reply stay buffered until the next call. Cancellation deliberately
        uses its own bounded calls rather than the already-set operation stop, so an acknowledgement can arrive.
        The caller owns the total deadline and must close/drain the owned browser if terminal state is unproved.
        """
        from .fab_browser_download import SelectedDownload
        if type(seconds) not in (int, float) or not 0 < seconds <= 30:
            raise BrowserUnavailable('DOWNLOAD_POLL_LIMIT')
        with self._lock:
            sink = getattr(self, '_download_sink', None)
            if (type(tracker) is not SelectedDownload or getattr(sink, '__self__', None) is not tracker
                    or getattr(sink, '__func__', None) is not SelectedDownload.accept):
                raise BrowserUnavailable('DOWNLOAD_OWNER_REQUIRED')
            if stop is not None and stop.is_set():tracker.cancel_requested()
            observed = tracker.observation()
            if (observed.cancel_requested and observed.guid is not None
                    and observed.state not in ('completed', 'canceled')
                    and getattr(self, '_download_cancel_guid', None) != observed.guid):
                self.call('Browser.cancelDownload', {'guid': observed.guid}, seconds=seconds)
                self._download_cancel_guid = observed.guid
            self.call('Browser.getVersion', seconds=seconds)
            return tracker.observation()

    def call(self,method,params=None,*,session=None,stop=None,seconds=15):
        with self._lock:
            if self._manual_login:
                raise BrowserUnavailable('MANUAL_BROWSER_NO_RPC')
            if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:
                raise BrowserUnavailable('TIMEOUT')
            deadline=self._clock()+min(seconds,30)
            self._check(stop,deadline)
            self._serial+=1
            request=dict(id=self._serial,method=method,params=params or {})
            if session is not None:request['sessionId']=session
            try:
                raw=json.dumps(request,separators=(',',':'),allow_nan=False).encode()+b'\0'
                if len(raw)>MAX_MESSAGE:raise BrowserUnavailable('MESSAGE_LIMIT')
                offset=0
                while offset<len(raw):
                    self._check(stop,deadline)
                    count=self._pipes.write(raw[offset:offset+4096])
                    if type(count) is not int or not 0<=count<=min(4096,len(raw)-offset):
                        raise BrowserUnavailable('PROTOCOL')
                    offset+=count
                    if count==0:self._pause(stop,deadline)
                while True:
                    self._check(stop,deadline)
                    if b'\0' not in self._buffer:
                        chunk=self._pipes.read()
                        if chunk is None:
                            self._pause(stop,deadline);continue
                        if type(chunk) is not bytes:raise BrowserUnavailable('PROTOCOL')
                        if not chunk:raise BrowserUnavailable('CLOSED')
                        self._buffer+=chunk
                        if len(self._buffer)>MAX_MESSAGE:raise BrowserUnavailable('MESSAGE_LIMIT')
                        continue
                    raw,self._buffer=self._buffer.split(b'\0',1)
                    reply=json.loads(raw)
                    if type(reply) is not dict:raise BrowserUnavailable('PROTOCOL')
                    reply_id=reply.get('id')
                    if reply_id is not None and type(reply_id) is not int:
                        raise BrowserUnavailable('PROTOCOL')
                    if reply_id!=request['id']:
                        sink=self._download_sink
                        if ('id' not in reply and sink is not None and reply.get('method') in
                                ('Browser.downloadWillBegin','Browser.downloadProgress')):
                            try:sink(reply['method'],reply.get('params'))
                            except BaseException:
                                self._download_uncertain=True
                                raise
                        continue
                    if 'error' in reply or type(reply.get('result')) is not dict:
                        raise BrowserUnavailable('PROTOCOL')
                    self._check(stop,deadline)
                    return reply['result']
            except BrowserUnavailable:
                raise
            except (OSError,ValueError,TypeError,RuntimeError) as error:
                raise BrowserUnavailable('PROTOCOL') from error

    def close(self,*,retain_profile=False):
        with self._lock:
            if type(retain_profile) is not bool or (retain_profile and self._profile_lease is None):
                raise WindowsBrowserCleanupUnavailable(self,RuntimeError('Profile retention unavailable'))
            if not retain_profile:
                self._retention_revoked=True  # Before ANY force, including failed and repeated cleanup.
            elif self._retention_revoked:
                raise WindowsBrowserCleanupUnavailable(self,RuntimeError('Profile retention revoked'))
            failure=None
            deadline=self._clock()+5
            if retain_profile and self._manual_login and self._process is not None and not self._drained:
                try:
                    from . import fab_browser_windows_proof as proof
                    try:
                        proof.request_normal_close(self,deadline)
                    except proof.NormalCloseRequestUncertain as error:
                        if (type(error) is not proof.NormalCloseRequestUncertain or error.owner is not self
                                or error.request is not self._windows_close_request or error.request is None):
                            raise BrowserUnavailable('NORMAL_CLOSE_REQUEST_UNPROVED') from error
                        # Only the helper's exact PostMessage-phase exception can
                        # be settled by passive full-Job closure, never a fresh proof refusal.
                        self._normal_close_request_uncertain=True
                except BaseException as error:
                    failure=error
                    self._retention_revoked=True
            if retain_profile and not self._manual_login and not self._closed and self._process is not None:
                try:
                    # Browser.close can close its pipe before returning a matching reply.
                    # EOF/timeout is not drain proof: passive native completion below is mandatory.
                    self.call('Browser.close',seconds=max(.001,deadline-self._clock()))
                except BrowserUnavailable as error:
                    if error.code not in ('CLOSED','TIMEOUT'):
                        failure=error
                        self._retention_revoked=True
                except BaseException as error:
                    failure=error
                    self._retention_revoked=True
            self._closed=True
            self._buffer=b''
            self._download_sink=None
            if self._pipes is not None:
                try:
                    self._pipes.close()
                    self._pipes=None
                except Exception as error:failure=failure or error
            if self._process is not None and not self._drained:
                try:
                    if retain_profile:
                        self._process.finish_job_passive(deadline)
                    else:
                        self._retention_revoked=True
                        self._process.drain_job(deadline)
                    self._drained=True
                except Exception as error:failure=failure or error
            try:
                # The proof helper may own a WTS response even after the browser
                # Job is empty. Unknown query/free ownership keeps its profile pins.
                from .fab_browser_windows_proof import require_query_cleanup
                require_query_cleanup(self)
            except BaseException as error:
                failure=failure or error
            # Never infer emptiness from leader poll or an attempted termination.
            if (failure is None and self._profile_lease is not None and self._pipes is None and
                    not getattr(self._profile_lease,'_released',False)):
                try:
                    if self._process is not None and self._drained:
                        # Forced cleanup can never publish a retained profile.
                        if retain_profile and self._retention_revoked:
                            raise BrowserUnavailable('PROFILE_RETENTION_REVOKED')
                        self._profile_lease.finish_drained(self,retain=retain_profile)
                    elif self._process is None and not self._spawn_attempted:
                        if self._profile_lease._browser is self:
                            self._profile_lease.abort_claim_before_spawn(self)
                        elif self._profile_lease._browser is None:
                            self._profile_lease.cancel_pending()
                        else:
                            raise BrowserUnavailable('PROFILE_OWNER_REQUIRED')
                    else:
                        raise BrowserUnavailable('PROFILE_DRAIN_UNPROVED')
                    self.profile=None
                except BaseException as error:failure=failure or error
            if (failure is None and self._profile_owner is not None and self._pipes is None and
                    ((self._process is None and not self._spawn_attempted) or self._drained)):
                try:
                    self._profile_owner.remove(deadline)
                    self._profile_owner=None
                    self.profile=None
                except Exception as error:failure=failure or error
            if failure is not None:
                raise WindowsBrowserCleanupUnavailable(self,failure) from failure
            if self._process is not None and not self._drained:
                raise WindowsBrowserCleanupUnavailable(self,RuntimeError('Retained Job is unproved'))
            if self._spawn_attempted and self._process is None:
                raise WindowsBrowserCleanupUnavailable(self,RuntimeError('Native launch ownership unknown'))

    def __enter__(self):return self
    def __exit__(self,*_args):self.close()
