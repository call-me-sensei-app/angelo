# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded private evidence gathered during reads the host already performs.

The main-thread observation owners publish existing captures into an explicit
``collect`` scope. No scene traversal or bpy reference is retained here. The
caller passes the returned plain records to its request's RecoveryBarrier;
records never join normal model observations or persisted identifier tables.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import copy
import hashlib
import json

MAX_RECORDS = 64
MAX_BYTES = 262144
MAX_RECORD_BYTES = 16384
_sink = ContextVar("angelo_recovery_observation", default=None)


def _encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


@contextmanager
def collect():
    """Gather bounded copies of already-computed exact captures, on this thread."""
    records = {}
    token = _sink.set(records)
    try:
        yield records
    finally:
        _sink.reset(token)


def _record(handle, revision, fields, values):
    records = _sink.get()
    if records is None or not isinstance(handle, str) or not isinstance(revision, str):
        return
    if handle not in records and len(records) >= MAX_RECORDS:
        return
    value = {"handle": handle, "revision": revision, "fields": fields, "values": values}
    try:
        size = len(_encoded(value))
    except (ValueError, TypeError, OverflowError):
        return
    if size > MAX_RECORD_BYTES or sum(row["bytes"] for key, row in records.items() if key != handle) + size > MAX_BYTES:
        return
    records[handle] = {**copy.deepcopy(value), "bytes": size}


def object_capture(state):
    """Only fields with complete already-captured values can explain a revision.

    Geometry, drivers, materials, dependencies and other opaque field hashes
    remain useful for detecting an unexplained difference, never for authorizing
    its correction. Extend this closed value mapping with its native owner.
    """
    if _sink.get() is None or not state.get("exact"):
        return
    summary, captured = state["summary"], state["captured"]
    values = {key: captured[key] for key in ("transform", "camera_light") if key in captured}
    _record(summary["object"], summary["object_revision"], state["fields"], values)


def scene_capture(scene, revision, values, world, layers, markers, *, proven=None, generic=None):
    if _sink.get() is None:
        return
    from .scene_settings import scene_handle
    handle = scene_handle(scene)
    fields = {"setting:" + key: _digest(value) for key, value in values.items()}
    fields.update(world=_digest(world), layers=_digest(layers), markers=_digest(markers))
    # These settings join the exact revision but are not reported as recovery
    # values. Keep their opaque change visible even beside a known change.
    if proven is not None:
        fields["proven_settings"] = _digest(proven)
    if generic is not None:
        fields["generic_settings"] = _digest(generic)
    _record(handle, revision, fields, {"setting:" + key: value for key, value in values.items()})
