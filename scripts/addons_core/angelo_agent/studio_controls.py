# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Studio controls each generation model takes (``angelo_studio_catalog``) and the checked options of one request.

Shared by the agent (``studio_agent``), Generation Flows and the Studio editor; no Blender dependency. A video model's
controls are its fal or BytePlus capability table (``studio_agent_video``); a fal 3D model's are ``fal_3d``'s.
"""

from .provider_registry import GEMINI_IMAGE_CONTROLS
from .studio_agent_video import kind_of, video_capabilities
from .studio_state import ASPECTS
from .refusal_codes import coded


def capabilities(mode, model=""):
    if kind_of(mode, model) == "VIDEO":
        return video_capabilities(mode, model)
    if mode == "fal_api":
        from .fal_3d import controls
        return controls(model)
    if mode in {"tripo_api", "meshy_api"}:
        return {"polycount": {"minimum": 48 if mode == "tripo_api" else 100,
            "maximum": (50000 if model.startswith("P2-") else 1500000) if mode == "tripo_api" else 300000},
            "texture": [False, True], "references_max": 1}
    controls = {"aspect": list(ASPECTS), "resolution": ["2K", "4K"] if mode == "seedream_api" else ["1K", "2K", "4K"], "references_max": 4}
    if mode == "gemini_images_api" and model in GEMINI_IMAGE_CONTROLS:
        # Studio's offered values, limited to what this exact model documents.
        documented = GEMINI_IMAGE_CONTROLS[model]
        controls.update(aspect=[a for a in controls["aspect"] if a in documented["aspect"]],
                        resolution=[r for r in controls["resolution"] if r in documented["resolution"]])
    if mode == "openai_images_api":
        controls.update(quality=["auto", "low", "medium", "high"], background=["auto", "opaque", "transparent"],
                        size="Optional WIDTHxHEIGHT; multiples of 16, max 3840 px, 655360–8294400 pixels, ratio <= 3:1")
    return controls


def generation_options(mode, model, supplied):
    controls = capabilities(mode, model)
    if set(supplied) - (set(controls) - {"references_max", "references_min"}):
        raise coded(ValueError("This model does not support the requested Studio controls; inspect the catalog"),
                    "VALIDATION")
    defaults = {"polycount": 10000, "texture": True} if mode in {"tripo_api", "meshy_api", "fal_api"} else {"aspect": "1:1", "resolution": "2K"}
    if mode == "openai_images_api":
        defaults.update(quality="auto", background="auto", size="")
    options = dict(defaults, **supplied)
    for key, value in options.items():
        constraint = controls[key]
        if isinstance(constraint, list) and value not in constraint:
            raise coded(ValueError("This provider does not support that " + key), "VALIDATION")
    return options
