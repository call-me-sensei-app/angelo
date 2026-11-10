# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Is another copy of this install's Angelo still running? install_update_windows.cmd asks right before msiexec,
# because Windows Installer would replace its files under it (doc/angelo/updates_and_reports.rst, "The installers").
# Angelo copies this file beside the script; it reads ANGELO_UPDATE_ROOT and ANGELO_UPDATE_EXECUTABLE.
#
# Every process's executable is compared with the installed one as a file (volume serial number and file index,
# links and junctions followed), never by name or spelling: a copy started through a link of another name, a short
# (8.3) name, a junction, a substituted drive or another letter case is still this install, and an Angelo.exe of
# another install is not. A process whose executable path cannot be read counts when it has the executable's name
# and is this user's, or its owner cannot be read (fail closed).
#
# Exit 0: no other copy. Exit 3: another copy runs. Any other exit (PowerShell blocked, the check failed): the
# caller cannot tell, and counts every Angelo.exe of this user instead.

$ErrorActionPreference = 'Stop'
Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class AngeloFileIdentity {
    [StructLayout(LayoutKind.Sequential)]
    struct Information {   // BY_HANDLE_FILE_INFORMATION: each FILETIME as its two DWORDs.
        public uint Attributes;
        public uint CreationLow, CreationHigh, AccessLow, AccessHigh, WriteLow, WriteHigh;
        public uint VolumeSerial, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition,
                                             uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetFileInformationByHandle(SafeFileHandle file, out Information information);

    // "volume:index" of the file a path names, or null when it cannot be opened. FILE_READ_ATTRIBUTES with every
    // sharing mode opens a running executable; OPEN_EXISTING follows links and junctions to the file itself.
    public static string Of(string path) {
        using (SafeFileHandle file = CreateFileW(path, 0x80, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            Information information;
            if (file.IsInvalid || !GetFileInformationByHandle(file, out information)) {
                return null;
            }
            return information.VolumeSerial.ToString("x8") + ":" + information.IndexHigh.ToString("x8") +
                   information.IndexLow.ToString("x8");
        }
    }
}
'@

$executable = $env:ANGELO_UPDATE_EXECUTABLE
$wanted = [AngeloFileIdentity]::Of((Join-Path $env:ANGELO_UPDATE_ROOT $executable))
if ($null -eq $wanted) { exit 2 }   # The installed executable cannot be read: no answer.
foreach ($process in Get-CimInstance -ClassName Win32_Process) {
    $path = $process.ExecutablePath
    if ([string]::IsNullOrEmpty($path)) {
        if ($process.Name -ieq $executable) {
            $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwner
            if ($owner.ReturnValue -ne 0 -or
                ($owner.User -ieq $env:USERNAME -and $owner.Domain -ieq $env:USERDOMAIN)) { exit 3 }
        }
        continue
    }
    if ([AngeloFileIdentity]::Of($path) -eq $wanted) { exit 3 }
}
exit 0
