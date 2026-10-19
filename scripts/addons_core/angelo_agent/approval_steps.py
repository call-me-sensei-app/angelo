# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The step list on a scene-change approval card, grouped so a long program reads as what it does.

Presentation only: the card's Details still list every exact step and check, and nothing here is sent to
the agent or decides what is approved.
"""

from .ui_text import iface_


def grouped_steps(operations, step_label, target_name):
    """One line per run of consecutive steps of the same kind, counted ("2–33. Material principled · “A”, “B” and 30 more").

    ``step_label(kind)`` and ``target_name(operation)`` are the card's own wording (``approvals``).
    """
    lines, start = [], 0
    while start < len(operations):
        kind = operations[start]["kind"]
        end = start
        while end + 1 < len(operations) and operations[end + 1]["kind"] == kind:
            end += 1
        step = step_label(kind)
        run = operations[start:end + 1]
        targets = list(dict.fromkeys(target_name(op) for op in run))
        handles = {str(op.get("target")) if op.get("target") else ("new", index) for index, op in enumerate(run)}
        if end == start:
            target = targets[0]
        elif len(targets) == len(handles) and len(targets) <= 3:
            target = ", ".join(targets)
        else:  # Distinct objects that share a generic name ("an object") are counted, never merged into one.
            target = iface_("{names} and {count} more", names=", ".join(targets[:2]),
                            count=len(handles) - min(2, len(targets)))
        if end != start:
            lines.append(iface_("{first}–{last}. {step} · {target}", first=start + 1, last=end + 1, step=step,
                                target=target))
        else:
            lines.append(iface_("{number}. {step} · {target}", number=start + 1, step=step, target=target))
        start = end + 1
    return lines
