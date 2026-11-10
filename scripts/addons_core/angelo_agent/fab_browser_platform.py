# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private platform dispatch to existing browser/profile/file owners; never a readiness or account flag."""
import hashlib
import json
import sys

from .fab_browser_process import BrowserUnavailable


def _platform_name():
    return sys.platform


def browser_type():
    if _platform_name() == 'darwin':
        from .fab_browser_process import BrowserProcess
        return BrowserProcess
    if _platform_name() == 'win32':
        from .fab_browser_process_windows import WindowsBrowserProcess
        return WindowsBrowserProcess
    raise BrowserUnavailable('PLATFORM_UNVERIFIED')


def profile_types():
    if _platform_name() == 'darwin':
        from .fab_browser_profile import ProfileStore, ProfileLease, ProfileUnavailable
        return ProfileStore, ProfileLease, ProfileUnavailable
    if _platform_name() == 'win32':
        from .fab_browser_profile_session_windows import WindowsProfileStore, WindowsProfileLease
        from .fab_browser_profile_metadata_windows import ProfileUnavailable
        return WindowsProfileStore, WindowsProfileLease, ProfileUnavailable
    raise BrowserUnavailable('PLATFORM_UNVERIFIED')


def chrome_program():
    if _platform_name() == 'darwin':
        from .fab_browser_process import chrome_program as locate
    elif _platform_name() == 'win32':
        from .fab_browser_process_windows import chrome_program as locate
    else:
        raise BrowserUnavailable('PLATFORM_UNVERIFIED')
    return locate()


def file_types():
    if _platform_name() == 'darwin':
        from .fab_browser_download_file import Landing, CompletedFile
    elif _platform_name() == 'win32':
        from .fab_browser_download_file_windows import Landing, CompletedFile
    else:
        raise BrowserUnavailable('PLATFORM_UNVERIFIED')
    return Landing, CompletedFile


def validate_lease(lease):
    if _platform_name() == 'win32':
        _store, expected, _error = profile_types()
        if type(lease) is not expected:
            raise BrowserUnavailable('PROFILE_OWNER_REQUIRED')
        lease.verify()
    else:
        lease.validate()


def owned_lease(browser):
    if _platform_name() == 'win32' and type(browser) is not browser_type():
        raise BrowserUnavailable('PROFILE_OWNER_REQUIRED')
    lease = getattr(browser, '_profile_lease', None)
    if type(lease) is not profile_types()[1]:
        raise BrowserUnavailable('PROFILE_OWNER_REQUIRED')
    validate_lease(lease)
    if lease._browser is not browser:
        raise BrowserUnavailable('PROFILE_OWNER_REQUIRED')
    return lease


def runtime_identity(browser):
    """Private invocation fence only; this value does not prove native or account ownership."""
    if _platform_name() == 'win32':
        if type(browser) is not browser_type():
            raise BrowserUnavailable('PROFILE_OWNER_REQUIRED')
        return browser._runtime_generation
    return browser._marker


def profile_generation(lease):
    validate_lease(lease)
    if _platform_name() == 'win32':
        return lease.generation
    return hashlib.sha256(json.dumps([lease.identity[0], lease.identity[1], lease._row['token']]).encode()).hexdigest()


def account_binding(browser):
    """Fresh native Windows proof is mandatory at every account fence, not a cached token or bool."""
    lease = owned_lease(browser)
    if browser._closed or getattr(browser, '_manual_login', False):
        raise BrowserUnavailable('AUTH_REQUIRED')
    if _platform_name() == 'win32':
        observed = browser._account_binding()
        if (type(observed) is not tuple or len(observed) != 3 or observed[0] is not lease
                or observed[1] != lease.generation or type(observed[2]) is not str
                or observed[2] != browser._runtime_epoch_token):
            raise BrowserUnavailable('PHYSICAL_BINDING_UNPROVED')
        return observed
    return lease, profile_generation(lease), browser._marker


def begin_manual_auth(browser, *, stop=None):
    if stop is not None and stop.is_set():
        raise BrowserUnavailable('CANCELLED')
    owned_lease(browser)
    if stop is not None and stop.is_set():
        raise BrowserUnavailable('CANCELLED')
    ready = browser.begin_manual_auth(stop=stop)
    if stop is not None and stop.is_set():
        raise BrowserUnavailable('CANCELLED')
    if _platform_name() == 'darwin':
        return ready
    from .fab_browser_windows_proof import _Binding
    proof = getattr(browser, '_physical_proof', None)
    if (ready is not True or type(proof) is not _Binding or proof.browser is not browser
            or proof.lease is not browser._profile_lease or proof.runtime is not browser._runtime_generation
            or type(proof.window) is not int or proof.window <= 0):
        raise BrowserUnavailable('MANUAL_FOREGROUND_UNPROVED')
    return dict(native_window_id=proof.window, owned_browser_pid=proof.pid, foreground_verified=True)


def _windows_store(store):
    if type(store) is not profile_types()[0]:
        raise BrowserUnavailable('PROFILE_OWNER_REQUIRED')
    return store


def store_known_closed(store):
    return (_platform_name() == 'win32' and type(store) is profile_types()[0]
            and getattr(store, '_fab_common_closed', False) is True)


def refuse_unknown_store_close(owner, *, store=None):
    if store is None:
        store = getattr(owner, '_profile_store', None)
    if store is not None and getattr(store, '_fab_common_close_uncertain', False):
        error = BrowserUnavailable('PROFILE_STORE_CLOSE_UNPROVED')
        error.owner = owner
        raise error


def close_profile_store(owner):
    """After browser/lease/files finish only. Unknown native close is never retried through another alias."""
    store = getattr(owner, '_profile_store', None)
    if store is None:
        return
    try:
        _windows_store(store)
        if getattr(store, '_fab_common_close_uncertain', False):
            raise BrowserUnavailable('PROFILE_STORE_CLOSE_UNPROVED')
        if not getattr(store, '_fab_common_closed', False):
            store._fab_common_close_uncertain = True  # Before any native close or lost return.
            store.close()
            store._fab_common_closed = True
            store._fab_common_close_uncertain = False
        owner._profile_store = None
    except BaseException as cause:
        error = BrowserUnavailable('PROFILE_STORE_CLOSE_UNPROVED', reason=getattr(cause, 'code', None))
        error.owner = owner
        raise error from None


def prelease_failure(store, cause):
    """Retain the WHOLE exact Windows Store plus any subordinate owner; perform no guessed cleanup."""
    code = cause.code if isinstance(cause, BrowserUnavailable) else 'PROFILE_UNAVAILABLE'
    refused = BrowserUnavailable(code, reason=getattr(cause, 'code', None))
    if type(store) is profile_types()[0]:
        refused.owner = store
        refused.native_partial_owner = getattr(cause, 'owner', None)
        refused.original_error = cause
        # Keep subordinate constructor/setup/Root owners reachable when callers retain only .owner.
        refused.previous_prelease_error = getattr(store, '_fab_common_prelease_error', None)
        store._fab_common_prelease_error = refused
    else:
        refused.owner = getattr(cause, 'owner', None)
    return refused


class _FactoryCleanupOwner:
    """One partial factory graph, delegating to existing native and Store owners; no second engine."""
    def __init__(self, store, lease):
        self._profile_store = _windows_store(store)
        self.native = lease
        self.native_finished = False

    def close(self):
        try:
            if not self.native_finished:
                self.native.close()
                self.native_finished = True
            close_profile_store(self)
        except BaseException as cause:
            error = BrowserUnavailable('PROFILE_CLEANUP_UNPROVED', reason=getattr(cause, 'code', None))
            error.owner = self
            raise error from None
