# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Windows policy's expandable strings, expanded from the machine alone (``managed_policy.windows_sources``).

Group Policy writes a path or URL as an expandable string (REG_EXPAND_SZ, e.g. ``%ProgramData%\\Corp\\roots.pem``).
Windows' ``ExpandEnvironmentStrings`` reads the process environment, which whoever starts Angelo sets: a person, or
a coding agent's shell, launching it with ``ProgramData=<their folder>`` made the administrator's roots path theirs
(break-it of the policy-file fix, 2026-11-20, S3). Here a ``%NAME%`` expands only from values in
HKEY_LOCAL_MACHINE, which only an administrator writes: the machine environment block and the system folders
Windows records there. A name the machine doesn't record stays as written, as Windows leaves it (a percent-encoded
URL keeps working); a path naming one is not an absolute file, so the policy applies it restrictively and reports
it. Nothing is read from elsewhere. Standard library only, no ``bpy``."""

import re

ENVIRONMENT = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
# Where Windows records its system folders: (key, {variable: value name}).
FOLDERS = (
    (r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", {"SystemRoot": "SystemRoot"}),
    (r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList",
     {"ProgramData": "ProgramData", "ALLUSERSPROFILE": "ProgramData", "PUBLIC": "Public"}),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion",
     {"ProgramFiles": "ProgramFilesDir", "ProgramFiles(x86)": "ProgramFilesDir (x86)", "ProgramW6432": "ProgramW6432Dir",
      "CommonProgramFiles": "CommonFilesDir"}),
)
_NAME = re.compile(r"%([^%]+)%")
DEPTH = 4   # Nested names (ProgramData is %SystemDrive%\ProgramData): a bounded number of passes.


class Variables:
    """The machine's variables, read from ``registry`` (``winreg``, a fake in tests) on the first expansion."""

    def __init__(self, registry):
        self._registry, self._names = registry, None

    def _read(self, path):
        registry = self._registry
        try:
            key = registry.OpenKey(registry.HKEY_LOCAL_MACHINE, path, 0,
                                   registry.KEY_READ | getattr(registry, "KEY_WOW64_64KEY", 0))
        except OSError:
            return {}
        values = {}
        try:
            for index in range(512):
                try:
                    name, value, _kind = registry.EnumValue(key, index)
                except OSError:
                    break
                if isinstance(name, str) and isinstance(value, str):
                    values[name] = value
        finally:
            registry.CloseKey(key)
        return values

    def names(self):
        if self._names is None:
            names = {name.upper(): value for name, value in self._read(ENVIRONMENT).items()}
            for path, wanted in FOLDERS:
                values = self._read(path)
                names.update({variable.upper(): values[name] for variable, name in wanted.items() if name in values})
            if "SYSTEMROOT" in names:
                names.setdefault("WINDIR", names["SYSTEMROOT"])
                names.setdefault("SYSTEMDRIVE", names["SYSTEMROOT"][:2])
            self._names = names
        return self._names

    def expand(self, value):
        """``value`` with every ``%NAME%`` the machine records expanded (nested up to ``DEPTH``); a name it doesn't
        record stays as written, as Windows leaves it, so it is never read from anywhere else."""
        if not _NAME.search(value):
            return value
        names = self.names()
        for _ in range(DEPTH):
            expanded = _NAME.sub(lambda m: names.get(m.group(1).upper(), m.group(0)), value)
            if expanded == value:
                break
            value = expanded
        return value
