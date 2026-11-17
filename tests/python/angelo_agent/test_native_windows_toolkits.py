# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The full-feature Windows build's GPU toolkit installs: signatures first, never a display driver.

``scripts/angelo/windows_toolkits.ps1`` runs only on the throwaway Windows build machine
(``app_identity.rst``, "Running it on Google Cloud"), so these checks read its source: every installer branch
checks the vendor's Authenticode signature before anything runs, the CUDA and HIP installs leave the display driver
alone, and the signer pattern (run here with Python's ``re``, whose syntax this pattern shares with .NET's) accepts
the vendor's own subject and nothing that merely starts with its name.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import re
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/angelo"
TOOLKITS = (SCRIPTS / "windows_toolkits.ps1").read_text(encoding="utf-8")


def branch(start, end):
    return TOOLKITS[TOOLKITS.index(start):TOOLKITS.index(end, TOOLKITS.index(start))]


def signer_pattern(publisher):
    """The script's signer pattern for one vendor: its quoted pieces joined around the escaped name."""
    line = next(line for line in TOOLKITS.splitlines() if line.strip().startswith("$vendor = "))
    pattern = ""
    for piece in re.split(r"\s*\+\s*", line.split("=", 1)[1].strip()):
        if piece == "[regex]::Escape($Publisher)":
            pattern += re.escape(publisher)
        else:
            assert piece.startswith('"') and piece.endswith('"'), piece
            pattern += piece[1:-1].replace('""', '"')
    return re.compile(pattern, re.IGNORECASE)  # PowerShell's -notmatch is case-insensitive.


class SignatureTests(unittest.TestCase):
    def test_each_installer_branch_checks_its_vendor_before_running(self):
        loop = TOOLKITS[TOOLKITS.index("foreach ($file in Get-ChildItem $Download -File)"):]
        for marker, vendor, runner in (('"^cuda_', "$NVIDIA", "Install-Cuda"),
                                       ('"optix.*\\.exe$"', "$NVIDIA", "Invoke-Installer"),
                                       ('"hip.*\\.exe$"', "$AMD", "Install-Hip")):
            with self.subTest(installer=marker):
                body = loop[loop.index(marker):]
                body = body[:body.index("} elseif") if "} elseif" in body else body.index("} else")]
                check = body.index("Test-Publisher $file.FullName " + vendor)
                self.assertLess(check, body.index(runner), "the signature is checked before the installer runs")
                self.assertIn("$failed = $true; continue", body[check:body.index(runner)])
        self.assertIn('$NVIDIA, $AMD, $INTEL = "NVIDIA Corporation", "Advanced Micro Devices", "Intel Corporation"',
                      TOOLKITS)

    def test_ocloc_is_named_only_when_every_binary_carries_intels_signature(self):
        register = branch("function Register-Ocloc", "$failed, $restart = $false, $false")
        self.assertLess(register.index("Test-Publisher $_.FullName $INTEL"),
                        register.index('SetEnvironmentVariable("OCLOC_INSTALL_DIR"'))
        self.assertIn('$_.Extension -in ".exe", ".dll"', register)
        loop = TOOLKITS[TOOLKITS.index("foreach ($file in Get-ChildItem $Download -File)"):]
        archive = loop[loop.index('"ocloc.*\\.zip$"'):]
        archive = archive[:archive.index("} elseif")]
        self.assertIn("Register-Ocloc @($target)", archive)
        self.assertIn("Remove-Item $target -Recurse -Force", archive[archive.index("Register-Ocloc"):],
                      "a refused package is removed")
        installer = loop[loop.index('"ocloc.*\\.exe$"'):]
        installer = installer[:installer.index("} elseif")]
        # The installer's own signature first, then Intel's silent mode with the EULA accepted,
        # then the same check of the installed binaries.
        self.assertLess(installer.index("Test-Publisher $file.FullName $INTEL"),
                        installer.index('"-s -a --silent --eula accept"'))
        self.assertLess(installer.index('"-s -a --silent --eula accept"'), installer.index("Register-Ocloc $oneapi"))
        self.assertIn('Intel\\oneAPI', installer)
        # The GPU toolkit checks of build_release_windows.ps1 live in its dot-sourced helper (Get-GpuToolkits).
        build = (SCRIPTS / "build_release_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("$gpu = Get-GpuToolkits $allowed $LibDir", build)
        helper = (SCRIPTS / "windows_release_toolchain.ps1").read_text(encoding="utf-8")
        oneapi = helper[helper.index('if ("oneapi" -in $Allowed) { $toolkits.oneapi'):helper.index("$switches = ")]
        self.assertIn('-DOCLOC_INSTALL_DIR=', oneapi)
        self.assertIn("Intel's ocloc not found", oneapi, "a missing ocloc stops the build in seconds")

    def test_a_signature_must_be_valid_and_an_unsigned_zip_is_refused(self):
        test = branch("function Test-Publisher", "function Invoke-Installer")
        self.assertIn('$signature.Status -ne "Valid"', test)
        self.assertIn("REFUSED", test)
        self.assertIn("REFUSED $($file.Name): a .zip carries no signature", TOOLKITS)

    def test_the_signer_is_the_vendor_itself(self):
        nvidia, amd = signer_pattern("NVIDIA Corporation"), signer_pattern("Advanced Micro Devices")
        for subject in ("CN=NVIDIA Corporation, OU=2-J, O=NVIDIA Corporation, L=Santa Clara, S=California, C=US",
                        "O=NVIDIA Corporation"):
            self.assertTrue(nvidia.search(subject), subject)
        for subject in ('CN="Advanced Micro Devices, Inc.", O="Advanced Micro Devices, Inc.", L=Santa Clara, C=US',
                        "CN=Advanced Micro Devices Inc., O=Advanced Micro Devices Inc., L=Santa Clara, C=US"):
            self.assertTrue(amd.search(subject), subject)
        for subject in ("CN=NVIDIA Corporation Imitations, O=Someone Else", "CN=Someone, OU=NVIDIA Corporation",
                        "CN=Advanced Micro Devices Clone Ltd", ""):
            self.assertFalse(nvidia.search(subject) or amd.search(subject), subject)


class DlssTests(unittest.TestCase):
    """NVIDIA's DLSS SDK reaches the build as headers only, at a pinned SHA-256, and nothing of it ships."""

    def archive_branch(self):
        loop = TOOLKITS[TOOLKITS.index("foreach ($file in Get-ChildItem $Download -File)"):]
        body = loop[loop.index('"^dlss-sdk-'):]
        return body[:body.index("} elseif")]

    def test_the_archive_is_taken_only_at_its_pinned_sha256(self):
        body = self.archive_branch()
        check = body.index("if ($DlssHeaderArchives[$version] -ne $hash)")
        self.assertLess(body.index("Get-FileHash $file.FullName -Algorithm SHA256"), check)
        self.assertIn("$failed = $true; continue", body[check:body.index("Expand-Archive")])
        self.assertLess(body.index('Test-Path (Join-Path $target "include\\nvsdk_ngx.h")'),
                        body.index('SetEnvironmentVariable("DLSS_SDK_ROOT"'))
        pins = re.search(r"\$DlssHeaderArchives = @\{(.+)\}", TOOLKITS).group(1)
        self.assertRegex(pins, r'^\s*"\d+\.\d+\.\d+" = "[0-9a-f]{64}"(; "\d+\.\d+\.\d+" = "[0-9a-f]{64}")*\s*$')

    def test_the_build_takes_blenders_dlss_version_and_needs_the_headers(self):
        helper = (SCRIPTS / "windows_release_toolchain.ps1").read_text(encoding="utf-8")
        dlss = helper[helper.index('if ("dlss" -in $Allowed)'):helper.index("$switches = ")]
        self.assertIn("build_files\\buildbot\\config\\blender_version.cmake", dlss)
        self.assertNotRegex(dlss, r"\d+\.\d+\.\d+", "the version is Blender's pin, never a literal here")
        self.assertIn('-ne "dlss-sdk-$want"', dlss)
        self.assertIn("-DDLSS_SDK_ROOT=", dlss)
        self.assertIn('dlss = @("WITH_DLSS")', helper)
        pin = (SCRIPTS.parents[1] / "build_files/buildbot/config/blender_version.cmake").read_text(encoding="utf-8")
        version = re.search(r"set\(DLSS_VERSION\s+(\d+\.\d+\.\d+)\)", pin).group(1)
        self.assertIn('"%s" = "' % version, TOOLKITS, "an archive is pinned for the version Blender builds with")
        inputs = (SCRIPTS / "windows_release_inputs.ps1").read_text(encoding="utf-8")
        self.assertIn('"DLSS_SDK_ROOT"', inputs)

    def test_a_release_can_leave_dlss_out_and_stay_release_equivalent(self):
        """Without NVIDIA's written approval of its marks, 1.1 ships without DLSS: a release choice, not a missing part."""
        helper = (SCRIPTS / "windows_release_toolchain.ps1").read_text(encoding="utf-8")
        self.assertIn('$env:ANGELO_RELEASE_DLSS -notin "on", "off") { throw', helper, "any other value stops the build")
        off = helper[helper.index('elseif ($dlssOff)'):helper.index("else {", helper.index('elseif ($dlssOff)'))]
        self.assertIn('$toolkits.dlss = "off (', off, "the manifest records the choice")
        self.assertNotIn("DLSS_SDK_ROOT", off, "the SDK is neither looked for nor passed")
        loop = helper[helper.index("foreach ($part in $switches.Keys)"):helper.index("return @{ toolkits")]
        self.assertIn('($part -eq "dlss" -and $dlssOff)', loop, "configured with WITH_DLSS=OFF")
        self.assertLess(loop.index("$cmakeGpu +="), loop.index('$switches.Remove("dlss")'),
                        "WITH_DLSS is set OFF before it leaves the options the build then requires")
        build = (SCRIPTS / "build_release_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("release_equivalent = (-not $allowed)", build, "only -AllowMissing makes a build unlike a release")
        self.assertNotIn("ANGELO_RELEASE_DLSS", build)
        boot = (SCRIPTS / "windows_cloud_bootstrap.ps1").read_text(encoding="utf-8")
        self.assertIn('$env:ANGELO_RELEASE_DLSS = Get-Meta "angelo-release-dlss" ""', boot)
        self.assertLess(boot.index("$env:ANGELO_RELEASE_DLSS"), boot.index('Invoke-CheckScript "windows_release_validate.ps1"'))
        driver = (SCRIPTS / "gcp_windows_validate.sh").read_text(encoding="utf-8")
        self.assertIn('RELEASE_DLSS="${ANGELO_GCP_RELEASE_DLSS:-on}"', driver, "a release builds with DLSS unless told")
        self.assertIn('[[ "$RELEASE_DLSS" == on || "$RELEASE_DLSS" == off ]] ||', driver)
        self.assertIn("angelo-release-dlss=$RELEASE_DLSS,", driver)

    def test_the_msi_ships_no_nvidia_dlss_binary(self):
        build = (SCRIPTS / "build_release_windows.ps1").read_text(encoding="utf-8")
        self.assertIn('-Filter "nvngx*"', build)
        self.assertIn("$required += @($gpu.options.Keys | Where-Object { $_ -notin $allowed }", build)
        self.assertIn('"dlss", "agent-runtime"', build)


class DriverTests(unittest.TestCase):
    def test_cuda_never_installs_the_display_driver(self):
        default = re.search(r'\$defaultCuda = "([^"]+)"', TOOLKITS).group(1).split()
        self.assertNotIn("Display.Driver", default)
        cuda = branch("function Install-Cuda", "function Install-Hip")
        self.assertIn('if ($part -eq "Display.Driver")', cuda, "an override naming the driver is ignored")
        self.assertIn("carries no '$part' package", cuda, "a part the installer lacks is dropped, not passed")

    def test_hip_installs_only_the_sdk_msis_each_signed(self):
        hip = branch("function Install-Hip", "$failed, $restart = $false, $false")
        self.assertIn("Packages\\Apps\\ROCmSDKPackages\\$package\\", hip)
        self.assertNotIn("Drivers", hip)
        self.assertNotIn("Setup.exe", hip)
        self.assertLess(hip.index("Test-Publisher $msi.FullName $AMD"), hip.index('"msiexec.exe"'))
        packages = re.search(r"\$hipPackages = (.+)", TOOLKITS).group(1)
        self.assertEqual(re.findall(r'"(\w+)"', packages),
                         ["SDKCore", "RTCRuntime", "RTCDevelopment", "RayTracingRuntime", "RayTracingDevelopment"])
        # The bundle's own installer runs only when someone set angelo-hip-args.
        self.assertLess(hip.index("if ($HipArgs)"), hip.index("Invoke-Installer $File.FullName $HipArgs"))

    def test_the_bootstrap_installs_toolkits_only_through_the_checked_script(self):
        boot = (SCRIPTS / "windows_cloud_bootstrap.ps1").read_text(encoding="utf-8")
        self.assertLess(boot.index("Invoke-StageStep Invoke-ToolkitStage"), boot.index("# The libraries at the commit this source pins"))
        stages = (SCRIPTS / "windows_bootstrap_stages.ps1").read_text(encoding="utf-8")
        stage = stages[stages.index("== stage: GPU toolkits"):stages.index("function Invoke-DesktopStage")]
        self.assertIn("windows_toolkits.ps1", stage)
        self.assertNotIn("Run $file.FullName", stage, "no installer runs from the bootstrap itself")
        self.assertIn("$env:ANGELO_HIP_ARGS = Get-Meta", stage)
        self.assertIn('if ($kitExit -eq 1)', stage, "a refused installer leaves the stage to run again")


class PinTests(unittest.TestCase):
    """The installed set is pinned by the vendor file's SHA-256 (windows_toolkit_pins.json)."""

    PINS = json.loads((SCRIPTS / "windows_toolkit_pins.json").read_text(encoding="utf-8"))["installers"]

    def test_each_pin_names_its_sdk_version_signer_and_a_sha256_or_tbd(self):
        self.assertEqual(sorted({pin["sdk"] for pin in self.PINS}), ["cuda", "hip", "ocloc", "optix"])
        signers = {"optix": "NVIDIA Corporation", "cuda": "NVIDIA Corporation", "hip": "Advanced Micro Devices",
                   "ocloc": "Intel Corporation"}
        for pin in self.PINS:
            with self.subTest(pin=pin["file"]):
                self.assertRegex(pin["version"], r"^\d+\.\d+(\.\d+)?$")   # ocloc's pin has two fields
                self.assertRegex(pin["sha256"], r"^([0-9a-f]{64}|TBD)$")
                self.assertEqual(pin["signer"], signers[pin["sdk"]])
                self.assertTrue(pin["source"])
        self.assertEqual(len({pin["sha256"] for pin in self.PINS if pin["sha256"] != "TBD"}),
                         len([pin for pin in self.PINS if pin["sha256"] != "TBD"]), "one pin per file")

    def test_an_unrecorded_pin_fails_before_any_installer_runs(self):
        head = TOOLKITS[:TOOLKITS.index("foreach ($file in Get-ChildItem $Download -File)")]
        self.assertIn('$_.sha256 -notmatch "^[0-9a-f]{64}$"', head)
        self.assertIn("exit 1", head[head.index("$unrecorded = "):])

    def test_only_a_pinned_installer_runs_and_the_pin_is_checked_before_the_signature(self):
        loop = TOOLKITS[TOOLKITS.index("foreach ($file in Get-ChildItem $Download -File)"):]
        gate = loop.index("$pin = Get-Pin $file")
        self.assertLess(gate, loop.index("try {"))
        self.assertIn("is not a pinned installer", loop[gate:loop.index("try {")])
        self.assertIn("(Get-FileHash $File.FullName -Algorithm SHA256)", branch("function Get-Pin", "function Get-OptixVersion"))
        # The DLSS headers keep their own SHA-256 pin, checked in their branch.
        self.assertIn('if ($file.Name -notlike "dlss-sdk-*")', loop[:gate])

    def test_every_pin_must_be_installed_and_the_record_is_written(self):
        tail = TOOLKITS[TOOLKITS.index("# Every pin installed"):]
        self.assertLess(tail.index("foreach ($pin in $Pins)"), tail.index("Set-Content $Record"))
        self.assertIn("$failed = $true", tail[:tail.index("$installedRecord")])
        self.assertLess(tail.index("Set-Content $Record"), tail.index("if ($failed) { exit 1 }"))
        for field in ("optix_root", "hip_path", "cuda_paths", "installers"):
            self.assertIn(field + " = ", tail)

    def test_optix_and_hip_name_the_pinned_version_beside_older_installs(self):
        loop = TOOLKITS[TOOLKITS.index("foreach ($file in Get-ChildItem $Download -File)"):]
        optix = loop[loop.index('"optix.*\\.exe$"'):]
        optix = optix[:optix.index("} elseif")]
        self.assertIn('(Get-OptixVersion $_.Directory.Parent.FullName) -eq $pin.version', optix)
        self.assertIn('Join-Path $Kits ("optix-" + $pin.version)', optix)
        version = branch("function Get-OptixVersion", "function Register-Ocloc")
        self.assertIn("[math]::Floor($number / 10000)", version)
        hip = branch("function Install-Hip", "function Get-Pin")
        self.assertIn('C:\\Program Files\\AMD\\ROCm\\$series\\bin\\hipcc.exe', hip)
        self.assertNotIn("Sort-Object { $_.Directory.Parent.Name } -Descending", hip, "never the newest folder")

    def test_the_pinned_ocloc_is_chosen_by_its_file_version_never_the_newest(self):
        register = branch("function Register-Ocloc", "$failed, $restart = $false, $false")
        self.assertIn('[string]$Version = ""', register)
        self.assertLess(register.index('(Get-OclocVersion $_).EndsWith("." + $Version)'), register.index("Sort-Object LastWriteTime"))
        version = branch("function Get-OclocVersion", "function Register-Ocloc")
        # An ocloc.exe without a version resource is read from the Graphics Compiler beside it.
        self.assertLess(version.index("$Exe.VersionInfo.FileVersion"), version.index('"igc64.dll"'))
        self.assertIn('-notmatch "[1-9]"', version, "0.0.0.0 counts as no version too")
        loop = TOOLKITS[TOOLKITS.index("foreach ($file in Get-ChildItem $Download -File)"):]
        installer = loop[loop.index('"ocloc.*\\.exe$"'):]
        installer = installer[:installer.index("} elseif")]
        self.assertIn("Register-Ocloc $oneapi $file.Name $pin.version", installer)
        # An ocloc already installed (Intel's exit code 1001) is checked and named, never a failure.
        self.assertIn("@(0, 3010, 1001)", installer)
        self.assertLess(installer.index("if ($code -eq 1001) { $code = 0 }"), installer.index("Register-Ocloc $oneapi"))

    def test_a_changed_pin_file_installs_the_set_again(self):
        stages = (SCRIPTS / "windows_bootstrap_stages.ps1").read_text(encoding="utf-8")
        stage = stages[stages.index("function Invoke-ToolkitStage"):stages.index("== stage: GPU toolkits")]
        self.assertIn('"scripts\\angelo\\windows_toolkit_pins.json"', stage)
        self.assertLess(stage.index('"`npins "'), stage.index('Remove-Item (Join-Path $state "toolkits.done")'))

    def test_installers_do_not_stay_on_the_build_disk(self):
        loop = TOOLKITS[TOOLKITS.index("foreach ($file in Get-ChildItem $Download -File)"):TOOLKITS.index("# Every pin installed")]
        # Removed when installed now, when installed earlier, and when not pinned at all; a failed one stays for a retry.
        self.assertEqual(loop.count("Remove-Item $file.FullName -Force"), 3)
        catch = loop[loop.index("} catch {"):]
        self.assertNotIn("Remove-Item $file.FullName", catch)

    def test_a_cuda_installer_must_be_pinned_as_that_cuda_series(self):
        loop = TOOLKITS[TOOLKITS.index("foreach ($file in Get-ChildItem $Download -File)"):]
        cuda = loop[loop.index('"^cuda_'):]
        cuda = cuda[:cuda.index("} elseif")]
        self.assertLess(cuda.index('$pin.sdk -ne "cuda"'), cuda.index("Install-Cuda $file $version"))


class CudaToolchainTests(unittest.TestCase):
    """Every CUDA install holds what nvcc runs for a kernel, or the toolkits stage fails by name (build5 2026-11-15:
    CUDA 13.4 without its nvvm package failed the kernel build with "The system cannot find the path specified")."""

    def test_the_default_parts_include_what_cuda_13_split_out_of_nvcc(self):
        default = re.search(r'\$defaultCuda = "([^"]+)"', TOOLKITS).group(1).split()
        for part in ("nvcc", "crt", "nvvm"):
            self.assertIn(part, default)
        cuda = branch("function Install-Cuda", "function Install-Hip")
        self.assertIn('("lib" + $name) -in $folders', cuda, "CUDA 13 names the NVVM package libnvvm")

    def test_the_toolchain_list_names_every_program_nvcc_runs(self):
        names = re.findall(r'"([^"]+)"', TOOLKITS[TOOLKITS.index("$cudaToolchain = "):TOOLKITS.index("# The HIP SDK's own packages")])
        for need in ("bin\\nvcc.exe", "bin\\cudafe++.exe", "bin\\ptxas.exe", "bin\\fatbinary.exe", "bin\\nvlink.exe",
                     "nvvm\\bin\\cicc.exe", "include\\crt\\host_config.h"):
            self.assertIn(need, names)

    def test_each_cuda_install_is_checked_before_it_is_marked_installed(self):
        loop = TOOLKITS[TOOLKITS.index("foreach ($file in Get-ChildItem $Download -File)"):TOOLKITS.index("# Every pin installed")]
        install = loop.index("$code = Install-Cuda $file $version")
        check = loop.index("$missing = Get-CudaToolchainMissing $version", install)
        self.assertLess(check, loop.index("throw \"CUDA $version is installed without", check))
        self.assertLess(check, loop.index("Set-Content $installed"))

    def test_an_earlier_install_missing_a_part_is_installed_again(self):
        loop = TOOLKITS[TOOLKITS.index("foreach ($file in Get-ChildItem $Download -File)"):TOOLKITS.index("# Every pin installed")]
        recheck = loop.index("Get-CudaToolchainMissing (")
        self.assertLess(recheck, loop.index("Remove-Item $installed -Force"))
        self.assertLess(loop.index("Remove-Item $installed -Force"), loop.index("was installed by an earlier run\""))

    def test_a_changed_installer_script_installs_the_set_again(self):
        stages = (SCRIPTS / "windows_bootstrap_stages.ps1").read_text(encoding="utf-8")
        stage = stages[stages.index("function Invoke-ToolkitStage"):stages.index("== stage: GPU toolkits")]
        self.assertIn('"scripts\\angelo\\windows_toolkits.ps1"', stage)
        self.assertLess(stage.index('"`nscript "'), stage.index('Remove-Item (Join-Path $state "toolkits.done")'))


if __name__ == "__main__":
    unittest.main()
