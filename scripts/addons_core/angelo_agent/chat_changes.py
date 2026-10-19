# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which tool results count as recorded scene changes in the chat.

Pure helpers without Blender imports, shared by the chat's change summary
(chat_view) and the Changes popup (ui) so both use one definition.
"""

GROUPS = ("added", "removed", "modified")


def scene_change_report(result):
    """Return the result's mutation report, or None when it has none.

    Mutating tools report {added, removed, modified} lists. Read-only results
    can reuse the key differently: angelo_scene_delta returns a list of
    observation rows, which describe a comparison, not changes the tool made.
    """
    changes = result.get("changes") if isinstance(result, dict) else None
    if isinstance(changes, dict) and all(isinstance(changes.get(group), list) for group in GROUPS):
        return changes
    return None


def change_counts(messages):
    """Changed object names plus added and removed counts across unreverted messages."""
    identities, added, removed = set(), 0, 0
    for message in messages:
        report = None if message.get("reverted") else scene_change_report(message.get("result"))
        if report is None:
            continue
        added += len(report["added"])
        removed += len(report["removed"])
        identities.update(record["name"] for group in GROUPS for record in report[group])
    return len(identities), added, removed


def file_counts(messages):
    written, deleted = set(), set()
    for message in messages:
        if message.get("reverted"):
            continue
        changes = message.get("result", {}).get("file_changes", {})
        written.update(changes.get("written", []))
        deleted.update(changes.get("deleted", []))
    return len(written | deleted), len(written), len(deleted)
