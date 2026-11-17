# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No key-map item takes an event no key press makes: the input method's composition events (``Event.type`` only).

Angelo exposes ``IME_COMPOSITE_START``, ``IME_COMPOSITE_EVENT`` and ``IME_COMPOSITE_END`` to scripts so the chat
composer can show and commit a composition (``composer_ime``). They are not in the key-map's list
(``rna_enum_event_type_items``): the Preferences key-map editor, ``KeyMapItem.type`` of every map type and
``KeyMap.keymap_items.new`` refuse them, so a person can never bind a key to an event they cannot press. The
window's input method state is read by the composer (``Window.ime_owner`` and the rest) and checked here to exist.

Background, --factory-startup; no window, provider, account or network.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy

assert '--factory-startup' in sys.argv
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-keymap-event-types"))
OUT.mkdir(parents=True, exist_ok=True)
IME = ("IME_COMPOSITE_START", "IME_COMPOSITE_EVENT", "IME_COMPOSITE_END")
report = {"success": False, "checks": {}, "measures": {}}


def check(name, condition, **measures):
    report["checks"][name] = bool(condition)
    if measures:
        report["measures"][name] = measures


def refused(action):
    try:
        action()
    except (TypeError, ValueError):
        return True
    return False


def main():
    listed = set(bpy.types.KeyMapItem.bl_rna.properties["type"].enum_items.keys())
    check("the_key_map_editors_list_has_no_composition_event", not listed.intersection(IME),
          offered=sorted(listed.intersection(IME)))
    keyconfig = bpy.context.window_manager.keyconfigs.new("angelo-ime-check")
    try:
        keymap = keyconfig.keymaps.new("Window")
        for kind in IME:
            check("keymap_items_new_refuses_" + kind.lower(),
                  refused(lambda kind=kind: keymap.keymap_items.new("wm.redraw_timer", kind, 'PRESS')))
        item = keymap.keymap_items.new("wm.redraw_timer", 'A', 'PRESS')
        map_types = [entry.identifier for entry in item.bl_rna.properties["map_type"].enum_items]
        for map_type in map_types:
            item.map_type = map_type
            for kind in IME:

                def bind(kind=kind):
                    item.type = kind
                check("a_%s_item_refuses_%s" % (map_type.lower(), kind.lower()), refused(bind), now=item.type)
        check("every_map_type_was_tried", len(map_types) >= 4, map_types=map_types)
    finally:
        bpy.context.window_manager.keyconfigs.remove(keyconfig)
    window_props = set(bpy.types.Window.bl_rna.properties.keys())
    wanted = {"ime_owner", "is_ime_composing", "ime_composition", "ime_composition_cursor",
              "ime_composition_selection", "ime_result", "ime_caret"}
    check("the_window_reports_its_input_method", wanted <= window_props, missing=sorted(wanted - window_props))
    functions = set(bpy.types.Window.bl_rna.functions.keys())
    check("the_window_has_the_session_calls", {"ime_attach", "ime_detach", "ime_simulate"} <= functions)


try:
    main()
    report["success"] = bool(report["checks"]) and all(report["checks"].values())
except Exception as error:
    report.update(success=False, error=str(error), traceback=traceback.format_exc())
(OUT / "keymap-event-types.json").write_text(json.dumps(report, indent=2))
print("ANGELO_KEYMAP_EVENT_TYPES", json.dumps({"success": report["success"]}), flush=True)
sys.exit(0 if report["success"] else 1)
