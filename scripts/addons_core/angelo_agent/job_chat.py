# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The background-job card in the chat transcript, below the call that started the job.

``card_layout`` is pure: it returns drawing operations and hit rectangles, so
its geometry is testable without a GPU (``chat_cards`` executes them with the
chat renderer's primitives). The card shows the job's state, a progress bar with
frames and an ETA while it runs, and its receipt once it ends. Its buttons are
ordinary canvas hits, so hover, pressed, keyboard focus (Tab, Return or Space,
Escape) and the disabled look with its reason come from ``canvas_controls``, in
the same visual language as every other chat control, mirrored in Arabic with
the rest of the canvas. All text is translated at display time; records keep
English reasons and notes (the model reads them), and the fixed ones are marked
with ``n_`` where they are written.
"""

from .canvas_controls import DISABLED as DISABLED_ACTION
from .job_store import output_name
from .ui_text import iface_, join, n_, plural_

PAD, GAP, BAR = 12, 8, 6
BUTTON_HEIGHT = 28
TEXT, MUTED, BG = (0.95, 0.95, 0.95, 1), (0.63, 0.63, 0.63, 1), (0.094, 0.094, 0.094, 1)
PANEL, LINE = (0.12, 0.12, 0.12, 1), (0.245, 0.245, 0.245, 1)
BUTTON, DISABLED, PRIMARY = (0.205, 0.205, 0.205, 1), (0.15, 0.15, 0.15, 1), TEXT
ACTIVE = (0.19, 0.48, 1.0, 1)
STOPPING_REASON = n_("The job stops at its next safe point")
MOVED_REASON = n_("The job's output was moved or deleted")
STATE_COLORS = {"queued": MUTED, "starting": ACTIVE, "running": ACTIVE, "stopping": (0.93, 0.62, 0.36, 1),
                "done": (0.38, 0.72, 0.62, 1), "cancelled": MUTED, "failed": (0.93, 0.42, 0.36, 1),
                "interrupted": (0.93, 0.62, 0.36, 1)}
BAKE_LABELS = {"COMBINED": n_("Combined"), "AO": n_("Ambient Occlusion"), "SHADOW": n_("Shadow"),
               "POSITION": n_("Position"), "NORMAL": n_("Normal"), "UV": n_("UV"), "ROUGHNESS": n_("Roughness"),
               "EMIT": n_("Emit"), "ENVIRONMENT": n_("Environment"), "DIFFUSE": n_("Diffuse"), "GLOSSY": n_("Glossy"),
               "TRANSMISSION": n_("Transmission")}
TITLES = {"RENDER_STILL": n_("Rendering a still"), "RENDER_ANIMATION": n_("Rendering an animation"),
          "BAKE_TEXTURE": n_("Baking textures"), "BAKE_PHYSICS": n_("Baking physics"),
          "BAKE_SIMULATION": n_("Baking a simulation"), "EXPORT_USD": n_("Exporting USD"),
          "EXPORT_ALEMBIC": n_("Exporting Alembic"),
          # angelo_view's RENDERED shading (job_spec.PREVIEW): the agent's look at the scene camera.
          "RENDER_PREVIEW": n_("Rendering a preview for the agent")}
# Product names, shown as Blender shows them (not translated); any other engine shows its identifier.
ENGINE_NAMES = {"CYCLES": "Cycles", "BLENDER_EEVEE": "EEVEE", "BLENDER_EEVEE_NEXT": "EEVEE",
                "BLENDER_WORKBENCH": "Workbench"}
STATUS_LABELS = {"queued": n_("Queued"), "starting": n_("Starting"), "running": n_("Running"),
                 "stopping": n_("Stopping…"), "done": n_("Done"), "cancelled": n_("Cancelled"), "failed": n_("Failed"),
                 "interrupted": n_("Interrupted")}


def title(record):
    """What the job does, translated (the card's and status bar's heading)."""
    return iface_(TITLES.get(record["kind"], record["kind"]))


def duration(seconds):
    """A short, translated duration: "45 s", "3 min", "1 h 5 min"."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return iface_("{seconds} s", seconds=seconds)
    minutes = seconds // 60
    if minutes < 60:
        return iface_("{minutes} min {seconds} s", minutes=minutes, seconds=seconds % 60) if minutes < 10 \
            else iface_("{minutes} min", minutes=minutes)
    return iface_("{hours} h {minutes} min", hours=minutes // 60, minutes=minutes % 60)


def facts_text(record):
    detail, request = record["detail"], record["request"]
    parts = []
    frames = detail.get("frames")
    if isinstance(frames, list) and len(frames) == 2:
        parts.append(iface_("Frame {frame}", frame=frames[0]) if frames[0] == frames[1]
                     else iface_("Frames {start}–{end}", start=frames[0], end=frames[1]))
    if isinstance(detail.get("resolution"), list):
        parts.append("%d×%d" % tuple(detail["resolution"]))
    if detail.get("samples") and detail.get("engine") in {"CYCLES", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"}:
        parts.append(plural_(detail["samples"], "{count} sample", "{count} samples"))
    if record["kind"] == "BAKE_TEXTURE":
        parts.append(iface_("{kind} into {images}", kind=iface_(BAKE_LABELS[request.get("bake_type", "COMBINED")]),
                            images=", ".join(detail.get("images", []))))
    if detail.get("caches"):
        parts.append(plural_(detail["caches"], "{count} cache", "{count} caches"))
    if detail.get("objects"):
        parts.append(", ".join(detail["objects"]))
    if request.get("format") and record["kind"].startswith("RENDER"):
        parts.append(request["format"])
    return join(parts)


def kept_text(receipt, sentence=False):
    """The finished frames a cancelled animation kept, counted in the interface language."""
    if type(receipt.get("files")) is not int or type(receipt.get("frames_total")) is not int:
        return receipt["kept"]  # A record written before the counts were stored.
    if sentence:
        return plural_(receipt["frames_total"], "Kept {done} of {count} frame.", "Kept {done} of {count} frames.",
                       done=receipt["files"])
    return plural_(receipt["frames_total"], "kept {done} of {count} frame", "kept {done} of {count} frames",
                   done=receipt["files"])


def progress_text(record):
    """What the job is doing now, or how it ended (translated)."""
    status, detail, receipt = record["status"], record["detail"], record["receipt"]
    if status == "queued":
        return iface_("Waiting for the running job")
    if status == "starting":
        return iface_("Starting…")
    if status == "stopping":
        return iface_("Stopping at the next safe point…")
    percent = int(100 * float(record.get("progress") or 0))
    if status == "running":
        parts = []
        if detail.get("frames_total", 0) > 1:
            parts.append(plural_(detail["frames_total"], "{done} of {count} frame", "{done} of {count} frames",
                                 done=detail.get("frames_done", 0)))
        parts.append(iface_("{percent}%", percent=percent))
        if record.get("eta_s") is not None:
            parts.append(iface_("about {time} left", time=duration(record["eta_s"])))
        return join(parts)
    elapsed = (record["ended"] or 0) - (record["started"] or record["ended"] or 0)
    if status == "done":
        text = iface_("Finished in {time}", time=duration(elapsed))
        if receipt.get("files", 0) > 1:
            text = join([text, plural_(receipt["files"], "{count} file", "{count} files")])
        return text
    if status == "cancelled":
        by = {"time_budget": iface_("Stopped at its time budget"), "agent": iface_("Cancelled by the agent")}.get(
            record["reason"], iface_("Cancelled"))
        text = join([by, duration(elapsed)]) if record.get("started") else by
        if receipt.get("kept"):
            text = join([text, kept_text(receipt)])
        return text
    if status == "failed" and record["reason"]:
        return iface_(record["reason"])  # A fixed reason has a translation; Blender's own text stays as it is.
    return iface_(receipt["note"]) if receipt.get("note") else iface_(STATUS_LABELS.get(status, status))


def _notice_output(record):
    output = output_name(record)
    return output if output and record["status"] in {"done", "cancelled"} and record["receipt"].get("files") else ""


def notice_text(record):
    """The host-only chat row of a job that ended, translated (shown, never stored)."""
    text = iface_("Job {job} · {title}: {state}", job=record["id"], title=title(record), state=progress_text(record))
    output = _notice_output(record)
    return join([text, output]) if output else text


def stored_notice(record):
    """The same row as stored in the chat: English, so a transcript never keeps a translation."""
    text = "Job %s (%s) %s" % (record["id"], record["kind"], record["status"])
    output = _notice_output(record)
    return join([text, output]) if output else text


def notice_display(message):
    """A stored job notice as shown: from the live record when Angelo still has it, else as stored."""
    from .job_manager import shared
    jobs = shared()
    record = jobs.record(message["job"]) if jobs is not None else None
    return notice_text(record) if record is not None else message["text"]


def _button(ops, rect, label, action, value, reason=None, *, primary=False):
    """A card button; ``reason`` (a message id) makes it disabled. Hover, pressed and focus are ``canvas_controls``'."""
    if reason is not None:
        fill, color = DISABLED, MUTED
    else:
        fill, color = (PRIMARY, BG) if primary else (BUTTON, TEXT)
    ops.append(("rect", *rect, fill, 8))
    ops.append(("text", label, rect[0] + 12, rect[1] + 9, 12, color, rect[2] - 16))
    # A disabled button still answers the pointer: the not-allowed cursor and its reason (canvas_controls).
    ops.append(("hit", rect, action, value) if reason is None else ("hit", rect, DISABLED_ACTION, reason))


def buttons(record, has_output):
    """``[(label, action, primary, disabled reason or None)]`` for a record (pure)."""
    status = record["status"]
    rows = []
    if status in {"queued", "starting", "running", "stopping"}:
        rows.append((iface_("Stopping…"), "job_cancel", False, STOPPING_REASON) if status == "stopping"
                    else (iface_("Cancel"), "job_cancel", False, None))
    elif record["receipt"].get("files"):
        rows.append((iface_("Open", "Angelo"), "job_open", True, None if has_output else MOVED_REASON))  # The verb.
    rows.append((iface_("Details"), "job_details", False, None))
    return rows


def details_text(record):
    request, detail = record["request"], record["detail"]
    lines = [iface_("Job {job}", job=record["id"]),
             iface_("Time budget: {time}", time=duration(60 * request.get("minutes", 0)))]
    if detail.get("engine"):
        lines.append(iface_("Engine: {engine}", engine=ENGINE_NAMES.get(detail["engine"], detail["engine"])))
    for layer in detail.get("layer_samples", []):
        lines.append(join((layer["name"], plural_(layer["samples"], "{count} sample", "{count} samples"))))
    receipt = record["receipt"]
    if receipt.get("files"):
        lines.append(iface_("Files written: {count}", count=receipt["files"]))
    if receipt.get("kept"):
        lines.append(kept_text(receipt, sentence=True))
    if receipt.get("images"):
        lines.append(iface_("Images baked: {names}", names=", ".join(receipt["images"])))
    if receipt.get("caches"):
        lines.append(iface_("Caches baked: {count}", count=receipt["caches"]))
    if receipt.get("bytes"):
        lines.append(iface_("Size: {megabytes} MB", megabytes="%.1f" % (receipt["bytes"] / 1e6)))
    if receipt.get("missing"):
        lines.append(iface_("Missing: {names}", names=", ".join(receipt["missing"])))
    if receipt.get("unrestored"):
        lines.append(iface_("Blender did not take back these settings: {names}", names=", ".join(receipt["unrestored"])))
    if receipt.get("note"):
        lines.append(iface_(receipt["note"]))
    lines.append(iface_("Blender Undo does not remove files a job wrote."))
    return "\n".join(lines)


def card_layout(record, x, top, width, wrap, line_height, ui, *, has_output=False, measure=None):
    """(ops, height) of one job card. ``ui``: the chat view's ``expanded`` flag for this card.

    ``measure(label, size)`` gives a label's width for its button (longer translations get wider buttons).
    """
    measure = measure or (lambda label, size: len(label) * size * 0.55)
    inner = max(40, width - 2 * PAD)
    ops, y = [], top - PAD - 14
    status = record["status"]
    label = iface_(STATUS_LABELS.get(status, status))
    label_width = measure(label, 12) + 18
    ops.append(("rect", x + PAD, y + 3, 8, 8, STATE_COLORS.get(status, MUTED), 4))
    ops.append(("text", title(record), x + PAD + 16, y, 13, TEXT, inner - label_width - 20))
    ops.append(("text", label, x + width - PAD - label_width + 8, y, 12, STATE_COLORS.get(status, MUTED), label_width))
    facts = facts_text(record)
    if facts:
        y -= line_height
        ops.append(("text", facts, x + PAD, y, 11, MUTED, inner))
    if status in {"queued", "starting", "running", "stopping"}:
        y -= BAR + GAP + 2
        ops.append(("rect", x + PAD, y, inner, BAR, LINE, BAR / 2))
        filled = inner * min(1.0, max(0.0, float(record.get("progress") or 0)))
        if filled >= 1:
            ops.append(("rect", x + PAD, y, max(BAR, filled), BAR, STATE_COLORS[status], BAR / 2))
    for value, _ in wrap(progress_text(record), inner, 12):
        y -= line_height
        ops.append(("text", value.rstrip(), x + PAD, y, 12, TEXT, inner))
    output = output_name(record)
    if output:
        y -= line_height - 2
        ops.append(("text", "→ " + output, x + PAD, y, 11, MUTED, inner))
    if ui.get("expanded"):
        for value, _ in wrap(details_text(record), inner, 11):
            y -= line_height - 4
            ops.append(("text", value.rstrip(), x + PAD, y, 11, MUTED, inner))
    y -= GAP + BUTTON_HEIGHT
    bx = x + PAD
    for text, action, primary, reason in buttons(record, has_output):
        button_width = max(84, measure(text, 12) + 26)
        _button(ops, (bx, y, button_width, BUTTON_HEIGHT), text, action, record["id"], reason, primary=primary)
        bx += button_width + GAP
    height = top - y + PAD
    return [("rect", x, top - height, width, height, PANEL, 10)] + ops, height
