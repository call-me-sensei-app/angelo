# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plan mode's interface text (doc/angelo/plan_mode.rst, "The card and chips"): every string the person reads, as
``n_`` constants translated where drawn (localization.rst). Model-facing text is ``planning_state``'s, never here.
No bpy."""

from .ui_text import iface_, n_, notice

# The + menu row (chat_destinations).
MENU_TITLE = n_("Plan first")
MENU_WHY = n_("Research and compare approaches; the agent changes nothing until you press Go with this")

# Notice rows in the chat.
# What planning enforces, no more (re-read S2): this chat's agent; a job already running is named (JOB_STILL_RUNS).
STARTED = n_("Planning: this chat's agent researches and proposes approaches. It changes nothing until you press Go "
             "with this.")
JOB_STILL_RUNS = n_("{job}, already running, finishes unless you cancel it.")
STOPPED = n_("Planning ended. The chat asks before changes now; choose Auto in its approval control if you want it.")
NOT_PLANNING = n_("This chat isn't planning any more, so that plan can't be used. Start planning again to revise it.")
STALE = n_("A newer plan replaced this one. Choose from the newest plan.")
BUSY = n_("Angelo is still working on your last message. Wait for it, or press Stop, then press Go with this.")
TOOL_BUSY = n_("Finish the tool you are using, then press Go with this.")
NO_AUTO = n_("Your organization requires Ask before changes, so this plan can't run in Auto.")
NOT_SENT = n_("The plan wasn't sent, so the chat is still planning: {reason}")
REVISING = n_("Planning again: tell the agent what should change in the plan.")
ENDED_GOAL = n_("Planning: the goal in this chat ended. This chat's agent researches and proposes approaches; it "
                "changes nothing until you press Go with this.")

# The card.
CARD_TITLE = n_("Plan", "Angelo")   # The card's header (the plan itself; "Plan first" is the menu's action).
RECOMMENDED = n_("Recommended")
ESTIMATE = n_("Estimate: {estimate}")
QUESTIONS = n_("Open questions")
GO = n_("Go with this")   # The button's name in sentences; the button itself names its setting (GO_LABELS).
GO_ASK = n_("Go with this · Ask")
GO_AUTO = n_("Go with this · Auto")
EDIT = n_("Edit")
THEN = n_("Then:")
ASK = n_("Ask before changes")
AUTO = n_("Auto · bounded edits")
FOOTER = n_("Changes you type in the message box go with your choice. Send a message to discuss or revise.")
CHOSEN = n_("You went with approach {number}: {title}")
SUPERSEDED = n_("A newer plan replaced this one.")
ENDED = n_("Planning ended without a choice.")
APPROACH = n_("Approach {number}")

# The composer.
CHIP_PLANNING = n_("Planning · read only")
CHIP_PLANNING_GOAL_ENDED = n_("Planning · read only · the goal ended")
CHIP_PLAN = n_("Plan · {title}")
REVISE = n_("Revise plan")
APPROVAL_LABEL = n_("Planning")   # The approval control's label while planning (chat_composer).

# A request row the person's Go sent (console.display_text).
ROW = n_("Going with approach {number}: {title}")
ROW_NOTES = n_("Your changes: {notes}")

SETTINGS = {"ASK": ASK, "AUTO_BOUNDED": AUTO}
GO_LABELS = {"ASK": GO_ASK, "AUTO_BOUNDED": GO_AUTO}   # Go says the setting it switches to (break-it review M1).


def row_display(choice, notes=""):
    """The person's Go row in their language: the approach they chose, then what they typed with it."""
    text = iface_(ROW, number=int(choice.get("approach", 0)) + 1, title=str(choice.get("title") or ""))
    return text + ("\n" + iface_(ROW_NOTES, notes=notes) if notes else "")


def stopped_notice():
    return notice(STOPPED)


def not_sent_notice(error):
    """Go's send was refused: ``error``'s own translatable sentence when it carries one, else its text."""
    from .display_text import Text
    first = error.args[0] if getattr(error, "args", None) else error
    return notice(NOT_SENT, reason=first if isinstance(first, Text) else str(first)[:300])
