<#
.SYNOPSIS
  Windows checks of the Angelo team account client, from the installed app, against the fake console.

.DESCRIPTION
  Installs the MSI the identity validation built, runs scripts\angelo\windows_team_checks.py with the installed
  app's own Python against the installed add-on, writes team-windows.json (and screenshots with -Browser), and
  uninstalls again. No real console, provider or account is involved. Run it as an administrator (the policy
  checks write HKLM). With -Browser the loopback sign-in address is opened in the default browser, so it needs a
  signed-in desktop session.
#>
param(
    [string]$SourceDir = "C:\angelo\src",
    [string]$ResultsDir = "",
    [string]$MsiPath = "",
    [switch]$Browser,
    [switch]$KeepInstalled
)
$ErrorActionPreference = "Stop"
if (-not $ResultsDir) { $ResultsDir = Join-Path $env:TEMP "angelo-teams-results" }
New-Item -ItemType Directory -Force $ResultsDir | Out-Null
$results = New-Object System.Collections.ArrayList
function Add-Result([string]$Name, [string]$Status, [string]$Detail = "") {
    [void]$results.Add([pscustomobject]@{ Check = $Name; Status = $Status; Detail = $Detail })
    Write-Host ("{0,-5} {1}  {2}" -f $Status, $Name, $Detail)
}
function Test-Check([string]$Name, [scriptblock]$Body) {
    try { $detail = & $Body; Add-Result $Name "PASS" ([string]($detail | Out-String).Trim()) }
    catch { Add-Result $Name "FAIL" $_.Exception.Message }
}

$identity = Get-Content (Join-Path $SourceDir "scripts\modules\angelo_identity.json") -Raw | ConvertFrom-Json
$product = $identity.product.name
$installDir = Join-Path $env:ProgramFiles $identity.windows.install_folder
$series = "5.2"
$python = Join-Path $installDir "$series\python\bin\python.exe"
$addons = Join-Path $installDir "$series\scripts\addons_core"

if (-not $MsiPath) {
    $MsiPath = Get-ChildItem "C:\angelo" -Recurse -Filter "$product-*-windows-x64.msi" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
}

Test-Check "teams: install the MSI" {
    if (-not $MsiPath -or -not (Test-Path $MsiPath)) { throw "no $product MSI found; run the identity validation first" }
    $log = Join-Path $ResultsDir "teams-msi-install.log"
    # ALLUSERS=1: for every user, in Program Files (the MSI's own default is a per-user install).
    $run = Start-Process msiexec.exe -ArgumentList "/i `"$MsiPath`" ALLUSERS=1 /qn /norestart /l*v `"$log`"" -Wait -PassThru
    if ($run.ExitCode -notin 0, 3010) { throw "msiexec exited with $($run.ExitCode)" }
    if (-not (Test-Path $python)) { throw "the installed app has no Python at $python" }
    "installed $MsiPath"
}
Test-Check "teams: the add-on ships in the installed app" {
    if (-not (Test-Path (Join-Path $addons "angelo_agent\team_account.py"))) { throw "angelo_agent\team_account.py is not in $addons" }
    foreach ($name in "credential_manager_windows.py", "managed_policy.py", "team_lock.py", "team_signin.py", "team_token_store.py") {
        if (-not (Test-Path (Join-Path $addons "angelo_agent\$name"))) { throw "$name is missing from the installed add-on" }
    }
    $addons
}
Test-Check "teams: run the checks with the installed Python" {
    $report = Join-Path $ResultsDir "team-windows.json"
    $screens = Join-Path $ResultsDir "screens"
    $arguments = @("-s", (Join-Path $SourceDir "scripts\angelo\windows_team_checks.py"), "--report", $report,
                   "--addons", $addons, "--fakes", (Join-Path $SourceDir "tests\python\angelo_agent"), "--screens", $screens)
    if ($Browser) { $arguments += "--browser" }
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $output = & $python @arguments 2>&1 | Out-String } finally { $ErrorActionPreference = $old }
    Set-Content (Join-Path $ResultsDir "team-windows.log") $output
    Write-Host $output
    if (-not (Test-Path $report)) { throw "the checks wrote no report: $output" }
    $data = Get-Content $report -Raw | ConvertFrom-Json
    $failed = @($data.steps.PSObject.Properties | Where-Object { $_.Value.ok -eq $false } | ForEach-Object { $_.Name })
    if ($failed) { throw "failed steps: $($failed -join ', ')" }
    "{0} steps, none failed" -f @($data.steps.PSObject.Properties).Count
}
if (-not $KeepInstalled -and (Test-Path $MsiPath)) {
    Test-Check "teams: uninstall the MSI" {
        $run = Start-Process msiexec.exe -ArgumentList "/x `"$MsiPath`" /qn /norestart /l*v `"$(Join-Path $ResultsDir 'teams-msi-uninstall.log')`"" -Wait -PassThru
        if ($run.ExitCode -notin 0, 3010) { throw "msiexec exited with $($run.ExitCode)" }
        "uninstalled"
    }
}

$json = $results | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $ResultsDir "teams-results.json"), $json, (New-Object System.Text.UTF8Encoding($true)))
$failed = @($results | Where-Object Status -eq "FAIL").Count
Write-Host ""
Write-Host ("{0} team checks: {1} failed" -f $results.Count, $failed)
exit ([int]($failed -gt 0))
