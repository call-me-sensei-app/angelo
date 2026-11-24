# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Writing text files into a chat's **working folder** (``context_folder_grants``; 2.1.0): research notes,
checklists, plans, tables. The one way the agent writes there: agent scripts get no new write access.

**The tool** ``angelo_context_folder_write`` (``SPEC``) has two actions: ``CREATE`` a new text file (refused when the
name is taken) and ``UPDATE`` the whole text of an existing one (refused when it is missing). Nothing deletes, moves or
renames a file, and nothing writes into a reference folder: the working folder is the only target.

**What it may write** (``validate``, ``context_folder_working.WorkingFolder``): a path relative to the working
folder, at most ``MAX_DEPTH`` names deep, each a plain file or folder name (no hidden name, no repository or
credential folder however it is spelled, no Windows short name, no character Windows gives a meaning to, no trailing
dot or space, no device name), ending in one of ``SUFFIXES``; text of at most ``MAX_CHARS`` characters and
``MAX_BYTES`` bytes as UTF-8, with no NUL. Up to ``MAX_CREATED`` new files per folder per chat, so a runaway turn
can't fill the disk. How it reaches the disk, atomically and never outside the working folder: ``WorkingFolder``.

**Approval** (``auto_reason``, read by ``auto_reasons.bounded_reason``): creating a document, or updating a file the
agent created in this chat that nobody changed since (held in C: ``context_folder_grants.Grants.owns``), follows the
chat's approval mode (Auto: a bounded typed edit, no question). Creating a data or settings file (``CONFIG``) and
updating any other file always ask the person, in every mode; such an update's run is refused unless their own click
approved it. The card's Approve runs it with no Undo step, as task memory's notes (``ui.ANGELO_OT_approve``). Results
name the folder and the file relative to it, never a path on the disk, and refusals carry closed codes that say
nothing ran.
"""

import re
import unicodedata
from pathlib import Path

from . import files, grant_authority
from .context_folder_working import CHANGED, INSIDE_REFERENCE, MISSING, NO_PUBLISH, Refusal, WorkingFolder  # noqa: F401
from .ui_text import iface_, n_, plural_

TOOL = "angelo_context_folder_write"
ACTIONS = ("CREATE", "UPDATE")
NOT_OFFERED = ("DELETE", "REMOVE", "MOVE", "RENAME", "COPY", "APPEND", "CHMOD", "LINK", "MKDIR", "RMDIR")
# Documents a person reads, and data and settings formats tools read (CONFIG: creating one always asks, S5).
DOCUMENTS = frozenset({".md", ".markdown", ".txt", ".text", ".csv", ".tsv", ".rst"})
CONFIG = frozenset({".json", ".jsonl", ".yaml", ".yml"})
SUFFIXES = DOCUMENTS | CONFIG
MAX_CHARS = 131072                  # As the file tools' own writes (tool_specs.validate_call).
MAX_BYTES = 512 * 1024              # MAX_CHARS of any text as UTF-8, bounded once more after encoding.
MAX_DEPTH = 8
MAX_NAME = 255
MAX_CREATED = 200
_SHORT_NAME = re.compile(r"~[0-9]")   # A Windows 8.3 short name (GIT~1) is another spelling of a long one.
_WINDOWS_MEANING = frozenset('<>:"|?*\\')
_DEVICES = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$"} | {"com%d" % n for n in range(10)}
                     | {"lpt%d" % n for n in range(10)})

# The tool's refusals, said for the model (never translated: model-facing text).
NO_WORKING = ("This chat has no working folder, so nothing can be written. "   # i18n: model-facing
              "The person can attach a folder or make one the working folder from its card.")
REFERENCE_ONLY = ("That folder is a reference folder: it is read-only. "   # i18n: model-facing
                  "Only the working folder can be written.")
EXISTS = "A file of that name is already there; nothing was written. Use UPDATE to change it."   # i18n: model-facing
MISSING_FILE = "No file of that name is there; nothing was written. Use CREATE for a new file."   # i18n: model-facing
ASKS = ("This file was in the folder before Angelo wrote it, "   # i18n: model-facing
        "so changing it needs the person's own approval; nothing was written.")
ASKS_CONFIG = ("A data or settings file needs the person's own approval to be created; "   # i18n: model-facing
               "nothing was written.")
TOO_MANY = ("This chat has created %d files in the working folder, "   # i18n: model-facing
            "the most it may; nothing was written." % MAX_CREATED)
SWITCHED = "The working folder changed since this write was prepared; nothing was written."   # i18n: model-facing
ELSEWHERE = ("That place can't be written: the path goes through a link or junction, "   # i18n: model-facing
             "another drive, or a hidden, private or application folder. Nothing was written.")
NOT_ALLOWED = ("Angelo may not write in that place now (the app refused it); "   # i18n: model-facing
               "nothing was written.")

SPEC = {
    "name": TOOL,
    "description": ("Write a UTF-8 text file (notes, a checklist, a plan, "   # i18n: model-facing
                    "csv/json/markdown/plain text) in THIS chat's working folder (context_folders: role working): "
                    "CREATE a new file or UPDATE the whole text of one. "
                    "Paths are relative to the working folder. Never deletes, moves or renames; reference folders are "
                    "read-only. Changing a file that was there before always asks the person."),
    "inputSchema": {"type": "object", "properties": {
        "action": {"enum": list(ACTIONS)}, "path": {"type": "string", "maxLength": 1024},
        "content": {"type": "string", "maxLength": MAX_CHARS}, "folder": {"type": "string", "maxLength": 200},
    }, "required": ["action", "path", "content"], "additionalProperties": False},
    "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
}


def check_path(relative):
    """The path's names, each a plain name a file may have on every platform Angelo runs on, or ValueError."""
    if not isinstance(relative, str) or not relative or len(relative) > 1024 or any(ord(c) < 32 for c in relative):
        raise Refusal("Name the file with a printable path relative to the working folder", code="VALIDATION")
    if any(c in _WINDOWS_MEANING for c in relative.replace("/", "")) or relative.startswith("/"):
        raise Refusal("Use / between names and no < > : \" | ? * \\ characters", code="VALIDATION")
    parts = relative.split("/")
    if len(parts) > MAX_DEPTH or any(not part for part in parts):
        raise Refusal("Use a path of at most %d names, with no empty name" % MAX_DEPTH, code="VALIDATION")
    for part in parts:
        folded = unicodedata.normalize("NFKC", part).casefold()
        if part in (".", "..") or folded.startswith(".") or folded in files.BLOCKED or part[-1] in ". " \
                or folded.split(".")[0].strip() in _DEVICES or len(part.encode("utf-8")) > MAX_NAME \
                or folded != folded.strip() or _SHORT_NAME.search(part):
            raise Refusal("Hidden, repository, credential, device and short (8.3) names can't be written",
                          code="VALIDATION")
    if Path(parts[-1]).suffix.lower() not in SUFFIXES:
        raise Refusal("Only text files can be written: " + ", ".join(sorted(SUFFIXES)), code="VALIDATION")
    return relative


def validate(arguments):
    """The checked arguments (``tool_specs.validate_call``)."""
    from .context_folder import validate_folder
    action = arguments.get("action")
    if action in NOT_OFFERED or (isinstance(action, str) and action.upper() in NOT_OFFERED):
        raise ValueError("Files in the working folder can't be deleted, moved or renamed; only CREATE and UPDATE")
    if action not in ACTIONS:
        raise ValueError("Choose CREATE or UPDATE")
    check_path(arguments.get("path"))
    content = arguments.get("content")
    if not isinstance(content, str) or len(content) > MAX_CHARS or "\x00" in content \
            or len(content.encode("utf-8", "surrogatepass")) > MAX_BYTES:
        raise ValueError("Write UTF-8 text of at most %d characters, with no NUL" % MAX_CHARS)
    try:
        content.encode("utf-8")
    except UnicodeError:
        raise ValueError("Write valid UTF-8 text") from None
    validate_folder(arguments)
    return dict(arguments)


# Binding, approval and the run (main thread) ----------------------------------------------------------------------

def _target(controller, arguments):
    """``(folder id, label, workspace)`` of the chat's working folder, or a ``Refusal`` said for the model. A worker's
    chat, on either path, is refused as every tool it can't run is (``worker_refusals``; S2)."""
    from . import context_folder_host as host
    if host.worker_chat(controller):
        from . import worker_refusals
        worker_refusals.admit(TOOL, arguments)
    working = host.working(controller)
    if working is None:
        raise Refusal(NO_WORKING, code=_code(NO_WORKING))
    named = arguments.get("folder")
    if named is not None and named != working["label"]:
        if any(record["label"] == named for record in host.labels(controller)):
            raise Refusal(REFERENCE_ONLY, code=_code(REFERENCE_ONLY))
        from .context_folder import UNKNOWN_FOLDER
        raise Refusal(UNKNOWN_FOLDER, code=_code(UNKNOWN_FOLDER))
    try:
        return working["id"], working["label"], host.working_workspace(controller, working)
    except ValueError:
        raise Refusal(NO_WORKING, code=_code(NO_WORKING)) from None


def said(error):
    """The model's sentence for a refusal: the tool's own as it is, anything a lower layer raised as one sentence that
    names no file-tool internals (break-it NIT: "Snapshot storage ...")."""
    return str(error) if isinstance(error, Refusal) else ELSEWHERE


def _code(text):
    """The closed refusal code of one of this tool's refusals (``refusal_codes``): never UNKNOWN, which would read as a
    change that may have happened and hold the request's other edits."""
    return {REFERENCE_ONLY: "OWNERSHIP", INSIDE_REFERENCE: "OWNERSHIP", NO_WORKING: "UNSUPPORTED",
            NO_PUBLISH: "UNSUPPORTED", TOO_MANY: "LIMIT_EXCEEDED", CHANGED: "STALE_CONTEXT",
            SWITCHED: "STALE_CONTEXT", ASKS: "OWNERSHIP", ASKS_CONFIG: "OWNERSHIP",
            NOT_ALLOWED: "READ_ONLY"}.get(text, "VALIDATION")


def refused(text):
    """A refusal before anything was written, said without a path, with its closed code."""
    from .refusal_codes import RefusalError
    error = RefusalError(text[:500], code=_code(text))
    error.partial_changes_possible = False   # Nothing ran: the request's other edits are not held (error_result).
    return error


def precondition(controller, call):
    """``(folder id, signature)``: the working folder and the file as this call is approved for (``Controller.
    _precondition``); its run is refused when either changed. A refusal here wrote nothing (``refused``)."""
    from .refusal_codes import RefusalError
    try:
        folder_id, _label, workspace = _target(controller, call.arguments)
        return folder_id, workspace.signature(check_path(call.arguments["path"]))
    except RefusalError:
        raise   # Its own refusal, or a worker's chat's (worker_refusals): coded, path-free, nothing ran.
    except PermissionError:   # C refused the place (its message holds the path).
        raise refused(NOT_ALLOWED) from None
    except OSError as error:   # Never the error's file name: it is a path on the person's disk.
        text = "The file couldn't be checked (%s)"   # i18n: model-facing
        raise refused(text % (error.strerror or "error")) from None
    except ValueError as error:
        raise refused(said(error)) from None


def own_file(controller, binding):
    """Whether the bound file is one the agent created here and nobody changed since, as C holds it (M2)."""
    from . import context_folder_host as host
    folder_id, signature = binding
    return signature != MISSING and host.grants(controller.store.directory).owns(
        controller.state["id"], folder_id, signature)


def auto_reason(controller, call):
    """None: Auto may run it (a new document, or the agent's own unchanged file). Else the rule that asks:
    ``auto_reasons.CONFIG_FILE`` for a new data or settings file, ``auto_reasons.PREEXISTING`` for any other file."""
    from . import auto_reasons
    binding = getattr(call, "precondition", None)
    if not isinstance(binding, tuple) or len(binding) != 2:
        return auto_reasons.PREEXISTING
    if binding[1] == MISSING:
        suffix = Path(str(call.arguments.get("path", ""))).suffix.lower()
        return None if suffix in DOCUMENTS else auto_reasons.CONFIG_FILE
    return None if own_file(controller, binding) else auto_reasons.PREEXISTING


def run(controller, call):
    """The write, at its run (``Controller._execute_tool``). Returns the tool's result; never raises for a refusal."""
    from . import context_folder_host as host
    from .refusal_codes import RefusalError
    arguments = call.arguments
    try:
        folder_id, label, workspace = _target(controller, arguments)
        bound_id, before = call.precondition
        if bound_id != folder_id:
            raise Refusal(SWITCHED, code=_code(SWITCHED))
        store, chat = host.grants(controller.store.directory), controller.state["id"]
        if arguments["action"] == "CREATE" and before != MISSING:
            raise Refusal(EXISTS, code=_code(EXISTS))
        if arguments["action"] == "UPDATE" and before == MISSING:
            raise Refusal(MISSING_FILE, code=_code(MISSING_FILE))
        if arguments["action"] == "UPDATE" and not own_file(controller, call.precondition) and (
                getattr(call, "automatic", False) or not grant_authority.granting()):
            raise Refusal(ASKS, code=_code(ASKS))   # Only the person's own click on its card runs it.
        if arguments["action"] == "CREATE" and Path(arguments["path"]).suffix.lower() in CONFIG and (
                getattr(call, "automatic", False) or not grant_authority.granting()):
            raise Refusal(ASKS_CONFIG, code=_code(ASKS_CONFIG))   # As auto_reason says, again at its run (S5).
        if arguments["action"] == "CREATE" and store.created_count(chat, folder_id) >= MAX_CREATED:
            raise Refusal(TOO_MANY, code=_code(TOO_MANY))
        if len(arguments["content"].encode("utf-8")) > MAX_BYTES:
            raise Refusal("Write at most %d bytes" % MAX_BYTES, code="LIMIT_EXCEEDED")
        signature = workspace.write_note(check_path(arguments["path"]), arguments["content"], before)
    except RefusalError as error:   # Its own refusal (Refusal), or a worker's chat's (worker_refusals): coded.
        from .refusal_codes import refusal_code
        return {"error": str(error)[:500], "error_code": refusal_code(error), "written": False, "scene_changed": False}
    except PermissionError:   # C refused the place: said as such, never with its path, which C's message holds.
        return {"error": NOT_ALLOWED, "error_code": _code(NOT_ALLOWED), "written": False, "scene_changed": False}
    except OSError as error:   # Never the error's file name: it is a path on the person's disk.
        text = "The file couldn't be written (%s); nothing was changed"   # i18n: model-facing
        return {"error": text % (error.strerror or "error"), "error_code": "INTERNAL_ERROR", "written": False,
                "scene_changed": False}
    except ValueError as error:
        text = said(error)
        return {"error": text[:500], "error_code": _code(text), "written": False, "scene_changed": False}
    if signature is not None:
        try:   # Written either way: a failure here only means the file isn't remembered as Angelo's (it asks).
            store.note_written(chat, folder_id, signature, created=before == MISSING,
                               replaced=None if before == MISSING else before)
        except (OSError, ValueError, PermissionError):
            pass
    return {"success": True, "written": True, "folder": label, "path": arguments["path"],
            "action": arguments["action"], "bytes": len(arguments["content"].encode("utf-8")), "scene_changed": False}


# What the person sees ---------------------------------------------------------------------------------------------

TITLES = (n_("Wrote “{name}”"), n_("Writing “{name}”"), n_("Write “{name}”"))
CHIP = n_("Working folder")   # Its card's chip (approval_families.approval_kind): a note changes no scene.


def file_name(arguments):
    """The file's own name (data, never translated), never a path."""
    path = arguments.get("path") if isinstance(arguments, dict) else None
    return (path.rsplit("/", 1)[-1] if isinstance(path, str) and path else "?")[:160]


def approval_message(args):
    size = len(args["content"].encode("utf-8"))
    if args["action"] == "CREATE":
        body = plural_(size, "Create this new text file ({count:,} byte) in your working folder.",
                       "Create this new text file ({count:,} bytes) in your working folder.")
        warning = ""
    else:
        body = plural_(size, "Replace the whole text of this file in your working folder ({count:,} byte).",
                       "Replace the whole text of this file in your working folder ({count:,} bytes).")
        warning = iface_("Its current text is replaced. Undo and snapshots don't bring it back.")
    return {"title": iface_("Write “{name}”", name=file_name(args)), "body": body, "warning": warning}


def message(name, args, usage_label=""):
    """Its card's text (``approvals.approval_message`` through ``approval_families``)."""
    return approval_message(args)


def approval_details(args):
    return args["path"] + "\n\n" + args["content"]
