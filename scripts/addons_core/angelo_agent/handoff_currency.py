# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which post-commit rows a handoff may still show, measured against the same message's observation.

A handoff replays earlier results. Each commit carries the revision (and, in a full result, the state) its objects
had right after it: ``live_revisions`` rows and a script's ``after`` read-back. The object can change again before
this message (a later edit, a person, a collaborator), and a row showing the older revision under a key that reads
as current is then wrong data beside a correct observation. Recorded (Opus, a 1.0.6 gate): the observation showed
the object's new location and revision, the previous commit's ``live`` row its older revision, and the model
reported the stale location because the revisions disagreed.

A row's revision is measured against the newest one this message knows for its object:

* the revision this message's observation shows (``IdentifierTable.observed(handle, shown=True)``). Every view a
  context's table holds from before this message predates the handoff's commits (a context's handoff starts
  after its own last turn), so only this message's observation can be newer than a commit;
* else the revision a later commit in the same handoff left (``context_ledger.own_commits``, latest wins).

A row at an older revision is superseded: it is left out, and its handle is listed in ``changed_since`` (the
observation or a later result shows the object's current state). A receipt also leaves out a row the observation
shows at that same revision (the observation repeats it) or the context's earlier view already holds; a full
result keeps those. A row without a revision (``revision_omitted``) is always kept: its handle is how a next plan
names a created object. Binding is unchanged: an omitted binding takes the observed revision
(``IdentifierTable.bound_revision``), and an explicit stale one fails closed at staging.
"""

LIVE_FIELDS = ("handle", "name", "object_revision", "revision_omitted")
CHANGED = "changed_since"


class Currency:
    """The newest revision a message knows per handle: its observation's, else a later commit's in its handoff."""

    def __init__(self, table=None, messages=()):
        from .context_ledger import own_commits
        self.table = table
        self.committed = own_commits(messages)

    def current(self, handle):
        shown = self.table.observed(handle, shown=True) if self.table is not None else None
        return shown if shown is not None else self.committed.get(handle)

    def earlier(self, handle):
        """The context's latest view of ``handle`` (this message's observation, else a persisted one), or None."""
        return self.table.observed(handle) if self.table is not None else None

    def superseded(self, row):
        """A dict row whose revision is no longer its object's current one."""
        revision = row.get("object_revision")
        current = self.current(row.get("handle")) if isinstance(revision, str) else None
        return current is not None and current != revision


def receipt_rows(rows, currency=None):
    """``(rows, superseded handles)``: a receipt's post-commit rows a next plan binds with (module docstring)."""
    kept, changed = [], []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if currency is not None and currency.superseded(row):
            changed.append(row.get("handle"))
        elif ("object_revision" not in row or currency is None
              or row["object_revision"] != currency.earlier(row.get("handle"))):  # Else its view repeats it.
            kept.append({key: row[key] for key in LIVE_FIELDS if key in row})
    return kept, changed


def full_result(result, currency=None):
    """A copy of a full result without its superseded post-commit rows, which ``changed_since`` names instead."""
    if currency is None or not isinstance(result, dict):
        return result
    output, changed = dict(result), []
    for key in ("live_revisions", "after"):
        section = result.get(key)
        objects = section.get("objects") if isinstance(section, dict) else None
        if not isinstance(objects, list):
            continue
        stale = [row for row in objects if isinstance(row, dict) and currency.superseded(row)]
        if stale:
            output[key] = dict(section, objects=[row for row in objects if not any(row is item for item in stale)])
            changed.extend(row.get("handle") for row in stale if row.get("handle") not in changed)
    return {CHANGED: changed, **output} if changed else output   # First: an excerpt cut never loses it.
