# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What keeping an animation render's frames needs, saved on its job record when it starts. No Blender dependency.

``facts`` is saved locally (never sent to a model): the workspace root, the output, the plan of files with their
frame-name padding, the frame range and step, the document and scene, the scene's own image format when the job
kept it, and the folder a resume publishes into. ``_usable`` reads it back as data, never authority: bounded like a
job, every plan entry well-formed. ``_legacy`` derives the same for a record written before they were saved (1.0.3).
"""

from pathlib import Path, PurePosixPath

from . import job_render_outputs as render_outputs
from .job_spec import FORMATS, MAX_FRAMES

KIND = "RENDER_ANIMATION"


def facts(runner):
    """What recovery and a resume need, saved on the record when an animation render starts (local only)."""
    plan = getattr(runner, "plan", None)
    if runner.request.get("kind") != KIND or plan is None or runner.staging is None:
        return None
    return {"root": str(runner.workspace.root), "output": str(runner.output), "skip": bool(runner.skip_requested),
            "plan": [dict(output) for output in plan.outputs], "frames": [runner.start_frame, runner.end_frame],
            "step": getattr(runner, "step", 1), "format": getattr(runner, "facts", {}).get("format"),
            "blend": getattr(runner, "blend_path", ""), "scene": getattr(runner, "scene_name", ""),
            "into": getattr(runner, "resume_into", None)}


def _legacy(record):
    """Recovery facts for a record written before they were saved (1.0.3): the render's own frames only."""
    detail, request = record["detail"], record["request"]
    frames, staging = detail.get("frames"), Path(detail["staging"])
    relative = PurePosixPath(detail.get("writes") or request.get("output") or "")
    if (type(frames) is not list or len(frames) != 2 or not all(type(value) is int for value in frames)
            or request.get("format", "PNG") not in FORMATS or not relative.name):
        return None
    root = staging.parent
    for _part in relative.parent.parts:
        root = root.parent
    return {"root": str(root), "output": str(staging.parent / PurePosixPath(request["output"]).name),
            "skip": bool(detail.get("writes")), "frames": frames, "legacy": True,
            "plan": [{"prefix": render_outputs.MAIN_PREFIX, "suffix": "", "main": True, "labels": {},
                      "extension": FORMATS[request.get("format", "PNG")]}]}


def _usable(saved):
    if type(saved) is not dict or not all(isinstance(saved.get(key), str) for key in ("root", "output")):
        return None
    frames, plan = saved.get("frames"), saved.get("plan")
    if (type(frames) is not list or len(frames) != 2 or not all(type(value) is int for value in frames)
            or frames[1] < frames[0] or type(plan) is not list or not plan):
        return None
    step = saved.get("step", 1)
    if (type(step) is not int or step < 1 or render_outputs.frame_count(frames[0], frames[1], step) > MAX_FRAMES
            or len(plan) > render_outputs.MAX_OUTPUTS):
        return None   # A record on disk is data: never a range or plan larger than a job may have.
    clean = []
    for output in plan:
        if (type(output) is not dict or not all(isinstance(output.get(key), str) for key in
                                                ("prefix", "suffix", "extension"))
                or type(output.get("labels", {})) is not dict):
            return None
        digits = output.get("digits", 4)
        if type(digits) is not int or not 1 <= digits <= 8:
            return None
        clean.append(dict(output, labels=dict(output.get("labels", {})), main=output.get("main") is True,
                          digits=digits))
    if not any(output["main"] for output in clean):
        return None
    root, output = Path(saved["root"]), Path(saved["output"])
    if not output.is_relative_to(root) or output == root:
        return None
    return dict(saved, plan=clean, step=step)
