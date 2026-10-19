# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live eval's returning-user variant: one persistent profile across launches, a new chat per task.

``eval_live_agent.py --profile-dir P`` keeps what a returning artist's app
keeps between launches: Angelo's state (with the remembered Codex bases,
``codex_bases.json``), the Blender user folders and the isolated connection
copy, all under ``P``. Every trial is one launch on ``P``; the first launch on
a new ``P`` is the cold start. ``--new-chat-per-task`` opens a new chat for each
task after the first, as an artist starts a new conversation (those chats fork
the warm base when one is ready). ``--request-log`` records every Codex thread
and request of the trial (``eval_request_log``). Nothing here reads a
credential: the connection copy holds metadata only (``isolated_connection``).
"""

import os
from pathlib import Path

import bench_workdirs

USER_FOLDERS = ("RESOURCES", "SCRIPTS", "DATAFILES", "EXTENSIONS")


def add_arguments(parser):
    parser.add_argument("--profile-dir", default="",
                        help="returning user: keep Angelo's state, the Blender user folders and the connection copy "
                             "in this folder across trials and runs (the first launch on a new folder is a cold start)")
    parser.add_argument("--new-chat-per-task", action="store_true",
                        help="open a new chat for every task after the first, as an artist starts a new conversation")
    parser.add_argument("--opening-wait", type=float, default=0.0, metavar="SECONDS",
                        help="a labelled extra: ready the bound chat's opening before the first message and wait for "
                             "it up to SECONDS (0, the default: the first prompt includes the runtime launch)")
    parser.add_argument("--request-log", action="store_true",
                        help="record every Codex thread and request (IDs and tokens only) in the trial's qa/requests.jsonl")


def check(parser, args):
    if getattr(args, "profile_dir", "") and getattr(args, "stable_state_dir", ""):
        parser.error("--profile-dir keeps its own state: leave --stable-state-dir out")
    if getattr(args, "profile_dir", "") and bench_workdirs.visible(profile(args)):
        # The profile's state is the runtimes' workspace: inside a checkout it would read project context.
        parser.error("--profile-dir would give the runtimes project context: %s"
                     % ", ".join(bench_workdirs.visible(profile(args))[:4]))
    return args


def plan(args):
    return {"returning_user": {"profile": bool(getattr(args, "profile_dir", "")),
                               "new_chat_per_task": bool(getattr(args, "new_chat_per_task", False)),
                               "request_log": bool(getattr(args, "request_log", False))},
            "opening_wait_s": getattr(args, "opening_wait", 0.0)}


def profile(args):
    return Path(args.profile_dir).resolve() if getattr(args, "profile_dir", "") else None


def prepare(args):
    """The profile's folders (kept as they are when they exist)."""
    root = profile(args)
    if root is not None:
        for name in ("state", "config", "accounts") + tuple("blender-user/" + n.lower() for n in USER_FOLDERS):
            (root / name).mkdir(parents=True, exist_ok=True)


def environment(environment, args):
    """The variant's switches, and with a profile its state and user folders in place of the trial's."""
    environment.update(ANGELO_EVAL_NEW_CHAT_PER_TASK="1" if getattr(args, "new_chat_per_task", False) else "",
                       ANGELO_EVAL_REQUEST_LOG="1" if getattr(args, "request_log", False) else "",
                       ANGELO_EVAL_OPENING_WAIT="%g" % getattr(args, "opening_wait", 0.0))
    root = profile(args)
    if root is not None:
        prepare(args)
        environment.update(ANGELO_STATE_DIR=str(root / "state"), BLENDER_USER_CONFIG=str(root / "config"))
        for name in USER_FOLDERS:
            environment["BLENDER_USER_" + name] = str(root / "blender-user" / name.lower())
    return environment


def accounts(args, output):
    """Where the connection copy lives: the profile's, or the run's own."""
    root = profile(args)
    return root / "accounts" if root is not None else output / "accounts"


def kept_connection(args, connection_id):
    """The profile's earlier connection copy of ``connection_id`` (metadata only), or None for a new profile."""
    root = profile(args)
    store = root / "accounts" / "connections.json" if root is not None else None
    if store is None or not store.is_file():
        return None
    from angelo_agent.connections import ConnectionStore
    ids = list(ConnectionStore(root / "accounts").records)
    if ids != [connection_id]:
        raise ValueError("The profile's connection store must hold exactly %s" % connection_id)
    return {"id": connection_id, "kept_from_profile": str(root / "accounts")}


# In the app (eval_live_agent_session) -----------------------------------------------------------

def in_app(qa_dir):
    """At the in-app tester's start, before any provider: --request-log's per-request log."""
    if os.environ.get("ANGELO_EVAL_REQUEST_LOG") == "1":
        import eval_request_log
        eval_request_log.install(Path(qa_dir) / "requests.jsonl")


def next_chat(trial, ui, connection_id):
    """Before each task: with --new-chat-per-task every task after the first opens a new chat, as an artist starts
    a new conversation; the task's record names its chat."""
    if os.environ.get("ANGELO_EVAL_NEW_CHAT_PER_TASK") == "1" and trial.index:
        trial.controller = ui.create_session(connection_id=connection_id)
        trial.configure()
    trial.record["chat"] = trial.controller.state["id"]
