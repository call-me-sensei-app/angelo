<#
.SYNOPSIS
  The checks that need a signed-in desktop: run in the interactive session by the unattended machine setup.

.DESCRIPTION
  Started from the Run key at the automatic sign-in of a local test user (windows_cloud_bootstrap.ps1). In this
  session it:
    1. installs the MSI and runs the team account checks with the loopback sign-in opened in the DEFAULT BROWSER,
       capturing the page (windows_teams_validate.ps1 -Browser);
    2. starts Angelo's real window (test_blender_app_identity.py, its Windows window phase): window checks, the splash
       and About captures, the Help menu, and the graphics renderer in use;
    3. checks the thumbnail handler's registration and takes a screenshot of the desktop;
    4. uninstalls the MSI;
    5. with the 'agent' stage, the agent's desktop checks (windows_agent_gui.ps1: CLI discovery and spawning from the
       app started by its launcher, and the GUI native suites).
  Each part runs only when its stage (angelo-stages metadata: teams, identity, thumbs, agent) was selected. With
  angelo-hold-minutes above zero it then runs remote commands for this session (windows_command_queue.ps1, cmd-gui).
  It writes everything under -ResultsDir and a DONE marker last.
#>
param(
    [string]$SourceDir = "C:\angelo\src",
    [string]$ResultsDir = "C:\angelo\results\gui"
)
$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force $ResultsDir | Out-Null
Start-Transcript (Join-Path $ResultsDir "gui-transcript.log") -Force | Out-Null
$results = New-Object System.Collections.ArrayList
function Add-Result([string]$Name, [string]$Status, [string]$Detail = "") {
    [void]$results.Add([pscustomobject]@{ Check = $Name; Status = $Status; Detail = $Detail })
    Write-Host ("{0,-5} {1}  {2}" -f $Status, $Name, $Detail)
}
function Test-Check([string]$Name, [scriptblock]$Body) {
    try { $detail = & $Body; Add-Result $Name "PASS" ([string]($detail | Out-String).Trim()) }
    catch { Add-Result $Name "FAIL" $_.Exception.Message }
}
function Save-Screenshot([string]$Path) {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose(); $bitmap.Dispose()
}

function Get-Meta([string]$Name, [string]$Default = "") {
    try { Invoke-RestMethod -Headers @{ "Metadata-Flavor" = "Google" } -Uri "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$Name" }
    catch { $Default }
}
$stages = (Get-Meta "angelo-stages" "identity+teams").Split("+")

try {
    $identity = Get-Content (Join-Path $SourceDir "scripts\modules\angelo_identity.json") -Raw | ConvertFrom-Json
    $installDir = Join-Path $env:ProgramFiles $identity.windows.install_folder
    $exe = Join-Path $installDir ($identity.windows.exe_stem + ".exe")
    $python = Join-Path $installDir "5.2\python\bin\python.exe"
    Add-Result "desktop session" "PASS" ("user {0}, session {1}, {2}" -f $env:USERNAME, (Get-Process -Id $PID).SessionId,
        ((Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) -join "; "))
    try { Save-Screenshot (Join-Path $ResultsDir "desktop-before.png") } catch { Add-Result "screenshot" "FAIL" $_.Exception.Message }
    # What kind of session this is, for the checks that depend on it (the credential vault in particular).
    try {
        $facts = @()
        $facts += "whoami: " + (& whoami.exe)
        $facts += (& whoami.exe /groups | Select-String -Pattern "Logon Session|Mandatory Label|BUILTIN\\Administrators" | ForEach-Object { $_.Line.Trim() })
        $facts += "VaultSvc: " + ((Get-Service VaultSvc -ErrorAction SilentlyContinue).Status)
        $facts += "EnableLUA: " + (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System").EnableLUA
        $facts += "elevated: " + ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        Set-Content (Join-Path $ResultsDir "session-facts.txt") $facts
    } catch { }

    if ($stages -contains "teams") { Test-Check "gui: team account checks with the default browser" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $SourceDir "scripts\angelo\windows_teams_validate.ps1") `
            -SourceDir $SourceDir -ResultsDir (Join-Path $ResultsDir "teams") -Browser -KeepInstalled *>&1 | Tee-Object (Join-Path $ResultsDir "teams-browser.log") | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "the team checks failed (exit $LASTEXITCODE); see teams\team-windows.json" }
        $shot = Join-Path $ResultsDir "teams\screens\loopback-sign-in-browser.png"
        if (-not (Test-Path $shot)) { throw "no screenshot of the browser page was taken" }
        "the loopback page was opened in the default browser ($shot)"
    } }
    if ($stages -contains "identity") { Test-Check "gui: Angelo's real window (splash, About, Help menu, renderer)" {
        if (-not (Test-Path $exe)) { throw "$exe is not installed" }
        $out = Join-Path $ResultsDir "identity-window"
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        try { $text = & $python -s (Join-Path $SourceDir "tests\python\angelo_agent\test_blender_app_identity.py") --binary $exe --out $out 2>&1 | Out-String }
        finally { $ErrorActionPreference = $old }
        Set-Content (Join-Path $ResultsDir "identity-window.log") $text
        if ($text -notmatch "ANGELO_APP_IDENTITY_OK") { throw "the window phase did not pass: $($text.Substring([Math]::Max(0, $text.Length - 600)))" }
        $renderer = (Get-Content (Join-Path $out "window.json") -Raw | ConvertFrom-Json)
        "window phase passed; graphics: $($renderer.gpu_renderer) ($($renderer.gpu_backend))"
    } }
    if ($stages -contains "thumbs") { Test-Check "gui: thumbnail handler (register as the user, render a .blend directly, unregister)" {
        $built = "C:\angelo\thumb\BlendThumb.dll"
        if (-not (Test-Path $built)) { throw "$built was not built (the 'thumbs' stage compiles it: the Lite build leaves the handler out)" }
        # The DLL needs the runtime libraries that sit beside Angelo.exe, so it runs from the install folder, as it
        # would in a build that ships it. The copy is removed again below.
        $dll = Join-Path $installDir "BlendThumb.dll"
        Copy-Item $built $dll -Force
        # BlendThumb.dll imports tbb12.dll. A build that ships the handler ships TBB too; the Lite install used here has
        # neither, so the library's own copy is placed beside the DLL for the check and removed again.
        $tbb = Join-Path $installDir "tbb12.dll"
        $addedTbb = $false
        if (-not (Test-Path $tbb)) {
            $source = Get-ChildItem (Join-Path $SourceDir "lib\windows_x64") -Recurse -Filter "tbb12.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($source) { Copy-Item $source.FullName $tbb -Force; $addedTbb = $true }
        }
        $clsid = "{" + $identity.windows.thumbnail_clsid + "}"
        $clsidKey = "Registry::HKEY_CLASSES_ROOT\CLSID\$clsid\InProcServer32"
        $handlerKey = "Registry::HKEY_CLASSES_ROOT\.blend\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}"
        $before = Test-Path $clsidKey
        $run = Start-Process regsvr32.exe -ArgumentList "/s `"$dll`"" -Wait -PassThru
        if ($run.ExitCode -ne 0) {
            # Say why it did not load: the Windows error of LoadLibrary and the DLLs it names, with which of them the install folder lacks.
            $why = @("regsvr32 exit code $($run.ExitCode) (3: the DLL did not load; 5: DllRegisterServer failed)")
            try {
                Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class NativeProbe {
  [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)] static extern IntPtr LoadLibraryW(string path);
  public static int Try(string path) { IntPtr h = LoadLibraryW(path); return h == IntPtr.Zero ? Marshal.GetLastWin32Error() : 0; }
}
"@
                $why += "LoadLibrary Windows error: " + [NativeProbe]::Try($dll) + " (126 a dependency is missing, 193 wrong architecture, 1114 DllMain failed)"
            } catch { $why += "LoadLibrary probe failed: $($_.Exception.Message)" }
            $dumpbin = Get-ChildItem "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC" -Recurse -Filter dumpbin.exe -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like "*Hostx64\x64*" } | Select-Object -First 1
            if ($dumpbin) {
                $deps = (& $dumpbin.FullName /dependents $dll 2>&1 | Out-String)
                $names = [regex]::Matches($deps, "^\s+(\S+\.dll)\s*$", "Multiline") | ForEach-Object { $_.Groups[1].Value }
                $missing = @($names | Where-Object { $_ -notlike "api-ms-win-*" -and -not (Test-Path (Join-Path $installDir $_)) -and -not (Test-Path (Join-Path $env:SystemRoot "System32\$_")) })
                $why += "depends on: " + ($names -join ", ")
                $why += "not in the install folder or System32: " + ($missing -join ", ")
            }
            Set-Content (Join-Path $ResultsDir "thumbnail-diagnostics.txt") $why
            Remove-Item $dll -Force -ErrorAction SilentlyContinue
            if ($addedTbb) { Remove-Item $tbb -Force -ErrorAction SilentlyContinue }
            throw ($why -join "; ")
        }
        if (-not (Test-Path $clsidKey)) { throw "after regsvr32, CLSID $clsid has no InProcServer32 key" }
        if ((Get-ItemProperty $clsidKey).'(default)' -ne $dll) { throw "the registered DLL is not $dll" }
        $handler = (Get-ItemProperty $handlerKey -ErrorAction SilentlyContinue).'(default)'
        if ($handler -ne $clsid) { throw ".blend's thumbnail handler is '$handler', not $clsid" }

        # A .blend with an embedded preview, saved by Angelo's own window.
        $blend = Join-Path $ResultsDir "thumbnail-sample.blend"
        $script = Join-Path $ResultsDir "save-sample.py"
        Set-Content $script ("import bpy`ndef save():`n    bpy.ops.wm.save_as_mainfile(filepath=r'{0}')`n    bpy.ops.wm.quit_blender()`nbpy.app.timers.register(save, first_interval=5.0)" -f $blend)
        $process = Start-Process $exe -ArgumentList "--factory-startup --no-window-focus --window-geometry 80 80 1000 700 --python `"$script`"" -PassThru
        if (-not $process.WaitForExit(150000)) { $process.Kill(); throw "Angelo's window did not save and quit within 150 s" }
        if (-not (Test-Path $blend) -or (Get-Item $blend).Length -lt 1000) { throw "the sample .blend was not written" }

        Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System; using System.Drawing; using System.Drawing.Imaging; using System.Runtime.InteropServices;
public static class DirectThumbnail {
  [ComImport, Guid("b824b49d-22ac-4161-ac8a-9916e8fa3f7f"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IInitializeWithStream { [PreserveSig] int Initialize([MarshalAs(UnmanagedType.Interface)] System.Runtime.InteropServices.ComTypes.IStream stream, int mode); }
  [ComImport, Guid("e357fccd-a995-4576-b01f-234630154e96"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IThumbnailProvider { [PreserveSig] int GetThumbnail(uint cx, out IntPtr phbmp, out int pdwAlpha); }
  [DllImport("shlwapi.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHCreateStreamOnFileEx(string path, int mode, int attrs, bool create, IntPtr templ, [MarshalAs(UnmanagedType.Interface)] out System.Runtime.InteropServices.ComTypes.IStream stream);
  [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr h);
  // The handler's own answer, with no shell host in between: create the class, give it the file, ask for the thumbnail.
  public static string Render(Guid clsid, string file, string png, uint size) {
    object handler = Activator.CreateInstance(Type.GetTypeFromCLSID(clsid));
    System.Runtime.InteropServices.ComTypes.IStream stream;
    SHCreateStreamOnFileEx(file, 0x20, 0x80, false, IntPtr.Zero, out stream);
    int hr = ((IInitializeWithStream)handler).Initialize(stream, 0);
    if (hr != 0) return "Initialize HRESULT 0x" + hr.ToString("X8");
    IntPtr hbitmap; int alpha;
    hr = ((IThumbnailProvider)handler).GetThumbnail(size, out hbitmap, out alpha);
    if (hr != 0 || hbitmap == IntPtr.Zero) return "GetThumbnail HRESULT 0x" + hr.ToString("X8") + (hr == 1 ? " (S_FALSE: the file holds no thumbnail)" : "");
    try {
      using (Bitmap bmp = Bitmap.FromHbitmap(hbitmap)) {
        bmp.Save(png, ImageFormat.Png);
        var colors = new System.Collections.Generic.HashSet<int>();
        for (int y = 0; y < bmp.Height; y += 4) for (int x = 0; x < bmp.Width; x += 4) colors.Add(bmp.GetPixel(x, y).ToArgb());
        return "OK " + bmp.Width + "x" + bmp.Height + " colors=" + colors.Count;
      }
    } finally { DeleteObject(hbitmap); }
  }
}
public static class ShellThumbnail {
  [StructLayout(LayoutKind.Sequential)] public struct SIZE { public int cx; public int cy; }
  [ComImport, Guid("bcc18b79-ba16-442f-80c4-8a59c30c463b"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IShellItemImageFactory { [PreserveSig] int GetImage(SIZE size, int flags, out IntPtr phbm); }
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHCreateItemFromParsingName(string path, IntPtr pbc, [In] ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IShellItemImageFactory factory);
  [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr h);
  public static string Render(string file, string png, int size, int flags) {
    Guid iid = new Guid("bcc18b79-ba16-442f-80c4-8a59c30c463b");
    IShellItemImageFactory factory; SHCreateItemFromParsingName(file, IntPtr.Zero, ref iid, out factory);
    IntPtr hbitmap; SIZE s; s.cx = size; s.cy = size;
    int hr = factory.GetImage(s, flags, out hbitmap);
    if (hr != 0) return "HRESULT 0x" + hr.ToString("X8");
    try {
      using (Bitmap bmp = Bitmap.FromHbitmap(hbitmap)) {
        bmp.Save(png, ImageFormat.Png);
        var colors = new System.Collections.Generic.HashSet<int>();
        for (int y = 0; y < bmp.Height; y += 4) for (int x = 0; x < bmp.Width; x += 4) colors.Add(bmp.GetPixel(x, y).ToArgb());
        return "OK " + bmp.Width + "x" + bmp.Height + " colors=" + colors.Count;
      }
    } finally { DeleteObject(hbitmap); }
  }
}
"@
        $png = Join-Path $ResultsDir "thumbnail-from-explorer.png"
        $colors = { param($text) if ($text -like "OK *") { [int]($text -replace '.*colors=', '') } else { 0 } }
        # 1. The handler itself: COM creates it, it reads the file's embedded preview and returns a bitmap.
        $direct = [DirectThumbnail]::Render([Guid]$clsid, $blend, (Join-Path $ResultsDir "thumbnail-from-handler.png"), 256)
        # 2. The shell's own route (the thumbnail cache and its host process) for the same file.
        $answer = [ShellThumbnail]::Render($blend, $png, 256, 8)   # 8 = a real thumbnail only, no icon fallback
        # 3. Control: the same shell route for a plain PNG, which the shell renders by itself.
        $probe = Join-Path $ResultsDir "shell-control.png"
        $bitmap = New-Object System.Drawing.Bitmap 128, 128
        for ($y = 0; $y -lt 128; $y++) { for ($x = 0; $x -lt 128; $x++) { $bitmap.SetPixel($x, $y, [System.Drawing.Color]::FromArgb(255, $x * 2, $y * 2, 128)) } }
        $bitmap.Save($probe, [System.Drawing.Imaging.ImageFormat]::Png); $bitmap.Dispose()
        $control = [ShellThumbnail]::Render($probe, (Join-Path $ResultsDir "shell-control-thumbnail.png"), 128, 8)
        Set-Content (Join-Path $ResultsDir "thumbnail-diagnostics.txt") @("handler directly: $direct", "shell route for the .blend: $answer", "shell route for a PNG: $control")
        $run = Start-Process regsvr32.exe -ArgumentList "/u /s `"$dll`"" -Wait -PassThru
        $gone = -not (Test-Path $clsidKey)
        Remove-Item $dll -Force -ErrorAction SilentlyContinue
        if ($addedTbb) { Remove-Item $tbb -Force -ErrorAction SilentlyContinue }
        if ((& $colors $direct) -le 8) { throw "the handler's own thumbnail for the sample .blend: $direct (the shell said: $answer)" }
        if (-not $gone) { throw "unregistering left CLSID $clsid in place" }
        $shellWorks = (& $colors $answer) -gt 8
        $shellControlWorks = (& $colors $control) -gt 8
        # Explorer's own route is reported as a separate line: the handler's correctness does not depend on this
        # machine's shell host, and a failure here is not yet understood (see doc/angelo/app_identity.rst).
        $script:shellStatus = if ($shellWorks) { "PASS" } else { "SKIP" }
        $script:shellNote = if ($shellWorks) { "the shell rendered the sample .blend with it ($answer)" }
            elseif ($shellControlWorks) { "NOT VERIFIED: the shell answered $answer for the .blend although it renders a PNG ($control); the handler itself rendered the file ($direct)" }
            else { "NOT VERIFIED: the shell's thumbnail host does not work here even for a PNG ($control)" }
        "handler absent before regsvr32: $(-not $before); registered for the user; the handler itself rendered the sample ($direct); unregistered cleanly"
    } }
    if ($script:shellNote) { Add-Result "gui: Explorer's own thumbnail route for a .blend" $script:shellStatus $script:shellNote }
    if (Test-Path $exe) { Test-Check "gui: uninstall" {
        $msi = Get-ChildItem "C:\angelo" -Recurse -Filter "$($identity.product.name)-*-windows-x64.msi" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $run = Start-Process msiexec.exe -ArgumentList "/x `"$($msi.FullName)`" /qn /norestart /l*v `"$(Join-Path $ResultsDir 'gui-msi-uninstall.log')`"" -Wait -PassThru
        if ($run.ExitCode -notin 0, 3010) { throw "msiexec exited with $($run.ExitCode)" }
        "uninstalled"
    } }
    if ($stages -contains "agent") {
        Test-Check "gui: agent desktop checks (CLI discovery and spawning, GUI native suites)" {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $SourceDir "scripts\angelo\windows_agent_gui.ps1") `
                -SourceDir $SourceDir -ResultsDir (Join-Path $ResultsDir "agent") *>&1 | Tee-Object (Join-Path $ResultsDir "agent-gui.log") | Out-Host
            if ($LASTEXITCODE -ne 0) { throw "agent desktop checks failed (exit $LASTEXITCODE); see agent\agent-gui-results.json" }
            "passed"
        }
    }
    try { Save-Screenshot (Join-Path $ResultsDir "desktop-after.png") } catch { }
    $holdMinutes = [int](Get-Meta "angelo-hold-minutes" "0")
    if ($holdMinutes -gt 0) {
        $gcloud = @("C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd",
                    "C:\Program Files\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd") | Where-Object { Test-Path $_ } | Select-Object -First 1
        $json = $results | ConvertTo-Json
        [System.IO.File]::WriteAllText((Join-Path $ResultsDir "gui-results.json"), $json, (New-Object System.Text.UTF8Encoding($true)))
        & $gcloud storage cp --quiet --recursive "$ResultsDir\*" "gs://$(Get-Meta 'angelo-bucket')/result/gui/" 2>&1 | Out-Null
        . (Join-Path $SourceDir "scripts\angelo\windows_command_queue.ps1")
        Wait-QueuedCommands $gcloud (Get-Meta "angelo-bucket") "cmd-gui" $ResultsDir (Join-Path $env:TEMP "angelo-cmd") $holdMinutes
    }
} catch {
    Add-Result "gui checks" "FAIL" $_.Exception.Message
} finally {
    $json = $results | ConvertTo-Json
    [System.IO.File]::WriteAllText((Join-Path $ResultsDir "gui-results.json"), $json, (New-Object System.Text.UTF8Encoding($true)))
    $failed = @($results | Where-Object Status -eq "FAIL").Count
    Write-Host ("{0} gui checks: {1} failed" -f $results.Count, $failed)
    Stop-Transcript | Out-Null
    Set-Content (Join-Path $ResultsDir "DONE") ("failed: {0}" -f $failed)
}
