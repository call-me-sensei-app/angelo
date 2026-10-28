# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Step states, input keys and run plans for Generation Flows. No Blender dependency.

Every generator step has an **input key**: a hash of its account, model,
options, composed prompt and the exact assets it reads. Library outputs record
the key that produced them, so a step whose key already has ready outputs is
reused for free, and editing anything upstream changes the key and makes the
step (and everything after it) stale. Nothing reruns on its own; only an
approved plan generates.

The planner never contacts a provider. Prices come from ``flow_prices`` and are
estimates; the enforced limit of an approval is its list of generations.
"""

import hashlib
import json

from .flow_spec import (GENERATOR_TYPES, REFERENCE_INPUT, RUNS_AS, FlowError, ancestors, descendants,
                        inputs_of, topological_order)

MAX_PAID_PER_RUN = 16
KEY_VERSION = 1
# The parts-sheet split's own rules (flow_parts' thresholds and steps). Part of every split key, so splitting a sheet
# again after they change makes new crops instead of reusing ones cut by the earlier rules (flow_split_host reuses
# crops by key). test_native_flow_split_reuse pins it to the split's code (flow_parts, and how flow_split_host reads
# the sheet and writes crops): change it, bump this.
SPLIT_ALGORITHM = 2

READY, PARTIAL, MISSING, STALE = "READY", "PARTIAL", "MISSING", "STALE"
WAITING, RUNNING, FAILED, BLOCKED = "WAITING", "RUNNING", "FAILED", "BLOCKED"
STOPPED = "STOPPED"   # The person stopped its last attempt: not a failure (1.0.5), shown neutrally.

# A BLOCKED state's ``setup`` says the step only lacks something nobody has filled in yet (a fresh template, a new
# node): an account or model, a prompt, an image, a link, a Subflow's recipe, or the same for a step before it. The
# interface shows that as what to do next, not as a failure (``flow_setup``). A block the person's own choices caused
# (a model of the wrong kind, too many references, a missing library image, a broken recipe) has no ``setup``.
# Reasons stay as they are.
SETUP_MODEL, SETUP_PROMPT, SETUP_PROMPT_OR_REFERENCE = "model", "prompt", "prompt_or_reference"
SETUP_IMAGE, SETUP_LINK, SETUP_UPSTREAM = "image", "link", "upstream"
SETUP_RECIPE = "recipe"


def _hash(value, length=24):
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:length]


def compose_prompt(node, texts):
    """The node's own prompt, then linked Text fragments in link order."""
    parts = [node.get("prompt", "").strip()] + [t.strip() for t in texts]
    return "\n\n".join(part for part in parts if part)


def input_key(node, prompt, references):
    """Identity of one generation request. ``references`` are ("asset", id) or ("url", url)."""
    body = {"v": KEY_VERSION, "type": node["type"], "connection": node["connection_id"], "model": node["model"],
            "options": node["options"], "prompt": prompt, "references": [list(r) for r in references]}
    if node.get("take"):
        body["take"] = node["take"]  # Redo: a new key; the first take keeps every existing key.
    return _hash(body)


class LibraryIndex:
    """Flow lineage of Studio assets, grouped by input key.

    ``assets`` is the Studio library's ``{id: record}``. Only records with a
    ``lineage.key`` take part in reuse; imported images are plain sources.
    """

    def __init__(self, assets):
        self.assets = assets
        self.by_key = {}
        for asset in assets.values():
            key = (asset.get("lineage") or {}).get("key")
            if key:
                self.by_key.setdefault(key, []).append(asset)
        for records in self.by_key.values():
            records.sort(key=lambda a: ((a.get("lineage") or {}).get("variant", 0), a.get("created", 0)))

    def ready(self, key):
        return [a for a in self.by_key.get(key, ()) if a.get("status") == "ready" and a.get("files")]

    def running(self, key):
        return [a for a in self.by_key.get(key, ()) if a.get("status") not in {"ready", "failed", "stopped"}]

    def failed(self, key):
        records = self.by_key.get(key, ())
        return bool(records) and not self.ready(key) and records[-1].get("status") in {"failed", "stopped"}

    def stopped(self, key):
        """Whether the last attempt for ``key`` ended because the person stopped it (not because it failed)."""
        records = self.by_key.get(key, ())
        return self.failed(key) and records[-1].get("status") == "stopped"

    def image(self, identity):
        asset = self.assets.get(identity)
        return asset if asset and asset.get("kind") == "IMAGE" and asset.get("status") == "ready" and asset.get("files") else None


def chosen_output(node, ready):
    """The node's choice when it is still one of its current outputs, else the first."""
    ids = [a["id"] for a in ready]
    if node.get("chosen") in ids:
        return node["chosen"]
    return ids[0] if ids else None


def _source_value(node, index, captures):
    if node["type"] == "IMAGE":
        source = node["source"]
        if source is None:
            return None, "pick an image"
        if source["kind"] == "URL":
            return ("url", source["id"]), None
        if index.image(source["id"]):
            return ("asset", source["id"]), None
        return None, "its library image is missing or not ready"
    if node["type"] == "SCENE_CAPTURE":
        identity = captures.get(node["id"])
        return (("asset", identity), None) if identity else (None, "the scene is captured when the run starts")
    if node["type"] == "TEXT":
        return node["text"], None
    return None, "not a source"


def _check_model(node, catalog):
    """(reason this generator can't run with its account/model, its SETUP code or None), or (None, None)."""
    if not node["connection_id"] or not node["model"]:
        return "choose an account and model", SETUP_MODEL
    entry = catalog(node["connection_id"], node["model"])
    if entry is None:
        return "its account or model isn't in the catalog; choose one from angelo_studio_catalog", SETUP_MODEL
    wanted = "IMAGE" if node["type"] == "GENERATE_IMAGE" else "3D"
    if entry.get("kind") != wanted:
        return f"{entry.get('name', node['model'])} makes {entry.get('kind', 'other')} output, not {wanted}", None
    if not entry.get("ready"):
        return "connect or check this account in AI Providers", SETUP_MODEL
    limit = entry.get("references_max")
    if limit is not None and node.get("_reference_count", 0) > limit:
        return f"{entry.get('name', node['model'])} accepts at most {limit} reference image(s)", None
    return None, None


# What a generator step lacks to run, for the interface only (the step's reason above is what a model reads):
# no connected generation account of its kind, its chosen account not connected, or no choice while one is.
CONNECT, RECONNECT, CHOOSE = "connect", "reconnect", "choose"


def connection_needs(flow, catalog, *, connected):
    """``{step: CONNECT | RECONNECT | CHOOSE}`` for the steps that run on an account (``RUNS_AS``) and lack one.

    ``connected`` holds the kinds ("IMAGE", "3D") with at least one connected catalog entry. Pure.
    """
    needs = {}
    for node in flow["nodes"]:
        if node["type"] not in RUNS_AS:
            continue
        kind = "IMAGE" if RUNS_AS[node["type"]] == "GENERATE_IMAGE" else "3D"
        entry = catalog(node["connection_id"], node["model"]) if node["connection_id"] and node["model"] else None
        if entry is None or entry.get("kind") != kind:
            needs[node["id"]] = CHOOSE if kind in connected else CONNECT
        elif not entry.get("ready"):
            needs[node["id"]] = RECONNECT
    return needs


def need_for(needs, name):
    """A node's need: its own, or for a Subflow use the most pressing of its steps (``<use>/<step>``)."""
    if name in needs:
        return needs[name]
    inner = {need for step, need in needs.items() if step.startswith(name + "/")}
    return next((need for need in (CONNECT, RECONNECT, CHOOSE) if need in inner), None)


def connect_steps(run_plan, needs):
    """The plan's blocked lines whose step needs a generation account connected (not merely chosen)."""
    return [line for line in run_plan["lines"]
            if line["action"] == BLOCKED and needs.get(line["node"]) in {CONNECT, RECONNECT}]


def split_key(sheet_asset, node):
    """Identity of one split: the sheet asset and the layout. The host keeps one split per key."""
    return _hash({"v": KEY_VERSION, "split": SPLIT_ALGORITHM, "sheet": sheet_asset, "rows": node["rows"],
                  "slots": node["slots"]})


def evaluate(flow, index, *, catalog, captures=None, last_keys=None, splits=None):
    """The current state of every node, from the library alone. Pure; no generation.

    Returns ``{node_id: {state, key, outputs, chosen, reason, prompt, references}}``.
    ``splits`` maps Split Sheet node names to the host's stored split
    ``{key, parts}`` (or ``{key, error}``); a split for another sheet or layout is ignored.
    """
    from . import flow_graph_parts as parts   # The character-parts steps (it imports this module's names).
    captures, last_keys, splits = captures or {}, last_keys or {}, splits or {}
    nodes = {n["id"]: n for n in flow["nodes"]}
    result = {}
    for identity in topological_order(flow):
        node = nodes[identity]
        if node["type"] == "SUBFLOW":  # Only a Subflow that couldn't be expanded is left (flow_subflows).
            result[identity] = {"state": BLOCKED, "value": None, "reason": node.get("problem") or "its recipe can't be used"}
            if not node.get("recipe"):   # A new Subflow node: nobody has chosen its recipe yet.
                result[identity]["setup"] = SETUP_RECIPE
            continue
        if node["type"] == "SPLIT_SHEET":
            result[identity] = parts.evaluate_split(flow, node, result, splits)
            continue
        if node["type"] == "GENERATE_3D_EACH":
            result[identity] = parts.evaluate_unexpanded_parts(flow, node, catalog, result)
            continue
        if node["type"] == "PLACE_CHARACTER":
            result[identity] = parts.evaluate_character(flow, node, result)
            continue
        if node["type"] not in GENERATOR_TYPES and node["type"] != "PLACE_IN_SCENE":
            value, reason = _source_value(node, index, captures)
            result[identity] = {"state": READY if value is not None else (WAITING if node["type"] == "SCENE_CAPTURE" else BLOCKED),
                                "value": value, "reason": reason}
            if node["type"] == "IMAGE" and node["source"] is None:
                result[identity]["setup"] = SETUP_IMAGE
            continue
        if node["type"] == "PLACE_IN_SCENE":
            upstream = inputs_of(flow, identity, "model")
            state = result.get(upstream[0]) if upstream else None
            chosen = state.get("chosen") if state else None
            reason = None if chosen else f"waits for '{upstream[0]}'" if upstream else "link a Generate 3D step into its model input"
            result[identity] = {"state": READY if chosen else (WAITING if upstream else BLOCKED), "value": chosen, "reason": reason}
            if upstream and not chosen:
                result[identity]["waits"] = [upstream[0]]   # The steps it waits for, for the interface's one line.
            if not upstream:
                result[identity]["setup"] = SETUP_LINK
            continue
        result[identity] = _evaluate_generator(flow, node, index, catalog, result, last_keys)
    return result


def _upstream(flow, node, states):
    """(texts, references, waiting_on, blocked_reason, setup) from upstream node states; ``setup`` is
    SETUP_UPSTREAM when the block comes from an earlier step that only lacks setup."""
    texts, references, waiting, blocked, setup = [], [], [], None, None
    for upstream in inputs_of(flow, node["id"], "prompt"):
        state = states[upstream]
        if state.get("value") is None:
            if blocked is None:
                blocked, setup = f"needs '{upstream}'", SETUP_UPSTREAM if state.get("setup") else None
        else:
            texts.append(state["value"])
    for upstream in inputs_of(flow, node["id"], REFERENCE_INPUT[node["type"]]):
        state = states[upstream]
        if "chosen" in state:  # A generator upstream.
            if state["chosen"]:
                references.append(("asset", state["chosen"]))
            else:
                waiting.append(upstream)
        elif state.get("value") is not None:
            references.append(state["value"])
        elif state["state"] == WAITING:
            waiting.append(upstream)
        elif blocked is None:
            blocked, setup = f"'{upstream}': {state.get('reason')}", SETUP_UPSTREAM if state.get("setup") else None
    return texts, references, waiting, blocked, setup


def _evaluate_generator(flow, node, index, catalog, states, last_keys):
    texts, references, waiting, blocked, upstream_setup = _upstream(flow, node, states)
    reference_count = len(inputs_of(flow, node["id"], REFERENCE_INPUT[node["type"]]))
    problem, setup = _check_model(dict(node, _reference_count=reference_count), catalog)
    prompt = compose_prompt(node, texts)
    entry = {"key": None, "outputs": [], "chosen": None, "prompt": prompt, "references": references}
    if node["type"] == "GENERATE_IMAGE" and not prompt and not blocked and not problem:
        problem, setup = "add a prompt or link a Text node", SETUP_PROMPT
    if node["type"] == "GENERATE_3D" and not prompt and not reference_count and not problem:
        problem, setup = "add a prompt or link a reference image", SETUP_PROMPT_OR_REFERENCE
    if problem or blocked:
        # Blocked regardless of what upstream steps do in a run.
        blocked_state = dict(entry, state=BLOCKED, reason=problem or blocked, fixed_block=True)
        setup = setup if problem else upstream_setup
        return dict(blocked_state, setup=setup) if setup else blocked_state
    if waiting:
        return dict(entry, state=WAITING, reason="waits for " + ", ".join(f"'{w}'" for w in waiting), waits=list(waiting))
    key = input_key(node, prompt, references)
    ready = index.ready(key)
    entry.update(key=key, outputs=[a["id"] for a in ready], chosen=chosen_output(node, ready))
    if index.running(key):
        return dict(entry, state=RUNNING, reason=None)
    if len(ready) >= node["variants"]:
        return dict(entry, state=READY, reason=None)
    if ready:
        return dict(entry, state=PARTIAL, reason=f"{len(ready)} of {node['variants']} variants exist")
    if index.stopped(key):
        return dict(entry, state=STOPPED, reason="the last attempt was stopped; running again is a new paid generation")
    if index.failed(key):
        return dict(entry, state=FAILED, reason="the last attempt failed; running again is a new paid generation")
    previous = last_keys.get(node["id"])
    if previous and previous != key and index.ready(previous):
        return dict(entry, state=STALE, reason="its inputs or settings changed since it last ran")
    return dict(entry, state=MISSING, reason=None)


def scope_nodes(flow, scope, node=None):
    """Node names in a run scope. A name that expanded into several steps stands for all of them: a Subflow's
    (``subflow``) and a per-part node's once its sheet is split (``fanout``, ``flow_fanout.expand_parts``)."""
    nodes = {n["id"] for n in flow["nodes"]}
    if scope == "ALL":
        return nodes
    group = {node} if node in nodes else {n["id"] for n in flow["nodes"]
                                           if node and node in (n.get("subflow"), n.get("fanout"))}
    if not group:
        raise FlowError(f"There's no node named '{node}' in this flow")
    if scope == "NODE":
        return group
    related = descendants if scope == "DOWNSTREAM" else ancestors
    return group.union(*(related(flow, member) for member in group))


def plan(flow, states, *, scope="ALL", node=None, price=None, accounts=None):
    """Every generator in scope, with what a run would do. Pure; no selection applied.

    A generator generates when it is missing outputs, or when an upstream step
    in the same plan generates (its references will change). Lines carry an
    ``approval`` record: the exact request except references produced by this
    run, which are named ``("run", node)`` and bound when they finish.
    """
    price, accounts = price or (lambda node: None), accounts or {}
    nodes = {n["id"]: n for n in flow["nodes"]}
    selected = scope_nodes(flow, scope, node)
    lines, generating = [], set()
    for identity in topological_order(flow):
        current = nodes[identity]
        if identity not in selected or current["type"] not in GENERATOR_TYPES:
            continue
        state = states[identity]
        upstream_generators = [u for u in inputs_of(flow, identity, REFERENCE_INPUT[current["type"]]) if u in generating]
        line = {"node": identity, "label": current.get("label") or identity, "type": current["type"],
                "model": current["model"], "connection_id": current["connection_id"],
                "account": accounts.get((current["connection_id"], current["model"]), current["model"]),
                "options": current["options"], "variants": current["variants"], "review": current["review"],
                "depends_on": upstream_generators, "reason": state.get("reason"),
                "price_each": price(dict(current, _references=len(inputs_of(flow, identity, REFERENCE_INPUT[current["type"]]))))}
        if state.get("fixed_block"):
            lines.append(dict(line, action="BLOCKED", count=0))
            continue
        if state["state"] == RUNNING:
            lines.append(dict(line, action="RUNNING", count=0))
            continue
        if upstream_generators:
            # Its references change when the upstream finishes: all variants are new.
            references = [("run", u) if u in generating else ref for u, ref in _reference_sources(flow, current, states)]
            if any(ref is None for ref in references):
                lines.append(dict(line, action="BLOCKED", count=0, reason="an input outside this run has no output"))
                continue
            count, why = current["variants"], "runs after " + ", ".join(f"'{u}'" for u in upstream_generators)
        elif state["state"] == WAITING:
            lines.append(dict(line, action="BLOCKED", count=0, reason=state["reason"] + " (outside this run)"))
            continue
        else:
            references = state["references"]
            count = current["variants"] - len(state["outputs"])
            why = {MISSING: "new", STALE: "out of date", PARTIAL: "missing variants", FAILED: "retry after failure",
                   STOPPED: "run again after a stop"}.get(state["state"], "new")
        if count <= 0:
            lines.append(dict(line, action="REUSE", count=0, reason="unchanged; reusing its output"))
            continue
        generating.add(identity)
        approval = {"node": identity, "type": current["type"], "connection_id": current["connection_id"],
                    "model": current["model"], "options": current["options"],
                    "prompt": state.get("prompt") if state.get("prompt") is not None else compose_prompt(current, []),
                    "references": [list(r) for r in references], "count": count}
        lines.append(dict(line, action="GENERATE", count=count, reason=why, approval=approval,
                          approval_digest=_hash(approval)))
    return {"scope": scope, "node": node, "lines": lines, "digest": _hash([l.get("approval_digest") or [l["node"], l["action"]] for l in lines])}


def _reference_sources(flow, node, states):
    """(upstream id, current reference value or None) for a generator's reference input."""
    result = []
    for upstream in inputs_of(flow, node["id"], REFERENCE_INPUT[node["type"]]):
        state = states[upstream]
        if "chosen" in state:
            result.append((upstream, ("asset", state["chosen"]) if state["chosen"] else None))
        else:
            result.append((upstream, state.get("value")))
    return result


def select(run_plan, flow, *, excluded=(), stops=None):
    """Apply the user's card choices: unticked steps and review-stop toggles.

    Unticking a step also leaves out every planned step after it. A review stop
    on a generating step defers the planned steps after it to a later card.
    Returns ``{now, later, excluded, total, unknown_prices, count}``.
    """
    stops = stops or {}
    generating = [l for l in run_plan["lines"] if l["action"] == "GENERATE"]
    planned = {l["node"] for l in generating}
    left_out = set()
    for identity in excluded:
        if identity in planned:
            left_out |= {identity} | (descendants(flow, identity) & planned)
    deferred = set()
    for line in generating:
        stop = stops.get(line["node"], line["review"])
        if stop and line["node"] not in left_out:
            deferred |= descendants(flow, line["node"]) & planned
    now = [l for l in generating if l["node"] not in left_out and l["node"] not in deferred]
    later = [l for l in generating if l["node"] not in left_out and l["node"] in deferred]
    known = [l["price_each"] * l["count"] for l in now if l["price_each"] is not None]
    return {"now": now, "later": later, "excluded": sorted(left_out), "count": sum(l["count"] for l in now),
            "total": round(sum(known), 2), "unknown_prices": sum(1 for l in now if l["price_each"] is None),
            "later_total": round(sum(l["price_each"] * l["count"] for l in later if l["price_each"] is not None), 2),
            "stops": {l["node"]: stops.get(l["node"], l["review"]) for l in generating}}


def check_selection(selection):
    if selection["count"] > MAX_PAID_PER_RUN:
        raise FlowError(f"This run has {selection['count']} paid generations; the limit is {MAX_PAID_PER_RUN}. "
                        "Run part of the flow (scope NODE, DOWNSTREAM or UPSTREAM) or add a review stop")
    if not selection["now"]:
        raise FlowError("Nothing in this run needs generating: every selected step is reused, blocked or left out")


def request_for(flow, states, node_id):
    """The exact generation request a step would send now, and its input key."""
    node = next(n for n in flow["nodes"] if n["id"] == node_id)
    state = states[node_id]
    if state.get("key") is None:
        raise FlowError(f"'{node_id}' can't run yet: " + (state.get("reason") or "its inputs aren't ready"))
    return ({"connection_id": node["connection_id"], "model": node["model"], "options": node["options"],
             "prompt": state["prompt"], "references": [list(r) for r in state["references"]]}, state["key"])
