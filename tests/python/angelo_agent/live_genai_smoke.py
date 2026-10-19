# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""BILLED one-shot live check of Gemini Images and Tripo 3D generation (in-app driver).

Runs INSIDE the compiled GUI app (``--python``). It submits at most ONE
generation per provider through the host path the approved
``angelo_studio_generate`` tool uses (``BlenderExecutor`` -> ``StudioAgent``
-> ``StudioJobs``), monitors it through ``StudioPending.poll`` and verifies the
saved output. It never retries a submission, never prompts for the Keychain,
and never reads, prints or writes key material: keys load through the app's own
saved-key path (``saved_keys_ui``) exactly as at a normal launch.

Refuses unless ``ANGELO_QA_LIVE_GENAI=1`` and ``--authorize-billed-generation``
are given, and unless state, library and connections are explicit overrides::

    ANGELO_QA_LIVE_GENAI=1 ANGELO_CONNECTIONS_DIR=<profile> ANGELO_STATE_DIR=<tmp> \\
    ANGELO_LIBRARY_DIR=<tmp> ANGELO_DISABLE_DEFAULT_CHAT=1 \\
    <Angelo> --factory-startup --no-window-focus --python live_genai_smoke.py -- \\
        --authorize-billed-generation --gemini-connection UUID --tripo-connection UUID \\
        --report <tmp>/genai-smoke.json
"""

import json
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
# Run THIS checkout's add-on, not the copy bundled in the compiled app.
ROOT = Path(__file__).resolve().parents[3]
ADDON_ROOT = ROOT / "scripts/addons_core"
sys.path.insert(0, str(ADDON_ROOT))
PROMPTS = {"gemini_images_api": "a small teal toy bird, plain background", "tripo_api": "a small low-poly teal toy bird"}
# No prices are in the catalog. Order: cheapest first, from primary sources:
# Gemini Flash image tier is cheaper than Pro; Tripo's changelog lists P2 at
# 100 credits without texture (low-poly, an upgrade of P1), v3.1 is the
# high-detail default with no documented lower price.
PREFERENCE = {"gemini_images_api": ["gemini-3.1-flash-image", "gemini-3-pro-image"],
              "tripo_api": ["P2-20260801", "v3.1-20260211"]}
TRIPO_FACE_LIMIT = 1000  # Price is not face-count based; small output, well inside 48-50,000.
KEY_TIMEOUT, IMAGE_TIMEOUT, MODEL_TIMEOUT = 60.0, 300.0, 1500.0  # seconds
STARTED = time.monotonic()


def arg(name):
    return ARGS[ARGS.index(name) + 1] if name in ARGS and ARGS.index(name) + 1 < len(ARGS) else ""


def now():
    return round(time.monotonic() - STARTED, 3)


def log(text):
    print("[genai-smoke %7.1fs] %s" % (now(), text), flush=True)


REPORT_PATH = Path(arg("--report")) if arg("--report") else None
report = {"script": "live_genai_smoke.py", "started_utc": datetime.now(timezone.utc).isoformat(),
          "binary": bpy.app.binary_path, "blender_version": bpy.app.version_string,
          "environment": {k: os.environ.get(k, "") for k in ("ANGELO_CONNECTIONS_DIR", "ANGELO_STATE_DIR",
                          "ANGELO_LIBRARY_DIR", "BLENDER_USER_CONFIG", "TMPDIR")},
          "providers": {}, "errors": [], "refused": None, "complete": False}


def write_report(secrets=()):
    if REPORT_PATH is None:
        return
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    text = json.dumps(report, indent=2, default=str)
    leaked = any(s and s in text for s in secrets)
    report["key_material_absent"] = not leaked
    text = json.dumps(report, indent=2, default=str)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_name(REPORT_PATH.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, REPORT_PATH)


def refusal():
    if os.environ.get("ANGELO_QA_LIVE_GENAI") != "1":
        return "ANGELO_QA_LIVE_GENAI=1 is required"
    if "--authorize-billed-generation" not in ARGS and not CATALOG_ONLY:
        return "--authorize-billed-generation is required: each provider is billed once"
    if bpy.app.background:
        return "Saved keys load only in the GUI app (background mode is memory-only)"
    for name in ("ANGELO_CONNECTIONS_DIR", "ANGELO_STATE_DIR", "ANGELO_LIBRARY_DIR"):
        if not os.environ.get(name):
            return name + " must select an explicit profile/temporary directory"
    if REPORT_PATH is None:
        return "--report PATH is required"
    if ONLY and ONLY not in FLAGS:
        return "--only must be one of " + ", ".join(FLAGS)
    try:
        for mode in ORDER:
            uuid.UUID(arg(FLAGS[mode]))
    except ValueError:
        return " and ".join(FLAGS[m] for m in ORDER) + " must be connection UUIDs"
    loaded = sys.modules.get("angelo_agent")
    if loaded is not None and not Path(loaded.__file__).resolve().is_relative_to(ADDON_ROOT):
        return "angelo_agent was already imported from " + str(loaded.__file__)
    return None


FLAGS = {"gemini_images_api": "--gemini-connection", "tripo_api": "--tripo-connection"}
ONLY = arg("--only")
MODEL = arg("--model")  # Exactly this model; no fallback to another one.
if MODEL and ONLY:
    PREFERENCE[ONLY] = [MODEL]
CATALOG_ONLY = "--catalog-only" in ARGS  # Non-billable: key load, GET connection check and catalog; no generation.
ORDER = (ONLY,) if ONLY else ("gemini_images_api", "tripo_api")
REASON = refusal()
if not REASON:
    import addon_utils
    from angelo_agent import ui
    # Coding-agent sign-in checks run the codex/claude CLIs against their real
    # profiles, and their results auto-discover native accounts into the shared
    # test profile and start CLI model discovery. None of that is needed here.
    ui._authentication.start = lambda *args, **kwargs: None
    ui._authentication.pump = lambda: []
    ui.refresh_connected_models = lambda *args, **kwargs: None
    # Startup enables the host lazily (default chat, which automation opts out
    # of); enable it the same non-persistent way before using its services.
    if not addon_utils.check("angelo_agent")[1]:
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    from angelo_agent import studio, saved_keys_ui
    from angelo_agent.blender_tools import BlenderExecutor
    from angelo_agent.saved_keys import FAILURES
    from angelo_agent import studio_generation
    from angelo_agent.studio_generation import MAX_IMAGE, MAX_MODEL, checked_glb, validated_image
    if not Path(studio_generation.__file__).resolve().is_relative_to(ADDON_ROOT):
        REASON = "studio_generation was not loaded from " + str(ADDON_ROOT)
CONNECTIONS = {} if REASON else {mode: str(uuid.UUID(arg(FLAGS[mode]))) for mode in ORDER}
run = {"phase": "keys", "index": 0, "pending": None, "executor": None, "catalog": None}


def window():
    return bpy.context.window_manager.windows[0]


def executor():
    if run["executor"] is None:
        run["executor"] = BlenderExecutor(window())
        run["executor"].studio_context = ui.get_controller()
    return run["executor"]


def fail(entry, stage, error):
    entry.update(stage=stage, outcome="failed", error=str(error)[:1000])
    log("%s failed at %s: %s" % (entry["connection_id"], stage, entry["error"]))


def key_state(mode):
    """True when the key is loaded and its non-billable check verified; False when failed; None while waiting."""
    entry, identity = report["providers"][mode], CONNECTIONS[mode]
    record = ui.connections().records.get(identity)
    if record is None or record["mode"] != mode:
        fail(entry, "key_load", "Connection is missing from the profile or has another mode")
        return False
    entry.update(connection_name=record["name"], mode=mode)
    if not saved_keys_ui.available():
        fail(entry, "key_load", "Keychain unavailable: " + saved_keys_ui.unavailable_reason())
        return False
    kind, outcome = saved_keys_ui._vault.state.get(identity, (None, None))
    entry["key_load"] = {"operation": kind, "outcome": outcome, "status": saved_keys_ui.missing_key_status(identity),
                         "storage": saved_keys_ui.storage_label(record, bool(ui._memory_keys.get(identity)))}
    if kind == "load" and (outcome in FAILURES or outcome in {"absent", "rejected"}):
        fail(entry, "key_load", "Saved key did not load (%s): %s" % (outcome, entry["key_load"]["status"]))
        return False
    if not ui._memory_keys.get(identity):
        return None
    entry.setdefault("key_loaded_at_s", now())
    check = ui._api_connections.results.get(identity)
    if check is None or check.state == "checking":
        return None
    entry["connection_check"] = {"state": check.state, "message": check.message, "verified": check.verified,
                                 "models": [m["id"] for m in check.models], "balance": check.balance, "at_s": now()}
    if check.state != "verified" or not check.models:
        fail(entry, "connection_check", check.message)
        return False
    return True


def choose(mode, models):
    entry = report["providers"][mode]
    mine = [m for m in models if m["connection"] == CONNECTIONS[mode]]
    entry["catalog"] = [{k: m.get(k) for k in ("id", "name", "ready", "verified", "account", "controls")} for m in mine]
    ready = {m["id"]: m for m in mine if m["ready"]}
    model = next((ready[i] for i in PREFERENCE[mode] if i in ready), None)
    if model is None:
        fail(entry, "catalog", "No ready model on this connection: " + ", ".join(m["id"] for m in mine))
        return None
    controls = model["controls"]
    if mode == "gemini_images_api":
        order = {"1K": 1, "2K": 2, "4K": 3}
        options = {"aspect": "1:1" if "1:1" in controls["aspect"] else controls["aspect"][0],
                   "resolution": min(controls["resolution"], key=lambda v: order.get(v, 99))}
        why = "Flash image tier before Pro; smallest resolution offered; square aspect"
    else:
        options = {"texture": False if False in controls["texture"] else controls["texture"][0],
                   "polycount": max(controls["polycount"]["minimum"], min(controls["polycount"]["maximum"], TRIPO_FACE_LIMIT))}
        why = "P2 (Tripo changelog: 100 credits without texture) before v3.1; texture/PBR off; low face limit"
    entry["selection"] = {"model": model["id"], "model_name": model["name"], "options": options, "reason": why,
                          "prompt": PROMPTS[mode]}
    return {"connection_id": CONNECTIONS[mode], "model": model["id"], "prompt": PROMPTS[mode], "options": options}


def submit(mode, args):
    """Exactly one execute() per provider. The attempt is recorded BEFORE the call and never reset."""
    entry = report["providers"][mode]
    tools = executor()
    try:
        tools.precondition("angelo_studio_generate", args)  # Validation only; the result holds a key digest.
        entry["usage_label"] = tools.usage_label("angelo_studio_generate", args)
    except Exception as error:  # noqa: BLE001 - preflight refusal, nothing submitted.
        fail(entry, "preflight", error)
        return None
    before = set(studio.library().assets)
    entry.update(submitted=True, submission_attempts=entry["submission_attempts"] + 1, stage="submission",
                 submitted_at_s=now(), submitted_utc=datetime.now(timezone.utc).isoformat())
    log("submitting ONE %s generation: %s %s" % (mode, args["model"], json.dumps(args["options"])))
    try:
        pending = tools.execute("angelo_studio_generate", args)
    except Exception as error:  # noqa: BLE001 - recorded, NEVER retried.
        created = sorted(set(studio.library().assets) - before)
        entry["assets_created"] = created
        fail(entry, "submission", error)
        entry["submission_note"] = ("A library asset was created, so a worker may have started; inspect provider "
                                    "history" if created else "No library asset was created; no worker started")
        return None
    entry.update(asset_id=pending.asset_id, stage="polling", transitions=[])
    return pending


def watch(mode, pending, deadline):
    entry = report["providers"][mode]
    try:
        result = pending.poll()
    except Exception as error:  # noqa: BLE001 - the controller treats this the same way.
        pending.cancel()
        fail(entry, "polling", "Monitoring raised: %s" % error)
        return True
    asset = studio.library().assets.get(pending.asset_id, {})
    state = (asset.get("status"), asset.get("progress"), asset.get("task_id"))
    if not entry["transitions"] or tuple(entry["transitions"][-1][1:]) != state:
        entry["transitions"].append([now()] + list(state))
        log("%s status=%s progress=%s task=%s" % (mode, *state))
    if asset.get("task_id") and "task_id_at_s" not in entry:
        entry.update(provider_task_id=asset["task_id"], task_route=asset.get("task_route"), task_id_at_s=now())
    if result is None:
        if time.monotonic() < deadline:
            return False
        pending.cancel()
        fail(entry, "polling", "Local monitoring timed out; stopped monitoring only. A submitted provider task "
             "may still finish and be billed (task ID: %s)" % asset.get("task_id"))
        entry["timed_out"] = True
        return True
    entry["completed_at_s"] = now()
    entry["seconds_to_completion"] = round(entry["completed_at_s"] - entry["submitted_at_s"], 3)
    entry["result"] = {k: v for k, v in result.items() if k != "views"}
    entry["result"]["preview_delivered"] = bool(result.get("views"))
    if result.get("status") != "ready":
        # Without a task ID the worker failed on (or before) the submission
        # response; after one, while polling, downloading or validating.
        stage = "worker_after_task_created" if asset.get("task_id") else "worker_generation_request"
        fail(entry, stage, result.get("error") or result.get("status"))
    else:
        entry.update(stage="validation", outcome="generated")
    return True


def verify_image(entry, asset):
    path = studio.library().checked_file(asset, asset["files"][0])
    data = path.read_bytes()
    check = {"path": str(path), "bytes": len(data), "within_limit": len(data) <= MAX_IMAGE}
    check["format"] = validated_image(data)  # Raises on unsupported/oversized images.
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        check["header_dimensions"] = [int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")]
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size  # Reading the size makes Blender decode the file.
        values = len(image.pixels)
        check.update(decoded_dimensions=[width, height], channels=image.channels, blender_format=image.file_format,
                     decoded=width > 0 and height > 0 and values == width * height * image.channels)
    finally:
        bpy.data.images.remove(image)
    check["passed"] = check["within_limit"] and check["decoded"] and min(check["decoded_dimensions"]) > 0
    return check


def verify_glb(entry, asset):
    path = studio.library().checked_file(asset, "model.glb")
    data = path.read_bytes()
    check = {"path": str(path), "bytes": len(data), "within_limit": len(data) <= MAX_MODEL}
    checked_glb(data)  # The app's own validation: header, JSON chunk, self-contained resources.
    check["checked_glb"] = True
    length = int.from_bytes(data[12:16], "little")
    document = json.loads(data[20:20 + length])
    accessors = document.get("accessors", [])
    primitives = [p for m in document.get("meshes", []) for p in m.get("primitives", [])]
    check.update(meshes=len(document.get("meshes", [])), primitives=len(primitives),
                 vertices=sum(accessors[p["attributes"]["POSITION"]]["count"] for p in primitives if "POSITION" in p.get("attributes", {})),
                 triangles=sum(accessors[p["indices"]]["count"] // 3 for p in primitives if "indices" in p and p.get("mode", 4) == 4),
                 materials=len(document.get("materials", [])), images=len(document.get("images", [])),
                 generator=str(document.get("asset", {}).get("generator", ""))[:120])
    if asset.get("thumbnail"):
        check["thumbnail"] = str(studio.library().checked_file(asset, asset["thumbnail"]))
    check["passed"] = check["within_limit"] and check["meshes"] > 0 and check["vertices"] > 0
    return check


def verify(mode):
    entry = report["providers"][mode]
    if entry.get("outcome") != "generated":
        return
    asset = studio.library().assets[entry["asset_id"]]
    entry["job_state"] = {k: asset.get(k) for k in ("status", "progress", "task_id", "task_route", "files", "thumbnail", "options")}
    try:
        entry["verification"] = verify_image(entry, asset) if mode == "gemini_images_api" else verify_glb(entry, asset)
        entry["outcome"] = "worked" if entry["verification"]["passed"] else "failed"
        if not entry["verification"]["passed"]:
            entry["error"] = "Output saved but failed verification"
        entry["stage"] = "done"
    except Exception as error:  # noqa: BLE001
        fail(entry, "validation", error)


def finish():
    report["complete"] = True
    report["elapsed_s"] = now()
    try:
        write_report([ui._memory_keys.get(i, "") for i in CONNECTIONS.values()])
        log("DONE report=%s" % REPORT_PATH)
    except Exception:  # noqa: BLE001 - never leave the app running after the check.
        log("REPORT WRITE FAILED")
        traceback.print_exc()
    bpy.ops.wm.quit_blender()
    return None


def tick():
    try:
        if run["phase"] == "keys":
            states = {m: key_state(m) for m in ORDER if report["providers"][m]["outcome"] == "pending"}
            if any(s is None for s in states.values()) and now() < KEY_TIMEOUT:
                return 0.5
            for mode, value in states.items():
                if value is None:
                    fail(report["providers"][mode], "key_load", "Key load/check did not finish within %.0fs" % KEY_TIMEOUT)
            run["phase"] = "catalog"
            return 0.1
        if run["phase"] == "catalog":
            catalog = executor().execute("angelo_studio_catalog", {})
            run["catalog"] = catalog["models"]
            run["phase"] = "next"
            return 0.1
        if run["phase"] == "next" and CATALOG_ONLY:
            for mode in ORDER:
                entry = report["providers"][mode]
                entry["catalog"] = [{k: m.get(k) for k in ("id", "name", "ready", "verified", "account", "controls")}
                                    for m in run["catalog"] if m["connection"] == CONNECTIONS[mode]]
                if entry["outcome"] == "pending":
                    entry.update(outcome="catalog_only", stage="done")
            log("catalog only: nothing submitted")
            return finish()
        if run["phase"] == "next":
            while run["index"] < len(ORDER):
                mode = ORDER[run["index"]]
                run["index"] += 1
                entry = report["providers"][mode]
                if entry["outcome"] != "pending" or entry["submitted"]:
                    continue
                args = choose(mode, run["catalog"])
                if args is None:
                    continue
                run["pending"] = (mode, submit(mode, args),
                                  time.monotonic() + (IMAGE_TIMEOUT if mode == "gemini_images_api" else MODEL_TIMEOUT))
                if run["pending"][1] is None:
                    run["pending"] = None
                    continue
                run["phase"] = "watch"
                return 1.0
            run["phase"] = "verify"
            return 0.1
        if run["phase"] == "watch":
            mode, pending, deadline = run["pending"]
            if not watch(mode, pending, deadline):
                return 1.0
            run["pending"] = None
            run["phase"] = "next"
            return 0.1
        if run["phase"] == "verify":
            for mode in ORDER:
                verify(mode)
            return finish()
    except Exception:  # noqa: BLE001 - recorded; never resubmits.
        report["errors"].append({"phase": run["phase"], "traceback": traceback.format_exc()[-3000:]})
        log("driver error in %s; finishing without any new submission" % run["phase"])
        if run["pending"] and run["pending"][1] is not None:
            run["pending"][1].cancel()
        for mode in ORDER:
            if report["providers"][mode]["outcome"] in {"pending", "generated"}:
                report["providers"][mode].setdefault("error", "Driver error in phase " + run["phase"])
        return finish()
    return 0.5


def main():
    if REASON:
        report["refused"] = REASON
        log("REFUSED: " + REASON + " (nothing submitted)")
        write_report()
        bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=0.5)
        return
    report["addon_path"] = str(Path(ui.__file__).parent)
    report["addon_enabled"] = bool(addon_utils.check("angelo_agent")[1])
    for mode in ORDER:
        report["providers"][mode] = {"connection_id": CONNECTIONS[mode], "stage": "key_load", "outcome": "pending",
                                     "submitted": False, "submission_attempts": 0}
    log("authorized: at most one billed generation per provider; waiting for saved keys")
    bpy.app.timers.register(tick, first_interval=1.0)


main()
