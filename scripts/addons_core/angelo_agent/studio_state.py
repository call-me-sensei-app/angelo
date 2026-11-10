# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local Media Studio library and non-secret preferences. No Blender dependency.

Each asset/folder owns its own atomic record, so a second Angelo window cannot
overwrite the entire library with a stale index. API keys never enter this store.
"""

import json
import os
import time
import uuid
from pathlib import Path

from . import product_env, shared_replace
from .storage import connection_directory
from .provider_registry import GENAI, PROVIDER_NAMES, SPECS, TEAM, service_mode
from .references import image_type, MIME
from .ui_text import iface_, n_, rpt_
from .refusal_codes import coded
from .display_text import describe_error, error_message

DEFAULT_ENHANCER = {"connection": "AUTO_GEMINI", "model": "gemini-3.8-flash"}
ASPECTS = ("1:1", "16:9", "9:16", "4:3", "3:4")
# Display text for values the editor stores as identifiers. Marked with ``n_`` here and
# translated with ``iface_`` where drawn; the identifiers themselves (and what a model sees) stay English.
KIND_LABELS = {"IMAGE": n_("Image"), "3D": n_("3D"), "VIDEO": n_("Video")}
KINDS = frozenset(KIND_LABELS)
MEDIA_KINDS = ("IMAGE", "3D", "VIDEO")   # Media Studio's generation kinds, in its tabs' order.
FOLDER_ROWS = (("ALL", n_("All items")), ("UNFILED", n_("Unfiled")))
# Fields that take whole paragraphs (Enter is a new line; up to 16000 characters); every other field is one line of up
# to 200. "workflow_text" is the Workflows tab's description (studio_workflows.INPUT_KEY).
MULTILINE_FIELDS = frozenset({"prompt", "expanded_prompt", "workflow_text"})
CARD_STATUS = {"ready": n_("3D asset"), "generating": n_("Generating…"), "texturing": n_("Texturing…"),
               "failed": n_("Generation failed"), "stopped": n_("Stopped locally"),
               # A queued provider job's stages (fal_video, byteplus_video) around "generating", naming its provider.
               "uploading": n_("Uploading frames…"), "submitting": n_("Sending to {provider}…"),
               "queued": n_("Queued at {provider}…"), "downloading": n_("Downloading video…"),
               "cancelling": n_("Cancelling at {provider}…"), "finishing": n_("Finishing at {provider}…")}
MODEL_CARD_STATUS = {"uploading": n_("Uploading images…"), "downloading": n_("Downloading model…")}   # A fal 3D job.
READY_CARD = {"VIDEO": n_("Video")}   # A ready item with no picture yet says what it is.
TASK_SAVED = n_("Task saved")
STATUS_TITLES = {"ready": n_("Ready"), "generating": n_("Generating"), "texturing": n_("Texturing"),
                 "failed": n_("Failed"), "stopped": n_("Stopped"), "uploading": n_("Uploading"),
                 "submitting": n_("Sending"), "queued": n_("Queued"), "downloading": n_("Downloading"),
                 "cancelling": n_("Cancelling"), "finishing": n_("Finishing")}
OPTION_LABELS = {"auto": n_("Auto"), "low": n_("Low"), "medium": n_("Medium"), "high": n_("High"), "xhigh": n_("Xhigh"),
                 "max": n_("Max"), "opaque": n_("Opaque"), "transparent": n_("Transparent", "Angelo")}
IMPORTED_MODEL = n_("Imported image")


class Templated:
    """An error whose English text is a template, so the editor can translate it where it shows it.

    ``str(error)`` stays the formatted English sentence (logs, tool results, stored errors);
    ``template`` and ``fields`` let ``error_text`` render it in the interface language.
    """

    def __init__(self, template="", **fields):
        super().__init__(template.format(**fields) if fields else template)
        self.template, self.fields = template, fields


class StudioValueError(Templated, ValueError):
    pass


def error_text(error):
    """An error for a report, in the interface language: ``display_text.error_message`` (a template's fields, such as
    a provider's detail, stay as sent; an unexpected exception is a plain sentence, never Python's wording)."""
    return error_message(error)


def refusal_text(error):
    """The whole reason of a refusal ``bpy.ops`` raised for a Studio operator ("Error: <reason>"), for the notice:
    every line of it, never only the last (``display_text.describe_error``)."""
    return describe_error(error)


NOTICE_ROWS = 3   # The Studio notice wraps to this many rows; a longer one ends its last row with "…".


def notice_rows(value, measure, width, max_rows=NOTICE_ROWS):
    """The Studio notice ``value`` (already translated) as at most ``max_rows`` measured rows within ``width``.

    ``measure(text)`` is a drawn width. Rows come back drawable (visual order); when the notice needs more rows,
    the last one shown holds the rest of the sentence cut with "…" at the end of its reading order.
    """
    from . import canvas_rtl, rtl_text
    rows = canvas_rtl.rows(str(value), measure, width)
    if len(rows) <= max_rows:
        return rows
    logical = [rtl_text.to_logical(row) if rtl_text.is_visual(row) else row for row in rows[max_rows - 1:]]
    text = rtl_text.to_logical(str(value)) if rtl_text.is_visual(str(value)) else str(value)
    rest = logical[0]
    for row in logical[1:]:   # Rows cut at a space get it back; a cut inside a word (CJK, long words) does not.
        rest += row if rest + row in text else " " + row
    return rows[:max_rows - 1] + [canvas_rtl.elide(rest, measure, width)]


def refusable(state, call):
    """Run a Studio click. An operator it starts may refuse (no account, no Gemini key): ``bpy.ops`` raises its
    report, which escaping the input modal showed as a traceback and ended the Studio's input. The reason
    becomes the Studio's notice instead (unless the operator set one), and the Studio keeps taking clicks."""
    notice = state.get("notice")
    try:
        call()
    except RuntimeError as error:
        if state.get("notice") == notice:
            state["notice"] = refusal_text(error)


def model_label(name):
    """An asset's model name: the one name Angelo writes itself is translated, provider model names are not."""
    return iface_(name) if name == IMPORTED_MODEL else name


def setup_text(model, key):
    """A model row's text. Rows with no connection (or a lost one) carry sentences Angelo wrote; connected rows carry names."""
    return model[key] if model["connection"] and model["mode"] else iface_(model[key])


def account_line(model):
    """The line under the Create panel's model: its account, or which account is missing. Never the words of
    the Connect in AI Providers button below it, so the panel says them once."""
    if not model:
        return ""
    if model["connection"]:
        return setup_text(model, "account")
    return iface_("No {provider} account yet", provider=PROVIDER_NAMES[SPECS[model["mode"]]["provider"]])


def status_title(asset):
    return iface_(STATUS_TITLES.get(asset["status"], asset["status"].title()))


def card_status(asset):
    if asset["status"] == "ready" and asset["kind"] in READY_CARD:
        return iface_(READY_CARD[asset["kind"]])
    labels = MODEL_CARD_STATUS if asset["kind"] == "3D" and asset["status"] in MODEL_CARD_STATUS else CARD_STATUS
    return iface_(labels.get(asset["status"], TASK_SAVED), provider=job_provider(asset))


def job_provider(asset):
    """The provider a queued media job runs on, as its labels and notices name it: BytePlus for a Seedance task on a
    BytePlus connection (personal or team), fal otherwise. An asset left mid-submit may carry no mode; it was fal's."""
    from .provider_registry import SPECS, service_mode
    mode = asset.get("mode") or ""
    return "BytePlus" if asset.get("byteplus_task") or (mode in SPECS and service_mode(mode) == "seedream_api") else "fal"


def library_directory():
    override, variable = product_env.lookup("LIBRARY_DIR")
    path = Path(override or connection_directory().parent / "media-studio").expanduser()
    if not path.is_absolute():
        raise coded(StudioValueError(n_("{variable} must be an absolute path"), variable=variable), "UNSUPPORTED")
    return path


def atomic_json(path, value):
    """Write ``value`` to ``path`` whole, or not at all. The rename is every add-on file's (``shared_replace``):
    on Windows it lands while a reader holds ``path`` open, as ``read_json`` does (a job's state, read by the
    library's reload or another window, while its worker saves), and a plain ``os.replace`` there refused the save:
    a finished job then never left "generating" (CI 41887060350, ``test_native_studio``)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + str(uuid.uuid4()) + ".tmp")
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        shared_replace.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, default):
    """``path``'s JSON, else ``default``. Read through ``shared_replace``: never a writer's refusal."""
    try:
        if path.stat().st_size > 1024 * 1024:
            return default
        return json.loads(shared_replace.read_bytes(path).decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        return default


def model_options(kind, records, health):
    """Documented choices remain discoverable, but only a checked key can run.

    Missing accounts are setup choices, not entitlements. Every connected model
    retains its named account identity; no prefix-based or billing fallback.
    A team connection's entry has its personal counterpart's ``mode`` (request
    formats and controls) and ``team=True``; its ``connection`` selects the
    gateway binding. Team services appear only while signed in.
    """
    output = []
    for mode, spec in SPECS.items():
        if spec["group"] != GENAI or not any(m.get("kind", spec["kind"]) == kind for m in spec["models"]):
            continue
        team = spec["method"] == TEAM
        accounts = [r for r in records if r["mode"] == mode]
        if team and not accounts:
            continue  # Setup rows are the personal services'; a team service comes from signing in.
        for record in accounts or [None]:
            check = health.get(record["id"]) if record else None
            admitted = {m["id"] for m in check.models} if check else set()
            for model in (m for m in spec["models"] if m.get("kind", spec["kind"]) == kind):
                output.append(dict(model, mode=service_mode(mode), connection=record["id"] if record else "",
                    account=record["name"] if record else n_("Connect in AI Providers"), team=team,
                    ready=bool(check and check.state in {"verified", "configured"} and model["id"] in admitted),
                    verified=bool(check and check.verified)))
    return output


BUTTON_PAD, ICON_ROOM = 22, 18   # studio_draw.button: text from 12 px in, elided 10 px before the right edge; an icon 18.


def button_width(label, measure, size=12, icon=False, minimum=0):
    """The width a Studio button needs to show ``label`` whole in the interface language (``measure(text, size)``
    is the canvas's). ``minimum`` keeps the English layout; a longer translation widens the button, never cuts it."""
    import math
    return max(minimum, math.ceil(measure(label, size) + BUTTON_PAD + (ICON_ROOM if icon else 0)))


def chip_grid(labels, measure, size, width, gap=6, cell=0):
    """Equal chips in a row (Studio's resolution and aspect choices) that each show their label whole: ``cell`` is a
    preferred width (0 shares the row), never narrower than the widest label needs; chips that don't fit one row wrap
    into balanced rows (3 + 3, never 5 + 1). Returns the chip width and each chip's (x offset, row)."""
    import math
    count = len(labels)
    if not count:
        return 0, []
    need = max(button_width(label, measure, size) for label in labels)
    per_row = count
    while per_row > 1 and (width - gap * (per_row - 1)) / per_row < need:
        per_row -= 1
    per_row = math.ceil(count / math.ceil(count / per_row))
    shared = (width - gap * (per_row - 1)) / per_row
    chip = min(width, max(need, min(cell, shared) if cell else shared))
    return chip, [((index % per_row) * (chip + gap), index // per_row) for index in range(count)]


def top_row(entries, measure, x=14, gap=8):
    """Studio's top row left to right: ``entries`` are (label, icon, minimum, gap before it) and the result is each
    one's (x, width) and the row's right end (where the "Turn into a flow" pill may start)."""
    placed = []
    for index, (label, icon, minimum, before) in enumerate(entries):
        x += before if index else 0
        width = button_width(label, measure, 12, icon, minimum)
        placed.append((x, width))
        x += width
    return placed, x


def studio_layout(width, height, collapsed=False):
    """Non-overlapping responsive columns in native region pixels."""
    gap, margin = 12, 14
    body_height = max(0, height - 72)
    available = max(0, width - margin * 2)
    # On a narrow dock Create is its own view, not an overlay over assets or
    # Blender. Collapsing it gives the entire region to the asset library.
    compact = width < 740
    create_width = min(304, available) if not collapsed else 0
    if compact:
        create_width = available if not collapsed else 0
        folder_width = 0
    else:
        folder_width = 170 if width >= 900 else 142
    x = margin
    create = (x, 12, create_width, body_height)
    if create_width:
        x += create_width + gap
    if compact and not collapsed:
        return {"create": create, "folders": (x, 12, 0, body_height),
                "assets": (x, 12, 0, body_height), "compact": True}
    folders = (x, 12, folder_width, body_height)
    if folder_width:
        x += folder_width + gap
    assets = (x, 12, max(0, width - margin - x), body_height)
    return {"create": create, "folders": folders, "assets": assets, "compact": compact}


def image_dimensions(aspect, resolution, override="", mode="openai_images_api"):
    if override.strip():
        pieces = override.strip().lower().split("x")
        if len(pieces) != 2 or not all(p.isdecimal() for p in pieces):
            raise ValueError(n_("Use an output size such as 1536x1024"))
        w, h = map(int, pieces)
    else:
        a, b = map(int, aspect.split(":"))
        # OpenAI 1K means its documented recommended sizes. Higher dimensions
        # use the documented custom-size bounds, never a made-up '4K' enum.
        if mode == "openai_images_api" and resolution == "1K":
            w, h = (1024, 1024) if a == b else (1536, 1024) if a > b else (1024, 1536)
        elif mode == "seedream_api":
            # Seedream's 2K/4K presets represent approximately 4/16 megapixels,
            # not a 2K/4K long edge. Keep non-square output within its area bounds.
            import math
            pixels = {"2K": 2048 ** 2, "4K": 4096 ** 2}[resolution]
            w, h = round(math.sqrt(pixels * a / b) / 16) * 16, round(math.sqrt(pixels * b / a) / 16) * 16
            if w * h > 16777216:
                if w >= h:
                    w -= 16
                else:
                    h -= 16
        else:
            edge = {"1K": 1024, "2K": 2048, "4K": 3840}[resolution]
            w, h = ((edge, round(edge * b / a / 16) * 16) if a >= b
                    else (round(edge * a / b / 16) * 16, edge))
    maximum = 8192 if mode == "seedream_api" else 3840
    if min(w, h) < 16 or max(w, h) > maximum or w % 16 or h % 16 or not 1 / 3 <= w / h <= 3:
        raise StudioValueError(n_("Dimensions must be multiples of 16, at most {maximum} px, with an aspect ratio up to 3:1"), maximum=maximum)
    if mode == "openai_images_api" and not 655360 <= w * h <= 8294400:
        raise ValueError(n_("OpenAI output must contain 655,360–8,294,400 pixels; choose a smaller preset"))
    if mode == "seedream_api" and not 3686400 <= w * h <= 16777216:
        raise ValueError(n_("Seedream output must contain 3,686,400–16,777,216 pixels"))
    return f"{w}x{h}"


class StudioLibrary:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else library_directory()
        self.assets, self.folders = {}, {}
        self.enhancer_path = self.directory / "ai-enhance.json"
        self.enhancer = dict(DEFAULT_ENHANCER)
        self.reload()

    def reload(self):
        for collection in ("assets", "folders"):
            records = {}
            for path in sorted((self.directory / collection).glob("*.json"))[:10000]:
                value = read_json(path, {})
                try:
                    identity = str(uuid.UUID(value["id"]))
                    if path.stem != identity:
                        continue
                    if collection == "assets" and value.get("kind") not in KINDS:
                        continue
                    if collection == "assets":
                        job = read_json(self.directory / "outputs" / identity / "job-state.json", {})
                        if isinstance(job, dict):
                            # An empty picture or file list in the job's state never hides one made later (a
                            # video's preview.png is added on the main thread, after its job ended).
                            value.update({k: v for k, v in job.items() if k in {"task_id", "task_route", "preview_task_id", "progress", "status", "error", "files", "thumbnail", "fal", "byteplus_task", "queue_position"}
                                          and not (k in {"files", "thumbnail"} and not v and value.get(k))})
                    records[identity] = value
                except (KeyError, TypeError, ValueError):
                    continue
            setattr(self, collection, records)
        value = read_json(self.enhancer_path, {})
        if isinstance(value, dict):
            self.enhancer = {k: str(value.get(k) or v)[:160] for k, v in DEFAULT_ENHANCER.items()}

    def configure_enhancer(self, connection, model):
        if connection != "AUTO_GEMINI":
            connection = str(uuid.UUID(connection))
        if not model or len(model) > 160 or any(not (c.isalnum() or c in "-._/[]") for c in model):
            raise ValueError(n_("Choose a valid enhancement model"))
        self.enhancer = {"connection": connection, "model": model}
        atomic_json(self.enhancer_path, self.enhancer)

    def folder(self, name):
        name = name.strip()[:100]
        if not name:
            raise ValueError(n_("Give the folder a name"))
        identity = str(uuid.uuid4())
        record = {"id": identity, "name": name}
        atomic_json(self.directory / "folders" / (identity + ".json"), record)
        self.folders[identity] = record
        return record

    def add(self, kind, prompt, model, connection="", folder="", status="generating", **extra):
        identity = str(uuid.uuid4())
        record = {"id": identity, "kind": kind, "prompt": prompt[:16000], "model": model,
                  "connection": connection, "folder": folder, "status": status,
                  "created": time.time(), "progress": 0, "files": [], "thumbnail": ""}
        # lineage: which Generation Flow step and input key produced this output.
        allowed = {"mode", "model_id", "options", "reference_names", "lineage", "price"}
        record.update({k: v for k, v in extra.items() if k in allowed})
        self.update(record)
        return record

    def update(self, record):
        identity = str(uuid.UUID(record["id"]))
        atomic_json(self.directory / "assets" / (identity + ".json"), record)
        self.assets[identity] = dict(record)

    def result_directory(self, identity):
        path = self.directory / "outputs" / str(uuid.UUID(identity))
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def checked_file(self, asset, relative):
        root = self.directory / "outputs" / str(uuid.UUID(asset["id"]))
        path = root / relative
        if Path(relative).name != relative or path.is_symlink() or not path.is_file():
            raise coded(ValueError(n_("The local asset is missing or invalid")), "VALIDATION")
        return path

    def visible(self, kind, folder="ALL", query=""):
        query = query.casefold().strip()
        return sorted((a for a in self.assets.values() if a["kind"] == kind
            and (folder == "ALL" or a.get("folder", "") == ("" if folder == "UNFILED" else folder))
            and (not query or query in (a["prompt"] + " " + a["model"]).casefold())),
            key=lambda a: a["created"], reverse=True)

    def import_image(self, source, folder=""):
        source = Path(source)
        if not source.is_file() or source.is_symlink():
            raise coded(ValueError(n_("Select a regular image file")), "VALIDATION")
        with shared_replace.open_file(source) as handle:   # Sharing delete: never refuses its writer.
            data = handle.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise coded(ValueError(n_("Local images must be at most 20 MiB")), "VALIDATION")
        from .studio_generation import validated_image
        suffix = validated_image(data)
        asset = self.add("IMAGE", source.name, IMPORTED_MODEL, folder=folder, status="ready")
        path = self.result_directory(asset["id"]) / ("image" + suffix)
        path.write_bytes(data)
        asset.update(files=[path.name], thumbnail=path.name, progress=100)
        self.update(asset)
        return asset
