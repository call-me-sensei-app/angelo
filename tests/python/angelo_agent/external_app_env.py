# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The private environment the external-MCP drivers launch Angelo with (no Blender; standard library only).

The same folders as ``test_blender_external_cli.isolated_environment`` (which cannot be imported: that driver
exits on import outside Blender): HOME, TMPDIR, user resources and config, state, accounts and a runtime
directory short enough for a Unix socket path.
"""

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(1, str(Path(__file__).resolve().parent))
from angelo_agent import product_env  # noqa: E402  (standard library only)
import native_suite_paths  # noqa: E402

SUN_PATH_LIMIT = 103
SOCKET_NAME = "angelo-cli-" + "0" * 16 + ".sock"


def runtime_directory(root):
    run = root / "run"
    if len(os.fsencode(run / SOCKET_NAME)) <= SUN_PATH_LIMIT:
        return run
    return native_suite_paths.short_temp_root() / ("angelo-cli-run-" + hashlib.sha256(os.fsencode(root)).hexdigest()[:12])


def isolated_environment(root, run=None):
    folders = {name: root / name for name in ("home", "tmp", "config", "resources", "state", "accounts")}
    folders["run"] = Path(run) if run else runtime_directory(root)
    for folder in folders.values():
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("BLENDER_", product_env.PREFIX, *product_env.LEGACY_PREFIXES, "PYTHON"))}
    env.update(HOME=str(folders["home"]), CFFIXED_USER_HOME=str(folders["home"]), TMPDIR=str(folders["tmp"]) + "/",
               BLENDER_USER_RESOURCES=str(folders["resources"]), BLENDER_USER_CONFIG=str(folders["config"]),
               ANGELO_STATE_DIR=str(folders["state"]), ANGELO_CONNECTIONS_DIR=str(folders["accounts"]),
               ANGELO_CLI_RUNTIME_DIR=str(folders["run"]), ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_QA_DIR=str(root))
    return env
