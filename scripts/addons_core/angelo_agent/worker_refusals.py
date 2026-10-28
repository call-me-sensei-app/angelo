# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a worker Blender's chat runs, and what it refuses with nothing run: one set for both paths. No bpy.

A worker chat's calls run on one of two paths. On the lane path (``ANGELO_WORKER_CHAT_HOST=0``) its ``Controller``
is in Angelo with a ``remote_executor.RemoteExecutor``, and ``worker_host`` checks again in the worker. On the
hosted path (design A′) its ``Controller`` runs in the worker itself (``worker_chat_host``), with the window's own
``BlenderExecutor`` made ``worker_chat_executor.confined``. Both refuse from this module, so they cannot drift apart
(pinned by ``test_native_worker_refusals``, which compares the paths):

* **Not in a worker** (``NOT_IN_WORKER``, ``UNSUPPORTED``): every tool outside its worker's part reads, views, typed
  edits and scripts (``worker_tools``) and the chat's own tools (``local_tools``: task memory, metrics, its scripts'
  code, its progress). Sculpting, jobs, Studio, flows, team tools, file summaries and previews' acceptance among them.
* **A PREVIEW** (``PREVIEW_IN_WORKER``, ``UNSUPPORTED``): a preview is accepted in Angelo's window, which a worker has
  not.
* **A script that would replace the open file** (``worker_script.LOADS``, ``UNSUPPORTED``): its code names a load,
  revert, recover or startup-file read (``worker_script.file_load``), refused before its card.
* **The file tools** (``seal``): the chat's file workspace refuses them (``worker_routing.WorkerChatFiles``).

What runs is then kept to the part on both paths: after an approved script, a load the name check could not see
is contained (``worker_script.contain``: the part loaded again as last saved, never another file saved as it), and a
view draws offscreen only after the worker's GPU check (``draws``, ``WorkerHost.gpu``).
"""

from .refusal_codes import RefusalError
from .worker_script import LOADS, SCRIPT, file_load

PREVIEW_IN_WORKER = ("A worker Blender applies typed edits; a PREVIEW is accepted in Angelo's window, which a worker has "
                     "not. A worker's edits run with mode APPLY. Nothing ran.")
NOT_IN_WORKER = ("{name} does not run in a worker Blender, which runs its part's scene reads, views, typed edits and "
                 "scripts. Nothing ran.")


def worker_tools():
    """(reads, edits) a worker runs: what runs headless (``external_clients.HEADLESS_TOOLS``) less what needs more
    than the worker's document, and the chat's scripts, which a person approves (``worker_script``)."""
    from .action_recipes import RECIPE_NAMES
    from .external_clients import HEADLESS_TOOLS
    from .scene_spec import SCENE_NAMES
    from .validation_spec import VALIDATION_NAMES
    from .view_spec import TOOL_NAME as VIEW
    reads = (SCENE_NAMES | VALIDATION_NAMES | (RECIPE_NAMES - {"angelo_apply_recipe"})
             | {"angelo_inspect_scene", "angelo_scene_settings", "angelo_read_data", "angelo_blender_api",
                "angelo_agent_capabilities", VIEW})
    edits = frozenset({"angelo_create_cube", "angelo_set_transform", "angelo_apply_batch", "angelo_scene_program",
                       "angelo_apply_recipe"})
    return frozenset(reads & HEADLESS_TOOLS), frozenset(edits & HEADLESS_TOOLS) | {SCRIPT}


def local_tools():
    """The chat's own tools: they read or write the chat itself, never a scene, so they run wherever the chat is."""
    from .memory_spec import MEMORY_NAMES
    from .worker_spec import PART_NAMES   # A worker's progress goes to its tile in Angelo.
    return frozenset(MEMORY_NAMES | {"angelo_agent_metrics", "angelo_script"} | PART_NAMES)


def nothing_ran(error):
    """A refusal before anything was sent or ran: never reported as a possible partial change (break-it L4)."""
    error.partial_changes_possible = False
    return error


def refusal(name, arguments=None):
    """The refusal of a worker chat's call to ``name`` with ``arguments`` (a ``RefusalError``, nothing ran), or None
    when it runs: as the chat's own (``local_tools``), or in the worker (``in_worker``)."""
    return None if name in local_tools() else in_worker(name, arguments)


def in_worker(name, arguments=None):
    """``refusal`` of a call that must run on the worker's document: the chat's own tools are refused too (the lane
    path's worker gets only the calls Angelo sends it, never one of the chat's own)."""
    reads, edits = worker_tools()
    if name not in reads and name not in edits:
        return nothing_ran(RefusalError(NOT_IN_WORKER.format(name=name), code="UNSUPPORTED"))
    if isinstance(arguments, dict) and arguments.get("mode") == "PREVIEW":
        return nothing_ran(RefusalError(PREVIEW_IN_WORKER, code="UNSUPPORTED"))
    load = file_load(arguments) if name == SCRIPT else None
    if load:
        return nothing_ran(RefusalError(LOADS.format(name=load), code="UNSUPPORTED"))
    return None


def admit(name, arguments=None, check=refusal):
    """Raise ``check``'s refusal (``refusal``, or ``in_worker``) when there is one (module docstring)."""
    refused = check(name, arguments)
    if refused is not None:
        raise refused


def draws(name):
    """Whether ``name`` draws offscreen when it runs in a worker: it needs the worker's GPU check first."""
    from .view_spec import VIEW_NAMES
    return name in VIEW_NAMES


def seal(controller):
    """The chat's file workspace refuses the file tools from now on (``worker_routing.seal_files``)."""
    from .worker_routing import seal_files
    seal_files(controller)
