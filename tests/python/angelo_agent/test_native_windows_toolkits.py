# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The full-feature Windows build's GPU toolkit installs: signatures first, never a display driver.

``scripts/angelo/windows_toolkits.ps1`` runs only on the throwaway Windows build machine
(``app_identity.rst``, "Running it on Google Cloud"), so these checks read its source: every installer branch
checks the vendor's Authenticode signature before anything runs, the CUDA and HIP installs leave the display driver
alone, and the signer pattern (run here with Python's ``re``, whose syntax this pattern shares with .NET's) accepts
the vendor's own subject and nothing that merely starts with its name.
"""

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
        build = (SCRIPTS / "build_release_windows.ps1").read_text(encoding="utf-8")
        oneapi = build[build.index('if ("oneapi" -in $allowed) { $toolkits.oneapi'):build.index("$switches = ")]
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
        stage = boot[boot.index("== stage: GPU toolkits"):boot.index("# The libraries at the commit this source pins")]
        self.assertIn("windows_toolkits.ps1", stage)
        self.assertNotIn("Run $file.FullName", stage, "no installer runs from the bootstrap itself")
        self.assertIn("$env:ANGELO_HIP_ARGS = Get-Meta", stage)
        self.assertIn('if ($kitExit -eq 1)', stage, "a refused installer leaves the stage to run again")


if __name__ == "__main__":
    unittest.main()
