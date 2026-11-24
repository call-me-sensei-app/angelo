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
* **The Linux release key.** ``linux_release_keys.json`` lists the public keys
  a Linux release's file manifest may be signed with (`Linux release
  signing`_). Until the Cloud KMS ceremony adds one, a Linux build trusts no
  release, so it never installs an update by itself (the card offers the
  download), and a Linux release build fails its release-ready check.
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
``update_card_addons.py``                 The update card's add-on section (`Your add-ons and the update`_).
``addon_compat.py``                       Which of the person's add-ons are known to work on an update's Blender.
``addon_compat_live.py``                  Blender's side of it: the facts, the load record, repository listings.
``update_download.py``                    The resumable, verified download of an update file.
``update_delta.py``                       Rebuilding the new app bundle from the installed one and a delta.
``update_stage_mac.py``                   macOS: can this app replace itself; unpack and verify the new bundle.
``update_stage_win.py``                   Windows: is this a per-user Windows Installer install; the MSI's signer.
``update_stage_linux.py``                 Linux: is this a release folder as extracted; unpack and check the new
                                          release against its signed manifest.
``release_manifest.py``                   Linux: the signed file manifest of a release and the check that a
                                          folder is exactly it (``linux_release_keys.json``). Fail closed.
``update_flow.py``                        Preparing an update in the background (delta, else full; ready.json).
``update_hold.py``                        One worker or removal at a time in an update's folder, in this process
                                          and across processes.
``update_install.py``                     Handing over to the installer script; reading its result.
``update_helpers/``                       The installer scripts (``install_update_macos.sh``,
                                          ``install_update_linux.sh``, ``install_update_windows.cmd`` with
                                          ``other_copies_windows.ps1``), copied out before they run.
``update_ready.py``                       The preparation worker, the card's live phase, retries, throttling.
``update_restart.py``                     Restart to Update: the work guard, the save prompt, the hand-over, quit;
                                          the next launch's result.
``work_guard.py``                         What a restart would interrupt (agent turns, previews, Flow runs,
                                          generations, background jobs, Blender's jobs, a tool in use).
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
``scripts/angelo/sign_linux_release.py``  Linux: trusts a release key (``add-key``), writes the signed manifest,
                                          signs the download (minisign), verifies a folder or a tarball.
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
2. says once where a newer Angelo moved the chats (``moved_state_notice``,
   ``app_identity.rst``, "Going back to an earlier version");
3. shows What's New after an update (never on a first launch, which has the
   welcome screen, nor for a version already shown);
4. lets the update notice found by the network check through.

One card is visible at a time, by priority: crash dialog 100, the moved-chats
notice 60, What's New 50, result notices 30, update notice 10. The crash dialog
and What's New are modal over the 3D view (the chat stays usable); the update notice and the result
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

What the server keeps
---------------------

The app adds nothing for this, so every version is counted the same way.
``angelo.studio`` counts the requests for the feed, a 304 included, to learn
roughly how many copies of Angelo are in use each day and which versions and
systems they run. For each check from Angelo it logs the UTC day, the version,
OS family and CPU read from the ``User-Agent``, the answer (200 or 304) and a
keyed hash of the network address. The hash's key changes every UTC day, so one
day's hashes don't match another's. The address and the full ``User-Agent`` are
not logged; a request from anything other than Angelo is only added to a daily
total.

The numbers are estimates: a studio behind one address counts once, an address
that changes counts more than once, and a check that is turned off, or that goes
to a mirrored ``UpdateFeedURL``, is never seen. Anyone can imitate a check, so a
sudden jump needs a second look. Turning off Check Automatically stops the
automatic requests (a manual check is counted like any other);
``DisableUpdateCheck`` stops them all.

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

  In the *ready* and *notify* phases, and under the two options, the card also
  says which of the person's add-ons are not yet known to work on the version
  it installs (`Your add-ons and the update`_).

  Later hides the card for 3 days (1 day for a critical release). Skip is not
  offered for a critical release or for a version older than
  ``minimum_supported`` ("Angelo 1.0 is no longer supported. Please update.").
  A release with no download for this platform makes Release notes the
  primary button.

  The card always shows the latest check's offer. A card that a more important
  one displaced comes back afterwards; when a newer check finds a different
  offer, the newer card replaces it rather than waiting behind it, and an offer
  already on screen is not queued to show a second time
  (``update_ui.show_notice``).
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

Your add-ons and the update
---------------------------

Upgrading should not be a gamble on the add-ons a person relies on. So the update
card names the person's enabled add-ons (never those that ship with Angelo:
``angelo_safe_start.shipped_here``) that are **not yet known to work** on the
Blender version the update is built on (the feed's ``blender``; a release of the
running line without it keeps this line's Blender series), and says what add-on
safe start does if one of them stops Angelo as it starts (``app_identity.rst``,
"A start that did not finish"): "If one stops Angelo as it starts, the next start
opens without your add-ons and names the one that stopped it. Turn it back on in
Preferences > Add-ons once it has been updated." **Show add-ons** opens the list in
the card, one sentence per add-on saying why (at most six; the rest are counted).
When every one is known to work the card says so in one line; a person with no
add-ons of their own sees nothing.

"Known" rests only on real signals, never on a list of add-on names
(``addon_compat.judge``, first match wins):

1. **Failing now**: it failed as it loaded in this session (``angelo_safe_start.
   failed_now``), named with its cause.
2. **A newer version on the platform**: the repository it came from lists a newer
   version compatible with the new Blender. Said first, since updating it before
   Angelo is the fix (more pressing when the installed one's range excludes it).
3. **Outside its declared range**: an extension's ``blender_version_min``,
   ``blender_version_max`` (exclusive) or ``platforms``, which Blender itself
   enforces (it does not load an extension outside them); a legacy add-on's
   ``bl_info["blender"]`` minimum above the new version.
4. **Known to work**: it loads in this Angelo now and the update keeps the same
   Blender series (``MAJOR.MINOR``); or Angelo recorded it loading, at this add-on
   version, on the new version's series (``angelo_addon_loads``: each completed start
   writes ``<config>/angelo-start/addon-loads.json`` with module names and versions,
   never a path, read from the new version's own folder beside this one); or its
   author closed its range (``blender_version_max``) past the new version.
5. **Not yet known**: anything else. An open range ("4.2 and later") was written
   before the new version existed, so it is not taken as proof; only 13 of the 1,415
   add-ons extensions.blender.org listed when asked for Blender 5.4.0 on 2026-11-14 state a
   maximum.

The repository listing is Blender's own query (``platform``, ``blender_version``,
``python_version``; nothing about the person or their add-ons), asked once per
session per repository on a worker thread, only for an update to another Blender
series, only for a remote repository that needs no access token and only while
Blender's online access is allowed, at most 16 MB within a 20-second timeout
(``addon_compat_live``). Until it answers, or when it cannot, the verdicts stand
without it. The facts are read on the main thread and again only when the enabled
add-ons, this session's failures or a listing change; reading them costs Blender's
cached add-on scan and one small manifest read per extension. Host tests:
``test_native_addon_compat`` (the verdicts, the listing, the load record) and the
``AddonTests`` of ``test_native_update_cards``.

The signed feed
===============

Location
--------

``https://angelo.studio/updates/v1/stable.json``, served by the website. The
machine policy ``UpdateFeedURL`` replaces it with an organization's https
mirror of the same signed file; ``ANGELO_UPDATE_FEED_URL`` does so for
development and tests (the policy wins). The signature is required whichever
address served the file, and the links inside are still checked. With
``UpdateFeedURL`` set, Angelo also downloads the installers and deltas from the
mirror (``update_feed.install_source``): a file the feed names at
``https://downloads.angelo.studio/<path>`` comes from ``<path>`` beside the
mirrored ``stable.json``. For example, ``https://it.example/angelo/stable.json``
gives ``https://it.example/angelo/1.0.10/Angelo-1.0.10.dmg``. The feed's size and
SHA-256 and the installer's code signature are checked as for Angelo's own host.
A mirror therefore copies each file at the same path, so a network that
reaches only the mirror still updates. A mirror that holds only
``stable.json`` still works: when the mirror answers that it doesn't have a
file (403, 404 or 410), Angelo downloads it from the feed's own host, with the
same checks. Any other failure (an interrupted transfer, a network error)
keeps the partial download, which resumes from the mirror next time.

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

Three optional fields are ignored by a build that predates them (it reads only
the keys it knows inside a release and a download):

* ``blender`` in a release: the Blender version it is built on
  (``MAJOR.MINOR.PATCH``), which the add-on check judges against. The tool
  writes it from the ``Based on Blender X.Y.Z`` line of the release's
  ``CHANGELOG.md`` entry; any other form refuses the feed.

* ``deltas`` in a platform's download: at most 8 packages, each from an older
  ``MAJOR.MINOR.PATCH`` listed once, with its own https link, SHA-256, size and
  ``format`` (lower-case letters, digits, ``.`` and ``-``). A build uses the one
  whose ``from`` is its own version and whose format it knows
  (``angelo-bundle-delta-1``), and ignores the rest.
* ``latest.automatic_install`` (default true): false makes every build offer the
  release as a download instead of installing it itself. A later feed can
  change it without a new build.

Release lines
-------------

A release line is ``MAJOR.MINOR``. When a new line ships, people on the older one
are always shown both options together, the update of their own line and the
later line, and the older line keeps getting fixes.

Builds before 1.0.9 read only ``latest``: they never see an optional field, and
no value of ``automatic_install``, ``critical`` or ``minimum_supported`` can make
one release both a choice and a later patch's route. So ``latest`` stays the
newest release of the oldest line still updated, and every other line's newest
release goes in the optional top-level ``lines``, a list of at most 4 releases in
``latest``'s format, one per line, none on ``latest``'s line (a duplicate refuses
the whole feed; an entry this build can't read, or past the fourth, is left out, so
a later line's new field never stops a build's own updates;
``sign_update_feed.py`` refuses both)::

    {"latest": {"version": "1.0.10", ...},
     "lines": [{"version": "1.1.2", "recommended": false, ...}], ...}

A build from 1.0.9 on (``update_feed.offer_for``):

* takes a newer ``latest``, or the newest release of its own line, as an ordinary
  update, prepared in the background as always;
* shows the newest release of a newer line beside it in the same card
  (``Offer.later``). With nothing newer of its own, the card keeps both sides: the
  own side says "You're up to date" and the newer line is the offer
  (``Offer.choice``);
* downloads nothing of a newer line, not even on Check for Updates…, until the
  person presses **Update**. From then on Angelo prepares that side instead of the
  own update (``update_ready.target``, ``updates.json`` ``accepted_line``), which
  stays in the card and comes back if the newer line can't be prepared or the
  person changes their mind. Restart to Update still decides; the yes is spent once
  that line is running. A build that can't replace itself, or offline mode, offers
  Download instead. A ``critical`` flag on another line's release is ignored.
* picks, of several newer lines, the one the person said yes to, else the
  recommended one, else the newest. A newer line or a change of its recommendation
  shows the card at once, even while the own update waits quietly.
* **Stay on Angelo 1.0** (``skipped_line``) keeps that line and every older one out
  of the card through their later releases, stops anything prepared and removes it
  from disk, and leaves the update of the own line alone. Preferences keeps saying the newer
  version is available; a still newer line, a manual check or a new recommendation
  asks again. Later works as for any update. A version below
  ``minimum_supported`` is told it is unsupported, is not offered Stay, and still
  chooses what to install.

``recommended`` on a ``lines`` entry (default false) is the release manager's
switch for recommending the move to everyone on an older line, for example once a
new Blender base has proved itself. It lives only in the signed feed, so turning it
on or off is a new feed sequence, never a client release, and builds before 1.0.9
ignore it. On, the card leads with the newer line and says so; both options stay
visible and nothing moves without the person's Update, so it is independent of
``automatic_install``. A person who chose Stay before it was turned on is asked
once more::

    python3 scripts/angelo/sign_update_feed.py recommend --line 2.0 \
      --previous published-stable.json --out payload.json     # --off to take it back

then sign and verify as for any feed. It changes that flag, the sequence and the
dates, nothing else.

To end a line's fixes for good, publish the newer line's release as ``latest``
(``build --end-line``): every build then takes it as an ordinary update, the only
way to reach builds before 1.0.9.

Publish a new line with ``sign_update_feed.py build --as-line --previous
published-stable.json``: the published ``latest`` is kept byte for byte and the
new release joins ``lines``, keeping the line's ``recommended``. A build on the
latest line replaces ``latest`` and keeps the other lines. ``build`` always needs
``--previous`` (``--first-feed`` only for sequence 1), carries ``minimum_supported``
and the crash endpoint from it unless given, refuses another line in ``latest``
without ``--end-line`` (a newer one only), and refuses to replace a release with an
older one; the same version may be issued again.
``update_lines_shipped_client.py`` proves a feed against published apps.

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
   fields; ``--previous`` is required and refuses a sequence that does not grow.
   A newer release line is ``--as-line`` (`Release lines`_). Each
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

Linux release signing
---------------------

Linux has no operating-system code signing, so a Linux release signs its own
files with the **Linux release key**: ``linux-release`` in Cloud KMS (key ring
``angelo-release``, project ``<project>``), Ed25519, separate from the feed
key so either can be rotated or revoked alone, with the same signers. Its
public keys ship in ``linux_release_keys.json``, in ``update_keys.json``'s
form, and each one's minisign public key is in
``doc/angelo/linux_release_keys/<id>.pub`` and on the downloads page.
``sign_linux_release.py`` does every step and verifies each signature with the
public key before it writes anything.

* **The file manifest** (``release_manifest.py``): ``angelo-release-manifest.json``
  at the top of ``Angelo-<version>-linux-x64`` lists every entry of the folder,
  sorted: each file's size, SHA-256 and executable bit, each link's target (it
  must stay inside the folder) and each folder. ``angelo-release-manifest.sig``
  beside it holds the key id and the Ed25519 signature of
  ``angelo-release-manifest-v1\n`` followed by the SHA-512 of the manifest's
  bytes: Cloud KMS signs at most 64 KiB, and a whole release's manifest is
  larger. ``sign_linux_release.py manifest`` writes both on the final folder,
  before it is packed, and refuses a key the shipped list does not trust and a
  folder without its own key list. The updater accepts a new release only when
  ``release_manifest.check_tree`` does: the signature by a trusted key, then
  exactly the listed entries, nothing missing, added or changed, no special
  file, set-id bit or file others may write.
* **The download's signature**: ``Angelo-<version>-linux-x64.tar.xz.minisig``,
  minisign's default form, which is plain Ed25519 over the file's BLAKE2b-512
  (not RFC 8032's Ed25519ph, which Cloud KMS does not offer), so KMS signs it
  whatever the file's size; its trusted comment names the file and the key id.
  The key number minisign shows is derived from the public key (the first 8
  bytes of SHA-256 over ``angelo-minisign-keynum\n`` and the key). It signs the
  tarball itself rather than ``SHA256SUMS``: a Linux release added to a
  published version (``publish_installers.py --add-to-version``) never
  rewrites ``SHA256SUMS``. People check a download with
  ``minisign -Vm Angelo-<version>-linux-x64.tar.xz -P <public key>``, and see that the
  trusted comment minisign prints names that file (minisign does not bind the name itself).
* **Release-ready**: ``sign_linux_release.py check-keys`` (an active key is
  shipped), ``check-tree`` on the folder and ``verify-tarball`` on the download
  (its ``.minisig``, with the ``minisign`` program too when installed, and the
  manifest inside, member by member, without extracting). ``publish_installers.py
  --tarball`` runs ``verify-tarball`` again against the checkout's key list and
  needs the tarball's clean builder-path receipt, then publishes the tarball and
  its ``.minisig``, both listed in the tarball's own ``.sha256``.

The key ceremony is the feed key's (above), once, by a project owner, before
the first Linux release build (builds made before it never trust the key):

1. ``gcloud kms keys create linux-release --keyring angelo-release --location
   global --project <project> --purpose asymmetric-signing
   --default-algorithm ec-sign-ed25519 --protection-level hsm`` (``software``
   where KMS does not offer Ed25519 in an HSM there; record which).
2. ``roles/cloudkms.signerVerifier`` on ``linux-release`` only, to the feed key's
   signers; ``roles/cloudkms.admin`` stays with the project owners; Cloud KMS
   Data Access audit logs on.
3. ``gcloud kms keys versions get-public-key 1 --key linux-release --keyring
   angelo-release --location global --project <project> --output-file
   linux-release-public.pem``.
4. In a reviewed change: ``python3 scripts/angelo/sign_linux_release.py add-key
   --key-id linux-<yyyy>-<mm> --pem linux-release-public.pem`` (it refuses the
   feed's key, a reused id and a private key), which writes the key list entry
   and ``doc/angelo/linux_release_keys/<id>.pub``.
5. Rehearse first with a local key (``keygen``, then ``add-key --keys`` on a copy
   of the list, ``manifest``, ``minisign``, ``verify-tarball`` and ``minisign
   -V``); record the key's path, protection level and id in the handover.

Rotation and exposure follow the feed key's rules. A stolen Linux release key
alone installs nothing: Angelo installs only what a feed signed by the feed key
names by SHA-256 on ``downloads.angelo.studio`` (it does let a compromised
mirror serve a tarball that passes a person's manual ``minisign -V``: check
that the trusted comment minisign prints names the file you downloaded). A
stolen feed key alone gets nothing unpacked: the updater fetches the
tarball's ``.minisig`` (at most 4 KiB, under the same mirror rules) and
verifies it against the release keys over the downloaded bytes before
``lzma`` or ``tarfile`` read any of them; unpacking then gives every mode
explicitly (never the session's umask) and stops at eight times the download.
A ready stage is used only while its manifest signature verifies with the keys
the running build trusts now and the feed still names the same tarball
(``Ready.download_sha256``, ``Ready.key_id``): a revoked key, an emptied list or
a respun tarball sends the update back to preparation. The two key lists never
share a key, revoked entries included (both ``add-key`` commands refuse it, and
a host test checks the shipped lists), and a release must carry the reviewed
``linux_release_keys.json`` byte for byte (``manifest``, ``check-tree`` and
``verify-tarball`` refuse another, such as one that still trusts a rehearsal
key). Linux crash logs are read from the shared ``/tmp`` only when they are
this user's alone (``crash_log.mine``): owned by them and writable by nobody
else. The crash handler (``creator_crash_log.cc``) creates each record, and its
side file, afresh and ``0600`` whatever the umask, removing an old one of the
user's first and never opening through a link or over a file another user left
(``O_CREAT|O_EXCL|O_NOFOLLOW``), so a co-user can neither plant nor rewrite one. The download's ``.minisig`` is checked against the name
the feed publishes, whatever the cache calls the file, and the PASS lines of
``sign_linux_release.py`` end with the SHA-256 of the key list they compared
against.

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
  * **Linux**: the running executable belongs to a release folder exactly as
    extracted (``update_stage_linux.support``): it holds the release's signed
    manifest for the running version; every entry in it is what the manifest
    lists there, by name and kind (the swap moves the whole folder aside and the
    next version deletes it, so a ``portable`` settings folder, a file the person
    copied in, or a released file replaced by a folder of their own would be
    lost: ``modified``); it is not on a read-only file system; the person owns it
    (another person's folder or ``/opt`` is ``permission``) and nobody else may
    write it or rename it away: a folder writable by everyone, by a group that is
    not provably the person's private one (``update_stage_linux.private_group``:
    named after them, no other member, no other local account's primary group;
    a proven answer is kept for the session, a failed lookup is asked again),
    or inside a non-sticky folder others may write, is ``shared``. A release the
    person extracted is ``0755`` whatever their umask (extraction only removes
    mode bits, and the release gates refuse a top folder a group may write); a
    folder the person made and extracted into is ``0775`` under umask 002 with
    their private group, which still counts as theirs, and the update replaces it
    with a ``0755`` one. A
    development build or a distribution's repackaging, which has no manifest, is
    ``location_linux``; a build that trusts no Linux release key is
    ``untrusted`` and offers the download at once instead of fetching it every
    session. The card says which, in the person's language
    (``update_card.WHY_MANUAL``), and offers the download.

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
have a valid one (a chain to a trusted root, PowerShell's
``Get-AuthenticodeSignature``) naming the same publisher: the subject's
validated legal name (O, else CN) and country, compared without regard to ASCII
letter case or runs of spaces (any other difference, a non-ASCII look-alike
included, stays a difference). The issuer is not compared: it changes
legitimately when the certificate comes from another CA or intermediate, or from
a service that issues short-lived certificates (Azure Artifact Signing). A
subject naming no publisher, or two values for one, is refused. Only a running
build that reads as unsigned (``NotSigned``) relies on the feed's digest alone;
any other state, a signature PowerShell could not read included, refuses the
installer and leaves the notify card. Every release is held to the publisher in
``angelo_identity.json`` (``windows.authenticode_publisher``) before it ships
(``releasing.rst``).

``ready.json`` records the prepared update and a fingerprint: the staged
bundle's layout (names, types, sizes, modes and times) on macOS, the MSI's
SHA-256 on Windows. A ready update survives a restart and is checked again,
cheaply, at the next launch and right before it is installed; a changed stage
(a cache cleaner, a full disk) is prepared again rather than installed. A newer
release replaces the folder of an older one (and its pending retries). One worker
at a time holds a version's folder (``update_hold``): stopping a preparation
(Stay, a newer release, a reload of the add-on) only asks it to stop, since
unpacking and verifying run to the end, so the next preparation of that version
waits for it and reuses what it finished, and a removal asked for meanwhile
happens as it lets go (Stay's is called off when the person says yes to that
version again first). Another Angelo of the same install sharing the cache
folder is held off by an OS lock on ``<cache>/updates/.<version>.lock``: its
worker is waited for and its folder is never cleaned under it. A version
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
``render``         a render, bake, composite or export Blender runs (Ocean, Dynamic     ``_bpy._angelo_jobs.``
                   Paint, fluid, point cache, light and Line Art bakes included)       ``running_work``
``task``           any other job Blender runs for the person: a proxy build, tracking  ``_bpy._angelo_jobs.``
                   or a camera solve, an import, a remesh, a UV pack...                ``running_work``
``interaction``    a modal tool or drag in a window (not the update card itself)       ``input_policy``
``unknown``        an owner could not be asked: never read as idle                     (fail closed)
=================  ==================================================================  ====================

Blender's own jobs come from ``_angelo_jobs.running_work``
(``bpy_angelo_jobs_work.cc``): every window-manager job that runs or waits to
run (suspended behind another), whoever owns it, whose type is someone's work.
Every ``eWM_JobType`` is classified in one ``switch`` with no ``default``,
compiled with the missing-enumerator warning as an error on Clang, GCC and
MSVC, so a type Blender adds (a base move included) does not build until it is
classified; ``test_native_work_guard`` reads the enum and the switch as text
too. Only jobs that recompute what a relaunch recomputes by itself are not
work: directory listings, asset library loads, material, icon and file
previews, sequencer thumbnails, drag previews and sound waves, shader
compilation, studio lights, bookmark checks and clip prefetch. Every other type
counts, so a job nobody thought about stops the restart rather than being lost.
The job check is Blender 5.3's ``WM_jobs_has_running(wm, owner, type)``
(running or suspended, any owner), adopted early
(``scripts/angelo/early_adoption.json``). A build without ``running_work``
falls back to the closed lists (``bpy.app.is_job_running`` for renders, preview
renders, bakes and composites; ``_angelo_jobs.progress`` for the point cache,
geometry-nodes bake and export jobs).

The reasons are read once a second on the main thread and cached for drawing.
They are read again when the button is pressed and once more right before the
installer starts. Another copy of the same app still running (its files would
be replaced under it) stops the restart with a notice. A copy counts when it
runs this install's executable file, however it was started: Angelo compares
files, not spellings (device and inode, links followed). On macOS each
process is named by the file the kernel runs (libproc's ``proc_pidpath``, not
``ps``, which reports the path or ``argv[0]`` it was started as), so a copy
started through a symbolic link to the app or its executable, or by a relative
path, is recognised and a copy of the app elsewhere is not; without libproc the
``ps`` paths are resolved the same way, and a relative one counts by its file
name (fail closed). On Windows each process's image path
(``QueryFullProcessImageNameW``) is compared with ``Angelo.exe`` as a file
(volume and file index), so a short (8.3) name, a junction or link to the
install folder, a substituted drive or another letter case still matches.

Restart to Update
-----------------

``update_restart.begin``, on the main thread:

1. the work guard and the other-copy check (above);
2. the cheap check of the prepared update (``Preparer.still_valid``);
3. unsaved changes: **"Save changes before restarting?"**, an Angelo card over
   the 3D view naming the file, the images with unsaved pixels and asset
   catalogs with unsaved changes (``_angelo_jobs.catalogs_unsaved``, the check
   Blender's own quit dialog makes; the file's own line is left out when only
   catalogs are unsaved in a saved file), with Save and
   Restart, Don't Save and Cancel. Save saves the images first
   (``image.save_all_modified``, which packs a generated or packed image into
   the file), then the file (``wm.save_mainfile``), as Blender's own
   quit-after-save does; an image that stays unsaved, or a save that fails,
   stops the restart with a notice. Saving the file writes the loaded
   libraries' modified catalogs (their ``save_post``) when Blender's "Save
   modified asset catalogs" setting is on; the quit dialog's checkbox keeps its
   last state for the session, so the restart turns it on for its own save
   (``_angelo_jobs.catalogs_save_with_file``) and puts it back after, the File
   Browser's save of an untitled file included. A catalog still unsaved after
   the save (its library folder can't be written) stops the restart with a
   notice. Don't Save discards them, as Blender's quit does. The save's
   own answer decides, as in Blender's quit-after-save: ``bpy.data.is_dirty``
   reads True until Blender handles the save's notifier, after the call. An untitled file opens the File
   Browser; the restart continues after ``save_post`` and stops on
   ``save_post_fail`` or when the browser closes without saving. Cancel brings
   the update card back. Every way into the hand-over (a saved file, Don't
   Save, Save, Save As) goes through ``update_restart_handoff.launch_when_settled``:
   the hand-over's document stamp is recorded only once the document reads the
   same across Blender's event-loop passes and, after a save, reads as saved,
   file and images (``bpy.data.is_dirty`` changes at the save's notifier, after
   the call). A file still modified after ``SETTLE_S`` (5 seconds) changed again
   since the save, and the restart stops with the save notice. Reason: a stamp
   taken right after the save no longer matched at the installer's readiness,
   so every Save and Restart said "The update could not start" and Angelo never
   quit (the Windows end-to-end update 1.0.11 to 2.0.0, 2026-11-18). The
   person's decision is kept from the moment they made it: its stamp, the asset
   catalogs' edit generation (after a save, catalogs unsaved again: the save
   itself writes them), and every edit event since (an undo or redo step,
   a depsgraph update, a load, a save: the installer's own change handlers). A
   clean file and Don't Save hand over only the exact state decided on; after a
   save, only the save marking the file and images saved may settle, and once
   read as saved they may not read modified again. Any other change stops the
   hand-over at once: after a save with the save notice; otherwise the person is
   asked again (the save card when anything is unsaved, else the update card).
   An edit Undone at once still counts. Reason: an undoable edit made while a
   clean file or Don't Save settled read the same twice, became the stamp the
   installer kept, and was treated as accepted and lost at the swap (the
   maintainer's audit of 2.0.1, P1; ``test_native_update_restart_settle_edits``,
   ``test_blender_update_restart_settle``);
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
version and, on Windows, Windows Installer's exit code, plus
``reboot=required`` for 3010) before they relaunch.

* **macOS** (``install_update_macos.sh``, ``/bin/sh``, absolute tool paths):
  waits up to ten minutes for the process to end (an exited process its parent
  has not reaped counts as ended); stops if another copy runs from the bundle
  (the executable compared as a file, links followed: ``ps`` names a process
  by the path it was started as, a relative one is asked of ``lsof`` and, for
  another user's process ``lsof`` cannot read, counts by its file name);
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
  waits for the process by number and image name (``tasklist``), stops if
  another copy of this install still runs (``other_copies_windows.ps1``, copied
  out beside it: every process's executable compared with the installed one as
  a file, by volume serial number and file index, so a copy started through a
  link of another name, a short name or a junction counts and another install's
  ``Angelo.exe`` does not; a process whose path cannot be read counts by name
  when it is this user's or its owner is unknown; when PowerShell cannot answer,
  any ``Angelo.exe`` of this user counts), checks the MSI's SHA-256 against the feed's
  again (``certutil``), then ``msiexec /i <msi> /qb!- /norestart ALLUSERS=2
  MSIINSTALLPERUSER=1 MSIRESTARTMANAGERCONTROL=Disable REBOOT=ReallySuppress
  INSTALL_ROOT="<install folder>" /l*v install.log`` (Windows Installer's own
  progress bar, no Cancel), waiting for another installation in progress (exit
  code 1618) up to ten minutes. Exit code 3010 (``ERROR_SUCCESS_REBOOT_REQUIRED``:
  a file in use, such as the thumbnail handler Explorer holds, is replaced when
  Windows next starts) is installed, recorded with ``reboot=required``. If Angelo
  has not quit after ten minutes, either
  script stops without relaunching anything (Angelo itself stops an installer
  that did not report within 15 seconds). Windows Installer replaces the installation as
  one transaction, so a failed or interrupted install leaves the previous
  version. It clears its own variables and starts ``Angelo-launcher.exe`` with
  the file, the new version or, after a failure, the old one, in the
  launcher's own folder as its shortcut does: Windows keeps a process's current
  folder from being deleted, and the new version deletes the update's folder.

* **Linux** (``install_update_linux.sh``, ``/bin/bash``, the system's tools
  only; the relaunched app gets the person's ``PATH`` back): the same
  hand-shake and wait (``/proc/<pid>/status``, a zombie or a process number
  reused by another start time counts as ended), then stops if another process
  runs the installed executable (every ``/proc/<pid>/exe`` compared with it as
  a file, by device and inode). The new folder was checked against its signed
  manifest when it was staged (``update_stage_linux.stage``), and right before
  the script starts its fingerprint is checked again and its manifest
  signature verified again with the keys the running build trusts now; a
  process of the same user that could change the stage after that could change
  the installed app just as well. A stage on another file system is copied
  beside the installed folder first (``cp -a``) and compared with the stage
  byte for byte (``diff -r --no-dereference``); a difference, or a missing
  ``diff``, fails as ``copy`` and changes nothing. Then one atomic exchange of
  the two folders (``renameat2(RENAME_EXCHANGE)``, through the new release's
  own Python, else the system's), so the person's path never stands empty, and
  the old release moves into the update's folder (``previous``, or
  ``.<name>.previous-<n>`` beside the install on another file system). Where
  no exchange is possible (an old kernel, a file system without it such as
  NFS), two renames: first a note beside the install (``<name> - update
  interrupted.txt``, naming both folders and how to put the old one back) and
  ``result.txt`` saying ``interrupted``, then the old folder renamed to a visible
  ``<name> (previous)`` beside it, then the new one into place; the note goes
  once the swap is done, and a failed second rename puts the old folder back.
  The folder name and the note are in the person's language: Angelo
  translates them when the hand-over starts (``update_card.linux_recovery``,
  never a name that exists) and passes them as ``ANGELO_UPDATE_KEPT``,
  ``ANGELO_UPDATE_NOTE`` and ``ANGELO_UPDATE_NOTE_TEXT``, in reading order
  (``ui_text.file_text``: never Blender's pre-shaped visual Arabic), the note
  always a ``.txt``. Angelo records both paths in the update's
  ``recovery.json``, a file the script never writes, with the install folder's
  device and inode, the running version and the SHA-256 of the note's bytes
  (``update_flow.record_recovery``). Cleanup deletes only what matches all of
  it, never anything found by a translated name: every recorded or
  ``previous.txt`` path must be absolute and already normal (no ``.`` or ``..``
  part), compared as a string, and directly beside the install; the kept folder
  only when it is the recorded folder with the recorded device, inode and
  version, a version older than the one now running; the note only when it is
  the recorded ``.txt``, a regular file opened without following a link or
  blocking, whose bytes hash to the recorded value and name both folders (the
  install folder also on its own, not only inside the kept folder's path),
  while the app at the install runs. Cleanup never raises: its readers open
  ``recovery.json``, ``previous.txt``, the note and the manifest without
  blocking and count anything but a regular UTF-8 file as no match; each step
  (note, leftovers, kept folder) is independent, so one that fails (a note in a
  sticky shared folder, a read-only mount) never skips the others; and
  ``update_ready._handle_results_once``, the one way in at launch, logs any
  exception instead of ending a timer or an operator. The record identifies, it does not
  authenticate: it lives in Angelo's private update folder, and a writer there
  could copy another folder's device, inode and version into it. What that
  reaches is a folder or ``.txt`` directly beside the install, never a link,
  another depth or another kind of file. The script creates the note with
  ``O_CREAT|O_EXCL|O_NOFOLLOW`` (through the same Python as the exchange), so a
  file, link or FIFO that appears at its name refuses the update (``note``) and
  is never opened. A recorded note is also removed at the next start
  after an attempt that stopped before it moved anything. At the next start of the installed version an
  ``interrupted`` result counts as installed (the stop came after the swap), and
  the previous folder and a note beside a running app are removed.
  The host tests force this path and a stop between the renames through
  ``ANGELO_UPDATE_TEST_*`` switches, which Angelo never passes on (it drops every
  ``ANGELO_UPDATE_*`` variable of its own environment before it starts the
  script). The folder keeps
  its path and name, so the person's launchers and desktop entry keep working.
  The script starts the release's launcher (``angelo``) with the file, in the
  person's home folder: the new version or, after a failure, the old one.

The next launch
---------------

``update_restart.handle_results`` reads every ``result.txt`` once:

* ``installed`` for the running version: the previous bundle and the update's
  folder are removed (only a folder the installer named, never the installed
  app), and the failure count is reset. When Windows still has to finish it
  (``update_install.needs_reboot``: ``reboot=required``, or exit code 3010 from
  an installer script older than that line, as the running version's own copy
  installs its update), "Angelo 1.1 is installed" says once that restarting
  Windows finishes the update; it is a success, never a failure count;
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

The crash handler
-----------------

A crash always leaves a log and always ends the process; it never hangs
(``source/creator/creator_signals.cc``, ``creator_crash_log.cc``). The
handler runs after a crash with any lock held, the heap corrupted or Python
half way through a call, so:

* **Minimal record first.** The version header and the signal (number, name,
  code, fault address; on Windows the exception code, address and module), then
  the native stack, faulting instruction first, go straight to the log with
  system calls that take no lock and allocate nothing (``write(2)`` and
  ``backtrace_symbols_fd``; ``WriteFile``), before anything else runs; the
  ``Writing: <log>`` line is printed after it.
* **Full record, best-effort.** The header, signal, Info reports (only when
  their lock is free, else one line saying they were skipped), ``# backtrace``,
  the stack (Windows: the exception record, stack walk, threads and modules)
  and the Python stack (only when the crashed thread holds the GIL) go to
  ``<log>.partial``, which replaces the minimal record only once complete. It is
  the layout ``crash_log`` reads; a ``.partial`` never matches ``*.crash.txt``.
* **One crash at a time.** The first crash claims the handler (an atomic
  owner, not ``std::call_once``, which blocked forever on a fault inside the
  handler). A fault or abort inside the handler ends the process at once with
  the first crash's signal and its default action, keeping what was written;
  another thread's crash waits for the owner.
* **A watchdog.** If the handler has not ended the process after 10 seconds
  (``CRASH_HANDLER_TIMEOUT_S``), an ``alarm`` (Windows: a thread started with
  the handler) ends it with the crash signal's default action. On Windows the
  crash dialog gets a fresh 10 seconds to appear, stops the watchdog only once
  it is on screen, and the watchdog covers the clean-up after it closes;
  without the dialog (background or console mode) the filter ends the process
  with ``TerminateProcess`` rather than the system's exit path.
* **An abort is a crash.** With the crash and abort handlers both on, the
  process's own SIGABRT (an uncaught C++ exception, a heap check, a fatal Python
  error) writes the log the same way and ends the process; on Windows the abort
  handler writes it (no exception record; the stack walk of the aborting
  thread) and ends the process with exit code 3 instead of the CRT's
  fast-fail. A SIGABRT sent by another process, or any abort with the crash
  handler off, only empties the session's temporary folder, as upstream.
* **Every crash signal.** ``sigaction`` with ``SA_SIGINFO | SA_ONSTACK |
  SA_NODEFER`` for SIGSEGV, SIGBUS, SIGILL, SIGFPE and SIGTRAP (upstream:
  SIGSEGV only), never replacing a disposition that is not the default
  (``--debug-fpe``, a sanitizer, an ignored signal). The main thread has a 256 KiB alternate
  stack, so its stack overflow is logged; an alternate stack is per thread, so
  another thread's overflow still ends the process without a log.
  ``SA_NODEFER`` (not ``SA_RESETHAND``) lets a fault inside the handler re-enter
  it to end the process, and lets a second thread's crash wait instead of
  killing the process half way through the log.
* ``--disable-crash-handler`` installs none of this; ``--console-crash-handler``
  writes the minimal record to stderr, then the reports, the stack again (so no
  report text follows the last ``# backtrace``) and the Python stack.

Why: in the 1.0.6 rehearsal a SIGSEGV under load left a 0-byte
``angelo.crash.txt`` (the old handler wrote through a buffered ``FILE``, then
waited on the reports lock or the heap) and the app hung for 10 minutes until
it was killed. ``test_blender_crash_handler`` (BACKGROUND, macOS and Linux)
crashes real background children seven ways (main thread with the GIL, a
worker without it, a fault inside the handler, held ``stdout`` and ``stderr``
locks, SIGBUS, a main-thread stack overflow) and checks each log reads and each
process ends within the watchdog bound. Just before each crash the child records
who handles every crash signal (``native``, or ``SIG_DFL`` when no handler is
installed), so a run that leaves no log says whether the handler was there:
runs of 2026-11-08 and 11-09 on one Mac left no log and no handler output in
every case, with 1.0.9 and 1.0.10 alike, and the same apps passed on both Macs
afterwards (directly and through the runner), so the cause was that launch
environment, not the log writer. It never aborts on purpose:
``test_native_no_deliberate_abort`` forbids SIGABRT in every script and test,
with no exemption, because on macOS an abort shows a system dialog that stops
automation. SIGABRT logging is proven on Windows (build4: an abort writes the
log and exits with code 3) and by the shared handler path on macOS; a native
macOS abort test is pending.

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

A crash in the graphics driver
------------------------------

Blender 5.3 moves every Windows x64 preference file to Vulkan, upgraders included (1.0.x drew with OpenGL). When the
crash Angelo found was in the graphics driver while this launch draws with Vulkan (``crash_gpu.offers_opengl``:
the crashed thread or the faulting module is the Vulkan loader, a driver's ``nvoglv``, ``amdvlk``, ``atio6axx``,
``igvk`` or ``ig*icd`` module, or Blender's own Vulkan backend), the dialog's question says so and offers
**Restart with OpenGL** above its buttons. It answers the crash as Close does, chooses OpenGL in Preferences >
System, saves the preferences, starts Angelo again and quits. A crash the dialog does not ask about (reports turned
off by policy, never, waiting for the service, or sent automatically) gets one corner notice with the same button
instead (``crash_ui._offer_opengl``, its own card kind). The person switches back in Preferences > System.
``test_native_crash_gpu.py``; the Windows GUI proof is gcp-kit's crash-card run on a T4 VM.

A Vulkan start that never drew a frame
--------------------------------------

The offer above needs a launch that gets as far as its interface. A driver that accepts Vulkan and then crashes while
Angelo starts never gives it one: every launch dies the same way, for an updater and for a new install alike
(upstream #166510, older Intel graphics after a driver update; Blender's own fallback only covers a device that
says it cannot do what Vulkan needs). So the start itself is watched, natively, before the GPU is touched
(``wm_angelo_gpu_startup.cc``):

* A window launch that sets out to draw with Vulkan writes ``angelo-gpu-startup.txt`` in the configuration folder
  (``vulkan starting <process id>``) when the preferences are read and keeps the file open for itself alone. When a
  frame has been presented, or Angelo exits by itself after the GPU is initialized (the person quits, a script
  ends the process with any status), it empties the record and lets go of it. On Windows it then removes the
  file; on Linux the empty file stays, because removing a name there asks no holder and could take the record of
  a launch that had just begun.
* A launch that finds a record whose launch is gone knows Vulkan did not start on this machine. It draws with
  OpenGL, sets the preference to OpenGL (Preferences > System shows what is used; the usual save on exit keeps it),
  shows Blender's own alert *Failed to load using Vulkan, using OpenGL instead* and rewrites the record to
  ``vulkan failed``.
* **Which deaths count.** While the GPU is initialized, and in the main loop before the first presented frame.
  Not while start-up scripts run, from Python's start to the main loop's: the record says ``vulkan starting <id>
  scripts`` then. A third-party add-on that crashes as it loads is the safe start's (``app_identity.rst``, "A start
  that did not finish"), the long first start of a new version (the settings carry-over) is not the GPU's, and a
  script on the command line that ends the process is automation. One death in that time does count: a crash on
  any thread but the main one, where scripts and add-ons run. Vulkan's submission thread and the driver's own
  threads work on while scripts run, and their crash is the GPU's.
* **A crash is marked at once.** On Windows the crash dialog keeps the crashed process alive, and its Restart
  starts the next launch meanwhile. The crash handler therefore rewrites the record to ``vulkan crashed`` (or
  empties it, for a crash that is the scripts' own) and lets go of the file before the dialog, as the safe start
  marks its record.
* **Who is gone** is decided by who can open the file, never by a process id: the operating system ends a
  process's hold when the process ends, however it ends, and an id used again by another process cannot hide a
  failed start. A launch that finds the file held by another process waits up to 2.5 s for it (a killed process's
  hold goes in the system's own time), unless the record says that launch is running its scripts. Still held, or
  not openable for another reason (an access rule, a lock service that is down), the file is read without being
  taken. A record that says crashed or failed is never held by a live launch, which rewrites it first; so it is
  believed whoever holds the file (another program, a child process that outlived its launch): OpenGL, and
  nothing is written. Any other content is no evidence: another launch is still starting, and this one goes on
  with Vulkan, unwatched.
* **The failed record stays**, in the launch that learned of the failure and in every later one: a preference file
  read later (settings imported from an earlier version are moved to Vulkan by Blender's versioning; factory
  preferences) is turned to OpenGL as long as the record says failed. Only the launch that learned of the failure
  shows the alert; later ones use OpenGL with a line in the log. Only choosing Vulkan in Preferences > System (or a
  script setting ``preferences.system.gpu_backend``) removes the record; the next launch then tries Vulkan, watched
  as before. After a driver update that is what a person does to try again. The record is not a setting:
  importing an earlier version's settings leaves it behind (``settings_import.PREVIOUS_LEFT_OUT``).
* ``--gpu-backend`` on the command line is never overridden and leaves no record. Background launches are not
  watched. macOS has one backend and nothing to fall back to.

What this does not do, stated plainly:

* **It cannot tell why a counted start died.** A death while the GPU is initialized, or in the main loop before
  the first frame, reads as a failed Vulkan start whatever caused it: a driver crash, but also the person ending a
  start by force then, a power cut, or a window that starts minimized on Windows (it presents no frame; closing
  it normally is fine, but logging off or ending it from Task Manager before it is first shown leaves the record).
  Such a death flips the machine to OpenGL once, the alert says so, and the person chooses Vulkan again in
  Preferences > System.
* **A driver crash on the main thread while start-up scripts run is missed**, by the rule above: an add-on that
  draws or compiles a shader as it registers, and the driver crashing inside that call. So is a death in that
  time that is no crash (the process is ended from outside). The crash log of #166510 holds no Python frames,
  which fits a crash that is counted, on the main thread outside that time or on another thread at any time;
  without the hardware this is not proven.
* **A native crash on a thread an add-on started while it loads counts as the GPU's**, the other side of the
  same rule: one fallback to OpenGL, with the alert.
* **A crash after the first frame** stays with the offer above.
* **OpenGL itself is broken on the hardware of #166510.** On those older Intel graphics Blender 5.3's OpenGL draws
  garbled interface text (upstream #166428, open, no fix yet). There the fallback gives an app that starts and
  whose text is hard to read; without it the app does not start at all.
* **An explicit OpenGL choice made in 1.0.x cannot be kept as such.** Its stored value is the 1.0.x default, so the
  two honest behaviours are: every earlier preference file moves to Vulkan, as in Blender (shipped), or every
  earlier file keeps its backend and only new installs start with Vulkan. One constant chooses,
  ``ANGELO_EARLIER_PREFERENCES_KEEP_GPU_BACKEND`` (``versioning_angelo_gpu_backend.hh``); its rule is tested for
  both values (``versioning_angelo_gpu_backend_test.cc``). The one line that applies it in
  ``versioning_userdef.cc`` is compiled out on macOS and no suite starts from a preference file older than 5.3,
  so that line is checked by reading only. The watch above is needed under either.

``wm_angelo_gpu_startup_test.cc`` covers the rule, the record's text and its file (who holds it, emptied before it
is let go, the crash handler's write, a file that cannot be opened), what a crash leaves by phase and thread, how
a thread is told from another, and a record that cannot be taken. No CI job builds the GTests on Windows; the
first twelve and the three of ``versioning_angelo_gpu_backend_test.cc`` were built and run there once by hand, on
the first Windows GTest build (2026-11-14, a Windows Server VM), and all run on macOS with every proof.
``test_blender_gpu_startup_fallback`` runs twenty-one real launches on a machine with both backends (Windows,
Linux). Three of them crash natively: inside the first frame's draw, and while the start-up script runs, on the
main thread and on another. One meets a failed record whose file another program holds. Each crash leaves
Blender's crash log, which the suite keeps beside its receipts (``native_crash.keep_crash_logs``): the suite
runner counts any ``*.crash.txt`` left in a suite's folder as a crash of the run.

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
``MAJOR.MINOR.PATCH`` (each release line, ``MAJOR.MINOR``, may have one
``Unreleased`` entry, its newest: 1.0.x gets fixes beside 1.1.x, so a 1.0.x fix
and the next minor can be pending at once; a release build refuses any undated
entry, ``release_dated.py``, and What's New and the feed pick an entry by its
exact version), the line
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
run for real: the wait, the swap, the rollback, another running copy started
by its path, through links or by a relative path, and a copy elsewhere that is not one),
``test_native_update_cards`` (every phase and the save prompt on every
region), ``test_native_update_ready`` (background preparation, throttling,
retries, the restart's refusals, the save prompt, the hand-over, the next
launch, the work guard), ``test_native_work_guard`` (every ``eWM_JobType``
classified once, a missing case a compile error, Blender's jobs as ``render``
or ``task``, the fallback without ``running_work``),
``test_native_update_other_copies`` (other copies by file on both platforms,
real processes started through links and relative paths, listed by libproc,
on macOS, and the Windows script's identity check by its contract; it runs for
real only in the Windows end-to-end update), ``test_native_update_restart_saves`` (asset catalogs in
the save prompt, saved with the file and put back, a catalog that can't be
written; the Windows reboot result told once) and
``test_native_update_release_tools`` (deltas and
``automatic_install`` through ``sign_update_feed.py`` and
``publish_installers.py``).

Background: ``run_native_suites.py --include test_blender_work_guard_jobs``
starts a real Ocean bake and an Alembic import job, which no closed list names,
and requires ``running_work`` and ``work_guard.reasons`` to report them (and
``running_scene_jobs``, which holds the parallel sessions' live view and
bindings, ``scene_jobs.py``).

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
