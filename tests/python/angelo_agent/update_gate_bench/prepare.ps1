# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
# prepare.ps1 (L5 2.0.1, a bench session command's preparation; runs as SYSTEM before the bench session): the two apps the
# update gate needs, as their installers install them: the published 2.0.0 MSI (downloaded, sha256 pinned) and the
# signed 2.0.1 MSI (in this input folder, sha256 pinned), each unpacked with msiexec /a (nothing installed). Logs each
# Angelo.exe's SHA-256 (the kit binds the receipt to them) and writes <In>\apps.json. Nothing outside <In>/<Out> changes.
param([string]$In, [string]$Out)
$ErrorActionPreference = "Continue"; $ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$log = Join-Path $Out "prepare.log"
function Say([string]$Text) { $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Text; Write-Output $line; Add-Content $log $line }
function Sha([string]$Path) { if (Test-Path -LiteralPath $Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower() } else { "missing" } }
$newSha = "a40aa274fd8598eab8c313ad111b65f5a6a3cc079ceeb2752484b1efc5a3972a"
$oldUrl = "https://downloads.angelo.studio/2.0.0/Angelo-2.0.0-windows-x64.msi"
$oldSha = "4947aed6d6c61a6d8da2ef6a57fa2315e0fc58783c7265546ffd7d2e51280883"
$msis = Join-Path $In "msi"; New-Item -ItemType Directory -Force $msis | Out-Null
$newMsi = Join-Path $msis "Angelo-2.0.1-windows-x64.msi"
$have = Sha $newMsi; Say "new MSI $newMsi sha256 $have"
if ($have -ne $newSha) { Say "FAIL prepare: the new MSI is $have, not the signed $newSha"; exit 1 }
$oldMsi = Join-Path $msis "Angelo-2.0.0-windows-x64.msi"; $have = "missing"
for ($try = 1; $try -le 5 -and $have -ne $oldSha; $try++) {
    if (Test-Path $oldMsi) { Remove-Item -Force $oldMsi }
    try { Invoke-WebRequest -UseBasicParsing -Uri $oldUrl -OutFile $oldMsi } catch { Say "old MSI download try ${try}: $_" }
    $have = Sha $oldMsi; if ($have -ne $oldSha) { Start-Sleep -Seconds 20 } }
Say "old MSI $oldUrl sha256 $have"
if ($have -ne $oldSha) { Say "FAIL prepare: $oldUrl is $have, not the published $oldSha"; exit 1 }
$sig = Get-AuthenticodeSignature $newMsi
Say ("new MSI signature: {0}; {1}" -f $sig.Status, $(if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { "none" }))
$apps = @{}
foreach ($pair in @(@("old", $oldMsi), @("new", $newMsi))) {
    $into = Join-Path $In ("apps\" + $pair[0])
    $p = Start-Process msiexec.exe -ArgumentList "/a `"$($pair[1])`" /qn TARGETDIR=`"$into`"" -Wait -PassThru
    $exe = Get-ChildItem $into -Recurse -Filter Angelo.exe -File -ErrorAction SilentlyContinue | Select-Object -First 1
    Say "$($pair[0]) msiexec /a exit $($p.ExitCode); exe $(if ($exe) { $exe.FullName } else { 'none' })"
    if (-not $exe) { Say "FAIL prepare: no Angelo.exe in the $($pair[0]) MSI"; exit 1 }
    Say ("EXECUTABLE {0} msi {1} Angelo.exe sha256 {2}" -f $pair[0], (Sha $pair[1]), (Sha $exe.FullName))
    $apps[$pair[0]] = $exe.FullName
}
[IO.File]::WriteAllText((Join-Path $In "apps.json"), ($apps | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
Say "DONE prepare"
exit 0
