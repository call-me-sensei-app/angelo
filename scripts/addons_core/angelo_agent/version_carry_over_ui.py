# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The first launch on a newer Blender base: the earlier settings come over on their own, then one short notice.

``version_carry_over`` moved the agent state when the add-on registered. Once the layout has settled
(``intro.schedule_first_launch``) this module finishes the update:

* **The earlier Angelo's settings** (preferences, shortcuts, add-ons, presets, start-up file) come over by the same
  import the setup card's "Use my earlier Angelo settings" runs (``settings_import_apply.run``), without asking:
  an update is not a new install, so the setup card does not show (nor, ever, Blender's Quick Setup). It runs only
  while this version has no saved preferences of its own, so it never replaces any.
* **One corner notice** (``update_card.NoticeCard``) says what came over, or why the chats did not move yet
  (another Angelo is open, so this session uses them in place; a move that stopped), and any add-on that did not
  load, once the layout the import reloaded has settled. It is shown on the launch that did the work and never again. An import that fails saves nothing, so the
  preferences it copied stay.

Main thread only.
"""

import traceback

import bpy

from . import settings_import as si, settings_import_apply as apply, settings_import_card as words
from . import version_carry_over as carry
from .ui_text import iface_, n_

READY_TITLE = n_("Angelo {version} is ready")
READY_ALL = n_("Your chats, snapshots and settings came over from your earlier Angelo.")
READY_CHATS = n_("Your chats and snapshots came over from your earlier Angelo.")
READY_SETTINGS = n_("Your settings came over from your earlier Angelo.")
KEPT = n_("Where both versions had the same file, the earlier one is kept in the {folder} folder.")
WAITING_TITLE = n_("Your chats are still with your earlier Angelo")
WAITING_BODY = n_("Another Angelo is open, so this time Angelo uses your chats and snapshots where they are. Quit "
                  "every Angelo, then open Angelo again to bring them over.")
MOVED_ON_TITLE = n_("Your chats are in your newer Angelo")
MOVED_ON_BODY = n_("They moved when you updated, and they're safe there. Chats you start here come along the next "
                   "time you open the newer Angelo.")
STOPPED_TITLE = n_("Some of your chats did not come over yet")
STOPPED_BODY = n_("Nothing was deleted. Angelo continues the next time it opens; Preferences > AI Providers has "
                  "the details.")

last_outcome = None   # The automatic import's outcome on this launch; window fixtures read it.
shown = None          # (icon, title, body) of the notice this launch showed; window fixtures read it.


def upgrade_source():
    """The earlier Angelo version's folder to bring settings from, while this version has none of its own; else None."""
    from . import splash_setup
    if splash_setup.preferences_saved():
        return None
    try:
        source, _reason = apply.find_source(include_angelo=True)
    except Exception:   # Reading folders must never stop the startup.
        traceback.print_exc()
        return None
    return source if source is not None and source.app == si.ANGELO else None


_finished = False
_owed = None   # The notice held back while the import's reloaded start-up file settles: a load drops a visible card.


def owed():
    """Whether the import reloaded the layout and its notice still waits for the new one."""
    return _owed is not None


def pending():
    """Whether this launch still owes the update's import or notice."""
    if _owed is not None:
        return True
    if _finished:
        return False
    result = carry.last_result
    return upgrade_source() is not None or (result is not None and result.status in {
        "migrated", "failed", carry.DEFERRED, carry.RETURNED, carry.MOVED_ON, carry.finish.RECORDS})


def finish():
    """Import the earlier settings if owed, then show the notice. Returns whether the start-up file was reloaded
    (the caller lets the new layout settle before anything else opens)."""
    global last_outcome, _finished, _owed
    if _owed is not None:
        notice, _owed = _owed, None
        _show(notice)
        return False
    _finished = True
    source = upgrade_source()
    outcome = None
    if source is not None:
        try:
            outcome = apply.run(source)
        except Exception as error:   # Startup goes on whatever an imported file does.
            traceback.print_exc()
            # Nothing is saved: the preferences in memory are not the person's, and the copied file must stay (nor
            # does Blender's save on quit write them: they are marked unchanged).
            bpy.context.preferences.is_dirty = False
            outcome = {"app": source.app, "version": source.version_label, "exception": str(error), "failed": [],
                       "errors": [], "reloaded_layout": False}
        last_outcome = outcome
    notice = notice_text(carry.last_result, outcome)
    reloaded = bool(outcome and outcome.get("reloaded_layout"))
    if notice is not None and reloaded:
        _owed = notice
    elif notice is not None:
        _show(notice)
    return reloaded


def notice_text(result, outcome):
    """``(icon, title, body, calm)`` for the update's notice, or None when there is nothing to say."""
    status = result.status if result is not None else "nothing"
    if status == carry.DEFERRED:
        return "alert", iface_(WAITING_TITLE), iface_(WAITING_BODY), False
    if status == carry.MOVED_ON:
        carry.told_moved_on(bpy.utils.user_resource('CONFIG'))
        return "check", iface_(MOVED_ON_TITLE), iface_(MOVED_ON_BODY), False
    if status == "failed":
        return "alert", iface_(STOPPED_TITLE), iface_(STOPPED_BODY), False
    if status == carry.finish.RECORDS:   # Said once, the launch it gives up: the files it could not update.
        from .display_text import shown
        return "alert", iface_(carry.finish.RECORDS_TITLE), shown(result.message), False
    moved = status in {"migrated", carry.RETURNED}
    if outcome is not None and outcome.get("exception"):
        return "alert", iface_(words.FAILED_TITLE), iface_(words.FAILED_BODY), False
    if not moved and outcome is None:
        return None
    lines = [iface_(READY_ALL if moved and outcome is not None else READY_CHATS if moved else READY_SETTINGS)]
    kept = carry.last_kept_folder if moved else None
    if kept:
        lines.append(iface_(KEPT, folder=kept))
    if outcome is not None:
        if outcome.get("failed"):
            lines.append(words.not_loaded(outcome))
        if outcome.get("errors"):
            lines.append(iface_(words.NOT_COPIED))
    calm = not (kept or (outcome and (outcome.get("failed") or outcome.get("errors"))))
    from angelo_identity import APP_VERSION_DISPLAY
    return ("check" if calm else "alert"), iface_(READY_TITLE, version=APP_VERSION_DISPLAY), " ".join(lines), calm


def _show(notice):
    global shown
    from . import card_host
    from .update_card import NoticeCard
    icon, title, body, calm = notice
    shown = (icon, title, body)
    card_host.request(NoticeCard(icon, title, body, lifetime=30 if calm else 0))
    card_host.pump(lambda: True)
