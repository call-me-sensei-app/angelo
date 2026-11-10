# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private recovery observations, never a cleanup or authentication certificate.

The random process marker stays inside the already-private profile. No caller should copy this record into
logs or public receipts. Fresh identities and ownership checks are always required before recovery actions.
"""
import json
import os
from pathlib import Path
import re
import stat
import tempfile

NAME = '.angelo-fab-process-owner.json'
PHASES = frozenset(('allocated', 'spawned', 'closing', 'drain_refused', 'drained'))


def write_record(profile, expected_identity, marker, leader, leader_identity, phase, reason=None, *, helper=None):
    """Atomically replace one bounded 0600 observation in an inode-checked private profile."""
    folder = Path(profile)
    info = folder.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or (info.st_dev, info.st_ino) != expected_identity):
        raise ValueError('Private recovery profile changed')
    if not re.fullmatch('[0-9a-f]{32}', marker) or phase not in PHASES:
        raise ValueError('Private recovery observation invalid')
    if leader is not None and (type(leader) is not int or leader <= 1):
        raise ValueError('Private recovery leader invalid')
    if leader_identity is not None:
        if (type(leader_identity) is not tuple or len(leader_identity) != 3
                or leader_identity[0] != leader or any(type(v) is not str or len(v) > 2048
                                                       for v in leader_identity[1:])):
            raise ValueError('Private recovery identity invalid')
    if reason is not None and (type(reason) is not str or not re.fullmatch('[A-Z_]{1,64}', reason)):
        raise ValueError('Private recovery reason invalid')
    if helper is not None:
        keys = {'pid', 'parent', 'group', 'start', 'program', 'reason',
                'marker_readable', 'marker_present', 'profile_named'}
        if (type(helper) is not dict or set(helper) != keys
                or any(type(helper[k]) is not int or helper[k] < 1 for k in ('pid', 'parent', 'group'))
                or any(type(helper[k]) is not str or len(helper[k]) > 2048 for k in ('start', 'program'))
                or helper['reason'] not in ('HELPER_UNREADABLE', 'HELPER_OWNERSHIP_UNKNOWN')
                or any(type(helper[k]) is not bool for k in ('marker_readable', 'marker_present', 'profile_named'))):
            raise ValueError('Private helper observation invalid')
    data = json.dumps(dict(version=1, authority=False, marker=marker, leader=leader,
                           leader_identity=leader_identity, profile=str(folder),
                           profile_identity=expected_identity, phase=phase, reason=reason, helper=helper))
    if len(data.encode()) > 16384:
        raise ValueError('Private recovery observation oversized')
    descriptor, temporary = tempfile.mkstemp(prefix='.fab-owner-', suffix='.tmp', dir=folder)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        fresh = folder.lstat()
        if (fresh.st_dev, fresh.st_ino) != expected_identity or not stat.S_ISDIR(fresh.st_mode):
            raise ValueError('Private recovery profile changed')
        os.replace(temporary, folder / NAME)  # Replaces a symlink itself, never follows its target.
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
