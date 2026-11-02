# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the photo drop card offers for one photo, and why a choice is unavailable (1.0.8). No Blender here.

``photo_drop`` gathers the facts (the file, the 3D models the person can bill, the chat, the object under the
pointer) into ``Facts``; ``rows`` turns them into the card's four rows, each enabled or saying why not, in the order
the card draws them. A row never fails silently: a format the chosen route can't take, a missing provider account,
a busy chat or a drop on empty space each say so on the row itself.

Formats, by the file's bytes (``references.image_type``): a 3D model takes what both 3D providers take (PNG, JPEG:
``studio_generation.MODEL_REFERENCE_MIMES``), a chat reference what the chat takes (PNG, JPEG, WebP); a material and a
picture in the scene take any image Blender reads.
"""

from dataclasses import dataclass, field

from .ui_text import n_

# What ``ui_text.iface_`` gets for each row; translated where the card draws them.
MAKE_3D, REFERENCE, MATERIAL, PICTURE = "make_3d", "reference", "material", "picture"
CONNECT = "connect"   # The Make a 3D model row's action while no 3D account is ready: open AI Providers.
TITLES = {MAKE_3D: n_("Make a 3D model"), REFERENCE: n_("Use as a reference"), MATERIAL: n_("Use as a material"),
          PICTURE: n_("Add as a picture")}
NOT_A_PHOTO_3D = n_("3D models are made from PNG or JPEG photos")
NOT_A_PHOTO_CHAT = n_("The chat takes PNG, JPEG or WebP pictures")
CONNECT_FIRST = n_("Connect Meshy or Tripo in AI Providers first")
MODEL_LINE = n_("{model} · {account} · placed here when it's done")
TEAM_LINE = n_("{model} · your team's key · placed here when it's done")
UNCHECKED_LINE = n_("{model} · {account} · key not checked yet · placed here when it's done")   # Generate's warning.
CHAT_BUSY = n_("Wait for the chat's turn to finish")
CHAT_LINE = n_("Attach it to the chat's next message")
MATERIAL_ON = n_("Give {object} this picture as its material")
NO_TARGET = n_("Drop it on an object to make it that object's material")
PICTURE_LINE = n_("A reference image standing here, facing the view")
PRICE = n_("≈ ${amount}")
PRICE_UNKNOWN = n_("price unknown")
GENERATION_OPTIONS = {"polycount": 10000, "texture": True}   # Media Studio's 3D defaults (studio_agent).


@dataclass
class Facts:
    """Everything the card's rows depend on, read when the photo landed (and again after a model is chosen)."""

    name: str                         # The file's name, shown as it is (never translated).
    model_photo: bool                 # PNG or JPEG by its bytes: a 3D model can be made from it.
    chat_photo: bool = None           # PNG, JPEG or WebP: the chat takes it (None: as ``model_photo``).
    models: list = field(default_factory=list)   # The 3D models ready to bill (``studio_state.model_options``).
    model: dict = None                # The one chosen (``choose``), or None while none is ready.
    chat_busy: bool = False
    target: str = ""                  # The object the photo landed on, when it can take a material.


@dataclass(frozen=True)
class Row:
    action: str
    title: str        # n_ constant.
    subtitle: str     # n_ template (``fields``) or a constant.
    fields: tuple = ()
    enabled: bool = True
    price: object = None   # Make a 3D model: the estimate in USD, None when unknown (shown as unknown, never $0).


def key(model):
    """A model choice's identity: its exact account and model."""
    return "%s|%s" % (model["connection"], model["id"])


def choose(models, preferred=()):
    """The model to bill: the first of ``preferred`` keys (Media Studio's 3D choice, then the last photo drop's)
    that is ready, else the first ready model. None when none is ready (the card asks to connect one)."""
    ready = [m for m in models if m.get("ready")]
    for wanted in preferred:
        found = next((m for m in ready if key(m) == wanted), None)
        if found is not None:
            return found
    return ready[0] if ready else None


def price(model, prices=None):
    """Estimated USD for one image-to-3D generation with Media Studio's defaults, or None when unknown."""
    if prices is None:
        from .flow_prices import PRICES as prices
    entry = prices.get((model["mode"], model["id"])) if model else None
    return entry["estimate"](dict(GENERATION_OPTIONS), 1) if entry else None


def rows(facts, prices=None):
    """The card's rows for ``facts``, in order."""
    model = facts.model
    if not facts.model_photo:
        make = Row(MAKE_3D, TITLES[MAKE_3D], NOT_A_PHOTO_3D, enabled=False)
    elif model is None:
        make = Row(CONNECT, TITLES[MAKE_3D], CONNECT_FIRST)
    else:
        line = TEAM_LINE if model.get("team") else MODEL_LINE if model.get("verified") else UNCHECKED_LINE
        make = Row(MAKE_3D, TITLES[MAKE_3D], line, (("model", model["name"]), ("account", model["account"])),
                   price=price(model, prices))
    if not (facts.model_photo if facts.chat_photo is None else facts.chat_photo):
        reference = Row(REFERENCE, TITLES[REFERENCE], NOT_A_PHOTO_CHAT, enabled=False)
    elif facts.chat_busy:
        reference = Row(REFERENCE, TITLES[REFERENCE], CHAT_BUSY, enabled=False)
    else:
        reference = Row(REFERENCE, TITLES[REFERENCE], CHAT_LINE)
    material = (Row(MATERIAL, TITLES[MATERIAL], MATERIAL_ON, (("object", facts.target),)) if facts.target
                else Row(MATERIAL, TITLES[MATERIAL], NO_TARGET, enabled=False))
    return [make, reference, material, Row(PICTURE, TITLES[PICTURE], PICTURE_LINE)]


def price_label(row):
    """(template, fields) of a row's price, or None for a row that bills nothing (any other row, or Make a 3D model
    while it can't run)."""
    if row.action != MAKE_3D or not row.enabled:
        return None
    if row.price is None:
        return PRICE_UNKNOWN, ()
    return PRICE, (("amount", "%.2f" % row.price),)


READY, FAILED = "ready", frozenset({"failed", "stopped"})   # Media Studio record states (studio_generation).


def decide(placement, asset, document, scene_alive, can_place_now):
    """What ``photo_drop_jobs`` does with a placement now: ``"wait"``, ``"place"``, ``"elsewhere"`` (ready, but the
    document it was dropped in was replaced or its scene is no longer shown), ``"failed"`` (failed or stopped) or
    ``"gone"`` (the record was removed from Media Studio)."""
    if asset is None:
        return "gone"
    status = asset.get("status")
    if status in FAILED:
        return "failed"
    if status != READY:
        return "wait"
    if placement.document != document or not scene_alive:
        return "elsewhere"
    return "place" if can_place_now else "wait"
