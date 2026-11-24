# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's Rules page: the person writes the standing rules (``project_rules``) in the composer itself.

Opened from the + menu's footer (**Rules…**, ``ANGELO_OT_chat_rules``) or a review card's **Review rules**
(``chat_notices``). While it is open, the transcript shows the page (its scope, what the rules do, Save and Cancel) and
the composer edits the rules instead of the draft: ``line_of`` hands the composer a ``RulesLine`` of its own, so the
draft, its Undo history and its attachments are never touched, and every editing feature the composer has (wrapping,
selection, the platform's keys, input methods, paste) works on the rules the same way. Return adds a line; the
composer's Send saves. The composer's view keys (``COMPOSER_KEYS``: selection, scroll, a drag, an input method's
composition) are set aside on open and put back on close, so the draft returns exactly as it was.

Only the person decides rules: Save and Use are clicks on the chat canvas, which no script can make (a script that
writes the file's Text only makes a new text to review, ``project_rules_host``). What is decided is always what the
page showed: Save and Use write the page's text to where it lives and decide that text; Don't use declines the text
the page loaded. Scopes: ``project`` (the file's Text) and ``global`` (the state folder). A third tab, ``scripts``,
lists the scripts the person remembered for this project (``reviewed_scripts``) with Remove; it edits nothing.
Main thread.
"""

from . import project_rules, project_rules_host
from .ui_text import iface_, n_, notice

PREFIX = "rules_sheet_"
COMPOSER_KEYS = ("selection", "composer_upstream", "composer_first", "composer_view", "composer_drag",
                 "composer_kill", "composer_clicks", "composer_ime", "composer_ime_shown", "composer_carry")

TITLE = n_("Rules")
PROJECT = n_("This project")
GLOBAL = n_("All projects")
ABOUT_PROJECT = n_("Angelo follows these in every request on this file. They're saved in the file, so anyone you share "
                   "it with sees them too. Write them in the box below.")
ABOUT_GLOBAL = n_("Angelo follows these in every request, in every project. They stay on this computer. Write them in "
                  "the box below.")
ABOUT_REVIEW = n_("These rules came with this file, or changed outside this page. They become instructions to the "
                  "agent: use them only if you trust where the file came from.")
SAVE, CANCEL, USE, DONT_USE = n_("Save"), n_("Cancel"), n_("Use these rules"), n_("Don't use")
SCRIPTS, CLOSE, REMOVE = n_("Scripts"), n_("Close"), n_("Remove")
ABOUT_SCRIPTS = n_("Scripts you chose to always run in this project, each while its text stays exactly the same. "
                   "Remove one and Angelo asks before it runs again.")
NO_SCRIPTS = n_("No scripts are remembered for this project.")
UNSAVED_SCRIPTS = n_("Save the file first: scripts are remembered per saved project.")
SCRIPT_ROW = n_("{label} · {size} KB · {date}")
MAX_SHOWN = 8
SAVED_PROJECT = n_("Saved the rules for this project. They apply from your next message.")
SAVED_GLOBAL = n_("Saved the rules for all projects. They apply from your next message.")
CLEARED = n_("Removed the rules. Your next message goes without them.")
DECLINED = n_("Angelo won't use this file's rules. You can review them again from Rules in the + menu.")
TOO_LONG = n_("Rules can be at most {limit} characters. These have {count}.")
BAD_TEXT = n_("Rules can't hold control, invisible or text-direction characters.")
CHANGED_TEXT = n_("These rules changed after the page showed them. Check them and press again.")
NO_PROJECT_TEXT = n_("This file's rules can't be read: they are too long to be rules.")


class RulesLine:
    """The composer's line while the page is open: ``body`` and a character ``current_character``, as a Console
    line has (``composer_input`` keys a line without ``as_pointer`` by its ``id``, so its Undo history is its own)."""

    def __init__(self, body):
        self.body, self.current_character = body, len(body)


def _loaded(ui, scope):
    if scope == "scripts":
        return "", ""
    if scope == "global":
        return project_rules_host.store(ui.state_directory()).global_rules(), ""
    text = project_rules_host.project_text(ui.bpy)
    if text is None:
        return "", (NO_PROJECT_TEXT, {})
    try:
        return project_rules.clean(text), ""
    except project_rules.RulesError:
        return "", (NO_PROJECT_TEXT, {})


def scopes():
    """The page's tabs: Scripts only while Always run is offered (``reviewed_scripts.OFFERED``)."""
    from . import reviewed_scripts
    return ("project", "global", "scripts") if reviewed_scripts.OFFERED else ("project", "global")


def open_sheet(state, controller, scope="project", review=False, ui=None):
    """Show the page in this chat area (``state``) for ``scope``; switching scope keeps the draft set aside."""
    if ui is None:
        from . import ui
    if scope not in scopes():
        scope = "project"
    sheet = state.get("rules_sheet")
    aside = sheet["aside"] if sheet is not None else {key: state.pop(key) for key in COMPOSER_KEYS if key in state}
    for key in COMPOSER_KEYS:
        state.pop(key, None)
    text, error = _loaded(ui, scope)
    review = review and scope == "project" and getattr(getattr(controller, "rules_standing", None),
                                                       "project_state", None) == "review"
    state["rules_sheet"] = {"scope": scope, "review": review, "line": RulesLine(text), "loaded": text,
                            "error": error, "aside": aside, "session": controller.state["id"]}
    state.pop("popover", None)
    state["focus_request"] = True   # The composer takes the keyboard: typing goes into the rules.


def close_sheet(state, controller=None):
    """Put the composer back as it was. A chat switched while the page was open keeps its own composer state."""
    sheet = state.pop("rules_sheet", None)
    if sheet is None:
        return
    for key in COMPOSER_KEYS:
        state.pop(key, None)
    if controller is None or controller.state["id"] == sheet["session"]:
        state.update(sheet["aside"])


def line_of(state, space):
    """The line the composer edits and draws: the page's while it is open, else the chat's draft line."""
    sheet = state.get("rules_sheet") if state else None
    if sheet is not None:
        return sheet["line"]
    return space.history[-1] if space.history else None


def is_open(state):
    return bool(state) and state.get("rules_sheet") is not None


def shown_digest(text):
    """The digest of the rules text the page shows (``text``, as the composer draws it), or None when it can't be
    kept: the value the page's Save and Use, and the composer's Send, carry while it is open, so C tickets the
    person's press by the text they saw (round 1, R-3)."""
    try:
        return project_rules.digest(text)
    except project_rules.RulesError:
        return None


def error_text(sheet):
    """The page's error as the person reads it now: kept as its template and fields, translated as the page draws
    (re-read of 23413c6bae1, N-4: a language chosen while the page is open shows at once)."""
    error = sheet.get("error")
    return iface_(error[0], **error[1]) if error else ""


def _checked(sheet, body):
    try:
        return project_rules.clean(body)
    except project_rules.RulesError:
        body = body if isinstance(body, str) else ""
        normalised = body.replace("\r\n", "\n").rstrip()
        sheet["error"] = ((TOO_LONG, {"limit": project_rules.LIMIT, "count": len(normalised)})
                          if len(normalised) > project_rules.LIMIT else (BAD_TEXT, {}))
        return None


def save(state, controller, ui, pressed=None):
    """Save (or, in a review, Use): the page's text becomes the rules where its scope keeps them, decided Use. Only
    the text the press was drawn with (``pressed``, its ``shown_digest``): text put in the box after the page drew
    it is never kept, and the page stays open on it."""
    sheet = state.get("rules_sheet")
    value = _checked(sheet, sheet["line"].body) if sheet is not None else None
    if value is None:
        return False
    if pressed is None or project_rules.digest(value) != pressed:
        sheet["error"] = (CHANGED_TEXT, {})   # Said, so the person checks what is there now (round 3, NIT g).
        return False
    store = project_rules_host.store(ui.state_directory())
    if sheet["scope"] == "global":
        store.set_global(value)
        said = SAVED_GLOBAL if value else CLEARED
    else:
        if value:   # Decided first: a decision that can't be made leaves the file's rules as they were (N-6).
            store.decide(value, True)
        project_rules_host.write_project_text(ui.bpy, value)
        said = SAVED_PROJECT if value else CLEARED
    close_sheet(state, controller)
    project_rules_host.apply(ui, controller)
    controller.emit("message", {"role": "system", "notice": True, **notice(said)})
    return True


def decline(state, controller, ui):
    """Don't use, in a review: the text the page loaded is declined; the file keeps it."""
    sheet = state.get("rules_sheet")
    if sheet is None or not sheet["review"]:
        return False
    if sheet["loaded"]:
        project_rules_host.store(ui.state_directory()).decide(sheet["loaded"], False)
    close_sheet(state, controller)
    project_rules_host.apply(ui, controller)
    controller.emit("message", {"role": "system", "notice": True, **notice(DECLINED)})
    return True


def dispatch(state, controller, action, value, ui=None):
    """The page's clicks, and the composer's Send while it is open (Save)."""
    if ui is None:
        from . import ui
    if state["rules_sheet"]["scope"] == "scripts":
        return _scripts_action(state, controller, action, value, ui)
    if action in ("send", PREFIX + "save"):
        return save(state, controller, ui, value)
    if action == PREFIX + "decline":
        return decline(state, controller, ui)
    if action == PREFIX + "cancel":
        close_sheet(state, controller)
        return True
    if action == PREFIX + "scope" and value in scopes() and not state["rules_sheet"]["review"]:
        open_sheet(state, controller, scope=value, ui=ui)
        return True
    return False


def _scripts_action(state, controller, action, value, ui):
    """The Scripts tab: Remove one, switch tabs, Close. Send and Save do nothing here: it has no text to save."""
    from . import reviewed_scripts
    if action == PREFIX + "revoke":
        project = controller.script_review_project()
        return bool(project) and reviewed_scripts.store(controller.store.directory).revoke(project, value)
    if action == PREFIX + "scope" and value in scopes():
        open_sheet(state, controller, scope=value, ui=ui)
        return True
    if action == PREFIX + "cancel":
        close_sheet(state, controller)
        return True
    return False


def _script_rows(controller):
    """``(sentence, [(digest, row text)])`` for the Scripts tab."""
    import time
    from . import reviewed_scripts
    project = controller.script_review_project()
    if not project:
        return iface_(UNSAVED_SCRIPTS), []
    entries = reviewed_scripts.store(controller.store.directory).entries(project)
    rows = [(key, iface_(SCRIPT_ROW, label=label or iface_(SCRIPTS), size=max(1, round(size / 1024)),
                         date=time.strftime("%Y-%m-%d", time.localtime(at)))) for key, label, at, size in entries]
    return (iface_(ABOUT_SCRIPTS) if rows else iface_(NO_SCRIPTS)), rows[:MAX_SHOWN]


def _overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def draw(view, state, controller, viewport, width, height, shown=None):
    """The page as a card at the top of the transcript's ``viewport`` (layout units, y up), so the newest rows and any
    approval waiting above the composer stay in view. Transcript hits under it are dropped; its own are the chat's.
    ``shown``: the rules as the composer draws them this frame (the line's text when not given)."""
    from . import ui_tokens as tk
    sheet = state["rules_sheet"]
    if sheet["session"] != controller.state["id"]:
        close_sheet(state, controller)
        return
    sheet["shown"] = shown_digest(sheet["line"].body if shown is None else shown)
    vx, vy, vw, vh = viewport
    x, w = vx + 12, vw - 24
    scripts = sheet["scope"] == "scripts"
    about, listed = _script_rows(controller) if scripts else (iface_(
        ABOUT_REVIEW if sheet["review"] else ABOUT_GLOBAL if sheet["scope"] == "global" else ABOUT_PROJECT), [])
    rows = [row for row, _ in view.wrap(about, w - 32, 12, view._font)]
    errors = [row for row, _ in view.wrap(error_text(sheet), w - 32, 12, view._font)] if sheet["error"] else []
    h = min(vh - 8, 36 + 38 + 26 + 18 * (len(rows) + len(errors)) + 30 * len(listed) + 52)
    y = vy + vh - 8 - h
    rect = (x, y, w, h)
    state["hits"] = [hit for hit in state["hits"] if not _overlaps(hit[0], rect)]
    view.shape(x, y, w, h, tk.HIGHLIGHT, 12)
    view.shape(x + 1, y + 1, w - 2, h - 2, tk.RAISED, 11)
    top = y + h - 30
    view.text(iface_(TITLE), x + 16, top, 16)
    tab_x = x + 16
    for scope, label in zip(scopes(), (PROJECT, GLOBAL, SCRIPTS)):
        label = iface_(label)
        tab_w = view.measure(label, 12) + 24
        chosen = sheet["scope"] == scope
        view.shape(tab_x, top - 40, tab_w, 26, tk.HIGHLIGHT if chosen else tk.CANVAS, 13)
        view.text(label, tab_x + 12, top - 31, 12, tk.TEXT if chosen else tk.MUTED)
        if not sheet["review"]:
            state["hits"].append(((tab_x, top - 40, tab_w, 26), PREFIX + "scope", scope))
        tab_x += tab_w + 8
    line_y = top - 64
    for row in rows:
        view.text(row, x + 16, line_y, 12, tk.MUTED)
        line_y -= 18
    for row in errors:
        view.text(row, x + 16, line_y, 12, tk.CORAL)
        line_y -= 18
    remove = iface_(REMOVE)
    remove_w = view.measure(remove, 12) + 24
    for key, text in listed:   # A remembered script: what it said it does, its size and when; Remove.
        view.text(view.elide(text, w - remove_w - 48, 12), x + 16, line_y - 4, 12, tk.TEXT)
        view.shape(x + w - 16 - remove_w, line_y - 12, remove_w, 26, tk.CANVAS, 8)
        view.text(remove, x + w - 4 - remove_w, line_y - 4, 12, tk.TEXT)
        state["hits"].append(((x + w - 16 - remove_w, line_y - 12, remove_w, 26), PREFIX + "revoke", key))
        line_y -= 30
    buttons = (((CLOSE, "cancel"),) if scripts else ((DONT_USE, "decline"), (USE, "save")) if sheet["review"]
               else ((CANCEL, "cancel"), (SAVE, "save")))
    right = x + w - 16
    for label, action in reversed(buttons):
        label = iface_(label)
        button_w = view.measure(label, 12) + 28
        right -= button_w
        primary = action == "save"
        view.shape(right, y + 12, button_w, 30, tk.TEAL if primary else tk.CANVAS, 8)
        view.text(label, right + 14, y + 23, 12, tk.CANVAS if primary else tk.TEXT)
        state["hits"].append(((right, y + 12, button_w, 30), PREFIX + action, sheet["shown"] if primary else None))
        right -= 8
