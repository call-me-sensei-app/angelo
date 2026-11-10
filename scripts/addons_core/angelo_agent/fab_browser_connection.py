# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Visible-site connection prototype. Public metadata is never entitlement or an import-ready Fab variant.

Observed public UI 2026-11-06: Fab search exposes listing links; a signed-out listing has a Sign in button and
Included formats tabs. Converted GLB rows expose rounded sizes without a quality/UID mapping. Do not guess that
mapping, claim signed-in state from a missing button, or adapt these rows to fab_provider.Session yet.
"""
import json
import threading
import time
from urllib.parse import quote

from .fab_browser_process import BrowserProcess, BrowserUnavailable, chrome_program
from . import fab_browser_platform as platform_owner

# Keep the existing import surface while page observation stays with the shared page decoder.
from .fab_browser_listing_page import (
    MAX_LISTINGS, MAX_SCAN, MAX_TEXT, ORIGIN, PublicListing, normalize_links,
    _STATE, _PAGE_FACTS, _search_script,
)


class _CombinedStop:
    def __init__(self, first, second):
        self.first, self.second = first, second
    def is_set(self):
        return ((self.first is not None and self.first.is_set())
                or (self.second is not None and self.second.is_set()))


class _DeadlineStop:
    def __init__(self, stop, deadline):
        self.stop, self.deadline = stop, deadline
    def is_set(self):
        return ((self.stop is not None and self.stop.is_set()) or time.monotonic() >= self.deadline)


class Connection:
    """A local worker-owned connection prototype; no Session registration or import/download authority.

    A caller opens this only after a person chooses Connect. begin_login presents the owned Fab window for normal human sign-in;
    the person completes their credentials/2FA/challenges directly in the visible browser. This owner never
    handles them. Page state is only UI state; live account/entitlement proof is a separate unfinished contract.
    """
    def __init__(self, root=None, *, browser_factory=None, stop=None, startup_stop=None, _profile_store=None):
        self._lock, self._browser, self._session = threading.RLock(), None, None
        self._query = None
        self._target = None
        self._account_probe_target = None
        self._selected_transfer = None
        self._download_cleanup = None
        self._download_landing = None
        self._download_tracker = None
        self._account_witness = None
        self._user_auth_active = False
        self._stop = stop
        self._profile_store = None
        self._startup_stop = startup_stop
        initial_stop = self._combined_stop(None)
        options = {} if initial_stop is None else {'stop': initial_stop}
        phase = 'browser_start'
        try:
            if initial_stop is not None and initial_stop.is_set():
                raise BrowserUnavailable('CANCELLED')
            self._browser = (platform_owner.browser_type() if browser_factory is None else browser_factory)(root)
            self._profile_store = _profile_store
            phase = 'download_denial'
            self._browser.call('Browser.setDownloadBehavior', {'behavior': 'deny'}, **options)
            phase = 'target_create'
            result = self._browser.call('Target.createTarget', {'url': 'about:blank'}, **options)
            target = result['targetId']
            if type(target) is not str or not 1 <= len(target) <= 128:
                raise BrowserUnavailable('PROTOCOL')
            self._target = target
            phase = 'target_attach'
            result = self._browser.call('Target.attachToTarget', {'targetId': target, 'flatten': True}, **options)
            self._session = result['sessionId']
            if type(self._session) is not str or not self._session:
                raise BrowserUnavailable('PROTOCOL')
            phase = 'initial_navigation'
            navigation = self._browser.call('Page.navigate', {'url': ORIGIN + '/'}, session=self._session, **options)
            if navigation.get('errorText'):
                raise BrowserUnavailable('NAVIGATION_FAILED')
            if initial_stop is not None and initial_stop.is_set():
                raise BrowserUnavailable('CANCELLED')
            self._startup_stop = None
            from .fab_browser_background import background
            background(getattr(self._browser, '_pid', None))  # Works behind Angelo; Chrome's launch brought it forward.
        except BaseException as error:
            failure = dict(code=error.code if isinstance(error, BrowserUnavailable) else 'CONNECTION_FAILED',
                           phase=phase, page_facts=None, facts_failure=None)
            if self._browser is not None and self._session is not None:
                try:
                    failure['page_facts'] = self.page_facts(seconds=2)
                except BaseException as facts_error:
                    failure['facts_failure'] = (facts_error.code if isinstance(facts_error, BrowserUnavailable)
                                                else 'FACTS_UNAVAILABLE')
            if self._browser is not None:
                self._browser._connection_failure = failure
            try:
                self.close()
            except BaseException as cleanup_error:
                if isinstance(cleanup_error, BrowserUnavailable):
                    cleanup_error.operation_failure = failure
                raise
            if isinstance(error, BrowserUnavailable):
                error.operation_failure = failure
                raise
            refused = BrowserUnavailable('CONNECTION_FAILED')
            refused.operation_failure = failure
            raise refused from None

    def _combined_stop(self, stop):
        owned = getattr(self, '_stop', None)
        startup = getattr(self, '_startup_stop', None)
        if startup is not None:owned = _CombinedStop(owned, startup)
        if owned is None:return stop
        if stop is None:return owned
        return _CombinedStop(owned, stop)

    def _evaluate(self, expression, stop=None, *, seconds=None):
        with self._lock:
            if getattr(self, '_user_auth_active', False):
                raise BrowserUnavailable('USER_SIGN_IN_PENDING')
            if self._browser is None:
                raise BrowserUnavailable('CLOSED')
            options = dict(session=self._session, stop=self._combined_stop(stop))
            if seconds is not None:
                options['seconds'] = seconds
            result = self._browser.call('Runtime.evaluate', dict(expression=expression, returnByValue=True,
                                                                awaitPromise=True), **options)
            if 'exceptionDetails' in result:
                raise BrowserUnavailable('SITE_CHANGED')
            remote = result.get('result')
            if type(remote) is not dict:
                raise BrowserUnavailable('METADATA_SHAPE')
            value = remote.get('value')
            if type(value) is not dict or len(json.dumps(value).encode()) > 65536:
                raise BrowserUnavailable('METADATA_SHAPE')
            return value

    @property
    def login_mode(self):
        return 'managed'

    def state(self, stop=None):
        value = self._evaluate(_STATE, stop)
        state = value.get('state')
        if state not in ('outside_fab', 'sign_in_available', 'scan_limit', 'site_visible'):
            raise BrowserUnavailable('SITE_CHANGED')
        return dict(state=state, account_verified=False, downloads_enabled=False)

    def page_facts(self, stop=None, *, seconds=None):
        """Bounded public document facts only. These never establish authentication or entitlement."""
        value = self._evaluate(_PAGE_FACTS, stop, seconds=seconds)
        booleans = {'body_present', 'sign_in_button', 'sign_in_link', 'sign_in_role',
                    'scan_limit', 'load_failure_document'}
        if (set(value) != booleans | {'origin', 'language', 'ready_state'}
                or value['origin'] not in ('fab', 'about_blank', 'other')
                or value['language'] not in ('english', 'japanese', 'other', 'empty')
                or value['ready_state'] not in ('loading', 'interactive', 'complete')
                or any(type(value[key]) is not bool for key in booleans)):
            raise BrowserUnavailable('SITE_CHANGED')
        return value

    def begin_login(self, stop=None):
        """Present the exact owned Fab window for normal human Epic login, independent of page language.

        Does not click a guessed label, inspect a credential form, or infer that an account is authenticated.
        """
        with self._lock:
            if self._browser is None or self._target is None:
                raise BrowserUnavailable('CLOSED')
            if getattr(self, '_selected_transfer', None) is not None:raise BrowserUnavailable('BUSY')
            self._assert_download_clean()
            self._account_witness=None
            self._browser.call('Target.activateTarget', {'targetId': self._target}, stop=self._combined_stop(stop))
            self._user_auth_active = True
        return dict(state='user_sign_in_pending', account_verified=False, downloads_enabled=False)

    def acknowledge_user_login(self, *, completed):
        """Internal human Done/Cancel acknowledgement, never an authenticated-account certificate."""
        if type(completed) is not bool:
            raise ValueError('completed must be boolean')
        with self._lock:
            self._user_auth_active = False
        return dict(state='user_reported_complete' if completed else 'user_cancelled',
                    account_verified=False, downloads_enabled=False)

    def start_search(self, query, stop=None):
        if type(query) is not str or not 0 < len(query) <= 200 or any(ord(c) < 32 for c in query):
            raise BrowserUnavailable('QUERY_LIMIT')
        with self._lock:
            if getattr(self, '_user_auth_active', False):
                raise BrowserUnavailable('USER_SIGN_IN_PENDING')
            if self._browser is None:
                raise BrowserUnavailable('CLOSED')
            if getattr(self, '_selected_transfer', None) is not None:raise BrowserUnavailable('BUSY')
            self._assert_download_clean()
            reply = self._browser.call('Page.navigate', {'url': ORIGIN + '/search?q=' + quote(query, safe='')},
                                       session=self._session, stop=self._combined_stop(stop))
            if reply.get('errorText'):
                raise BrowserUnavailable('NAVIGATION_FAILED')
            self._query = query
        # Navigation/loading is asynchronous. The UI pump can poll public_results; do not block for user input.
        return dict(state='search_loading', scene_changed=False)

    def public_results(self, limit=8, stop=None):
        if type(limit) is not int or not 1 <= limit <= MAX_LISTINGS:
            raise BrowserUnavailable('METADATA_LIMIT')
        if self._query is None:
            raise BrowserUnavailable('SEARCH_REQUIRED')
        value = self._evaluate(_search_script(limit, self._query), stop)
        if value.get('state') != 'public_metadata':
            raise BrowserUnavailable('SITE_CHANGED')
        rows = normalize_links(value.get('links'), limit)
        return dict(items=[row.compact() for row in rows], metadata_only=True, import_ready=False,
                    account_verified=False, scene_changed=False)

    def public_search(self, query, limit=8, stop=None):
        """One worker-owned, bounded metadata-only search through the existing browser operations."""
        if type(limit) is not int or not 1 <= limit <= MAX_LISTINGS:
            raise BrowserUnavailable('METADATA_LIMIT')
        with self._lock:
            deadline = time.monotonic() + 15
            bounded = _DeadlineStop(self._combined_stop(stop), deadline)
            self.start_search(query, stop=bounded)
            last = None
            while not bounded.is_set():
                try:last = self.public_results(limit=limit, stop=bounded)
                except BrowserUnavailable as error:
                    if error.code != 'SITE_CHANGED':raise
                if last is not None and last['items']:return last
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            if self._combined_stop(stop) is not None and self._combined_stop(stop).is_set():
                raise BrowserUnavailable('CANCELLED')
            if last is not None:return last
            raise BrowserUnavailable('SEARCH_NOT_READY')

    def _assert_download_clean(self):
        if getattr(getattr(self,'_browser',None),'_download_uncertain',False):
            error=BrowserUnavailable('DOWNLOAD_CLEANUP_UNPROVED');error.owner=self;raise error

    def service_idle(self, stop=None, *, seconds=.25):
        """Existing serialized worker only: service events without DOM/account reads or public state changes.

        No-event timeout is not a health or account proof. An actual taint/closed-pipe error retains this owner;
        the same worker must perform normal close/drain and report the cached error, never start another owner.
        """
        if type(seconds) not in (int,float) or not 0<seconds<=1:raise BrowserUnavailable('IDLE_LIMIT')
        if not self._lock.acquire(blocking=False):return None
        try:
            browser=getattr(self,'_browser',None)
            combined=self._combined_stop(stop)
            if (browser is None or getattr(self,'_user_auth_active',False)
                    or getattr(self,'_selected_transfer',None)is not None
                    or combined is not None and combined.is_set()):return None
            self._assert_download_clean()
            try:browser.call('Browser.getVersion',stop=combined,seconds=seconds)
            except BrowserUnavailable as cause:
                if cause.code=='TIMEOUT'and not getattr(browser,'_download_uncertain',False):return None
                if cause.code=='CANCELLED'and combined is not None and combined.is_set():return None
                error=BrowserUnavailable('DOWNLOAD_CLEANUP_UNPROVED');error.owner=self;raise error from None
            self._assert_download_clean()
            return None
        finally:self._lock.release()

    def current_account(self, stop=None):
        """Read the actual current account in an owned separate normal-UI target, not the download frame."""
        if getattr(self, '_browser', None) is None:raise BrowserUnavailable('CLOSED')
        if getattr(self, '_user_auth_active', False):raise BrowserUnavailable('USER_SIGN_IN_PENDING')
        self._assert_download_clean()
        try:from .fab_browser_identity import read_current_account
        except ImportError:raise BrowserUnavailable('ACCOUNT_PROOF_PENDING') from None
        return read_current_account(self, stop)

    def listing(self, item_id, stop=None):
        """Observe the normal one-item Download modal; no agreement/acquisition/download is performed."""
        if getattr(self, '_browser', None) is None:raise BrowserUnavailable('CLOSED')
        self._assert_download_clean()
        from .fab_browser_listing_owner import listing
        return listing(self, item_id, stop)

    def open_selected(self, approved_item, choice, expected_account, stop=None, maximum_bytes=None):
        """Actual selected normal-UI transfer; no native import/adoption authority."""
        if getattr(self, '_browser', None) is None:raise BrowserUnavailable('CLOSED')
        self._assert_download_clean()
        from .fab_browser_selected import open_selected
        return open_selected(self, approved_item, choice, expected_account, stop, maximum_bytes)

    def close_normal_shutdown(self):
        """Ordinary product shutdown retains only an actual owner-bound account witness; otherwise purge.

        This policy is separate from the explicitly consented bounded human Done handoff. Cached UI flags and
        Check sign-in acknowledgment never create a witness or authorize retention. Cleanup ignores job stop.
        """
        with self._lock:
            retain=False
            try:
                from .fab_browser_identity import _AccountWitness
                witness=getattr(self,'_account_witness',None)
                retain=type(witness)is _AccountWitness and witness.valid_for_retention(self)
            except Exception:retain=False
            self.close(retain_profile=retain)

    def close(self, *, retain_profile=False):
        with self._lock:
            platform_owner.refuse_unknown_store_close(self)
            self._account_witness = None
            try:
                if self._browser is not None:
                    if retain_profile:
                        self._browser.close(retain_profile=True)
                    else:
                        self._browser.close()
                pending = getattr(self, '_download_cleanup', None)
                if pending is not None:
                    landing, file = pending
                    try:
                        if file is not None:
                            file.close()
                        if landing is not None:
                            landing.remove()
                    except BaseException:
                        error = BrowserUnavailable('DOWNLOAD_CLEANUP_UNPROVED')
                        error.owner = self
                        raise error from None
                platform_owner.close_profile_store(self)
            except BaseException as cause:
                if getattr(self, '_profile_store', None) is not None:
                    error = BrowserUnavailable('PROFILE_CLEANUP_UNPROVED', reason=getattr(cause, 'code', None))
                    error.owner = self
                    raise error from None
                raise
            # Keep every owner reference until browser, files and Store all have known cleanup outcomes.
            self._browser = None
            self._session = None
            self._target = None
            self._account_probe_target = None
            self._user_auth_active = False
            self._download_cleanup = None
            self._download_landing = None
            self._download_tracker = None
            self._selected_transfer = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def create_owned_connection(*, stop=None, resume=True, store=None, login_mode='managed'):
    """Worker-only real factory. Cached UI state/Session ownership stays with existing host/provider owners.

    Failure carries the exact partial cleanup owner. No browser/profile I/O belongs in draw or the main thread.
    """
    if type(login_mode) is not str or login_mode not in ('managed', 'manual'):
        raise ValueError('login_mode must be managed or manual')
    if type(resume) is not bool:
        raise ValueError('resume must be boolean')
    windows = platform_owner._platform_name() == 'win32'
    if stop is not None and stop.is_set():
        error = BrowserUnavailable('CANCELLED')
        if windows:
            raise platform_owner.prelease_failure(store, error) from None
        raise error
    Process = platform_owner.browser_type()
    ProfileStore, _Lease, ProfileUnavailable = platform_owner.profile_types()
    fresh = False
    try:
        program = chrome_program() if Process is BrowserProcess else platform_owner.chrome_program()
        if store is None:
            if windows:
                store = ProfileStore.__new__(ProfileStore)  # Publish before any native constructor acquisition.
                ProfileStore.__init__(store)
            else:
                store = ProfileStore()
        if windows:
            platform_owner._windows_store(store)
        if resume:
            try:
                lease = store.resume()
            except ProfileUnavailable as error:
                if error.code != 'PROFILE_MISSING':
                    raise
                lease = store.create()
                fresh = True
        else:
            lease = store.create()
            fresh = True
    except BaseException as error:
        if windows:
            raise platform_owner.prelease_failure(store, error) from None
        if isinstance(error, ProfileUnavailable):
            refused = BrowserUnavailable('PROFILE_UNAVAILABLE', reason=error.code)
            refused.owner = error.owner
            raise refused from None
        raise

    return _open_profile_connection(lease, program, store, stop, manual=login_mode == 'manual' and fresh)


def _open_profile_connection(lease, program, store, stop, *, manual=False, startup_stop=None):
    """One shared native constructor/partial-owner cleanup for an already acquired exact lease."""
    tracked = []
    Process = platform_owner.browser_type()
    cleanup_owner = (platform_owner._FactoryCleanupOwner(store, lease)
                     if platform_owner._platform_name() == 'win32' else None)
    def factory(root):
        browser = Process.__new__(Process)
        tracked.append(browser)
        if cleanup_owner is not None:
            cleanup_owner.native = browser
        if manual:
            Process.__init__(browser, root, program=program, profile_lease=lease, manual_login=True)
        else:
            Process.__init__(browser, root, program=program, profile_lease=lease)
        return browser
    try:
        if manual:
            from .fab_browser_manual_connection import ManualConnection
            return ManualConnection(factory(None), store, stop)
        options = {} if cleanup_owner is None else {'_profile_store': store}
        if startup_stop is None:
            return Connection(browser_factory=factory, stop=stop, **options)
        return Connection(browser_factory=factory, stop=stop, startup_stop=startup_stop, **options)
    except BaseException as error:
        if cleanup_owner is not None:
            partial = getattr(error, 'owner', None)
            if isinstance(partial, Connection) and getattr(partial, '_profile_store', None) is store:
                raise  # Constructor already retained the full graph after its independent cleanup refusal.
            cleanup_owner.close()
            if isinstance(error, BrowserUnavailable):
                raise
            raise BrowserUnavailable('CONNECTION_FAILED') from None
        owner = tracked[-1] if tracked else lease
        try:owner.close()
        except BaseException as cleanup_error:
            if isinstance(cleanup_error, BrowserUnavailable):
                cleanup_error.owner = owner
                raise
            refused = BrowserUnavailable('PROFILE_CLEANUP_UNPROVED', reason=getattr(cleanup_error, 'code', 'PROFILE_OWNER_FAILURE'))
            refused.owner = owner
            raise refused from None
        if isinstance(error, BrowserUnavailable):raise
        raise BrowserUnavailable('CONNECTION_FAILED') from None


def _resume_managed_expected(store, expected, *, stop=None, startup_stop=None):
    """Private transition path: exact retained lease acquisition precedes ANY native constructor."""
    windows = platform_owner._platform_name() == 'win32'
    if ((stop is not None and stop.is_set()) or
            (startup_stop is not None and startup_stop.is_set())):
        error = BrowserUnavailable('CANCELLED')
        if windows:
            raise platform_owner.prelease_failure(store, error) from None
        raise error
    _Store, _Lease, ProfileUnavailable = platform_owner.profile_types()
    Process = platform_owner.browser_type()
    try:
        program = chrome_program() if Process is BrowserProcess else platform_owner.chrome_program()
        lease = store.resume_expected(expected)
    except BaseException as error:
        if windows:
            raise platform_owner.prelease_failure(store, error) from None
        if isinstance(error, ProfileUnavailable):
            refused = BrowserUnavailable('PROFILE_UNAVAILABLE', reason=error.code)
            refused.owner = error.owner
            raise refused from None
        raise
    return _open_profile_connection(lease, program, store, stop, startup_stop=startup_stop)

