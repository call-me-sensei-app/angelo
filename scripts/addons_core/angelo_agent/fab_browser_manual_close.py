# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual-only native quit and presence proof. No generic marker relaxation or helper signal authority."""
import hashlib
import os
import re
import signal
import stat
import time

from . import fab_browser_ownership as ownership
from .fab_browser_manual_window import _application_api

_SINGLETONS = ('SingletonLock', 'SingletonSocket', 'SingletonCookie')


def _refused(reason):
    from .fab_browser_process import BrowserUnavailable
    return BrowserUnavailable('DRAIN_UNPROVED', reason=reason)


def _bound(browser):
    from .fab_browser_process import BrowserProcess
    from .fab_browser_profile import ProfileLease
    owner = getattr(browser, '_owner', None)
    lease = getattr(browser, '_profile_lease', None)
    pinned = getattr(browser, '_leader_identity', None)
    if (type(browser) is not BrowserProcess or type(pinned) is not tuple or len(pinned) != 3 or
            not browser._manual_login or not browser._closed or
            type(owner) is not ownership.ProcessOwner or type(lease) is not ProfileLease or
            lease._browser is not browser or owner.leader != browser._pid or
            owner.profile != str(lease.path) or browser.profile != lease.path or
            browser._profile_identity != lease.identity or
            owner.known.get(owner.leader) != browser._leader_identity or
            pinned != (owner.leader, pinned[1], browser._program)):
        raise _refused('MANUAL_LEASE_UNPROVED')
    try:
        lease.validate()
    except Exception as error:
        raise _refused(getattr(error, 'code', 'MANUAL_LEASE_UNPROVED')) from None
    return owner, lease


def _leader(browser, deadline):
    """Original physical identity + exact unique typed private-profile argument, never a substring grant."""
    owner, lease = _bound(browser)
    row = ownership.snapshot(deadline).get(owner.leader)
    if row is None or row['zombie']:
        return None
    if ownership.identity(row) != browser._leader_identity or row['group'] != owner.leader:
        raise ownership.OwnershipUnavailable('Refusing reused browser PID')
    raw = ownership._run(['/bin/ps', '-p', str(owner.leader), '-o', 'command='], deadline)
    if (len(re.findall(r'(?:^|\s)--user-data-dir=', raw)) != 1 or
            not re.search(r'(?:^|\s)--user-data-dir=' + re.escape(str(lease.path)) + r'(?=\s--|$)', raw)):
        raise _refused('MANUAL_PROFILE_ARGUMENT_UNPROVED')
    fresh = ownership.snapshot(deadline).get(owner.leader)
    if fresh is None or fresh['zombie']:
        return None
    if ownership.identity(fresh) != browser._leader_identity:
        raise ownership.OwnershipUnavailable('Refusing reused browser PID')
    return fresh


def _presence(browser, deadline):
    """Remember original-group identities only to block cleanup while live, never to signal a helper."""
    owner, _lease = _bound(browser)
    while True:
        rows = ownership.snapshot(deadline)
        selected = [row for row in rows.values() if row['group'] == owner.leader and not row['zombie']]
        if len(selected) > ownership.MAX_CANDIDATES:
            raise ownership.OwnershipUnavailable('Browser process limit')
        outside = any(not row['program'].startswith(owner.bundle) for row in selected)
        changed = any(row['pid'] in owner.known and owner.known[row['pid']] != ownership.identity(row)
                      for row in selected)
        if not outside and not changed:
            # Publish only a complete valid bundle inventory; unknown rows never become ownership pins.
            owner.known.update((row['pid'], ownership.identity(row)) for row in selected)
            return
        if time.monotonic() + .05 >= deadline:
            if outside:
                raise _refused('MANUAL_LEASE_UNPROVED')
            raise ownership.OwnershipUnavailable('Tracked browser identity changed')
        time.sleep(.05)


def _singletons(lease):
    result = {}
    for name in _SINGLETONS:
        path = lease.path / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
            raise _refused('MANUAL_SINGLETON_CHANGED')
        result[name] = (info.st_dev, info.st_ino, hashlib.sha256(os.readlink(path).encode()).hexdigest())
    return result


def _passive_observe(owner, deadline):
    """Manual quit only: an identity refusal can wait, never signal or substitute an empty proof."""
    while True:
        try:
            return owner._observe(deadline)
        except ownership.OwnershipUnavailable as error:
            if error.code not in ('TRACKED_IDENTITY_CHANGED', 'IDENTITY_CHANGED'):
                raise
            if time.monotonic() + .05 >= deadline:
                raise
            time.sleep(.05)


def _leader_after_quit(browser, deadline):
    """Cancellation only: an identity drift can wait for absence, never authorize a signal."""
    while True:
        try:
            return _leader(browser, deadline)
        except ownership.OwnershipUnavailable as error:
            if error.code != 'PID_REUSED' or time.monotonic() + .05 >= deadline:
                raise
            time.sleep(.05)


def drain_manual(browser, *, retain):
    """One eight-second native quit/drain recipe. Done never uses signal/force fallback."""
    deadline = time.monotonic() + 8
    owner, lease = _bound(browser)
    if retain and (getattr(browser, '_manual_retention_revoked', False) or
                   getattr(browser, '_manual_cancel_signal_used', False)):
        raise _refused('MANUAL_CANCEL_SIGNAL_USED' if getattr(browser, '_manual_cancel_signal_used', False)
                       else 'MANUAL_RETENTION_REVOKED')
    if not retain:
        browser._manual_retention_revoked = True
    browser._manual_helper_drained = False
    browser._manual_singletons = _singletons(lease)
    _presence(browser, deadline)
    row = _leader(browser, deadline)
    if row is not None:
        try:
            with _application_api() as operations:
                requested = operations[1](owner.leader)
        except Exception:
            if retain and _leader(browser, deadline) is not None:
                raise _refused('MANUAL_NORMAL_QUIT_REFUSED') from None
            requested = False
        if retain and not requested and _leader(browser, deadline) is not None:
            raise _refused('MANUAL_NORMAL_QUIT_REFUSED')
        if not retain:
            grace = min(deadline, time.monotonic() + 2) if requested else time.monotonic()
            while time.monotonic() < grace:
                if _leader_after_quit(browser, deadline) is None:
                    break
                time.sleep(.05)
            # Cancellation-only fallback. Revalidate original identity + mutual lease + typed argument now.
            live = _leader_after_quit(browser, deadline)
            if live is not None:
                browser._manual_cancel_signal_used = True  # Set BEFORE uncertain native signal delivery.
                try:
                    os.kill(owner.leader, signal.SIGTERM)
                except ProcessLookupError:
                    pass  # Complete helper observations and waitpid proof are still mandatory.
    # Existing marker/known-identity observations stay fail-closed. No helper/group signal occurs here.
    while time.monotonic() < deadline:
        if not _passive_observe(owner, deadline) and not _passive_observe(owner, deadline):
            browser._manual_helper_drained = True
            return
        time.sleep(.05)
    raise ownership.OwnershipUnavailable('Browser processes did not drain')


def retire_singletons(browser):
    """Only after waitpid normal0 + complete helper proof; unlink exact stale owned-root links, never targets."""
    from .fab_browser_profile import ProfileLease
    lease = browser._profile_lease
    if (type(lease) is not ProfileLease or lease._browser is not browser or
            browser._pid is not None or not browser._closed or
            not browser._manual_graceful_drained or not browser._manual_helper_drained or
            type(browser._manual_singletons) is not dict):
        raise _refused('MANUAL_LEASE_UNPROVED')
    lease.validate()
    with lease.store._transaction():
        if lease.store._binding() != lease._row:
            raise _refused('MANUAL_LEASE_UNPROVED')
        current = _singletons(lease)
        if any(name not in browser._manual_singletons or browser._manual_singletons[name] != value
               for name, value in current.items()):
            raise _refused('MANUAL_SINGLETON_CHANGED')
        lock = lease.path / 'SingletonLock'
        if 'SingletonLock' in current and os.readlink(lock).rsplit('-', 1)[-1] != str(browser._owner.leader):
            raise _refused('MANUAL_SINGLETON_CHANGED')
        for name in current:
            (lease.path / name).unlink()
