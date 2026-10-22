# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a background job's approval says: what runs, where it writes, its bounds and its effects.

``args["_facts"]`` are the host's admission facts for this exact request
(``job_runners.Runner.describe`` via the call's precondition), so the card shows
the resolution, samples and frames the job will really use, not the model's
claim. No Blender dependency; all text is translatable.
"""

from .job_chat import BAKE_LABELS, ENGINE_NAMES, duration
from .job_spec import DEFAULT_MINUTES, LOCKING
from .ui_text import iface_, join, plural_

MAX_LISTED = 12  # Files an approval names one by one; the rest are counted.


def _output_lines(outputs):
    """One line per file the job will write, with what it is: the view, and the File Output node and item."""
    lines = []
    for output in outputs[:MAX_LISTED]:
        parts = [output["file"]]
        if output.get("node") and output.get("layers"):
            parts.append(iface_("File Output {node}: layers {layers}", node=output["node"],
                                layers=", ".join(output["layers"])))
        elif output.get("node"):
            parts.append(iface_("File Output {node}: {slot}", node=output["node"], slot=output.get("slot", "")))
        if output.get("view"):
            parts.append(iface_("View {view}", view=output["view"]))
        elif output.get("views"):
            parts.append(iface_("Views {views} in one file", views=", ".join(output["views"])))
        lines.append(join(parts))
    if len(outputs) > MAX_LISTED:
        lines.append(iface_("More files: {count}", count=len(outputs) - MAX_LISTED))
    return lines


def _extension_lines(extensions):
    """What add-on or script Python the job runs (``render_extensions``); approving it covers exactly this."""
    if not extensions:
        return []
    lines = [iface_("Runs Python from add-ons or scripts, which Angelo can't check:")]
    if extensions.get("engine"):
        lines.append(iface_("Render engine {engine}", engine=extensions["engine"]))
    if extensions.get("handlers"):
        lines.append(plural_(extensions["handler_count"], "{count} handler: {names}", "{count} handlers: {names}",
                             names=", ".join(extensions["handlers"])))
    if extensions.get("drivers"):
        lines.append(plural_(extensions["driver_count"], "{count} scripted driver: {names}",
                             "{count} scripted drivers: {names}", names=", ".join(extensions["drivers"])))
    lines.append(iface_("If the engine, handlers or drivers change before it starts, it asks again."))
    return lines


def _fluid_lines(facts):
    """What a Mantaflow bake will use and write (``job_fluid.FluidBakeRunner.describe``)."""
    cells, frames = facts["cells"], facts["frames"]
    lines = [iface_("{x}×{y}×{z} cells, {voxel} per cell, frames {start}–{end}", x=cells[0], y=cells[1], z=cells[2],
                    voxel="%g" % facts["voxel"], start=frames[0], end=frames[1]),
             iface_("Caches: {kinds}", kinds=", ".join(facts.get("cache_kinds", []))),
             iface_("About {memory} GiB of memory while it bakes, and up to {disk} GiB on disk ({free} GiB free); "
                    "it stops before the disk fills", memory=facts["memory_gib"], disk=facts["disk_gib_at_most"],
                    free=facts["disk_free_gib"]),
             iface_("Flows: {names}", names=", ".join(facts.get("flows", [])))]
    if facts.get("outside"):
        lines.append(iface_("Outside the domain: {names}", names=", ".join(facts["outside"])))
    lines.append(iface_("Bakes to disk; the domain then reads its cache from the folder below, by a path from this "
                        "file. Cancel leaves the domain's cache settings and any earlier cache as they were."))
    if facts.get("earlier_cache"):
        lines.append(iface_("The domain's earlier cache stays on disk, no longer used once this bake finishes."))
    if facts.get("replay"):
        lines.append(iface_("Its cache type changes from Replay to All, so the baked cache is what plays."))
    if facts.get("no_cache_folder"):
        lines.append(iface_("The domain has no cache folder of its own; this bake gives it one in the output."))
    return lines


def _title(args, facts):
    kind = args["kind"]
    frames = facts.get("frames") or [args.get("frame_start"), args.get("frame_end")]
    if kind == "RENDER_STILL":
        return iface_("Render frame {frame}", frame=frames[0]) if frames[0] is not None else iface_("Render a still")
    if kind == "RENDER_ANIMATION":
        if frames[0] is not None and frames[1] is not None:
            return iface_("Render frames {start}–{end}", start=frames[0], end=frames[1])
        return iface_("Render the animation")
    if kind == "BAKE_TEXTURE":
        return iface_("Bake {kind} textures", kind=iface_(BAKE_LABELS[args.get("bake_type", "COMBINED")]))
    if kind == "BAKE_PHYSICS":
        return iface_("Bake the scene's physics")
    if kind == "BAKE_SIMULATION" and facts.get("fluid"):
        return iface_("Bake a liquid simulation") if facts["fluid"] == "liquid" else iface_("Bake a smoke and fire "
                                                                                            "simulation")
    if kind == "BAKE_SIMULATION":
        return iface_("Bake geometry-nodes simulations")
    return iface_("Export USD") if kind == "EXPORT_USD" else iface_("Export Alembic")


def message(name, args, usage_label=""):
    if name == "angelo_job_cancel":
        return {"title": iface_("Cancel job {job}", job=args["job"]), "body": iface_("Stop this background job."),
                "warning": iface_("Completed animation frames are kept; everything else the job wrote is removed.")}
    facts = args.get("_facts") or {}
    lines = []
    if facts.get("resolution"):
        lines.append(iface_("{width}×{height} pixels", width=facts["resolution"][0], height=facts["resolution"][1]))
    if facts.get("samples") and facts.get("engine") in {"CYCLES", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"}:
        lines.append(plural_(facts["samples"], "{count} sample ({engine})", "{count} samples ({engine})",
                             engine=ENGINE_NAMES[facts["engine"]]))
        for layer in facts.get("layer_samples", []):
            lines.append(join((layer["name"], plural_(layer["samples"], "{count} sample", "{count} samples"))))
    if facts.get("strip_scenes"):
        lines.append(iface_("The Sequencer also renders {scenes} with their own render settings",
                            scenes=", ".join(facts["strip_scenes"])))
    lines += _extension_lines(facts.get("extensions"))
    if facts.get("sources"):
        lines.append(iface_("From {sources} onto {target}", sources=", ".join(facts["sources"]), target=facts["target"]))
        if facts.get("cage"):
            lines.append(iface_("Cage: {name}", name=facts["cage"]))
        extrusion, distance = "%g" % facts.get("extrusion", 0), "%g" % facts.get("ray_distance", 0)
        lines.append(iface_("Extrusion {extrusion}, max ray distance {distance}", extrusion=extrusion, distance=distance)
                     if facts.get("ray_distance") else
                     iface_("Extrusion {extrusion}, no max ray distance", extrusion=extrusion))
    if facts.get("images"):
        lines.append(iface_("Into: {images}", images=", ".join(facts["images"])))
        for name, tiles in (facts.get("tiles") or {}).items():
            lines.append(iface_("{image}: tiles {tiles} of {count}", image=name, count=tiles["count"],
                                tiles=", ".join(str(number) for number in tiles["baked"])))
        if facts.get("unsaved"):
            lines.append(iface_("Save {images} first if Cancel should put the tiles back.",
                                images=", ".join(facts["unsaved"])))
        if facts.get("new_image"):
            lines.append(iface_("A new {width}×{height} image; Cancel removes it.", width=facts["new_image"][0],
                                height=facts["new_image"][1]))
        else:
            lines.append(iface_("Replaces their pixels; Blender Undo can't restore them. Cancel puts them back.")
                         if facts.get("restorable") else
                         iface_("Replaces their pixels; Blender Undo can't restore them, even after Cancel."))
    if facts.get("caches"):
        lines.append(plural_(facts["caches"], "{count} physics cache", "{count} physics caches"))
    if facts.get("kept"):
        lines.append(plural_(facts["kept"], "Leaves {count} already baked cache as it is",
                             "Leaves {count} already baked caches as they are"))
    if facts.get("objects"):
        lines.append(iface_("Objects: {names}", names=", ".join(facts["objects"])))
    if facts.get("fluid"):
        lines += _fluid_lines(facts)
    elif args["kind"] == "BAKE_SIMULATION":
        lines.append(iface_("Bakes to disk; these simulations then read their cache from the folder below, by a path "
                            "from this file. An earlier packed bake is replaced.") if facts.get("disk") else
                     iface_("Replaces an earlier bake of these simulations, packed into this file"))
    if args.get("output") and facts.get("writes") and facts.get("taken"):   # Never overwritten (job_host).
        lines.append(iface_("{path} already exists and is never overwritten; this job writes {written} instead",
                            path=facts.get("requested") or args["output"], written=facts["writes"]))
    elif args.get("output") and facts.get("writes"):   # A name its export writes beside it is taken: the set moves.
        lines.append(iface_("Writes {path} in this chat's file workspace (new; nothing is overwritten)",
                            path=facts["writes"]))
    elif args.get("output"):
        lines.append(iface_("Writes {path} in this chat's file workspace (new; nothing is overwritten)",
                            path=facts.get("requested") or facts.get("output") or args["output"]))
    if args.get("output"):
        lines += _output_lines(facts.get("outputs") or [])
    if facts.get("open_checks"):   # The agent's own checks of the scene this job uses fail now: a fact, no verdict.
        lines.append(iface_("These self-checks of the agent's scripts fail now: {checks}",
                            checks="; ".join(str(line)[:200] for line in facts["open_checks"][:4])))
    minutes = args.get("minutes", DEFAULT_MINUTES[args["kind"]])
    lines.append(iface_("Time budget: {time}; it stops when the budget runs out", time=duration(60 * minutes)))
    if args.get("queue"):
        lines.append(iface_("Waits for a running job to finish first"))
    warning = iface_("Runs in the background with progress and Cancel in its chat card. The agent can't change "
                     "the scene until it ends. Render settings apply to this job only.")
    if args["kind"] in LOCKING:
        warning += " " + iface_("Blender's interface stays locked while it runs; Escape or Cancel stops it.")
    return {"title": _title(args, facts), "body": "\n".join(lines), "warning": warning}
