# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""The process's real Fab connection, shared Session and cached public UI state.

Only explicit person commands open a browser. Browser/profile/metadata/download work
runs through one worker. The main-thread pump alone publishes public snapshots and
creates the Session from a verified transport. A human Done acknowledgement is not
account proof; missing account/download/native producers remain unavailable.
"""
from functools import partial
import threading
import uuid

from . import fab_failure_report, fab_host_state as records, fab_login_transition as login
from .fab_catalog import FabError
from .fab_host_jobs import Job, Stop, Worker


def _main():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('Fab UI ownership requires the main thread')


def _native_ready():
    from .blender_asset_commit import native_api
    try:
        native_api('glb')
        return True
    except Exception:
        return False


def _session(transport, root):
    if root is None:
        raise FabError('CACHE_UNAVAILABLE')  # Registration owns path preparation, never a draw/pump fallback.
    from . import fab_preferences, ui
    return fab_preferences.create_session(root, transport.account_scope, transport, ui.preferences())


def _observe(connection, stop):
    from .fab_browser_transport import BrowserTransport
    return BrowserTransport.observe(connection, stop)


def _reason(error):
    if getattr(error, 'fab_reason', None) == 'UNDO_REQUIRED':
        return 'UNDO_REQUIRED'  # A known preference refusal, never a build/account claim.
    code = getattr(error, 'code', None)
    if code in {'AUTH_REQUIRED', 'ACCOUNT_PROOF_PENDING'}:
        return 'ACCOUNT_UNVERIFIED'
    if code in {'LISTING_PROOF_PENDING', 'INVALID_METADATA'}:
        return 'METADATA_UNAVAILABLE'
    if code == 'DOWNLOAD_PROOF_PENDING':
        return 'IMPORT_UNAVAILABLE'
    return fab_failure_report.reported(error, code, {
        'CANCELLED': 'CANCELLED', 'EXPIRED': 'EXPIRED', 'BROWSER_REQUIRED': 'BROWSER_REQUIRED',
        'UNSUPPORTED_PLATFORM': 'PLATFORM_UNAVAILABLE', 'PLATFORM_UNVERIFIED': 'PLATFORM_UNAVAILABLE', 'CHROME_REQUIRED': 'BROWSER_REQUIRED',
        'USER_SIGN_IN_PENDING': 'SIGN_IN_REQUIRED', 'PURCHASE_REQUIRED': 'PURCHASE_REQUIRED', 'ACQUISITION_REQUIRED': 'ACQUISITION_REQUIRED',
        'TERMS_REQUIRED': 'LICENSE_UNVERIFIED', 'UNSUPPORTED': 'UNSUPPORTED_FORMAT'}.get(code, 'SERVICE_UNAVAILABLE'))


class Driver:
    """Worker-owned browser references, including an exact partial cleanup owner."""
    def __init__(self, factory, observe):
        self.factory, self.observe = factory, observe
        self.connection = self.partial = self.transport = None
        self.cleanup_required = False
        self._idle_lock, self._idle_failure, self._idle_cause = threading.Lock(), None, None
        self.lifetime = threading.Event()

    def connect(self, cancel, acknowledge):
        stop = Stop(cancel, self.lifetime, seconds=120)
        if self.partial is not None:
            raise FabError('UNAVAILABLE')
        if self.connection is None:
            self.lifetime = threading.Event()
            stop = Stop(cancel, self.lifetime, seconds=120)
            try:
                self.connection = self.factory(stop=self.lifetime, resume=True, login_mode="manual")
            except Exception as error:
                self.partial = getattr(error, 'owner', None)
                raise
        stop.check()
        try:
            if not acknowledge and login.mode(self.connection) == 'manual':
                self.connection.begin_login(stop)  # Actual owned foreground proof, no DOM/account reads.
                return dict(transport=None, status='sign_in_required', reason_code='SIGN_IN_REQUIRED', can_search=False)
            if acknowledge:
                self.connection = login.acknowledge(self.connection, self.transport, stop)
                self.transport = None
        except login.TransitionFailed as error:
            self.partial, self.connection, self.cleanup_required = error.owner, None, True
            raise
        stop.check()
        try:
            transport = self.observe(self.connection, stop)
            stop.check()
            self.transport = transport
            return dict(transport=transport, status='connected', reason_code=None, can_search=True)
        except FabError as error:
            if error.code != 'AUTH_REQUIRED':
                raise
            # Retained managed auth failure is closed: no automatic CDP login, purge or old-runtime loop.
            return dict(transport=None, status='error', reason_code='ACCOUNT_UNVERIFIED', can_search=True)

    def search(self, query, limit, cancel):
        if self.connection is None:
            raise FabError('AUTH_REQUIRED')
        return self.connection.public_search(query, limit, Stop(cancel, self.lifetime, seconds=120))

    def close(self, retain=False):
        self.lifetime.set()  # Cleanup deliberately does not use this canceled stop.
        try:
            if self.partial is not None:
                self.partial.close()  # Exact factory failure owner; default erase, never inferred retained success.
                self.partial = None
            if self.connection is not None:
                normal = getattr(self.connection, 'close_normal_shutdown', None)
                if retain and callable(normal):
                    normal()  # Private owner validates its own verified-profile witness, never a UI connected flag.
                else:
                    self.connection.close(retain_profile=False)  # Default purge, including older/unverified owners.
                self.connection = None
        except (Exception, SystemExit, KeyboardInterrupt):
            self.cleanup_required = True
            raise
        self.transport = None
        self.cleanup_required = False
        return None

    def service_idle(self):
        connection = self.connection
        service = getattr(connection, 'service_idle', None)
        if connection is None or not callable(service) or self.partial is not None:
            return False
        try:
            service(Stop(self.lifetime, seconds=1), seconds=0.25)
        except (Exception, SystemExit, KeyboardInterrupt) as failure:
            # The tainted exact connection cannot serve another job. Revoke the
            # observed transport even if Main has not bound its Session yet.
            try:
                if self.transport is not None:
                    self.transport.revoke()
            except (Exception, SystemExit, KeyboardInterrupt):
                pass  # Browser cleanup still runs; lifetime cancellation independently blocks new work.
            self.lifetime.set()
            try:
                connection.close(retain_profile=False)  # Same worker as observer; cleanup ignores stop.
            except (Exception, SystemExit, KeyboardInterrupt):
                self.cleanup_required = True
            else:
                self.connection = None
                self.cleanup_required = False
            with self._idle_lock:
                if self._idle_failure is None:
                    self._idle_failure = self.cleanup_required
                    self._idle_cause = failure  # Exact private cause; never placed in public/model records.
            return False
        return not self.lifetime.is_set()

    def take_idle_failure(self):
        with self._idle_lock:
            value, self._idle_failure = self._idle_failure, None
            return value


class Host:
    def __init__(self, factory, *, session_factory=_session, observe=_observe, native_ready=_native_ready, state_root=None):
        self.driver = Driver(factory, observe)
        self.worker = Worker(idle=self.driver.service_idle)
        if session_factory is _session:
            self.session_factory = partial(_session, root=state_root)
        else:
            self.session_factory = session_factory
        self.native_ready = native_ready
        self.session = self.transport = self.selection = None
        self.view = records.initial(uuid.uuid4().hex, available=True)
        self.job = self.import_pending = self.retired_import = self.active_import = None
        self.closed = False
        self.dirty = True

    def snapshot(self):
        _main()
        return records.public(self.view)

    def _clear(self):
        self.selection = None
        self.view.update(selected_item_id=None, selected_item=None, selected_variant_id=None,
                         lookup_status='idle', variants=[], import_ready=False)

    def _revoke(self):
        if self.transport is not None:
            self.transport.revoke()  # In-memory revocation, not a browser operation.
        if self.session is not None:
            self.session.disconnect()
        self.transport = self.session = None
        self._clear()

    def _next(self):
        self.view['request_id'] += 1  # Never reset on account changes, cancellation or a new Session.
        self.dirty = True
        return self.view['request_id']

    def _start(self, kind, work, *, cleanup=None, replace=True):
        _main()
        request = self.view['request_id'] + 1
        job = Job(request, kind, work, cleanup=cleanup)
        previous = self.job if replace else None
        if self.retired_import is not None and self.retired_import.job is previous:
            previous = None  # The exact retired slot owns failed result cleanup; do not skip browser close.
        self.worker.submit(job, replace=replace, previous=previous)
        self.view['request_id'] = request
        self.dirty = True
        self.job = job
        return job

    def connect(self):
        _main()
        self.pump()
        if self.closed or self.active_import is not None or self.retired_import is not None or self.view['can_connect'] is not True:
            return None
        acknowledge = self.view['status'] == 'sign_in_required'
        self._revoke()
        if acknowledge:
            self.view['generation'] = uuid.uuid4().hex  # Old UI/approval tickets cannot cross a login transition.
        self.view.update(status='verifying' if acknowledge else 'connecting', reason_code=None,
                         can_connect=False, can_disconnect=True, can_search=False)
        job = self._start('connect', lambda cancel: self.driver.connect(cancel, acknowledge),
                          cleanup=lambda result: result['transport'].revoke() if result and result.get('transport') else None)
        return job.request

    def disconnect(self, *, normal_shutdown=False):
        _main()
        self.driver.lifetime.set()
        self._revoke()
        if self.active_import is not None:
            self.active_import.cancel()
        if self.import_pending is not None:
            self.import_pending.cancel()
        self.view.update(status='disconnecting', reason_code=None, can_connect=False,
                         can_disconnect=False, can_search=False)
        return self._start('close', lambda _cancel: self.driver.close(normal_shutdown)).request

    def search(self, query, limit=8):
        _main()
        self.pump()
        if self.active_import is not None or self.retired_import is not None:
            return None
        if (self.closed or not self.view['can_search'] or type(query) is not str
                or not 0 < len(query) <= 200 or not query.isprintable() or type(limit) is not int or not 1 <= limit <= 20):
            return None
        self._clear()
        self.view.update(query=query, search_status='searching', reason_code=None)
        return self._start('search', lambda cancel: self.driver.search(query, limit, cancel)).request

    def current_session(self):
        _main()
        if self.session is None or not self.session.active or self.driver.lifetime.is_set():
            raise FabError('AUTH_REQUIRED')
        return self.session

    def lookup(self, item_id, show=False):   # show: the person adds it on Fab next.
        _main()
        self.pump()
        if self.active_import is not None or self.retired_import is not None:
            return None
        records.identity(item_id)
        if self.closed or not any(row['id'] == item_id for row in self.view['items']):
            return None
        try:
            session = self.current_session()
        except FabError:
            self.view['reason_code'] = 'AUTH_REQUIRED'
            self.dirty = True
            return None
        self._clear()
        self.view.update(selected_item_id=item_id, lookup_status='loading', reason_code=None)
        return self._start('lookup', lambda cancel: session.lookup(item_id, cancel, show)).request

    def select(self, item_id, variant_id, *, expected_generation, expected_request_id):
        _main()
        self.pump()
        if self.active_import is not None or self.retired_import is not None:
            return False
        if (self.closed or (expected_generation, expected_request_id) != self.ticket()
                or item_id != self.view['selected_item_id'] or not (self.view.get('selected_item') or {}).get('import_ready')):
            return False
        try:
            session = self.current_session()
            selection = session.selection(item_id, variant_id)
        except FabError as error:
            self.view.update(import_ready=False, reason_code=_reason(error))
            self.dirty = True
            return False
        self._next()
        self.selection = selection
        ready = self.native_ready()
        self.view.update(selected_variant_id=variant_id, import_ready=ready,
                         reason_code=None if ready else 'IMPORT_UNAVAILABLE')
        return True

    def ticket(self):
        return self.view['generation'], self.view['request_id']

    def claim_import(self, pending):
        _main()
        if self.active_import is not None or self.retired_import is not None:
            raise FabError('UNAVAILABLE')
        self.active_import = pending
        self.view['import_ready'] = False
        self.dirty = True

    def release_import(self, pending):
        _main()
        if self.active_import is pending:
            self.active_import = None
        elif self.active_import is not None:
            raise RuntimeError('Another exact Fab import owns this facade')

    def retire_import(self, retired):
        _main()
        if self.retired_import is not None and self.retired_import is not retired:
            raise RuntimeError('The previous Fab import still owns cleanup')
        self.retired_import = retired
        self.view.update(import_ready=False, reason_code='IMPORT_CLEANUP_REQUIRED')
        self.dirty = True

    def submit_agent(self, work, *, cleanup=None, import_owner=None):
        _main()
        # Continuations may enqueue from the current pump; never recursively poll imports.
        if (self.retired_import is not None
                or self.active_import is not None and self.active_import is not import_owner):
            raise FabError('UNAVAILABLE')
        session = self.current_session()
        return self._start('agent', lambda cancel: work(session, cancel), cleanup=cleanup, replace=False)

    def use(self, window, area, region, item_id, variant_id, *, expected_generation, expected_request_id, at=None):
        _main()
        self.pump()
        if ((expected_generation, expected_request_id) != self.ticket() or not self.view['import_ready']
                or self.import_pending is not None or self.retired_import is not None or item_id != self.view['selected_item_id']
                or variant_id != self.view['selected_variant_id'] or not self.native_ready()):
            return None
        session = self.current_session()
        if session.selection(item_id, variant_id) != self.selection:
            raise FabError('EXPIRED')
        from . import fab_library_agent
        self.import_pending = fab_library_agent.human_use(self, window, area, region, item_id, variant_id, at=at)
        self.view['import_ready'] = False
        return self._next()

    def pump(self):
        _main()
        changed, self.dirty = self.dirty, False
        idle_failure = self.driver.take_idle_failure()
        if idle_failure is not None:
            self._revoke()
            if self.active_import is not None:
                self.active_import.cancel()
            self._next()  # Actual taint invalidates hits; routine no-event idle service never changes this nonce.
            self.view.update(status='cleanup_required' if idle_failure else 'error',
                             reason_code='CLEANUP_REQUIRED' if idle_failure else 'SERVICE_UNAVAILABLE',
                             can_connect=not idle_failure, can_disconnect=idle_failure, can_search=False)
            changed = True
        if self.retired_import is not None:
            try:
                if self.retired_import.poll():
                    self.retired_import = None
                    if self.view['reason_code'] == 'IMPORT_CLEANUP_REQUIRED':
                        self.view['reason_code'] = None
                    changed = True
            except Exception:
                self.view.update(import_ready=False, reason_code='IMPORT_CLEANUP_REQUIRED')
        if self.session is not None and not self.session.active:
            self._revoke()
            self._next()
            self.view.update(status='error', reason_code='ACCOUNT_CHANGED', can_connect=True)
            changed = True
        job = self.job
        if job is not None and job.kind != 'agent' and job.done.is_set():
            self.job = None
            if job.request != self.view['request_id']:
                if job.value is not None and job.cleanup is not None:
                    job.cleanup(job.value)
                    job.value = None
            else:
                try:
                    value = job.take()
                    if job.kind == 'connect':
                        transport = value['transport']
                        if transport is not None:
                            self.session = self.session_factory(transport)
                            self.transport = transport
                        self.view.update(status=value['status'], reason_code=value['reason_code'],
                                         can_connect=transport is None, can_disconnect=True,
                                         can_search=value['can_search'])
                    elif job.kind == 'close':
                        self.view = dict(records.initial(self.view['generation'], available=True),
                                         request_id=self.view['request_id'])
                    elif job.kind == 'search':
                        self.view.update(items=records.discoveries(value), search_status='ready')
                    elif job.kind == 'lookup':
                        item, variants, reason = records.lookup(value)
                        if item['id'] != self.view['selected_item_id']:
                            raise FabError('EXPIRED')
                        self.view.update(selected_item=item, variants=variants, lookup_status='ready', reason_code=reason)
                except Exception as error:
                    cleanup = self.driver.partial is not None or job.kind == 'close'
                    self.view.update(reason_code='CLEANUP_REQUIRED' if cleanup else _reason(error),
                                     status='cleanup_required' if cleanup else 'error' if job.kind == 'connect' else self.view['status'],
                                     can_connect=not cleanup, can_disconnect=self.driver.connection is not None or cleanup)
                    if job.kind == 'search':
                        self.view['search_status'] = 'error'
                    if job.kind == 'lookup':
                        self.view['lookup_status'] = 'error'
                changed = True
        if self.import_pending is not None:
            result = self.import_pending.poll()
            if result is not None:
                self.import_pending.cancel()
                self.import_pending = None
                self.view['reason_code'] = None if result.get('success') is True else 'IMPORT_FAILED'
                changed = True
        if self.driver.cleanup_required or self.driver.partial is not None:
            self.view.update(import_ready=False, reason_code='CLEANUP_REQUIRED')
        elif self.retired_import is not None:
            self.view.update(import_ready=False, reason_code='IMPORT_CLEANUP_REQUIRED')
        return changed

    def busy(self):
        return (self.worker.busy() or self.import_pending is not None or self.retired_import is not None
                or self.active_import is not None)


_host = None


def register():
    global _host
    _main()
    if _host is None:
        try:
            from .fab_browser_connection import create_owned_connection
        except ImportError:
            return False  # No producer installed: the cached initial state stays honestly unavailable.
        from . import fab_preferences, fab_provider, ui  # noqa: F401 - preload before any draw/pump can need them.
        _host = Host(create_owned_connection, state_root=ui.state_directory())  # Existing startup preparation only.
    return True


def snapshot():
    return _host.snapshot() if _host is not None else records.initial()


def owner():
    _main()
    if _host is None or _host.closed:
        raise FabError('AUTH_REQUIRED')
    return _host


def pump():
    return _host.pump() if _host is not None else False


def busy():
    return _host.busy() if _host is not None else False


def needs_pump():
    """Keep Main's timer for a cached live/cleanup owner, without making idle service UI-busy."""
    return _host is not None and (_host.busy() or (_host.job is not None and _host.job.kind != 'agent')
                                  or _host.view['can_disconnect'] is True)


def unregister():
    global _host
    _main()
    if _host is not None:
        _host.closed = True
        _host.disconnect(normal_shutdown=True)
        if not _host.worker.join(10):
            raise RuntimeError('Fab browser cleanup remains pending; exact owner retained')
        _host.pump()
        if (_host.driver.connection is not None or _host.driver.partial is not None
                or _host.retired_import is not None or _host.active_import is not None):
            raise RuntimeError('Fab browser cleanup was not proved; exact owner retained')
        _host = None


def human(method, *args, **kwargs):
    """Main-thread action boundary: only closed reason codes reach cached public UI."""
    _main()
    try:
        return getattr(owner(), method)(*args, **kwargs)
    except Exception as error:
        if _host is not None:
            _host.view['reason_code'] = _reason(error)
            _host.dirty = True
        return None
