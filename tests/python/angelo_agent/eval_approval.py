# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live eval's approval settings: the product default, or Ask, and the person's Always allow scripts.

By default each trial's chats keep the product's default approval, Auto bounded
edits (``chat_settings.DEFAULT_APPROVAL``), so a benchmark measures what a
person gets: typed bounded edits run without asking and the in-app tester only
answers what still asks. ``--approval ask`` puts the chats in Ask, for suites
that measure every approval. ``--always-allow-scripts`` sets the person's
Always allow scripts preference in the trial's own profile, as the script
card's button does, so scene Python runs unasked in Auto (the opted-in path).
``--python-approvals`` keeps answering any script that still asks. Each trial
reports the mode its chat actually had (``apply``); a report without one was
recorded in Ask, before the default changed (``eval_gate.approval_mode``), and
the gate tools refuse to compare runs of different modes. Contract:
doc/angelo/agent_execution.rst, "Always allow scripts".
"""

import os

CHOICES = ("default", "ask")


def add_arguments(parser):
    parser.add_argument("--approval", choices=CHOICES, default="default",
                        help="default: each new chat keeps the product's default approval, Auto · bounded edits "
                             "(typed bounded edits run without asking, as for a person); ask: Ask before changes, "
                             "for suites that measure the approval of every edit")
    parser.add_argument("--always-allow-scripts", action="store_true",
                        help="set the person's Always allow scripts preference in the trial's own profile, as the "
                             "script card's button does: scene Python runs without a review in Auto chats")


def check(parser, args, prefix=""):
    """Refuse Always allow scripts with Ask before anything runs (``prefix``: the bench's ``angelo_`` options)."""
    if getattr(args, prefix + "always_allow_scripts", False) and getattr(args, prefix + "approval", "default") == "ask":
        flag = "--" + prefix.replace("_", "-")
        parser.error("%salways-allow-scripts covers Auto chats only; with %sapproval ask every script still asks"
                     % (flag, flag))
    return args


def plan(args):
    return {"approval": getattr(args, "approval", "default"),
            "always_allow_scripts": bool(getattr(args, "always_allow_scripts", False))}


def environment(args):
    """The trial's variables: ``ANGELO_EVAL_APPROVAL`` is ASK or empty (the product default)."""
    return {"ANGELO_EVAL_APPROVAL": "ASK" if getattr(args, "approval", "default") == "ask" else "",
            "ANGELO_EVAL_ALWAYS_ALLOW_SCRIPTS": "1" if getattr(args, "always_allow_scripts", False) else ""}


# In the app (eval_live_agent_session) -----------------------------------------------------------

def options():
    """The chat options the tester sets: none for the product default, Ask for --approval ask."""
    approval = os.environ.get("ANGELO_EVAL_APPROVAL", "")
    if approval not in {"", "ASK"}:
        raise ValueError("ANGELO_EVAL_APPROVAL is ASK or empty")
    return {"approval": approval} if approval else {}


def apply(ui, controller):
    """After the chat's options: Always allow scripts when asked for; the report fields of the chat's actual mode."""
    from angelo_agent import always_allow
    from angelo_agent.chat_settings import clean_options
    if os.environ.get("ANGELO_EVAL_ALWAYS_ALLOW_SCRIPTS") == "1":
        always_allow.set_enabled(True)  # Saved in this trial's own state folder, as the card's button saves it.
    approval = clean_options(controller.state.get("options"))["approval"]
    return {"approval": approval, "approval_policy": approval + " with test-only typed allowlist for what still asks",
            "always_allow_scripts": bool(controller.always_allow_scripts)}
