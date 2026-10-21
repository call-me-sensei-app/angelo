.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Updates, crash reports and release notes
****************************************

The update check, installing updates, the "Angelo quit unexpectedly" dialog and
What's New are built, host-tested and run in the compiled app
(``test_blender_update_crash``; the end-to-end update in `Installing updates`_).
Two switches decide what is active:

* **The update-feed key.** ``update_keys.json`` lists the public keys a feed
  may be signed with; this source trusts key ``2026-10``. A build whose list
  has no active key fails closed: it makes no request at all and shows
  nothing, and Check for Updates… says update checks are not available in
  this build. The key is created in Cloud KMS by the key ceremony below and
  trusted with ``sign_update_feed.py add-key`` before the release build
  (``releasing.rst``); the release signers refuse a build without it.
* **The crash-report endpoint**, which only a signed feed announces. Until one
  does, the dialog shows Send report disabled, and offers Save report… and
  Report on the website. Sending becomes possible, without a new build, once a
  signed feed announces an endpoint that follows the contract below
  (``services.crash_reports``).

Where Angelo can replace itself (`Installing updates`_), it downloads a new
version in the background, verifies it against the signed feed, and offers
**Restart to Update**; nothing is installed until the person restarts, and never
while work is running. Anywhere else (an install for all users, a copy run from
its disk image, a release the feed marks for manual installation) it tells the
person and opens the download link when they click it, as before.

Module ownership
================

========================================  =====================================================================
Module                                    Responsibility
========================================  =====================================================================
``ed25519.py``                            RFC 8032 verification (and signing for tests) in plain Python.
``update_feed.py``                        The signed envelope, the payload rules, the offer. Fail closed.
``update_check.py``                       When to check, the request, the decision, Later and Skip.
``update_state.py``                       ``updates.json``: choices, schedule, the last good signed feed.
``app_http.py``                           One small HTTPS client (system proxy, bundled roots, limits).
``app_version.py``                        The product version and public links (application identity).
``release_notes.py``                      The bundled release notes and the What's New rule.
``crash_log.py``                          The crash handler's log, read by an allowlist grammar.
``crash_report.py``                       The report document, its exact text and bytes, pending crashes.
``crash_state.py``                        ``crash-reports.json``: consent and which crashes were handled.
``crash_upload.py``                       The endpoint and the one POST.
``crash_flow.py``                         One crash from the dialog's side: consent, send, save, outcome.
``card_kit.py``                           Notice card layout (top-down builder, controls, scroll box); the
                                          shared ``ui_kit`` components through its Builder (``ui_kit.rst``).
``card_draw.py``                          Drawing a card's plan; the four control states.
``card_host.py``                          One card at a time over the largest 3D view; its modal input.
``update_card.py``                        The update notice through its phases (download, ready, restart) and
                                          the generic notice card.
``update_download.py``                    The resumable, verified download of an update file.
``update_delta.py``                       Rebuilding the new app bundle from the installed one and a delta.
``update_stage_mac.py``                   macOS: can this app replace itself; unpack and verify the new bundle.
``update_stage_win.py``                   Windows: is this a per-user Windows Installer install; the MSI's signer.
``update_flow.py``                        Preparing an update in the background (delta, else full; ready.json).
``update_install.py``                     Handing over to the installer script; reading its result.
``update_helpers/``                       The installer scripts (``install_update_macos.sh``,
                                          ``install_update_windows.cmd``), copied out before they run.
``update_ready.py``                       The preparation worker, the card's live phase, retries, throttling.
``update_restart.py``                     Restart to Update: the work guard, the save prompt, the hand-over, quit;
                                          the next launch's result.
``work_guard.py``                         What a restart would interrupt (agent turns, previews, Flow runs,
                                          generations, background jobs, renders, a tool in use).
``crash_card.py``                         The "Angelo quit unexpectedly" dialog.
``whatsnew_card.py``                      What's New.
``update_ui.py``                          The worker, schedule, notices, top-bar and status-bar item, menus.
``crash_ui.py``                           Finding a crash, consent handling, the automatic send, Save.
``updates_prefs.py``                      Preferences > System > Updates and Reports.
``app_notices.py``                        Registration, readiness and the startup order.
``font_watch.py``                         Rebinds Angelo's fonts after Blender reloads its own.
``scripts/angelo/changelog.py``           CHANGELOG.md's parser and exporter.
``scripts/angelo/sign_update_feed.py``    Trusts a key (``add-key``), builds, signs (Cloud KMS or a local
                                          rehearsal key) and verifies feeds.
``scripts/angelo/make_update_delta.py``   Writes and checks a delta between two published macOS apps.
========================================  =====================================================================

The bpy-free modules are host-tested directly. ``app_notices.register`` is
called from the add-on's ``__init__.py`` (``ui.py`` is pinned at its size by
``test_native_module_size``). Nothing here runs in a background session, and
nothing shows in an automation run: ``ANGELO_DISABLE_INTRO`` (set by the native
suite runner) or ``ANGELO_DISABLE_DEFAULT_CHAT`` (set by the live harness)
turns all three off; ``ANGELO_ENABLE_APP_NOTICES=1`` is for the one suite that
tests them. The same card host shows the one-time chat hint (``chat_hint``,
priority below every notice here), which follows the same automation rule;
``ANGELO_ENABLE_CHAT_HINTS=1`` is for its suite (``agent_chat.rst``, "Show and
hide").

Startup order
=============

Four seconds after launch, once the window has settled (no layout change, no
welcome screen pending or open, no interaction in progress), ``app_notices``:

1. scans for a crash on a worker thread and shows the dialog for it;
2. shows What's New after an update (never on a first launch, which has the
   welcome screen, nor for a version already shown);
3. lets the update notice found by the network check through.

One card is visible at a time, by priority: crash dialog 100, What's New 50,
result notices 30, update notice 10. The crash dialog and What's New are modal
over the 3D view (the chat stays usable); the update notice and the result
notices sit in the 3D view's bottom-right corner and take only presses on
themselves. A closed card's operator is woken by a timer so it ends at once
(the timer is removed within half a second in any case). Timers, window events,
Blender's motionless hover refresh and the quit key always pass a card
(``input_policy.overlay_passes``, ``refresh_move``), so it never starves another
operator and never holds a quit requested before it opened (Blender's quit waits
for that refresh, behind every modal handler added after the request). Opening a file drops
every modal operator, so a visible card is closed as lost first; the crash
dialog and What's New come back once the file is open.

The update check
================

Schedule
--------

The first check runs 20 seconds after launch, on its own thread, unless one
succeeded within the last hour or was tried in the last 5 minutes. While
Angelo stays open it runs about every 24 hours (plus or minus 10 percent). A
failed try is retried after 1, 2, 4, 8 and then 24 hours. Check for Updates…
always runs.

Switches, strongest first: the machine policy ``DisableUpdateCheck`` (no
request, not even a manual one), an unusable ``UpdateFeedURL`` (the same), an
explicit ``--offline-mode`` (Blender's "Allow Online Access" preference is not
read: it is off by default and none of Angelo's network features depend on
it), and Preferences > System > Updates and Reports > Check Automatically
(manual checks still work). Without a trusted key no request is made.

The request
-----------

One ``GET`` of the feed with ``Accept: application/json``, ``If-None-Match``
(the last ETag, when a feed is stored) and ``User-Agent: Angelo/<version>
(<macOS|Windows|Linux>; <CPU>)``. Nothing else: no identifier, account, scene
or cookie. ``app_http`` follows the system proxy (environment, macOS network
settings, Windows registry; no PAC or proxy authentication beyond basic),
trusts the bundled roots plus the policy's ``ExtraCACertificates``, speaks only
https (http only to this computer, for tests), follows at most 5 redirects
without a downgrade, and reads at most 256 KiB within twice the 15-second
timeout.

What the person sees
--------------------

* **The update card** (corner card), in the phase the update is in
  (``update_ready.phase``):

  * *ready*: "Angelo 1.1 is ready", "Restart Angelo to finish updating. Your
    file opens again afterwards.", up to three highlights, Release notes,
    **Restart to Update**, Skip this version and Later. While a restart would
    stop work (`Never during work`_) the button is disabled and a line says
    why. An automatic check shows nothing until the update is ready.
  * *downloading* / *preparing*: a progress bar ("206 MB of 412 MB"), then a
    spinner while the new version is unpacked and verified, with Release notes
    and Hide. Shown only when the person asked (Check for Updates…, the
    status-bar item).
  * *notify*: where Angelo can't install the update itself, "Angelo 1.1 is
    available", "You have Angelo 1.0.", the highlights, Release notes,
    Download, Skip this version and Later, and one line saying why (moved
    out of the Applications folder, installed by an administrator or for all
    users, or the update could not be prepared). Download opens the
    platform's installer link in the browser and counts as Later.
  * *restarting*: a spinner until Angelo quits; it cannot be dismissed.

  Later hides the card for 3 days (1 day for a critical release). Skip is not
  offered for a critical release or for a version older than
  ``minimum_supported`` ("Angelo 1.0 is no longer supported. Please update.").
  A release with no download for this platform makes Release notes the
  primary button.
* **"Restart to Update"** (once the update is ready; before that, "Angelo 1.1
  available") beside the title in Director's top bar and beside the version in
  Manual mode's status bar, until the version is skipped or installed. Both are
  global areas, redrawn through the message bus (``Window.view_layer``, the
  workspace's ``tools``).
* **Check for Updates…** and **What's New** in Help and, because Director
  hides Help, in Director's mode menu. A manual check always answers: up to
  date, could not check (with Try again), not available in this build, or
  turned off by the organization or by offline mode.
* **Preferences > System > Updates and Reports**: Check Automatically (which also
  governs the background download), the last check or the update's state
  ("Downloading Angelo 1.1: 45%", "Angelo 1.1 is ready."), Check Now, Restart to
  Update when ready, What's New, the crash-report choice, View Last Report Sent.

The signed feed
===============

Location
--------

``https://angelo.studio/updates/v1/stable.json``, served by the website. The
machine policy ``UpdateFeedURL`` replaces it with an organization's https
mirror of the same signed file; ``ANGELO_UPDATE_FEED_URL`` does so for
development and tests (the policy wins). The signature is required whichever
address served the file, and the links inside are still checked.

Envelope
--------

At most 64 KiB::

    {"format": "angelo-update-feed", "version": 1, "key_id": "2026-10",
     "payload": "<base64 of the payload's exact UTF-8 bytes>",
     "signature": "<base64 of the Ed25519 signature of 'angelo-update-feed-v1\n' + payload bytes>"}

The signature covers bytes, not a re-serialized object, so there is no
canonical form to get wrong. ``key_id`` names an entry of ``update_keys.json``;
a revoked entry is never trusted.

Payload
-------

At most 32 KiB of JSON::

    {"schema": 1, "channel": "stable", "sequence": 2,
     "issued": "2026-10-25T09:00:00Z", "expires": "2027-04-23T09:00:00Z",
     "minimum_supported": "1.0.0",
     "latest": {"version": "1.0.1", "released": "2026-10-24", "title": "Angelo 1.0.1",
                "summary": "...", "highlights": ["...", "..."],
                "notes_url": "https://angelo.studio/releases#1.0.1", "critical": false,
                "downloads": {"macos-arm64": {"url": "https://downloads.angelo.studio/1.0.1/Angelo-1.0.1-macos-arm64.dmg",
                                              "sha256": "<64 hex>", "size": 412000000,
                                              "deltas": [{"from": "1.0.0", "format": "angelo-bundle-delta-1",
                                                          "url": "https://downloads.angelo.studio/1.0.1/Angelo-1.0.0-to-1.0.1-macos-arm64.delta.tar.xz",
                                                          "sha256": "<64 hex>", "size": 46856608}]},
                              "windows-x64": {"url": "...", "sha256": "...", "size": 301000000}}},
     "services": {"crash_reports": "https://console.angelo.studio/api/v1/crash-reports"}}

Rules, all fail closed (any violation refuses the whole feed and keeps the
stored one): an unknown key or a bad signature; a ``sequence`` lower than the
highest one ever accepted (rollback); a past ``expires``; any text longer than
its limit (title 80, summary 300, each of at most 5 highlights 140) or holding
control or bidirectional characters; versions that are not
``MAJOR.MINOR.PATCH``; a platform other than ``macos-arm64``, ``macos-x64``,
``windows-x64``, ``windows-arm64``, ``linux-x64``; a size outside 1 byte to
8 GiB; a link (notes or download) that is not https on ``angelo.studio`` or
one of its subdomains, or on ``github.com`` itself (not its subdomains); a
crash endpoint that is not https on ``angelo.studio`` or one of its subdomains.
``services`` is optional: without ``crash_reports`` there is nowhere to send.

Two optional fields are ignored by a build that predates them (it reads only
the keys it knows inside a download):

* ``deltas`` in a platform's download: at most 8 packages, each from an older
  ``MAJOR.MINOR.PATCH`` listed once, with its own https link, SHA-256, size and
  ``format`` (lower-case letters, digits, ``.`` and ``-``). A build uses the one
  whose ``from`` is its own version and whose format it knows
  (``angelo-bundle-delta-1``), and ignores the rest.
* ``latest.automatic_install`` (default true): false makes every build offer the
  release as a download instead of installing it itself. A later feed can
  change it without a new build.

Angelo installs by itself only files on ``downloads.angelo.studio``
(``update_feed.INSTALL_HOSTS``), the host the release tools publish to; a feed
may still link the download page to another allowed host, which is then only
opened in the browser.
The last good envelope is stored and verified again whenever it is read, so an
offer survives a restart while offline.

Publishing a release
--------------------

From the repository root, on the release machine:

1. Date the release in ``CHANGELOG.md`` (``## 1.0.1 — <YYYY-MM-DD>``), run
   ``python3 scripts/angelo/changelog.py export`` and ship the build.
2. Build the payload; title, summary and highlights come from the changelog,
   sizes and SHA-256 from the installers, and each ``platform=FILE`` download
   is that installer's published address,
   ``https://downloads.angelo.studio/<version>/<file name>``
   (``releasing.rst``, "Publishing the installers")::

       python3 scripts/angelo/sign_update_feed.py build --version 1.0.1 --sequence 2 \
         --download macos-arm64=dist/Angelo-1.0.1-macos-arm64.dmg \
         --download windows-x64=dist/Angelo-1.0.1-windows-x64.msi \
         --delta macos-arm64=1.0.0=dist/deltas/Angelo-1.0.0-to-1.0.1-macos-arm64.delta.tar.xz \
         --previous published-stable.json --out payload.json

   ``--critical``, ``--minimum-supported``, ``--crash-reports URL`` and
   ``--manual-install`` (``automatic_install: false``) set the matching
   fields; ``--previous`` refuses a sequence that does not grow. Each
   ``--delta`` is a file ``make_update_delta.py`` wrote (`Deltas`_), named for
   its two versions and published beside the installers.
3. Sign in Cloud KMS; the signature is verified with the key's public key
   before anything is written::

       python3 scripts/angelo/sign_update_feed.py sign payload.json --key-id <key-id> \
         --kms "$ANGELO_UPDATE_FEED_KMS_KEY" --out stable.json

   ``ANGELO_UPDATE_FEED_KMS_KEY`` holds the key version's full resource name,
   ``projects/<project>/locations/<location>/keyRings/<keyring>/cryptoKeys/<key>/cryptoKeyVersions/<n>``;
   the tool has no default for it.

4. Check it as the app will: ``python3 scripts/angelo/sign_update_feed.py
   verify stable.json``. Publish the installers first
   (``publish_installers.sh``, which verifies the public download), then
   ``stable.json``.

Key ceremony
------------

The private key is an ``EC_SIGN_ED25519`` key in Google Cloud KMS, in the
release's own Google Cloud project; it never leaves KMS. Once, by a project
owner (``<project>``, ``<location>`` and ``<keyring>`` are the release's own):

1. Create the key ring and key (choose ``hsm`` where Cloud KMS offers
   Ed25519 at that protection level, otherwise ``software``, and record which)::

       gcloud kms keyrings create <keyring> --location <location> --project <project>
       gcloud kms keys create update-feed --keyring <keyring> --location <location> \
         --project <project> --purpose asymmetric-signing \
         --default-algorithm ec-sign-ed25519 --protection-level hsm

2. Grant ``roles/cloudkms.signerVerifier`` on that key only to the people who
   publish releases; keep ``roles/cloudkms.admin`` with project owners; turn on
   Data Access audit logs for Cloud KMS so every signature is logged.
3. Export the public key and add it to
   ``scripts/addons_core/angelo_agent/update_keys.json`` in a reviewed change::

       gcloud kms keys versions get-public-key 1 --key update-feed --keyring <keyring> \
         --location <location> --project <project> --output-file update-feed-public.pem
       python3 scripts/angelo/sign_update_feed.py add-key --key-id <key-id> --pem update-feed-public.pem

   ``add-key`` accepts only an Ed25519 public key, keeps the rest of the file,
   changes nothing when the entry is already there, and refuses an id that is
   already used (or revoked) and a key listed under another id; ``--kms <key
   version>`` reads the key through ``gcloud`` instead, and ``public-key``
   prints the entry without writing it. Builds made after this change trust
   the key; earlier builds never will.
4. Rehearse with a local key (``sign_update_feed.py keygen --out
   rehearsal.pem --public-out rehearsal-public.pem``, then ``add-key --keys``
   on a copy of the key list, ``build``, ``sign --key-file`` and ``verify
   --keys``) before the first real signature; ``releasing.rst`` has the
   commands.

Rotation: create a new key version, ship its public key next to the old one,
and sign with it once no supported version lacks it; then mark the old entry
``"status": "revoked"`` (never delete it) and disable the old version in KMS.
If a key is ever exposed: disable its KMS version at once, publish a feed signed
by the next key, and ship a release that marks the exposed entry revoked.

What a stolen feed key alone can do: point people at https links on Angelo's own
hosts and, since Angelo installs updates, name any file on
``downloads.angelo.studio`` (the only host it installs from) by its SHA-256. It
cannot make Angelo install a file published anywhere else. On macOS the new app
must also carry a valid signature of the running app's Apple developer team, so
the key alone installs nothing that Angelo's team did not sign. On Windows a
signed install takes only an MSI signed by the same publisher; a build without
an Authenticode signature has the feed's signature and the downloads bucket's
write access as its only guards.
Keep the bucket's token as restricted as the KMS key.

Installing updates
==================

When Angelo installs an update itself
-------------------------------------

All of these must hold (``update_ready.automatic``), otherwise the update card
is the *notify* phase with its reason:

* the feed allows it (``latest.automatic_install``) and the platform's file is
  on ``downloads.angelo.studio``;
* the install can replace itself:

  * **macOS**: the running ``Angelo.app`` is a real bundle, not started from its
    disk image or a quarantined download location (App Translocation's random
    read-only path), not on a read-only volume, and the person can write both
    the bundle and its folder (``update_stage_mac.support``). An app an
    administrator put in ``/Applications`` for another account can't.
  * **Windows**: Windows Installer installed this folder **for this user**
    (``update_stage_win.support`` asks ``msi.dll`` for the products with Angelo's
    upgrade code, their context and their recorded ``InstallLocation``). An
    install for all users (``ALLUSERS=1``) is updated by its administrator; a
    copied folder or a development build is not Windows Installer's.
  * Linux: not yet.

* the version has not failed to install twice already
  (``update_state.install_failures``).

Angelo downloads on its own only where automatic checks may run (no
``DisableUpdateCheck`` policy, not in offline mode, Check Automatically on);
Check for Updates… downloads too when the person asks. A release the feed later
marks for manual installation, or a policy that turns checks off, makes a
prepared update a download again: it is never installed.

Downloading
-----------

After a check finds a newer version, one worker thread
(``update_ready``, ``update_flow.Preparer``) downloads into
``<cache>/updates/<version>/`` (``~/Library/Caches/Angelo`` on macOS,
``%LOCALAPPDATA%\Angelo\Cache`` on Windows; ``bpy.app.cachedir``):

* through the same proxy and trust rules as the check (``app_http.stream``);
* into ``<file>.part``, moved into place only when its size and SHA-256 are the
  feed's (``update_download``); more bytes than announced, or another digest,
  delete the partial file;
* resumable: an interrupted download continues with ``Range`` and ``If-Range``
  (the first answer's strong ETag or Last-Modified), so a file that changed on
  the server starts over instead of being spliced; a ``206`` for other bytes is
  refused;
* only with enough free space for the download and its unpacking (4 times the
  disk image on macOS, 3 times the MSI on Windows);
* at most 2 MB/s while an agent turn runs, so the agent keeps the connection;
* a failure for a passing reason (offline, a busy server, a cut connection) is
  tried again after 15 minutes, 1 hour, then 4 hours, keeping the partial file;
  the person sees the *notify* card after the third, or at once for a failure
  that would repeat (a wrong digest, a refused signature).

Nothing is shown while an automatic check downloads; the card appears when the
update is ready.

Preparing
---------

**macOS.** With a delta from the running version (`Deltas`_), the new bundle is
rebuilt from the installed one; on any problem with the delta, the release's
own disk image is downloaded instead. From the disk image: ``hdiutil attach
-nobrowse -readonly -noautoopen`` on a mount point inside the update's folder,
``ditto`` of ``Angelo.app``, ``hdiutil detach`` (forced if needed). Either way
the new bundle is staged as ``<version>/staged/Angelo.app`` and accepted only
when:

* its ``CFBundleIdentifier`` is the running app's and its
  ``CFBundleShortVersionString`` is the feed's version;
* ``codesign --verify --deep --strict -R=<requirement>`` passes, where the
  requirement is ``identifier "<bundle id>"`` and, when the running app is
  signed by an Apple developer team, ``anchor apple generic and certificate
  leaf[subject.OU] = "<team>"``: an update is signed by the same team;
* when Gatekeeper approves the running app (``spctl --assess --type execute``:
  a notarized release), it approves the new bundle too. Angelo never installs
  an app macOS would call damaged or refuse to open. A build that is not
  notarized (development and test builds) is held to the signature check
  alone. ``spctl`` is part of macOS; ``stapler`` is not (it comes with Xcode's
  tools), so the release pipeline is what refuses a disk image whose app is not
  stapled (``publish_installers.py``).

The downloaded file is deleted once the bundle is staged. Long tools run at
background priority (``taskpolicy -b``).

**Windows.** The MSI itself is what is installed; its SHA-256 is the feed's.
When the running ``Angelo.exe`` has a valid Authenticode signature, the MSI must
have a valid one with the same subject and issuer (PowerShell's
``Get-AuthenticodeSignature``); an unsigned build relies on the feed's digest.

``ready.json`` records the prepared update and a fingerprint: the staged
bundle's layout (names, types, sizes, modes and times) on macOS, the MSI's
SHA-256 on Windows. A ready update survives a restart and is checked again,
cheaply, at the next launch and right before it is installed; a changed stage
(a cache cleaner, a full disk) is prepared again rather than installed. A newer
release replaces the folder of an older one (and its pending retries); a version
that is not newer than the running one is never prepared, whatever a stored
feed says. A prepared update belongs to the install that prepared it: another
copy of Angelo sharing the cache folder does not take it.

Never during work
-----------------

**Restart to Update** quits Angelo, so it is disabled while a restart would stop
something (``work_guard.reasons``), and the card says which:

=================  ==================================================================  ====================
Reason             What runs                                                           Owner
=================  ==================================================================  ====================
``agent``          a chat turn, a tool call or an External CLI read, in any chat       ``Controller.busy``,
                                                                                       ``pending``
``preview``        a staged preview waiting for Accept or Discard                      the controller's
                                                                                       preview cards
``flow``           a Generation Flow run                                               ``flow_host``
``generation``     a Generative Studio job                                             ``studio._jobs``
``job``            a background render, bake or export                                 ``job_manager``
``render``         a render, preview render, bake, composite or export Blender runs    ``bpy.app``,
                                                                                       ``_bpy._angelo_jobs``
``interaction``    a modal tool or drag in a window (not the update card itself)       ``input_policy``
``unknown``        an owner could not be asked: never read as idle                     (fail closed)
=================  ==================================================================  ====================

The reasons are read once a second on the main thread and cached for drawing.
They are read again when the button is pressed and once more right before the
installer starts. Another copy of the same app still running (its files would
be replaced under it) stops the restart with a notice.

Restart to Update
-----------------

``update_restart.begin``, on the main thread:

1. the work guard and the other-copy check (above);
2. the cheap check of the prepared update (``Preparer.still_valid``);
3. unsaved changes: **"Save changes before restarting?"**, an Angelo card over
   the 3D view naming the file and the images with unsaved pixels, with Save and
   Restart, Don't Save and Cancel. Save saves the images first
   (``image.save_all_modified``, which packs a generated or packed image into
   the file), then the file (``wm.save_mainfile``), as Blender's own
   quit-after-save does; an image that stays unsaved, or a save that fails,
   stops the restart with a notice. The save's
   own answer decides, as in Blender's quit-after-save: ``bpy.data.is_dirty``
   reads True until Blender handles the save's notifier, after the call. An untitled file opens the File
   Browser; the restart continues after ``save_post`` and stops on
   ``save_post_fail`` or when the browser closes without saving. Cancel brings
   the update card back;
4. the installer script is copied out of the app into the update's folder and
   started detached (``update_install.start``); Angelo waits up to 15 seconds
   for ``helper.ready``, the script's first act, and then quits
   (``wm.quit_blender``, which asks nothing more: the file is saved, or the
   person chose not to). A script that never starts leaves Angelo running, says
   so, and offers the download.

The relaunched app opens the same file, and What's New shows the new
version's notes.

The installers
--------------

Both scripts take every input from ``ANGELO_UPDATE_*`` environment variables
(paths in any language arrive intact), run one at a time (``helper.lock``),
wait for Angelo's process to end, verify the update once more, and write
``result.txt`` (``status`` installed, failed or aborted, the step, the
version and, on Windows, Windows Installer's exit code) before they relaunch.

* **macOS** (``install_update_macos.sh``, ``/bin/sh``, absolute tool paths):
  waits up to ten minutes for the process to end (an exited process its parent
  has not reaped counts as ended); stops if another copy runs from the bundle;
  copies the new bundle beside the old one first when it was staged on another
  volume; then two renames: the installed bundle into the update's folder
  (``previous.app``, or ``.Angelo.app.previous-<n>`` beside it on another
  volume), the new one into its place. Right before, the new bundle must again
  pass ``codesign --verify --deep --strict`` against the running app's
  requirement and carry the feed's version (about 3.5 seconds for 1.26 GB), so
  a stage changed after Angelo verified it is never installed, and Gatekeeper
  must approve it when it approved the running app. A failed second rename puts
  the old one back. In place, the new bundle passes the same checks once more
  (anything wrong puts the previous app back). Nothing macOS would call damaged
  is registered or opened: the script registers a bundle with Launch Services,
  and opens one, only right after ``codesign --verify --deep --strict`` (and
  ``spctl`` when required) passed on it, the previous app after a failure
  included. It touches the bundle and registers it, unregisters the previous
  app it moved aside (Launch Services follows a moved bundle, and it must not
  answer for Angelo while it waits to be deleted), then
  ``open -n`` the app with ``--env`` for the variables that chose how Angelo was
  started (``ANGELO_*``, ``BLENDER_*``, ``CFFIXED_USER_HOME``; not the values
  Angelo sets for itself, and never a variable named like a secret, with a
  ``KEY``, ``TOKEN``, ``SECRET``, ``PASSWORD``, ``AUTH`` or similar part:
  ``open``'s command line is visible to every local user) and ``--args <file>``.
* **Windows** (``install_update_windows.cmd``, run hidden by ``cmd /d /c``):
  waits for the process by number and image name (``tasklist``), stops if this
  user still runs an ``Angelo.exe``, checks the MSI's SHA-256 against the feed's
  again (``certutil``), then ``msiexec /i <msi> /qb!- /norestart ALLUSERS=2
  MSIINSTALLPERUSER=1 MSIRESTARTMANAGERCONTROL=Disable REBOOT=ReallySuppress
  INSTALL_ROOT="<install folder>" /l*v install.log`` (Windows Installer's own
  progress bar, no Cancel), waiting for another installation in progress (exit
  code 1618) up to ten minutes. If Angelo has not quit after ten minutes, either
  script stops without relaunching anything (Angelo itself stops an installer
  that did not report within 15 seconds). Windows Installer replaces the installation as
  one transaction, so a failed or interrupted install leaves the previous
  version. It clears its own variables and starts ``Angelo-launcher.exe`` with
  the file, the new version or, after a failure, the old one, in the
  launcher's own folder as its shortcut does: Windows keeps a process's current
  folder from being deleted, and the new version deletes the update's folder.

The next launch
---------------

``update_restart.handle_results`` reads every ``result.txt`` once:

* ``installed`` for the running version: the previous bundle and the update's
  folder are removed (only a folder the installer named, never the installed
  app), and the failure count is reset;
* ``failed`` for a newer version: "Angelo 1.1 could not be installed" (nothing
  was changed; it will try again, or download it), counted in
  ``updates.json``. After two failures in a row that version is offered as a
  download instead;
* ``aborted`` (another copy was running, Angelo did not quit): nothing to tell;
  the prepared update stays.

Folders of newer versions (a ready update, a partial download) are kept; older
ones are removed.

Deltas
------

A delta (``angelo-bundle-delta-1``, ``update_delta.py``) is a ``.tar.xz`` whose
first member, ``delta.json``, describes every entry of the new bundle (folders,
files with size, mode and SHA-256, symbolic links) and where each file's bytes
come from: ``same`` (the installed file at the same path), ``copy`` (an
installed file elsewhere), ``blob`` (new content, stored once by digest) or
``patch`` (the installed file with block records written over it: a re-signed
library differs from its old copy only in its signature). The new bundle is
built in a new folder from APFS clones of the installed files, compared entry by
entry with the description, then verified like a full download. A changed
installed app, a missing base file, a path with ``..`` or through a symbolic
link, an unexpected member or the wrong pair of versions fails the delta, and
the disk image is downloaded instead.

``scripts/angelo/make_update_delta.py`` writes one on the Mac that signed the
release, from the two published disk images (or apps), and applies it to a
copy of the old app before it is written, so a delta that does not rebuild the
new app exactly is never published::

   python3 scripts/angelo/make_update_delta.py --platform macos-arm64 \
     --from-version 1.0.0 --from dist/1.0.0/Angelo-1.0.0-macos-arm64.dmg \
     --to-version 1.0.1 --to dist/macos/Angelo-1.0.1-macos-arm64.dmg --out dist/deltas

Measured between two release-signed macOS builds two release candidates apart
(1.26 GB installed, 9,257 files): 46.9 MB against the 416.8 MB disk image
(11%); 5,707 files unchanged, 2 moved, 99 patched (5.1 MB of new blocks), 248 new or rewritten
(213 MB before compression, almost all of it the 192 MB main executable, which
any C++ change rewrites). Writing it took 2 minutes (xz preset 9); rebuilding
and checking the bundle 17 seconds, both on a loaded M-series Mac at low
priority. A binary diff of the executable would shrink it further; that is not
done yet. Windows has no deltas: Windows Installer owns the files.

Windows installs per user
-------------------------

The MSI is one package for both install contexts (``WIX.template.in``:
``ALLUSERS=2``, ``MSIINSTALLPERUSER=1``, ``InstallPrivileges="limited"``):

* **By default, for the person installing it**: ``%LOCALAPPDATA%\Programs\Angelo``,
  shortcuts and file types for that user (``Angelo.exe --register``, run as
  them), no administrator prompt for the install or any update.
* **For every user**: ``msiexec /i Angelo-<version>-windows-x64.msi ALLUSERS=1``
  from an elevated prompt or a deployment tool; Program Files, registration for
  all users, updates installed by the administrator (Angelo only tells).

CPack records the folder (``ARPINSTALLLOCATION``), which is how Angelo
recognises its own install. An Angelo installed for all users by an earlier build is not upgraded
by a per-user install (Windows Installer keeps the contexts apart): uninstall it
first. Angelo's own validation scripts install with ``ALLUSERS=1``, so they keep
checking the Program Files install.

End-to-end runs
---------------

**macOS** (``tests/python/angelo_agent/update_e2e_macos.py``): ``newer`` makes a
copy of a built app with another version and a What's New entry; ``sign`` signs
it with the release signer; ``run`` installs the older signed app (refusing
to open an old app or an update that fails ``codesign --verify --deep
--strict``, so an unsigned ``newer`` copy is never launched), serves a feed
signed with a throwaway key announcing the newer disk image (and a delta,
``--delta``), and starts the old app through Launch Services with an isolated
profile and ``update_e2e_driver.py``. The driver saves a file, leaves an
unsaved change, waits for the ready card, and presses Restart to Update and
Save and Restart with real window events. The run then requires: the old app
quit, the installer reported ``installed``, the new version started from the
same place, the installed bundle is the new version and passes ``codesign
--verify --deep --strict``, What's New was shown, the saved file was reopened,
and the update's folder was cleaned up. At the end it unregisters its copy
from Launch Services, so nothing that resolves Angelo by its identifier lands
on a test copy. ``<work>/qa/update-e2e.json`` holds every check, the
installer's result and log, and the driver's screenshots.

**Windows** (``tests/python/angelo_agent/update_e2e_windows.py``): the same
sequence with two per-user MSIs on a clean Windows machine, in the signed-in
desktop session (``releasing.rst``, "Rehearsals").

Crash reports
=============

Finding a crash
---------------

The crash handler writes ``angelo.crash.txt`` (or ``<file>.crash.txt``) in the
temporary folder. At the next launch a worker looks there (the parent of the
session's own temporary folder) for ``*.crash.txt`` files no larger than
512 KiB and no older than 14 days, and keeps only logs whose frames name
Angelo's executable, because a stock Blender may share the folder. The newest
unhandled one is offered; older ones are recorded as superseded. The log
itself is never deleted, moved or uploaded.

**Symbols.** The Windows installer carries no ``blender.pdb``: a PDB, even
stripped, records every source path of the build machine (releasing.rst, "No
builder paths"). A Windows log therefore names its frames by module and offset;
the release keeps each build's ``blender_public.pdb`` and ``blender_private.pdb``,
with the build hash the report carries, and reports are symbolicated on our side.
macOS frames keep their function names (the executable's own symbol table).

What a report holds
-------------------

**The stack and the environment, nothing else.** ``crash_log`` reads the log
with an allowlist grammar: only the header line and the lines after the last
``# backtrace`` marker are read, a line is kept only when it matches a stack
frame, Python frame, Windows exception, module or thread grammar, and it is
rebuilt field by field; everything else is dropped and counted
(``omitted_log_lines``). Paths become a file name, or for Python files a path
inside the installation (``scripts/…``); any other file is ``<external>``.
The document (``schema: angelo.crash-report.v1``, at most 64 KiB, the stacks
halved until it fits) has exactly these top-level fields: ``schema``,
``report_id`` (a random UUID), ``created``, ``crashed_at`` (rounded to the
minute), ``app`` (name, version, Blender base, build hash and date),
``crashed_build`` (the crashed build's Blender version, commit date and hash,
and whether it is this build), ``system`` (OS and version, CPU architecture,
GPU vendor, renderer, driver version and backend), ``crash`` (exception,
native stack, other threads, Python stack, loaded modules) and
``omitted_log_lines``. It never holds a scene, prompt, chat, file name or path
outside the installation, account, key, or an identifier of the person or
computer. The dialog shows ``crash_report.render(document)``; the upload sends
``crash_report.body(document)``, the same text encoded, and Save report…
writes the same text. Tests plant canaries (names, paths, a prompt, keys) in
macOS, Linux and Windows logs and check none survives.

The person's choice
-------------------

``crash-reports.json`` holds the consent, ``ask`` by default, and which crash
logs were handled (by name, modification time and size; at most 50 entries).

* ``ask``: the dialog. Send report sends after the person presses it.
  "Remember my choice" applies to the button pressed (Send report makes
  ``always``, Don't send makes ``never``). Close, Escape, Save report… and
  Report on the website never change the consent. Return does nothing until a
  control was focused with Tab.
* ``always``: the report is sent without a dialog and a corner notice says so,
  with See what was sent (the review card, exactly what left) and Ask me next
  time. A send that fails is tried at the next launch, three times at most.
  While no endpoint exists, ``always`` shows the dialog instead.
* ``never``: nothing is shown or sent; the crash is recorded as declined.

The machine policy ``DisableCrashReporting`` shows and sends nothing and
records nothing, so a lifted policy can still ask.

The dialog
----------

"Angelo quit unexpectedly", in phases: ask, sending (a spinner), sent ("Thank
you", the report ID, Report on the website, Close), failed (Try again unless
the server refused the report or is limiting reports, Save report…, Close; a
send that failed offline or on a busy service and is then closed is offered
again at a later launch) and review. Show what will be sent
opens the exact text in a scroll box with Copy report. Without an endpoint the
text says sending is not available yet, Send report keeps its place disabled
(no hover, press or focus), and there is no Remember my choice.

**Report on the website** opens ``https://angelo.studio/support/report-a-bug``
with ``os``, ``gpu`` and ``version`` (``1.0.0 (<build hash>)``), the same
parameters as the Windows crash dialog, plus ``report`` (the report ID) after a
report was sent. The stack never goes into the link. **Save report…** opens
Blender's File Browser with ``Angelo-crash-report-<date>.json``.

Every control (buttons, the checkbox, the disclosure, the close button) has a
rest, hover (lighter), pressed (darker, label one pixel down), keyboard-focus
(a ring) and, where it applies, disabled look. Card text is laid out with the
chat's fonts and wraps; the card shrinks as a whole in a small 3D view.

The crash-report endpoint
-------------------------

A feed announces the endpoint with ``--crash-reports
https://console.angelo.studio/api/v1/crash-reports``; until one does, the app
has nowhere to send. The server side follows this contract:

* ``POST https://console.angelo.studio/api/v1/crash-reports``, anonymous: no
  cookie, ``Authorization`` or token is sent or read. Headers: ``Content-Type:
  application/json``, ``Accept: application/json``, ``User-Agent:
  Angelo/<version> (<OS>; <CPU>)``; no ``Content-Encoding``.
* Body: exactly ``crash_report.body(document)``, at most 65,536 bytes
  (``crash_report.MAX_BODY``; larger bodies get ``413``, which is never
  retried, so the client cuts its lists to fit first).
* The server validates the whole document strictly: an unknown or missing
  field at any level, another ``schema``, or a value outside the client's own
  grammars and lengths (``clean()`` text, frame fields, list caps) is ``400``.
  Every string the client builds, ``app.version`` included, goes through
  ``clean()``.
* Success: ``202`` with ``{"id": "<report_id>"}`` (the client also accepts 200
  and 201 and compares the id). ``report_id`` is the idempotency key: a retry of
  a stored report gets the same answer and stores nothing new.
* Errors: ``400``, ``413`` and ``415`` are final; ``429`` (with
  ``Retry-After`` in seconds) and ``5xx`` are retried at a later launch, not
  before ``Retry-After`` (kept up to a week, ``crash_state`` ``not_before``),
  three attempts per crash in all. A ``429`` in the dialog offers no Try again.
* Limits: 5 requests in 10 minutes per client network (per server instance),
  20 stored reports a day per network, 1,000 a day overall. The client sends at
  most one report per launch, only after consent.
* Storage: the validated report and the time received, kept 30 days; no
  address, account or session is stored with it (a keyed hash of the network,
  in a separate table that cannot be joined to reports, counts the daily caps
  for two days).

Release notes and What's New
============================

``CHANGELOG.md`` is the single source, written for people, and strictly
checked by ``scripts/angelo/changelog.py``: newest first, one entry per
``MAJOR.MINOR.PATCH`` (only the newest may be ``Unreleased``), the line
``Based on Blender X.Y.Z[ LTS].`` under each heading, a summary of at most 300
characters, 1 to 5 Highlights of at most 140 (any release; a small one lists only what changed), and the sections New, Improved,
Fixed, For IT administrators and Known issues, in that order, with plain-text
bullets (no markup, links or issue numbers). ``changelog.py export`` writes
``assets/release_notes.json``, which the app bundles and the website renders;
a host test fails when it is stale and when the newest entry's Blender is not
this source's ``BKE_blender_version.h``.

What's New shows the running version's notes once after an upgrade (the
remembered version is ``updates.json``'s ``last_seen_version``), never on a
first launch, a downgrade or a version without notes, and is always in Help
and Director's menu. The notes are Angelo's own text: each line (summary,
highlights, headings, items) goes through the interface translations when drawn,
and the extractor lists the bundled release's lines (``i18n/sources.py``), so a
catalogued line shows translated and any other stays English. The update
notice's highlights go through the same lookup; a newer release's lines are only
translated when they match a catalogue entry.

What the website must serve
===========================

* ``/updates/v1/stable.json``: the file ``sign_update_feed.py sign`` writes,
  byte for byte, as ``application/json`` with ``Cache-Control: no-cache`` (or
  ``max-age`` of 300 seconds at most), a strong ``ETag`` answering
  ``If-None-Match`` with ``304``, and an identity encoding when the request
  has no ``Accept-Encoding`` (Angelo sends none). No cookies, scripts or
  consent walls on that path. Until the first signed feed exists it may answer
  ``404``; builds without a key never ask.
* The installers the feed names are not on the website: they are at
  ``https://downloads.angelo.studio/<version>/Angelo-<version>-<platform>.<dmg|msi>``,
  with ``SHA256SUMS`` beside them (or, for a platform added to the version
  later, its own ``<installer>.sha256``), never changed once published (a public
  Cloudflare R2 bucket behind that domain; ``releasing.rst``, "Publishing the
  installers"). The feed accepts any ``angelo.studio`` subdomain or
  ``https://github.com/...`` release assets; no other host.
* ``/releases``: every entry of ``release_notes.json`` (exported from this
  repository with ``python3 scripts/angelo/changelog.py export --out
  <file>``), newest first, each in an element
  whose ``id`` is its version (``1.0.1``), showing the title, the date or "In
  preparation", "Based on Blender …", the summary, the highlights and the
  sections in order. The feed's ``notes_url`` is
  ``https://angelo.studio/releases#<version>``; What's New links to
  ``https://angelo.studio/releases``.
* ``/support/report-a-bug``: reads ``os``, ``gpu`` and ``version`` from the
  query, and ``report`` when present ("Crash report ID"). Its "If
  Angelo crashed" section says that Angelo offers, at the next launch, to save
  the report as ``Angelo-crash-report-<date>.json`` to attach to the email.

Validation
==========

Host (``run_host_tests.py``): ``test_native_ed25519``, ``test_native_update_feed``,
``test_native_update_check`` (schedule, switches, proxy, the policy's mirror),
``test_native_crash_report`` (the grammar and canaries on three platforms),
``test_native_release_notes``, ``test_native_sign_update_feed`` (with ``add-key``
and a rehearsal that trusts a throwaway key and refuses tampered and replayed
feeds),
``test_native_managed_policy``, ``test_native_notice_cards`` (layout, states,
every phase, translation of every drawn word), ``test_native_card_host``
(queue and input rules) and ``test_native_app_notices`` (the Blender glue on a
stand-in ``bpy``, drawing without file access, registration).

Installing updates, host: ``test_native_update_download`` (resume with
``If-Range``, a changed file, a corrupt, oversized, cut or stopped download,
disk space, offline, throttling, against a loopback server that answers
ranges), ``test_native_update_delta`` (round trip, block patches, moves,
modes and links, a changed or incomplete installed app, hostile paths, links
and members), ``test_native_update_stage`` (who can update itself on each
platform, the code requirement, unpacking, the fingerprint, the MSI's signer),
``test_native_update_flow`` (delta first then the full download, never a
downgrade, only the downloads host, a ready update across a restart),
``test_native_update_install`` (the hand-over and the macOS installer script
run for real: the wait, the swap, the rollback, another running copy),
``test_native_update_cards`` (every phase and the save prompt on every
region), ``test_native_update_ready`` (background preparation, throttling,
retries, the restart's refusals, the save prompt, the hand-over, the next
launch, the work guard) and ``test_native_update_release_tools`` (deltas and
``automatic_install`` through ``sign_update_feed.py`` and
``publish_installers.py``).

Window: ``run_native_suites.py --gui --include test_blender_update_crash``
drives the real default startup with real mouse and key events against a
loopback feed signed by a throwaway key, a loopback crash endpoint and
synthetic crash logs. It checks the dialog without an endpoint (disabled Send
report, Save report… through the File Browser, the website link), What's New,
the update notice beside a viewport that still orbits, Later, Skip, the
result notices, the policy, the dialog with an endpoint (focus, scrolling,
Remember my choice, the bytes received against the bytes shown), the
automatic send and its review, the Help menu, the Preferences panel, the light
theme and Manual mode's status bar, and samples the pixels of every control
state. Look at the screenshots it writes to ``qa/``.

Before the first release that checks for updates
================================================

* Publish the first ``stable.json``, signed with a key ``update_keys.json``
  trusts (``releasing.rst`` has the order and commands), with the installers on
  ``downloads.angelo.studio`` (the only host Angelo installs from).
* Run the end-to-end update on both platforms with the release's signed
  builds (`End-to-end runs`_).
* Serve the website paths above, with ``/updates/v1/stable.json`` at
  ``no-cache`` (or at most 300 seconds).
* Before announcing ``--crash-reports``, run the window suite's endpoint
  scenes once by hand against the real endpoint.

Limits
======

The scan, the dialog and the File Browser use the same code on Windows, but
the window suite runs on macOS; on Windows they are covered by host tests
only.

Installing updates:

* An update is installed only through Restart to Update; quitting Angelo does
  not install it. The ready card returns at each launch until the person
  restarts, chooses Later or skips the version.
* Windows shows Windows Installer's own progress bar for the minute the MSI
  takes, between Angelo quitting and reopening.
* A Mac app an administrator installed, or a Windows install for all users,
  gets the download link instead; Angelo does not ask for an administrator
  password.
* Deltas exist for macOS only and carry the main executable whole (no binary
  diff yet).
* An unsaved asset catalogue (Blender's own quit dialog checks those too) is not
  part of the save prompt; save it from the Asset Browser first.
* The work guard sees renders, bakes, composites and exports Blender reports,
  Angelo's own jobs and agent work; Dynamic Paint and Ocean bakes and sequencer
  proxy builds are not reported to it, and the restart stops them as a normal
  quit would.
* Windows Installer's exit code 3010 (a file in use, replaced at the next
  reboot, such as the thumbnail handler Explorer holds) counts as installed.
* Another copy of the app is recognised by the path it was started from; one
  started through a symbolic link or a relative path may not be.
