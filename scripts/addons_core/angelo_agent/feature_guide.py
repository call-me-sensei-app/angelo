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
from dataclasses import dataclass

DOCUMENT = "features"   # doc/angelo/features.rst: the people's guide to every entry here.


@dataclass(frozen=True)
class Guide:
    id: str
    name: str
    what: str           # What it does, one or two sentences.
    reach: str          # Where the person finds it, by the labels they see (English; relayed in their language).
    agent: str          # How the agent turns it on or uses it.
    menu: tuple = ()    # chat_destinations feature ids that reach it.
    doc: str = ""       # Its contract: doc/angelo/<doc>.rst.
    requires: tuple = ()   # Operator idnames that must be registered for the entry to be offered.
    suggest: str = ""   # When to recommend it unasked.
    heading: str = ""   # Its section in the people's guide (doc/angelo/features.rst); not sent to the model.
    examples: tuple = ()   # Calls as the agent makes them: {"tool", "arguments"} or {"operation"} of a program;
    # test_native_feature_guide validates each with the tool's own validator, so the guide can't teach a refused call.


U = "123e4567-e89b-42d3-a456-426614174000"   # Example ids and handles: placeholders the note says to replace.
H = "scn:" + "a" * 32 + ":1"
EXAMPLES_NOTE = ("Ids and handles in examples are placeholders: connection_id and model from angelo_studio_catalog, "
                 "attachment and asset ids from the chat and angelo_studio_list, handles from the observation.")


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
                                                                 "engine": "UNITY"}}),
          menu=("godot_link",), doc="engine_link", heading="Game engine live link (Godot, Unity, Unreal Engine)",
          suggest="The person works for a game engine (Godot, Unity, Unreal), names one, or the request's engines "
                  "note says one runs while the link is off."),
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
    Guide("bake_maps", "Bake every map at once",
          "Bakes all of a material's maps (colour, roughness, normal...) in one job, packed separately or for glTF, "
          "Unreal or Unity.",
          "Chat bar + menu > Create > Render > Bake maps…; F3.",
          "angelo_job_start kind BAKE_MAPS with the object's handle as target and an output folder (deferred job "
          "tools: its fields come with the lookup).",
          examples=({"tool": "angelo_job_start", "arguments": {"kind": "BAKE_MAPS", "target": H,
                                                               "output": "textures/crate"}},),
          menu=("bake_maps",), doc="long_jobs", heading="Bake every map at once", requires=("angelo.bake_maps",),
          suggest="The person exports to a game engine or the web and the materials use procedural nodes."),
    Guide("render_region", "Region render over the last render",
          "Re-renders only a chosen region over the last finished render, to check a change quickly.",
          "Chat bar + menu > Create > Render > Render region; Render > Render Region Over Last Render; the region "
          "pills in the render window.",
          "angelo_job_start kind RENDER_REGION with region.",
          examples=({"tool": "angelo_job_start", "arguments": {"kind": "RENDER_REGION",
                                                               "region": [0.25, 0.25, 0.75, 0.75]}},),
          menu=("render_region",), doc="render_region", heading="Render a region over the last render", requires=("angelo.render_region",),
          suggest="A small change needs checking in a slow final render."),
    Guide("select_through", "Select Through",
          "Lets box, lasso and circle selection reach hidden elements in solid display, as X-ray does, without "
          "changing the display (mesh Edit Mode, paint masks, Particle Edit).",
          "The 3D view header beside X-ray; Director's top bar in Edit Mode; the shading pie (Z); chat bar + menu > "
          "Inspect > Select Through.",
          "A setting of the person's view: tell them where it is; the agent's own selections never need it.",
          menu=("select_through",), doc="select_through", heading="Select Through"),
    Guide("tension_map", "Tension maps",
          "Shows where a deforming mesh stretches or squashes, as an overlay and as data a material can use.",
          "Chat bar + menu > Inspect > Tension map; Add Modifier > Edit > Tension.",
          "angelo_scene_program modifier_add with type TENSION on the object's handle; the overlay is the person's "
          "view setting.",
          examples=({"operation": {"kind": "modifier_add", "target": H, "parameters": {"type": "TENSION"}}},),
          menu=("tension_map",), doc="tension_map", heading="Tension maps", requires=("angelo.tension_map",),
          suggest="Rigging or cloth work where stretching matters."),
    Guide("uv_checker", "UV checker",
          "Shows a checker pattern on models in the viewport to judge UV stretching and texel density.",
          "Chat bar + menu > Check > UV checker; Director's top bar; View > Toggle UV Checker.",
          "The person's view setting: tell them where it is; angelo_view's UV look shows the agent a layout.",
          menu=("uv_checker",), doc="uv_checker", heading="UV checker", requires=("angelo.uv_checker",),
          suggest="After unwrapping, or before texturing or baking."),
    Guide("web_export", "Web export and live browser preview",
          "A web-ready .glb for Three.js with its size budget, and a browser preview that updates after every "
          "change.",
          "Chat bar + menu > Share > Web export / Live browser preview; File > Export.",
          "angelo_web_check, angelo_web_export, angelo_web_preview (deferred).",
          examples=({"tool": "angelo_web_check", "arguments": {}},),
          menu=("web_export", "web_preview"), doc="web_export", heading="Web export and live browser preview"),
    Guide("generation_flows", "Generation Flows",
          "Node-tree workflows that chain generation steps (image, 3D, upscaling...) and run with one approval.",
          "Chat bar + menu > Create > Generation Flows (New, templates and saved flows).",
          "The angelo_flow_* tools (deferred: angelo_agent_capabilities TOOLS).",
          menu=("flows",), doc="generation_flows", heading="Generation Flows"),
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
    by_id = {guide.id: guide for guide in offered(available_operators)}
    found = [name for name in names if name in by_id]
    entries = []
    for name in found:
        guide = by_id[name]
        entry = {"name": guide.id, "title": guide.name, "what": guide.what, "where": guide.reach,
                 "agent": guide.agent}
        if guide.suggest:
            entry["suggest_when"] = guide.suggest
        if guide.examples:
            entry["examples"], entry["examples_note"] = copy.deepcopy(list(guide.examples)), EXAMPLES_NOTE
        if name == "engine_link":
            tool = APP_FEATURE_TOOLS[0]
            entry["tool"] = {"name": tool["name"], "input_schema": tool["inputSchema"]}
            if engines is not None:
                entry["this_computer"] = engines
        entries.append(entry)
    return {"features": entries}, found
