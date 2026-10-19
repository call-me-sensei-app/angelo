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
"""

AS_OF = "2026-10-08"

OPENAI = "https://developers.openai.com/api/docs/guides/image-generation#calculating-costs"
GEMINI = "https://ai.google.dev/gemini-api/docs/pricing"
SEEDREAM = "https://docs.byteplus.com/en/docs/ModelArk/1544106"
TRIPO = "https://developers.tripo3d.ai/en/pricing"
MESHY = "https://docs.meshy.ai/api/pricing.md"

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
        if spec.get("kind") in {"IMAGE", "3D"} and any(m["id"] == node.get("model") for m in spec.get("models", [])):
            return mode
    return None


def describe(total, unknown):
    """'≈ $1.09', 'at least ≈ $0.52' or 'price unknown', with the table date."""
    if unknown and not total:
        return "price unknown"
    text = ("at least ≈ $" if unknown else "≈ $") + f"{total:.2f}"
    return text + f" (estimate, prices as of {AS_OF})"
