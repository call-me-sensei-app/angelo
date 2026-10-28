# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The character-parts steps' states (Split Sheet, a per-part 3D step before its split, Place Character), as
``flow_graph.evaluate`` reads every other step's: pure, no Blender. A step that only lacks something nobody has filled
in yet (a link, an account, the same for a step before it) carries flow_graph's ``setup`` code, so the interface says
what to do next rather than showing a failure (``flow_setup``)."""

from .flow_graph import (BLOCKED, MISSING, PARTIAL, READY, RUNNING, SETUP_LINK, SETUP_UPSTREAM, STALE, WAITING,
                         _check_model, inputs_of, split_key)
from .flow_spec import RUNS_AS


def evaluate_split(flow, node, states, splits):
    """A Split Sheet step: READY with the stored split of its current sheet, else waiting for one."""
    upstream = inputs_of(flow, node["id"], "sheet")
    if not upstream:
        return {"state": BLOCKED, "value": None, "reason": "link the parts sheet image into its sheet input",
                "setup": SETUP_LINK}
    state = states[upstream[0]]
    if "chosen" in state:
        sheet = state["chosen"]
    else:
        value = state.get("value")
        if value is not None and value[0] != "asset":
            return {"state": BLOCKED, "value": None, "reason": "a parts sheet must be a library image, not a URL"}
        sheet = value[1] if value else None
    if not sheet:
        waiting = state["state"] in {WAITING, MISSING, STALE, RUNNING, PARTIAL} or "chosen" in state
        if waiting:
            return {"state": WAITING, "value": None, "reason": f"waits for '{upstream[0]}'", "waits": [upstream[0]]}
        return _blocked_by(upstream[0], state)
    key = split_key(sheet, node)
    found = splits.get(node["id"]) or {}
    if found.get("key") == key and found.get("error"):
        return {"state": BLOCKED, "value": None, "reason": found["error"], "split_request": None}
    if found.get("key") == key and found.get("parts") is not None:
        return {"state": READY, "value": {"key": key, "sheet": sheet, "parts": found["parts"],
                                          "cell_height": found.get("cell_height"), "grid": found.get("grid"),
                                          "size": found.get("size"), "grounding": found.get("grounding")},
                "reason": None}
    return {"state": WAITING, "value": None, "reason": "the sheet is split as soon as it is ready",
            "split_request": {"key": key, "sheet": sheet}}


def evaluate_unexpanded_parts(flow, node, catalog, states):
    """A per-part 3D step before its parts are known (flow_fanout expands it once they are)."""
    runs_as = dict(node, type=RUNS_AS[node["type"]])
    problem, setup = (node["problem"], None) if node.get("problem") else _check_model(runs_as, catalog)
    if problem:
        blocked = {"state": BLOCKED, "value": None, "reason": problem, "fixed_block": True}
        return dict(blocked, setup=setup) if setup else blocked
    upstream = inputs_of(flow, node["id"], "parts")
    if not upstream:
        return {"state": BLOCKED, "value": None, "reason": "link a Split Sheet step into its parts input",
                "setup": SETUP_LINK}
    state = states[upstream[0]]
    if state["state"] == BLOCKED:
        return _blocked_by(upstream[0], state)
    return {"state": WAITING, "value": None, "reason": f"its parts appear once '{upstream[0]}' has split the sheet",
            "waits": [upstream[0]]}


def _blocked_by(upstream, state):
    """BLOCKED because step ``upstream`` is; set up (SETUP_UPSTREAM) when that step only lacks setup itself."""
    blocked = {"state": BLOCKED, "value": None, "reason": f"'{upstream}': {state.get('reason')}"}
    return dict(blocked, setup=SETUP_UPSTREAM) if state.get("setup") else blocked


def evaluate_character(flow, node, states):
    """Place Character: ready once the body's model is chosen; parts still missing are named."""
    parts = inputs_of(flow, node["id"], "parts")
    models = inputs_of(flow, node["id"], "models")
    if not parts or not models:
        return {"state": BLOCKED, "value": None, "setup": SETUP_LINK,
                "reason": "link the Split Sheet into parts and the per-part 3D step into models"}
    split = states[parts[0]]
    if split["state"] == BLOCKED:
        return _blocked_by(parts[0], split)
    if split["state"] != READY:
        return {"state": WAITING, "value": None, "reason": f"waits for '{parts[0]}'", "waits": [parts[0]]}
    entries = {entry["id"]: entry for entry in flow["nodes"]}
    by_part = {}
    for member in models:
        state, part = states.get(member) or {}, (entries.get(member) or {}).get("part")
        if part and state.get("chosen"):
            by_part[part] = state["chosen"]
    manifest = split["value"]
    missing = [p["id"] for p in manifest["parts"] if p["id"] not in by_part]
    body = next((p["id"] for p in manifest["parts"] if p["kind"] == "BODY"), None)
    if body is None or body not in by_part:
        return {"state": WAITING, "value": None, "reason": "waits for the body's 3D model"}
    value = {"parts": manifest["parts"], "cell_height": manifest.get("cell_height"), "models": by_part,
             "height": node["height"], "grounding": manifest.get("grounding")}
    return {"state": READY, "value": value,
            "reason": ("these parts aren't ready and are left out: " + ", ".join(missing)) if missing else None}
