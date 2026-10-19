# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows: whether this install can update itself, and the checks on a downloaded installer.

Contract: doc/angelo/updates_and_reports.rst, "Installing updates". Angelo
updates itself only where Windows Installer installed it **for this user**
(the MSI's default: ``%LOCALAPPDATA%\\Programs\\Angelo``, no administrator
prompt). Windows Installer is asked through ``msi.dll``: the products that
share Angelo's upgrade code, each one's install context (``AssignmentType``:
0 per user, 1 per machine) and folder (``InstallLocation``, which the MSI
records). A per-machine install (``ALLUSERS=1``, how studios deploy it) needs
an administrator for every change, so it keeps the notify-only behaviour, as
does anything Windows Installer did not install (a development build, a copied
folder).

The installer's SHA-256 is checked against the signed feed when it is
downloaded (``update_download``). When the running ``Angelo.exe`` carries a
valid Authenticode signature, the installer must carry a valid one from the
same signer too (read with PowerShell's ``Get-AuthenticodeSignature``), so a
signed install never takes an unsigned or differently signed package.

Standard library only; no bpy. Blocking: call it from a worker thread.
"""

import ctypes
import json
import os
import subprocess
from pathlib import Path

ERROR_SUCCESS, ERROR_MORE_DATA, ERROR_NO_MORE_ITEMS = 0, 234, 259
PER_USER, PER_MACHINE = "0", "1"
CREATE_NO_WINDOW = 0x08000000
SIGNATURE_SCRIPT = ("$s = Get-AuthenticodeSignature -LiteralPath $env:ANGELO_SIGNATURE_PATH; "
                    "[pscustomobject]@{status = [string]$s.Status; "
                    "subject = [string]$s.SignerCertificate.Subject; "
                    "issuer = [string]$s.SignerCertificate.Issuer} | ConvertTo-Json -Compress")


class InstallerError(Exception):
    """``code``: signature, missing."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def _product_info(msi, product, name):
    size = ctypes.c_uint(512)
    buffer = ctypes.create_unicode_buffer(size.value)
    status = msi.MsiGetProductInfoW(product, name, buffer, ctypes.byref(size))
    if status == ERROR_MORE_DATA:
        size.value += 1
        buffer = ctypes.create_unicode_buffer(size.value)
        status = msi.MsiGetProductInfoW(product, name, buffer, ctypes.byref(size))
    return buffer.value if status == ERROR_SUCCESS else ""


def related_products(upgrade_code, msi=None):
    """``[(product code, assignment type, install location)]`` of every installed product with ``upgrade_code``."""
    if msi is None:
        msi = ctypes.WinDLL("msi")
    code = upgrade_code if upgrade_code.startswith("{") else "{%s}" % upgrade_code
    products, index = [], 0
    while index < 64:
        buffer = ctypes.create_unicode_buffer(39)
        status = msi.MsiEnumRelatedProductsW(code, 0, index, buffer)
        if status != ERROR_SUCCESS:
            break
        product = buffer.value
        products.append((product, _product_info(msi, product, "AssignmentType"),
                         _product_info(msi, product, "InstallLocation")))
        index += 1
    return products


def _same_folder(a, b):
    def norm(path):
        return os.path.normcase(os.path.abspath(str(path))).rstrip("\\/")
    return bool(a) and bool(b) and norm(a) == norm(b)


def support(binary, upgrade_code, products=related_products):
    """``(reason, install folder)``: reason "" when this install can update itself, else ``machine``
    (installed for all users), ``location`` (not installed by Windows Installer, or not found) or
    ``permission``."""
    root = Path(binary).parent
    try:
        found = products(upgrade_code)
    except (OSError, AttributeError):
        return "location", root
    match = [p for p in found if _same_folder(p[2], root)]
    if not match:
        return "location", root
    if match[0][1] != PER_USER:
        return "machine", root
    if not os.access(root, os.W_OK):
        return "permission", root
    return "", root


def _powershell():
    system = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(system, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def authenticode(path, runner=subprocess.run):
    """``{"status", "subject", "issuer"}`` of ``path``'s Authenticode signature (status "Unknown" when unreadable)."""
    env = dict(os.environ, ANGELO_SIGNATURE_PATH=str(path))
    try:
        result = runner([_powershell(), "-NoProfile", "-NonInteractive", "-Command", SIGNATURE_SCRIPT],
                        capture_output=True, text=True, timeout=120, env=env, creationflags=CREATE_NO_WINDOW)
        document = json.loads(result.stdout or "{}") if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        document = {}
    if not isinstance(document, dict):
        document = {}
    return {"status": str(document.get("status") or "Unknown"), "subject": str(document.get("subject") or ""),
            "issuer": str(document.get("issuer") or "")}


def check_installer(msi, running_exe, signature=authenticode):
    """Raise ``InstallerError`` unless ``msi`` may replace the running install (see the module text)."""
    if not Path(msi).is_file():
        raise InstallerError("missing", "The installer is gone")
    running = signature(running_exe)
    if running["status"] != "Valid":
        return   # An unsigned build: the signed feed's SHA-256 is the whole check, as for the download itself.
    package = signature(msi)
    if package["status"] != "Valid" or (package["subject"], package["issuer"]) != (running["subject"], running["issuer"]):
        raise InstallerError("signature", "The installer is not signed by Angelo's publisher")


def other_instances(executable_path, own_pid):
    """Process ids of other copies of this install's ``Angelo.exe`` (Windows only; [] when it cannot tell)."""
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi_ids = (ctypes.c_ulong * 4096)()
        needed = ctypes.c_ulong()
        if not ctypes.WinDLL("psapi").EnumProcesses(psapi_ids, ctypes.sizeof(psapi_ids), ctypes.byref(needed)):
            return []
    except (OSError, AttributeError):
        return []
    found = []
    for pid in psapi_ids[:needed.value // ctypes.sizeof(ctypes.c_ulong)]:
        if pid in (0, own_pid):
            continue
        handle = kernel.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            size = ctypes.c_ulong(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) and _same_folder(
                    buffer.value, executable_path):
                found.append(pid)
        finally:
            kernel.CloseHandle(handle)
    return found
