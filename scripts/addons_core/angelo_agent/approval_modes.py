# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's approval mode held in C (grant_authority.APPROVAL_MODES; the C guard review, 2026-11-15, SHOULD-3/4).

Auto runs bounded edits without a card. Before C, whether a chat was in Auto was only its Python state, which an agent
script can write. Now Auto counts only while C holds it for the chat:

* the person's choice sets it (``set_options`` in a grant scope: controller_entries), and Ask or Read only narrows;
* a new chat starts in Auto (``held``, Angelo's own decision), and a chat read from Angelo's private session file gets
  the mode that file holds;
* the file is written with Auto only as C holds it (``as_held``), so a script's write to the chat's Python state
  never reaches what the next launch reads;
* the bounded-edit rule asks unless C holds Auto (auto_reasons.bounded_reason).
"""

from . import grant_authority
from .chat_settings import stored_options

AUTO = "AUTO_BOUNDED"


def held(state):
    """Hold ``state``'s saved or default mode in C for its chat (Angelo's own decision); returns ``state``."""
    mode = stored_options(state.get("options"))["approval"]
    grant_authority.mirror(grant_authority.APPROVAL_MODES, [(state["id"], AUTO if mode == AUTO else "ASK")])
    return state


def as_held(state):
    """``state``'s options to save: Auto only while C holds it for the chat, else Ask."""
    options = stored_options(state.get("options"))
    if options["approval"] == AUTO and not grant_authority.chat_in_auto(state.get("id", "")):
        options["approval"] = "ASK"
    return options


def record(chat_id, mode):
    """The person's choice for the chat (a grant scope for Auto; Ask and Read only always narrow)."""
    grant_authority.record(grant_authority.APPROVAL_MODES, chat_id, AUTO if mode == AUTO else "ASK")


def inherit_mode(controller, lead):
    """``controller``'s options are its lead's, and C holds the same approval mode for it: Auto only as C holds it for
    the lead, never the new chat's default Auto while it asks (a worker chat: worker_sessions)."""
    mode = as_held(lead.state)["approval"]
    controller.state["options"] = dict(lead.state.get("options") or {}, approval=mode)
    held(controller.state)
