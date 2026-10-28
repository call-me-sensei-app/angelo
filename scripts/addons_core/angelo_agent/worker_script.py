# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A reviewed script in a parallel-edit worker Blender, and what keeps the worker's part its own. No bpy.

A worker chat's ``angelo_execute_python`` is approved in Angelo as any chat's script is: its card, with the exact-code
review or a standing consent of that chat (``trusted_scripts``). The approved call goes to the worker with its
invocation digest (``remote_executor``) and runs there through the worker's shadow controller and Angelo's apply
operator, with its one Undo step; the part is saved after it (``worker_host.edit``). Three guards:

* **Only the approved script runs.** The worker runs a script only when the invocation digest of the call its shadow
  admitted (the tool and its arguments) equals the one Angelo approved, and its binding is unchanged (as every edit's,
  ``STALE_REVISION``); any other runs nothing (``OWNERSHIP``). The shadow's approval is then its exact-code review of
  that call (``review_script``): the person, or the chat's consent, approved that exact text in Angelo.
* **The open file stays the part.** A script whose code names an operator that replaces the open file
  (``FILE_LOADS``: load, revert, recover, read the startup file) as an attribute, a name or a word of a string is
  refused before its card in Angelo and again in the worker; nothing ran. Appending or linking from a file replaces
  nothing and runs. The names are a check over the code, as ``trusted_scripts.consent_excluded`` is; ``after`` covers
  a computed name.
* **After the script** (``after``): a load the check could not see ends in ``DOCUMENT_REPLACED`` (``script_document``)
  and the worker loads its part again as last saved, so another file's data is never saved as the part. Its document
  is then a new one, so the chat ends, as a window's chat does after a load (fail-safe). A part it cannot load again
  ends the worker, which saves nothing. A script that saved the open file as another file keeps that file (its own
  effect, as a window script's is), and the worker saves the same data as its part again (``on_part``), so its open
  file is the part again (spike r2, r4, f8, 2026-10-25).
"""

import ast
import os
import re

SCRIPT = "angelo_execute_python"
FILE_LOADS = ("open_mainfile", "revert_mainfile", "read_homefile", "read_factory_settings", "recover_last_session",
              "recover_auto_save")
_WORDS = re.compile(r"\b(%s)\b" % "|".join(FILE_LOADS))
LOADS = ("A script in a worker Blender cannot load, revert or recover a file or read the startup file ({name}): the "
         "worker's open file is its part. Appending or linking data from a file works. Nothing ran.")
NOT_APPROVED = {"error_code": "OWNERSHIP", "scene_changed": False, "partial_changes_possible": False,
                "error": "This script is not the one approved in Angelo, so the worker ran nothing."}
RELOADED = "The script loaded another file ({name}); the worker loaded its part again as last saved."
PART_LOST = ("The script loaded another file ({name}) and the worker could not load its part again, so the worker "
             "ends without saving.")
SAVED_AGAIN = "The script saved the open file as {name}; the worker saved it as its part again, its open file."


def file_load(arguments):
    """The file-replacing operator a script's code names (an attribute, a name, or a word of a string it may run), or
    None (module docstring)."""
    code = arguments.get("code") if isinstance(arguments, dict) else None
    try:
        tree = ast.parse(code) if isinstance(code, str) else None
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None   # validate_call refuses it.
    for node in ast.walk(tree) if tree is not None else ():
        text = (node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name)
                else node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None)
        found = _WORDS.search(text) if text else None
        if found:
            return found.group(1)
    return None


def approved(call, request):
    """Whether the shadow's admitted ``call`` is exactly the script Angelo approved (``request``'s digest)."""
    digest = request.get("digest")
    return isinstance(digest, str) and bool(digest) and getattr(call, "invocation_digest", None) == digest


def serial(ui):
    """The worker's document instance (a load renews it, ``ui.document_load_pre``)."""
    return getattr(ui, "_document_serial", None)


def on_part(host):
    """Whether the worker's open file is its part (a script may have saved it as another file)."""
    current = host.bpy.data.filepath
    return bool(current) and os.path.realpath(current) == os.path.realpath(host.part)


def contain(host, result, before):
    """After a script in ``host`` (``worker_host.WorkerHost``), ``before`` its document's ``serial`` from before, on
    either path (the hosted one: ``worker_chat_executor``): ``(result, reloaded)``. Unless the document was replaced,
    the result as it is, noting when the script saved the open file as another file (the part is then saved again,
    ``on_part``). Else the part was loaded again as last saved, with nothing saved, and the result says so; one that
    cannot be loaded again ends the worker (``host.ended``) (module docstring)."""
    current = host.bpy.data.filepath or ""
    name = os.path.basename(current) or "an unsaved file"
    if not (isinstance(result, dict) and result.get("document_replaced")) and serial(host.ui) == before:
        if isinstance(result, dict) and not on_part(host):
            result["part_saved_again"] = SAVED_AGAIN.format(name=name)
        return result, False
    try:
        loaded = host.bpy.ops.wm.open_mainfile(filepath=host.part, load_ui=False, use_scripts=False)
    except RuntimeError:
        loaded = set()
    if "FINISHED" in loaded:
        note = RELOADED.format(name=name)
        host.turns.clear()   # Their change feeds were the replaced document's.
        for shadow in host.shadows.values():   # As a window's chats are told of a load.
            shadow.set_document(host.ui.document_key())
    else:
        note, host.ended = PART_LOST.format(name=name), True   # The run loop ends after this answer.
    return dict(result if isinstance(result, dict) else {}, part_reloaded=note), True


def after(host, result, before):
    """The lane path's ``contain``: None to save as after any edit (``on_part`` says whether it must), or the edit's
    answer once the part was loaded again, with nothing saved."""
    result, reloaded = contain(host, result, before)
    if not reloaded:
        return None
    return {"result": result, "captured": None, "saved": None, "layout": None, "revision": None}
