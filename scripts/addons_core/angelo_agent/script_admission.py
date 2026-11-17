# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The C ledger's approval for the script a call is about to run (doc/angelo/grant_operators.rst, "Authority in C").

``run_confined`` runs a script only when ``approved_scripts`` holds its exact text for its call, once. The person's
Approve writes it (``TrustedScripts.review_script``). A script a standing consent covers is approved here, as Angelo's
own decision, right before it runs: ``script_consent_kind`` reads those consents from C (grant_authority), so a script
cannot make itself covered by changing Python state. Any other call runs nothing that needs admission.
"""

from . import grant_authority

SCRIPT_TOOL = "angelo_execute_python"


def prepare(chat):
    """The call id ``run_confined`` is asked about for the script ``chat`` is running now (``executing_call``): its
    ``<chat id>/<call id>``, after approving its text when a standing consent held in C covers it. "" without one,
    which admits nothing."""
    call = getattr(chat, "executing_call", None)
    if call is None or call.name != SCRIPT_TOOL:
        return ""
    if getattr(call, "automatic", False) and chat.script_consent_kind(call) is not None:
        grant_authority.automatic(grant_authority.approve_script, chat.state["id"], call.id,
                                  call.arguments.get("code", ""))
    return grant_authority.call_key(chat.state["id"], call.id)
