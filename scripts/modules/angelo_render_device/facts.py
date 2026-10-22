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

_rates = {}          # Device text -> (seconds, work) of the last finished render on it (``observe``).


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


def work(entry):
    """A render's size for estimates: megapixels x samples x frames (x views)."""
    try:
        width, height = entry["resolution"]
        start, end = entry.get("frames") or (1, 1)
        frames = 1 if start is None or end is None else max(1, end - start + 1)
        return width * height / 1e6 * max(1, int(entry.get("samples") or 1)) * frames * max(1, entry.get("views", 1))
    except (KeyError, TypeError, ValueError):
        return None


def observe(record):
    """Remember a finished Cycles render's pace on its device (job_ui: every ended job)."""
    detail = record.get("detail") or {}
    if (record.get("status") != "done" or detail.get("engine") != "CYCLES" or not detail.get("device")
            or not record.get("started") or not record.get("ended")):
        return
    size, seconds = work(detail), record["ended"] - record["started"]
    if size and seconds > 0:
        _rates[detail["device"]] = (seconds, size)


def estimate(entry):
    """``{"estimate_s", "estimate_from"}`` for a render about to start, from the last one on its device; or {}."""
    seconds, size = _rates.get(entry.get("device"), (None, None))
    wanted = work(entry)
    if not seconds or not wanted:
        return {}
    return {"estimate_s": max(1, int(round(seconds * wanted / size))),
            "estimate_from": "the last finished render on this device in this session: %.1f s for %.3g "
                             "megapixel-samples; this one is %.3g" % (seconds, size, wanted)}


def describe(scene, entry, preferences=None):
    """Add the device facts (and an estimate when one is known) to a Cycles render's facts."""
    try:
        core.setup(preferences)
        found = facts(scene, preferences)
    except Exception as error:   # Facts never stop a render.
        found = {"device": "unknown", "device_why": "reading the device failed (%s)" % type(error).__name__}
    if found:
        entry.update(found)
        entry.update(estimate(entry))
    return entry


# At the end: render_device re-exports this module's names, so whichever of the two is imported first, the other
# finds every definition in place.
from . import device as core  # noqa: E402
