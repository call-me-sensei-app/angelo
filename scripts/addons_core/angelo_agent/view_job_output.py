# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with ``image: "j<n>"`` and ``file``: one output of a job that published several.

A production job reports what it published as ``outputs`` (doc/angelo/
long_jobs.rst, "Output identity"): a stereo render's views, a compositor File
Output node's items, a baked image's maps and a UDIM image's tiles. ``file``
is one of those entries' ``file`` exactly as the job reported it, with
``frame`` for an animation's pattern (its first kept frame otherwise). The
picture file is reached only through ``job_ui.output_file``, which accepts a
name the job reported and nothing else, so a model never names a path.

**What the model is shown.**

* A render's view or File Output item is a picture: an 8-bit file as its
  pixels are, a 16-bit one reduced to 8 bits, an OpenEXR one as Blender's own
  8-bit preview through the scene's view transform (``view_finished``).
* A baked map that holds data, not colours (``job_spec.DATA_MAPS``: a normal,
  position, UV, roughness, ambient-occlusion or shadow map), gets **no view
  transform**, whatever the file: an 8- or 16-bit file is shown as stored, and
  an OpenEXR file's values from 0 to 1 are shown as they are, the pixels
  outside that range clipped and counted. A view transform would bend the very
  numbers the picture is looked at for. The result states it (``values``:
  ``STORED``, ``clipped``) and so does its note.
* A baked colour map in an 8- or 16-bit file is shown as stored too; in OpenEXR
  it is scene-linear and gets the same preview as a render.

**What is refused, with what works.** A job with several pictures and no
``file`` (``needs_file`` names them); a name the job did not report; a file
that holds several layers or views in one OpenEXR (a view shows one picture);
a bake without an ``output`` (it published no file); a job that has not ended.

Main thread; model-facing text is English.
"""

from .job_spec import DATA_MAPS
from .job_store import ACTIVE, FRAME_MARK, output_entries, output_name

MAX_LISTED = 8  # Outputs a refusal names one by one; the rest are counted.
BAKED = " It reads the job's current output file, including any edits made to that file since the bake."
STORED = " It is non-colour data (a %s map): its stored values are shown as they are, with no view transform."
NO_FILES = ("Job %s baked into %s and published no file: it had no output. Start the bake with an output folder to "
            "look at its maps")


def _is(entry):
    """What one output is, from its receipt entry: "view right", "File Output Passes: Mask, view left",
    "NORMAL map of Low Normal", "tile 1002 of Skin (EMIT map)"."""
    parts = []
    if entry.get("node"):
        parts.append("File Output %s: %s" % (entry["node"], entry["slot"] if entry.get("slot") else
                                             "layers " + ", ".join(map(str, entry.get("layers") or ()))))
    if entry.get("view"):
        parts.append("view %s" % entry["view"])
    elif entry.get("views"):
        parts.append("views %s in one file" % ", ".join(map(str, entry["views"])))
    name = " of %s" % entry["name"] if entry.get("name") else ""
    if entry.get("tile"):
        parts.append("tile %s%s%s" % (entry["tile"], name, " (%s map)" % entry["map"] if entry.get("map") else ""))
    elif entry.get("map"):
        parts.append("%s map%s" % (entry["map"], name))
    return ", ".join(parts)


def _files(record):
    return [entry for entry in output_entries(record["receipt"]) if isinstance(entry.get("file"), str)]


def _names(entries):
    """The outputs a model may pass as ``file``, each with what it is; bounded."""
    listed = ["%s (%s)" % (entry["file"], _is(entry)) if _is(entry) else entry["file"] for entry in entries[:MAX_LISTED]]
    more = len(entries) - len(listed)
    return "; ".join(listed) + ("; and %d more in its outputs" % more if more > 0 else "")


def chosen(record, checked):
    """The output to show: the ``file`` asked for, or the only file a job with outputs published; None otherwise."""
    if "file" in checked:
        return checked["file"]
    entries = _files(record)
    return entries[0]["file"] if len(entries) == 1 and record["status"] not in ACTIVE else None


def needs_file(record):
    """Why this job's picture cannot be shown without ``file``, naming its outputs; None for a job with one picture."""
    identity, kind, entries = record["id"], record["kind"], _files(record)
    if kind == "BAKE_TEXTURE":
        if record["status"] in ACTIVE:
            return "Job %s has not finished (%s); its images exist once it ends" % (identity, record["status"])
        if record["status"] != "done":
            return "Job %s published no image (%s)" % (identity, record["status"])
        if not entries:
            images = ", ".join(str(name) for name in record["receipt"].get("images") or ()) or "its images"
            return NO_FILES % (identity, images)
        return "Job %s baked several images or tiles: pass file with one of its outputs: %s" % (identity, _names(entries))
    if kind not in ("RENDER_STILL", "RENDER_ANIMATION") or not entries:
        return None
    if kind == "RENDER_ANIMATION":
        return ("Job %s wrote several pictures a frame: pass file (and frame for ####) with one of its outputs: %s"
                % (identity, _names(entries)))
    return "Job %s wrote several pictures: pass file with one of its outputs: %s" % (identity, _names(entries))


def _what(entry, record, shown):
    is_ = _is(entry)
    return "%s from job %s (%s/%s)" % (is_[:1].upper() + is_[1:] if is_ else "One output", record["id"],
                                       output_name(record), shown)


def source(window, checked, references, controller):
    """The ``view_finished.Source`` of one output of a job: its published file and what the result says of it."""
    from . import job_ui, view_finished
    identity, name, frame = checked["image"], checked["file"], checked.get("frame")
    record = view_finished.job_record(controller, identity)
    if record["status"] in ACTIVE:
        raise RuntimeError("Job %s has not finished (%s); its pictures exist once it ends" % (identity, record["status"]))
    entries = _files(record)
    entry = next((item for item in entries if item["file"] == name), None)
    if entry is None:
        if not entries:
            raise RuntimeError(needs_file(record) or "Job %s reports no outputs; leave file out" % identity)
        raise RuntimeError("Job %s reports no output %s. Its outputs: %s" % (identity, name[:80], _names(entries)))
    passes = "item" in checked  # One pass of a multilayer file (view_passes, view_sources.rst).
    if (entry.get("layers") and not passes) or (entry.get("views") and name.lower().endswith(".exr")):
        held = "layers " + ", ".join(map(str, entry["layers"])) if entry.get("layers") else \
            "views " + ", ".join(map(str, entry["views"]))
        raise RuntimeError("%s of job %s holds the %s in one OpenEXR file, and a view shows one picture: name one "
                           "pass of its layers as image %s:<pass> (or <layer>.<pass>), or write them as separate "
                           "files (a File Output item each; views in individual files)"
                           % (name[:80], identity, held, identity))
    facts = {"job": identity, "output": output_name(record), "file": name}
    facts.update({key: entry[key] for key in ("view", "views", "node", "slot", "tile", "map", "name") if key in entry})
    for key in ("engine", "samples"):
        if record["detail"].get(key) is not None and record["kind"] != "BAKE_TEXTURE":
            facts[key] = record["detail"][key]
    shown = name
    if FRAME_MARK in name:
        frames = job_ui.output_frames(record, name)
        if not frames:
            raise RuntimeError("Job %s kept no frames of %s (%s), or they were moved or deleted"
                               % (identity, name[:80], record["status"]))
        frame = frames[0] if frame is None else frame
        if frame not in frames:
            raise RuntimeError("Job %s has no frame %d of %s: it wrote %d frames, %d to %d"
                               % (identity, frame, name[:80], len(frames), frames[0], frames[-1]))
        facts.update(frame=frame, frames=[frames[0], frames[-1]], frame_count=len(frames))
        shown = name.replace(FRAME_MARK, "%.4d" % frame)
    elif frame is not None:
        raise RuntimeError("%s of job %s is one picture, not a frame of an animation; leave frame out"
                           % (name[:80], identity))
    path = job_ui.output_file(record, name, frame if FRAME_MARK in name else None)
    if path is None:
        raise RuntimeError("%s of job %s was moved or deleted since the job published it" % (shown[:80], identity))
    label = "Job %s %s" % (identity, shown)
    what = _what(entry, record, shown)
    baked = record["kind"] == "BAKE_TEXTURE"
    data = baked and record["request"].get("bake_type") in DATA_MAPS
    then = BAKED if baked else view_finished.FILE_THEN
    if data:
        facts["values"] = "STORED"
    if passes:
        from .view_passes import file_pass_source
        return file_pass_source(window, path, references, label, what, facts, checked["item"])
    return view_finished.file_source(window.scene, path, references, label, what, facts, stored=data, then=then,
                                     note=STORED % record["request"]["bake_type"] if data else "")
