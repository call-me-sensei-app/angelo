# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A step's how-to card: how a person does by hand what Angelo's typed operations did (learn mode). No Blender here.

A card is built from the step's saved row (``activity_rows``: its ``tool`` and ``arguments``), so every typed step of
every saved chat has one. It is display data: built when a row is opened, never stored and never sent to a model.

``steps(message, lookup)`` returns one ``Step`` per hand command, in order. What a step says comes from two places:

* ``learn_routes.hand``: which command a person uses for the operation, by identifier;
* ``lookup``: what the running app calls it and where it is, read at that moment (``learn_resolver.Lookup``; a host
  test passes a stand-in). The lookup answers None or an empty value for anything the app does not have, and the
  step then leaves that part out. **A card never shows a label, a menu or a key that was not found.**

The lookup's methods (all return display text in the person's language):

    operator_label(idname) -> str | None            the command's own name; None when this build has no such command
    enum_label(idname, rna_name, value) -> str | None   the menu item of an operator's choice (a modifier's type)
    setting(idname, rna_name, value) -> (label, text) | None   one setting with its label and its value as the app shows it
    menu_label(menu) -> str | None                  a menu's title; None when this build has no such menu
    item_label(text) -> str                         a menu's own text for a command (``Route.item``), translated
    editor_label(editor), tab_label(tab), mode_label(mode) -> str
    keys(idname, properties, editor, mode, free) -> key texts bound to the command now (empty: none); ``free``
                                                    names the settings the card leaves to the person
    menu_key(menu, editor, mode) -> str             the key that opens a menu now ("" when none)

Text is translated here, where it is shown (``ui_text``).
"""

from dataclasses import dataclass

from . import learn_routes
from .legacy_names import current_tool_name
from .ui_text import iface_, n_, plural_

# Tools whose arguments hold typed operations: a program, and a recipe once it is expanded to its program.
PROGRAM_TOOLS = frozenset({"angelo_scene_program", "angelo_apply_recipe"})
T_OWN = n_("Done by Angelo's own tool")
MAX_STEPS = 64   # A card's bound: a program holds at most 32 operations, a transform at most three commands.


@dataclass(frozen=True)
class Step:
    """One hand command of a card. Every text is ready to draw; an empty field is left out."""
    kind: str             # The typed operation's kind (for an icon; never shown as text).
    index: int            # The operation's place in its tool call, from 0.
    title: str            # What the command is called.
    keys: tuple = ()      # The keys bound to it now, as the app prints them.
    path: tuple = ()      # Where it is: the editor, a tab, the menus from the header down, then the item.
    opens: tuple = ()     # (a menu's title, the key that opens it now), when a key opens one of the path's menus.
    settings: tuple = ()  # ((label, value), ...): the settings Angelo used.
    needs: tuple = ()     # What must be true first: the mode, what is selected.
    note: str = ""        # How doing it by hand differs, or why there is no single hand command.
    command: bool = False  # Whether it is a command a person runs (it can have a key) or a place or a note.


def operations(message):
    """The typed operations of a step's row, or () when the step is not a typed edit."""
    if not isinstance(message, dict) or current_tool_name(message.get("tool", "")) not in PROGRAM_TOOLS:
        return ()
    arguments = message.get("arguments")
    arguments = arguments if isinstance(arguments, dict) else {}
    program = arguments.get("program") if isinstance(arguments.get("program"), dict) else arguments
    found = program.get("operations")
    return tuple(item for item in found if isinstance(item, dict)) if isinstance(found, list) else ()


def has_card(message):
    """Whether a step's row has a how-to card (cheap: no lookup)."""
    return bool(operations(message))


def steps(message, lookup):
    """The card of one step's row: its hand commands in order, at most ``MAX_STEPS``."""
    found = []
    for index, operation in enumerate(operations(message)):
        hand = learn_routes.hand(operation)
        kind = operation.get("kind") if isinstance(operation.get("kind"), str) else ""
        built = [step for step in (_step(route, operation, kind, index, lookup) for route in hand.routes)
                 if step is not None]
        if not built:   # No hand command, or none this build has: say so, never guess.
            reason = hand.reason or learn_routes.OWN_TOOL
            built = [Step(kind, index, iface_(T_OWN), note=iface_(reason))]
        found.extend(built)
        if len(found) >= MAX_STEPS:
            break
    return tuple(found[:MAX_STEPS])


def _step(route, operation, kind, index, lookup):
    parameters = operation.get("parameters") if isinstance(operation.get("parameters"), dict) else {}
    if route.operator:
        title = lookup.operator_label(route.operator)
        if not title:
            return None   # This build has no such command.
    else:
        title = iface_(route.title)
    item = [(lookup.item_label(route.item) if route.item else "") or title]   # As the menu itself shows it.
    if route.operator and route.choice:
        chosen = lookup.enum_label(route.operator, route.choice, dict(route.properties)[route.choice])
        if chosen:   # Its items are the menu's entries, or the entries of a submenu the menu names.
            item = item + [chosen] if route.submenu else [chosen]
    path, opens = _path(route, item, lookup)
    keys = tuple(lookup.keys(route.operator, route.properties, route.editor, route.mode, free=route.free)) \
        if route.operator else ()
    return Step(kind, index, title, keys, path, opens, _settings(route, parameters, lookup),
                _needs(route, parameters, lookup), iface_(route.differs) if route.differs else "", bool(route.operator))


def _path(route, item, lookup):
    """The breadcrumbs to the command and the key that opens one of its menus; no path when any menu is missing."""
    if not route.menus and not route.tab:
        return (), ()
    labels = [lookup.menu_label(menu) for menu in route.menus]
    if not all(labels):
        return (), ()
    path = [lookup.editor_label(route.editor)]
    if route.tab:
        path.append(lookup.tab_label(route.tab))
    path += labels
    if route.operator:
        path += item
    opens = ()
    for menu, label in zip(reversed(route.menus), reversed(labels)):   # The innermost menu a key opens.
        key = lookup.menu_key(menu, route.editor, route.mode)
        if key:
            opens = (label, key)
            break
    return tuple(text for text in path if text), opens


def _settings(route, parameters, lookup):
    if not route.operator:
        return ()
    values = parameters.get("properties") if isinstance(parameters.get("properties"), dict) else parameters
    found = []
    for parameter, name in route.settings:
        if values.get(parameter) is None:
            continue
        shown = lookup.setting(route.operator, name, values[parameter])
        if shown:
            found.append(tuple(shown))
    return tuple(found)


def _count(parameters, field):
    chosen = parameters.get(field)
    return len(chosen) if isinstance(chosen, list) else 0


def _needs(route, parameters, lookup):
    needs = [lookup.mode_label(route.mode)] if route.mode in learn_routes.MODES else []
    if route.mode == learn_routes.EDIT:
        # Literal plural_ calls: the extractor makes a counted family's plural:<category> entries only from these.
        faces, edges, vertices = (_count(parameters, field) for field in ("face_indices", "edge_indices", "vertex_indices"))
        if faces:
            needs.append(plural_(faces, "{count} face selected", "{count} faces selected"))
        if edges:
            needs.append(plural_(edges, "{count} edge selected", "{count} edges selected"))
        if vertices:
            needs.append(plural_(vertices, "{count} vertex selected", "{count} vertices selected"))
    return tuple(text for text in needs if text)
