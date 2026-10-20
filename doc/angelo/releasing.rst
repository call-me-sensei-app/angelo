.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Releasing Angelo
****************

The release procedure for both platforms. Steps marked [Credentials] need the
release's own certificates, keys and accounts; everything else runs from the
repository, and every step has a dry run (`Rehearsals`_).

One release is one commit, tagged ``v<version>``, built on both platforms. The
tools take certificate names, profile names and key resource names only; none
reads, prints or stores a password, private key or token, and none has a
built-in default for one: a missing setting stops the tool with a message that
names it.

================================================  ==========================================================
Tool                                              Does
================================================  ==========================================================
``scripts/angelo/sign_notarize_macos.py``         Stage, check, sign inside out, verify, launch-check, styled
                                                  disk image, sign, notarize, staple, validate (macOS).
``scripts/angelo/macho.py``, ``dmg_macos.py``     Mach-O inventory; the disk image and its stored window.
``scripts/angelo/sign_windows.ps1``               Authenticode with a Cloud KMS key: preflight, sign,
                                                  verify, verify the MSI's contents (Windows).
``scripts/angelo/windows_sign_staging.cmake``     CPack hook that signs what CPack is about to pack.
``scripts/angelo/build_release_windows.ps1``      ``-Sign``: preflight first, signing inside cpack, then the
                                                  MSI signed and checked file by file.
``scripts/angelo/sign_update_feed.py``            ``add-key`` (trust the KMS key), ``build``, ``sign``,
                                                  ``verify`` the update feed.
``scripts/angelo/make_update_delta.py``           The macOS update delta from the previous release's disk
                                                  image to this one's, checked by rebuilding the new app.
``scripts/angelo/publish_installers.py``          Upload the installers and ``SHA256SUMS`` to the downloads
                                                  bucket, then verify the public download (`Publishing the
                                                  installers`_); ``--add-to-version`` adds a platform later,
                                                  with its own ``.sha256``; ``--check`` tests the bucket and
                                                  domain.
``publish_installers.sh``                         The same, with its R2 credentials read from the Keychain.
``scripts/angelo/release_downloads.py``           The download layout every tool shares; ``r2_client.py`` is
                                                  the S3 (SigV4) client; ``published_files.py`` the
                                                  checksum files and the public verification.
``scripts/angelo/source_release.py``              The GPL source archive, dependency manifest, library
                                                  sources and written offer for a tag.
``scripts/angelo/release_checks.py``              Shared checks: Git LFS pointers (``lfs-pointers <install>``:
                                                  no shipped file may be a pointer; both release builds and
                                                  the macOS signer refuse one), build bytecode, the shipped
                                                  update keys, the agent runtime (``agent-runtime <folder>``).
``scripts/angelo/release_launch_checks.py``       The launch check, and the seal check (``seal``): a launch
                                                  writes nothing inside the app (`Nothing is written inside
                                                  the app`_).
``windows_agent_runtime_check.ps1`` (Windows)     The agent runtime an MSI or an install carries, checked
                                                  and run from there (`Agent runtime`_).
``scripts/angelo/agent_runtime_notices.py``       The agent runtime's third-party notices, from the Codex
                                                  fork's lock file at the pinned commit.
================================================  ==========================================================

The commands below use these settings, all the release's own; nothing in the
repository supplies them:

====================================  ==============================================================================
Setting                               Value
====================================  ==============================================================================
``ANGELO_SIGN_IDENTITY``              The Developer ID Application certificate's name or SHA-1 (``--identity``).
``ANGELO_NOTARY_PROFILE``             The ``xcrun notarytool store-credentials`` profile name (``--notary-profile``).
``ANGELO_SIGN_CERTIFICATE``           The Windows code-signing certificate file, public part only (``-SignCertificate``).
``ANGELO_SIGN_KMS_KEY``               The Authenticode key version, ``projects/<project>/locations/<location>/``
                                      ``keyRings/<keyring>/cryptoKeys/<key>/cryptoKeyVersions/<n>`` (``-SignKmsKey``).
``ANGELO_UPDATE_FEED_KMS_KEY``        The update-feed key version, in the same form (``--kms``).
``R2_ACCOUNT_ID``                     The Cloudflare account that holds the downloads bucket (not secret).
``R2_DOWNLOADS_ACCESS_KEY_ID``        The S3 access key id of the downloads token, an Account API token with Object
                                      Read & Write on ``angelo-downloads`` only (`Publishing the installers`_).
``R2_DOWNLOADS_SECRET_ACCESS_KEY``    That token's S3 secret access key.
``ANGELO_KEYCHAIN_SERVICE``           The Keychain service ``publish_installers.sh`` reads the three above from.
====================================  ==============================================================================

Order of a release
==================

1. **[Credentials] Once:** the Apple identity and notary profile, the two Cloud KMS
   keys (update feed, Authenticode) and the Windows certificate (below).
2. **[Credentials] Once:** trust the update-feed key in the source
   (``sign_update_feed.py add-key``), review and merge it. Builds made before
   this never find an update.
3. Move Claude Code's range (``ai_providers.rst``, "Claude Code's range"): run
   ``tests/python/angelo_agent/runtime_launch_check.py`` (unbilled) on the release
   Claude Code's ``stable`` channel delivers and on npm's ``latest``; set the
   floor in ``cli_runtime.RUNTIMES`` to the stable release when it passes, and
   the ceiling to the newest release the release's live agent tests ran on.
   Then date ``CHANGELOG.md``'s entry (``## 1.0.0 — <YYYY-MM-DD>``), run
   ``python3 scripts/angelo/changelog.py export``, merge, and **tag** the commit:
   ``git tag -s v1.0.0 <commit>`` (signing the tag is optional).
4. Build both platforms from that commit (``build_release_macos.sh``;
   ``build_release_windows.ps1 -Package -Sign``), each with the agent runtime
   built for it (`Agent runtime`_).
5. macOS: sign, notarize and staple (``sign_notarize_macos.py``); then the
   delta from the previous release (``make_update_delta.py``, `Update feed`_).
6. Source: ``source_release.py`` for the tag.
7. Feed: build, sign in Cloud KMS, verify.
8. **[Credentials]** Publish: the installers and the delta
   (``publish_installers.sh``, which verifies the public download) and the
   source first, then ``stable.json``, then the website's download links.
   Every Angelo that can update itself downloads the release in the
   background from then on (``updates_and_reports.rst``, "Installing updates"),
   so publish ``stable.json`` only once the installers are final.

Steps that need release credentials
===================================

Apple: identity and notary profile [Credentials]
------------------------------------------------

* **Developer ID Application certificate** for the vendor (``vendor.name`` in
  ``scripts/modules/angelo_identity.json``) in the login keychain of the Mac
  that signs (Xcode > Settings > Accounts > Manage Certificates > + > Developer
  ID Application; the Apple Developer account holder can create it).
  ``security find-identity -v -p codesigning`` must list exactly one
  ``Developer ID Application: <Organization> (<TEAMID>)``; that name (or its
  SHA-1) is ``--identity``.
* **Notary profile**, stored in the same keychain, once::

     xcrun notarytool store-credentials <profile> --apple-id <Apple ID> --team-id <TEAMID>
     # it asks for an app-specific password (appleid.apple.com); or, with an App Store Connect API key:
     xcrun notarytool store-credentials <profile> --key <AuthKey_XXXX.p8> --key-id <ID> --issuer <UUID>

  ``<profile>`` is ``--notary-profile``. Keep the ``.p8`` out of any
  repository, and never paste a password or key into a chat or a file here.

Update-feed key ceremony [Credentials]
--------------------------------------

The Ed25519 key in Cloud KMS, in the release's own Google Cloud project, as in
``updates_and_reports.rst``, "Key ceremony", steps 1 and 2. Then export its
public key and trust it in the source::

   gcloud kms keys versions get-public-key <n> --key <key> --keyring <keyring> \
     --location <location> --project <project> --output-file update-feed-public.pem
   python3 scripts/angelo/sign_update_feed.py add-key --key-id <key-id> --pem update-feed-public.pem
   git diff scripts/addons_core/angelo_agent/update_keys.json    # one new "active" entry

``add-key`` accepts only an Ed25519 public key (it refuses a private key file),
keeps the rest of the file, is idempotent, and refuses an id already used or a
key already listed. ``--kms <key version>`` reads the key through ``gcloud``
instead of a file. Merge it before the release build: the macOS signer and
``build_release_windows.ps1 -Sign`` refuse a build without an active key
(``--allow-no-update-key`` / ``-AllowNoUpdateKey`` exist for rehearsals and are
refused with notarization).

Windows certificate and KMS key [Credentials]
---------------------------------------------

Code-signing keys must live in an HSM, and the Cloud KMS CNG provider accepts
only ``HSM`` keys: ``EC_SIGN_P256_SHA256``, ``EC_SIGN_P384_SHA384`` or
``RSA_SIGN_PKCS1_{2048,3072,4096}_SHA256`` (``RSA_SIGN_PKCS1_4096_SHA512``).
Choose what the certificate authority accepts (RSA 3072 is the most widely
accepted)::

   gcloud kms keys create <key> --keyring <keyring> --location <location> \
     --project <project> --purpose asymmetric-signing \
     --default-algorithm rsa-sign-pkcs1-3072-sha256 --protection-level hsm

Then, with the CA: give it the key's HSM attestation (Cloud KMS console, the
key version's "Verify attestation", or the CA's own Cloud KMS instructions),
create the certificate request with the key (Google's "Create a CSR" with the
Cloud KMS PKCS #11 library and OpenSSL), and receive the OV or EV code-signing
certificate. Put the **public** certificate (``.crt``/``.cer``; a ``.pfx`` is
refused) on the Windows build machine, for example ``C:\angelo\codesign.crt``,
and any intermediate the CA asks to embed as ``-SignChainFile``.

On that machine: install the Windows SDK's Signing Tools (``signtool``), the
Cloud KMS CNG provider (the ``.msi`` from the GoogleCloudPlatform
kms-integrations releases; ``certutil -csplist`` then lists "Google Cloud KMS
Provider"), and give the signing identity ``roles/cloudkms.signerVerifier`` on
that key only: ``gcloud auth application-default login`` as a person who
holds it, or the build machine's service account. Use the CA's RFC 3161
time-stamping address as ``-SignTimestampUrl`` (DigiCert's
``http://timestamp.digicert.com`` is the default).

Publishing [Credentials]
------------------------

The GNU GPL requires the source of every published binary to be offered:
publish the source archive together with the installers, never after them.

* ``https://downloads.angelo.studio/<version>/``: the stapled DMG, the signed
  MSI and ``SHA256SUMS``, each written once and never changed; a platform
  published later is added with its own ``<installer>.sha256`` (`Publishing the
  installers`_).
* ``/source``: each version's source archive, dependency manifest, library
  sources, offer text and ``SHA256SUMS``; the website's ``LINKS.source`` points
  here (``urls.source`` in ``angelo_identity.json``; ``source_offer.txt`` names it).
* ``/updates/v1/stable.json``: last, byte for byte as signed
  (``updates_and_reports.rst``, "What the website must serve").

macOS
=====

From the repository root of the tagged commit::

   ANGELO_AGENT_RUNTIME_DIR=<agent runtime folder> scripts/angelo/build_release_macos.sh
   python3 scripts/angelo/sign_notarize_macos.py --app ../build-angelo-release/bin/Angelo.app \
     --out dist/macos --identity "$ANGELO_SIGN_IDENTITY" \
     --notary-profile "$ANGELO_NOTARY_PROFILE" --launch-check

Run it in a logged-in session: the disk image's window is laid out through
Finder, which asks once to let the terminal control Finder (System Settings >
Privacy & Security > Automation). ``ANGELO_SIGN_IDENTITY``,
``ANGELO_NOTARY_PROFILE`` and ``ANGELO_SIGN_KEYCHAIN`` may replace the options.
``--out`` must not already hold an ``Angelo.app``; the build folder is never
changed. ``dist/macos`` then holds the signed and stapled ``Angelo.app``,
``Angelo-1.0.0-macos-arm64.dmg`` (stapled) and ``angelo-macos-release.json``
with every step, the DMG's size and SHA-256, and the ``--download`` value for
the feed.

What it does, refusing at the first problem:

1. **Stage** a clone of the app; drop the ``.pyc`` files Python wrote (an
   older build that ran wrote some; the build's precompiled library and
   scripts stay, `Nothing is written inside the app`_), ``.DS_Store`` and the bundled
   Python's ``python.o`` and ``libpython*.a`` (current builds no longer
   install those two; the install rule is pinned by
   ``test_native_bundle_contents.py``).
2. **Check**: the bundle id and version are this source's; the shipped
   ``update_keys.json`` has an active key; ``Contents/Helpers`` holds exactly
   the agent runtime's pinned executables, arm64 Mach-O files
   (``release_checks.agent_runtime_problems``), and
   ``Contents/Resources/agent-runtime-release.json``, the release marker
   (``release_marker_problems``); ``--allow-no-agent-runtime`` accepts a build
   without them, for rehearsals, and is refused with notarization; every Mach-O file is an executable, dylib or bundle; no
   dependency or ``LC_RPATH`` is an absolute path outside the system; no
   symbolic link leaves the bundle; no nested bundle type other than the Quick
   Look ``.appex``.
3. **Sign inside out** with ``--options runtime --timestamp``: the dylibs
   and Python modules (deepest first), the bundled ``python3.13`` with
   ``release/darwin/entitlements.plist``, ``Contents/Helpers/codex`` and
   ``codex-code-mode-host`` (by path, nothing else) with
   ``agent_runtime_entitlements.plist``, the ``.appex`` with
   ``thumbnailer_entitlements.plist``, then ``Angelo.app`` with
   ``entitlements.plist``. The code-mode host arrives signed by OpenAI and is
   re-signed with Angelo's identity like every other file.
4. **Verify**: ``codesign --verify --strict`` and the hardened-runtime flag on
   every signature, a Developer ID authority, a secure timestamp and one team
   for all of them, the entitlements of every file signed with entitlements
   (the app, ``python3.13``, the agent runtime, the ``.appex``) exactly as its
   file, ``codesign --verify --deep --strict`` of the app, and ``spctl``
   (refused until notarized).
5. **Launch check** (``--launch-check``, ``release_launch_checks.py``): a
   clone starts headless under the hardened runtime, calls back through
   ctypes, uses NumPy and renders an OSL material with Cycles; the clone's
   signed agent runtime reports the pinned version and writes its app-server
   schema with ``cacheAffinityKey``. Then the **seal check** on another clone
   (`Nothing is written inside the app`_), headless and, unless
   ``--unstyled-dmg`` says there is no window server, a window launch: the
   clone must be unchanged and still pass ``codesign --verify --deep
   --strict``.
6. **Notarize the app**: a ``ditto -c -k --keepParent`` zip of the signed
   app goes to ``xcrun notarytool submit --keychain-profile <profile>
   --wait``; anything but ``Accepted`` fetches Apple's log into the error.
   The ticket is stapled to the app itself (``stapler staple`` and
   ``stapler validate``) and ``spctl --assess --type execute`` must say
   "Notarized Developer ID". A ticket on the image covers only the image, so
   without this an app dragged out of it could not be verified offline on its
   first launch.
7. **Disk image**: the stapled app, an ``Applications`` link and the background,
   laid out by ``release/darwin/dmg_window.applescript.in``; the layout Finder
   stores in ``.DS_Store`` is read back and must be the script's (540 by 372
   points, 128-point icons, the app at 135, 190 and Applications at 400, 190),
   or the script runs again, three times at most, because Finder can keep its
   default window although the icons were placed. Then UDZO compression,
   ``hdiutil verify``, a read-only mount to check the layout, the link and the
   app's signature, and ``codesign`` of the image.
8. **Notarize the image** the same way, then ``stapler staple`` and
   ``stapler validate``; ``spctl --assess --type open`` must say "Notarized
   Developer ID", and the app inside the mounted image must pass
   ``stapler validate`` and ``spctl --assess --type execute``.

``--skip-notarize`` stops after signing the image, ``--no-dmg`` after
verifying the app, ``--unstyled-dmg`` builds the image without Finder (a
session without a window server); none of these is a release.

Entitlements
------------

``release/darwin/entitlements.plist`` (the app and ``python3.13``), pinned by
``test_native_sign_notarize_macos.py``. Measured on the full-feature build with
an ad-hoc hardened-runtime signature, macOS 26.3:

============================================================  ===================================================
Entitlement                                                   Why
============================================================  ===================================================
``cs.allow-unsigned-executable-memory``                       **Required; weakens the runtime.** OSL's LLVM JIT:
                                                              without it the launch check's OSL render is killed
                                                              (SIGKILL, exit -9).
``cs.disable-library-validation``                             **Required; weakens the runtime.** Native modules of
                                                              add-ons and extensions (Python wheels) are not signed
                                                              by Angelo's team. Angelo's own libraries pass
                                                              validation without it once Developer ID signs them.
``device.audio-input``                                        Voice records through a child process attributed to
                                                              Angelo; scripts and extensions. macOS still asks.
``device.camera``                                             Scripts and extensions, as Blender. macOS still asks.
============================================================  ===================================================

Left out: upstream Blender's ``cs.allow-jit``. ``allow-unsigned-executable-memory``
already permits ``MAP_JIT`` (a hardened-runtime probe mapped ``MAP_JIT`` memory
with that key alone and failed with ``EINVAL`` with none), and every check
passes without it.

``release/darwin/agent_runtime_entitlements.plist`` is applied to the agent
runtime's two executables only, by path: ``com.apple.security.cs.allow-jit``
and ``cs.allow-unsigned-executable-memory``, OpenAI's own set for ``codex``
and ``codex-code-mode-host`` (``.github/scripts/macos-signing`` in the Codex
repository). The code-mode host runs code mode's JavaScript in V8, whose JIT
maps executable memory. Both link only system frameworks, so they need no
library-validation exception, and neither needs a device, the sandbox or a
provisioning profile. The launch check runs them signed, but neither
``--version`` nor the schema runs V8: a code-mode call does. To check it on a
signed build, run the signed runtime against the local fake endpoint, unbilled::

   python3.13 tests/python/angelo_agent/codex_prompt_probe.py <folder> --task lookup \
     --codex dist/macos/Angelo.app/Contents/Helpers/codex

whose scripted model calls tools from ``exec`` (V8 in the host); a killed
host (exit -9) means an entitlement is missing. Also not needed: the App Sandbox (only the ``.appex`` is
sandboxed), ``get-task-allow`` (no debugger on a release), Apple Events,
``keychain-access-groups`` and DYLD environment variables.

Running the installed app writes nothing inside it, so its signature stays
valid and an update finds the bundle as it was signed (`Nothing is written
inside the app`_).

Windows
=======

On the Windows build machine, in the tagged source, with the agent runtime
built for Windows (`Agent runtime`_)::

   .\scripts\angelo\build_release_windows.ps1 -Package -Sign `
     -AgentRuntimeDir C:\angelo\agent-runtime `
     -SignCertificate C:\angelo\codesign.crt `
     -SignKmsKey $env:ANGELO_SIGN_KMS_KEY `
     -ResultsDir C:\angelo\results

Order inside the script: ``sign_windows.ps1 -Mode Preflight`` (signtool, the
CNG provider, a code-signing certificate in date without a private key, the
key name), the update-key check and the agent-runtime check (the files, the
pinned version from ``codex.exe --version`` and ``cacheAffinityKey`` in its
app-server schema) run before anything compiles; after ``INSTALL`` no file
it wrote (``install_manifest.txt``) may be a Git LFS pointer; CPack then
installs into its staging folder and ``windows_sign_staging.cmake`` (passed as
``CPACK_PRE_BUILD_SCRIPTS``) signs every unsigned ``.exe``, ``.dll`` and
``.pyd`` there before WiX packs it, because CPack copies third-party DLLs and
Python modules from ``lib\windows_x64`` again and signing the build folder
would not reach the MSI. Then the MSI is signed and ``-Mode VerifyMsi``
unpacks it (``msiexec /a`` into a temporary folder) and requires a valid,
timestamped signature on every binary. ``angelo-release-build.json`` records
the certificate, key, timestamp service and counts; ``angelo-sign-*.json``
hold each step's report.

Each signature: ``signtool sign /fd sha256 /tr <RFC 3161 service> /td sha256
/f <certificate> /csp "Google Cloud KMS Provider" /kc <key version>``, in
batches with retries for a busy time-stamping service, and ``signtool verify
/pa``. A vendor's valid signature (Microsoft's runtime, GPU libraries) is kept;
a file changed after it was signed (``HashMismatch``) stops the build; any
other invalid signature is replaced. The agent runtime is the exception, by
its pin: OpenAI's unchanged executables in ``agent-runtime`` keep OpenAI's
signature and are never signed, and ``codex.exe`` always gets Angelo's
(`Agent runtime`_, "Signing"). ``ANGELO_SIGN_CERTIFICATE``,
``ANGELO_SIGN_KMS_KEY``, ``ANGELO_SIGN_TIMESTAMP_URL`` and
``ANGELO_SIGN_CHAIN_FILE`` may replace the options.

To review what would be signed without signing (any PowerShell)::

   .\scripts\angelo\sign_windows.ps1 -Mode Sign -DryRun -Path <build>\bin\Release `
     -CertificateFile C:\angelo\codesign.crt -KmsKey <key version> -Report plan.json

Then, on a clean Windows 11: install the MSI for the signed-in user (the
default: no UAC prompt, ``%LOCALAPPDATA%\Programs\Angelo``) and for all users
(``msiexec /i <msi> ALLUSERS=1`` from an elevated prompt: the UAC prompt names
the vendor), check that
``Get-AuthenticodeSignature "C:\Program Files\Angelo\Angelo.exe"`` is
``Valid``, and run the installed agent runtime::

   .\scripts\angelo\windows_agent_runtime_check.ps1 -InstallDir "C:\Program Files\Angelo" -Release -ExpectSigned

SmartScreen reputation for a new OV certificate builds up with downloads; an EV
certificate has it at once.

Publishing the installers
=========================

The installers are served from a public Cloudflare R2 bucket, ``angelo-downloads``,
reached only through its custom domain ``downloads.angelo.studio`` (R2 charges no
download egress); its ``r2.dev`` address stays disabled. One folder per version,
whose files are written once and never changed (``scripts/angelo/release_downloads.py``)::

   https://downloads.angelo.studio/<version>/Angelo-<version>-macos-arm64.dmg
   https://downloads.angelo.studio/<version>/Angelo-<version>-windows-x64.msi
   https://downloads.angelo.studio/<version>/SHA256SUMS
   https://downloads.angelo.studio/<version>/<installer>.sha256   (a platform added later)

``SHA256SUMS`` covers the files of the version's first publish. A platform whose
installer is ready later (the Windows installer days after the disk image, or
the other way round) is added to the published version with ``--add-to-version``:
its installer, and any delta with it, go beside the others, and
``<installer>.sha256`` (for example ``Angelo-1.0.1-windows-x64.msi.sha256``)
lists the installer and those deltas; ``SHA256SUMS`` is never rewritten. Each
object is stored with ``Cache-Control: public, max-age=31536000, immutable``,
``Content-Disposition: attachment`` and its type:
``application/x-apple-diskimage``, ``application/x-msi``, ``application/x-xz``
or ``text/plain`` (``SHA256SUMS`` and every ``.sha256``). Both hold ``shasum -a
256`` lines, so ``shasum -a 256 -c <checksum file> --ignore-missing`` checks a
download.

**Once** [Credentials]: the bucket with its custom domain, and an Account API
token with Object Read & Write on that bucket alone. Its S3 key pair and the
account id go in the login Keychain as generic passwords whose account is the
variable's name, under a service of your choice (prompted, so nothing reaches the
shell history)::

   security add-generic-password -U -s <service> -a R2_ACCOUNT_ID -w
   security add-generic-password -U -s <service> -a R2_DOWNLOADS_ACCESS_KEY_ID -w
   security add-generic-password -U -s <service> -a R2_DOWNLOADS_SECRET_ACCESS_KEY -w
   ANGELO_KEYCHAIN_SERVICE=<service> scripts/angelo/publish_installers.sh --check

``--check`` writes a random ``_check/`` object (``no-store``), reads it back,
fetches it from ``https://downloads.angelo.studio`` (proving the domain serves this
bucket), deletes it and confirms it is gone. The wrapper reads only variables not
already in the environment, puts them in the tool's environment and never on a
command line; ``--dry-run`` and ``--verify-only`` never open the Keychain.

**Each release**, after both installers are final, from the repository root::

   python3 scripts/angelo/publish_installers.py --version 1.0.0 \
     --dmg dist/macos/Angelo-1.0.0-macos-arm64.dmg \
     --msi dist/windows/Angelo-1.0.0-windows-x64.msi --dry-run
   ANGELO_KEYCHAIN_SERVICE=<service> scripts/angelo/publish_installers.sh --version 1.0.0 \
     --dmg dist/macos/Angelo-1.0.0-macos-arm64.dmg \
     --msi dist/windows/Angelo-1.0.0-windows-x64.msi

From the second release on, ``--delta dist/deltas/Angelo-<previous>-to-<version>-macos-arm64.delta.tar.xz``
publishes the macOS delta in the same run, beside the installers and listed in
``SHA256SUMS`` (``application/x-xz``). It must be named for this version, be a
delta whose description goes from its named version to this one, and come with
its platform's installer in the same run.

**A platform later**, once the version is published and verified, from a
checkout of that version's own release line (the same command with the late
platform's installer alone, and ``--add-to-version``)::

   ANGELO_KEYCHAIN_SERVICE=<service> scripts/angelo/publish_installers.sh --version 1.0.1 \
     --msi dist/windows/Angelo-1.0.1-windows-x64.msi --add-to-version

An addition takes only the version the checkout builds (``version.label`` in
``scripts/modules/angelo_identity.json``; an identity file without it refuses
every addition, naming the file, while the other modes still run), so a released
version never gains a platform from a later line: 1.0.0, published for macOS
alone, never takes a Windows installer; Windows ships as 1.0.1. This is a guard
rail, not a control: it protects a closed version only while the tool lives on
later lines. Merged into a closed version's own release line (for example
``angelo-v1.0-release``, whose label is 1.0.0), it would allow that version.
**Never merge this tool into a closed version's release line.** A version is
spelled one way only (ASCII digits, no leading zero), so ``01.0.0`` or other
digits never name another folder or delta.

Before sending anything it refuses: a file not named for the version; a Git LFS
pointer; a disk image that ``codesign --verify --strict``, ``stapler validate``
and ``spctl`` (which must say "Notarized Developer ID") do not accept, the same
check ``sign_notarize_macos.py`` ends with, so a disk image is published from a
Mac only; an MSI without an Authenticode signature (its ``DigitalSignature``
stream; ``--allow-unsigned-msi`` is a deliberate, printed exception); and any
object it would write that already exists. A first publish also refuses a
version that holds ``SHA256SUMS``, any platform's installer or any ``.sha256``
already, so its ``SHA256SUMS`` never leaves out a file of the version (to add a
platform, pass ``--add-to-version``). An addition refuses a version without
``SHA256SUMS`` (publish it first), a platform the version already has, and any
state its own verification would reject, read from the bucket through the S3
API (``published_files.coverage``, one rule for both): a file covered twice, a
file a checksum file lists that does not exist, a ``.sha256`` listing anything
but its installer and its platform's deltas to this version, and an installer
no checksum file covers. It names every object a cut-off publish left; delete
exactly those and run the addition again.

Then it writes. A first publish uploads the installers and deltas, then
``SHA256SUMS`` last: once it exists, the version is complete. An addition writes
each platform's ``<installer>.sha256`` first, then its installer and deltas: every
object an addition writes is named by a checksum file before it exists, so a
run cut off at any point is found, in full, by the next one, and an addition of
the same platform racing it stops at its first write. Every write carries
``If-None-Match: *`` and its real SHA-256 signed in (R2 refuses a body that
changed on the way, and a key that appeared since the check with ``412``).
After its writes, every publishing run, a first publish or an addition, reads
the version from the bucket again and applies the same coverage rule, so a
publish of the same version that ran at the same time (two first publishes of
different platforms, the second losing its ``SHA256SUMS`` with ``412`` after its
installer went up) fails, naming every object it left, instead of succeeding
over an installer no checksum covers. A run whose write fails (``412`` or any
other error) reads the bucket again too, best effort, and names every object it
wrote before it stopped and every leftover the bucket shows, so both racing runs
report.

Then it verifies from the custom domain: ``SHA256SUMS`` and each platform's
``<installer>.sha256``, then every file they list (installers and deltas, given
or not) and every file given, each against the one checksum file that covers it
(a file covered by none or by both fails), and each file sent against what was
sent: status 200, ``Content-Length``, ``Content-Type``, ``Cache-Control``,
``Content-Disposition`` and the SHA-256. It never asks the domain for an
installer no checksum file lists, nor, after publishing, for a key the bucket
showed absent: Cloudflare can cache a 404, and a cached 404 on a file a later
addition writes would break that addition's check and give users a 404. It
prints the addresses, the ``.sha256`` files, the website's ``RELEASE.sha256``
values for every installer the version has and the ``--download`` arguments of
this run's files for the feed (a feed naming both platforms after an addition
takes the earlier platform's ``--download`` from its own file, as before).

A ``--verify-only`` run asks the domain for each platform's ``.sha256``, the
ones not yet published included, and for every file a ``.sha256`` lists. Run
shortly before an addition, it can leave a cached 404 on that ``.sha256``; run
during an addition (after its ``.sha256`` went up, before its installer), on the
installer itself (``.dmg`` is among the types Cloudflare caches by default).
When a file the run itself wrote answers 404, the check says so ("answered 404,
but it is the checksum file this run wrote" or "a file this run wrote"): purge
each URL it names from Cloudflare's cache (the dashboard's zone, Caching,
Configuration, Custom Purge, by URL, or ``POST
/client/v4/zones/<zone id>/purge_cache`` with ``{"files":
["https://downloads.angelo.studio/<version>/<installer>.sha256",
"https://downloads.angelo.studio/<version>/<installer>"]}``) and run
``--verify-only`` again; nothing needs publishing again. Run no ``--verify-only``
while an addition is in progress.

It reads its credentials only from ``R2_ACCOUNT_ID``,
``R2_DOWNLOADS_ACCESS_KEY_ID`` and ``R2_DOWNLOADS_SECRET_ACCESS_KEY``, signs every
request in its header (SigV4, no presigned URL), and prints neither a credential
nor a signature nor R2's error bodies: a failure names the request, the object
and the HTTP status. ``--verify-only`` repeats the public verification of a
published version without credentials, whichever way it was published: every
file its checksum files list is checked against them, and the files given
against the published ones. If a run stops after an upload, nothing links to
what it wrote yet: for a first publish, delete the version's objects and publish
again; for an addition, run it again, and it refuses naming every object the
cut-off left (its ``.sha256`` and whichever of its files went up): delete exactly
those and run it once more. The tool never overwrites an object. The website
links the files only after the verification succeeds.

Update feed
===========

After both installers are final (the stapled DMG, the signed MSI), and from the
second release on, the macOS delta from the previous release (the published
disk image, downloaded again and checked against its ``SHA256SUMS``)::

   python3 scripts/angelo/make_update_delta.py --platform macos-arm64 \
     --from-version 1.0.0 --from dist/1.0.0/Angelo-1.0.0-macos-arm64.dmg \
     --to-version 1.0.1 --to dist/macos/Angelo-1.0.1-macos-arm64.dmg --out dist/deltas

   python3 scripts/angelo/sign_update_feed.py build --version 1.0.0 --sequence 1 \
     --download macos-arm64=dist/macos/Angelo-1.0.0-macos-arm64.dmg \
     --download windows-x64=dist/windows/Angelo-1.0.0-windows-x64.msi \
     --crash-reports https://console.angelo.studio/api/v1/crash-reports --out payload.json
   python3 scripts/angelo/sign_update_feed.py sign payload.json --key-id <key-id> \
     --kms "$ANGELO_UPDATE_FEED_KMS_KEY" --out stable.json
   python3 scripts/angelo/sign_update_feed.py verify stable.json

``platform=FILE`` names the installer at its published address
(``https://downloads.angelo.studio/<version>/<file name>``) and refuses a file not
named for the version; a link on that host must be that address
(``platform=URL=FILE`` and ``platform=URL=SHA256=SIZE`` remain for other hosts,
but Angelo installs only from ``downloads.angelo.studio``: a release linked
elsewhere is offered as a download). ``--delta macos-arm64=<from>=FILE`` adds a
delta (from the second release on; the older the ``from``, the larger it is, and
a build without a matching delta downloads the disk image), and
``--manual-install`` tells every build to offer this release as a download
instead of installing it.

``build`` refuses an Unreleased changelog entry, a sequence that does not grow
(``--previous`` with the published feed, from the second release on) and any
field the app would refuse; ``sign`` verifies the KMS signature before writing;
``verify`` checks the file exactly as the app does, against the shipped
``update_keys.json``. Announce ``--crash-reports`` only once that endpoint is
deployed (``updates_and_reports.rst``, "The crash-report endpoint"); a feed
without it is valid, and a later feed can add it without a new build.

Rehearsal with a throwaway key (no KMS; delete the folder afterwards)::

   R=$(mktemp -d)
   python3 scripts/angelo/sign_update_feed.py keygen --out $R/key.pem --public-out $R/public.pem
   cp scripts/addons_core/angelo_agent/update_keys.json $R/keys.json
   python3 scripts/angelo/sign_update_feed.py add-key --key-id rehearsal --pem $R/public.pem --keys $R/keys.json
   python3 scripts/angelo/sign_update_feed.py sign payload.json --key-id rehearsal --key-file $R/key.pem --out $R/stable.json
   python3 scripts/angelo/sign_update_feed.py verify $R/stable.json --keys $R/keys.json

Source code
===========

The GNU GPL covers Angelo and the Blender it is built on: every published
binary needs its complete corresponding source. For the tag::

   python3 scripts/angelo/source_release.py list --tag v1.0.0              # what goes in
   python3 scripts/angelo/source_release.py fetch-deps --tag v1.0.0 --out dist/deps
   python3 scripts/angelo/source_release.py archive --tag v1.0.0 --out dist/source \
     --dependency-sources dist/deps

``archive`` refuses a tag that is not ``v`` plus the identity version at that
commit and a Git LFS file without a local object (``git lfs fetch`` first). It
writes ``Angelo-1.0.0-source.tar.xz`` (every tracked file under
``angelo-1.0.0/``, LFS content from the local store, reproducible; Blender's
regression-test data in ``tests/files`` is left out, as Blender's own source
archive does), ``Angelo-1.0.0-dependencies.json`` (the pinned ``lib/*``
submodule commits and, from ``versions.cmake``, every library's version,
license, upstream and Blender-mirror download and checksum),
``Angelo-1.0.0-dependency-sources.tar`` (each library source checked against
its checksum; ``fetch-deps`` downloads them, several gigabytes, from Blender's
``lib-source`` mirror), ``Angelo-1.0.0-source-offer.txt`` and ``SHA256SUMS``.

The written offer is ``release/text/source_offer.txt``: CMake installs it next
to ``copyright.txt`` in both installers (``Angelo.app/Contents/Resources/text``
and the install folder), and ``source_release.py`` writes the same text for the
website. It names ``urls.source`` and ``vendor.contact_email``; both must work
before the first public download.

Agent runtime
=============

Angelo bundles its agent runtime: a build of OpenAI Codex with the app-server
parameter ``cacheAffinityKey`` (``gpt_launch.rst``, "Agent runtime and prompt
caching"), the custom-provider ``request_compression`` capability, and the line,
column, source line and caret of a failed code-mode cell in its error text
(``release/license/agent-runtime/README.txt`` describes each).
``scripts/addons_core/angelo_agent/agent_runtime.json``
pins it: the Codex version (``0.163.0``), the fork's branch and
commit, the feature and, per platform, the folder in the installed app and its
files. Upstream names are kept, because Codex finds its code-mode host next to
its own executable:

=========  ==================================  ==============================================================
Platform   Installed in                        Files
=========  ==================================  ==============================================================
macOS      ``Angelo.app/Contents/Helpers``     ``codex``, ``codex-code-mode-host`` (arm64)
Windows    ``<install folder>\agent-runtime``  ``codex.exe``, ``codex-code-mode-host.exe``; when present,
                                               ``codex-command-runner.exe`` and
                                               ``codex-windows-sandbox-setup.exe``
=========  ==================================  ==============================================================

**Building it.** In the fork, on the pinned commit, as ``ANGELO.md`` there
says (upstream's release profile, then ``strip -S -x`` on macOS); the code-mode
host is upstream's own build of the same release. On Windows ``codex.exe`` is
built the way upstream's ``rust-release-windows.yml`` builds it (the MSVC
Build Tools and Windows SDK, its MSVC environment, ``cargo build --target
x86_64-pc-windows-msvc --release --bin codex``; the ``.pdb`` is kept apart and
never bundled), and the other three files are OpenAI's signed release assets of
the pinned tag, renamed without their ``-x86_64-pc-windows-msvc`` suffix and
checked against the SHA-256 digests GitHub publishes for them. Keep a
``SHA256SUMS`` and the build's provenance beside them. Put the files in one
folder and check it::

   python3 scripts/angelo/release_checks.py agent-runtime <folder> [--platform macos|windows]

It refuses a missing or wrong-platform file (an arm64 Mach-O executable on
macOS, an x64 PE executable on Windows), a Windows helper without OpenAI's
embedded signature, a version other than the pin (``codex --version``) and a
build without the parameter in its app-server schema (``codex app-server
generate-json-schema``: stock Codex reports the same version). Both run offline
and write only to a temporary ``CODEX_HOME``; ``--no-run`` checks another
platform's folder without running it.

**Release builds require it.** ``build_release_macos.sh`` stops without
``ANGELO_AGENT_RUNTIME_DIR``, runs that check and configures with
``-DANGELO_AGENT_RUNTIME_DIR`` and ``-DANGELO_REQUIRE_AGENT_RUNTIME=ON``
(``build_files/cmake/angelo_agent_runtime.cmake``, which installs exactly the
pinned files, refuses a folder missing one and, for a release, installs the
pin's ``release_marker``: the app then refuses any path in
``ANGELO_AGENT_RUNTIME`` and accepts only ``off``).
``build_release_windows.ps1`` takes ``-AgentRuntimeDir`` (or
``ANGELO_AGENT_RUNTIME_DIR``) and checks the same in PowerShell, plus OpenAI's
valid signature on the helpers (``Get-AuthenticodeSignature``), before anything
compiles: both platforms find the runtime the same way, in the folder
``ANGELO_AGENT_RUNTIME_DIR`` names on the build machine. After the build the
installed copies must be the checked files byte for byte, and the manifest
records each file's SHA-256. Without the folder the script stops at once with
what to supply; ``-AllowMissing agent-runtime`` builds a rehearsal without it
(the manifest's ``agent_runtime`` and ``release_equivalent`` say so, and Codex
connections then use the person's own Codex), and ``-Sign`` refuses it.
``test_blender_release_features`` fails on a full-feature build without the
runtime and checks a bundled one's version and parameter.

**Signing.** macOS signs both executables with Angelo's identity (`Entitlements`_).
On Windows the pin's ``upstream_signed`` files (``codex-code-mode-host.exe``,
``codex-command-runner.exe``, ``codex-windows-sandbox-setup.exe``: OpenAI's
unchanged release files) ship with OpenAI's own Authenticode signature
(``upstream_signer``): ``sign_windows.ps1`` keeps it, never signs, re-signs or
strips one, and stops when one is not validly signed by OpenAI; ``codex.exe``,
Angelo's build, is signed with Angelo's certificate like every other Angelo
binary and may never keep another signer's. ``-Mode VerifyMsi`` requires
OpenAI's signature on the helpers, Angelo's on ``codex.exe``, and exactly the
pinned files in the MSI's ``agent-runtime`` folder.

**Checking the installed runtime.** ``windows_agent_runtime_check.ps1`` takes
``-Msi`` (unpacked with ``msiexec /a``) or ``-InstallDir``: exactly the pinned
files, byte for byte the source folder's (``-SourceRuntimeDir``), the release
marker (``-Release``), the signatures above (``-ExpectSigned`` once signed),
then, from that folder, ``codex.exe --version``, the app-server schema's
``cacheAffinityKey`` and an ``initialize`` over standard input whose user agent
must name the pinned version, as the app requires of every runtime process.
``windows_release_validate.ps1`` runs it on every MSI it validates.

**On a cloud validation machine.** ``gcp_windows_validate.sh agent-runtime
<folder>`` (a Compute Engine Windows machine in your own Google Cloud project,
named by the ``ANGELO_GCP_*`` settings) checks a Windows runtime folder locally
(``release_checks.py``, its ``SHA256SUMS``) and uploads the pinned files with
their sums to that machine's storage bucket; a ``release-build`` then fetches
them into ``C:\angelo\agent-runtime``, checks the sums again and builds with it
as ``ANGELO_AGENT_RUNTIME_DIR``.

**Notices.** ``release/license/agent-runtime`` (installed with the rest of
``release/license``): Codex's Apache-2.0 ``LICENSE.txt`` and ``NOTICE.txt``
from the pinned commit, ``README.txt`` (what is bundled, the modification and
where its source is) and ``THIRD-PARTY-NOTICES.txt``. Upstream publishes no
notices for the binaries, so ``agent_runtime_notices.py`` writes them from the
fork's ``Cargo.lock`` at the pinned commit: every run-time crate of the pinned
programs for both platform targets with its license texts, and the components
of V8 linked into the code-mode host. Rerun it whenever the pin changes::

   python3 scripts/angelo/agent_runtime_notices.py --fork <Codex fork checkout>

It reads the fork through ``git archive`` and ``cargo metadata --offline``
(the crates must be in Cargo's cache, as after a build of the runtime) and
changes nothing there. ``test_native_agent_runtime_notices.py`` checks that the
notices are this pin's.

**Returning to stock Codex.** Once upstream ships an equivalent of the
parameter (openai/codex#21796), point the pin at that release's binaries,
drop the fork from the README and the notices' modification statement, and
keep the release checks: they then guard the upstream build.

Nothing is written inside the app
=================================

A launch never writes inside the installed app. In a macOS bundle a new file
breaks the code signature's sealed resources: ``codesign --verify --deep
--strict`` then fails ("a sealed resource is missing or invalid") and macOS can
call the app damaged, and Sparkle's updates need the bundle sealed. In a
Windows install folder it leaves files Windows Installer does not own, in the
way of repair and updates. Stock Blender writes Python bytecode
(``__pycache__``) next to every module it imports: a build without the
measures below wrote 806 ``.pyc`` files (in 68 new folders) into its bundle on
one headless launch with the add-on, and 25 more on the next, a window launch:
the standard library, Blender's ``scripts`` (startup, modules, the bundled
add-ons) and 376 files of
``angelo_agent``. Where the folder is not writable (another user,
Program Files, a bundle macOS protects from a terminal without App Management
permission), Python silently compiled every module again on every launch
instead.

* **Angelo's interpreter**: before Python starts,
  ``source/blender/python/intern/bpy_interface_pycache.cc`` sets
  ``PyConfig.pycache_prefix`` to ``<bpy.app.cachedir>/pycache/<build hash>``:
  ``~/Library/Caches/Angelo/pycache/<hash>`` on macOS,
  ``%LOCALAPPDATA%\Angelo\Cache\pycache\<hash>`` on Windows. Python mirrors
  each source's absolute path below it, so two installs never share a file,
  and an updated build never reads another build's bytecode. A build without
  build information uses ``dev-<major>.<minor>``. An explicit absolute
  ``PYTHONPYCACHEPREFIX`` chooses the folder and an explicit
  ``PYTHONDONTWRITEBYTECODE`` turns writing off, as for a standalone Python,
  although Blender's isolated configuration ignores the environment otherwise.
  A relative ``PYTHONPYCACHEPREFIX`` is relative to the working folder, which
  can be inside the app (from there it wrote 509 entries into
  ``Contents/Resources``), so it turns writing off; so does a missing cache
  folder.
* **Its children**: ``bpy.app.python_args`` (which Blender's extension system
  passes to ``sys.executable``) carries ``-X pycache_prefix=<folder>`` (``-B``
  while writing is off), and the folder is in ``ANGELO_PYCACHE_PREFIX``: the
  bundled ``sitecustomize.py`` gives it to any other child of the bundled
  Python, such as a ``multiprocessing`` worker (``-I`` makes those ignore
  ``PYTHONPYCACHEPREFIX``). Any other run of the bundled Python writes no
  bytecode; an explicit absolute prefix or ``-B`` is kept, a relative prefix
  writes none.
* **Everything the app ships is precompiled**: a child can bypass
  ``sitecustomize`` or import modules before it: pip's isolated build of a
  source distribution puts its own ``sitecustomize`` first (43 ``.pyc`` files
  in the bundle from one ``pip install`` started as an add-on starts it), so
  does any ``sitecustomize`` on ``PYTHONPATH``; a venv made from the bundled
  Python; ``-S``; ``-W``, ``-X dev``, ``PYTHONWARNINGS``, ``-X
  frozen_modules=off`` or a user ``.pth`` file; the shipped commands in
  ``scripts/angelo`` under any of those wrote into ``scripts`` too. The install
  step (``build_files/cmake/angelo_python_bytecode.cmake``) therefore compiles
  every module of the bundled library (``site-packages`` included) and of
  Blender's ``scripts`` with the installed interpreter
  (``build_files/utils/angelo_python_bytecode.py``): 3,178 modules on macOS,
  none that cannot compile, as checked hash-based ``.pyc`` (PEP 552). Such a
  child finds valid bytecode and writes none; the files are valid whatever time
  an installer gives the sources, an edited source is compiled again, and an
  incremental install recompiles only what changed.
  ``release_checks.is_build_bytecode`` recognises them, so staging keeps them
  and drops anything Python wrote. Angelo's own interpreter does not read them
  (it reads only its cache), so they do not change its startup.
* **Size**: 52.9 MB of bytecode (31.8 library, 21.2 scripts); the compressed
  disk image grows 22.2 MB (441,986,752 against 419,823,789 bytes, the same
  staged app with and without it, UDZO level 9, +5.3 %); the MSI an estimated
  23 MB (the files compressed with deflate level 9; the Windows library
  differs slightly).
* **Old builds' caches** (``angelo_agent/pycache_retention.py``): a window
  session, a minute after startup on a thread, marks its build's folder used
  and removes a sibling folder only when its name is a build key the app
  generates, it is unused for seven days, and its whole tree is ``.pyc``
  files, folders and the mark: a folder anyone else put there is never
  touched. It refuses to run when the cache root, or a folder above it up to
  the Caches folder, is a symbolic link or a Windows junction. Inside its own
  folder it removes the mirrors of source folders that no longer exist, by the
  same content rule (POSIX): App Translocation gives a quarantined app launched
  where it was downloaded a new random path on each launch (about 18 MB of
  bytecode each), and a moved or deleted install leaves its mirror behind. A
  folder chosen with ``PYTHONPYCACHEPREFIX`` is not Angelo's to manage. One
  build's folder holds about 17 MB after a launch with the add-on.

Startup is unchanged: the bytecode is compiled once per build either way, now
into the cache. A first launch with an empty cache compiles about 900 modules
(1.2 s on an M4 Pro), as a build without the prefix does into its bundle on
its first launch; the bundled bytecode cannot spare it, because Python reads
bytecode only from the prefix once one is set. Measured on one Mac with the
same source built without and with these measures, both staged and signed ad
hoc and started through Launch Services, interleaved (seconds from ``open``
until the add-on is enabled):

* Eight pairs at a 1-minute load average of about 7 (means), without and with:
  headless cold 1.867 and 1.868, warm 0.632 and 0.632; window cold 2.284 and
  2.283, warm 0.970 and 0.973.
* The build with these measures, with and without its bundled bytecode, eight
  pairs: with it equal or faster (headless warm 0.640 against 0.679, window
  cold 2.276 against 2.320).

**The seal check** proves it on a built app, pass or fail with a JSON report::

   python3 scripts/angelo/release_launch_checks.py seal --app <Angelo.app> --gui --report seal.json
   python scripts\angelo\release_launch_checks.py seal --install <install folder> --report seal.json

On macOS it launches a clone through Launch Services (``open``), as Finder
does: only then may the app write into its own bundle. Started from a terminal
without App Management permission its writes are refused, so such a launch
could prove nothing. It drops the caller's ``PYTHON*``, ``BLENDER_*`` and
``ANGELO_*`` variables (a caller's ``PYTHONDONTWRITEBYTECODE`` would hide every
write) and uses a throwaway profile for each launch: headless
(``scripts/angelo/release_seal_probe.py``), enabling the add-on and starting
a ``bpy.app.python_args`` child, ``pip`` building a source distribution as an
add-on would, a ``-W`` child and a ``multiprocessing`` worker; then
(``--gui``, needs a desktop session) a window launch that runs Angelo's own
startup and quits ten seconds after the add-on loads. It refuses when the
clone's inventory changed, when ``codesign --verify --deep --strict`` no longer
passes (checked when the input was validly signed), when the launch wrote no
bytecode at all, when a child keeps its bytecode elsewhere or fails, or when a
bundled module that compiles has no precompiled bytecode or one that differs
from its source. On Windows it launches the install folder in place and
compares its inventory. It runs in ``sign_notarize_macos.py --launch-check``,
in the Windows release stage (``windows_release_validate.ps1``, on the build's
folder before any suite runs it) and in the installed-program scenario of
``windows_validate.ps1`` (Program Files, by path, size and time; the launch
must have written bytecode, to a cache outside the install folder). On a
signed build without these measures it fails: 846 ``.pyc`` files and 73 folders
added, "a sealed resource is missing or invalid"; with the cache prefix but
without the precompiled bytecode it fails too (51 entries from the pip child,
modules without bytecode).

A person can still change the bundle on purpose: ``pip install`` into the
bundled ``site-packages`` puts the package there, and an extension installed
into the System repository (``5.2/extensions/system``) writes there. Neither
happens on a launch.

Known limits:

* **Uninstalling leaves the cache**: dragging the app to the Trash leaves
  ``~/Library/Caches/Angelo/pycache`` and the MSI leaves
  ``%LOCALAPPDATA%\Angelo\Cache\pycache`` (about 17 MB per build used in the
  last week). Retention trims it while Angelo is used; nothing removes it after.
* **The Windows seal check uses the real profile's cache**: Windows gives the
  app its folders through known-folder APIs no environment variable redirects,
  so the check's throwaway profile does not move the cache; the install
  folder, which is what it checks, is unaffected.
* **MSIX paths**: under an MSIX install the mirrored bytecode paths reach 282
  characters (158 files over the 260 limit for a long user name). Python then
  cannot write those files and compiles those modules again at each launch:
  speed only, nothing is written inside the app. The MSI stays under the
  limit (232 at most for an 18-character user name).
* **Translocation on Windows**: stale mirrors are removed on POSIX only (a
  Windows mirror drops the drive letter, so a missing folder cannot be told
  from one on another drive); old build folders are removed on both.
* **App Translocation is checked by unit tests only**: a real translocated
  launch needs a quarantined app and the Gatekeeper prompt.

Bundle contents
===============

``source/creator/CMakeLists.txt`` installs ``scripts/`` without
``scripts/angelo`` and then exactly ``ANGELO_RUNTIME_SCRIPTS``: the external CLI
launcher (``angelo_cli.py``), ``migrate_user_folder.py`` and
``recover_connections.py``, the commands a person runs with the bundled
Python. Build, release, signing, cloud and Windows validation scripts never
ship. ``test_native_bundle_contents.py`` pins the list, installs a copy of
``scripts/angelo``'s names through the rule's own text, and checks that shipped
code only ever names those three.

No builder paths
================

Nothing Angelo ships may name the machine or the person that built it: no home
folder, temporary folder, user name or host name, in any file, binaries
included, and no credential. Two parts keep it so.

* **The build maps its own paths away.** ``build_files/cmake/angelo_build_paths.cmake``
  maps the source tree, the build folder, ``LIBDIR`` and the builder's home to
  ``angelo``, ``build``, ``lib`` and ``~`` for every target: ``-ffile-prefix-map``
  on Clang (``__FILE__`` in asserts and library headers, debug information) with
  ld64's ``-oso_prefix``; on MSVC ``/d1trimfile`` for ``__FILE__`` and
  ``/PDBALTPATH:%_PDB%``, so the executable names its ``.pdb`` (never shipped) by
  file name. OSL bytecode, where ``oslc`` writes its options and every source
  and include path, is rewritten with the same map (``angelo_map_paths.cmake``).
  Bundled Python bytecode records sources relative to the version folder
  (``build_files/utils/angelo_python_bytecode.py``); importing still shows the
  installed path in a traceback. ``ANGELO_MAP_BUILD_PATHS=OFF`` is for a local
  debugging build only.
* **Every release stage refuses what is left.** ``release_checks.py
  builder-paths <folder>`` (``scripts/angelo/builder_paths.py``) reads every
  file byte by byte, in UTF-8 and UTF-16LE, for ``/Users/<name>``,
  ``/home/<name>``, ``/var/folders`` and ``/private/var/folders``,
  ``<drive>:\Users\<name>``, the user and host names of the machine running the
  check (plus ``--identity``), and credentials in their published formats. Any
  hit fails with each file and its count. ``build_release_macos.sh`` runs it on
  ``Angelo.app``; ``build_release_windows.ps1`` on the installed folder and,
  after signing, on the MSI's administrative-install payload, writing
  ``<msi>.builder-paths.json``, a receipt tied to the MSI's SHA-256.
  Build artifacts (``.pdb``, ``.exp``, ``.ilk``, ``.iobj``, ``.ipdb``, ``.obj``,
  ``.lib``) are refused by type: they are excluded from the install
  (``source/creator/CMakeLists.txt``), never allowlisted.
  The installed-folder scan reads only what the install wrote
  (``--files-from install_manifest.txt``): build-only output beside it is never
  packed. The Windows installer carries no ``blender.pdb`` (it records every
  source path); the release keeps ``blender_public.pdb`` and
  ``blender_private.pdb`` to symbolicate crash reports
  (``ANGELO_SHIP_WINDOWS_PDB=ON`` would install it).
  ``publish_installers.py`` (``--dry-run`` included) mounts the disk image
  read-only and scans it, and refuses an MSI without a clean receipt for exactly
  its bytes.

The only exceptions are in ``scripts/angelo/builder_paths_allow.json``, each
with its files, its exact match and its reason: third-party files shipped
unchanged (Blender's precompiled libraries and embedded ``.blend`` data, the
Python standard library's documentation examples, the upstream agent-runtime
helpers built on public CI runners). Credentials and the building machine's
own names are never allowed. The agent runtime Angelo builds itself (``codex``)
needs ``--remap-path-prefix`` for the builder's home, Cargo registry, toolchain
and checkout (``RUSTFLAGS``; on Windows also the fork's own MSVC
``rustflags``, which ``RUSTFLAGS`` replaces).

Rehearsals
==========

Every step has a dry run that needs no certificate, key or account and changes
nothing outside a temporary folder:

* **macOS**: ``sign_notarize_macos.py --identity - --skip-notarize
  --allow-no-update-key --launch-check`` (and ``--allow-no-agent-runtime`` for
  a build without the runtime) on a copy of the full-feature build
  signs every file ad hoc with the hardened runtime and runs every check except
  notarization and Gatekeeper (``spctl`` refuses an ad-hoc signature, as
  expected). The launch check must pass (ctypes callback, NumPy, OSL render,
  exit 0) and so must the seal check; the same app re-signed without ``allow-unsigned-executable-memory``
  is killed at the OSL render (exit -9). ``test_blender_release_features`` on a
  copy of the signed app then checks Cycles and OSL, OpenVDB, remesh, Bullet,
  every importer and exporter, FFmpeg, audio and the essential asset
  libraries. The disk image's stored window is read back on the writable and
  again on the compressed image.
* **Windows**: ``windows_sign_staging.cmake`` runs inside a real ``cpack`` with
  a stand-in signer: the packed files must be the signed ones, and a failing
  signer or a staging folder without the executable must stop cpack with no
  package. ``sign_windows.ps1 -DryRun`` lists the plan and command lines; it
  plans ``codex.exe`` and never OpenAI's files. ``sign_windows.ps1`` and
  ``build_release_windows.ps1`` are also checked by contract
  (``test_native_sign_windows.py``), and ``windows_release_validate.ps1`` runs
  every MSI's agent runtime (``windows_agent_runtime_check.ps1``).
* **Installers**: ``publish_installers.py --dry-run`` runs every local check
  (names, Git LFS pointers, the disk image's signature, ticket and Gatekeeper
  verdict, the MSI's Authenticode stream) and prints the objects, headers and
  ``SHA256SUMS`` (with ``--add-to-version``, each ``.sha256``) without
  credentials or network. ``test_native_publish_installers``
  publishes, refuses and verifies against a loopback stand-in for R2 that checks
  every signature, and runs the Keychain wrapper with stand-ins for ``security``
  and ``python3``; the signer matches AWS's published Signature Version 4 examples.
  ``test_native_publish_add_installer`` adds each platform to a version published
  with the other, refuses another version than the checkout's, every overwrite
  and every state the verification rejects with no write sent, and cuts an
  addition off at each write; ``test_native_publish_add_verify`` checks both kinds
  of checksum file (a mismatch in either fails, a delta added later included) and
  that the custom domain is never asked for a file before it is published.
* **Updating itself**: the end-to-end update on both platforms
  (``updates_and_reports.rst``, "End-to-end runs"): on macOS
  ``update_e2e_macos.py`` from one signed build to a newer one (``--skip-notarize``
  is enough; the update checks the signature's team, not the ticket), with and
  without a delta; on Windows ``update_e2e_windows.py`` with two per-user MSIs
  on a clean Windows machine.
* **Update feed**: the rehearsal commands above, with a throwaway key and a
  copy of the changelog with its entry dated. ``add-key`` refuses a private key
  file given as ``--pem``; ``verify`` accepts the feed against the temporary
  key list and refuses it against the shipped one, and the add-on's
  ``update_feed.open_feed`` accepts it. A changed download link, a changed crash
  endpoint or one flipped signature bit is refused as ``bad_signature``,
  another key id as ``unknown_key``, a truncated file as ``malformed``, and a
  feed replayed after a later sequence as ``rollback``.
  ``test_native_sign_update_feed`` repeats this.
* **Source**: ``source_release.py archive --rev HEAD --format tar.gz`` builds
  the archive from the current commit with every Git LFS file from the local
  store, the library submodule commits and libraries in the manifest and the
  offer text filled in; ``shasum -c SHA256SUMS`` must pass.

Before the first public release
===============================

These need the release's own accounts or a published website, so no dry run
covers them:

* A real Developer ID signature, notarization and stapling; Gatekeeper on a
  second Mac after downloading the DMG; the signed agent runtime through
  ``codex_prompt_probe.py`` (V8 under the hardened runtime, `Entitlements`_).
* The Codex fork's source at a public address, recorded in
  ``agent_runtime.json`` and the runtime's ``README.txt``; ``codex.exe``
  Authenticode-signed with the KMS key.
* Authenticode on Windows with the KMS key; the MSI installed on a clean
  machine; SmartScreen.
* The website: ``/source`` and ``LINKS.source``; ``stable.json`` served with
  ``Cache-Control: no-cache`` or a ``max-age`` of at most the feed contract's
  300 seconds; ``RELEASE.downloads`` at ``https://downloads.angelo.studio/<version>``
  and the download buttons switched on only after ``publish_installers.py`` has
  verified the files there.
* That ``vendor.contact_email`` receives mail (``_pending`` in the identity),
  and a legal read of the written offer.
