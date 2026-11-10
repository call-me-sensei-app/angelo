@echo off
rem SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
rem SPDX-License-Identifier: GPL-2.0-or-later
rem
rem Angelo's update installer for Windows (doc/angelo/updates_and_reports.rst, "Installing updates").
rem
rem Angelo copies this file and other_copies_windows.ps1 out of its install folder into the update's own folder
rem and starts it (cmd /d /c, hidden) just before it quits; it never runs from the folder Windows Installer
rem replaces. Every input is an environment variable, so any path (spaces, any language) arrives intact:
rem
rem   ANGELO_UPDATE_PID         Angelo's process: nothing changes until it has exited
rem   ANGELO_UPDATE_EXECUTABLE  its file name (Angelo.exe), so a reused process number is not waited for
rem   ANGELO_UPDATE_MSI         the verified installer (per user: no administrator prompt)
rem   ANGELO_UPDATE_SHA256      its digest from the signed feed, checked again right before msiexec (Angelo always
rem                             passes it; only a test leaves it empty)
rem   ANGELO_UPDATE_ROOT        the install folder, kept by the upgrade
rem   ANGELO_UPDATE_DIR         this update's folder: ready marker, result, Windows Installer's log
rem   ANGELO_UPDATE_VERSION     the new version, for the result
rem   ANGELO_UPDATE_RELAUNCH    the launcher to start afterwards (the new version, or the old one after a failure)
rem   ANGELO_UPDATE_OPEN        a file the relaunched app opens (may be empty)
rem
rem Windows Installer replaces the installation as one transaction: a failed or interrupted install is rolled
rem back, so the previous version is still there to relaunch. result.txt says what happened; Angelo reads it
rem at its next launch. Exit code 3010 is a success that left a file in use (the thumbnail handler Explorer
rem holds, say) to be replaced when Windows restarts: installed, with reboot=required, so Angelo says so once.

setlocal EnableExtensions DisableDelayedExpansion
set "dir=%ANGELO_UPDATE_DIR%"
set "relaunch=%ANGELO_UPDATE_RELAUNCH%"
set "open=%ANGELO_UPDATE_OPEN%"
set "reboot="
rem One installer at a time.
mkdir "%dir%\helper.lock" 2>nul
if errorlevel 1 exit /b 0
rem Readiness is not authorization. PowerShell validates bounded ASCII input and
rem EOF before any tasklist/msiexec/relaunch operation; no frame means no swap.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -Command "$n=$env:ANGELO_UPDATE_INVOCATION; if($n -cnotmatch '^[0-9a-f]{32}$'){exit 1}; [IO.File]::WriteAllText([IO.Path]::Combine($env:ANGELO_UPDATE_DIR,'helper.ready'),$n+[char]10,[Text.Encoding]::ASCII); $s=[Console]::OpenStandardInput(); $b=New-Object byte[] 97; $used=0; $until=[DateTime]::UtcNow.AddSeconds(600); while($true){$left=[int][Math]::Max(0,($until-[DateTime]::UtcNow).TotalMilliseconds); if($left -eq 0){exit 1}; $t=$s.ReadAsync($b,$used,97-$used); if(-not $t.Wait($left)){exit 1}; $k=$t.Result; if($k -eq 0){break}; $used+=$k; if($used -ge 97){exit 1}}; $s.Dispose(); $v=[Text.Encoding]::ASCII.GetString($b,0,$used); if($v -cne ('ANGELO_UPDATE_COMMIT:'+$n+[char]10)){exit 1}; exit 0"
if errorlevel 1 exit /b 1

set /a waited=0
:wait
tasklist /FI "PID eq %ANGELO_UPDATE_PID%" /FI "IMAGENAME eq %ANGELO_UPDATE_EXECUTABLE%" /NH 2>nul | find /I "%ANGELO_UPDATE_EXECUTABLE%" >nul
if errorlevel 1 goto install
set /a waited+=1
rem Ten minutes: Angelo did not quit; it is still open, so nothing relaunches.
if %waited% GEQ 600 (
    set "status=aborted" & set "code=wait" & set "relaunch="
    goto finish
)
ping -n 2 127.0.0.1 >nul
goto wait

:install
rem Another running copy of this install would have its files replaced under it. other_copies_windows.ps1 compares
rem every process's executable with the installed one as a file (volume and file index), so a copy started through
rem a link of another name, a short name or a junction counts and another install's Angelo.exe does not: exit 0 none,
rem 3 one runs. When PowerShell cannot answer (blocked, missing, failed), any Angelo.exe of this user counts.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%dir%\other_copies_windows.ps1" >nul 2>&1
set "copies=%errorlevel%"
if "%copies%"=="0" goto no_other_copy
if "%copies%"=="3" goto another_copy
tasklist /FI "IMAGENAME eq %ANGELO_UPDATE_EXECUTABLE%" /FI "USERNAME eq %USERDOMAIN%\%USERNAME%" /NH 2>nul | find /I "%ANGELO_UPDATE_EXECUTABLE%" >nul
if errorlevel 1 goto no_other_copy
:another_copy
set "status=aborted" & set "code=another_instance"
goto finish
:no_other_copy
rem The installer must still be the release's file: one line of certutil's output is exactly its digest
rem (whatever else a command-processor AutoRun prints around it).
if not defined ANGELO_UPDATE_SHA256 goto verified
certutil -hashfile "%ANGELO_UPDATE_MSI%" SHA256 2>nul | findstr /i /x /c:"%ANGELO_UPDATE_SHA256%" >nul
if not errorlevel 1 goto verified
set "status=failed" & set "code=digest"
goto finish
:verified
set /a tries=0
:retry
start "" /wait msiexec.exe /i "%ANGELO_UPDATE_MSI%" /qb!- /norestart ALLUSERS=2 MSIINSTALLPERUSER=1 MSIRESTARTMANAGERCONTROL=Disable REBOOT=ReallySuppress INSTALL_ROOT="%ANGELO_UPDATE_ROOT%" /l*v "%dir%\install.log"
set "code=%errorlevel%"
rem 1618: another installation is running; wait for it, up to ten minutes.
if "%code%"=="1618" if %tries% LSS 20 (
    set /a tries+=1
    ping -n 31 127.0.0.1 >nul
    goto retry
)
set "status=failed"
if "%code%"=="0" set "status=installed"
if "%code%"=="3010" (set "status=installed" & set "reboot=required")

:finish
> "%dir%\result.tmp" (
    echo status=%status%
    echo step=msiexec
    echo code=%code%
    echo version=%ANGELO_UPDATE_VERSION%
    if defined reboot echo reboot=%reboot%
)
move /y "%dir%\result.tmp" "%dir%\result.txt" >nul
rem The relaunched app must not inherit this installer's inputs.
for %%v in (PID EXECUTABLE MSI SHA256 ROOT DIR VERSION RELAUNCH OPEN INVOCATION) do set "ANGELO_UPDATE_%%v="
if not defined relaunch goto end
if not exist "%relaunch%" goto end
rem It starts in its own folder, as from its shortcut, and never in this update's folder: Windows keeps any
rem process's current folder from being deleted, and the new version deletes this one once it has read the result.
for %%r in ("%relaunch%") do set "home=%%~dpr."
if defined open (
    start "" /D "%home%" "%relaunch%" "%open%"
) else (
    start "" /D "%home%" "%relaunch%"
)
:end
endlocal
