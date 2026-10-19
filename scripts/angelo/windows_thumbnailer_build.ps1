<#
.SYNOPSIS
  Builds BlendThumb.dll (the Explorer thumbnail handler) in the existing validation build tree.

.DESCRIPTION
  The Lite build the validation machine uses leaves the thumbnail handler out (WITH_BLENDER_THUMBNAILER is off in
  blender_lite.cmake), so its source has not been compiled by the normal run. This switches the option on in the
  existing build folder, builds only the BlendThumb target (plus what it needs), copies the DLL to
  C:\angelo\thumb, and switches the option off again so the next make.bat finds the cache as it expects.
  Needs cmake on PATH (the machine setup installs it).
#>
param(
    [string]$SourceDir = "C:\angelo\src",
    [string]$ResultsDir = "",
    [string]$OutDir = "C:\angelo\thumb"
)
$ErrorActionPreference = "Stop"
if (-not $ResultsDir) { $ResultsDir = Join-Path $env:TEMP "angelo-thumb-results" }
New-Item -ItemType Directory -Force $ResultsDir, $OutDir | Out-Null
$results = New-Object System.Collections.ArrayList
function Add-Result([string]$Name, [string]$Status, [string]$Detail = "") {
    [void]$results.Add([pscustomobject]@{ Check = $Name; Status = $Status; Detail = $Detail })
    Write-Host ("{0,-5} {1}  {2}" -f $Status, $Name, $Detail)
}
function Test-Check([string]$Name, [scriptblock]$Body) {
    try { $detail = & $Body; Add-Result $Name "PASS" ([string]($detail | Out-String).Trim()) }
    catch { Add-Result $Name "FAIL" $_.Exception.Message }
}
function Invoke-Native([string]$Log, [scriptblock]$Command) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $Command *>&1 | Out-File $Log -Encoding utf8 } finally { $ErrorActionPreference = $old }
    return $LASTEXITCODE
}

$buildDir = Get-ChildItem (Split-Path $SourceDir -Parent) -Directory -Filter "build_windows_Lite_*" | Select-Object -First 1 -ExpandProperty FullName
Test-Check "thumbnailer: find the build folder" {
    if (-not $buildDir -or -not (Test-Path (Join-Path $buildDir "CMakeCache.txt"))) { throw "no Lite build folder next to $SourceDir" }
    $buildDir
}
if ($buildDir) {
    Test-Check "thumbnailer: configure with the handler on" {
        $code = Invoke-Native (Join-Path $ResultsDir "thumb-configure.log") { cmake $buildDir -DWITH_BLENDER_THUMBNAILER=ON }
        if ($code -ne 0) { throw "cmake exited with $code (see thumb-configure.log)" }
        "WITH_BLENDER_THUMBNAILER=ON"
    }
    Test-Check "thumbnailer: BlendThumb.dll compiles and links" {
        $code = Invoke-Native (Join-Path $ResultsDir "thumb-build.log") { cmake --build $buildDir --config Release --target BlendThumb -- /m /v:minimal }
        if ($code -ne 0) { throw "the build exited with $code (see thumb-build.log)" }
        $dll = Get-ChildItem $buildDir -Recurse -Filter "BlendThumb.dll" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (-not $dll) { throw "BlendThumb.dll was not produced" }
        Copy-Item $dll.FullName (Join-Path $OutDir "BlendThumb.dll") -Force
        "{0} ({1:N0} bytes)" -f $dll.FullName, $dll.Length
    }
    Test-Check "thumbnailer: switch the option off again" {
        $code = Invoke-Native (Join-Path $ResultsDir "thumb-restore.log") { cmake $buildDir -DWITH_BLENDER_THUMBNAILER=OFF }
        if ($code -ne 0) { throw "cmake exited with $code" }
        "WITH_BLENDER_THUMBNAILER=OFF"
    }
}
$json = $results | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $ResultsDir "thumbnailer-build-results.json"), $json, (New-Object System.Text.UTF8Encoding($true)))
$failed = @($results | Where-Object Status -eq "FAIL").Count
Write-Host ("{0} thumbnailer build checks: {1} failed" -f $results.Count, $failed)
exit ([int]($failed -gt 0))
