# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's approval mode, said to the model in a request whenever it changed for that model context.

The mode is enforced where each tool is dispatched (``approval_modes``, held in C); this only tells the model, so a
person's switch (Read only to Ask, say) reaches it at the next request instead of the model going by its earlier
refusals (2.0.1 acceptance rerun: the first reply after Ask still acted as Read only). A ``TurnBlocks`` delta block,
as the other context blocks: a digest of the mode in the context's ledger, the line sent only when it differs. A
fresh context in the default mode (Approve for me) gets no line. Model-facing English, never translated. No bpy.
"""

from . import context_ledger

DEFAULT = "AUTO_BOUNDED"
NOTES = {
    "AUTO_BOUNDED": ("Approval mode now: Approve for me. Bounded typed edits run without a card; anything else asks "
                     "the person on a card. An earlier mode's refusals no longer apply."),
    "ASK": ("Approval mode now: Ask before changes. Make the changes the request needs as usual; each one asks the "
            "person on a card. An earlier Read only no longer applies."),
    "READ_ONLY": ("Approval mode now: Read only. Nothing in the scene or files can change in this chat: observe, "
                  "explain and plan only."),
}
HEADER = "\n\n[Angelo] "


def block(blocks, mode):
    """This request's approval line for ``blocks`` (a ``context_ledger.TurnBlocks``), or ""."""
    mode = mode if mode in NOTES else "ASK"
    stamp = context_ledger.digest(mode)
    blocks.after["approval"] = stamp
    said = blocks.received.get("approval") if not blocks.full else None
    if said == stamp or (said is None and blocks.full and mode == DEFAULT):
        return blocks._record("approval", "")
    return blocks._record("approval", HEADER + NOTES[mode])
