# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The device a Cycles render uses and why, and how long the last one on it took (``device``'s facts).

Contract: doc/angelo/long_jobs.rst, "Render device" (``angelo_agent.render_device_facts`` is this module).
``facts`` is what the agent reads (English, never translated),
``shown`` the same for the person's job card (``job_chat.device_text`` words it), ``describe`` adds them to a
render's admission facts, ``observe``/``estimate`` turn the last finished render on a device into an estimate.
They read the preferences and ``device``'s decision only, so ``facts``/``shown`` are safe on a render
thread. ``device`` re-exports every name here.
"""

import sys

_rates = {}          # Device text -> [(seconds, work, setup, (scene uid, frames))] of its recent renders (``observe``).


def _override():
    """Cycles' ``--cycles-device`` choice, parsed as Cycles parses it (``engine._parse_command_line``: after ``--``,
    argparse, so an unambiguous abbreviation counts), as ``"CUDA"`` or ``"CUDA+CPU"``; or None."""
    argv = sys.argv
    if "--" not in argv:
        return None
    import argparse   # Only with arguments after "--": a start without them never imports it.
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--cycles-print-stats", action="store_true")   # Cycles' other option: same abbreviations.
    parser.add_argument("--cycles-device", default=None)
    try:
        found, _rest = parser.parse_known_args(argv[argv.index("--") + 1:])
    except SystemExit:
        return None
    return found.cycles_device.upper() if found.cycles_device else None


def _names(prefs, kind):
    """Enabled devices of ``kind`` that exist now (the last listing, else the saved entries), and the CPU's flag."""
    available = None if core._available is None else {device[2] for device in core._available.get(kind, ())}
    names, cpu = [], False
    for device in prefs.devices:
        if not device.use:
            continue
        if device.type == "CPU":
            cpu = True
        elif device.type == kind and (available is None or device.id in available):
            names.append(device.name)
    return names, cpu


def gpu_text(kind, names):
    return "GPU (%s: %s)" % (core.NAMES.get(kind, kind), ", ".join(names[:4]))


def facts(scene, preferences=None):
    """``{"device", "device_why", "gpu_available"}``: the device a Cycles render of ``scene`` uses now; None for
    other engines. Reads the preferences only (any thread)."""
    found = _facts(scene, preferences)
    return None if found is None else found[0]


def shown(scene, preferences=None):
    """The same for the person's job card (``job_chat.device_text`` words it): ``{"gpu": True, "backend",
    "names", "cpu"}``, or ``{"gpu": False, "reason"[, "backend"]}``; None for other engines."""
    found = _facts(scene, preferences)
    return None if found is None else found[1]


def _facts(scene, preferences):
    render = getattr(scene, "render", None)
    if getattr(render, "engine", None) != "CYCLES":
        return None
    prefs = core._cycles_prefs(core._preferences(preferences))
    if prefs is None:
        return None   # Without Cycles' preferences Cycles is not enabled: no Cycles render to describe.
    override = _override()
    if override:
        kind = override.split("+")[0]
        entry = {"device": kind, "device_why": "set by Cycles' --cycles-device command-line option"}
        return entry, ({"gpu": False, "reason": "override"} if kind == "CPU" else
                       {"gpu": True, "backend": kind, "names": [], "cpu": "+CPU" in override})
    wanted = getattr(getattr(scene, "cycles", None), "device", "CPU")
    kind = getattr(prefs, "compute_device_type", "NONE") or "NONE"
    names, cpu = _names(prefs, kind) if kind != "NONE" else ([], False)
    if wanted == "GPU" and names:
        return ({"device": gpu_text(kind, names) + (" + CPU" if cpu else "")},
                {"gpu": True, "backend": kind, "names": names[:4], "cpu": cpu})
    entry, card = {"device": "CPU"}, {"gpu": False}
    state = core._status.get("state")
    if wanted != "GPU":
        entry["device_why"], card["reason"] = "the scene's Cycles device (cycles.device) is CPU", "scene"
        if names:
            entry["gpu_available"] = gpu_text(kind, names)
    elif state == "listing":
        entry["device_why"] = "the scene asks for the GPU; Angelo is still listing GPU devices (it started with the app)"
        card["reason"] = "listing"
    elif state in {"no_gpu", "failed", "unavailable"}:
        entry["device_why"], card["reason"] = "the scene asks for the GPU; " + core._status["note"], state
    elif kind == "NONE":
        # The person's None: their decision, or a change since Angelo's (someone set it in Preferences > System).
        person = state == "person" or (core._seen is not None and core._setup_of(prefs) != core._seen)
        entry["device_why"] = "the scene asks for the GPU, but the Cycles compute device in Preferences > System is None"
        if not person:
            entry["device_why"] += " (Angelo has not set a GPU up yet)"
        card["reason"] = "none" if person else "not_set_up"
    else:
        entry["device_why"] = ("the scene asks for the GPU, but no %s device is enabled in Preferences > System"
                               % core.NAMES.get(kind, kind))
        card.update(reason="not_enabled", backend=kind)
    return entry, card


def corrected(found, card):
    """``(facts, shown)`` of a session that began on a GPU from an earlier listing, against this launch's listing
    once it reported (job_manager, while the job runs): a GPU no longer listed rendered on the CPU."""
    if not card or not card.get("gpu") or core._available is None or not card.get("names"):
        return found, card
    listed = {device[0] for device in core._available.get(card.get("backend"), ()) if device[1] != "CPU"}
    if listed & set(card["names"]):
        return found, card
    return ({"device": "CPU", "device_why": "the scene asks for the GPU; the GPU set up from the last listing (%s) "
             "is not listed any more; Cycles renders on the CPU" % ", ".join(card["names"][:4])},
            {"gpu": False, "reason": "no_gpu"})


def _frames(entry):
    """Frames x views a render draws: each one pays the render's fixed part again (scene sync, BVH, writing)."""
    try:
        start, end = entry.get("frames") or (1, 1)
        step = max(1, int(entry.get("frame_step") or 1))   # An animation draws every step-th frame of its range.
        frames = 1 if start is None or end is None else max(1, (end - start) // step + 1)
        return frames * max(1, int(entry.get("views", 1) or 1))
    except (TypeError, ValueError):
        return None


def work(entry):
    """A render's size for estimates: megapixels x samples x frames drawn (x views)."""
    try:
        width, height = entry["resolution"]
        frames = _frames(entry)
        return None if frames is None else width * height / 1e6 * max(1, int(entry.get("samples") or 1)) * frames
    except (KeyError, TypeError, ValueError):
        return None


KEPT = 6   # Recent finished renders kept per device (``observe``).
FIT_MIN_SPREAD = 1.0   # The least time between the two fitted renders. A job's end is stamped by the manager's 0.25 s
                       # poll, so each time is up to 0.25 s late; with less spread that error is as large as the part
                       # that grows with work (see ``fitted``).


def observe(record):
    """Remember a finished Cycles render's pace on its device (job_ui: every ended job): its seconds, work, the time
    before its first progress when the job saw it (``detail["first_progress_s"]``, job_manager), and what it rendered
    (its scene, ``detail["scene_uid"]``, and its frames): only renders of one scene and frame count are fitted."""
    detail = record.get("detail") or {}
    if (record.get("status") != "done" or detail.get("engine") != "CYCLES" or not detail.get("device")
            or not record.get("started") or not record.get("ended")):
        return
    size, seconds = work(detail), record["ended"] - record["started"]
    setup = detail.get("first_progress_s")
    setup = setup if isinstance(setup, (int, float)) and 0 <= setup < seconds else None
    if size and seconds > 0:
        kept = [entry for entry in _rates.get(detail["device"], []) if isinstance(entry, tuple) and len(entry) == 4]
        _rates[detail["device"]] = (kept + [(seconds, size, setup, _shape(detail, detail.get("scene_uid")))])[-KEPT:]


def _shape(entry, scene_uid):
    """``(scene_uid, frames x views)``: what makes two renders comparable for a fit (the same scene, the same frames x
    views); None when the frames are unreadable."""
    frames = _frames(entry)
    return None if frames is None else (scene_uid, frames)


def _observed(entry):
    found = _rates.get(entry.get("device"))
    return [item for item in found if isinstance(item, tuple) and len(item) == 4] if isinstance(found, list) else []


def fitted(entry, scene_uid=None):
    """``(setup_s, seconds_per_work)`` for a render of ``scene_uid`` about to start, or None: fitted through the last
    finished render of the same scene and frame count on the device and the most recent earlier such one whose work
    differs at least 2x and whose time differs at least ``FIT_MIN_SPREAD``. A render's time is a fixed part (kernel
    load, scene sync, BVH, GPU synchronization, writing the image) plus a part that grows with megapixels x samples;
    two sizes separate them, which one render (or its first-progress time, much of a GPU render's fixed part comes
    after the first sample) cannot. Measured on Mac 1 (2026-10-23, 3 scenes x Cycles CPU/GPU, stills): with the larger
    render at least 1 s, 126 extrapolations to 4x+ the work were 0.88-1.15x the real time; with the real 0.25 s end
    stamp added that rule reached 3.2x, which the 1 s spread keeps at 1.48x or less. Renders of another frame count
    pay the fixed part per frame (an animation fitted against a still read its per-frame setup as work), and another
    scene has another fixed part and rate: neither is fitted."""
    shape = _shape(entry, scene_uid)
    if shape is None or scene_uid is None:
        return None
    observed = [item for item in _observed(entry) if item[3] == shape]
    if len(observed) < 2:
        return None
    last = observed[-1]
    other = next((item for item in reversed(observed[:-1])
                  if max(item[1], last[1]) >= 2 * min(item[1], last[1])
                  and abs(item[0] - last[0]) >= FIT_MIN_SPREAD), None)
    if other is None:
        return None
    low, high = sorted((last, other), key=lambda item: item[1])
    rate = (high[0] - low[0]) / (high[1] - low[1])
    if rate <= 0:
        return None   # The larger render was not slower: the scene changed between them, nothing to fit.
    return max(0.0, low[0] - rate * low[1]), rate


def estimate(entry, scene_uid=None):
    """``{"estimate_s", "estimate_from"}`` for a render of ``scene_uid`` about to start, from earlier renders on its
    device; or {}. Two renders of different sizes: their fitted fixed part plus the per-work part scaled
    (``fitted``). One (the last of this scene, else the last on the device): its time before the first sample plus the
    rest scaled, else its whole time scaled (both overstate a GPU render: part of its fixed cost comes after the first
    sample)."""
    wanted = work(entry)
    observed = _observed(entry)
    if not observed or not wanted:
        return {}
    fit = fitted(entry, scene_uid)
    if fit is not None:
        setup, rate = fit
        return {"estimate_s": max(1, int(round(setup + rate * wanted))),
                "estimate_from": "fitted to two earlier finished renders of this scene of different sizes on this "
                                 "device in this session: about %.1f s fixed plus %.3g s per megapixel-sample; this one "
                                 "is %.3g megapixel-samples" % (setup, rate, wanted)}
    same = [item for item in observed if item[3] is not None and scene_uid is not None and item[3][0] == scene_uid]
    seconds, size, setup, shape = (same or observed)[-1]
    frames, then = _frames(entry), (shape[1] if shape is not None else None)
    basis = "the last finished render on this device in this session" + ("" if same else ", of another scene")
    if frames and then and frames != then:
        basis += ", %d frames x views against this one's %d" % (then, frames)
    if setup is None or not frames or not then:
        return {"estimate_s": max(1, int(round(seconds * wanted / size))),
                "estimate_from": "%s: %.1f s for %.3g megapixel-samples; this one is %.3g"
                                 % (basis, seconds, size, wanted)}
    # Per frame: the time before the first sample (its first frame's, and a first render's kernel load) is paid
    # again by every frame, and the rest of a frame's time scales with its megapixel-samples. A still scaled to an
    # animation added that setup once and read 0.26x the real time (break-it review 2026-10-23, C2).
    sampling = max(0.0, seconds / then - setup) * (wanted / frames) / (size / then)
    return {"estimate_s": max(1, int(round(frames * (setup + sampling)))),
            "estimate_from": "%s: %.1f s for %.3g megapixel-samples, %.1f s of its first frame before its first "
                             "sample; this one is %.3g" % (basis, seconds, size, setup, wanted)}


def describe(scene, entry, preferences=None):
    """Add the device facts (and an estimate when one is known) to a Cycles render's facts."""
    try:
        core.setup(preferences)
        found = facts(scene, preferences)
    except Exception as error:   # Facts never stop a render.
        found = {"device": "unknown", "device_why": "reading the device failed (%s)" % type(error).__name__}
    if found:
        entry.update(found)
        entry.update(estimate(entry, getattr(scene, "session_uid", None)))
    return entry


# At the end: render_device re-exports this module's names, so whichever of the two is imported first, the other
# finds every definition in place.
from . import device as core  # noqa: E402
