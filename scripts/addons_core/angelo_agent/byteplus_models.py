# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Seedance video on a BytePlus ModelArk key: the capability table and the request body. No Blender dependency.

BytePlus sells Seedance itself, at about half fal's price (``flow_prices``), on the key the BytePlus connection
already holds for Seedream images (``seedream_api``). Its models sit in the same capability shape as ``fal_models``
(``fal_models.spec`` finds them by id), so Media Studio's Video tab, Generation Flows' Generate Video step and the
agent offer and check them the same way; only the request differs.

Read from BytePlus ModelArk's API reference on 2026-10-30 ("Create a video generation task", ``POST
/api/v3/contents/generations/tasks``): a ``content`` list with the prompt (``text``) and images as ``image_url``
(a URL or a base64 data URI) with a ``role`` (``first_frame``, ``last_frame`` or ``reference_image``); ``resolution``,
``ratio`` (``adaptive`` for image-to-video: Seedance 2.5 accepts nothing else there), ``duration`` (an integer),
``generate_audio`` and ``watermark``. Images go inline as data URIs (the request may be up to 64 MB), so nothing is
uploaded anywhere first. Reference videos must be a public URL BytePlus can fetch, which Angelo doesn't have for a
local file, so they are not offered here (fal takes them). Models need activating in the BytePlus console (a
balance of USD 30 or a resource pack); a key without it is refused by BytePlus at submit.
"""

TEXT, IMAGE, REFERENCE = "text", "image", "reference"
_ASPECTS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")


def _seedance(name, resolutions, longest, images):
    return {"name": name, "durations": tuple(range(4, longest + 1)), "duration_format": "integer",
            "resolutions": resolutions, "audio_field": "generate_audio",
            "endpoints": {TEXT: "byteplus", IMAGE: "byteplus", REFERENCE: "byteplus"},
            "aspects": {TEXT: _ASPECTS, REFERENCE: _ASPECTS}, "frames": ("first_frame", "last_frame"),
            "references": ("reference_image", images), "prompt_required": {TEXT: True, IMAGE: False, REFERENCE: False}}


MODELS = {
    "dreamina-seedance-2-0-mini-260615": _seedance("Seedance 2.0 Mini", ("480p", "720p"), 15, 9),
    "dreamina-seedance-2-0-fast-260128": _seedance("Seedance 2.0 Fast", ("480p", "720p"), 15, 9),
    "dreamina-seedance-2-0-260128": _seedance("Seedance 2.0", ("480p", "720p", "1080p", "4k"), 15, 9),
    "dreamina-seedance-2-5-260628": _seedance("Seedance 2.5", ("480p", "720p", "1080p"), 30, 30),
}
DEFAULT_MODEL = "dreamina-seedance-2-0-fast-260128"


def _refusal(template, **fields):
    from .studio_state import StudioValueError
    return StudioValueError(template, **fields)


def request(model, prompt, options, first="", last="", references=(), videos=()):
    """The task body for one generation. Images are HTTPS URLs or ``data:`` URIs; checked by ``fal_models``'s rules."""
    from . import fal_models
    from .ui_text import n_
    if videos:
        raise _refusal(n_("This model can't use a reference video"))
    mode = fal_models.mode_for(bool(first), bool(last), len(references))
    found = fal_models.capabilities(model, mode)
    prompt = (prompt or "").strip()
    if found["prompt_required"] and not prompt:
        raise _refusal(n_("Describe the shot you want"))
    if len(prompt) > fal_models.MAX_PROMPT:
        raise _refusal(n_("Keep the video prompt under {count} characters"), count=f"{fal_models.MAX_PROMPT:,}")
    if len(references) > found["references"]:
        raise _refusal(n_("Use at most {count} reference images with this model"), count=found["references"])
    chosen = fal_models.checked_options(model, mode, options)
    content = [{"type": "text", "text": prompt}] if prompt else []
    images = [(first, "first_frame"), (last, "last_frame")] if mode == IMAGE else [(r, "reference_image") for r in references]
    content += [{"type": "image_url", "image_url": {"url": url}, "role": role} for url, role in images if url]
    body = {"model": model, "content": content, "duration": chosen["duration"], "resolution": chosen["resolution"],
            "ratio": chosen["aspect"] or "adaptive", "generate_audio": chosen["audio"], "watermark": False}
    return "byteplus", body
