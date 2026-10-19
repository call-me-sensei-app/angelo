# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The folders a benchmark's runtimes work in: empty, and nowhere that hands a client project context.

A coding client reads project context from its working folder and every
parent: Claude Code loads each ``CLAUDE.md`` (and ``.claude/``) it finds and,
inside a git repository, the repository's status; Codex loads ``AGENTS.md``.
A run folder inside a checkout therefore put the checkout's context into a
side's first message: there the MCP side's Claude Code carried a parent
checkout's large ``CLAUDE.md`` and the git status (a first
message of 41.7 KB instead of 0.7 KB, measured against a local endpoint), and
Angelo's runtime the git status (5.6 KB instead of 2.5 KB; its
``--setting-sources ""`` keeps every ``CLAUDE.md`` out). Results recorded
before this module carry that inflation.

* ``visible``: the context files a client started in a folder would read.
* ``root``: where a run's runtime folders go: the run folder when nothing is
  visible from it, else a folder of the run's own under the temp folder
  (``ANGELO_BENCH_WORKDIR_ROOT`` overrides the temp folder).
* ``fresh``: an MCP trial's empty working folder, refused when anything is
  visible from it or when the client's own home holds a memory file (Claude
  Code reads ``$CLAUDE_CONFIG_DIR/CLAUDE.md``, Codex ``$CODEX_HOME/AGENTS.md``).
* ``trial_state``: an Angelo trial's state folder (its runtimes' workspace)
  when the trial folder itself sees context, checked the same way.

Standard library only.
"""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "AGENTS.override.md", ".claude", ".git")
MEMORY = {"claude": ("CLAUDE.md",), "codex": ("AGENTS.md", "AGENTS.override.md")}
ROOT_ENV = "ANGELO_BENCH_WORKDIR_ROOT"


class Unclean(RuntimeError):
    """A runtime would start where it reads project context: refused before anything is billed."""


def visible(folder):
    """The context files and folders a client started in ``folder`` would read: in it and in every parent."""
    path = Path(folder).resolve()
    return [str(parent / name) for parent in (path, *path.parents) for name in NAMES if (parent / name).exists()]


def root(output):
    """The folder a run's runtime folders go in: ``output`` when nothing is visible from it, else its own temp one."""
    if not visible(output):
        return Path(output)   # As the caller spelled it: a clean run keeps its runtime folders where they were.
    base = Path(os.environ.get(ROOT_ENV) or tempfile.gettempdir()).resolve()
    return base / ("angelo-bench-" + hashlib.sha256(str(Path(output).resolve()).encode()).hexdigest()[:12])


def require_clean(folder, what="a runtime's working folder"):
    found = visible(folder)
    if found:
        raise Unclean("%s (%s) would read project context: %s. Use a run folder outside any checkout, or set %s "
                      "to a clean folder" % (what, folder, ", ".join(found[:6]), ROOT_ENV))


def require_no_memory(home, runtime):
    """The client's own home must hold no memory file: it would join every message of that side."""
    found = [str(Path(home) / name) for name in MEMORY[runtime] if home and (Path(home) / name).exists()]
    if found:
        raise Unclean("the %s profile holds a memory file the client would load: %s" % (runtime, ", ".join(found)))


def problems(folder, home, runtime):
    """``[reason]`` that ``fresh`` would refuse with, for a preflight that must not create anything."""
    try:
        require_clean(folder)
        require_no_memory(home, runtime)
    except Unclean as error:
        return [str(error)]
    return []


def fresh(folder, home=None, runtime="claude"):
    """An empty working folder for one MCP trial's client, checked clean (``require_clean``, ``require_no_memory``)."""
    require_clean(folder)
    require_no_memory(home, runtime)
    shutil.rmtree(folder, ignore_errors=True)
    Path(folder).mkdir(parents=True)
    return Path(folder)


def state_path(folder):
    """Where the state of an Angelo trial in ``folder`` lives (``trial_state``): its own ``state`` when clean."""
    return root(folder) / "state" if visible(folder) else Path(folder) / "state"


def trial_state(folder, stable=None):
    """The state folder an Angelo trial's runtimes work in, when it is not the trial's own ``state``.

    ``stable`` (``--stable-state-dir``) is checked and made; otherwise, when the trial folder sees context, a
    fresh folder under ``root``; None when the trial's own ``state`` is clean. The caller moves it into the
    trial afterwards.
    """
    state = Path(stable) if stable is not None else (state_path(folder) if visible(folder) else None)
    if state is not None:
        require_clean(state, "the Angelo trial's state folder")
        if stable is None:
            shutil.rmtree(state, ignore_errors=True)
        state.mkdir(parents=True, exist_ok=True)
    return state
