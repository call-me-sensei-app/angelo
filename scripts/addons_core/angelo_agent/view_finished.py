# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with ``image``: a finished render shown to the model, without rendering.

A source names one picture file (``SOURCES``); ``view_picture`` turns it into
what the model gets (bounded, cropped to ``region``, scaled down, never
enlarged, opaque). A further source is one more entry there and one more
alternative of ``view_spec.IMAGE``.

* ``image: "j<n>"``: what a render job of this conversation published
  (``angelo_job_start`` reports only its path). A ``RENDER_STILL`` has one
  file; a ``RENDER_ANIMATION`` has one file per frame, chosen with ``frame``
  (its first frame otherwise), also from a job that kept only some frames.
  A job that published several pictures (stereo views, File Output items,
  UDIM tiles, baked maps) is shown one output at a time, named with ``file``
  as its receipt names it (``view_job_output``).
  ``LAST_JOB`` resolves once to this conversation's latest render. A render
  still running is waited for within the request's allowance (``view_wait``).
* ``image: "RENDER_RESULT"``: what Blender's Render Result holds in the
  person's slot, such as their last F12 or the last job's frame.
* Current state (``view_spec.CURRENT_SOURCES``, doc/angelo/view_sources.rst):
  an Image data-block's pixels, a UV layout and the person's editors, made
  into their file by their own modules and shown through the same path,
  never waited for.

**What the model sees is what the person sees.** An 8-bit PNG or JPEG is
display-referred already and is delivered as its pixels are; a 16-bit PNG is
reduced to 8 bits. Scene-linear pixels have no look until a view transform is
applied: the Render Result and an OpenEXR file are saved once by Blender itself
as an 8-bit PNG through the scene's view transform, look and exposure
(``Image.save_render``, which is how the render window shows them), and the
result says that the picture is such a preview. The save has an image format
of its own, made from the scene's output format: the scene is never written,
so no add-on Python runs and the view is automatic in every approval mode
(``view_save``; a build without its native helper sets the scene's format and
puts it back, following ``view_policy``). It is refused while a job has its
own settings applied.

**Only the combined image**, unless ``file`` names another output of a job.
Separate passes, other view layers and stereo views of the Render Result or
of a one-file render are not shown, and the result says when the render has
some. Video files are not read.

The Render Result is a data-block of the file and is only read. An OpenEXR
file is read into an image outside the document, freed before this returns.
Nothing in the scene, the Render Result or the workspace changes. Main thread;
model-facing text is English.
"""

import datetime
import re
from pathlib import Path

from . import live_target, view_picture
from .render_extensions import ViewRefused
from .view_spec import CURRENT_SOURCES, JOB, LAST_JOB, RENDER_RESULT, RENDERED, split_image

FRAME_FILE = re.compile(r"frame_(-?[0-9]+)\.(png|jpg|exr)")
RENDER_KINDS = ("RENDER_STILL", "RENDER_ANIMATION")
TERMINAL = ("done", "failed", "cancelled", "interrupted")  # No job module imported for the image contract.
THEN = " It shows the scene as it was rendered then, not edits made since."
FILE_THEN = " It reads the job's current output file. The recorded engine and samples describe the original job."


class Source:
    """One picture to show: its file, how the result names it, its facts, and what making the file left to undo.

    ``stored``: data, not colours, shown as its stored values (``view_picture.scaled``). ``then``: what the
    result says the picture is of, when that is not a render of the scene. ``note``: a sentence it adds.
    ``framing``: the result's framing and coverage (receipts count pictures by it). ``uncertainty`` and
    ``side_effects``: entries the result lists beside a colour approximation and unrestored settings.
    ``on_shown``: called with the finished result once the picture is ready to send (an editor capture's
    chat notice).
    """

    def __init__(self, path, label, what, facts, temporary=None, unrestored=(), exact=True, *, stored=False,
                 then=THEN, note="", framing="FINISHED_IMAGE", uncertainty=(), side_effects=(), on_shown=None):
        self.path, self.label, self.what, self.facts = Path(path), label, what, facts
        self.temporary, self.unrestored, self.exact = temporary, list(unrestored), exact
        self.stored, self.then, self.note = stored, then, note
        self.framing, self.uncertainty, self.side_effects = framing, list(uncertainty), list(side_effects)
        self.on_shown = on_shown


def job_record(controller, identity):
    """A job of this conversation by its ID (RuntimeError otherwise)."""
    from . import job_ui
    record = job_ui.manager().record(identity)
    if record is None or record["session"] != controller.state["id"]:
        raise RuntimeError("No job %s in this conversation" % identity)
    return record


def _job_file(controller, identity, frame):
    """One published picture of a render job of this conversation: ``(path, label, facts)`` (RuntimeError otherwise)."""
    from . import job_ui, view_job_output
    from .job_store import output_names, regular_output
    record = job_record(controller, identity)
    kind, status = record["kind"], record["status"]
    several = view_job_output.needs_file(record)
    if several:  # A job with several pictures: the refusal names them.
        raise RuntimeError(several)
    if kind not in RENDER_KINDS:
        raise RuntimeError("Job %s is %s; only render and texture bake jobs have pictures to show" % (identity, kind))
    if status not in TERMINAL:
        raise RuntimeError("Job %s has not finished (%s); its pictures exist once it ends" % (identity, status))
    path = job_ui.output_path(record)
    output = record["receipt"].get("output") or record["request"].get("output")
    facts = {"job": identity, "output": output, "engine": record["detail"].get("engine"),
             "samples": record["detail"].get("samples")}
    if kind == "RENDER_STILL":
        if frame is not None:
            raise RuntimeError("Job %s rendered one still; leave frame out" % identity)
        if status != "done" or path is None or not regular_output(path):
            raise RuntimeError("Job %s wrote no image (%s), or its image was moved or deleted" % (identity, status))
        return path, "Job %s image" % identity, facts
    frames = {}
    if path is not None:
        for name in output_names(path):
            match = FRAME_FILE.fullmatch(name.lower())
            if match:
                frames[int(match.group(1))] = path / name
    if not frames:
        raise RuntimeError("Job %s kept no frames (%s), or they were moved or deleted" % (identity, status))
    chosen = min(frames) if frame is None else frame
    if chosen not in frames:
        raise RuntimeError("Job %s has no frame %d: it wrote %d frames, %d to %d"
                           % (identity, chosen, len(frames), min(frames), max(frames)))
    facts.update(frame=chosen, frames=[min(frames), max(frames)], frame_count=len(frames))
    return frames[chosen], "Job %s frame %d" % (identity, chosen), facts


# Not further images of a render: the picture itself, and a Cryptomatte option that is not a pass.
NOT_OTHER_PASSES = ("use_pass_combined", "use_pass_cryptomatte_accurate")


def _other_images(scene):
    """What a render of this scene holds besides its combined image: facts for the result ({} when nothing)."""
    layers = [layer for layer in scene.view_layers if layer.use]
    passes = 0
    for layer in layers:
        for owner in (layer, getattr(layer, "cycles", None), getattr(layer, "eevee", None)):
            if owner is not None:
                passes += sum(1 for prop in owner.bl_rna.properties
                              if prop.identifier.startswith("use_pass_") and prop.identifier not in NOT_OTHER_PASSES
                              and getattr(owner, prop.identifier, False) is True)
        passes += len(getattr(layer, "aovs", ()))
    facts = {}
    if passes:
        facts["other_passes"] = passes
    if len(layers) > 1:
        facts["view_layers"] = len(layers)
    if scene.render.use_multiview:
        facts["stereo_views"] = True
    return facts


def _busy():
    import bpy
    from . import job_ui
    return job_ui.manager().active() is not None or bpy.app.is_job_running("RENDER")


def _display_copy(scene, references, name, image=None, source=None):
    """Blender saves ``image`` or the file ``source`` once as a private 8-bit PNG in the scene's display colours,
    never writing the scene (``view_save``): ``(reference, unrestored, exact, multilayer)``."""
    from . import view_save
    copy = references.new_observation(name + " · display copy")
    try:
        unrestored, exact, multilayer = view_save.display_copy(scene, copy["path"], image=image, source=source)
    except Exception:
        references.discard_observation(copy)
        raise
    return copy, unrestored, exact, multilayer


def _render_result(scene, references):
    """Blender's Render Result (the person's slot) as a display copy: ``(reference, unrestored, exact, facts)``."""
    import bpy
    result = next((image for image in bpy.data.images if image.type == "RENDER_RESULT"), None)
    nothing = "Blender's Render Result holds no render yet; RENDERED without image renders a look"
    if result is None:
        raise RuntimeError(nothing)
    if _busy():
        raise RuntimeError("A render is running; the Render Result can be shown once it ends")
    try:
        copy, unrestored, exact, _multilayer = _display_copy(scene, references, "Render Result", image=result)
    except ViewRefused:
        raise
    except RuntimeError:  # Blender has no pixels to save: nothing was rendered in this session.
        raise RuntimeError(nothing) from None
    slots = result.render_slots
    facts = {"image": RENDER_RESULT, "slot": slots[slots.active_index].name if len(slots) else ""}
    return copy, unrestored, exact, dict(facts, **_other_images(scene))


def linear_file(scene, path, references, label):
    """A scene-linear file (OpenEXR) as a display copy: ``(reference, unrestored, exact, facts)``."""
    if _busy():
        raise RuntimeError("A job is running with its own render settings; %s is OpenEXR and can be shown once "
                           "it ends" % label)
    facts = {"preview": "DISPLAY_REFERRED", "source_format": "OPEN_EXR"}
    try:
        copy, unrestored, exact, multilayer = _display_copy(scene, references, label, source=path)
    except ViewRefused:
        raise
    except RuntimeError as error:
        raise RuntimeError("%s could not be read (%s)" % (label, str(error)[:120])) from None
    if multilayer:  # Known once Blender has read the file, which the save made it do.
        facts["multilayer"] = True
    return copy, unrestored, exact, facts


def _note(what, facts, picture, region, scene_colour, then=THEN):
    source, size = picture["source_size"], picture["size"]
    if region:
        note = "%s: the part asked for (%dx%d of its %dx%d pixels) at %dx%d." % (
            (what,) + tuple(picture["region_pixels"]) + tuple(source) + tuple(size))
    elif size != source:
        note = "%s, %dx%d downscaled to %dx%d." % ((what,) + tuple(source) + tuple(size))
    else:
        note = "%s, %dx%d." % ((what,) + tuple(size))
    note += then
    if facts.get("preview"):
        note += (" The file is OpenEXR (scene-linear values): this is an 8-bit preview through the scene's current %s "
                 "view transform, not its exact values." % scene_colour["view_transform"])
    if any(key in facts for key in ("other_passes", "view_layers", "stereo_views", "multilayer")):
        note += " Only the combined image is shown; its separate passes, layers or views are not."
    return note + view_picture.transparency_note(picture["transparent"])


def _render_result_source(window, checked, references, controller):
    if "item" in checked:  # One pass of it (view_sources.rst).
        from .view_passes import render_result_source
        return render_result_source(window, checked, references, controller)
    scene = window.scene
    copy, unrestored, exact, facts = _render_result(scene, references)
    facts["color"] = view_picture.colour(scene)
    return Source(copy["path"], "Render Result", "Blender's current Render Result", facts, copy, unrestored, exact)


def _job_source(window, checked, references, controller):
    from . import view_job_output
    name, scene = checked["image"], window.scene
    one = view_job_output.chosen(job_record(controller, name), checked)
    if one is not None:  # One output of a job that reports its outputs: the file asked for, or its only one.
        return view_job_output.source(window, dict(checked, file=one), references, controller)
    path, label, facts = _job_file(controller, name, checked.get("frame"))
    what = "%s of job %s (%s)" % ("Frame %d" % facts["frame"] if "frame" in facts else "The finished image", name,
                                  facts["output"])
    if "item" in checked:  # A pass of a multilayer render (view_sources.rst).
        from .view_passes import file_pass_source
        return file_pass_source(window, path, references, label, what, facts, checked["item"])
    return file_source(scene, path, references, label, what, facts)


def file_source(scene, path, references, label, what, facts, *, stored=False, then=FILE_THEN, note=""):
    """A published picture consumed from one bounded private snapshot, never reopened through workspace paths.

    The held input descriptor covers the entire copy, including change checks.
    Header admission, native EXR loading and imbuf decoding then see the same
    bytes even if an output directory or file was replaced after lookup.
    """
    from .job_store import copy_output
    facts["source"] = "CURRENT_OUTPUT_FILE"
    private = references.new_observation(label + " · source copy")
    try:
        try:
            with open(private["path"], "wb") as destination:
                copy_output(path, destination, view_picture.MAX_BYTES)
        except (OSError, ValueError) as error:
            raise RuntimeError("%s could not be read safely: its file changed, is linked, or exceeds %d MB"
                               % (label, view_picture.MAX_BYTES // (1024 * 1024))) from error
        safe_path = Path(private["path"])
        depth = view_picture.admit(safe_path, label)[2]
        if depth == 16:
            facts["source_depth"] = 16
        if depth == view_picture.LINEAR and not stored:
            copy, unrestored, exact, more = linear_file(scene, safe_path, references, label)
            facts.update(more, color=view_picture.colour(scene))
            references.discard_observation(private)
            return Source(copy["path"], label, what, facts, copy, unrestored, exact, then=then, note=note)
        return Source(safe_path, label, what, facts, private, stored=stored, then=then, note=note)
    except Exception:
        references.discard_observation(private)
        raise


def _current_source(window, checked, references, controller):
    """A picture of current state, made by its own module (doc/angelo/view_sources.rst)."""
    from . import view_editor, view_image_data, view_uv, view_web
    make = {"IMAGE": view_image_data.source, "UV": view_uv.source, "EDITOR": view_editor.source,
            "WEB": view_web.source}[checked["image"]]
    return make(window, checked, references, controller)


# ``image`` -> what makes its picture file. A further picture is one more entry here (and in view_spec.IMAGE);
# everything after the file is shared.
SOURCES = ((re.compile(RENDER_RESULT), _render_result_source), (re.compile(JOB), _job_source),
           (re.compile("|".join(CURRENT_SOURCES)), _current_source))


def _job_named(controller, name):
    """Resolve a render alias once, using this conversation's existing job records."""
    from . import job_ui
    manager = job_ui.manager()
    if name != LAST_JOB:
        return manager, manager.record(name)
    mine = [record for record in manager.store.records.values()
            if record["session"] == controller.state["id"] and record["kind"] in RENDER_KINDS]
    if not mine:
        raise RuntimeError("This conversation has started no render job yet; start one with angelo_job_start, or "
                           "render a look (RENDERED without image)")
    return manager, max(mine, key=lambda record: int(record["id"][1:]))


def finished_image(window, checked, references, controller):
    """Show the existing picture, or wait for its exact render job without starting another."""
    source, item = split_image(checked["image"])  # ``item``: the name after the colon, never re-validated here.
    checked = dict(checked, image=source, **({"item": item} if item is not None else {}))
    if source == RENDER_RESULT or source in CURRENT_SOURCES:
        return shown(window, checked, references, controller)
    manager, record = _job_named(controller, checked["image"])
    if record is not None:
        checked = dict(checked, image=record["id"])
    owned = record is not None and record["session"] == controller.state["id"] and record["kind"] in RENDER_KINDS
    identity = (record["id"], record["session"], record["kind"]) if owned else None
    held = live_target.window_ref(window)   # ``show`` runs when the render ends, ticks later (SW-M-2).

    def current():
        from . import job_ui
        return (job_ui.manager() is manager and manager.record(identity[0]) is record
                and (record["id"], record["session"], record["kind"]) == identity
                and controller.state["id"] == identity[1])

    def show():
        from . import job_host
        if owned and not current():
            raise RuntimeError("The render job changed or is no longer owned by this conversation; inspect its card")
        job_host.guard("angelo_view")  # Another queued job may already have locked Blender.
        try:
            live = held()
            if live is None:
                raise RuntimeError("The window closed (or another file was opened) before the render finished")
            result = shown(live, checked, references, controller)
        except RuntimeError:
            if owned and not current():
                from .view_wait import ownership_lost
                return ownership_lost(identity[0])
            raise
        if owned and not current():
            from .view_wait import ownership_lost
            for reference in result.get("views", ()):
                references.discard_observation(reference)
            return ownership_lost(identity[0], outcome=result)
        return result

    if owned:
        from . import view_wait
        if record["status"] not in TERMINAL:
            return view_wait.begin(manager, record, show, controller, current=current)
        if record["status"] != "done":
            return view_wait.finished(record, show, 0.0, view_wait.allowance(controller))
        return view_wait.picture_result(record, show())
    return show()


def shown(window, checked, references, controller):
    """Decode the admitted production picture now, preserving its output and private-copy owners."""
    from . import scene_queries, view_policy
    name, region, scene = checked["image"], checked.get("region"), window.scene
    with view_policy.making(controller) as python:  # Settings set for a save run add-on Python by approval mode.
        source = next(make for pattern, make in SOURCES if pattern.fullmatch(name))(window, checked, references,
                                                                                    controller)
    try:
        view_picture.admit(source.path, source.label)
        try:
            png, picture = view_picture.scaled(source.path, region, checked["resolution"], stored=source.stored)
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError("%s could not be read (%s)" % (source.label, str(error)[:120])) from None
    finally:
        if source.temporary is not None:
            references.discard_observation(source.temporary)
    reference = references.new_observation(source.label + " · rendered" + (" · region" if region else ""))
    Path(reference["path"]).write_bytes(png)
    reference["bytes"] = len(png)
    width, height = picture["size"]
    view = dict(reference, view_id=reference["id"], view="SCENE_CAMERA", framing=source.framing, shading=RENDERED,
                width=width, height=height, cache_hit=False, camera={"source": source.framing})
    rendered = dict(source.facts, complete=True, source_size=picture["source_size"])
    if region:
        view["region"] = region
        rendered.update(region=region, region_pixels=picture["region_pixels"])
    if picture["transparent"]:
        rendered["transparent"] = picture["transparent"]
    if picture.get("clipped"):
        rendered["clipped"] = picture["clipped"]
    side_effects = list(source.side_effects) + ([{"code": "render_settings_not_restored", "detail": (
        "Blender did not take these settings back; tell the person: " + ", ".join(source.unrestored[:8]))}
                    ] if source.unrestored else [])
    uncertainty = list(source.uncertainty) + ([] if source.exact else [{"code": "render_color_approximate", "detail": (
        "The scene's view uses custom curves and its output overrides the colour management; this picture has the "
        "scene's view transform, look and exposure but the output's curves")}])
    result = {"success": True, "read_only": True, "scene_changed": False,
              "document_epoch": scene_queries.document_epoch(), "scene": scene.name[:128],
              "coverage": source.framing, "targets": [], "views": [view], "shading": RENDERED,
              "resolution": checked["resolution"],
              "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
              "uncertain": bool(uncertainty), "uncertainty": uncertainty, "side_effects": side_effects,
              "rendered": rendered,
              "note": _note(source.what, source.facts, picture, region, view_picture.colour(scene), source.then)
              + source.note + view_picture.clipped_note(picture.get("clipped"))}
    view_policy.disclose(result, python)
    if source.on_shown is not None:
        source.on_shown(result)
    return result
