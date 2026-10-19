# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Observation coverage: whole-scene baselines versus object-scoped ones.

A SCENE observation fingerprints every object in the bound scene, so a delta
against it can report added, removed and unchanged objects scene-wide. An
OBJECTS observation fingerprints only named handles (an object detail, a
relationship query or one discovery page). A delta against it compares exactly
those objects, can prove only their removal or change, and never claims anything
about the rest of the scene. Its live scene object count is informational.

Discovery filters every object with cheap live reads and leaves fingerprints to
the returned page. Its filters are the same predicate the SCENE search applies.
"""

import time
from itertools import islice

from .scene_query_results import object_matches
from .scene_read_values import hash_value

SCENE, OBJECTS = "SCENE", "OBJECTS"
OBJECTS_SCOPE = ("COVERED_OBJECTS_ONLY: compares only the baseline's objects; objects outside it, "
                 "including new ones, are not compared. Use a SCENE baseline for scene-wide changes.")
_DEADLINE_STRIDE = 256


def discover(window, args, scan_limit, deadline, selection, visibility, collections_of):
    """Return (matching objects sorted by session UID, scanned count, complete)."""
    matches, scanned = [], 0
    for obj in islice(window.scene.objects, scan_limit):
        if scanned % _DEADLINE_STRIDE == 0 and time.monotonic() > deadline:
            break
        scanned += 1
        if object_matches(obj, args, selection(window, obj), visibility(window, obj), collections_of):
            matches.append(obj)
    matches.sort(key=lambda obj: obj.session_uid)
    return matches, scanned, scanned == len(window.scene.objects)


def match_fingerprint(handles):
    """Discovery pages must reject a cursor once the ordered match list changes."""
    return hash_value(["discovery", list(handles)])


def captured_row(state):
    return {"summary": state["summary"], "fields": state["fields"], "exact": state["exact"]}


def objects_snapshot(scope, rows, scene_object_count, frame):
    """An admitted OBJECTS observation over exactly the given captured rows.

    ``membership_complete`` means every covered handle was checked against the
    live bound scene, so a missing one is provably removed, not merely unseen.
    """
    return {"scope": list(scope), "coverage": OBJECTS, "objects": rows, "object_count": scene_object_count,
            "membership_complete": True, "exact": all(row["exact"] for row in rows.values()),
            "uncertain_count": sum(not row["exact"] for row in rows.values()),
            "fingerprint": hash_value(rows), "frame": frame}


def covered_objects(window, handles, object_handle):
    """The covered objects still present in the bound scene, looked up by UID.

    If a UID now names a replaced ID, object_handle's identity check rotates the
    document epoch and execute() fails the whole query, so replacement is never
    reported as a removal.
    """
    present = {obj.session_uid: obj for obj in window.scene.objects}
    found = []
    for handle in handles:
        obj = present.get(int(handle.rsplit(":", 1)[1]))
        if obj is not None and object_handle(obj) == handle:
            found.append(obj)
    return found
