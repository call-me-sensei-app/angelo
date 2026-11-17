# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Learn mode's hand routes are true in the running app: its commands, menus, settings and the person's keys.

Real default startup, so the person's key configuration is the one Blender builds. No CLI, account, network,
inference or simulated input.

``test_native_learn_routes`` proves the routes against Blender's menu sources. This suite proves what only the app
knows. Covered: every route's operator, every menu class, every choice and every mapped setting exists in this
build and has a label (``learn_resolver.Lookup``); the keys a card would show for the commands a person uses most
are bound in the default key configuration, and each is a key Blender's own lookup
(``KeyConfigurations.find_item_from_operator``, what its menus print) also reports for that command in that mode;
one operator bound to several keys gives each action its own (vertex, edge and face select mode);
a key that opens a menu is found for the Add, Apply, Merge and Delete menus; a command Angelo ran by name ends its
path with the header menu's own text, translated, where that differs from its label; a
rebinding shows at once in a new lookup and after ``FRESH_S`` in a kept card; the Industry Compatible key
configuration gives its own keys, again equal to Blender's own answer; in another interface language the labels are
that language's (recorded as skipped where the build has no translations); a whole card is built from a saved row,
and how long that takes is measured (cold and kept).

Run through ``run_native_suites.py --gui --include test_blender_learn_routes``; check ``learn-routes.json`` for
``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import learn_cards, learn_resolver, learn_routes, product_env  # noqa: E402  (bundled add-on path)
from angelo_agent.learn_routes import EDIT, OBJECT, VIEW_3D  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
from test_native_learn_routes import SAMPLES, all_routes  # noqa: E402  (the same routes the host test proves)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-learn-routes-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "learn-routes.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "measures": {}, "skipped": [], "keys": {}}
started = time.monotonic()

# Commands a person uses most, whose key Blender's default key configuration binds: (kind, parameters).
KEYED = (("mesh_bevel", {}), ("mesh_inset", {}), ("mesh_extrude", {}), ("mesh_separate", {}),
         ("mesh_recalculate_normals", {}), ("transform", {"location": [0, 0, 1]}),
         ("transform", {"rotation": [0, 0, 1]}), ("transform", {"scale": [2, 2, 2]}), ("object_join", {}),
         ("parent_set", {"parent": "x"}), ("object_duplicate", {}), ("object_duplicate", {"linked": True}),
         ("object_delete", {}), ("keyframe_insert", {}), ("visibility_set", {"hidden": True}))
# Commands reached through a menu a key opens in the default key configuration.
MENU_KEYED = (("create_primitive", {"primitive": "CUBE"}), ("transform_apply", {"scale": True}),
              ("mesh_merge_by_distance", {}), ("mesh_delete", {"mode": "FACES"}))
SAMPLE_VALUES = {"BOOLEAN": True, "INT": 3, "FLOAT": 0.5}


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    """The window's 3D view and its main region."""
    from angelo_agent import live_windows
    for area in live_windows.areas(window()):
        if area.type == 'VIEW_3D':
            return area, next(region for region in area.regions if region.type == 'WINDOW')
    raise AssertionError("The default startup has no 3D view")


def routes_of(kind, parameters):
    return learn_routes.hand({"kind": kind, "parameters": parameters}).routes


def blenders_own_key(idname):
    """The key Blender's menus would print for ``idname`` in the 3D view as it is now, or ""."""
    area, region = view3d()
    with bpy.context.temp_override(window=window(), area=area, region=region):
        _keymap, item = bpy.context.window_manager.keyconfigs.find_item_from_operator(idname)
    return item.to_string(compact=True) if item is not None and item.map_type == 'KEYBOARD' else ""


def set_mode(mode):
    area, region = view3d()
    with bpy.context.temp_override(window=window(), area=area, region=region):
        if bpy.context.view_layer.objects.active is None or bpy.context.view_layer.objects.active.type != 'MESH':
            bpy.ops.mesh.primitive_cube_add()
        bpy.ops.object.mode_set(mode=mode)


def everything_exists(checks):
    lookup = learn_resolver.Lookup()
    missing = {"operators": set(), "menus": set(), "choices": set(), "settings": set()}
    count = 0
    for kind, parameters, route in all_routes():
        count += 1
        if route.operator and not lookup.operator_label(route.operator):
            missing["operators"].add(route.operator)
            continue
        missing["menus"] |= {menu for menu in route.menus if not lookup.menu_label(menu)}
        if route.item and not lookup.item_label(route.item):
            missing["menus"].add(route.item)
        if route.operator and route.choice and not lookup.enum_label(route.operator, route.choice,
                                                                     dict(route.properties)[route.choice]):
            missing["choices"].add((route.operator, route.choice, dict(route.properties)[route.choice]))
        if kind == "operator_run":
            continue   # Its settings are whatever the record names; the kernel checked them when it ran.
        rna = lookup._operator(route.operator) if route.operator else None
        for _parameter, name in route.settings:
            prop = rna.properties.get(name) if rna is not None else None
            if prop is None or not lookup.setting(route.operator, name, SAMPLE_VALUES.get(prop.type, 1)):
                missing["settings"].add((route.operator, name))
        for label in (lookup.editor_label(route.editor), lookup.mode_label(route.mode),
                      lookup.tab_label(route.tab) if route.tab else "x"):
            checks["a_place_has_a_label"] = checks.get("a_place_has_a_label", True) and bool(label)
    state["measures"]["routes"] = count
    for name, found in missing.items():
        checks["every_route_has_its_%s_in_this_build" % name] = not found
        if found:
            state["measures"]["missing_" + name] = sorted(map(str, found))
    checks["the_samples_reach_routes"] = count > len(SAMPLES)


def keys_agree(checks, label):
    """Every KEYED command has a key, and it is one Blender's own lookup reports for it in that mode."""
    shown = {}
    for mode in (OBJECT, EDIT):
        set_mode(mode)
        lookup = learn_resolver.Lookup()
        for kind, parameters in KEYED:
            for route in routes_of(kind, parameters):
                if route.mode != mode or route.editor != VIEW_3D:
                    continue
                ours = lookup.keys(route.operator, route.properties, route.editor, route.mode, free=route.free)   # As the card asks.
                every = lookup.keys(route.operator, (), route.editor, route.mode, limit=16)
                theirs = blenders_own_key(route.operator)
                name = "%s:%s" % (kind, route.operator)
                shown[name] = {"shown": list(ours), "blender": theirs}
                checks["%s_has_a_key_in_%s" % (name, label)] = bool(ours)
                checks["%s_is_a_key_blender_reports_in_%s" % (name, label)] = bool(theirs) and theirs in every
    set_mode(OBJECT)
    state["keys"][label] = shown


def keys_by_property(checks):
    """One operator, several keys: the record's own properties pick the key (vertex, edge and face select mode)."""
    set_mode(EDIT)
    try:
        lookup = learn_resolver.Lookup()
        found = {}
        for kind in ("VERT", "EDGE", "FACE"):
            (route,) = routes_of("operator_run", {"operator": "mesh.select_mode", "properties": {"type": kind}})
            found[kind] = lookup.keys(route.operator, route.properties, route.editor, route.mode, limit=16)
        state["keys"]["select_mode"] = {kind: list(keys) for kind, keys in found.items()}
        checks["each_select_mode_has_its_own_key"] = all(found.values()) and len({keys[0] for keys in found.values()}) == 3
        # Shift 1 extends and Ctrl 1 expands the selection: other actions, so not this one's keys.
        checks["a_key_that_also_turns_something_on_is_not_shown"] = all(len(keys) <= 2 for keys in found.values())
        checks["a_select_modes_keys_are_not_anothers"] = not (set(found["VERT"]) & set(found["EDGE"]) & set(found["FACE"]))
    finally:
        set_mode(OBJECT)
    # A key that leaves a setting at a default other than the action's value runs another action, unless the person
    # picks it after the key: Alt P sets no type and asks which clear, so it is the key of Clear and Keep
    # Transformation only where the card leaves the type to that menu.
    lookup = learn_resolver.Lookup()
    wanted = (("type", "CLEAR_KEEP_TRANSFORM"),)
    asked = lookup.keys("object.parent_clear", wanted, VIEW_3D, OBJECT, free=("type",))
    fixed = lookup.keys("object.parent_clear", wanted, VIEW_3D, OBJECT)
    state["keys"]["parent_clear"] = {"asked": list(asked), "fixed": list(fixed)}
    checks["a_key_whose_menu_asks_the_choice_is_shown"] = bool(asked)
    checks["a_key_that_leaves_a_fixed_setting_at_another_default_is_not"] = not fixed


def menu_keys(checks):
    lookup = learn_resolver.Lookup()
    for kind, parameters in MENU_KEYED:
        (route,) = routes_of(kind, parameters)
        found = [lookup.menu_key(menu, route.editor, route.mode) for menu in route.menus]
        state["keys"].setdefault("menus", {})[kind] = found
        checks["a_key_opens_a_menu_of_" + kind] = any(found)


def rebinding(checks):
    """A person's own rebinding: a new lookup shows it at once; a kept card after FRESH_S."""
    (route,) = routes_of("object_join", {})
    row = {"role": "activity", "tool": "angelo_scene_program",
           "arguments": {"operations": [{"kind": "object_join", "parameters": {"sources": ["x"]}}]}}
    before = learn_resolver.card(row)[0].keys
    keymap = bpy.context.window_manager.keyconfigs.user.keymaps["Object Mode"]
    item = next(item for item in keymap.keymap_items if item.idname == route.operator and item.active)
    kept = (item.type, item.ctrl, item.shift, item.alt)
    try:
        item.type, item.ctrl, item.shift, item.alt = 'F7', False, True, True
        fresh = learn_resolver.Lookup().keys(route.operator, (), route.editor, route.mode)
        checks["a_rebinding_shows_at_once_in_a_new_lookup"] = fresh[:1] == (item.to_string(compact=True),) and fresh != before
        checks["a_kept_card_is_reused_within_its_freshness"] = learn_resolver.card(row)[0].keys == before
        fresh_for, learn_resolver.FRESH_S = learn_resolver.FRESH_S, 0.0   # As if its freshness had passed.
        try:
            checks["a_kept_card_follows_the_rebinding_once_stale"] = learn_resolver.card(row)[0].keys == fresh
        finally:
            learn_resolver.FRESH_S = fresh_for
    finally:
        item.type, item.ctrl, item.shift, item.alt = kept
        learn_resolver.forget()
    checks["the_rebinding_is_put_back"] = learn_resolver.card(row)[0].keys == before


def industry_compatible(checks):
    presets = bpy.utils.preset_paths("keyconfig")
    path = next((Path(folder) / "Industry_Compatible.py" for folder in presets
                 if (Path(folder) / "Industry_Compatible.py").exists()), None)
    if path is None:
        state["skipped"].append("Industry Compatible key configuration: this build has no such preset")
        return
    default = bpy.context.window_manager.keyconfigs.active.name
    bpy.ops.preferences.keyconfig_activate(filepath=str(path))
    try:
        checks["the_other_key_configuration_is_active"] = bpy.context.window_manager.keyconfigs.active.name != default
        lookup = learn_resolver.Lookup()
        (copy,) = routes_of("object_duplicate", {})   # A command both key configurations bind, to different keys.
        ours = lookup.keys(copy.operator, (), copy.editor, copy.mode, limit=16)
        theirs = blenders_own_key(copy.operator)
        state["keys"]["industry_compatible"] = {"shown": list(ours), "blender": theirs}
        checks["its_duplicate_key_is_blenders_own_answer"] = bool(theirs) and theirs in ours
        checks["its_duplicate_key_differs_from_the_defaults"] = (
            list(ours[:1]) != state["keys"]["default"]["object_duplicate:object.duplicate_move"]["shown"][:1])
    finally:
        folder = next(Path(folder) for folder in presets if (Path(folder) / "Blender.py").exists())
        bpy.ops.preferences.keyconfig_activate(filepath=str(folder / "Blender.py"))
    checks["the_default_key_configuration_is_back"] = bpy.context.window_manager.keyconfigs.active.name == default


def languages(checks):
    view = bpy.context.preferences.view
    english = learn_resolver.Lookup()
    labels = (english.operator_label("mesh.bevel"), english.menu_label("VIEW3D_MT_object"), english.mode_label(EDIT))
    offered = {item.identifier for item in view.bl_rna.properties["language"].enum_items}
    kept = (view.language, view.use_translate_interface)
    for language in ("ja_JP", "ar_EG"):
        if language not in offered:
            state["skipped"].append("language %s: this build has no translations" % language)
            continue
        try:
            view.language, view.use_translate_interface = language, True
            lookup = learn_resolver.Lookup()
            theirs = (lookup.operator_label("mesh.bevel"), lookup.menu_label("VIEW3D_MT_object"), lookup.mode_label(EDIT))
            state["measures"]["labels_" + language] = list(theirs)
            checks["labels_are_in_" + language] = all(theirs) and theirs != labels
        finally:
            view.language, view.use_translate_interface = kept
    checks["english_labels_are_back"] = (learn_resolver.Lookup().operator_label("mesh.bevel"),) == labels[:1]


def whole_card(checks):
    row = {"role": "activity", "tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
        {"kind": "create_primitive", "parameters": {"primitive": "CYLINDER", "radius": 0.04, "depth": 0.1, "segments": 32}},
        {"kind": "mesh_inset", "parameters": {"face_indices": [30], "thickness": 0.004}},
        {"kind": "mesh_bevel", "parameters": {"edge_indices": [1, 2, 3], "offset": 0.002, "segments": 3}},
        {"kind": "modifier_add", "parameters": {"type": "SUBSURF"}},
        {"kind": "mesh_bend", "parameters": {"rise": 0.1}}]}}
    learn_resolver.forget()
    clock = time.perf_counter()
    card = learn_resolver.card(row)
    cold = time.perf_counter() - clock
    clock = time.perf_counter()
    for _ in range(100):
        learn_resolver.card(row)
    kept = (time.perf_counter() - clock) / 100
    state["measures"]["card_ms"] = {"cold": round(cold * 1000, 2), "kept": round(kept * 1000, 4), "steps": len(card)}
    state["measures"]["card"] = [step.__dict__ for step in card]
    by_kind = {step.kind: step for step in card}
    checks["a_card_has_a_step_per_operation"] = [step.index for step in card] == [0, 1, 2, 3, 4]
    checks["the_add_step_names_the_menu_its_key_opens"] = bool(by_kind["create_primitive"].opens)
    checks["the_add_step_shows_its_settings_with_labels"] = len(by_kind["create_primitive"].settings) == 3
    checks["the_bevel_step_has_its_key_path_and_needs"] = (bool(by_kind["mesh_bevel"].keys)
                                                           and len(by_kind["mesh_bevel"].path) == 3
                                                           and len(by_kind["mesh_bevel"].needs) == 2)
    checks["the_modifier_step_ends_at_its_type"] = len(by_kind["modifier_add"].path) == 5
    checks["a_step_with_no_hand_command_says_so"] = (not by_kind["mesh_bend"].keys and not by_kind["mesh_bend"].path
                                                     and bool(by_kind["mesh_bend"].note))
    checks["a_row_that_is_not_a_typed_edit_has_no_card"] = learn_resolver.card({"tool": "angelo_execute_python",
                                                                                "arguments": {"code": "pass"}}) == ()


def operator_run_card(checks):
    """A command Angelo ran by name is found where the header holds it, under the menu's words, not its label's."""
    lookup = learn_resolver.Lookup()
    row = {"role": "activity", "tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
        {"kind": "operator_run", "parameters": {"operator": "mesh.select_edge_loop_multi"}}]}}
    learn_resolver.forget()
    (step,) = learn_resolver.card(row)
    state["measures"]["operator_run_card"] = step.__dict__
    menus = ("VIEW3D_MT_select_edit_mesh", "VIEW3D_MT_edit_mesh_select_loops")
    checks["an_operator_run_card_names_the_header_menus"] = step.path[1:3] == tuple(map(lookup.menu_label, menus))
    checks["an_operator_run_card_ends_with_the_menus_own_text"] = step.path[-1:] == (lookup.item_label("Edge Loops"),)
    checks["the_menus_text_is_not_the_label"] = step.title == lookup.operator_label("mesh.select_edge_loop_multi") \
        and step.title != step.path[-1]


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_LEARN_ROUTES", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 240, "Fixture timed out in " + state["phase"]
        if state["phase"] == "boot":
            if not qa_boot.started():
                return .2
            state["phase"] = "prove"
            return .3
        checks = state["checks"]
        for name, step in (("exists", everything_exists), ("default_keys", lambda c: keys_agree(c, "default")),
                           ("keys_by_property", keys_by_property), ("menu_keys", menu_keys), ("rebinding", rebinding), ("industry", industry_compatible),
                           ("languages", languages), ("card", whole_card), ("operator_run", operator_run_card)):
            state["phase"] = name
            step(checks)
            save()
        finish()
    except Exception as error:
        finish(error)
    return None


bpy.app.timers.register(tick, first_interval=1.0)
