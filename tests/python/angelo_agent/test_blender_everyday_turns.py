# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whole turns with the everyday kinds in the compiled app, the model played by the stand-in ``claude`` CLI.

Real default startup and real turns: the typed-plan loop, approvals under Ask
before changes (chosen after checking that a new chat starts in Auto), staging,
verification, commit and Blender's Undo are the app's own; ``fake_provider_cli.play_round`` returns the plans this suite scripts.
No account, network or inference.

One chat, five requests:

* A stone footbridge in six operations: three prototypes, one material stated
  once for all of them (``also``), the four posts as one ``batch`` grid and the
  two rails as a mirrored one. One program, one model round, one Undo step.
* The second edit on what was built, with the handles the next request's
  context gives the model: the shared material darkened for every user
  (``shared: ALL_USERS``) and a Bevel on one member of the batch, which keeps
  sharing its mesh, plus a Mirror using the deck as its origin.
* A Subdivision modifier with render level 5 and adaptive dicing, then its
  render level raised again.
* Three area lights, a physical sky, Cycles settings and clearing that Mirror
  origin in one program.

After each, what the model is sent with the next request is read back from the
stand-in's recorded prompt: the names, handles and verified checks of what the
host committed. Then one Undo step per program returns to the factory scene.

Run through ``run_native_suites.py --gui --include test_blender_everyday_turns``;
check ``everyday-turns.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-everyday-turns-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.

ROUNDS = OUT / "rounds"
ROUNDS.mkdir(exist_ok=True)
fake_provider_cli.sign_in(os.environ["HOME"], "claude")  # An everyday sign-in that already exists.
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_ROUNDS=str(ROUNDS))
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.edit.use_global_undo = True
REPORT = OUT / "everyday-turns.json"
state = {"phase": "boot", "success": False, "checks": {}, "observed": {},
         "fixture": "Stand-in claude CLI playing scripted rounds; no browser, account, network or inference"}
started = time.monotonic()
SESSION = "fixture-everyday-turns"
STONE, DARK = [0.4, 0.4, 0.39, 1], [0.12, 0.12, 0.13, 1]
NAMES = ["Footbridge Deck"] + ["Footbridge Post %d" % index for index in (1, 2, 3, 4)] + [
    "Footbridge Rail 1", "Footbridge Rail 2"]


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view_state():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def actions():
    return [(action, value) for _, action, value in view_state().get("hits", [])]


def click(action, value=None):
    area, region = chat()
    rect = next(r for r, a, v in view_state()["hits"] if a == action and (value is None or v == value))
    x, y = int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    for kind, event_value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=event_value, x=x, y=y)


def send(text):
    area, region = chat()
    area.spaces.active.history[-1].body = text
    x, y = region.x + region.width // 2, region.y + 60
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='RET', value='PRESS', x=x, y=y)
    window().event_simulate(type='RET', value='RELEASE', x=x, y=y)


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_EVERYDAY_TURNS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=60):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def played():
    counter = ROUNDS / "next"
    return int(counter.read_text()) if counter.is_file() else 0


def prompt(index):
    return (ROUNDS / ("prompt-%d.txt" % index)).read_text(encoding="utf-8")


def script(index, plan):
    """The plan the stand-in model returns for the session's next ``index``-th round."""
    (ROUNDS / ("round-%d.json" % index)).write_text(json.dumps([{
        "type": "result", "subtype": "success", "is_error": False, "session_id": SESSION, "duration_ms": 1000,
        "structured_output": plan}]), encoding="utf-8")


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def program(*operations, writes=()):
    """One angelo_scene_program as a model writes it; existing objects are bound at their current revisions."""
    from angelo_agent import scene_queries
    write_set = [{"handle": scene_queries.object_handle(obj), "revision": scene_queries.object_revision(window(), obj)}
                 for obj in writes]
    return {"reply": "", "operations": [{"tool": "angelo_scene_program", "arguments": {
        "mode": "APPLY", "read_set": [], "write_set": write_set, "operations": list(operations)}}]}


def handle(obj):
    from angelo_agent import scene_queries
    return scene_queries.object_handle(obj)


def objects():
    return window().scene.objects


def pending():
    from angelo_agent import ui
    return next((call for call in ui.get_controller().pending.values()
                 if call.name == "angelo_scene_program" and call.approval), None)


def rows(role=None):
    from angelo_agent import ui
    return [m for m in ui.get_controller().state["messages"] if role is None or m["role"] == role]


def summaries():
    return [m["text"] for m in rows("system") if m.get("host_summary")]


def undo():
    with bpy.context.temp_override(window=window()):
        return bpy.ops.ed.undo()


def stone():
    return {slot.material for name in NAMES for slot in objects()[name].material_slots}


def colour(material):
    node = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
    return [round(value, 3) for value in node.inputs["Base Color"].default_value]


def sky():
    world = window().scene.world
    return next((node for node in world.node_tree.nodes if node.type == "TEX_SKY"), None) if world else None


def footbridge_round():
    cube = lambda name, alias, location, scale: op("create_primitive", primitive="CUBE", name=name, alias=alias,
                                                  location=location, scale=scale)
    script(0, program(
        cube("Footbridge Deck", "deck", [0, 4, 0.075], [2, 0.6, 0.075]),
        cube("Footbridge Post 1", "post", [-1.85, 3.5, 0.65], [0.06, 0.06, 0.5]),
        cube("Footbridge Rail 1", "rail", [0, 3.5, 1.19], [2, 0.04, 0.04]),
        op("material_principled", "$deck", name="Grey Stone", inputs={"Base Color": STONE, "Roughness": 0.85},
           also=["$post", "$rail"]),
        op("batch", sources=["$post"], array=[{"step": [3.7, 0, 0], "count": 2}, {"step": [0, 1, 0], "count": 2}]),
        op("batch", sources=["$rail"], array=[{"step": [0, 1, 0], "count": 2}])))


def second_edit_round():
    deck, post = objects()["Footbridge Deck"], objects()["Footbridge Post 2"]
    script(1, program(
        op("material_principled", handle(deck), name="Grey Stone", inputs={"Base Color": DARK, "Roughness": 0.9},
           shared="ALL_USERS"),
        op("modifier_add", handle(post), type="BEVEL", name="Soft Edges", settings={"width": 0.01, "segments": 2}),
        op("modifier_add", handle(post), type="MIRROR", name="Deck Mirror", settings={"mirror_object": handle(deck)}),
        writes=[deck, post]))


def subdivision_round(index, kind, **settings):
    deck = objects()["Footbridge Deck"]
    name = {"name": "Subdivision"} if kind == "modifier_add" else {"modifier": "Subdivision"}
    script(index, program(op(kind, handle(deck), type="SUBSURF", settings=settings, **name), writes=[deck]))


def lighting_round():
    from angelo_agent import scene_settings
    scene = scene_settings.execute(window(), {})
    area = lambda name, location, energy, size: op("light_create", name=name, location=location, look_at=[0, 0, 0],
                                                   settings={"light_type": "AREA", "energy": energy, "size": size})
    post = objects()["Footbridge Post 2"]
    plan = program(area("Key Light", [4, -4, 5], 1000, 2), area("Fill Light", [-2, -6, 2.5], 250, 3),
                   area("Rim Light", [-5, 5, 3.5], 600, 1.5),
                   op("world_configure", scene["handle"], sky={"sun_elevation": 0.0873}),
                   op("property_set", scene["handle"], values={"render.engine": "CYCLES", "cycles.samples": 128,
                                                              "cycles.use_denoising": True}),
                   op("modifier_configure", handle(post), type="MIRROR", modifier="Deck Mirror", settings={"clear": True}),
                   writes=[post])
    plan["operations"][0]["arguments"]["write_set"].append({"handle": scene["handle"], "revision": scene["revision"]})
    script(4, plan)


STEPS = [  # (phase, the request, the round that answers it)
    ("footbridge", "Add a stone footbridge beside the cube: a deck 4 m long and 1.2 m wide, four square posts and two "
     "rails, in a grey stone material.", footbridge_round),
    ("second_edit", "Make the stone darker everywhere, soften the edges of the second post and mirror it using the deck "
     "as the origin.", second_edit_round),
    ("subdivision", "Give the deck a Subdivision modifier: level 1 in the viewport, 5 in renders, adaptive.",
     lambda: subdivision_round(2, "modifier_add", levels=1, render_levels=5, adaptive={"enabled": True, "pixel_size": 1.5})),
    ("subdivision_again", "Raise the deck's render level to 6.",
     lambda: subdivision_round(3, "modifier_configure", levels=1, render_levels=6)),
    ("lighting", "Light it with a key, a fill and a rim area light, a physical sky with the sun 5 degrees up, and "
     "Cycles at 128 samples with denoising. Clear the second post's Mirror origin.", lighting_round),
]


def verify(step, checks, observed):
    """What the scene, the person and the model's next context hold after ``step`` committed."""
    summary = summaries()[-1]
    observed[step + "_summary"] = summary[:1500]
    if step == "footbridge":
        posts = [objects()["Footbridge Post %d" % index] for index in (1, 2, 3, 4)]
        checks["seven_objects_from_six_operations"] = all(name in objects() for name in NAMES)
        checks["the_posts_share_one_mesh_and_everything_one_material"] = (
            len({post.data for post in posts}) == 1 and len(stone()) == 1 and colour(next(iter(stone()))) == [0.4, 0.4, 0.39, 1.0])
        checks["the_posts_stand_on_a_two_by_two_grid"] = sorted(
            (round(post.location.x, 2), round(post.location.y, 2)) for post in posts) == [
                (-1.85, 3.5), (-1.85, 4.5), (1.85, 3.5), (1.85, 4.5)]
        checks["the_host_verified_the_copies"] = summary.startswith("Host-verified result") and "copies on" in summary
        checks["one_model_round_for_the_footbridge"] = played() == 1
    elif step == "second_edit":
        handoff = prompt(1)
        observed["second_request_context_chars"] = len(handoff)
        checks["the_next_request_names_what_was_built"] = all(name in handoff for name in NAMES)
        checks["the_next_request_carries_the_verified_checks"] = "copies" in handoff and "material_assignment" in handoff
        material = next(iter(stone()))
        checks["one_edit_recoloured_every_user"] = len(stone()) == 1 and colour(material) == [0.12, 0.12, 0.13, 1.0]
        checks["the_summary_names_the_users"] = "material 'Grey Stone' edited for all 7 users" in summary
        post = objects()["Footbridge Post 2"]
        checks["one_member_was_bevelled_and_still_shares_its_mesh"] = (
            [modifier.type for modifier in post.modifiers] == ["BEVEL", "MIRROR"] and post.data == objects()["Footbridge Post 1"].data
            and not objects()["Footbridge Post 3"].modifiers)
        checks["mirror_origin_is_the_exact_live_deck"] = post.modifiers["Deck Mirror"].mirror_object == objects()["Footbridge Deck"]
        checks["mirror_identity_was_verified"] = "modifier_values" in summary
    elif step == "subdivision":
        checks["the_model_was_told_the_mirror_identity_was_verified"] = "modifier_values" in prompt(2)
        modifier = objects()["Footbridge Deck"].modifiers["Subdivision"]
        checks["render_level_five_and_adaptive_are_typed"] = (
            (modifier.levels, modifier.render_levels, bool(modifier.use_adaptive_subdivision)) == (1, 5, True)
            and abs(modifier.adaptive_pixel_size - 1.5) < 1e-6)
        checks["the_model_was_told_who_shares_the_material"] = "edited for all 7 users" in prompt(2)
    elif step == "subdivision_again":
        modifier = objects()["Footbridge Deck"].modifiers["Subdivision"]
        checks["the_level_was_raised_again_and_adaptive_kept"] = (
            modifier.render_levels == 6 and bool(modifier.use_adaptive_subdivision)
            and abs(modifier.adaptive_pixel_size - 1.5) < 1e-6)
        checks["an_object_with_render_five_took_another_typed_edit"] = "modifier" in summary
        checks["the_model_was_told_the_subdivision_was_verified"] = "modifier" in prompt(3) and "Footbridge Deck" in prompt(3)
    elif step == "lighting":
        scene = window().scene
        lights = [obj for obj in scene.objects if obj.type == "LIGHT" and obj.data.type == "AREA"]
        checks["three_lights_a_sky_and_cycles_in_one_program"] = (
            len(lights) == 3 and sky() is not None and abs(sky().sun_elevation - 0.0873) < 1e-5
            and scene.render.engine == "CYCLES" and scene.cycles.samples == 128 and scene.cycles.use_denoising)
        checks["the_world_and_settings_were_verified"] = "world_settings" in summary and "property" in summary
        checks["mirror_clear_was_executed_and_verified"] = (
            objects()["Footbridge Post 2"].modifiers["Deck Mirror"].mirror_object is None and "modifier_values" in summary)
        checks["five_model_rounds_for_five_requests"] = played() == 5


def unwind(checks):
    """One Undo step per program, newest first."""
    undo()
    scene = window().scene
    checks["undo_clear_restores_exact_origin"] = scene.objects["Footbridge Post 2"].modifiers["Deck Mirror"].mirror_object == scene.objects["Footbridge Deck"]
    checks["undo_one_removes_the_lighting_only"] = (sky() is None and scene.render.engine != "CYCLES"
                                                    and "Key Light" not in scene.objects and "Footbridge Deck" in scene.objects
                                                    and scene.objects["Footbridge Deck"].modifiers["Subdivision"].render_levels == 6)
    undo()
    checks["undo_two_lowers_the_render_level"] = window().scene.objects["Footbridge Deck"].modifiers["Subdivision"].render_levels == 5
    undo()
    checks["undo_three_removes_the_subdivision"] = not window().scene.objects["Footbridge Deck"].modifiers
    undo()
    checks["undo_four_restores_the_stone_and_the_post"] = (
        len(stone()) == 1 and colour(next(iter(stone()))) == [0.4, 0.4, 0.39, 1.0]
        and not objects()["Footbridge Post 2"].modifiers)
    undo()
    checks["undo_five_is_the_factory_scene"] = sorted(obj.name for obj in window().scene.objects) == ["Camera", "Cube", "Light"]


def tick():
    try:
        assert time.monotonic() - started < 420, "Everyday-turns fixture timed out in " + state["phase"]
        from angelo_agent import director, ui, quick_connect
        checks, phase, observed = state["checks"], state["phase"], state["observed"]
        controller = ui.get_controller()
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not wait(lambda: not ui._authentication.running and ("welcome_connect", "claude_subscription") in actions()):
                return .2
            click("welcome_connect", "claude_subscription")
            state["phase"] = "connected"
            return .3
        if phase == "connected":
            if not wait(lambda: quick_connect.flow("claude_subscription") is None and controller.models and ui.connection_available()):
                return .2
            # A new chat starts in Auto · bounded edits; this suite clicks each program's Approve, so it chooses Ask.
            checks["a_new_chat_starts_in_auto"] = controller.state["options"]["approval"] == "AUTO_BOUNDED"
            controller.set_options(approval="ASK")
            checks["ask_before_changes_is_the_mode"] = controller.state["options"]["approval"] == "ASK"
            state.update(phase="send", step=0)
            return .3
        step = state.get("step", 0)
        if phase == "send":
            name, request, prepare = STEPS[step]
            prepare()
            send(request)
            state["phase"] = "approval"
            return .2
        if phase == "approval":
            call = pending()
            if not wait(lambda: call is not None and call.id != observed.get("last_program") and ("approve", call.id) in actions()):
                return .2
            observed["last_program"] = call.id
            click("approve", call.id)
            state["phase"] = "committed"
            return .3
        if phase == "committed":
            if not wait(lambda: not controller.busy and len(summaries()) == step + 1):
                return .2
            checks[STEPS[step][0] + "_completed"] = controller.status == "Completed"
            verify(STEPS[step][0], checks, observed)
            save()
            if step + 1 < len(STEPS):
                state.update(phase="send", step=step + 1)
                return .3
            unwind(checks)
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
