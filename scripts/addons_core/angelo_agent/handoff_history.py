# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Lazy transcript order and host-summary adjacency; no result can certify itself.

Only a closed group can justify removing its summary. An unfinished group at
the excerpt boundary or a work refusal never says all its activities merged.
The caller owns a stable transcript snapshot on the existing request path.
"""

from dataclasses import dataclass

from .handoff_json import UNSUPPORTED, WORK_LIMIT, admit_mapping
from .legacy_names import current_tool_name


class History:
    """Newest-first raw records, including filtered records, with no suffix copy."""

    def __init__(self, messages, start, budget):
        if type(messages) is not list or type(start) is not int:
            raise ValueError("A handoff requires a transcript list and integer start")
        self.messages, self.start, self.budget = messages, min(len(messages), max(0, start)), budget
        self.cursor, self.reason = len(messages) - 1, None

    @property
    def unexamined(self):
        return max(0, self.cursor - self.start + 1)

    def take(self):
        if not self.unexamined or self.reason is not None:
            return None
        if not self.budget.visit():
            self.reason = WORK_LIMIT
            return None
        index = self.cursor
        self.cursor -= 1
        row = self.messages[index]
        if type(row) is not dict:
            self.reason = UNSUPPORTED
            return index, None
        return index, row


@dataclass(frozen=True)
class Group:
    summary: int
    rows: tuple


@dataclass(frozen=True)
class Certification:
    certified: bool = False
    accepted: bool = False
    closed: Group | None = None
    reason: str | None = None


class HostSummaries:
    """Read the original adjacency rule backward; never inspect a saved result."""

    def __init__(self):
        self.summary, self.rows = None, []
        self.refused = False

    def observe(self, index, message, budget):
        if self.refused:
            return Certification(reason=UNSUPPORTED)
        if not budget.visit():
            self.refused = True
            return Certification(reason=WORK_LIMIT)
        if (reason := admit_mapping(message, budget)) is not None:
            self.refused = True
            return Certification(reason=reason)
        if type(message.get("role")) is not str:
            self.refused = True
            return Certification(reason=UNSUPPORTED)
        role = message["role"]
        if role == "activity":
            if self.summary is None:
                return Certification()
            tool = message.get("tool")
            if type(tool) is not str:
                self.refused = True
                return Certification(reason=UNSUPPORTED)
            if tool and not budget.scan(len(tool)):
                self.refused = True
                return Certification(reason=WORK_LIMIT)
            self.rows.append(index)
            return Certification(certified=True, accepted=current_tool_name(tool) == "angelo_accept_preview")
        if role == "thinking" or message.get("notice") is True:
            return Certification()
        closed = self._close()
        if role == "system" and message.get("host_summary") is True:
            self.summary = index
        return Certification(closed=closed)

    def _close(self):
        group = Group(self.summary, tuple(reversed(self.rows))) if self.summary is not None else None
        self.summary, self.rows = None, []
        return group

    def end(self, *, reached_origin):
        """Only transcript origin proves a final group complete; ``start`` alone does not."""
        return self._close() if reached_origin and not self.refused else None
