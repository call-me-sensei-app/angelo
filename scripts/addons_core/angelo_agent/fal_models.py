# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The fal video models Angelo offers: one capability table and one input adapter. No Blender dependency.

fal gives every model the same queue API (``fal_queue``) but its own input schema, and the schemas disagree on
field names and types: Seedance's duration is a string enum ("5"), Veo's a string with a unit ("8s"), MiniMax
H3 Max's a float, Wan's an integer; the first frame is ``image_url`` on most image-to-video endpoints but
``start_image_url`` on Kling 3.0 and Wan, and Veo has a separate first-last-frame endpoint. This module is the
only place those differences live. Everything above it (Media Studio's Video tab, Generation Flows' Video step,
the agent's ``angelo_studio_generate`` kind VIDEO and the price table) speaks Angelo's canonical request:

``{"duration": int seconds, "resolution": a value of the model's list, "aspect": a value of the mode's list,
"audio": bool}`` plus inputs ``first`` and ``last`` (frame image URLs) or ``references`` (image URLs).

Every row was read from ``https://fal.ai/models/<endpoint>/llms.txt`` on ``SCHEMAS_READ`` (fixtures:
``tests/python/angelo_agent/fixtures/fal_video/``, the queue OpenAPI documents). A value a schema doesn't list is
refused here, before anything is uploaded or billed. Angelo always sends an explicit duration (never "auto"), so
the price shown before Generate (``flow_prices``) is known.

Modes: ``text`` (no image), ``image`` (a first frame; on most models also an optional last frame) and
``reference`` (subject/style images). Image modes send no aspect ratio: every model's image-to-video endpoint
follows the frame's own shape by default.
"""

from .ui_text import n_

SCHEMAS_READ = "2026-10-30"

TEXT, IMAGE, REFERENCE = "text", "image", "reference"
MODES = (TEXT, IMAGE, REFERENCE)
MAX_PROMPT = 5000   # The shortest documented prompt limit among these models is above this (Kling: 2,500 per shot is
#                     multi_prompt's; plain prompt has none listed). Studio's own field allows 16,000.
_SEEDANCE_ASPECTS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
_H3_ASPECTS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
_WAN_ASPECTS = ("16:9", "4:3", "1:1", "3:4", "9:16")
_VEO_ASPECTS = ("16:9", "9:16")
_KLING_ASPECTS = ("16:9", "9:16", "1:1")


def _refusal(template, **fields):
    """A ``StudioValueError`` (imported here: ``provider_registry`` reads this table, and ``studio_state`` imports it)."""
    from .studio_state import StudioValueError
    from .refusal_codes import coded
    return coded(StudioValueError(template, **fields), "VALIDATION")   # A model's option refused: nothing ran.


def _seconds(low, high):
    return tuple(range(low, high + 1))


def _seedance(app, resolutions, longest, images):
    return {"app": app, "durations": _seconds(4, longest), "duration_format": "string", "resolutions": resolutions,
            "audio_field": "generate_audio", "endpoints": {TEXT: app + "/text-to-video", IMAGE: app + "/image-to-video",
                                                           REFERENCE: app + "/reference-to-video"},
            "aspects": {TEXT: _SEEDANCE_ASPECTS, REFERENCE: _SEEDANCE_ASPECTS},
            "frames": ("image_url", "end_image_url"), "references": ("image_urls", images),
            "reference_videos": ("video_urls", 10 if images > 9 else 3),
            "prompt_required": {TEXT: True, IMAGE: True, REFERENCE: True}}


def _h3(app):
    # H3 Max has no audio switch: it is MiniMax's choice (unconfirmed until a real call; plan "Not confirmed").
    # prompt_expansion_mode is REQUIRED by its schema; "balanced" is its documented default.
    return {"app": app, "durations": _seconds(1, 15), "duration_format": "float", "resolutions": ("480P", "768P", "1080P"),
            "audio_field": None, "endpoints": {TEXT: app + "/text-to-video", IMAGE: app + "/image-to-video",
                                               REFERENCE: app + "/reference-to-video"},
            "aspects": {TEXT: _H3_ASPECTS, REFERENCE: _H3_ASPECTS},
            "frames": ("image_url", "end_image_url"), "references": ("reference_image_urls", 12),
            "reference_videos": ("reference_video_urls", 3), "fixed": {"prompt_expansion_mode": "balanced"},
            "prompt_required": {TEXT: True, IMAGE: True, REFERENCE: True}}


def _veo(app):
    # Veo's image-to-video takes one frame; first + last goes to its own first-last-frame endpoint.
    # Its reference endpoint documents no image count; 3 is Veo's own reference limit (Google's Veo 3.1 docs).
    return {"app": "fal-ai/veo3.1", "durations": (4, 6, 8), "duration_format": "seconds_suffix",
            "resolutions": ("720p", "1080p", "4k"), "audio_field": "generate_audio",
            "endpoints": {TEXT: app, IMAGE: app + "/image-to-video", "first_last": app + "/first-last-frame-to-video",
                          REFERENCE: app + "/reference-to-video"},
            "aspects": {TEXT: _VEO_ASPECTS, REFERENCE: _VEO_ASPECTS},
            "frames": ("image_url", None), "first_last_frames": ("first_frame_url", "last_frame_url"),
            "references": ("image_urls", 3),
            "prompt_required": {TEXT: True, IMAGE: True, REFERENCE: True}}


def _kling(path, first_field, reference):
    # No resolution control on Kling. Kling 3.0 has no reference endpoint on fal (404, 2026-10-30).
    endpoints = {TEXT: path + "/text-to-video", IMAGE: path + "/image-to-video"}
    if reference:
        endpoints[REFERENCE] = path + "/reference-to-video"
    return {"app": "fal-ai/kling-video", "durations": _seconds(3, 15), "duration_format": "string", "resolutions": (),
            "audio_field": "generate_audio", "endpoints": endpoints,
            "aspects": {TEXT: _KLING_ASPECTS, REFERENCE: _KLING_ASPECTS},
            "frames": (first_field, "end_image_url"), "references": ("image_urls", 4) if reference else None,
            "prompt_required": {TEXT: True, IMAGE: False, REFERENCE: True}}


MODELS = {
    "seedance-2.0": dict(_seedance("bytedance/seedance-2.0", ("480p", "720p", "1080p", "4k"), 15, 9), name="Seedance 2.0"),
    "seedance-2.0-fast": dict(_seedance("bytedance/seedance-2.0/fast", ("480p", "720p"), 15, 9), name="Seedance 2.0 Fast",
                              app="bytedance/seedance-2.0"),
    "seedance-2.5": dict(_seedance("bytedance/seedance-2.5", ("480p", "720p", "1080p"), 30, 30), name="Seedance 2.5",
                         prompt_required={TEXT: True, IMAGE: False, REFERENCE: False}),
    "h3-max": dict(_h3("minimax/h3-max"), name="MiniMax H3 Max"),
    "h3-max-turbo": dict(_h3("minimax/h3-max-turbo"), name="MiniMax H3 Max Turbo"),
    "veo-3.1": dict(_veo("fal-ai/veo3.1"), name="Veo 3.1"),
    "veo-3.1-fast": dict(_veo("fal-ai/veo3.1/fast"), name="Veo 3.1 Fast"),
    "kling-3.0-pro": dict(_kling("fal-ai/kling-video/v3/pro", "start_image_url", False), name="Kling 3.0 Pro"),
    "kling-3.0-standard": dict(_kling("fal-ai/kling-video/v3/standard", "start_image_url", False), name="Kling 3.0 Standard"),
    "kling-o3-pro": dict(_kling("fal-ai/kling-video/o3/pro", "image_url", True), name="Kling O3 Pro"),
    "kling-o3-standard": dict(_kling("fal-ai/kling-video/o3/standard", "image_url", True), name="Kling O3 Standard"),
    "wan-3.0": {"name": "Wan 3.0", "app": "alibaba/wan-3.0", "durations": _seconds(2, 30), "duration_format": "integer",
                "resolutions": ("480p", "720p", "1080p"), "audio_field": "audio",
                "endpoints": {TEXT: "alibaba/wan-3.0/text-to-video", IMAGE: "alibaba/wan-3.0/image-to-video",
                              REFERENCE: "alibaba/wan-3.0/reference-to-video"},
                "aspects": {TEXT: _WAN_ASPECTS, REFERENCE: _WAN_ASPECTS},
                "frames": ("start_image_url", "end_image_url"), "references": ("reference_image_urls", 10),
                "reference_videos": ("reference_video_urls", 5),
                "prompt_required": {TEXT: True, IMAGE: False, REFERENCE: False}},
}
DEFAULT_MODEL = "seedance-2.0-fast"   # The cheapest Seedance at 720p; a person picks any other in the model menu.
# Every (owner, app) whose request URLs fal returns (status, result, cancel live under the app, not the endpoint).
APPS = frozenset(spec["app"] for spec in MODELS.values())
ENDPOINTS = frozenset(e for spec in MODELS.values() for e in spec["endpoints"].values())


def spec(model):
    """A video model's capability entry: fal's (``MODELS``) or BytePlus's (``byteplus_models.MODELS``, same shape)."""
    from . import byteplus_models
    found = MODELS.get(model) or byteplus_models.MODELS.get(model)
    if found is None:
        raise _refusal(n_("Choose a supported video model"))
    return found


def is_video_model(model):
    from . import byteplus_models
    return model in MODELS or model in byteplus_models.MODELS


MAX_REFERENCE_VIDEOS = 1   # Angelo sends at most one (a shot's camera move); the table keeps each model's own limit.


def mode_for(first=False, last=False, references=0, videos=0):
    """``references`` and ``videos``: reference images and reference videos (both reference mode)."""
    references += videos
    if references and (first or last):
        raise _refusal(n_("Use start/end frames or reference images, not both"))
    if last and not first:
        raise _refusal(n_("Add a first frame before a last frame"))
    return REFERENCE if references else IMAGE if first else TEXT


def capabilities(model, mode=TEXT):
    """What the Video tab and the agent may offer for ``model`` in ``mode``; ``None`` when the mode is unsupported."""
    entry = spec(model)
    if mode not in entry["endpoints"]:
        return None
    references = entry["references"]
    return {"durations": entry["durations"], "resolutions": entry["resolutions"],
            "aspects": entry["aspects"].get(mode, ()), "audio": entry["audio_field"] is not None,
            "last_frame": mode == IMAGE and (entry["frames"][1] is not None or "first_last" in entry["endpoints"]),
            "references": min(4, references[1]) if references and mode == REFERENCE else 0,
            "reference_videos": min(MAX_REFERENCE_VIDEOS, entry["reference_videos"][1])
            if entry.get("reference_videos") and mode == REFERENCE else 0,
            "reference_mode": REFERENCE in entry["endpoints"],
            "prompt_required": entry["prompt_required"][mode]}


def defaults(model, mode=TEXT):
    """Canonical options a fresh Video tab (or an agent call that names none) uses for ``model``."""
    found = capabilities(model, mode) or capabilities(model, TEXT)
    durations, resolutions, aspects = found["durations"], found["resolutions"], found["aspects"]
    return {"duration": 5 if 5 in durations else durations[0],
            "resolution": next((r for r in ("720p", "768P") if r in resolutions), resolutions[0] if resolutions else ""),
            # Audio off by default: a shot blocked in 3D usually gets its sound in the Sequencer, and Veo and Kling bill
            # audio extra. The person (or the agent) turns it on per generation.
            "aspect": "16:9" if "16:9" in aspects else (aspects[0] if aspects else ""), "audio": False}


def checked_options(model, mode, options):
    """``options`` limited to what ``model`` takes in ``mode``, or a StudioValueError naming the first mismatch."""
    found = capabilities(model, mode)
    if found is None:
        raise _refusal(n_("This model can't use reference images; choose another model or use frames"))
    merged = dict(defaults(model, mode), **{k: v for k, v in options.items() if v not in (None, "")})
    duration = merged["duration"]
    if type(duration) is not int or duration not in found["durations"]:
        raise _refusal(n_("Choose a length this model supports: {low} to {high} seconds"),
                               low=found["durations"][0], high=found["durations"][-1])
    if found["resolutions"] and merged["resolution"] not in found["resolutions"]:
        raise _refusal(n_("This model doesn't offer that resolution"))
    if found["aspects"] and merged["aspect"] not in found["aspects"]:
        raise _refusal(n_("This model doesn't offer that aspect ratio"))
    if type(merged["audio"]) is not bool:
        raise _refusal(n_("Audio is on or off"))
    return {"duration": duration, "resolution": merged["resolution"] if found["resolutions"] else "",
            "aspect": merged["aspect"] if found["aspects"] else "",
            "audio": merged["audio"] if found["audio"] else False}


def fitted_options(model, mode, options):
    """``options`` without the values ``model`` doesn't take in ``mode`` (they fall back to its defaults), as a flow
    step shows them after its model changed; never invents a value. An unknown model keeps them all."""
    found = capabilities(model, mode) if is_video_model(model) else None
    if found is None:
        return dict(options)
    allowed = {"duration": found["durations"], "resolution": found["resolutions"], "aspect": found["aspects"]}
    kept = {}
    for key, value in options.items():
        if key == "audio":
            if type(value) is bool and found["audio"]:
                kept[key] = value
        elif key not in allowed or (type(value) is not bool and value in allowed[key]):
            kept[key] = value
    return kept


def _duration(entry, seconds):
    form = entry["duration_format"]
    return {"string": str(seconds), "seconds_suffix": f"{seconds}s", "float": float(seconds), "integer": seconds}[form]


def request(model, prompt, options, first="", last="", references=(), videos=()):
    """The fal endpoint id and input body for one generation. Inputs are already-uploaded HTTPS URLs."""
    if model not in MODELS:
        raise _refusal(n_("Choose a supported video model"))
    entry = spec(model)
    mode = mode_for(bool(first), bool(last), len(references), len(videos))
    found = capabilities(model, mode)
    if found is None:
        raise _refusal(n_("This model can't use reference images; choose another model or use frames"))
    prompt = (prompt or "").strip()
    if found["prompt_required"] and not prompt:
        raise _refusal(n_("Describe the shot you want"))
    if len(prompt) > MAX_PROMPT:
        raise _refusal(n_("Keep the video prompt under {count} characters"), count=f"{MAX_PROMPT:,}")
    if last and not found["last_frame"]:
        raise _refusal(n_("This model can't take a last frame"))
    if len(references) > found["references"]:
        raise _refusal(n_("Use at most {count} reference images with this model"), count=found["references"])
    if len(videos) > found["reference_videos"]:
        raise _refusal(n_("This model can't use a reference video"))
    chosen = checked_options(model, mode, options)
    endpoint = entry["endpoints"][mode]
    body = {}
    if prompt:
        body["prompt"] = prompt
    body["duration"] = _duration(entry, chosen["duration"])
    if chosen["resolution"]:
        body["resolution"] = chosen["resolution"]
    if chosen["aspect"]:
        body["aspect_ratio"] = chosen["aspect"]
    if entry["audio_field"]:
        body[entry["audio_field"]] = chosen["audio"]
    body.update(entry.get("fixed", {}))
    if mode == IMAGE:
        first_field, last_field = entry["frames"]
        if last and last_field is None:   # Veo: both frames go to the first-last-frame endpoint.
            endpoint = entry["endpoints"]["first_last"]
            first_field, last_field = entry["first_last_frames"]
        body[first_field] = first
        if last:
            body[last_field] = last
    elif mode == REFERENCE:
        if references:
            body[entry["references"][0]] = list(references)
        if videos:
            body[entry["reference_videos"][0]] = list(videos)
    return endpoint, body


def app_of(endpoint):
    """The (owner/app) whose request URLs fal returns for ``endpoint`` (an admitted endpoint id only)."""
    if endpoint not in ENDPOINTS:
        raise _refusal(n_("Choose a supported video model"))
    return next(spec["app"] for spec in MODELS.values() if endpoint in spec["endpoints"].values())


def catalog_entry(model):
    """The agent catalog's compact controls for one model (``angelo_studio_catalog``)."""
    entry = spec(model)
    modes = {}
    for mode in entry["endpoints"]:
        if mode == "first_last":
            continue
        found = capabilities(model, mode)
        modes[mode] = {"seconds": [found["durations"][0], found["durations"][-1]] if len(found["durations"]) > 3
                       else list(found["durations"]), "resolutions": list(found["resolutions"]),
                       "aspects": list(found["aspects"]), "audio": found["audio"]}
        if mode == IMAGE:
            modes[mode]["last_frame"] = found["last_frame"]
        if mode == REFERENCE:
            modes[mode]["max_references"] = found["references"]
            modes[mode]["max_reference_videos"] = found["reference_videos"]
    return modes
