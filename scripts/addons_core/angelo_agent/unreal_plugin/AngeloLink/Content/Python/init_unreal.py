# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: MIT
"""Run by the Python Editor Script Plugin at editor start-up: every mounted content root's Content/Python folder is
on sys.path and each init_unreal.py there runs (PythonScriptPlugin.cpp, RunStartupScripts). Starts Angelo Link in
an interactive editor only, never in a commandlet (-run=) or an unattended run."""

import unreal


def _interactive():
    try:
        command_line = unreal.SystemLibrary.get_command_line().lower()
        return unreal.is_editor() and not unreal.SystemLibrary.is_unattended() and "-run=" not in command_line
    except Exception:
        return False


if _interactive():
    try:
        from angelo_link import editor as _angelo_link_editor
        _angelo_link_editor.start()
    except Exception as error:   # A broken link never stops the editor from starting.
        unreal.log_error("Angelo Link didn't start: %s: %s" % (type(error).__name__, error))
