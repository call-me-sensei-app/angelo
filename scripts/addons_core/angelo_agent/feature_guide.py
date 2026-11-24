# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's major features as the agent learns them: the ``FEATURES`` family of ``angelo_agent_capabilities``.

A person asks "how do I link Unity?" or "can Angelo render with V-Ray?"; the agent answers from here and, where a
feature can be turned on for them, does it with the tool or action the entry names. Each ``Guide`` says what the
feature does, where the person finds it (the chat bar's + menu first: Angelo's main navigation), how the agent turns
it on or uses it, and which document describes it for people (doc/angelo/features.rst) and as a contract.

A lookup round costs nothing until the agent asks: the tool's description names the family in one clause, and no
instruction text lists features. Without names the lookup lists every feature in one line each with this computer's
engine facts; with names it returns those entries in full (``engine_link`` with ``angelo_app_feature``'s exact
schema, so turning it on needs no second lookup).

Text here is model-visible and English (never translated). A feature from a branch that has not merged yet lists
the operator a person's click runs in ``requires``; the entry is offered only where that operator is registered, so
the agent never describes a feature this build lacks. ``test_native_feature_guide`` checks that every entry's menu
ids are registered features of the + menu (``chat_destinations.TOP_LEVEL``) and that its documents exist.
No Blender here; ``capabilities_host`` passes what the build has.
"""

import copy

from .feature_guide_types import H, U, Guide  # noqa: F401  (Guide, H, U: the guide modules' shared names)
from .feature_guide_scene import SCENE_GUIDES

DOCUMENT = "features"   # doc/angelo/features.rst: the people's guide to every entry here.


EXAMPLES_NOTE = ("Ids and handles in examples are placeholders: connection_id and model from angelo_studio_catalog, "
                 "attachment and asset ids from the chat and angelo_studio_list, handles from the observation.")


# The engine link's tools, in its FEATURES entry only where this launch offers them (lookup).
ENGINE_LINK_TOOLS_SENTENCE = (
    "Once it is on: angelo_engine_link_status (read) and angelo_engine_link (deferred): OPEN asks the person on the "
    "engine's own request card to open an asset; PUSH, SAVE and CLOSE (approved) push the live update, save the asset "
    "for re-import and end the link, with each editor's acknowledgement.")


GUIDES = (
    Guide("engine_link", "Game engine live link (Godot, Unity, Unreal Engine)",
          "Edit a game engine's models in Angelo: in the engine, Edit in Angelo on a model opens it here; Godot and "
          "Unity show each change live, and saving here updates the model in the engine (Unreal re-imports it; its "
          "live preview needs the Angelo Link Live source plugin). One link serves all three engines.",
          "Chat bar + menu > Share > Game engine live link: Turn on the live link, and Add Angelo Link to a Godot / "
          "Unity / Unreal Engine project… (copies the plugin and turns the link on). Also File > Export. When an "
          "engine's editor is open, Angelo suggests it once on a card. Then: Godot, enable the plugin in Project "
          "Settings > Plugins; Unreal, enable it in Edit > Plugins and restart; Unity loads it by itself.",
          "angelo_app_feature {feature: ENGINE_LINK, action: ON | OFF | ADD_TO_PROJECT, engine: GODOT | UNITY | "
          "UNREAL (ADD_TO_PROJECT only)}; approved on a card. ADD_TO_PROJECT opens a folder picker the person "
          "answers.",
          examples=({"tool": "angelo_app_feature", "arguments": {"feature": "ENGINE_LINK", "action": "ON"}},
                    {"tool": "angelo_app_feature", "arguments": {"feature": "ENGINE_LINK", "action": "ADD_TO_PROJECT",
                                                                 "engine": "UNITY"}},
                    {"tool": "angelo_engine_link_status", "arguments": {}},   # Only where offered (lookup).
                    {"tool": "angelo_engine_link", "arguments": {"action": "SAVE"}}),
          menu=("godot_link",), doc="engine_link", heading="Game engine live link (Godot, Unity, Unreal Engine)",
          suggest="The person works for a game engine (Godot, Unity, Unreal), names one, or the request's engines "
                  "note says one runs while the link is off."),
    Guide("game_physics", "Game physics (colliders and bodies for game engines)",
          "Marks what an object is to a game engine: a Static, Rigid (with mass), Kinematic or Trigger body and a "
          "collision shape (box, sphere, capsule, cylinder fitted to the mesh or sized by hand, convex hull or exact "
          "mesh; Collision Only exports a mesh only as its shape). Every glTF export writes it as standard OMI "
          "physics, which Godot 4.4+ imports as RigidBody3D, StaticBody3D, AnimatableBody3D, Area3D and "
          "CollisionShape3D nodes, replacing Godot's -col/-colonly/-rigid name suffixes (still available as the "
          "scene's physics format).",
          "Properties > Physics > Game Physics on the active object, or chat bar + menu > Share > Game engine live "
          "link > Game physics for the selected object…; the panel lists its findings (a shape animated inside its "
          "body, -colonly on an empty, a Mesh shape that moves, a body without a shape, a scaled rigid body).",
          "property_set on the object, with the settings path prefix the example shows: body (NONE, STATIC, "
          "RIGID, KINEMATIC, TRIGGER), mass, shape (NONE, BOX, SPHERE, CAPSULE, CYLINDER, CONVEX, TRIMESH), fit "
          "(AUTO fits the shape to the mesh at every export; MANUAL uses size, radius, height (full) and offset in "
          "the object's axes), axis (AUTO longest, X, Y, Z) and collision_only. A shape belongs to the nearest body "
          "at or above its object; keep each shape's object directly below its body or unanimated inside it, "
          "because Godot attaches shapes only to their parent body.",
          examples=({"operation": {"kind": "property_set", "target": H, "parameters": {"values": {
              "angelo_physics.body": "RIGID", "angelo_physics.mass": 5, "angelo_physics.shape": "BOX"}}}},),
          menu=("godot_link",), doc="game_physics", heading="Game physics for game engines",
          suggest="The person prepares a model for a game engine and mentions collision, colliders, physics, "
                  "triggers or Godot's -col suffixes."),
    Guide("media_studio", "Media Studio (AI images and 3D models, TRELLIS 2 included)",
          "Generates images and 3D models from text or reference pictures (3D: Tripo, Meshy, or TRELLIS 2 through "
          "fal from images), keeps them in the person's Media Studio library, and imports results into the scene.",
          "Chat bar + menu > Create > Media Studio (its 3D tab lists TRELLIS 2 once a fal key is connected: AI "
          "Providers > Media generation > fal), or Workflows for saved multi-step workflows; the Studio button beside "
          "Chat in the top bar.",
          "Deferred angelo_studio_* tools (angelo_agent_capabilities TOOLS with their names). angelo_studio_catalog "
          "names each connection_id/model and its options; angelo_studio_generate takes that pair, a prompt, options "
          "and references (image or model by the model chosen; TRELLIS 2 takes images only, up to four views of one "
          "object); angelo_studio_import_3d brings a finished model into the scene; angelo_studio_import_image only "
          "copies a chat attachment into the Studio library. Paid generation is approved with its price.",
          menu=("media_studio", "workflows"), doc="generative_studio",
          suggest="The person wants an asset faster than modelling it, or a 3D model of a picture.",
          heading="Media Studio: AI images and 3D models",
          examples=({"tool": "angelo_studio_catalog", "arguments": {}},
                    {"tool": "angelo_studio_generate", "arguments": {
                        "connection_id": U, "model": "trellis-2", "prompt": "a wooden chair",
                        "references": [{"source": "ATTACHMENT", "id": U}, {"source": "ATTACHMENT", "id": U}]}},
                    {"tool": "angelo_studio_import_3d", "arguments": {"asset_id": U}})),
    Guide("video", "Video generation (fal models, Seedance on BytePlus)",
          "Makes a video clip from a prompt, a still, two frames or a reference video, with fal's video models or "
          "Seedance through the person's BytePlus ModelArk key (about half fal's price), priced per second.",
          "Chat bar + menu > Create > Video > Video (Media Studio's Video tab). A key first: AI Providers > Media "
          "generation > fal, or AI Providers > BytePlus ModelArk for Seedance; each model picker names its provider "
          "and price.",
          "angelo_studio_generate with a video model's connection_id/model from angelo_studio_catalog (the model "
          "decides that it is a video), its duration and audio in options, and frames [first, last] or references; "
          "billed per second and approved with its price. Deferred studio tools.",
          menu=("video",), doc="generative_studio", heading="Video generation",
          suggest="The person wants motion: a turntable, a shot, an animated idea.",
          examples=({"tool": "angelo_studio_generate", "arguments": {
              "connection_id": U, "model": "seedance-2.0-fast", "prompt": "a slow turntable of the chair",
              "options": {"duration": 5, "audio": False}, "frames": [{"source": "ASSET", "id": U}]}},)),
    Guide("photo_to_3d", "Photo to 3D model",
          "Turns a photo into a 3D model placed in the scene.",
          "Drag a photo from Finder or Explorer onto the 3D view and choose Make a 3D model on the card; or chat bar "
          "+ menu > Create > Photo to 3D model… (placed at the 3D cursor).",
          "With the photo attached to the chat: angelo_studio_generate with a 3D model and the photo in references "
          "(source ATTACHMENT), then angelo_studio_import_3d; or tell the person to drop it on the 3D view.",
          menu=("photo_to_3d",), doc="photo_drop", heading="Photo to 3D model",
          examples=({"tool": "angelo_studio_generate", "arguments": {
              "connection_id": U, "model": "trellis-2", "prompt": "the object in the photo",
              "references": [{"source": "ATTACHMENT", "id": U}]}},),
          suggest="The person has a photo or concept picture of an object they want in 3D."),
    Guide("render_engine", "Render engine switcher (EEVEE, Cycles Preview / Final)",
          "Chooses the engine F12, Render Animation and render jobs use; Cycles' Preview and Final set its samples "
          "and denoising and use the GPU.",
          "Director's top bar engine button; chat bar + menu > Create > Render Engine; Render > Render Engine.",
          "angelo_scene_program property_set on the scene handle (scene_settings.handle) with render.engine "
          "(BLENDER_EEVEE, CYCLES); Cycles samples and device in the same values. angelo_scene_settings reads it.",
          menu=("render_engine",), doc="render_engines", heading="Render engines and AI render",
          examples=({"operation": {"kind": "property_set", "target": H, "parameters": {
              "values": {"render.engine": "CYCLES", "cycles.samples": 128}}}},),
          suggest="A render looks noisy or slow, or the person wants a final-quality picture."),
    Guide("installed_engines", "Installed render engines (V-Ray and other add-on engines)",
          "Lists render engine add-ons installed in Blender (V-Ray and others) in the switcher and sets them up in "
          "Angelo, including the vendor's sign-in.",
          "Director's engine button or Render > Render Engine: an installed engine not on yet says so; choose it to "
          "set it up.",
          "angelo_scene_settings lists installed_engines; set one with property_set render.engine once it is set "
          "up (Auto asks first). Setting one up is the person's click in the switcher.",
          menu=("render_engine",), doc="render_engines", heading="Render engines and AI render", requires=("angelo.render_engine_setup",)),
    Guide("ai_render", "AI render",
          "Restyles the scene camera's view into a finished picture with a Generation Flow, priced in one click.",
          "Chat bar + menu > Create > Render > AI render; Render > AI Render…; the engine button's menu.",
          "Tell the person where it is; its run is the person's priced click.",
          menu=("ai_render",), doc="render_engines", heading="Render engines and AI render"),
    Guide("camera_to_video", "Camera to video",
          "Makes a video from the scene camera: Make Video from This Shot (AI video from rendered frames) and Video "
          "from This Camera Move; renders can also write MP4.",
          "Chat bar + menu > Create > Video > Make Video from This Shot / Video from This Camera Move; the Render "
          "menu; View > Cameras.",
          "angelo_job_start RENDER_ANIMATION with format MP4 for a rendered video; angelo_studio_generate with frames "
          "for an AI video from the shot.",
          examples=({"tool": "angelo_job_start", "arguments": {"kind": "RENDER_ANIMATION", "output": "renders/shot",
                                                               "format": "MP4"}},),
          menu=("shot_to_video", "shot_motion"), doc="long_jobs", heading="Camera to video", requires=("angelo.shot_to_video",)),
    Guide("library", "Angelo Library",
          "Ready-made models, materials and HDRIs to search and add to the scene.",
          "The search pill at the top centre of the 3D view in Object Mode; chat bar + menu > Create > Angelo "
          "Library; Browse Library Materials / HDRIs in the Material and World properties.",
          "angelo_library_search, then angelo_library_import (deferred: angelo_agent_capabilities TOOLS).",
          examples=({"tool": "angelo_library_search", "arguments": {"query": "chair", "limit": 20}},
                    {"tool": "angelo_library_import", "arguments": {"item_id": "tl-chair-01"}}),
          menu=("library",), doc="library", heading="Angelo Library", requires=("angelo.library_open",),
          suggest="Before building or generating a common prop, material or sky from scratch."),
    Guide("download", "Files from the web",
          "Brings one file from an https link (a texture, model, HDRI, image or sound) into the project's "
          "assets/downloads folder (an unsaved file's own temporary folder until it is saved) after the person approves it on a card; its type is checked from its bytes and it "
          "is never opened, run or imported by the download.",
          "Ask in the chat with the link; a card names the website, the size limit and the folder. Free ready-made "
          "assets: chat bar + menu > Create > Angelo Library.",
          "angelo_download (deferred; a card in Ask and in Auto), then import the returned path. Agent scripts have "
          "no network: never fetch a URL in a script (urllib, requests, sockets); it fails.",
          examples=({"tool": "angelo_download", "arguments": {"url": "https://example.com/textures/brick_4k.png",
                                                               "kind": "texture", "name": "Brick wall"}},),
          menu=("library",), doc="downloads", heading="Files from the web",
          suggest="The person gives a link to a file, or a script would otherwise download something."),
    Guide("markup", "Mark up a picture",
          "The person draws on a picture (pen, arrows, ellipses, rectangles, text) and pins numbered comments on "
          "points or regions, then sends it with instructions. The same viewer crops or scales it, as a new picture.",
          "Click a picture in the chat's message box, or chat bar + menu > Mark up a picture... (the newest picture, "
          "or the 3D view as it is now).",
          "angelo_app_feature {feature: MARKUP, action: OPEN, reference_id?} opens the viewer for the person on a "
          "picture of this chat (its reference_id) or, without one, on the 3D view as it is now, after its card; "
          "the person draws and sends, the agent never draws. A marked-up picture arrives as two images, marked then "
          "original, and a block headed 'Pictures the person marked up for you' whose comment numbers are the numbers "
          "on the pins and whose positions are the original's pixels.",
          examples=({"tool": "angelo_app_feature", "arguments": {"feature": "MARKUP", "action": "OPEN"}},
                    {"tool": "angelo_app_feature", "arguments": {"feature": "MARKUP", "action": "OPEN",
                                                                 "reference_id": U}}),
          menu=("markup",), doc="attachment_markup", heading="Mark up a picture", requires=("angelo.markup_picture",),
          suggest="Words alone leave it unclear which part of a picture or the 3D view the person means."),
    *SCENE_GUIDES,   # Scene, modelling, UV and rigging tools (feature_guide_scene.py), in this place of the order.
    Guide("web_export", "Web export and live browser preview",
          "A web-ready .glb for Three.js with its size budget, and a browser preview that updates after every "
          "change.",
          "Chat bar + menu > Share > Web export / Live browser preview; File > Export.",
          "angelo_web_check, angelo_web_export, angelo_web_preview (deferred).",
          examples=({"tool": "angelo_web_check", "arguments": {}},),
          menu=("web_export", "web_preview"), doc="web_export", heading="Web export and live browser preview"),
    Guide("rules", "Rules: standing instructions",
          "Instructions the person writes once and Angelo follows in every request: for this project (kept in the "
          "file) or for all projects (kept on this computer). A file's rules from someone else wait for the person's "
          "review.",
          "Chat bar + menu > Rules… (footer): This project, All projects, and Scripts (scripts always run in this "
          "project, with Remove).",
          "None: rules are only ever the person's own words; the agent never writes or changes them. Suggest Rules… "
          "when the person repeats the same instruction across requests.",
          menu=("rules",), doc="agent_chat", requires=("angelo.chat_rules",), heading="Rules",
          suggest="The person states the same convention again (naming, units, a polycount budget, a style)."),
    Guide("context_folder", "Folder as context",
          "Lets Angelo use up to five folders of the person's (a game project, references) in one chat, never seeing "
          "their paths: it reads them all, and writes text files (notes, checklists, plans) only in the working "
          "folder; it never deletes, moves or renames, and asks before changing a file that was already there.",
          "Chat bar + menu > Folder as context… (footer); a card per folder names it and its role, with Make working "
          "folder and Remove. The first folder attached is the working folder.",
          "angelo_context_folder {action: LIST | READ | SEARCH | VIEW, folder, path, query} and "
          "angelo_context_folder_write {action: CREATE | UPDATE, path, content} (both deferred), only in a chat whose "
          "person attached folders (the observation's context_folders names each, with its role).",
          examples=({"tool": "angelo_context_folder", "arguments": {"action": "LIST"}},
                    {"tool": "angelo_context_folder", "arguments": {"action": "SEARCH", "query": "player"}}),
          menu=("context_folder",), doc="agent_chat", requires=("angelo.context_folder",), heading="Folder as context",
          suggest="The person mentions files on their computer (a game project, a folder of references)."),
    Guide("generation_flows", "Generation Flows",
          "Node-tree workflows that chain generation steps (image, 3D, upscaling...) and run with one approval.",
          "Chat bar + menu > Create > Generation Flows (New, templates and saved flows).",
          "The angelo_flow_* tools (deferred: angelo_agent_capabilities TOOLS).",
          menu=("flows",), doc="generation_flows", heading="Generation Flows"),
    Guide("goal", "Goal: keep going until it's done",
          "Angelo holds a goal across turns: after each turn it measures it (an outline match against an attached "
          "picture from one camera, scene checks, or the agent's own judgment) and continues the chat with the "
          "result until it passes, stops making progress or its working-time or spend budget runs out.",
          "Chat bar + menu > Goal (beside Attach): a goal chip in the composer, started by the message it goes "
          "with; a click on the chip opens its card (threshold, budget, rounds), and its close glyph is Stop goal.",
          "When the person asks for work to continue until a condition holds, angelo_goal (deferred) holds the goal "
          "and states how it is measured.",
          menu=("goal",), doc="goal_mode", heading="Goal mode",
          examples=({"tool": "angelo_goal", "arguments": {"action": "SET", "objective": "Keep refining the model "
                                                          "until its outline matches the attached picture"}},
                    {"tool": "angelo_goal", "arguments": {"action": "STOP", "reason": "NEEDS_INPUT",
                                                          "message": "Which of the two styles in the brief comes first?"}})),
    Guide("plan", "Plan first: research and compare approaches before building",
          "The chat becomes read only and plans: the agent researches (the web, the scene, views), asks what it "
          "needs, and ends with a plan card of up to three approaches, each with steps, risks and an estimate, its "
          "recommendation and open questions. This chat's agent changes nothing until the person presses Go with this on "
          "an approach, which switches the chat to the approval setting the card shows and sends that approach.",
          "Chat bar + menu > Plan first (beside Attach and Goal); or the person says they want to plan first. Edit on "
          "an approach copies it into the message box to change before Go; the Planning chip's close stops planning.",
          "When the person asks to plan, research or discuss approaches before building, angelo_plan (deferred) "
          "START makes the chat read only; PROPOSE ends the planning turn with the plan the person chooses from.",
          menu=("plan",), doc="plan_mode", heading="Plan mode",
          examples=({"tool": "angelo_plan", "arguments": {"action": "START"}},
                    {"tool": "angelo_plan", "arguments": {
                        "action": "PROPOSE", "summary": "Two ways to make the shelf, by how often it will change.",
                        "approaches": [{"title": "One mesh with modifiers", "steps": ["Model one board",
                                                                                   "Array and bevel it"],
                                        "risks": ["Harder to move single boards"], "estimate": "a few minutes"},
                                       {"title": "Separate boards", "steps": ["Make each board its own object"],
                                        "estimate": "about ten minutes"}],
                        "recommended": 0, "questions": ["Will it be exported to a game engine?"]}})),
    Guide("learn", "Learn: how to do a step yourself",
          "Shows how a person does by hand what Angelo's typed steps did, once the person turns it on for a chat; "
          "it is off by default and then shows nothing. With it on, the steps of the latest request open with a card: "
          "the command, the keys bound to it, where it is in the menus, the settings Angelo used and the mode it "
          "needs; opening an earlier step shows its card too. Angelo reads these from the running app, so they are "
          "the person's own keymap and interface language, which may differ from Blender's defaults.",
          "Chat bar + menu > Learn (at the bottom of the menu) > Teach me as you work, for that chat.",
          "No tool: it is the person's own view setting, and Angelo builds each card from a step already made with "
          "angelo_scene_program. A scripted step has no card. Nothing here changes what the agent is sent.",
          menu=("learn",), doc="learn_mode", heading="Learn: how to do it yourself", requires=("angelo.learn",),
          suggest="When the person asks how to do by hand something Angelo did or can do."),
    Guide("base_meshes", "Base meshes and hands",
          "Start from one of Angelo's base meshes, or have the agent start from a rig's hands with Angelo's hand "
          "tools.",
          "Chat bar + menu > Start from > Base meshes (pick one; it is added when the message is sent) or Work on "
          "hands.",
          "Chosen by the person in the menu; the request then carries what was added (hands: the inspection). "
          "The hand tools are angelo_rig_hand_* (deferred).",
          menu=("base_mesh", "hands"), doc="hand_rigging", heading="Base meshes and hands"),
    Guide("feedback", "Send feedback (bug reports and feature requests)",
          "Opens Angelo's feedback form on angelo.studio with the person's Angelo version, build and system (and, "
          "for a bug, graphics card) already filled in. They see and can edit every detail, and nothing is sent "
          "until they send the form; it never includes their scene, files or paths.",
          "Chat bar + menu > Feedback > Report a bug… / Request a feature…; Help > Report a Bug / Request a "
          "Feature; or angelo.studio/feedback in a browser.",
          "Tell the person where it is; sending is theirs, never the agent's. Offer to help them word what "
          "happened, what they expected and the steps.",
          menu=("report_bug", "request_feature"), doc="app_identity", heading="Send feedback",
          suggest="The person hits a bug or a missing feature in Angelo itself (not in their scene), or asks how "
                  "to report one or ask for one."),
)


def offered(available_operators=None):
    """The entries this build has: all of them, or only those whose ``requires`` are in ``available_operators``."""
    if available_operators is None:
        return GUIDES
    return tuple(guide for guide in GUIDES if set(guide.requires) <= set(available_operators))


def index(available_operators=None, engines=None):
    """The ``FEATURES`` family without names: one line per feature, and what runs on this computer."""
    result = {"features": [{"name": guide.id, "title": guide.name, "what": guide.what}
                           for guide in offered(available_operators)],
              "next": "Name features for where the person finds each, how to turn it on and when to suggest it."}
    if engines is not None:
        result["engines"] = engines
    return result


def lookup(names, available_operators=None, engines=None):
    """(result, found) for the named features; ``engines`` are this computer's engine facts (names only)."""
    from .app_feature_spec import APP_FEATURE_TOOLS
    from .engine_link_tool_spec import ENGINE_LINK_NAMES
    from .tool_specs import TOOL_MAP
    by_id = {guide.id: guide for guide in offered(available_operators)}
    found = [name for name in names if name in by_id]
    entries = []
    for name in found:
        guide = by_id[name]
        entry = {"name": guide.id, "title": guide.name, "what": guide.what, "where": guide.reach,
                 "agent": guide.agent}
        if guide.suggest:
            entry["suggest_when"] = guide.suggest
        if guide.examples:   # Never an example of a tool this launch doesn't offer (tool_specs.WITHHELD_TOOLS).
            entry["examples"] = [item for item in copy.deepcopy(list(guide.examples))
                                 if "tool" not in item or item["tool"] in TOOL_MAP]
            entry["examples_note"] = EXAMPLES_NOTE
        tool = APP_FEATURE_TOOLS[0]   # Every feature taught through it gets the deferred tool's schema with it.
        if name == "engine_link" or any(item.get("tool") == tool["name"] for item in guide.examples):
            entry["tool"] = {"name": tool["name"], "input_schema": tool["inputSchema"]}
        if name == "engine_link":
            if ENGINE_LINK_NAMES <= set(TOOL_MAP):
                entry["agent"] += " " + ENGINE_LINK_TOOLS_SENTENCE
            if engines is not None:
                entry["this_computer"] = engines
        entries.append(entry)
    return {"features": entries}, found
