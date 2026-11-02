# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_studio_*``'s video kind: the catalog's video controls and prices, and a video generation's checks.

A video model's catalog entry lists, per input mode (text, image frames, reference images), the lengths,
resolutions, aspect ratios and audio switch fal documents for it (``fal_models.catalog_entry``), and its list price
per second by resolution (``flow_prices``), so the agent can choose and the person can approve with the price in the
approval card (``price_note``). The checks are ``fal_video.validate``'s, the Video tab's own: nothing is uploaded or
billed before they pass. Model-facing text is English.
"""

from . import fal_models, flow_prices
from .provider_registry import model_kind

VIDEO_OPTIONS = frozenset({"duration", "resolution", "aspect", "audio"})


def kind_of(mode, model=None):
    """IMAGE, 3D or VIDEO for a catalog entry's (personal) mode and model (fal makes video and 3D)."""
    return model_kind(mode, model)


def video_capabilities(mode, model):
    modes = fal_models.catalog_entry(model)
    resolutions = modes[fal_models.TEXT]["resolutions"] or [""]
    rates = {r or "any": flow_prices.rate(mode, model, r) for r in resolutions}
    with_audio = {r or "any": flow_prices.rate(mode, model, r, audio=True) for r in resolutions}
    controls = {"modes": modes, "usd_per_second": rates, "prices_as_of": flow_prices.as_of(mode, model)}
    if with_audio != rates:
        controls["usd_per_second_with_audio"] = with_audio
    return controls


def video_generation(agent, choice, args):
    """(options, inputs) of a video: canonical options for the mode its frames or references choose."""
    from .fal_video import validate_any as validate
    supplied = args.get("options", {})
    if set(supplied) - VIDEO_OPTIONS:
        raise ValueError("Video models take duration, resolution, aspect and audio; inspect the catalog")
    frames = [agent.reference(frame) for frame in args.get("frames", [])]
    inputs = {"first": frames[0] if frames else None, "last": frames[1] if len(frames) > 1 else None,
              "references": agent.references(args)}
    mode = fal_models.mode_for(bool(inputs["first"]), bool(inputs["last"]), len(inputs["references"]))
    options = fal_models.checked_options(choice["id"], mode, supplied)
    validate(choice["id"], args["prompt"], options, inputs)
    return options, inputs


def reference_list(refs):
    """Every image a generation sends: its references, or a video's frames and references (``video_generation``)."""
    if isinstance(refs, dict):
        return [r for r in (refs["first"], refs["last"], *refs["references"]) if r]
    return list(refs)


def video_price(choice, options, inputs):
    return flow_prices.estimate(choice["mode"], choice["id"], options, len(inputs["references"]))


def price_note(choice, options, inputs):
    """' · 5 s 720p · ≈ $1.21' for the approval card (kept English, as the rest of its label)."""
    usd = video_price(choice, options, inputs)
    shape = "%d s%s" % (options["duration"], " " + options["resolution"] if options["resolution"] else "")
    return " · %s · %s" % (shape, "≈ $%.2f" % usd if usd is not None else "price unknown")
