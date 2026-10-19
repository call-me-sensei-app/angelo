# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Automatic enforcement of the human-configured snapshot retention policy.

The saved policy (Snapshot Settings) is the standing human choice; enforcing
it needs no per-run confirmation. Each run plans with the same conservative
planner as the manual review (checkpoint_policy.plan_retention) and removes
the planned points oldest first, leaving PRUNED tombstones so restore links
show "unavailable". Never removed:

* durably protected safety snapshots;
* ``protected_refs``: the current/active request snapshots of every chat,
  collected on Blender's main thread when the run was requested;
* snapshots captured after that collection (timeline ``boundary`` greater
  than ``watermark``), whose chats may not have bound them yet;
* snapshots owning file-journal versions a retained restore point needs.

When protection prevents meeting a limit, the summary says so and why.
Store chunks are released afterwards by snapshot_maintenance's garbage
collection. Runs on the snapshot worker thread; holds the profile transaction
only while planning and removing, never while asking git.

Most runs remove nothing: the profile is under every limit. Planning reads the
whole history (O(n) in stored snapshots), and Blender's main thread needs the
same transaction to capture the next request, so the run first plans without
the transaction (a probe, which re-reads only changed records:
``checkpoint_inventory_cache``). A probe that finds nothing to remove, the budget met
and no issues ends the run: nothing is deleted, so a read racing a concurrent
write cannot cause harm, and a missed change is seen by the next run. Anything
else repeats the exact locked flow below; the probe only replaces its first,
advisory plan (the input of the git preference), and only when the probe saw
no issues that could be a write in progress. The probe first plans with an
upper bound of the store's bytes (``snapshot_accounting.StoreBound``, two
aggregate queries) and reads every manifest only when that bound does not
already show the history within its limits; its summary's byte figures are
then that bound's.
"""

from .checkpoint_policy import _protected_refs, clean_policy, plan_retention, scene_usable
from .checkpoint_transactions import _transaction
from .snapshot_accounting import store_accounting, store_bound


def _limited(policy):
    return any(policy[key] for key in ("max_count", "max_bytes", "max_age_days"))


def _plan(checkpoints, policy, protected_refs, watermark, now, preferred_refs, probe=False):
    inventory = checkpoints._inventory(probe=probe)
    refs = set(_protected_refs(protected_refs))
    if watermark is not None:
        refs |= {item["ref"] for item in inventory["checkpoints"]
                 if item.get("boundary") and item["boundary"] > watermark}
    if probe:
        # Most probes find the history within its limits: decide that from an upper bound of the store's
        # bytes before reading every manifest (snapshot_accounting.StoreBound). Anything else plans exactly.
        bound = store_bound(checkpoints)
        if bound is not None:
            plan = plan_retention(inventory, policy, protected_refs=sorted(refs), now=now,
                                  accounting=bound, preferred_refs=preferred_refs)
            if not plan["candidates"] and plan["budget_met"] and not plan["blocked"]:
                return plan, inventory
    accounting = store_accounting(checkpoints, inventory)
    plan = plan_retention(inventory, policy, protected_refs=sorted(refs), now=now,
                          accounting=accounting, preferred_refs=preferred_refs)
    return plan, inventory


def _preferences(inventory, plan, git_signal):
    """Snapshots captured before a matching git commit of their project."""
    protected = {item["ref"] for item in plan["protected"]}
    usable = [item for item in inventory["checkpoints"] if scene_usable(item)
              and item["ref"] not in protected and item.get("original_path")]
    try:
        times = git_signal({item["original_path"] for item in usable}) or {}
    except Exception:  # The signal is advisory; any failure means "no preference".
        return ()
    return sorted(item["ref"] for item in usable
                  if item["original_path"] in times and item["created"] < times[item["original_path"]])


def enforce_retention(checkpoints, policy, *, protected_refs=(), watermark=None, git_signal=None, now=None,
                      probe=None):
    """Apply ``policy`` now and return a summary for the user notice.

    ``checkpoints`` is any CheckpointStore of the profile. ``git_signal`` maps
    project paths to commit times (snapshot_git.committed_project_times) and is
    consulted only when something must be removed. ``probe``, a dict, receives
    the lock-free probe's inventory as ``"inventory"`` when the run ended there
    (nothing removed), for the worker's orphan probe to share.
    """
    policy = clean_policy(policy)
    summary = {"removed": [], "bytes_freed": 0, "store_bytes_pending": 0, "budget_met": True,
               "blocked": False, "protected": [], "preferred": [], "policy": policy, "remaining_bytes": 0,
               "pressure_bytes": 0, "unavailable_bytes": 0, "remaining_pressure_bytes": 0,
               "remaining_unavailable_bytes": 0}
    if not _limited(policy):
        return summary
    plan, inventory = _plan(checkpoints, policy, protected_refs, watermark, now, (), probe=True)  # No transaction.
    if not plan["candidates"] and plan["budget_met"] and not plan["blocked"]:
        if probe is not None:
            probe["inventory"] = inventory
        summary.update(budget_met=True, protected=plan["protected"], remaining_bytes=plan["remaining_bytes"],
                       remaining_count=plan["remaining_count"], released_bytes=0,
                       pressure_bytes=plan["pressure_bytes"], unavailable_bytes=plan["unavailable_bytes"],
                       remaining_pressure_bytes=plan["remaining_pressure_bytes"],
                       remaining_unavailable_bytes=plan["remaining_unavailable_bytes"])
        return summary
    if plan["blocked"]:
        with _transaction(checkpoints.root):
            plan, inventory = _plan(checkpoints, policy, protected_refs, watermark, plan["planned_at"], ())
    preferred = ()
    if plan["candidates"] and not plan["blocked"] and git_signal is not None:
        preferred = _preferences(inventory, plan, git_signal)
    with _transaction(checkpoints.root):
        # Re-plan inside the removal transaction: nothing may change between
        # the plan that is reported and the removal that is applied.
        plan, _ = _plan(checkpoints, policy, protected_refs, watermark, plan["planned_at"], preferred)
        result = checkpoints._remove(plan) if plan["candidates"] and not plan["blocked"] else {}
    summary.update(removed=result.get("removed", []), bytes_freed=result.get("bytes_freed", 0),
                   store_bytes_pending=result.get("store_bytes_pending", 0), budget_met=plan["budget_met"],
                   blocked=plan["blocked"], protected=plan["protected"], preferred=plan["preferred_refs"],
                   remaining_bytes=plan["remaining_bytes"], remaining_count=plan["remaining_count"],
                   released_bytes=plan["candidate_bytes"], pressure_bytes=plan["pressure_bytes"],
                   unavailable_bytes=plan["unavailable_bytes"],
                   remaining_pressure_bytes=plan["remaining_pressure_bytes"],
                   remaining_unavailable_bytes=plan["remaining_unavailable_bytes"])
    return summary
