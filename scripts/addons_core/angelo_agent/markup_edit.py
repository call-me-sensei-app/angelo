# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remove BG and Erase on a picture in the viewer, through Media Studio's image generation (2.1.0, maintainer:
"Include in 2.1.0"; doc/angelo/attachment_markup.rst, "Edit with AI").

Both are one image generation with the picture as its reference, run as any Media Studio generation
(``studio_ops.start_job``, billed to the chosen model's own account or the team's key) after the person confirms a card
that says what it costs (``flow_prices``' estimate, never zero when unknown), that it takes a few seconds and that the
result is a new picture with the original kept. ``markup_edit_host`` runs it and brings the result into the chat.

* **Model**: the image model the person last chose in Media Studio (``studio_views.chosen``) when it is ready, for
  both edits (coordinator, 2026-11-19); else the first ready one, Remove BG preferring a model that returns a
  transparent background (``TRANSPARENT_MODES``). On a model without one Remove BG still runs, and the card says the
  background comes back plain.
* **Erase**: the areas the person brushed are painted solid ``ERASE_HEX`` on a copy of the picture
  (``markup_flatten.erase_source``); the prompt asks for them to be filled from their surroundings.
* **Options**: the aspect closest to the picture's shape that every image service documents, at 2K.

Prompts are fixed English for the image model; the card's lines are ``n_`` templates translated where drawn. Pure.
"""

from . import flow_prices
from .ui_text import n_

ACTIONS = ("remove_bg", "erase")
TRANSPARENT_MODES = ("openai_images_api",)   # Returns a PNG with alpha when asked (``background: transparent``).
ASPECTS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")   # Every image service takes these.
ERASE_HEX = "#FF00FF"   # i18n: a colour code the image model reads
ERASE_RGBA = (1.0, 0.0, 1.0, 1.0)
PROMPTS = {   # The image model's instructions: English, as every model request is.
    "remove_bg": ("Remove the background of this picture. "  # i18n: for the image model
                  "Keep the main subject exactly as it is: its shape, edges, colours and details. Make everything "
                  "else a fully transparent background. Change nothing about the subject and add nothing."),
    "erase": ("Parts of this picture are painted over in solid magenta ({hex}). "  # i18n: for the image model
              "Remove whatever is under the magenta and fill those areas so they match their surroundings seamlessly, "
              "as if it had never been there. Change nothing outside the magenta areas, and leave no magenta in the "
              "result.").format(hex=ERASE_HEX),
}
TITLES = {"remove_bg": n_("Remove the background?"), "erase": n_("Erase the brushed areas?")}
RUN = {"remove_bg": n_("Remove background"), "erase": n_("Erase")}
MODEL_LINE = n_("{model} · {account}")
COST = n_("Costs about {price} (estimate, prices as of {date})")
UNKNOWN_COST = n_("This model's price isn't known; your provider bills it")
TEAM_PAYS = n_("Uses your team's provider key through Angelo.")
ACCOUNT_PAYS = n_("Billed to this API account, not your coding subscription.")
WAIT = n_("Takes a few seconds. You can keep working meanwhile.")
KEPT = n_("The result is a new picture; this one is kept.")
PLAIN_BACKGROUND = n_("This model can't make a transparent background: the background comes back plain.")
NO_MODEL = n_("Connect an image model in AI Providers to remove backgrounds and erase")


def transparent(model):
    return model.get("mode") in TRANSPARENT_MODES


def preferred(models, choice):
    """The model entry the person's Studio ``choice`` (connection, model id) names, or None."""
    if not choice:
        return None
    return next((m for m in models if (m.get("connection"), m.get("id")) == tuple(choice)), None)


def choose(models, action, preferred=None):
    """The ready image model ``action`` runs with, or None: the person's choice when it is ready, else the rule."""
    if action not in ACTIONS:
        raise ValueError("Unknown picture edit %r" % (action,))
    if preferred is not None and preferred.get("ready"):
        return preferred
    ready = [m for m in models if m.get("ready")]
    if action == "remove_bg":
        return next((m for m in ready if transparent(m)), ready[0] if ready else None)
    return ready[0] if ready else None


def aspect(size):
    """The documented aspect closest to the picture's shape (by the log of the ratio, so 2:1 and 1:2 are as far)."""
    import math
    target = math.log(size[0] / float(size[1]))
    return min(ASPECTS, key=lambda value: abs(math.log(int(value.split(":")[0]) / int(value.split(":")[1])) - target))


def options(model, action, size):
    """The generation's options, as Media Studio's Generate passes them."""
    chosen = {"aspect": aspect(size), "resolution": "2K", "quality": "auto", "background": "auto", "size": "",
              "polycount": 10000, "texture": True}
    if action == "remove_bg" and transparent(model):
        chosen["background"] = "transparent"
    return chosen


def prompt(action):
    return PROMPTS[action]


def card_lines(action, model, size):
    """The confirmation card's lines: ``n_`` constants or (template, fields), in order."""
    lines = [(MODEL_LINE, {"model": model["name"], "account": model["account"]})]
    price = flow_prices.estimate(model["mode"], model["id"], options(model, action, size), 1)
    if price is None:
        lines.append(UNKNOWN_COST)
    else:
        lines.append((COST, {"price": "$%.2f" % price, "date": flow_prices.as_of(model["mode"], model["id"])}))
    lines.append(TEAM_PAYS if model.get("team") else ACCOUNT_PAYS)
    if action == "remove_bg" and not transparent(model):
        lines.append(PLAIN_BACKGROUND)
    lines += [WAIT, KEPT]
    return lines
