# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The approval cards of ``angelo_workers_start`` and ``angelo_workers_merge``: what the person approves when the lead
starts workers, and when it merges their parts into the person's scene.

Break-it P1-1 M8: the generic card said "Approve this change · workers start". The person approves processes and
model sessions billed on their connection, so the card names each part and the start of its brief, how many workers
start, what each one is, and that the parts stay in their own files. Briefs are the model's text, shown as written.

The merge card names each part, how many objects it adds, its copies and the file it goes into, and that the merge
is one Undo step. It reads the merge tool's own arguments and what Angelo knows of each worker (``merge_facts``: its
part's name and live objects); a worker it knows nothing of is named by its number. A part merged into this scene
before that the merge places again (its ``again``) says so on its line, and Auto asks for that merge instead of
running it (``worker_merge.auto_merge_allowed``; break-it E2E-20 R5-M-2: the card read as a first merge).
"""

from .ui_text import iface_, plural_

BRIEF_SHOWN = 180   # Characters of each brief on the card (the model's full brief reaches the worker unchanged).


def message(args):
    parts = args.get("parts") or []
    lines = []
    for part in parts:
        brief = " ".join(str(part.get("brief", "")).split())
        if len(brief) > BRIEF_SHOWN:
            brief = brief[:BRIEF_SHOWN - 1].rstrip() + "…"
        lines.append(iface_("{name}: {brief}", name=part.get("name", ""), brief=brief))
    return {"title": plural_(len(parts), "Start {count} worker agent", "Start {count} worker agents"),
            "body": "\n".join(lines),
            "warning": iface_("Each worker is a background Angelo with a file of its own and its own model session on "
                              "this chat's connection, so its turns use this connection's quota. Each part stays in "
                              "its own file until the agent merges it into your scene, as one Undo step.")}


def merge_message(args, facts=None):
    """``args``: the merge tool's ({"parts": [{"worker", "placements"?, "again"?}]}). ``facts``: what Angelo knows of
    each worker (``merge_facts``): {"target": the lead's file label, number: {"name", "objects", "merged_before"}}."""
    facts = merge_facts() if facts is None else facts
    lines = []
    for part in args.get("parts") or ():
        known = facts.get(part.get("worker")) or {}
        name = known.get("name") or iface_("Worker {number}", number=part.get("worker"))
        bits = [plural_(int(known["objects"]), "{count} object", "{count} objects")] if known.get("objects") else []
        copies = len(part.get("placements") or ()) or 1
        if copies > 1:
            bits.append(plural_(copies, "{count} copy", "{count} copies"))
        if part.get("again") is True and known.get("merged_before"):
            bits.append(iface_("merged into this scene before, so this adds another copy"))
        lines.append(iface_("{name}: {what}", name=name, what=", ".join(bits)) if bits else name)
    target = facts.get("target") or iface_("your scene")
    return {"title": plural_(len(lines), "Merge {count} part into your scene", "Merge {count} parts into your scene"),
            "body": "\n".join(lines),
            "warning": iface_("The parts are added to {target} as one Undo step, so Ctrl+Z takes them out again. "
                              "Their workers end once the parts are in.", target=target)}


def merge_facts(lead_id=""):
    """{worker number: {"name", "objects", "merged_before"}} of the workers of the lead chat ``lead_id`` (the merge
    card's own chat: each lead numbers its workers from 1, break-it E2E-20 final F-M-1), and the lead file's label.
    Without a chat: every worker Angelo runs, a number two leads share left out (the card then says "Worker n")."""
    from . import live_view, parallel_sessions, worker_sessions
    found, seen = {}, set()
    parts = dict(worker_sessions._parts)
    if lead_id:
        parts = {session: parts[session] for session in worker_sessions._started.get(lead_id, ()) if session in parts}
    for session, (number, name, path) in parts.items():
        summary = live_view.summary(session) or {}
        try:   # Merged into its lead's own scene (``parallel_sessions.lead_of``), whichever the window shows.
            before = parallel_sessions.part_in_file(path, parallel_sessions.lead_of(session))
        except ImportError:   # No Blender: no scene to have merged it.
            before = False
        entry = {"name": name, "objects": len(summary.get("objects") or ()), "merged_before": before}
        found[number] = None if number in seen else entry
        seen.add(number)
    found = {number: entry for number, entry in found.items() if entry is not None}
    try:
        import bpy
        found["target"] = bpy.path.basename(bpy.data.filepath) or None
    except (ImportError, AttributeError):
        pass
    return found
