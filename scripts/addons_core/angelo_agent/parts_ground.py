# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fit parts to the character image: where each split part sits on the character, for Place Character. No Blender.

A parts sheet draws every part in its own cell at its own scale: on the ten research characters garments came
out about 1.5x and small parts (hair, hats, held items) about 2.4x too large, and small parts away from where
they are worn (median 20 cm off on a 1.7 m character). The character image shows them where they are worn. So
after the person approves it, their own AI Enhance account (Claude or Codex, ``studio_enhance.ground_native``)
is shown the character image and the split's cut-outs, numbered, and returns each part's box on the character.
Place Character then puts each found part in that box (``character_compose``); a part it didn't find keeps
the sheet's box.

Measured (compose-eval PREREG-2026-10-23, research folder): the boxes against hand-labelled truth on six held-out
characters, 3 trials each, GPT 6.1 Sol: median 1.3 cm off, 90th percentile 3.7 cm, size within 3%. A connection kind
and model are offered only once the shipped path has been measured on exactly them (``MEASURED``, ``measured``).

The wording names only what the images are made of, never anything about a particular character.
"""

import json
import re

import numpy as np

from .provider_registry import is_coding

# AI Enhance connections whose shipped path has passed the pre-registered measurement (compose-eval PREREG, addenda A
# and B), by (connection mode, model): the button is drawn, and the operator runs, only for exactly these. A runtime is
# not enough: the Codex runtime also runs other providers' models and keys nobody measured. {(mode, model): record}
MEASURED = {
    ("codex_subscription", "gpt-6.1-sol"): (
        "GPT 6.1 Sol on a ChatGPT connection in the app, low effort, commit 82f11ec69c1, 2026-10-23: 30/30 trials, "
        "held-out median 1.1-1.3 cm, p90 3.7 cm, every part kind passing (compose-eval inapp-sol-B; the first run, "
        "before the capacity retry, parsed 27/30 and failed)"),
}


def measured(mode, model):
    """Whether Fit to My Image is offered for an AI Enhance connection of ``mode`` running ``model``."""
    return (mode, model) in MEASURED


def offered(record, config):
    """Whether Fit to My Image is offered with AI Enhance bound to connection ``record`` and its ``config`` (settings
    with the model): a coding connection and model whose fit was measured. Read while the card draws, so it never
    raises: a record or settings of any other shape (a Gemini key, a team seat, a stale file) is simply not offered."""
    try:
        return bool(is_coding(record) and measured(record["mode"], (config or {}).get("model")))
    except (KeyError, TypeError, AttributeError, ValueError):
        return False
CHARACTER_EDGE = 1024     # the character image's long edge as sent (the measurement's size)
TILE_EDGE = 360           # a cut-out's long edge in the numbered parts image
MIN_BODY_SHARE = 0.1      # a body box shorter than this share of the image is not a body: the fit failed
EDGE_MARGIN = 2           # 0-1000 units: a body box this close to the top or bottom edge is a cropped figure


class FitTimeout(ValueError):
    """The account didn't answer in time; Place keeps the sheet's layout."""

INSTRUCTIONS = ("You locate the parts of a character on its image. You cannot use tools, read files, browse or edit "
                "anything. Reply with one JSON object and nothing else.")
# On Claude, Claude Code ends the turn with one StructuredOutput call that offers this request no tools; its reply
# field carries the answer, so the JSON goes there as text (studio_enhance.OWN_TEXTS).
CLAUDE_REPLY = " Answer with one StructuredOutput call: reply is that JSON object, written as text, and operations is []."

# The measured request, verbatim (sha256 d6bd564b... of the frozen file, compose-eval).
PROMPT = (
    "The first image is a character. The second image shows the parts the character is made of, each cut out on white "
    "and numbered: 0 is the plain base body, the others are hair, headwear, garments, footwear and accessories. For "
    "each numbered part, give the box it occupies on the character in the first image, as the character wears it.\n\n"
    "Coordinates run from 0 to 1000 across the first image: x from its left edge to its right edge, y from its top "
    "edge to its bottom edge, whatever the image's shape.\n\n"
    "- Part 0, the body: from the top of the head itself (under any hair or headwear; ears, horns or a muzzle that "
    "belong to the body count) down to the soles of the feet, and across the body including its arms and hands.\n"
    "- A garment or item partly hidden under another layer: box the whole of it as worn, estimating the hidden extent "
    "from what shows.\n"
    "- Box everything a part's cut-out shows: a cut-out of a pair (two boots, two gloves) is boxed around both as worn.\n"
    "- A single item the character wears more than once (one bracelet drawn, two worn): box one of them.\n"
    "- A part you can't see on the character at all: null.\n\n"
    'Reply with one JSON object and nothing else: {"parts": {"0": [x0, y0, x1, y1], "1": [x0, y0, x1, y1] or null, ...}}')

_DIGITS = {"0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
           "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
           "8": "111101111101111", "9": "111101111001111"}


def ordered(parts):
    """The parts in the order they are numbered: the body first (0), then the split's order."""
    return sorted(parts, key=lambda part: part["kind"] != "BODY")


def _number(tile, text, x, y, scale=9, colour=(217, 0, 0)):
    for index, character in enumerate(text):
        bits = _DIGITS[character]
        for row in range(5):
            for column in range(3):
                if bits[row * 3 + column] == "1":
                    top, left = y + row * scale, x + (index * 4 + column) * scale
                    tile[top:top + scale, left:left + scale] = colour


def _shrink(rgba):
    """Box-filter a cut-out by a whole factor so its long edge is at most ``TILE_EDGE`` (a sliver stays as is)."""
    factor = max(1, int(np.ceil(max(rgba.shape[:2]) / TILE_EDGE)))
    if factor == 1 or min(rgba.shape[:2]) < factor:
        return rgba.astype(np.float32)
    height, width = rgba.shape[0] // factor, rgba.shape[1] // factor
    return rgba[:height * factor, :width * factor].astype(np.float32).reshape(height, factor, width, factor, -1).mean(axis=(1, 3))


def contact_sheet(cutouts):
    """The numbered parts image (uint8 RGB, top row first) from cut-outs (uint8 RGBA arrays) in ``ordered`` order:
    each on white in its own column under its number, as the measurement sent them."""
    tiles = []
    for number, rgba in enumerate(cutouts):
        small = _shrink(np.asarray(rgba)) / 255.0
        rgb = small[..., :3] * small[..., 3:] + (1.0 - small[..., 3:])
        tile = np.full((440, max(rgb.shape[1], 120) + 30, 3), 255, np.uint8)
        tile[60:60 + min(rgb.shape[0], 370), 15:15 + rgb.shape[1]] = (rgb[:370] * 255 + 0.5).astype(np.uint8)
        _number(tile, str(number), 12, 8)
        tile[:, -3:] = 153
        tiles.append(tile)
    if not tiles:
        raise ValueError("there are no parts to fit")
    return np.concatenate(tiles, axis=1)


def _box(value, size):
    """A 0-1000 box from the reply as pixels of an image of ``size`` (width, height), or None."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (min(1000.0, max(0.0, float(v))) for v in value)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite([x0, y0, x1, y1]).all() and x1 - x0 >= 1 and y1 - y0 >= 1):
        return None
    width, height = size
    return [round(x0 / 1000 * width, 1), round(y0 / 1000 * height, 1), round(x1 / 1000 * width, 1),
            round(y1 / 1000 * height, 1)]


def parse(reply, part_ids, size):
    """{"size", "body", "boxes": {part id: box}} in pixels of the character image as sent (``size``, width x height).

    ``part_ids`` are the numbered parts in order (``ordered``; 0 is the body). A part the reply leaves out or boxes
    badly is left out: Place keeps its sheet box. Raises ValueError when the reply has no usable body box, so a
    failed fit never changes Place.
    """
    match = re.search(r"\{.*\}", reply or "", re.S)
    try:
        value = json.loads(match.group(0)) if match else None
    except ValueError:
        value = None
    parts = value.get("parts") if isinstance(value, dict) else None
    if not isinstance(parts, dict):
        raise ValueError("the fit's reply had no result")
    raw = parts.get("0")
    body = _box(raw, size)
    if body is None or body[3] - body[1] < MIN_BODY_SHARE * size[1]:
        raise ValueError("the fit's reply had no body")
    if float(raw[1]) <= EDGE_MARGIN or float(raw[3]) >= 1000 - EDGE_MARGIN:
        # The figure runs off the image: its height can't set the frame, and every part would be scaled by it.
        raise ValueError("the character isn't whole in the image")
    boxes = {}
    for number, identity in enumerate(part_ids[1:], start=1):
        box = _box(parts.get(str(number)), size)
        if box is not None:
            boxes[identity] = box
    return {"size": [int(size[0]), int(size[1])], "body": body, "boxes": boxes}
