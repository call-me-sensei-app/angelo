# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A host stand-in for the native checkpoint content binding (``_angelo_checkpoint.capture_hasher``/``capture_bind``).

Native records what it writes to each bound component and compares it, inside native, with what the owner read back
(``bpy_angelo_checkpoint_binding.cc``). This stand-in keeps the same contract for host tests that write the components
themselves: the test says what "native" wrote (``wrote``), the owner feeds a hasher the chunks it reads, and
``capture_bind`` compares once, refusing a hasher of another capture or role, a partial read, a spent hasher or a
second call. It exposes no digest, as native exposes none. Not a model of XXH3 (any hash shows the same equality).
"""

import hashlib

CHANGED = "The retained native checkpoint output changed"
_BLAKE2B = hashlib.blake2b   # Bound now: a test may record the owner's own BLAKE2b calls by patching hashlib.


def _digest(data):
    return _BLAKE2B(data, digest_size=16).digest()


class FakeHasher:
    """Fixed to one capture capability and role; ``update`` is its only public method."""

    __slots__ = ("capability", "role", "_hash", "_bytes", "spent", "chunks")

    def __init__(self, capability, role):
        self.capability, self.role = capability, role
        self._hash, self._bytes, self.spent, self.chunks = _BLAKE2B(digest_size=16), 0, False, []

    def update(self, data):
        if self.spent:
            raise ValueError("The checkpoint content hasher was already compared")
        data = bytes(data)
        self._hash.update(data)
        self._bytes += len(data)
        self.chunks.append(len(data))


class FakeContentBinding:
    """What native wrote per role, and the one comparison ``capture_bind`` makes."""

    def __init__(self):
        self.capability, self.written, self.bind_calls, self.bound, self.hashers = None, {}, 0, False, []

    def wrote(self, capability, role, data):
        """Record native's write of ``data`` to ``role`` (only for a role native binds)."""
        self.capability = capability
        self.written[role] = (_digest(bytes(data)), len(data))

    def install(self, native):
        native.capture_hasher, native.capture_bind = self.capture_hasher, self.capture_bind
        return native

    def capture_hasher(self, capability, owner, role):
        if capability is not self.capability or role not in self.written or self.bind_calls:
            raise ValueError("The native checkpoint component has no pending content binding")
        hasher = FakeHasher(capability, role)
        self.hashers.append(hasher)
        return hasher

    def capture_bind(self, capability, owner, base, runtime=None):
        if capability is not self.capability or self.bind_calls:
            raise ValueError("The native checkpoint content binding was already attempted")
        self.bind_calls += 1
        for role, hasher in (("BASE", base), ("RUNTIME", runtime)):
            expected = self.written.get(role)
            if expected is None:
                if hasher is not None:
                    raise ValueError(CHANGED + " (a hasher for an unbound component)")
                continue
            if (type(hasher) is not FakeHasher or hasher.capability is not capability or hasher.role != role
                    or hasher.spent):
                raise ValueError(CHANGED + " (not this component's hasher)")
            hasher.spent = True
            if (hasher._hash.digest(), hasher._bytes) != expected:
                raise ValueError(CHANGED + " (the published bytes differ from those written)")
        self.bound = True

    def requires_bind(self, owner_count, bind_required):
        """Native's POST admission: a pair or a bind_required capture must be bound first."""
        if (owner_count or bind_required) and not self.bound:
            raise ValueError("The retained native checkpoint output was not bound to its published content")
