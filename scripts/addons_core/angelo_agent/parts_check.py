# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The completeness check behind a parts sheet's review stop: is everything in the character on the sheet? No Blender.

Image models leave items out of parts sheets (4 of 10 research sheets missed one), and a missing item is
easy to overlook by eye. The check sends the character image and the sheet to the person's own AI
Enhance account (Claude or Codex, ``studio_enhance.check_native``) after they approve it, and lists what
the sheet is missing or draws differently. It only reports; the person decides whether to redo the sheet.

The wording names only what a parts sheet is made of (the template's columns and the kinds of items they
hold), never anything about a particular character. Item names in the reply are model output: shown as
written, never translated.
"""

import json
import re

MAX_ITEMS = 12
MAX_TEXT = 80
# The check is offered only once its accuracy on the ten research characters is measured and approved
# (maintainer decision, 2026-10-20). MEASUREMENT names that record; until it does, the button is not drawn
# and the operator refuses to run.
MEASUREMENT = ""
READY = bool(MEASUREMENT)

INSTRUCTIONS = ("You check a character parts sheet against the character image it was made from. You cannot use tools, "
                "read files, browse or edit anything. Reply with one JSON object and nothing else.")
# On Claude, Claude Code ends the turn with one StructuredOutput call that offers this check no tools; its reply
# field carries the answer, so the JSON goes there as text (studio_enhance.OWN_TEXTS).
CLAUDE_REPLY = " Answer with one StructuredOutput call: reply is that JSON object, written as text, and operations is []."


def prompt(slots):
    """The check's request for a sheet laid out in ``slots`` (the template's columns, left to right)."""
    columns = ", ".join(f"{index + 1} {slot['id']}" for index, slot in enumerate(slots))
    return ("The first image is a character. The second image is a parts sheet made from it: a grid of columns "
            f"({columns}), front views in the top row and back views below. Compare them. List each item the "
            "character visibly has that the sheet leaves out, and each item the sheet draws clearly differently "
            "(another colour, shape or count). Items are hair (including facial hair), headwear, garments, footwear "
            "and accessories such as glasses, jewellery, scarves, belts, bags, weapons and held items. Ignore the "
            "plain base layer on the body column, pose, lighting and small differences in detail. Reply with JSON: "
            '{"missing": [{"item": "<short name>", "column": "<column id>"}], '
            '"different": [{"item": "<short name>", "column": "<column id>", "how": "<a few words>"}]}. '
            "Use the column where the item belongs. Two empty lists mean the sheet is complete.")


def _items(value, columns, keys):
    found = []
    for entry in value if isinstance(value, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("item"), str) or not entry["item"].strip():
            continue
        item = {"item": entry["item"].strip()[:MAX_TEXT],
                "column": entry.get("column") if entry.get("column") in columns else None}
        if "how" in keys and isinstance(entry.get("how"), str):
            item["how"] = entry["how"].strip()[:MAX_TEXT]
        found.append(item)
    return found[:MAX_ITEMS]


def parse(reply, slots):
    """{complete, missing: [{item, column}], different: [{item, column, how}]} from the check's reply.

    Raises ValueError when the reply holds no JSON object: a failed check never reads as a pass.
    """
    match = re.search(r"\{.*\}", reply or "", re.S)
    if not match:
        raise ValueError("the check's reply had no result")
    try:
        value = json.loads(match.group(0))
    except ValueError:
        raise ValueError("the check's reply had no result") from None
    if not isinstance(value, dict) or not {"missing", "different"} & set(value):
        raise ValueError("the check's reply had no result")
    columns = {slot["id"] for slot in slots}
    missing = _items(value.get("missing"), columns, ())
    different = _items(value.get("different"), columns, ("how",))
    return {"complete": not missing and not different, "missing": missing, "different": different}
