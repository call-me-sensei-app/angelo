# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker-only manual-to-managed ownership transition; user Done never authenticates an account."""
import threading

from .fab_browser_process import BrowserUnavailable
from . import fab_browser_platform as platform_owner


class ManualConnection:
    """One ordinary browser owner. No page, account, listing, or download observer exists in this mode."""

    def __init__(self, browser, store, stop=None):
        if type(browser) is not platform_owner.browser_type() or not browser._manual_login:
            raise BrowserUnavailable('MANUAL_OWNER_UNPROVED')
        self._browser = browser
        self._store = store
        self._profile_store = store if platform_owner._platform_name() == 'win32' else None
        self._stop = stop
        self._lock = threading.RLock()
        self._acknowledged = False
        self._transition_started = False
        self._replacement = None
        self._closed = False

    @property
    def login_mode(self):
        return 'manual'

    def _refused(self, code):
        error = BrowserUnavailable(code)
        error.owner = self
        return error

    def _check(self, stop=None):
        if self._closed or self._transition_started:
            raise self._refused('CLOSED')
        if ((self._stop is not None and self._stop.is_set()) or
                (stop is not None and stop.is_set())):
            raise self._refused('CANCELLED')

    def begin_login(self, stop=None):
        with self._lock:
            self._check(stop)
            from .fab_browser_connection import _CombinedStop
            ready = platform_owner.begin_manual_auth(self._browser, stop=_CombinedStop(self._stop, stop))
            self._check(stop)
            return dict(state='user_sign_in_pending', account_verified=False,
                        downloads_enabled=False, owned_window=ready)

    def acknowledge_user_login(self, *, completed):
        if type(completed) is not bool:
            raise ValueError('completed must be boolean')
        with self._lock:
            self._check()
            if not self._browser._manual_auth_active:
                raise BrowserUnavailable('MANUAL_HANDOFF_NOT_STARTED')
            self._acknowledged = completed
            return dict(state='user_reported_complete' if completed else 'user_cancelled',
                        account_verified=False, downloads_enabled=False)

    def finish_manual_login(self, *, stop=None):
        """Drain and reopen the same profile with a NEW native runtime; caller must then observe actual account."""
        with self._lock:
            self._check(stop)
            if not self._acknowledged or not self._browser._manual_auth_active:
                raise self._refused('MANUAL_COMPLETION_REQUIRED')
            self._transition_started = True
            old = self._browser
            lease = old._profile_lease
            expected_identity = lease.identity
            expected_token = lease._row['token']
            old_runtime = platform_owner.runtime_identity(old)
            try:
                # Cleanup ignores caller cancellation. No new runtime until positive graceful drain/retain.
                old.close(retain_profile=True)
                if ((self._stop is not None and self._stop.is_set()) or
                        (stop is not None and stop.is_set())):
                    raise self._refused('CANCELLED')
                from .fab_browser_connection import _resume_managed_expected
                expected_retained = dict(lease._row)
                # Publish immediately before inspecting the returned owner's fields.
                self._replacement = _resume_managed_expected(self._store, expected_retained,
                                                            stop=self._stop, startup_stop=stop)
                new = self._replacement
                if platform_owner._platform_name() == 'win32':
                    if getattr(new, '_profile_store', None) is not self._profile_store:
                        raise BrowserUnavailable('MANUAL_TRANSITION_UNPROVED')
                    self._profile_store = None  # The already published replacement owns this SAME Store.
                native = new._browser
                current = native._profile_lease
                platform_owner.validate_lease(current)
                if (new.login_mode != 'managed' or native is old or native._manual_login or
                        native._closed or (platform_owner._platform_name() == 'darwin' and native._pid is None)
                        or current.identity != expected_identity or
                        current._row['token'] != expected_token or platform_owner.runtime_identity(native) == old_runtime):
                    raise BrowserUnavailable('MANUAL_TRANSITION_UNPROVED')
                if platform_owner._platform_name() == 'win32':
                    platform_owner.account_binding(native)  # Actual retained-Job/profile proof, not getVersion.
                if ((self._stop is not None and self._stop.is_set()) or
                        (stop is not None and stop.is_set())):
                    raise self._refused('CANCELLED')
                self._closed = True
                self._replacement = None
                return new
            except BaseException as error:
                partial = getattr(error, 'owner', None)
                if partial is not None and partial is not self:
                    self._replacement = partial
                refused = BrowserUnavailable(getattr(error, 'code', 'MANUAL_TRANSITION_FAILED'),
                                             reason=getattr(error, 'reason', None))
                refused.owner = self
                raise refused from None

    def current_account(self, stop=None):
        raise BrowserUnavailable('AUTH_REQUIRED')

    def public_search(self, *args, **kwargs):
        raise BrowserUnavailable('AUTH_REQUIRED')

    def listing(self, *args, **kwargs):
        raise BrowserUnavailable('AUTH_REQUIRED')

    def open_selected(self, *args, **kwargs):
        raise BrowserUnavailable('AUTH_REQUIRED')

    def service_idle(self, stop=None, *, seconds=.25):
        # Human auth has no RPC/event channel. The serialized host owns cancellation/close.
        return None

    def close_normal_shutdown(self):
        # No account witness can exist on a manual runtime. Ordinary shutdown always purges.
        return self.close()

    def close(self, *, retain_profile=False):
        """Unconditional cleanup even with a cancelled lifetime; failures retain this exact composite owner."""
        if retain_profile is not False:
            raise BrowserUnavailable('PROFILE_RETENTION_UNAVAILABLE')
        with self._lock:
            platform_owner.refuse_unknown_store_close(self, store=self._store)
            self._closed = True
            try:
                if self._replacement is not None:
                    self._replacement.close()
                    self._replacement = None
                self._browser.close()
                # A cancelled transition may have already retained/released the old lease before spawning.
                # Reclaim the exact retained profile through its existing store owner, then purge it.
                binding = None if platform_owner.store_known_closed(self._store) else self._store._binding()
                if binding is not None:
                    old_lease = self._browser._profile_lease
                    if binding != old_lease._row or binding['state'] != 'retained':
                        raise BrowserUnavailable('MANUAL_TRANSITION_CLEANUP_UNPROVED')
                    cleanup_lease = self._store.resume_expected(dict(old_lease._row))
                    self._replacement = cleanup_lease
                    cleanup_lease.close()
                    self._replacement = None
                platform_owner.close_profile_store(self)
            except BaseException as error:
                refused = BrowserUnavailable('MANUAL_TRANSITION_CLEANUP_UNPROVED',
                                             reason=getattr(error, 'reason', None))
                refused.owner = self
                raise refused from None
