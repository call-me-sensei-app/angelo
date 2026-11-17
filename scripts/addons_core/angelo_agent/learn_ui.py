# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Learn: its link in the chat bar's + menu, the Learn card, and the per-chat option Teach me as you work.

The + menu's footer holds **Learn** (a link beside Report a bug: a tile of its own would push the menu's last row out
of view at laptop sizes in every language, ``chat_add_layout``). It opens the Learn card (``card_host``): what
learning with Angelo is, and the switch **Teach me as you work** for the chat on screen. It is off by default and
learn is strictly opt-in: with it off no how-to card shows anywhere and an opened step shows what it always did; with
it on, the how-to cards (``learn_cards``) of that chat's latest request are open by themselves and any other typed
step shows its card when opened (``learn_rows``). The choice is the person's, per chat (``learn_option``). No model
tool reads or changes it, and no model reads anything here.

``teaching(chat_id)`` is what the transcript asks while it draws: memory only (``learn_option.load`` runs at
register).

Another module adds rows to the card under the switch with ``add_rows(provider)`` at its own ``register`` and takes
them away with ``remove_rows(provider)`` at its ``unregister`` (the lessons do). ``provider(chat_id)`` returns rows as
``chat_destinations.leaf("learn", ...)`` makes them (a title, a line under it, the operator a click runs and its
properties); a click closes the card and runs the operator. A provider that raises is left out and noted, never the
card.
"""

import bpy

from . import card_kit as kit, learn_option
from .ui_text import iface_, n_, rpt_

TITLE = n_("Learn", "Angelo")
WHY = n_("See how to do each of Angelo's steps yourself")
ABOUT = n_("Turn on Teach me as you work to see, under each step Angelo makes in this chat, how to do it yourself: "
           "the command, its keys in your keymap, where it is and the settings it used.")
TEACH = n_("Teach me as you work")
TEACH_WHY = n_("The steps of your latest request open by themselves in this chat")
ON = n_("Angelo now shows how to do each step yourself in this chat")
OFF = n_("Angelo no longer shows how to do the steps yourself in this chat")
NO_CHAT = n_("Open a chat first: this is set for one chat")
NOT_SAVED = n_("Angelo could not save this choice: {reason}")
CLOSE = n_("Close")
WIDTH = 460
_folder = ""   # The state folder, read once at register: asking for it makes the folder, which a draw must not.
_more = []     # Providers of the card's further rows, in the order they were added (``add_rows``).


def _directory():
    from . import ui
    return _folder or ui.state_directory()


def _shown_chat():
    """The id of the chat on screen, or ""."""
    from . import ui
    controller = ui._controller
    return controller.state.get("id", "") if controller is not None else ""


def teaching(chat_id):
    """Whether ``chat_id`` has Teach me as you work on (memory only: safe while drawing)."""
    return bool(chat_id) and learn_option.teaching(_directory(), chat_id)


def add_rows(provider):
    """Show ``provider(chat_id)``'s rows on the Learn card, under the switch. Adding one twice keeps one."""
    if provider not in _more:
        _more.append(provider)


def remove_rows(provider):
    if provider in _more:
        _more.remove(provider)


def more_rows(chat_id):
    """The rows the other modules add, in order; a provider that raises is left out (noted)."""
    from . import chat_guard
    found = []
    for provider in tuple(_more):
        found += chat_guard.call("The Learn card's rows", provider, chat_id, default=()) or ()
    return found


def _redraw(context):
    from . import live_windows
    for window in live_windows.windows(context.window_manager):   # The chat may be in any window.
        for area in live_windows.areas(window):
            area.tag_redraw()


class ANGELO_OT_learn_teach(bpy.types.Operator):
    # The Learn card's switch. A comment, not a docstring: bl_description is the tooltip.
    bl_idname = "angelo.learn_teach"
    bl_label = "Teach Me as You Work"
    bl_description = ("Show how to do each of Angelo's steps yourself, opening the steps of your latest request in "
                      "this chat")
    bl_options = {'INTERNAL'}

    session_id: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        chat_id = self.session_id or _shown_chat()
        if not chat_id:
            self.report({'WARNING'}, rpt_(NO_CHAT))
            return {'CANCELLED'}
        on = not teaching(chat_id)
        try:
            learn_option.set_teaching(_directory(), chat_id, on)
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, rpt_(NOT_SAVED, reason=str(error)))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt_(ON if on else OFF))
        _redraw(context)
        return {'FINISHED'}


class LearnCard(kit.BaseCard):
    """What learning with Angelo is, the chat's switch, and the rows other modules add (``add_rows``)."""

    kind, modal, priority = "learn", True, 55
    cancel_action, default_action, outside_dismisses = "close", "close", True

    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.rows = []
        self.changes = 0

    def version(self):
        return (self.changes, teaching(self.chat_id), len(_more))

    def layout(self, view, region_w, region_h, scale, ui):
        def plan(size):
            builder = self._build(view, size, region_w, ui)
            top = min((region_h + builder.height()) / 2, region_h - 10 * builder.s)
            return builder.place((region_w - builder.width) / 2, top, True, (region_w, region_h))
        return kit.fit(plan, region_w, region_h, scale)

    def _build(self, view, scale, region_w, ui):
        from .chat_add_menu import _shown
        width = min(WIDTH * scale, region_w - 40 * scale)
        b = kit.Builder(view, scale, width, ui, padding=26)
        b.heading(iface_(TITLE))
        b.gap(6)
        b.paragraph(iface_(ABOUT), 13.5, kit.MUTED, leading=1.45)
        b.gap(14)
        b.toggle("teach", None, iface_(TEACH), teaching(self.chat_id), caption=iface_(TEACH_WHY),
                 enabled=bool(self.chat_id))
        self.rows = more_rows(self.chat_id)
        if self.rows:
            b.gap(10)
            b.rule()
            b.gap(6)
            for index, row in enumerate(self.rows):
                b.list_row("open", index, _shown(row, "label"), _shown(row, "subtitle"))
        b.gap(16)
        b.buttons([("close", None, iface_(CLOSE), "primary", True)])
        b.close_button("close")
        return b

    def act(self, action, value, host):
        if action == "teach":
            bpy.ops.angelo.learn_teach(session_id=self.chat_id)
            self.changes += 1
        elif action == "open" and isinstance(value, int) and value < len(self.rows):
            row = self.rows[value]
            host.close("closed")
            group, name = row["operator"].split(".", 1)
            # After the card has closed, as a + menu row runs its operator: with its own Undo step.
            bpy.app.timers.register(lambda: getattr(getattr(bpy.ops, group), name)(
                'INVOKE_DEFAULT', True, **dict(row.get("properties") or ())) and None, first_interval=0.05)
        elif action in {"close", "dismiss"}:
            host.close("closed")


class ANGELO_OT_learn(bpy.types.Operator):
    # The + menu's Learn link, and operator search. A comment, not a docstring: bl_description is the tooltip.
    bl_idname = "angelo.learn"
    bl_label = "Learn"
    bl_description = "See how to do Angelo's steps yourself, and choose whether Angelo teaches as it works in this chat"
    bl_translation_context = "Angelo"

    def execute(self, context):
        from . import card_host
        card_host.request(LearnCard(_shown_chat()))
        card_host.pump(lambda: True)   # Shown now, or after the card in front of it.
        return {'FINISHED'}


CLASSES = (ANGELO_OT_learn_teach, ANGELO_OT_learn)


def register():
    global _folder
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    _folder = ""
    _folder = _directory()
    learn_option.use(_folder)   # The folder the transcript's draw asks about (learn_rows.begin).
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "learn", TITLE, WHY, glyph="learn", order=89, section=n_("Learn", "Angelo"), operator=ANGELO_OT_learn.bl_idname,
        placement="footer", owner="learn_ui"))


def unregister():
    global _folder
    from . import chat_destinations, learn_card_draw, learn_resolver, learn_rows
    chat_destinations.unregister("learn")
    learn_rows.forget()
    learn_card_draw.forget()
    learn_resolver.forget()
    learn_option.forget()
    _more.clear()
    _folder = ""
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
