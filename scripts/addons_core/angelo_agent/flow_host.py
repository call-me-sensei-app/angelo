# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread runtime for Generation Flows: evaluation cache, previews, captures and runs.

Everything here runs on Blender's main thread (a persistent timer and
operators). Provider work goes through the existing Media Studio jobs, whose
workers never touch ``bpy``. Node draw functions read only what ``pump``
prepared: node states and preview icon IDs.

Runs are not saved. After a restart or file load, a run that was in progress
is gone; its started generations remain Studio assets, and the flow shows
their steps as RUNNING, READY or FAILED from the library. Nothing is resubmitted.

Subflow nodes are expanded (``flow_subflows``) before evaluation, so states,
plans and runs name a recipe's steps ``<subflow>/<step>``. Their per-use state
(chosen output, last key, capture) lives on the Subflow node; ``flow_steps``
resolves a step name to where its settings and state are kept.
"""

import hashlib
import time

import bpy
from bpy.utils import previews as bpy_previews

from . import flow_nodes, flow_tree
from .flow_graph import LibraryIndex, connection_needs, evaluate, request_for
from .flow_runner import DONE, FlowRun
from .flow_spec import GENERATOR_TYPES, FlowError
from .flow_steps import (flow_trees, per_step, recipe_trees, slot_get, slot_set, step_slot,
                         subflow_summaries)
from .flow_subflows import expand

PUMP_SECONDS = 0.25
IDLE_REFRESH_SECONDS = 2.0
MAX_PREVIEWS = 256
CAPTURE_REUSE_SECONDS = 900

_cache = {}        # tree name -> {"definition", "flow" (expanded), "states", "subflows", "error", "at"}
_dirty = set()
_runs = {}         # run id -> {"run", "tree", "owner", "flow_id"}
_previews = None
_preview_ids = {}  # asset id -> icon id
_scene_changes = 0
_captures = {}     # (tree name, node name) -> (scene change count, monotonic time)
_last_refresh = 0.0


# --- Catalog and library -----------------------------------------------------

def catalog_entries(kind):
    from . import studio, ui
    return [dict(entry, kind=kind) for entry in studio.model_options(kind, ui.generation_connections(), ui.generation_health())]


def connected_kinds():
    """The kinds ("IMAGE", "3D") with at least one connected generation account and model."""
    return {kind for kind in ("IMAGE", "3D") if any(entry["ready"] for entry in catalog_entries(kind))}


def catalog(connection_id, model):
    """The catalog entry for an exact account/model, with its kind and reference limit."""
    from .studio_agent import capabilities
    for kind in ("IMAGE", "3D"):
        for entry in catalog_entries(kind):
            if entry["connection"] == connection_id and entry["id"] == model:
                return dict(entry, references_max=capabilities(entry["mode"], model)["references_max"])
    return None


def library():
    from . import studio
    return studio.library()


def jobs():
    from . import studio
    library()
    return studio._jobs


def asset_title(identity):
    asset = library().assets.get(identity)
    return (asset.get("prompt") or asset.get("model") or "Image")[:60] if asset else "Missing library image"


# --- Evaluation cache --------------------------------------------------------

def evaluate_tree(tree, index=None, definition=None):
    """(expanded flow, states) of a tree now. Raises FlowError for a tree that can't be read."""
    definition = definition or flow_tree.tree_flow(tree)
    recipes = {}
    for node in tree.nodes:
        if node.bl_idname == flow_tree.SUBFLOW_NODE and node.recipe and node.recipe.name not in recipes:
            try:
                recipe = flow_tree.tree_flow(node.recipe)
            except FlowError:
                continue  # Expansion reports the use as blocked.
            # Without a usable interface, expansion explains what's missing.
            recipes[node.recipe.name] = recipe if flow_tree.recipe_interface(node.recipe) else dict(recipe, recipe=None)
    flow = expand(definition, recipes)
    states = evaluate(flow, index or LibraryIndex(library().assets), catalog=catalog,
                      captures=per_step(tree, "capture_asset", ("SCENE_CAPTURE",)),
                      last_keys=per_step(tree, "last_key", GENERATOR_TYPES))
    return flow, states


def refresh(tree, index=None):
    try:
        definition = flow_tree.tree_flow(tree)
        flow, states = evaluate_tree(tree, index, definition)
        _cache[tree.name] = {"definition": definition, "flow": flow, "states": states,
                             "subflows": subflow_summaries(flow, states), "error": None, "at": time.monotonic(),
                             "recipe_problem": flow_tree.recipe_problem(tree),
                             "needs": connection_needs(flow, catalog, connected=connected_kinds())}
    except FlowError as error:
        _cache[tree.name] = {"definition": None, "flow": None, "states": {}, "subflows": {}, "error": str(error),
                             "at": time.monotonic()}
    _dirty.discard(tree.name)
    return _cache[tree.name]


def node_state(tree, name):
    """A node's state; for a Subflow node, its summary. Draw-safe: reads the prepared cache."""
    entry = _cache.get(tree.name)
    if not entry:
        return None
    return entry["states"].get(name) or entry["subflows"].get(name)


def subflow_summary(tree, name):
    entry = _cache.get(tree.name)
    return entry["subflows"].get(name) if entry else None


def set_chosen(tree, name, asset_id):
    """Set a generator step's chosen output (only its current outputs are allowed)."""
    entry = refresh(tree)
    state = entry["states"].get(name)
    if not state or "outputs" not in state:
        raise FlowError(f"'{name}' isn't a generator step in this flow")
    if asset_id not in state["outputs"]:
        raise FlowError(f"That asset isn't a current output of '{name}'; inspect the flow for its outputs")
    slot_set(step_slot(tree, entry["flow"], name), "chosen", asset_id)
    tree_changed(tree)


def tree_changed(tree):
    """Called from NodeTree.update; the next pump re-evaluates. Never heavy work here."""
    _dirty.add(tree.name)


LABELS = {"READY": "Ready", "PARTIAL": "Some variants ready", "MISSING": "Not generated yet",
          "STALE": "Out of date", "WAITING": "Waiting for inputs", "RUNNING": "Generating…",
          "FAILED": "Failed", "BLOCKED": "Can't run"}
ICONS = {"READY": 'CHECKMARK', "PARTIAL": 'INFO', "MISSING": 'DOT', "STALE": 'FILE_REFRESH',
         "WAITING": 'TIME', "RUNNING": 'SORTTIME', "FAILED": 'ERROR', "BLOCKED": 'CANCEL'}


def reason_lines(reason, width):
    """A state's reason split into lines of about ``width`` characters, at most three."""
    if not reason:
        return []
    lines, current = [], ""
    for word in reason.split():
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    lines.append(current)
    return lines[:3]


def state_label(state):
    label = LABELS.get(state["state"], state["state"].title())
    return label + (": " + state["reason"] if state.get("reason") else "")


def state_icon(name):
    return ICONS.get(name, 'DOT')


# --- Previews ----------------------------------------------------------------

def preview_icon(identity):
    """Icon ID of a prepared preview, or 0. Draw-safe: only reads the prepared table."""
    return _preview_ids.get(identity, 0)


def _prepare_previews(identities):
    global _previews
    if bpy.app.background:
        return
    if _previews is None:
        _previews = bpy_previews.new()
    store = library()
    for identity in identities:
        if not identity or identity in _preview_ids:
            continue
        asset = store.assets.get(identity)
        if not asset or asset.get("status") != "ready":
            continue
        # 3D outputs preview their provider thumbnail; images preview themselves.
        filename = asset.get("thumbnail") or ((asset.get("files") or [""])[0] if asset.get("kind") == "IMAGE" else "")
        if not filename:
            continue
        if len(_preview_ids) >= MAX_PREVIEWS:
            _previews.clear()
            _preview_ids.clear()
        try:
            path = store.checked_file(asset, filename)
            _preview_ids[identity] = _previews.load(identity, str(path), 'IMAGE').icon_id
        except (ValueError, OSError, KeyError, RuntimeError):
            continue


def _visible_assets(entry, tree):
    identities = []
    for state in entry["states"].values():
        identities.extend(state.get("outputs") or [])
    for node in tree.nodes:
        identities.extend(filter(None, (getattr(node, "asset_id", ""), getattr(node, "capture_asset", ""))))
        if node.bl_idname == flow_tree.SUBFLOW_NODE:
            identities.extend(node.inner("capture_asset").values())
    return identities


# --- Scene capture -----------------------------------------------------------

def _scene_updated(scene, depsgraph):
    """Count edits that can change a capture; this flow's own node edits don't."""
    global _scene_changes
    for update in depsgraph.updates:
        if not (isinstance(update.id, bpy.types.NodeTree) and update.id.bl_idname == flow_nodes.TREE):
            _scene_changes += 1
            return


def capture_inputs(tree, window, names):
    """Capture the named Scene Capture nodes into the library, reusing unchanged captures.

    A capture is a free, local render; it becomes an imported Studio image so
    its pixels are what generators reference. An unchanged scene keeps the
    previous capture (same asset), so downstream steps stay reusable.
    """
    from . import scene_queries, studio, view_capture
    store = library()
    if names:
        # Evaluate pending edits now so their update handlers count them before
        # a capture is reused; an edit made in this same tick must invalidate it.
        window.view_layer.update()
    flow = evaluate_tree(tree)[0] if names else None
    for name in names:
        slot = step_slot(tree, flow, name)
        node = slot[0] if slot else None
        if node is None or node.bl_idname != flow_nodes.ANGELO_FlowSceneCaptureNode.bl_idname:
            continue
        stamp, previous = _captures.get((tree.name, name)), slot_get(slot, "capture_asset")
        if (previous and previous in store.assets and stamp
                and stamp[0] == _scene_changes and time.monotonic() - stamp[1] < CAPTURE_REUSE_SECONDS):
            continue
        targets = []
        if node.subject == "SELECTION":
            targets = [scene_queries.object_handle(o) for o in window.view_layer.objects.selected][:16]
            if not targets:
                raise FlowError(f"'{name}' captures the selection; select the objects to capture first")
        if node.framing == "CAMERA" and window.scene.camera is None:
            raise FlowError(f"'{name}' uses the scene camera, and this scene has none; add one or use Current view")
        result = view_capture.execute(window, {"targets": targets, "resolution": int(node.resolution),
                                               "views": ["SCENE_CAMERA" if node.framing == "CAMERA" else "FIT"],
                                               "shading": "SOLID" if node.shading == "CLAY" else "MATERIAL"},
                                      studio._references)
        view = result["views"][0]
        try:
            with open(view["path"], "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            if digest != slot_get(slot, "capture_hash") or previous not in store.assets:
                asset = store.import_image(view["path"])
                asset.update(prompt=f"Scene capture · {tree.name} · {name}",
                             lineage={"source": "capture", "flow_id": flow_tree.flow_id(tree), "node": name})
                store.update(asset)
                slot_set(slot, "capture_asset", asset["id"])
                slot_set(slot, "capture_hash", digest)
        finally:
            studio._references.discard_observation(view)
        _captures[(tree.name, name)] = (_scene_changes, time.monotonic())


def needed_captures(flow, scope_nodes):
    """Scene Capture nodes feeding any generator in scope."""
    from .flow_spec import ancestors
    nodes = {n["id"]: n for n in flow["nodes"]}
    needed = set()
    for identity in scope_nodes:
        if nodes[identity]["type"] in GENERATOR_TYPES:
            needed |= {a for a in ancestors(flow, identity) | {identity} if nodes[a]["type"] == "SCENE_CAPTURE"}
    return sorted(needed)


# --- Runs --------------------------------------------------------------------

class StudioFlowHost:
    """``FlowRun`` adapter over the Media Studio library and jobs."""

    def __init__(self, tree_name):
        self.tree_name = tree_name

    def tree(self):
        tree = bpy.data.node_groups.get(self.tree_name)
        if tree is None or tree.bl_idname != flow_nodes.TREE:
            raise FlowError("The flow was renamed, deleted or replaced by a file load; nothing more was started")
        return tree

    def request(self, line):
        flow, states = evaluate_tree(self.tree())
        return request_for(flow, states, line["node"])

    def next_variant(self, key):
        records = LibraryIndex(library().assets).by_key.get(key, [])
        return 1 + max(((r.get("lineage") or {}).get("variant", -1) for r in records), default=-1)

    def start(self, line, spec, key, variant):
        from .studio_agent import StudioAgent
        from . import ui
        store, workers = library(), jobs()
        agent = StudioAgent(store, workers, lambda: [e for k in ("IMAGE", "3D") for e in catalog_entries(k)],
                            ui.generation_binding, None, None, None)
        references = [{"source": "ASSET" if kind == "asset" else "URL", "id": value} for kind, value in spec["references"]]
        args = {"connection_id": spec["connection_id"], "model": spec["model"], "prompt": spec["prompt"],
                "options": spec["options"], "references": references}
        choice, binding, options, refs = agent.generation(args)
        if workers.closed or len(workers.active) >= 4:
            raise FlowError("Media Studio is already running four generations; wait for one to finish")
        tree = self.tree()
        kind = "3D" if line["type"] == "GENERATE_3D" else "IMAGE"
        asset = store.add(kind, spec["prompt"], choice["name"], choice["connection"], flow_folder(store, tree.name), mode=choice["mode"],
                          model_id=choice["id"], options=options, reference_names=[r["name"] for r in refs],
                          lineage={"source": "flow", "flow_id": flow_tree.flow_id(tree), "flow": tree.name,
                                   "node": line["node"], "key": key, "variant": variant,
                                   "reference_ids": [value for kind_, value in spec["references"] if kind_ == "asset"]})
        try:
            workers.start(asset, binding, options, refs)
        except Exception:
            store.update(dict(asset, status="failed", error="Job could not start; no automatic retry"))
            raise
        return asset["id"]

    def asset(self, identity):
        return library().assets.get(identity)

    def capacity(self):
        workers = jobs()
        return 0 if workers.closed else 4 - len(workers.active)

    def chosen(self, node, key):
        _, states = evaluate_tree(self.tree())
        state = states.get(node) or {}
        return state.get("chosen") if state.get("key") == key else None

    def stop(self, identity):
        jobs().stop(identity)


def flow_folder(store, flow_name):
    """The Studio folder a flow's outputs go to, so they can be found again in the library."""
    name = ("Flow · " + flow_name)[:100]
    existing = next((f["id"] for f in store.folders.values() if f.get("name") == name), None)
    return existing or store.folder(name)["id"]


def start_run(tree, selection, owner):
    """Start the approved ``now`` lines of a selection. The caller has the user's consent."""
    run = FlowRun(tree.name, selection["now"], StudioFlowHost(tree.name), later=selection["later"],
                  stops=selection["stops"])
    _runs[run.id] = {"run": run, "tree": tree.name, "owner": owner, "flow_id": flow_tree.flow_id(tree)}
    run.tick()
    _after_tick(run)
    _dirty.add(tree.name)
    return run


def run_for(run_id):
    record = _runs.get(run_id)
    return record["run"] if record else None


def active_runs(tree_name=None):
    return [r["run"] for r in _runs.values() if not r["run"].finished and (tree_name is None or r["tree"] == tree_name)]


def stop_run(run_id):
    run = run_for(run_id)
    if run and not run.finished:
        run.stop()
        _dirty.add(_runs[run_id]["tree"])


def _after_tick(run):
    """Record finished steps on their nodes: the key they ran with, and a valid choice."""
    tree = bpy.data.node_groups.get(run.flow_name)
    if tree is None:
        return
    flow = None
    for name, step in run.steps.items():
        if step["state"] != DONE or not step["key"]:
            continue
        try:
            flow = flow or evaluate_tree(tree)[0]
        except FlowError:
            return
        slot = step_slot(tree, flow, name)
        if slot is None:
            continue
        slot_set(slot, "last_key", step["key"])
        chosen = slot_get(slot, "chosen")
        if step["chosen"] and chosen != step["chosen"] and chosen not in step["assets"]:
            slot_set(slot, "chosen", step["chosen"])


# --- Timer -------------------------------------------------------------------

def pump():
    global _last_refresh
    try:
        if jobs().pump():
            _dirty.update(t.name for t in flow_trees())
        for record in list(_runs.values()):
            run = record["run"]
            if run.finished:
                continue
            if run.tick():
                _after_tick(run)
                _dirty.add(record["tree"])
        now = time.monotonic()
        trees = flow_trees()
        due = now - _last_refresh >= IDLE_REFRESH_SECONDS
        if due or _dirty & {tree.name for tree in recipe_trees()}:
            # A recipe may have changed: Subflow uses elsewhere need matching sockets and new states.
            for tree in trees:
                if any(node.bl_idname == flow_tree.SUBFLOW_NODE for node in tree.nodes):
                    flow_tree.sync_subflows(tree)
                    _dirty.add(tree.name)
        if _dirty or due:
            index = LibraryIndex(library().assets)
            for tree in trees:
                if due or tree.name in _dirty:
                    entry = refresh(tree, index)
                    _prepare_previews(_visible_assets(entry, tree))
            _last_refresh = now
            _redraw()
        for name in [n for n in _cache if n not in {t.name for t in trees}]:
            _cache.pop(name, None)
    except Exception as error:  # A timer that raises is unregistered; keep flows alive.
        print("Angelo: Generation Flow update failed:", error)
    return PUMP_SECONDS


def _redraw():
    if bpy.app.background:
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'NODE_EDITOR' and getattr(area.spaces.active, "tree_type", "") == flow_nodes.TREE:
                area.tag_redraw()
            elif area.type == 'CONSOLE':
                area.tag_redraw()


def _load_pre(*_):
    """A file load replaces Main: drop runs and caches that pointed into the old one."""
    for record in _runs.values():
        if not record["run"].finished:
            record["run"].stop()
    _runs.clear()
    _cache.clear()
    _captures.clear()


def register():
    flow_nodes.register()
    bpy.app.handlers.depsgraph_update_post.append(_scene_updated)
    bpy.app.handlers.load_pre.append(_load_pre)
    bpy.app.timers.register(pump, first_interval=PUMP_SECONDS, persistent=True)


def unregister():
    global _previews
    if bpy.app.timers.is_registered(pump):
        bpy.app.timers.unregister(pump)
    if _scene_updated in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_scene_updated)
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    _load_pre()
    if _previews is not None:
        bpy_previews.remove(_previews)
        _previews = None
    _preview_ids.clear()
    flow_nodes.unregister()
