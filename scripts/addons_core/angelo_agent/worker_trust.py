# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's standing script consent: the lead's "Trust scripts for this chat" carries to the chats it starts.

The maintainer's decisions (2026-10-25, parallel edit in 1.0.6):

* A worker chat's scripts follow its own approval mode, which it takes from the lead when it starts. In Ask every
  worker script asks, whatever is trusted (``covers_mode``). In Auto a script asks unless a standing consent covers it.
* A worker chat inherits the lead's Trust scripts for this chat: from its creation (``link``, which
  ``worker_sessions._new_chat`` calls) and again whenever the lead turns it on later (``changed``). It is read live,
  never copied: the lead's own consent token (``TrustedScripts.own_chat_consent``) covers the worker's scripts while
  the lead's trust lasts. The lead turning it off, a New Chat or a document change there, or Read only ends it for its
  workers too, and a script queued under the old token asks (``TrustedScripts.script_consent_kind``), as the lead's
  own queued scripts do.
* The person may turn it off in a worker's own chat (the trust menu there): that worker no longer follows its lead
  until the lead turns trust on again. A worker chat may also be trusted on its own, as any chat is.
* Always allow scripts covers an Auto worker chat as it covers any Auto chat, and a script that names preferences or
  ``angelo_agent`` always asks (``trusted_scripts.consent_excluded``), whatever consent it would otherwise have.

The links live on the chats (``Controller`` objects) and in a weak set, so a chat that is gone holds nothing. No bpy.
"""

import weakref

_followers = weakref.WeakSet()   # Worker chats linked to their lead (``link``), while they live.


def link(lead, worker):
    """``worker``, a new chat ``lead`` starts for a part, follows ``lead``'s chat trust from now on."""
    worker._trust_lead = (weakref.ref(lead), lead.state["id"])
    worker._trust_follows = True
    _followers.add(worker)


def is_worker(chat):
    """Whether ``chat`` works on a worker Blender's part (``worker_routing.bind``)."""
    return getattr(chat, "remote_executor", None) is not None


def lead_of(chat):
    """The lead chat that started ``chat``, while it is still that conversation (a New Chat there starts another), or
    None."""
    reference, session = getattr(chat, "_trust_lead", (None, None))
    lead = reference() if reference is not None else None
    return lead if lead is not None and lead.state.get("id") == session else None


def follows(chat):
    """Whether ``chat`` follows a lead's chat trust (``link``), unless the person turned it off there since."""
    return getattr(chat, "_trust_follows", False)


def inherited(chat):
    """The lead's chat-trust token that covers ``chat``'s scripts now, or None: ``chat`` is a worker chat that still
    follows its lead, and the lead's own trust holds."""
    if not follows(chat) or not is_worker(chat):
        return None
    lead = lead_of(chat)
    return lead.own_chat_consent() if lead is not None else None


def covers_mode(chat, mode):
    """Whether a chat trust may cover ``chat``'s scripts in approval ``mode``: any mode but Read only, and for a worker
    chat Auto only (in Ask every worker script asks)."""
    return mode == "AUTO_BOUNDED" or (mode != "READ_ONLY" and not is_worker(chat))


def changed(chat, enabled):
    """The person turned Trust scripts for this chat on or off in ``chat`` (``set_trusted_scripts``)."""
    if not enabled and chat in _followers:
        chat._trust_follows = False   # Off in a worker's own chat: it no longer follows its lead...
    for worker in list(_followers):
        if lead_of(worker) is chat:
            worker._trust_follows = worker._trust_follows or enabled   # ... until the lead turns trust on again.
            worker.version += 1   # Its trust menu, composer label and cards show the change.
