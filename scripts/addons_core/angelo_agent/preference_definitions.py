# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The definitions Angelo registers its preferences with, kept from import (the re-read of 9b3f450, S13).

A deferred property's ``keywords`` and a class's ``__annotations__`` are plain containers a confined script can change
in place (the confinement journals rebindings, not changes inside a container: script_confinement.rst). No RNA
changes then, so nothing is refused; but a later ``register_class`` of the same class (Angelo disabled and enabled
again, a settings import that reloads it) would read the changed definition: a guarded setting without its update
(Strict script protection, the widening settings) would stop reaching what guards it.

``keep(cls)`` records, at import (before any agent code), every property definition ``cls`` and its bases declare:
its function and a copy of its keywords. The returned ``fresh()`` writes a new definition from that record into the
class that declares it; registration calls it just before ``register_class``. Needs no ``bpy``.
"""

import copy


def _copied(keywords):
    try:
        return copy.deepcopy(keywords)
    except Exception:   # noqa: BLE001 - a value deepcopy can't copy: a shallow copy still owns the dict.
        return dict(keywords)


def keep(cls):
    """``fresh()`` for every deferred property ``cls`` and its bases declare, as they are now."""
    kept = []
    for klass in cls.__mro__:
        annotations = klass.__dict__.get("__annotations__") or {}
        for name, deferred in annotations.items():
            function, keywords = getattr(deferred, "function", None), getattr(deferred, "keywords", None)
            if callable(function) and isinstance(keywords, dict):
                kept.append((klass, name, function, _copied(keywords)))

    def fresh():
        """Write each kept definition anew into its class; the number written."""
        for klass, name, function, keywords in kept:
            annotations = klass.__dict__.get("__annotations__")
            if annotations is None:   # Taken away: the class gets its own dict back.
                annotations = {}
                type.__setattr__(klass, "__annotations__", annotations)
            annotations[name] = function(**_copied(keywords))
        return len(kept)

    return fresh
