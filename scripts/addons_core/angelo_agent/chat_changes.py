# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which tool results count as recorded scene changes in the chat.

Pure helpers without Blender imports, shared by the chat's change summary
(chat_view) and the Changes popup (ui) so both use one definition.
"""

GROUPS = ("added", "removed", "modified")


def scene_change_report(result):
    """Return the result's mutation report, its rows as the chat reads them, or None when it has none.

    Mutating tools report {added, removed, modified} lists. Read-only results
    can reuse the key differently: angelo_scene_delta returns a list of
    observation rows, which describe a comparison, not changes the tool made.
    Any dict that names one of the groups is a report (``_is_report``): a group it lacks or cannot hold is empty, an
    entry it cannot read is still a row, so a drifting report never vanishes from the summary (REVIEW-CHAT-GUARD M3).
    """
    changes = result.get("changes") if isinstance(result, dict) else None
    if not _is_report(changes):
        return None
    return dict(changes, truncated=bool(changes.get("truncated")),
                **{group: [_row(entry, group) for entry in _rows(changes, group)] for group in GROUPS})


def _names(changes):
    """``(names, added, removed)`` of a report as ``_row`` names them, without building rows (every draw counts
    them: REVIEW-CHAT-GUARD-2 N3), kept per report while its groups keep their lengths."""
    key = id(changes)
    sizes = tuple(len(group) if isinstance(group := changes.get(name), (list, tuple, dict)) else -1 for name in GROUPS)
    kept = _counted.get(key)
    if kept is not None and kept[0] is changes and kept[1] == sizes:
        return kept[2]
    rows = {name: _rows(changes, name) for name in GROUPS}
    names = frozenset(entry["name"] if type(entry) is dict and type(entry.get("name")) is str else _name(entry)
                      for group in GROUPS for entry in rows[group])
    if len(_counted) > 2048:
        _counted.clear()
    _counted[key] = (changes, sizes, (names, len(rows["added"]), len(rows["removed"])))
    return _counted[key][2]


_counted = {}   # id(report) -> (report, its groups' lengths, its names and counts); the report is held, so its id stays its own.


def _is_report(changes):
    return isinstance(changes, dict) and any(group in changes for group in GROUPS)


def _rows(changes, group):
    """A group's entries: a list's; a dict's, by name (N6); a single name as one; none for nothing, or for a value
    that names no object (a count, a flag: REVIEW-CHAT-GUARD-3 L6)."""
    rows = changes.get(group)
    if isinstance(rows, (list, tuple)):
        return list(rows)
    if isinstance(rows, dict):
        return [dict(entry, name=entry["name"] if isinstance(entry.get("name"), str) else str(name))
                if isinstance(entry, dict) else {"name": str(name)} for name, entry in rows.items()]
    return [rows] if isinstance(rows, str) and rows else []


def _name(entry):
    record = entry if isinstance(entry, dict) else {"name": entry}
    name = record.get("name")
    return (name if isinstance(name, str) else str(name) if isinstance(name, (int, float))
            and not isinstance(name, bool) else repr(entry)[:80])


def _row(entry, group):
    """One change row as the chat and the Changes popup read it: a tool's ``{"name", "type"[, "fields"]}`` record, a
    bare object name, or what an unreadable entry is, as text. A report of names (the parallel merge's, 9060a812b84)
    raised on every draw of the chat, which then showed its console text (Director capture, 2026-10-25): no tool's
    report may, and none of its entries is silently dropped."""
    record = entry if isinstance(entry, dict) else {"name": entry}
    kind = record.get("type")
    row = dict(record, name=_name(entry), type=kind if isinstance(kind, str) else "" if kind is None else str(kind))
    if group == "modified":
        fields = record.get("fields")
        row["fields"] = [str(field) for field in fields] if isinstance(fields, (list, tuple)) else []
    return row


def file_report(result):
    """The result's ``file_changes`` as ``{"written": [str], "deleted": [str]}``, or None when it reports none."""
    files = result.get("file_changes") if isinstance(result, dict) else None
    if not isinstance(files, dict) or not files:
        return None
    return {key: [str(path) for path in files[key]] if isinstance(files.get(key), (list, tuple)) else []
            for key in ("written", "deleted")}


def recorded(message):
    """Whether ``message`` reports scene or file changes (the Changes popup's list)."""
    result = message.get("result") if isinstance(message, dict) else None
    return scene_change_report(result) is not None or file_report(result) is not None


def report_problems(changes, files=None):
    """Why a tool's ``changes`` (and ``files``, its ``file_changes``) break the one schema every tool's report follows,
    or [] when they keep it (or are no such report). The schema: ``added`` and ``removed`` hold ``{"name": str,
    "type": str}`` records, ``modified`` ``{"name": str, "fields": [str]}`` records, all three present as lists in any
    dict that names one of them; ``truncated``, when given, is a bool; ``file_changes``' ``written`` and ``deleted``,
    when given, list paths. ``controller.finish_tool`` checks every tool's result with it: a producer that drifts (the
    parallel merge reported bare names, which blanked the chat on every draw: Director capture, 2026-10-25) fails its
    suites (``STRICT``), never a person's chat."""
    problems = []
    if _is_report(changes):
        for group in GROUPS:
            if not isinstance(changes.get(group), list):
                problems.append("%s is not a list: %s" % (group, repr(changes.get(group))[:40]))
                continue
            for index, row in enumerate(changes[group]):
                if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                    problems.append("%s[%d] is not a record with a name: %s" % (group, index, repr(row)[:80]))
                elif group != "modified" and not isinstance(row.get("type"), str):
                    problems.append("%s[%d] has no type" % (group, index))
                elif group == "modified" and not (isinstance(row.get("fields"), list)
                                                  and all(isinstance(field, str) for field in row["fields"])):
                    problems.append("%s[%d] has no list of field names" % (group, index))
        if not isinstance(changes.get("truncated", False), bool):
            problems.append("truncated is not true or false")
    if files is not None:
        if not isinstance(files, dict):
            problems.append("file_changes is not a record: %s" % repr(files)[:40])
        else:
            problems += ["file_changes.%s is not a list of paths" % key for key in ("written", "deleted")
                         if key in files and not (isinstance(files[key], list)
                                                  and all(isinstance(path, str) for path in files[key]))]
    return problems[:8]


STRICT = "ANGELO_STRICT_CHANGE_REPORTS"   # Set by the suite runner (not for LIVE suites): drift is an error there.
_drift_logged = set()


def drifted(tool, problems, result):
    """``result`` of ``tool`` whose change report breaks the schema. In a suite (``STRICT``) it keeps everything it
    says (partial changes, recovery, files, its own error) and adds the drift: its ``change_report_problems``, and
    the tool's error when it had none, so the suite fails. Otherwise the result as it is (the chat reads its rows
    normalized), logged once per tool."""
    import os
    if os.environ.get(STRICT) == "1":
        flagged = dict(result, change_report_problems=problems)
        if not flagged.get("error"):
            flagged.update(error_code="CHANGE_REPORT_SCHEMA", error="%s reported scene changes outside the one "
                           "schema: %s" % (tool, "; ".join(problems)))
        return flagged
    if tool not in _drift_logged:
        _drift_logged.add(tool)
        print("Angelo: %s reported scene changes outside the schema: %s" % (tool, "; ".join(problems)))
    return result


def change_counts(messages):
    """Changed object names plus added and removed counts across unreverted messages."""
    identities, added, removed = set(), 0, 0
    for message in messages:
        result = message.get("result") if isinstance(message, dict) and not message.get("reverted") else None
        changes = result.get("changes") if isinstance(result, dict) else None
        if not _is_report(changes):
            continue
        names, plus, minus = _names(changes)
        added, removed = added + plus, removed + minus
        identities.update(names)
    return len(identities), added, removed


def file_counts(messages):
    written, deleted = set(), set()
    for message in messages:
        files = file_report(message.get("result")) if isinstance(message, dict) \
            and not message.get("reverted") else None
        if files is not None:
            written.update(files["written"])
            deleted.update(files["deleted"])
    return len(written | deleted), len(written), len(deleted)
