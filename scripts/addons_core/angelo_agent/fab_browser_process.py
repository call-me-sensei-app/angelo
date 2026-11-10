# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A separate visible Chrome process/profile, controlled over private inherited CDP pipes.

Connection prototype, not a verified Fab transport. No existing browser profile, cookies, passwords, tokens,
network headers or account storage is read. Mac only until other platform pipe/process owners are implemented and
accepted. Chrome is an external, person-installed dependency; nothing downloads or bundles a browser here.
"""
import json
import os
from . import private_roots
from .fab_browser_ownership import ProcessOwner, browser_bundle, OwnershipUnavailable, snapshot, identity
from .fab_browser_diagnostics import write_record

from pathlib import Path
import select
import shutil
import stat
import subprocess
import sys
import threading
import uuid
import time

MAX_MESSAGE = 2 * 1024 * 1024


class BrowserUnavailable(RuntimeError):
    """Closed failures: never expose Chrome protocol responses or credential-bearing navigation URLs."""
    def __init__(self, code='UNAVAILABLE', *, reason=None):
        self.code = code
        reasons = set(OwnershipUnavailable.CODES.values()) | {'LEADER_UNREAPED', 'OWNER_MISSING',
                  'DRAIN_OS_ERROR', 'DRAIN_VALUE_ERROR', 'DRAIN_SUBPROCESS_ERROR', 'DRAIN_INTERNAL_ERROR', 'MANUAL_NONZERO_EXIT', 'MANUAL_NORMAL_QUIT_REFUSED', 'MANUAL_LEASE_UNPROVED',
                  'MANUAL_PROFILE_ARGUMENT_UNPROVED', 'MANUAL_SINGLETON_CHANGED', 'MANUAL_COCOA_THREAD_UNSUPPORTED',
                  'MANUAL_CANCEL_SIGNAL_USED', 'MANUAL_RETENTION_REVOKED'}
        reasons.update({
            'PROFILE_BUSY',
            'PROFILE_CHANGED',
            'PROFILE_CREATION_INCOMPLETE',
            'PROFILE_DRAIN_UNPROVED',
            'PROFILE_EXISTS',
            'PROFILE_INSPECTION_LIMIT',
            'PROFILE_INSPECTION_UNPROVED',
            'PROFILE_IN_USE',
            'PROFILE_LOCK_CHANGED',
            'PROFILE_LOCK_CLOSE_UNPROVED',
            'PROFILE_METADATA_CHANGED',
            'PROFILE_METADATA_CLOSE_UNPROVED',
            'PROFILE_METADATA_SHAPE',
            'PROFILE_METADATA_UNSAFE',
            'PROFILE_MISSING',
            'PROFILE_PLATFORM_UNVERIFIED',
            'PROFILE_PRIVACY_REGISTRATION_FAILED',
            'PROFILE_RECOVERY_REQUIRED',
            'PROFILE_LEGACY_LAYOUT_REQUIRES_RECOVERY',
            'PROFILE_REOPEN_INCOMPLETE',
            'PROFILE_RETENTION_POLICY',
            'PROFILE_ROOT_CHANGED',
            'PROFILE_ROOT_UNSAFE',
        })
        self.reason = reason if type(reason) is str and reason in reasons else None
        super().__init__('Fab browser connection: ' + code)


def chrome_program():
    """Find an installed Chrome; never a shell command, download or user's browser profile."""
    if sys.platform != 'darwin':
        raise BrowserUnavailable('PLATFORM_UNVERIFIED')
    candidates = ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                  '/Applications/Chromium.app/Contents/MacOS/Chromium',
                  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser')
    for value in candidates:
        path = Path(value)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise BrowserUnavailable('CHROME_REQUIRED')


def _pipe():
    """Keep source descriptors clear of CDP's fixed child descriptors 3 and 4."""
    import fcntl
    read, write = os.pipe()
    copies = []
    try:
        for fd in (read, write):
            copies.append(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10))
        return tuple(copies)
    except BaseException:
        for fd in copies:
            os.close(fd)
        raise
    finally:
        os.close(read)
        os.close(write)


class BrowserProcess:
    """One visible browser; a new private ephemeral profile on every connection/account change.

    Worker calls are serialized. The parent PID is deliberately not reaped until cleanup finishes, keeping the
    original group identity stable for discovery. Every signal separately checks the helper's identity/marker.
    Failed drain retains the
    private profile and raises; it never reports sign-out complete or removes a profile still used by a child.
    """
    def __init__(self, root=None, *, program=None, profile_lease=None, manual_login=False):
        if type(manual_login)is not bool:raise BrowserUnavailable('LOGIN_MODE_INVALID')
        if manual_login and profile_lease is None:
            raise BrowserUnavailable('MANUAL_PROFILE_LEASE_REQUIRED')
        self._manual_login=manual_login
        self._manual_auth_active=False
        self._manual_graceful_drained=False
        self._manual_retention_revoked=False
        self._manual_cancel_signal_used=False
        self._manual_helper_drained=False
        self._manual_singletons=None
        self._lock = threading.RLock()
        self._pid, self._read, self._write = None, None, None
        self._buffer, self._serial, self._closed = b'', 0, False
        self._owner = None
        self._download_sink = None
        self._download_uncertain = False
        self._download_cancel_guid = None
        self._profile_lease = profile_lease
        self._program = None
        self._leader_identity = None
        self._record_failure = None
        self._marker = uuid.uuid4().hex
        self.profile = None
        if sys.platform != 'darwin' or not hasattr(os, 'posix_spawn'):
            raise BrowserUnavailable('PLATFORM_UNVERIFIED')
        scratch = private_roots.scratch_root()
        if root is not None and Path(root).resolve() != scratch.resolve():
            raise BrowserUnavailable('PROFILE_ROOT')
        # The family is declared at private_roots import, so crashed profiles stay private in a fresh process.
        if scratch.is_symlink() or any(part.is_symlink() for part in scratch.parents):
            raise BrowserUnavailable('PROFILE_ROOT')
        program = chrome_program() if program is None else str(Path(program))
        # program injection is an internal fixture seam, never an application-agent or UI argument.
        if not Path(program).is_absolute() or not Path(program).is_file():
            raise BrowserUnavailable('CHROME_REQUIRED')
        try:
            browser_bundle(program)  # Refuse an untracked executable layout before any child can start.
        except OwnershipUnavailable:
            raise BrowserUnavailable('PLATFORM_UNVERIFIED') from None
        try:
            self._program = program
            if profile_lease is None:
                self.profile = private_roots.allocate(private_roots.FAB_BROWSER_PREFIX)
            else:
                from .fab_browser_profile import ProfileLease, ProfileUnavailable
                if type(profile_lease) is not ProfileLease:
                    self._profile_lease = None
                    raise BrowserUnavailable('PROFILE_OWNER_REQUIRED')
                try:self.profile = profile_lease.claim(self, program)
                except ProfileUnavailable as error:
                    refused = BrowserUnavailable('PROFILE_UNAVAILABLE', reason=error.code)
                    refused.owner = self
                    raise refused from None
            os.chmod(self.profile, 0o700)
            self._profile_identity = (self.profile.stat().st_dev, self.profile.stat().st_ino)
        except BaseException as error:
            if profile_lease is None:
                raise
            refused = error if isinstance(error, BrowserUnavailable) else BrowserUnavailable('PROFILE_START_FAILED')
            refused.owner = self
            raise refused from None
        descriptors = []
        try:
            self._record('allocated', required=True)
            incoming=outgoing=None
            if not self._manual_login:
                incoming, self._write = _pipe()
                descriptors.extend((incoming, self._write))
                self._read, outgoing = _pipe()
                descriptors.extend((self._read, outgoing))
            import fcntl
            original_null = os.open(os.devnull, os.O_RDWR)
            try:
                null = fcntl.fcntl(original_null, fcntl.F_DUPFD_CLOEXEC, 10)
            finally:
                os.close(original_null)
            descriptors.append(null)
            actions=[] if self._manual_login else[(os.POSIX_SPAWN_DUP2,incoming,3),(os.POSIX_SPAWN_DUP2,outgoing,4)]
            actions += [(os.POSIX_SPAWN_DUP2, null, fd) for fd in (0, 1, 2)]
            args = [program, '--user-data-dir=' + str(self.profile)]
            if not self._manual_login:
                # The last three keep pages running behind Angelo (fab_browser_background.MANAGED_FLAGS).
                args += ['--restore-last-session', '--no-startup-window', '--remote-debugging-pipe',
                         '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
                         '--disable-renderer-backgrounding']
            args += ['--no-first-run','--no-default-browser-check','--new-window',
                     'https://www.fab.com/' if self._manual_login else 'about:blank']
            env = {key: value for key, value in os.environ.items()
                   if key in ('HOME', 'PATH', 'LANG', 'LC_ALL', 'DISPLAY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR')}
            env['ANGELO_FAB_PROCESS_OWNER'] = self._marker
            self._pid = os.posix_spawn(program, args, env, file_actions=actions, setpgroup=0)
            self._owner = ProcessOwner(self._pid, self._marker, program, str(self.profile))
            reason = None
            try:
                row = snapshot(time.monotonic() + 2).get(self._pid)
                if row is not None:
                    self._leader_identity = identity(row)
                    if self._manual_login:self._owner.known[self._pid]=self._leader_identity
            except OwnershipUnavailable as error:
                reason = error.code
            self._record('spawned', reason=reason, required=True)
            if self._manual_login and self._leader_identity is None:
                raise BrowserUnavailable('MANUAL_OWNERSHIP_UNPROVED')
            for fd in ((null,)if self._manual_login else(incoming,outgoing,null)):
                os.close(fd)
                descriptors.remove(fd)
        except BaseException:
            for fd in descriptors:
                try: os.close(fd)
                except OSError: pass
            self._read = self._write = None
            self.close()
            raise BrowserUnavailable('START_FAILED') from None

    def begin_manual_auth(self, *, stop=None):
        """Serialize entry to the human privacy phase with the native metadata reader on this same lock."""
        with self._lock:
            if not getattr(self,'_manual_login',False)or self._closed:
                raise BrowserUnavailable('MANUAL_WINDOW_UNAVAILABLE')
            from .fab_browser_manual_window import foreground_manual_window
            if stop is not None and stop.is_set():
                raise BrowserUnavailable('CANCELLED')
            ready=foreground_manual_window(self, stop=stop)
            if stop is not None and stop.is_set():
                raise BrowserUnavailable('CANCELLED')
            self._manual_auth_active=True
            return ready

    def _check(self, stop, deadline):
        if self._closed or self._pid is None:
            raise BrowserUnavailable('CLOSED')
        if stop is not None and stop.is_set():
            raise BrowserUnavailable('CANCELLED')
        if time.monotonic() >= deadline:
            raise BrowserUnavailable('TIMEOUT')

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

    def call(self, method, params=None, *, session=None, stop=None, seconds=15):
        """Internal CDP call. No generic command/evaluation surface is exposed to application agents."""
        with self._lock:
            if getattr(self,'_manual_login',False):raise BrowserUnavailable('MANUAL_BROWSER_NO_RPC')
            deadline = time.monotonic() + min(max(seconds, 0), 30)
            self._check(stop, deadline)
            self._serial += 1
            request = dict(id=self._serial, method=method, params=params or {})
            if session is not None:
                request['sessionId'] = session
            raw = json.dumps(request, separators=(',', ':'), allow_nan=False).encode() + b'\0'
            if len(raw) > MAX_MESSAGE:
                raise BrowserUnavailable('MESSAGE_LIMIT')
            offset = 0
            try:
                while offset < len(raw):
                    self._check(stop, deadline)
                    if select.select([], [self._write], [], 0.1)[1]:
                        offset += os.write(self._write, raw[offset:offset + 4096])
                while True:
                    self._check(stop, deadline)
                    if b'\0' not in self._buffer:
                        if not select.select([self._read], [], [], 0.1)[0]:
                            continue
                        chunk = os.read(self._read, 65536)
                        if not chunk:
                            raise BrowserUnavailable('CLOSED')
                        self._buffer += chunk
                        if len(self._buffer) > MAX_MESSAGE:
                            raise BrowserUnavailable('MESSAGE_LIMIT')
                        continue
                    raw, self._buffer = self._buffer.split(b'\0', 1)
                    reply = json.loads(raw)
                    if type(reply) is not dict:
                        raise BrowserUnavailable('PROTOCOL')
                    if reply.get('id') != request['id']:
                        sink = getattr(self, '_download_sink', None)
                        if ('id' not in reply and sink is not None
                                and reply.get('method') in ('Browser.downloadWillBegin', 'Browser.downloadProgress')):
                            try:sink(reply['method'], reply.get('params'))
                            except BaseException:
                                self._download_uncertain = True
                                raise
                        continue  # No Network/Storage event subscriptions; unrelated page events are not retained.
                    if 'error' in reply or type(reply.get('result')) is not dict:
                        raise BrowserUnavailable('PROTOCOL')
                    self._check(stop, deadline)
                    return reply['result']
            except (OSError, ValueError, TypeError):
                raise BrowserUnavailable('PROTOCOL') from None

    def _record(self, phase, *, reason=None, required=False):
        """Private observations aid recovery; failures never authorize cleanup or expose the marker."""
        try:
            write_record(self.profile, self._profile_identity, self._marker, self._pid,
                         self._leader_identity, phase, reason,
                         helper=getattr(self._owner, 'last_refusal', None))
        except (OSError, ValueError, TypeError, AttributeError):
            self._record_failure = 'RECOVERY_RECORD_UNAVAILABLE'
            if required:
                raise BrowserUnavailable('RECOVERY_RECORD_UNAVAILABLE') from None

    def close(self, *, retain_profile=False):
        """Drain first; default erases. Explicit retention requires a coherent exclusive profile lease."""
        with self._lock:
            if type(retain_profile) is not bool or (retain_profile and getattr(self, '_profile_lease', None) is None):
                refused = BrowserUnavailable('PROFILE_RETENTION_UNAVAILABLE')
                refused.owner = self
                raise refused
            if getattr(self, '_manual_login', False):
                if retain_profile and (getattr(self, '_manual_retention_revoked', False) or
                                       getattr(self, '_manual_cancel_signal_used', False)):
                    reason = ('MANUAL_CANCEL_SIGNAL_USED' if getattr(self, '_manual_cancel_signal_used', False)
                              else 'MANUAL_RETENTION_REVOKED')
                    refused = BrowserUnavailable('MANUAL_GRACEFUL_CLOSE_UNPROVED', reason=reason)
                    refused.owner = self
                    raise refused
                if not retain_profile:
                    self._manual_retention_revoked = True  # Purge intent cannot later become retention.
            self._closed = True
            self._download_sink = None
            if getattr(self, '_pipe_close_failed', False):
                refused = BrowserUnavailable('PIPE_CLEANUP_UNPROVED')
                refused.owner = self
                raise refused
            for name in ('_read', '_write'):
                fd = getattr(self, name)
                if fd is not None:
                    try:os.close(fd)
                    except OSError:
                        # Do not retry an uncertain descriptor number: another thread could reuse it.
                        self._pipe_close_failed = True
                        refused = BrowserUnavailable('PIPE_CLEANUP_UNPROVED')
                        refused.owner = self
                        raise refused from None
                    setattr(self, name, None)
            if self._pid is not None:
                try:
                    self._record('closing')
                    if self._owner is None:
                        raise BrowserUnavailable('DRAIN_UNPROVED', reason='OWNER_MISSING')
                    if getattr(self, '_manual_login', False):
                        from .fab_browser_manual_close import drain_manual
                        drain_manual(self, retain=retain_profile)
                    else:
                        self._owner.drain()
                    pid, _status = os.waitpid(self._pid, os.WNOHANG)
                    if pid != self._pid:
                        raise BrowserUnavailable('DRAIN_UNPROVED', reason='LEADER_UNREAPED')
                    self._record('drained')
                    self._pid = None
                    if getattr(self,'_manual_login',False)and retain_profile:
                        if not os.WIFEXITED(_status)or os.WEXITSTATUS(_status)!=0:
                            raise BrowserUnavailable('MANUAL_GRACEFUL_CLOSE_UNPROVED',reason='MANUAL_NONZERO_EXIT')
                        self._manual_graceful_drained=True
                except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as error:
                    if isinstance(error, OwnershipUnavailable):
                        reason = error.code
                    elif isinstance(error, BrowserUnavailable) and error.reason:
                        reason = error.reason
                    elif isinstance(error, OSError):
                        reason = 'DRAIN_OS_ERROR'
                    elif isinstance(error, ValueError):
                        reason = 'DRAIN_VALUE_ERROR'
                    elif isinstance(error, subprocess.SubprocessError):
                        reason = 'DRAIN_SUBPROCESS_ERROR'
                    else:
                        reason = 'DRAIN_INTERNAL_ERROR'
                    self._record('drain_refused', reason=reason)
                    refused = BrowserUnavailable('DRAIN_UNPROVED', reason=reason)
                    refused.owner = self
                    raise refused from None
            if (getattr(self,'_manual_login',False)and retain_profile
                    and not getattr(self,'_manual_graceful_drained',False)):
                refused=BrowserUnavailable('MANUAL_GRACEFUL_CLOSE_UNPROVED');refused.owner=self;raise refused
            lease = getattr(self, '_profile_lease', None)
            if lease is not None and not getattr(lease, '_released', False):
                from .fab_browser_profile import ProfileUnavailable
                try:
                    if getattr(self, '_manual_login', False) and retain_profile:
                        from .fab_browser_manual_close import retire_singletons
                        retire_singletons(self)
                    if lease._browser is self:
                        lease.finish_drained(self, retain=retain_profile)
                    elif lease._browser is None and not retain_profile:
                        lease.cancel_pending(self._program)
                    else:
                        raise ProfileUnavailable('PROFILE_DRAIN_UNPROVED', lease)
                except (OSError, ValueError, RuntimeError) as error:
                    refused = BrowserUnavailable('PROFILE_CLEANUP_UNPROVED', reason=(error.reason if isinstance(error, BrowserUnavailable) and error.reason
                                                                      else getattr(error, 'code', 'PROFILE_OWNER_FAILURE')))
                    refused.owner = self
                    raise refused from None
                self.profile = None
                return
            if self.profile is not None:
                info = self.profile.lstat()
                if (info.st_dev, info.st_ino) != self._profile_identity or not stat.S_ISDIR(info.st_mode):
                    raise BrowserUnavailable('PROFILE_CHANGED')
                shutil.rmtree(self.profile)
                self.profile = None
                private_roots.prune()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
