# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Saved Generation Flow recipes, outside any .blend so other projects can use them. No Blender dependency.

One JSON file per recipe (``{format, name, saved, flow}``) in the Media
Studio library's ``flow-recipes`` folder: recipes name library images as
defaults, so they live with that library. Saving replaces a recipe of the same
name. Using a saved recipe copies it into the open file as a recipe flow,
where Subflow nodes use it; from then on the file's copy is what runs, so a
.blend never depends on this folder.

Per-use state (chosen outputs) is never saved in a recipe.
"""

import hashlib
import time

from .flow_spec import FlowError, as_definition, interface_of, normalize
from .studio_state import atomic_json, library_directory, read_json

FORMAT = "angelo.flow-recipe/1"
MAX_RECIPES = 200


def directory():
    return library_directory() / "flow-recipes"


def _path(name):
    # Recipe names are free text; a digest is a safe, stable file name.
    return directory() / (hashlib.sha256(name.encode("utf-8")).hexdigest()[:24] + ".json")


def save(name, flow):
    """Save a canonical recipe flow under ``name``. Returns its saved form."""
    if not flow.get("recipe"):
        raise FlowError("Only a flow with a recipe interface (inputs and an output) can be saved as a recipe")
    definition = as_definition(flow)
    for node in definition["nodes"]:
        node.pop("chosen", None)
    record = {"format": FORMAT, "name": name, "saved": time.time(), "flow": normalize(definition)}
    saved = list(directory().glob("*.json")) if directory().exists() else []
    if not _path(name).exists() and len(saved) >= MAX_RECIPES:
        raise FlowError(f"The recipe library holds at most {MAX_RECIPES} recipes; remove one first")
    atomic_json(_path(name), record)
    return record


def load_all():
    """{name: canonical recipe flow} of every readable saved recipe; unreadable files are skipped."""
    recipes = {}
    if not directory().exists():
        return recipes
    for path in sorted(directory().glob("*.json"))[:MAX_RECIPES]:
        record = read_json(path, None)
        if not isinstance(record, dict) or record.get("format") != FORMAT or not isinstance(record.get("name"), str):
            continue
        try:
            flow = normalize(record["flow"])
        except (FlowError, KeyError, TypeError):
            continue
        if flow.get("recipe") and path == _path(record["name"]):
            recipes[record["name"]] = flow
    return recipes


def interfaces():
    return {name: interface_of(flow) for name, flow in load_all().items()}


def remove(name):
    path = _path(name)
    if not path.exists():
        raise FlowError(f"There's no saved recipe named '{name}'")
    path.unlink()
