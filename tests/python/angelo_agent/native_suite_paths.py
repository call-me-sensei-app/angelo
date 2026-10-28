# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Temporary-folder roots for native suites, their host drivers and the benchmarks.

Standard library only: imported by host Python (run_native_suites.py and the
host drivers) and inside Blender (suites and benchmark_fixtures.py).

``short_temp_root()`` is the one place that decides where a fixture needing a
short or fixed-depth path lives:

* macOS: ``/private/tmp``. A Unix-socket path (``sun_path``) holds at most 104
  bytes there, the terminating NUL included (``external_cli.SUN_PATH_LIMIT``),
  and the per-user ``$TMPDIR`` (``/private/var/folders/...``) already takes
  about half of that, so the external CLI fixture's socket needs the short root.
  Snapshot storage also anchors every ancestor folder on each write, so its cost
  grows with depth, and ``perf_ceilings.json`` was recorded at
  ``/private/tmp/<name>`` (doc/angelo/performance.rst).
* Elsewhere: the resolved ``tempfile.gettempdir()``. Linux allows 108 socket
  bytes and the external CLI does not listen on Windows, so no fixed root is
  needed there.

Suites without such a need default to ``tempfile.gettempdir()`` directly.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import sys
import tempfile

MACOS_SHORT_ROOT = Path("/private/tmp")
# Windows paths stop at MAX_PATH, 260 characters with the terminating NUL: Angelo.exe is not long-path aware, so a
# Python open or rename by a longer path fails (snapshot storage alone opens handle-relative). A person's
# ``%LOCALAPPDATA%`` is about 30 characters; the runner's profiles sit deeper (``run_native_suites.profile_folder``).
WINDOWS_PATH_LIMIT = 259
# The deepest path Angelo writes by path below ``%LOCALAPPDATA%``: a parallel worker's, in its private folder in
# ``\Angelo\scratch`` (``angelo_agent.worker_paths``, which refuses a worker past the limit; pinned by
# test_native_suite_profile_depth). Windows build4 2026-10-27, before those names were shortened: a worker's chat
# journal one character past the limit failed test_blender_parallel_lead@lanes from repeat run 10 on, 11/20.
ANGELO_DATA_DEPTH = 131


def short_temp_root(platform=None):
    """``/private/tmp`` on macOS; the resolved ``tempfile.gettempdir()`` elsewhere (or when it is missing)."""
    platform = sys.platform if platform is None else platform
    if platform == "darwin" and MACOS_SHORT_ROOT.is_dir():
        return MACOS_SHORT_ROOT
    return Path(tempfile.gettempdir()).resolve()


def windows_depth_refusal(local_appdata, platform=None):
    """Why a suite cannot run with ``local_appdata`` as its ``%LOCALAPPDATA%`` (Angelo's deepest private path would
    pass ``WINDOWS_PATH_LIMIT``), or None; always None off Windows."""
    platform = sys.platform if platform is None else platform
    deepest = len(str(local_appdata or "")) + ANGELO_DATA_DEPTH
    if platform != "win32" or not local_appdata or deepest <= WINDOWS_PATH_LIMIT:
        return None
    return ("profile folder too deep for Windows paths: %%LOCALAPPDATA%% is %d characters and Angelo writes %d more "
            "below it (%d > %d); use a shorter --output" % (len(str(local_appdata)), ANGELO_DATA_DEPTH, deepest,
                                                            WINDOWS_PATH_LIMIT))


def fresh_fixture_root(out, environ=None, platform=None):
    """A host driver's resolved ``--out``; raises ValueError unless it is new and disposable.

    The directory must not exist yet and must lie inside ``short_temp_root()``,
    so a driver never writes into an existing or personal folder. On macOS that
    root is a fixed system folder holding every runner directory. Elsewhere it
    comes from the environment, which run_native_suites.py points at each
    suite's own ``tmp`` folder, so the QA folder the runner created for the
    suite (``ANGELO_QA_DIR``) is accepted there as well.
    """
    environ = os.environ if environ is None else environ
    root = Path(out).resolve()
    base = short_temp_root(platform)
    allowed = [base]
    if base != MACOS_SHORT_ROOT and environ.get("ANGELO_QA_DIR"):
        allowed.append(Path(environ["ANGELO_QA_DIR"]).resolve())
    if root.exists() or not any(root.is_relative_to(folder) for folder in allowed):
        raise ValueError("Use a fresh disposable directory under " + " or ".join(map(str, allowed)))
    return root


@contextmanager
def temporary_directory(prefix):
    """A new folder under the temp folder, removed afterwards however deep it grew.

    Snapshot journals nest past Windows' 260-character MAX_PATH inside the
    runner's per-suite folders. Angelo's own storage reaches them (handle-relative
    opens), but ``os.unlink`` by path reports them missing, so a plain
    ``TemporaryDirectory`` cannot remove them; ``\\\\?\\`` paths have no such limit.
    """
    directory = tempfile.mkdtemp(prefix=prefix)
    try:
        yield directory
    finally:
        shutil.rmtree("\\\\?\\" + os.path.abspath(directory) if os.name == "nt" else directory)
