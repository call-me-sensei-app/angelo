# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's Video tab: the Create column's frames and settings, its popovers and the price before Generate.

The tab is Media Studio's ``kind`` VIDEO: the model menu lists the fal connection's models (``fal_models``), the grid
the library's videos, and Generate runs ``fal_video`` through ``StudioJobs`` like any other generation. What this
module adds is drawn between the model and the prompt (``studio_draw.create_panel`` calls ``frames``) and below the
prompt (``settings``), with the same controls as the other tabs.

Frames. A first frame (and, where the model takes one, a last frame) comes from a Media Studio image, an image file,
or the current camera. The camera source is a hook (``set_camera_source``) that the camera-to-video feature
registers: ``source(which, aspect, deliver)`` starts rendering the active camera's view ("first" or "last" frame, at
the tab's aspect ratio or "" when the frame decides it) and returns at once; later, on the main thread, it calls
``deliver(path)`` with the PNG or ``deliver(None, reason)``. Until something registers it, the camera entry isn't
offered. A reference video (a shot's camera move, ``set_reference_video``) goes to models that take one in reference
mode. Frames, references and the video are copied into Studio's private store when chosen and sent (uploaded to fal)
only when the person confirms Generate.

For other surfaces (camera-to-video's "Make video from this shot"): ``show_shot`` opens the Video tab with a shot's
frames and video, and ``current_options`` / ``choose_options`` read and set its length, resolution, aspect and audio.

Every setting offered is one the chosen model takes in the current mode (``fal_models.capabilities``); a value the
model doesn't take (after switching models) falls back to the model's default rather than being sent.
"""

from . import fal_models, flow_prices
from .ui_text import iface_, n_

KEYS = ("video_first", "video_last", "video_reference_video", "video_duration", "video_resolution", "video_aspect",
        "video_audio")
FRAME_LABELS = {"video_first": n_("First frame"), "video_last": n_("Last frame")}
ADD_FRAME = {"video_first": n_("Add first frame…"), "video_last": n_("Add last frame…")}
PRIVACY = n_("Frames and references upload to fal (unlisted links) when you generate.")
RENDERING = n_("Rendering the camera's view…")
NOT_RENDERED = n_("The camera's view couldn't be rendered")
REFERENCE_VIDEO = n_("Reference video")
MOVE_GUIDES = n_("Your camera move guides the motion. Describe what happens in the prompt; a first frame replaces the move.")
FIRST_INSTEAD = n_("Use a first frame instead…")
LIBRARY_ROWS = 20
_camera = {"source": None, "label": n_("From the current camera")}


def set_camera_source(source, label=None):
    """Register the current camera's frame source (camera-to-video): ``source(which, aspect, deliver)``; None removes
    it. ``label`` (marked with ``n_``) replaces the menu entry's words."""
    _camera["source"] = source
    if label:
        _camera["label"] = label


def camera_available():
    return _camera["source"] is not None


def defaults():
    return {"video_first": None, "video_last": None, "video_reference_video": None, "video_duration": None,
            "video_resolution": None, "video_aspect": None, "video_audio": False}


def mode(state):
    """The input mode the tab's inputs choose; frames win over references (``inputs`` sends only the frames)."""
    if state.get("video_first"):
        return fal_models.IMAGE
    return fal_models.REFERENCE if state["references"] or state.get("video_reference_video") else fal_models.TEXT


def capabilities(state, model):
    """The current model's offer in the current mode, or None (no fal model chosen, or the mode unsupported)."""
    if not model or not fal_models.is_video_model(model["id"]):
        return None
    try:
        return fal_models.capabilities(model["id"], mode(state))
    except ValueError:
        return None


def options(state, model):
    """The canonical options for ``model``: the person's choices where the model takes them, else its defaults."""
    found = capabilities(state, model)
    chosen = fal_models.defaults(model["id"], mode(state))
    if found is None:
        return chosen
    if state.get("video_duration") in found["durations"]:
        chosen["duration"] = state["video_duration"]
    if state.get("video_resolution") in found["resolutions"]:
        chosen["resolution"] = state["video_resolution"]
    if state.get("video_aspect") in found["aspects"]:
        chosen["aspect"] = state["video_aspect"]
    chosen["audio"] = bool(state.get("video_audio")) and found["audio"]
    return chosen


def inputs(state):
    """``fal_video.generate``'s inputs from the tab: its frames, or its references."""
    framed = bool(state.get("video_first"))
    video = state.get("video_reference_video")
    return {"first": state.get("video_first"), "last": state.get("video_last"),
            "references": [dict(r) for r in state["references"]] if not framed else [],
            "videos": [dict(video)] if video and not framed else []}


def price_options(state, model):
    """``options`` plus what prices a reference video (its length), never sent to fal."""
    chosen, video = options(state, model), inputs(state)["videos"]
    return dict(chosen, reference_video_seconds=video[0].get("seconds") or -1) if video else chosen


def short_price(state, model):
    """'≈ $0.97' for the Generate button, or "" when unknown (the line under it says so)."""
    if not model or not model.get("mode"):
        return ""
    total = flow_prices.estimate(model["mode"], model["id"], price_options(state, model), len(inputs(state)["references"]))
    return "" if total is None else f"≈ ${total:.2f}"


def rate_note(mode, model, resolution=""):
    """'≈ $0.12/s at 720p' for a model picker (``resolution`` or the model's default), so the same model offered by two
    providers (Seedance on fal and on BytePlus) reads side by side; "" when the price is unknown."""
    if not fal_models.is_video_model(model):
        return ""
    found = fal_models.capabilities(model, fal_models.TEXT)
    resolution = resolution if resolution in found["resolutions"] else fal_models.defaults(model)["resolution"]
    per_second = flow_prices.rate(mode, model, resolution)
    if per_second is None:
        return ""
    amount = f"{per_second:.3f}".rstrip("0")
    amount += "00" if amount.endswith(".") else ""   # $0.121, $0.08, $1.00
    return (iface_("≈ ${amount}/s at {resolution}", amount=amount, resolution=resolution)
            if resolution else iface_("≈ ${amount}/s", amount=amount))


def picker_note(entry):
    """A model picker's tooltip (Flows' generator steps): its provider and account, and a video model's price per
    second, so the same model on two providers (Seedance on fal and on BytePlus) can be told apart and compared."""
    from .provider_registry import PROVIDER_NAMES, SPECS
    provider = PROVIDER_NAMES.get(SPECS.get(entry["mode"], {}).get("provider"), "")
    text = iface_("{provider} · {account}", provider=provider, account=entry["account"]) if provider else entry["account"]
    return " · ".join(filter(None, (text, rate_note(entry["mode"], entry["id"]))))


def price_line(state, model):
    """'≈ $1.21 · 5 s at 720p (estimate, prices as of 2026-10-30)', or what's unknown."""
    if not model or not model.get("mode"):
        return iface_("Connect a video provider in AI Providers to see prices")
    chosen = options(state, model)
    total = flow_prices.estimate(model["mode"], model["id"], price_options(state, model), len(inputs(state)["references"]))
    shape = iface_("{seconds} s at {resolution}", seconds=chosen["duration"], resolution=chosen["resolution"]) \
        if chosen["resolution"] else iface_("{seconds} s", seconds=chosen["duration"])
    if total is None:
        return iface_("Price unknown · {shape}", shape=shape)
    return iface_("≈ ${amount} · {shape} (estimate, prices as of {date})", amount=f"{total:.2f}", shape=shape,
                  date=flow_prices.as_of(model["mode"], model["id"]))


# -- drawing (studio_draw.create_panel) ------------------------------------------------------------------------------
def frames(state, model, px, cw, top):
    """The FRAMES block under the model; returns the new ``top``."""
    from . import studio_media_tiles   # Drawing only: the tab's logic stays importable without Blender.
    from .studio_draw import button, fit, label, paragraph
    from .studio import MUTED
    found = capabilities(state, model)
    top -= 38
    label(iface_("FRAMES"), px, top)
    rows = ["video_first"] + (["video_last"] if state.get("video_first") and (found is None or found["last_frame"]) else [])
    video = state.get("video_reference_video") if not state.get("video_first") else None
    # Media first: what is attached shows as pictures (a video as its poster, a play mark and its length).
    items = [{"path": state[key].get("path"), "label": iface_(FRAME_LABELS[key]) + " · " + state[key]["name"],
              "remove": ("video_clear", key)} for key in rows if state.get(key)]
    if video:
        items.append({"path": video.get("path"), "label": iface_(REFERENCE_VIDEO) + " · " + video["name"],
                      "seconds": max(0, video.get("seconds") or 0), "remove": ("video_clear", "video_reference_video")})
    top = studio_media_tiles.tiles(state, items, px, cw, top - 4)
    if video:   # How the move and a first frame relate, before the person is asked for one.
        top -= 20
        top -= paragraph(iface_(MOVE_GUIDES), px, top, cw, 10, MUTED, 3) - 14
    for key in rows:
        if not state.get(key):
            top -= 36
            button(state, (px, top, cw, 30), iface_(FIRST_INSTEAD if video else ADD_FRAME[key]), "video_frame", key,
                   icon="plus", size=11)
    if found and found["reference_mode"] and not state.get("video_first"):
        top -= 38
        label(iface_("REFERENCES ({count} of {limit})", count=len(state["references"]),
                     limit=fal_models.capabilities(model["id"], fal_models.REFERENCE)["references"]), px, top)
        top -= 40
        button(state, (px, top, fit(iface_("Image"), 12, True, 90), 30), iface_("Image"), "reference", icon="plus")
        top = studio_media_tiles.reference_tiles(state, px, cw, top)
    top -= 22
    top -= paragraph(iface_(PRIVACY), px, top, cw, 10, MUTED, 2) - 12
    return top


def settings(state, model, px, cw, top):
    """Length, resolution, aspect ratio and audio for the chosen model; returns the new ``top``."""
    from .studio_draw import button, canvas, fit, label
    from .studio_state import chip_grid
    found = capabilities(state, model)
    if found is None:
        return top
    chosen = options(state, model)
    top -= 28
    label(iface_("Length"), px, top)
    top -= 37
    length = iface_("{seconds} seconds", seconds=chosen["duration"])
    button(state, (px, top, min(cw, fit(length, 11, False, 110)), 30), length, "video_duration_menu", size=11)
    if found["resolutions"]:
        top -= 29
        label(iface_("Resolution"), px, top)
        top -= 37
        cell, places = chip_grid(found["resolutions"], canvas.measure, 11, cw, cell=64)
        for value, (dx, row) in zip(found["resolutions"], places):
            button(state, (px + dx, top - row * 37, cell, 30), value, "video_resolution", value,
                   active=chosen["resolution"] == value, size=11)
        top -= places[-1][1] * 37
    if found["aspects"]:
        top -= 29
        label(iface_("Aspect ratio"), px, top)
        top -= 37
        cell, places = chip_grid(found["aspects"], canvas.measure, 10, cw)
        for value, (dx, row) in zip(found["aspects"], places):
            button(state, (px + dx, top - row * 37, cell, 30), value, "video_aspect", value,
                   active=chosen["aspect"] == value, size=10)
        top -= places[-1][1] * 37
    elif state.get("video_first"):
        top -= 26
        label(iface_("Shape follows the first frame"), px, top)
    if found["audio"]:
        top -= 41
        button(state, (px, top, cw, 32), iface_("Audio · On") if chosen["audio"] else iface_("Audio · Off"),
               "video_audio", active=chosen["audio"])
    return top


# -- clicks and popovers (studio.click, studio_input) ---------------------------------------------------------------
def click(context, state, action, value, rect):
    """A Video tab control; True when ``action`` was one."""
    from . import studio
    if action == "video_frame":
        open_frame_menu(state, value, rect)
    elif action == "video_clear":
        state[value] = None
        if value == "video_first":
            state["video_last"] = None
    elif action == "video_duration_menu":
        model = studio.selected(state)
        found = capabilities(state, model)
        if found:
            chosen = options(state, model)["duration"]
            entries = [{"label": iface_("{seconds} seconds", seconds=s), "value": s, "selected": s == chosen}
                       for s in found["durations"]]
            state["popover"] = {"action": "video_duration", "title": n_("Length"), "entries": entries,
                                "anchor": (rect[0], rect[1] + rect[3]), "scroll": 0}
    elif action in {"video_resolution", "video_aspect"}:
        state[action] = value
    elif action == "video_audio":
        state["video_audio"] = not options(state, studio.selected(state))["audio"]
    else:
        return False
    return True


def open_frame_menu(state, which, rect):
    from . import studio
    entries = []
    if camera_available():
        entries.append({"label": iface_(_camera["label"]), "value": "camera"})
    entries.append({"label": iface_("Image file…"), "value": "file"})
    images = studio.library().visible("IMAGE")[:LIBRARY_ROWS]
    entries += [{"label": (a["prompt"] or a["model"])[:60], "description": iface_("Media Studio image"), "value": a["id"]}
                for a in images if a["status"] == "ready" and a.get("files")]
    state["popover"] = {"action": "frame:" + which, "title": FRAME_LABELS[which], "entries": entries,
                        "anchor": (rect[0], rect[1] + rect[3]), "scroll": 0}


def choose(context, state, action, choice):
    """A popover choice of this tab (``studio_input``); True when ``action`` was one of its popovers."""
    import bpy
    from . import studio
    if action == "video_duration":
        state["video_duration"] = choice
        return True
    if not action.startswith("frame:"):
        return False
    which = action.partition(":")[2]
    if choice == "file":
        bpy.ops.angelo.studio_files('INVOKE_DEFAULT', purpose="FRAME_" + which.removeprefix("video_").upper(),
                                    area_pointer=str(context.area.as_pointer()))
    elif choice == "camera":
        def deliver(path, reason=None):   # The source calls it later, on the main thread.
            try:
                if path:
                    set_frame(state, which, [str(path)])
                else:
                    state["notice"] = reason or NOT_RENDERED
            except (ValueError, OSError) as error:
                state["notice"] = str(error)
            studio.redraw()
        state["notice"] = RENDERING
        aspect = options(state, studio.selected(state))["aspect"] if which == "video_first" or not state.get("video_first") else ""
        _camera["source"](which.removeprefix("video_"), aspect, deliver)
    else:
        library = studio.library()
        asset = library.assets.get(choice)
        if asset and asset["kind"] == "IMAGE" and asset["status"] == "ready":
            path = library.checked_file(asset, asset["files"][0])
            set_frame(state, which, [str(path)], name=(asset["prompt"] or asset["model"])[:40])
    return True


def drop(state, paths):
    """An image dropped on the Video tab: the first frame, then the last where the model takes one; a frame already
    there is replaced (the last, or the first on a model without a last frame)."""
    from . import studio
    found = capabilities(dict(state, video_first=True), studio.selected(state))
    if not state.get("video_first"):
        return set_frame(state, "video_first", paths)
    return set_frame(state, "video_last" if found is None or found["last_frame"] else "video_first", paths)


def set_reference_video(state, path, name=None):
    """A shot's camera-move MP4 as the reference video (reference mode: it replaces any frames)."""
    from pathlib import Path
    from . import studio
    from .fal_video import load_video, mp4_seconds
    data, _mime = load_video({"path": str(path)})
    store = studio._references.directory
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    import uuid
    copy = store / (str(uuid.uuid4()) + ".mp4")
    copy.write_bytes(data)
    state.update(video_first=None, video_last=None,
                 video_reference_video={"id": copy.stem, "path": str(copy), "name": (name or Path(path).name)[:160],
                                        "seconds": mp4_seconds(data)})
    state["notice"] = n_("Reference video added. It's sent to fal only when you confirm Generate.")


def current_options(state):
    """The tab's length, resolution, aspect and audio for its chosen model (``fal_models`` canonical options)."""
    from . import studio
    model = studio.selected(state)
    return options(state, model) if model else {}


def choose_options(state, duration=None, resolution=None, aspect=None, audio=None):
    """Set the tab's choices; a value its model doesn't take falls back to the model's default when drawn."""
    for key, value in (("video_duration", duration), ("video_resolution", resolution), ("video_aspect", aspect),
                       ("video_audio", audio)):
        if value is not None:
            state[key] = value


def show_shot(window, first=None, last=None, video=None, name=None):
    """Open Media Studio's Video tab from ``window`` with a shot's frames (PNG paths) or its camera-move ``video``
    (MP4); returns the tab's state. Raises RuntimeError when Media Studio can't open, ValueError for a bad file."""
    from . import live_windows, studio, studio_toggle
    target = studio_toggle.open_media_studio("video", window)
    area = next(a for a in live_windows.areas(target) if studio.is_studio(a))
    state = studio.view(area)
    # What was delivered is shown: the Create column opens even where a narrow window would fold it away.
    state["collapsed"] = False
    if video:
        set_reference_video(state, video, name)
    if first:
        set_frame(state, "video_first", [str(first)], name)
    if last:
        set_frame(state, "video_last", [str(last)], name)
    studio.redraw()
    return state


def set_frame(state, which, paths, name=None):
    """Copy one image into Studio's private reference store as a frame (``ReferenceStore``: checked, size-bounded)."""
    from pathlib import Path
    from . import studio
    from .references import MIME
    if not paths or Path(paths[0]).suffix.lower() not in MIME:
        raise ValueError(n_("Frames must be PNG, JPEG or WebP images"))
    reference = studio._references.import_images(paths[:1], [])[0]
    if name:
        reference["name"] = name
    state[which] = reference
    state["references"], state["video_reference_video"] = [], None   # Frames and references don't mix (mode_for).
    state["notice"] = n_("Frame added. It's sent to fal only when you confirm Generate.")
