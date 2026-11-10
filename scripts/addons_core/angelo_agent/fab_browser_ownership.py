# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conservative identity/marker-based drain, including Chrome helpers outside the original process group.

Only the random marker crosses to the owned process environment. Process inspection returns identities and
booleans, never raw command/environment text; no cookie or browser storage is opened. Unknown ownership refuses
cleanup. This is not a security boundary against another program running as the same OS user.
"""
import os
from pathlib import Path
import re
import signal
import subprocess
import time

OWNER_ENV = 'ANGELO_FAB_PROCESS_OWNER'
MAX_PROCESSES, MAX_CANDIDATES, MAX_OUTPUT = 8192, 128, 2 * 1024 * 1024


class OwnershipUnavailable(RuntimeError):
    """Fixed safe reason codes; process text and environment never enter exceptions."""
    CODES = {
        'Process inspection deadline': 'INSPECTION_DEADLINE',
        'Process inspection unavailable': 'INSPECTION_UNAVAILABLE',
        'Process inspection timeout': 'INSPECTION_TIMEOUT',
        'Process inspection denied': 'INSPECTION_DENIED',
        'Process inspection oversized': 'INSPECTION_OVERSIZED',
        'Process inspection limit': 'INSPECTION_LIMIT',
        'Process identity unavailable': 'IDENTITY_UNAVAILABLE',
        'Browser helper layout unverified': 'HELPER_LAYOUT',
        'Invalid process owner': 'OWNER_INVALID',
        'Browser process limit': 'HELPER_LIMIT',
        'Tracked browser identity changed': 'TRACKED_IDENTITY_CHANGED',
        'Browser identity changed during inspection': 'IDENTITY_CHANGED',
        'Browser helper inspection unknown': 'HELPER_UNREADABLE',
        'Browser helper ownership unknown': 'HELPER_OWNERSHIP_UNKNOWN',
        'Refusing reused browser PID': 'PID_REUSED',
        'Refusing unproved browser PID': 'PID_UNPROVED',
        'Browser processes did not drain': 'HELPERS_SURVIVED',
    }

    def __init__(self, message):
        self.code = self.CODES.get(message, 'OWNERSHIP_UNAVAILABLE') if type(message) is str else 'OWNERSHIP_UNAVAILABLE'
        super().__init__(message if type(message) is str and message in self.CODES else 'Browser ownership unavailable')


def _run(args, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OwnershipUnavailable('Process inspection deadline')
    try:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=min(1, remaining), check=False, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    except subprocess.TimeoutExpired:
        raise OwnershipUnavailable('Process inspection timeout') from None
    except OSError:
        raise OwnershipUnavailable('Process inspection denied') from None
    except subprocess.SubprocessError:
        raise OwnershipUnavailable('Process inspection unavailable') from None
    if result.returncode not in (0, 1):
        raise OwnershipUnavailable('Process inspection denied')
    if len(result.stdout) > MAX_OUTPUT:
        raise OwnershipUnavailable('Process inspection oversized')
    return result.stdout


def snapshot(deadline):
    """Same-user process identities only, with no command arguments or environment in the returned rows."""
    raw = _run(['/bin/ps', '-U', str(os.getuid()), '-o', 'pid=,ppid=,pgid=,lstart=,stat=,comm='], deadline)
    if len(raw.splitlines()) > MAX_PROCESSES:
        raise OwnershipUnavailable('Process inspection limit')
    rows = {}
    for line in raw.splitlines():
        parts = line.split(None, 9)
        if len(parts) != 10:
            raise OwnershipUnavailable('Process identity unavailable')
        try:
            pid, parent, group = map(int, parts[:3])
        except ValueError:
            raise OwnershipUnavailable('Process identity unavailable') from None
        rows[pid] = dict(pid=pid, parent=parent, group=group, start=' '.join(parts[3:8]),
                         zombie=parts[8].startswith('Z'), program=parts[9])
    return rows


def marker_and_profile(pid, marker, profile, deadline):
    """Inspect only candidate Chrome processes; discard raw text after exact marker/profile comparisons."""
    raw = _run(['/bin/ps', 'eww', '-p', str(pid), '-o', 'command='], deadline)
    if not raw.strip():
        return None, False  # The caller distinguishes a vanished PID from a still-live unreadable helper.
    owned = re.search(r'(?:^|\s)' + OWNER_ENV + '=' + re.escape(marker) + r'(?:\s|$)', raw) is not None
    profile_named = ('--user-data-dir=' + profile) in raw or profile in raw
    return owned, profile_named


def identity(row):
    return row['pid'], row['start'], row['program']


def browser_bundle(program):
    program = Path(program).resolve().as_posix()
    anchor = '.app/Contents/'
    if anchor not in program:
        raise OwnershipUnavailable('Browser helper layout unverified')
    return program.split(anchor, 1)[0] + anchor


class ProcessOwner:
    """Track exact observed PID/start/program plus unguessable owner marker, never historical PGID alone."""
    def __init__(self, leader, marker, program, profile):
        if type(leader) is not int or leader <= 1 or not re.fullmatch('[0-9a-f]{32}', marker):
            raise OwnershipUnavailable('Invalid process owner')
        # Initial source prototype is Mac-only: its helpers/crashpad live in the same Chrome application bundle.
        self.bundle = browser_bundle(program)
        self.leader, self.marker, self.profile = leader, marker, profile
        self.known = {}
        self.last_refusal = None

    def inspect(self, deadline):
        rows = snapshot(deadline)
        candidates = {pid for pid, row in rows.items() if row['program'].startswith(self.bundle)
                      or row['group'] == self.leader or pid in self.known}
        # Follow children while their parent identity is visible. Detached marker-bearing bundle helpers are
        # scanned independently, so reparenting into launchd does not hide them.
        for _ in range(MAX_CANDIDATES):
            children = {pid for pid, row in rows.items() if row['parent'] in candidates}
            if children <= candidates:
                break
            candidates |= children
        if len(candidates) > MAX_CANDIDATES:
            raise OwnershipUnavailable('Browser process limit')
        owned = []
        for pid in sorted(candidates):
            row = rows[pid]
            if row['zombie']:
                continue
            if pid in self.known and self.known[pid] != identity(row):
                raise OwnershipUnavailable('Tracked browser identity changed')
            marked, profile_named = marker_and_profile(pid, self.marker, self.profile, deadline)
            fresh = snapshot(deadline).get(pid)
            if fresh is None or fresh['zombie']:
                continue
            if identity(fresh) != identity(row):
                raise OwnershipUnavailable('Browser identity changed during inspection')
            if marked is None:
                self._refused(row, marked, profile_named, 'HELPER_UNREADABLE')
                raise OwnershipUnavailable('Browser helper inspection unknown')
            if marked:
                self.known[pid] = identity(row)
                owned.append(row)
            elif pid in self.known or row['group'] == self.leader or profile_named:
                self._refused(row, marked, profile_named, 'HELPER_OWNERSHIP_UNKNOWN')
                raise OwnershipUnavailable('Browser helper ownership unknown')
            # Other Chrome profiles are foreign. They are inspected for the marker, never signaled.
        return owned

    def _refused(self, row, marked, profile_named, reason):
        # Closed private observations only. They do not turn unknown ownership into signal authority.
        self.last_refusal = dict(pid=row['pid'], parent=row['parent'], group=row['group'],
                                 start=row['start'], program=row['program'], reason=reason,
                                 marker_readable=marked is not None, marker_present=marked is True,
                                 profile_named=profile_named is True)

    def _observe(self, deadline):
        """An unknown live helper prevents signals; it may still exit cooperatively before this same deadline."""
        while True:
            try:
                return self.inspect(deadline)
            except OwnershipUnavailable as error:
                # A candidate may exit or exec between the two snapshots in inspect().
                # Discard that observation and retry within the SAME deadline; it grants
                # no empty result, changed identity pin or signal authority. Tracked
                # identity changes remain immediate refusals and _signal stays strict.
                if error.code not in ('HELPER_UNREADABLE', 'HELPER_OWNERSHIP_UNKNOWN', 'IDENTITY_CHANGED'):
                    raise
                if time.monotonic() + 0.05 >= deadline:
                    raise
                time.sleep(0.05)

    def _signal(self, row, sig, deadline):
        fresh = snapshot(deadline).get(row['pid'])
        if fresh is None or fresh['zombie']:
            return
        if identity(fresh) != identity(row):
            raise OwnershipUnavailable('Refusing reused browser PID')
        marked, _profile = marker_and_profile(row['pid'], self.marker, self.profile, deadline)
        final = snapshot(deadline).get(row['pid'])
        if final is None or final['zombie']:
            return
        if not marked or identity(final) != identity(row):
            raise OwnershipUnavailable('Refusing unproved browser PID')
        try:
            os.kill(row['pid'], sig)
        except ProcessLookupError:
            pass

    def drain(self, *, graceful_only=False):
        """Eight seconds total, including cooperative exit and unknown-helper observations. Never signal unknowns."""
        deadline = time.monotonic() + 8
        if graceful_only:
            # Ask ONLY the pinned, marker-proved leader to perform normal Chrome shutdown. Never kill
            # helpers to manufacture a retained-profile proof, and never signal unknown identities.
            row=snapshot(deadline).get(self.leader)
            if row is not None and not row['zombie']:
                if self.known.get(self.leader)!=identity(row):
                    raise OwnershipUnavailable('Refusing unproved browser PID')
                self._signal(row,signal.SIGTERM,deadline)
            while time.monotonic()<deadline:
                if not self._observe(deadline)and not self._observe(deadline):return
                time.sleep(.05)
            raise OwnershipUnavailable('Browser processes did not drain')
        grace = min(deadline, time.monotonic() + 2)
        while time.monotonic() < grace:
            if not self._observe(deadline) and not self._observe(deadline):
                return
            time.sleep(0.05)
        for sig, seconds in ((signal.SIGTERM, 2), (signal.SIGKILL, 3)):
            phase = min(deadline, time.monotonic() + seconds)
            for row in self._observe(deadline):
                self._signal(row, sig, deadline)
            while time.monotonic() < phase:
                if not self._observe(deadline) and not self._observe(deadline):
                    return
                time.sleep(0.05)
        if self._observe(deadline) or self._observe(deadline):
            raise OwnershipUnavailable('Browser processes did not drain')
