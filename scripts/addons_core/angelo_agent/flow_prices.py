# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dated per-generation price ESTIMATES for flow approvals. No Blender dependency.

Providers don't report what a request cost, and their prices change, so these
are estimates from the public pricing pages listed beside each entry, as of
``AS_OF``. An unknown price is shown as unknown, never as zero. The approval's
enforced limit is its list of generations, not this table.

Assumptions, so a reader can check them against the sources:

* OpenAI ``quality: auto`` has no published price; it is estimated as ``high``.
  A non-square 2K size is estimated at the 2048x2048 price (the next tabled
  size up), and custom sizes are unknown.
* Tripo charges credits at 1 credit = $0.01. PBR (Angelo's default with
  texture) has no surcharge, so a textured task costs the standard-texture price.
* Meshy publishes no per-credit API rate; $0.02 is its Pro plan's rate
  ($20 / 1,000 credits), the plan the API requires, so other plans cost less.
  Textured text-to-3D is a 20-credit preview plus a 10-credit refine.
* Seedream 5.0 Pro has no published 4K price; the first reference image is
  free and later ones ($0.003 each) are ignored.
* fal's TRELLIS 2 (3D, ``fal_3d``) is $0.30 a generation at fal's default resolution 1024 (its page: $0.25, $0.30,
  $0.35 at 512, 1024, 1536; Angelo sends the default). Its multi-view endpoint states no per-request price, so
  several images make it unknown.
* fal video (rows dated ``FAL_AS_OF``, each from its model's own fal page) is billed per second of output, so a
  video's estimate is its rate times the length Angelo asks for (Angelo never sends "auto"). Seedance prices
  by tokens, (width x height x seconds x 24) / 1024, and states per-second rates only for 720p and 1080p; its 480p
  and 4K estimates here apply that published formula at 864x480 and 3840x2160 ($0.014 per 1,000 tokens, Fast
  $0.0112, 4K $0.008), so a long 4K clip shows its real order of cost rather than "unknown" (the Angelo gateway's
  meter, which must not estimate, leaves them unknown). Veo 3.1 Fast's reference mode is a flat $0.10 per second
  ($0.15 with audio) at every resolution. MiniMax H3 Max's pages showed a 35%
  promotion "ending October 31"; these are the list prices they give for after it, since Angelo 1.0.8 ships
  then. Its per-request reference allowance covers Studio's four references, so none adds a charge.
"""

AS_OF = "2026-10-08"
FAL_AS_OF = "2026-10-30"

OPENAI = "https://developers.openai.com/api/docs/guides/image-generation#calculating-costs"
GEMINI = "https://ai.google.dev/gemini-api/docs/pricing"
SEEDREAM = "https://docs.byteplus.com/en/docs/ModelArk/1544106"
TRIPO = "https://developers.tripo3d.ai/en/pricing"
MESHY = "https://docs.meshy.ai/api/pricing.md"


def _fal_page(endpoint):
    return f"https://fal.ai/models/{endpoint}/llms.txt"

# USD per image by quality, for the sizes Angelo's presets produce.
_OPENAI_SIZES = {
    "square_1k": {"low": 0.0059, "medium": 0.0132, "high": 0.0527},
    "wide_1k": {"low": 0.0047, "medium": 0.0103, "high": 0.0412},
    "square_2k": {"low": 0.0119, "medium": 0.0268, "high": 0.1070},
    "wide_4k": {"low": 0.0111, "medium": 0.0260, "high": 0.1001},
}


def _openai(options, references):
    if options.get("size"):
        return None
    quality = options.get("quality", "auto")
    quality = "high" if quality == "auto" else quality
    aspect, resolution = options.get("aspect", "1:1"), options.get("resolution", "2K")
    square = aspect == "1:1"
    size = {"1K": "square_1k" if square else "wide_1k", "2K": "square_2k",
            "4K": "wide_4k" if aspect in {"16:9", "9:16"} else None}.get(resolution)
    return _OPENAI_SIZES.get(size, {}).get(quality)


def _by_resolution(table):
    return lambda options, references: table.get(options.get("resolution", "2K"))


def _textured(textured, untextured):
    return lambda options, references: textured if options.get("texture", True) else untextured


# (mode, model) -> {"estimate": f(options, reference_count) -> USD or None, "source": url}
PRICES = {
    ("openai_images_api", "gpt-image-2.5-flare"): {"estimate": _openai, "source": OPENAI},
    ("openai_images_api", "gpt-image-2.5-sunburst"): {"estimate": _openai, "source": OPENAI},
    ("gemini_images_api", "gemini-3.1-flash-image"): {
        "estimate": _by_resolution({"512": 0.045, "1K": 0.067, "2K": 0.101, "4K": 0.151}), "source": GEMINI},
    ("gemini_images_api", "gemini-3-pro-image"): {
        "estimate": _by_resolution({"1K": 0.134, "2K": 0.134, "4K": 0.24}), "source": GEMINI},
    ("seedream_api", "dola-seedream-5-0-pro-260628"): {
        "estimate": _by_resolution({"2K": 0.09}), "source": SEEDREAM},
    ("tripo_api", "P2-20260801"): {"estimate": _textured(1.10, 1.00), "source": TRIPO},
    ("tripo_api", "v3.1-20260211"): {
        "estimate": lambda options, references: ((0.30 if options.get("texture", True) else 0.20) if references
                                                 else (0.20 if options.get("texture", True) else 0.10)),
        "source": TRIPO},
    ("meshy_api", "meshy-7"): {"estimate": _textured(0.60, 0.40), "source": MESHY},
    ("meshy_api", "meshy-7.1"): {"estimate": _textured(0.60, 0.40), "source": MESHY},
}


def _per_second(rates, audio_rates=None, reference_rates=None, reference_audio_rates=None, video_input=None):
    """USD per second by resolution (``""`` when the model has none), with or without audio, or in reference mode.

    ``video_input`` prices a reference video (``options["reference_video_seconds"]``, its length): "seedance" bills
    0.6 times the rate over the input and output seconds together (Seedance's pages), "flat" bills the output only
    (Wan), and None makes it unknown (H3 Max bills a video's reference tokens, which Angelo can't count)."""
    def estimate(options, references):
        given = options.get("reference_video_seconds")   # A negative length: the file's length couldn't be read.
        if given:
            if video_input is None or (video_input == "seedance" and (not isinstance(given, (int, float)) or given < 0)):
                return None
            if video_input == "seedance":
                base = plain(dict(options, duration=1), references)
                duration = options.get("duration")
                return None if base is None or type(duration) is not int else round(base * 0.6 * (duration + given), 4)
        return plain(options, references)

    def plain(options, references):
        audio = bool(options.get("audio"))
        if references and reference_rates:
            table = reference_audio_rates if audio and reference_audio_rates else reference_rates
        else:
            table = audio_rates if audio and audio_rates else rates
        rate = table.get(options.get("resolution", ""))
        duration = options.get("duration")
        return None if rate is None or type(duration) is not int else round(rate * duration, 4)
    return estimate


def _fal(model, endpoint, rates, audio_rates=None, reference_rates=None, reference_audio_rates=None, video_input=None):
    from . import fal_models
    per_second = _per_second(rates, audio_rates, reference_rates, reference_audio_rates, video_input)
    # A flow step may leave options to the model's defaults (fal_models.defaults), as its run does.
    estimate = lambda options, references: per_second(  # noqa: E731
        dict(fal_models.defaults(model), **fal_models.fitted_options(model, fal_models.TEXT, options)), references)
    rate = lambda options, references: per_second(dict(options, duration=1, reference_video_seconds=0), references)  # noqa: E731
    return {("fal_api", model): {"estimate": estimate, "rate": rate, "source": _fal_page(endpoint), "as_of": FAL_AS_OF}}


_KLING = lambda off, on: ({"": off}, {"": on})   # noqa: E731 (Kling has no resolution control)
PRICES.update({
    **_fal("seedance-2.0", "bytedance/seedance-2.0/text-to-video", {"480p": 0.1361, "720p": 0.3034, "1080p": 0.682, "4k": 1.5552},
           video_input="seedance"),
    **_fal("seedance-2.0-fast", "bytedance/seedance-2.0/fast/text-to-video", {"480p": 0.1089, "720p": 0.2419},
           video_input="seedance"),
    **_fal("seedance-2.5", "bytedance/seedance-2.5/text-to-video", {"480p": 0.2205, "720p": 0.473, "1080p": 1.164},
           video_input="seedance"),
    **_fal("h3-max", "minimax/h3-max/text-to-video", {"480P": 0.05, "768P": 0.08, "1080P": 0.16}),
    **_fal("h3-max-turbo", "minimax/h3-max-turbo/text-to-video", {"480P": 0.025, "768P": 0.04, "1080P": 0.08},
           reference_rates={"480P": 0.05, "768P": 0.05, "1080P": 0.05}),
    **_fal("veo-3.1", "fal-ai/veo3.1", {"720p": 0.20, "1080p": 0.20, "4k": 0.40}, {"720p": 0.40, "1080p": 0.40, "4k": 0.60}),
    **_fal("veo-3.1-fast", "fal-ai/veo3.1/fast", {"720p": 0.10, "1080p": 0.10, "4k": 0.30}, {"720p": 0.15, "1080p": 0.15, "4k": 0.35},
           reference_rates={"720p": 0.10, "1080p": 0.10, "4k": 0.10}, reference_audio_rates={"720p": 0.15, "1080p": 0.15, "4k": 0.15}),
    **_fal("kling-3.0-pro", "fal-ai/kling-video/v3/pro/text-to-video", *_KLING(0.112, 0.168)),
    **_fal("kling-3.0-standard", "fal-ai/kling-video/v3/standard/text-to-video", *_KLING(0.084, 0.126)),
    **_fal("kling-o3-pro", "fal-ai/kling-video/o3/pro/text-to-video", *_KLING(0.112, 0.14)),
    **_fal("kling-o3-standard", "fal-ai/kling-video/o3/standard/text-to-video", *_KLING(0.084, 0.112)),
    **_fal("wan-3.0", "alibaba/wan-3.0/text-to-video", {"480p": 0.05, "720p": 0.10, "1080p": 0.20}, video_input="flat"),
})


# Seedance on BytePlus ModelArk (byteplus_models), from its model pricing page (``SEEDREAM``) on 2026-10-30: USD per
# million tokens, online inference, no video input: 2.0 $7.0 (480p, 720p), $7.7 (1080p), $4.0 (4K); 2.0 Fast $5.6;
# 2.0 Mini $3.5; 2.5 $10.70 and $11.7 (1080p). Tokens = (input + output seconds) x width x height x 24 / 1024, so per
# output second at 16:9 (864x480, 1280x720, 1920x1080, 3840x2160) the rates below; the page's own example (2.5, 1080p,
# 16:9: $0.569 a second) agrees. Other aspect ratios differ by a few percent. Audio costs nothing extra.
_BYTEPLUS_TOKENS = {"480p": 9720, "720p": 21600, "1080p": 48600, "4k": 194400}


def _byteplus(model, per_million):
    rates = {r: round(_BYTEPLUS_TOKENS[r] * usd / 1e6, 5) for r, usd in per_million.items()}
    per_second = _per_second(rates)
    from . import fal_models
    return {("seedream_api", model): {
        "estimate": lambda options, references: per_second(
            dict(fal_models.defaults(model), **fal_models.fitted_options(model, fal_models.TEXT, options)), references),
        "rate": lambda options, references: per_second(dict(options, duration=1), references),
        "source": SEEDREAM, "as_of": FAL_AS_OF}}


PRICES.update({
    **_byteplus("dreamina-seedance-2-0-260128", {"480p": 7.0, "720p": 7.0, "1080p": 7.7, "4k": 4.0}),
    **_byteplus("dreamina-seedance-2-0-fast-260128", {"480p": 5.6, "720p": 5.6}),
    **_byteplus("dreamina-seedance-2-0-mini-260615", {"480p": 3.5, "720p": 3.5}),
    **_byteplus("dreamina-seedance-2-5-260628", {"480p": 10.70, "720p": 10.70, "1080p": 11.7}),
})
PRICES[("fal_api", "trellis-2")] = {"estimate": lambda options, references: 0.30 if references <= 1 else None,
                                    "source": _fal_page("fal-ai/trellis-2"), "as_of": FAL_AS_OF}


def estimate(mode, model, options, references=0):
    """Estimated USD for one generation of ``model`` on a personal or team ``mode`` with ``options``, or None."""
    from .provider_registry import service_mode
    entry = PRICES.get((service_mode(mode), model))
    return None if entry is None else entry["estimate"](options, references)


def rate(mode, model, resolution="", audio=False, references=0):
    """USD per second of a video model at ``resolution``, or None (a model without per-second rows, an unknown one)."""
    from .provider_registry import service_mode
    entry = PRICES.get((service_mode(mode), model))
    return None if entry is None or "rate" not in entry else entry["rate"]({"resolution": resolution, "audio": audio},
                                                                         references)


def as_of(mode, model):
    """The date of the row that prices ``model`` (``AS_OF`` for the image and 3D table)."""
    from .provider_registry import service_mode
    return PRICES.get((service_mode(mode), model), {}).get("as_of", AS_OF)


def price(node):
    """Estimated USD for one generation of a canonical generator node, or None when unknown.

    ``node["_references"]`` (set by the planner) is how many reference images it reads.
    """
    entry = PRICES.get((_mode(node), node.get("model")))
    if entry is None:
        return None
    return entry["estimate"](node.get("options", {}), node.get("_references", 0))


def _mode(node):
    from .provider_registry import SPECS
    for mode, spec in SPECS.items():
        if spec.get("kind") in {"IMAGE", "3D", "VIDEO"} and any(m["id"] == node.get("model") for m in spec.get("models", [])):
            return mode
    return None


def describe(total, unknown, date=AS_OF):
    """'≈ $1.09', 'at least ≈ $0.52' or 'price unknown', with the table date."""
    if unknown and not total:
        return "price unknown"
    text = ("at least ≈ $" if unknown else "≈ $") + f"{total:.2f}"
    return text + f" (estimate, prices as of {date})"
