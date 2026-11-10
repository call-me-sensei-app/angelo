# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""fal's 3D models: Microsoft TRELLIS 2, image to a textured GLB, on the fal connection. No Blender dependency.

The fal connection serves video (``fal_models``) and 3D (here): its registry entry lists both, each model with its
own kind (``provider_registry.model_kind``), so TRELLIS shows in Media Studio's 3D tab beside Tripo and Meshy and goes
wherever a 3D model can (``angelo_studio_generate``, Flows' Generate 3D steps, a dropped photo's Make a 3D model).
A job uses the video jobs' queue path (``fal_video.submit_and_follow``: uploads, ONE submit, the returned links, Stop,
pause and resume) and saves the result as ``model.glb`` (``save_model``) like Tripo's and Meshy's.

Read from ``https://fal.ai/models/<endpoint>/llms.txt`` and fal's queue OpenAPI documents on 2026-10-30:
``fal-ai/trellis-2`` takes one ``image_url``, ``fal-ai/trellis-2/multi`` several ``image_urls`` ("multiple views of
the same object"); both answer ``model_glb``. Studio's target polygon count maps to ``decimation_target`` (5,000 to
2,000,000); TRELLIS always textures its model (``texture_size`` 2048 by default), so "Textures off" is refused rather
than ignored. Other settings keep fal's defaults (``resolution`` 1024, the price fal states as $0.30).
"""

from .ui_text import n_

APP = "fal-ai/trellis-2"
MODELS = {"trellis-2": {"name": "TRELLIS 2", "single": "fal-ai/trellis-2", "multi": "fal-ai/trellis-2/multi"}}
ENDPOINTS = frozenset(e for spec in MODELS.values() for e in (spec["single"], spec["multi"]))
MIN_POLYCOUNT, MAX_POLYCOUNT = 5000, 2000000
MAX_IMAGES = 4   # Studio's reference limit; fal documents no maximum for the multi-view endpoint.


def _refusal(template, **fields):
    from .studio_state import StudioValueError
    from .refusal_codes import coded
    return coded(StudioValueError(template, **fields), "VALIDATION")   # A model's option refused: nothing ran.


def controls(model):
    """The agent catalog's controls for a fal 3D model (``studio_agent.capabilities``)."""
    if model not in MODELS:
        raise _refusal(n_("Choose a supported 3D model"))
    return {"polycount": {"minimum": MIN_POLYCOUNT, "maximum": MAX_POLYCOUNT}, "texture": [True],
            "references_min": 1, "references_max": MAX_IMAGES}


def checked_options(options):
    try:
        polycount = int(options.get("polycount", 10000))
    except (TypeError, ValueError):
        raise _refusal(n_("Enter a whole-number target polygon count")) from None
    if not MIN_POLYCOUNT <= polycount <= MAX_POLYCOUNT:
        raise _refusal(n_("Target polygon count must be between {minimum} and {maximum}"),
                       minimum=f"{MIN_POLYCOUNT:,}", maximum=f"{MAX_POLYCOUNT:,}")
    if options.get("texture", True) is False:
        raise _refusal(n_("TRELLIS always makes a textured model; turn Textures on"))
    return {"polycount": polycount, "texture": True}


def request(model, options, images):
    """The endpoint id and body for ``images`` (already-uploaded HTTPS URLs): one image, or several views."""
    if model not in MODELS:
        raise _refusal(n_("Choose a supported 3D model"))
    if not images:
        raise _refusal(n_("TRELLIS makes a model from an image; add a reference image"))
    if len(images) > MAX_IMAGES:
        raise _refusal(n_("Use at most {count} reference images with this model"), count=MAX_IMAGES)
    chosen = checked_options(options)
    body = {"decimation_target": chosen["polycount"]}
    if len(images) == 1:
        return MODELS[model]["single"], dict(body, image_url=images[0])
    return MODELS[model]["multi"], dict(body, image_urls=list(images))


def validate(model, options, references):
    """Every refusal ``generate`` could make before uploading or billing anything."""
    return request(model, options, ["https://fal.media/angelo-placeholder.png"] * len(references))
