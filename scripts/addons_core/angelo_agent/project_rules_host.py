# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a project's rules live in its file, and the rules each request carries (``project_rules``).

**This project** is a Text data-block, "Angelo Rules", marked with the ``_angelo_rules`` custom property: it saves
with the .blend and travels with it, shows in the Outliner's Blender File view, and is never written into a glTF
(the exporter writes no Text). A Text a library links in is never read: only the file's own. The person writes it in
the chat's Rules page (``rules_sheet``); an edit made anywhere else (the Text Editor, a script, another program) is
a text this person has not decided, so it waits for their review, as a downloaded file's rules do.

``apply`` runs at every request's preparation (``ui.prepare_controller``): it reads the text (bounded before it is
read whole), sets ``controller.rules`` to the block the model gets (``project_rules.model_block``) and
``controller.rules_standing``, and posts the review card once per chat and text (``REVIEW``, a person-only notice
with **Review rules**; never in the model's context). Main thread.
"""

from pathlib import Path

from . import project_rules
from .ui_text import n_, notice

TEXT_NAME = "Angelo Rules"   # i18n: a data-block name, the same in every language (found by its mark, not its name)
MARK = "_angelo_rules"
REVIEW = n_("This file has project rules Angelo isn't using yet: they came with the file or changed outside "
            "the Rules page. Review them to use them.")
REVIEW_ACTION = "notice_rules_review"

_stores = {}


def store(directory):
    """The state folder's ``RulesStore``, one per folder for the process (it caches its file)."""
    key = str(Path(directory))
    found = _stores.get(key)
    if found is None:
        found = _stores[key] = project_rules.RulesStore(Path(directory))
    return found


def rules_text_block(bpy):
    """The file's own marked Text (the one named "Angelo Rules" first), or None."""
    marked = [text for text in bpy.data.texts if text.library is None and text.get(MARK)]
    marked.sort(key=lambda text: (text.name != TEXT_NAME, text.name))
    return marked[0] if marked else None


def project_text(bpy):
    """The project's rules as written in the file; None when the Text is too long to be rules (never read whole)."""
    text = rules_text_block(bpy)
    if text is None:
        return ""
    if len(text.lines) > project_rules.LIMIT + 1 or sum(len(line.body) for line in text.lines) > project_rules.LIMIT * 2:
        return None
    return text.as_string()


def write_project_text(bpy, value):
    """The person's Save: the file's rules become ``value`` (cleaned); an empty text removes the Text. Returns the
    text kept. The decision is recorded by the caller (``rules_sheet``), which only a person's click reaches."""
    value = project_rules.clean(value)
    text = rules_text_block(bpy)
    if not value:
        if text is not None:
            bpy.data.texts.remove(text)
        return ""
    if text is None:
        text = bpy.data.texts.new(TEXT_NAME)
        text[MARK] = 1
    text.use_module = False   # Rules are words, never a script that registers at load.
    text.from_string(value)
    return value


def apply(ui, controller):
    """Set the rules ``controller``'s next request carries, and post the review card when one is owed."""
    try:
        directory = ui.state_directory()
        standing = project_rules.standing(store(directory), project_text(ui.bpy))
    except (OSError, ValueError, AttributeError, ReferenceError):
        standing = project_rules.Standing("", "", "invalid")
    controller.rules = project_rules.model_block(standing)
    controller.rules_standing = standing
    if standing.project_state == "review":
        owed = project_rules.digest(standing.project_text)
        if getattr(controller, "rules_review_posted", None) != owed:
            controller.rules_review_posted = owed
            controller.emit("message", {"role": "error", "notice": True, "rules_review": owed, **notice(REVIEW)})
    return standing


def review_stands(controller):
    """Whether the review card still has something to review (``chat_notices``: its button is drawn only then)."""
    standing = getattr(controller, "rules_standing", None)
    return standing is not None and standing.project_state == "review"


from . import grant_guard as _grant_guard  # noqa: E402  (the entry behind a grant: grant_guard.entry)
write_project_text = _grant_guard.entry(write_project_text)   # The Rules page's save: the person's words only.
