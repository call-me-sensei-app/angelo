.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Angelo for organizations: managed policy and team sign-in
**********************************************************

For IT administrators who deploy Angelo to an organization's computers. The
policy is read by ``managed_policy.py`` (with the modules named below); the
people-facing side of team sign-in is in ``ai_providers.rst``, "Team account
and team connections".

What an organization can control
================================

Angelo reads a **machine policy** once at startup. Nobody can change it from
inside the app: the settings it sets are locked and shown as "managed by your
organization" in Preferences > AI Providers. Every key is optional; an unset
key keeps Angelo's default.

============================  =============  =======  ============================================================
Key                           Type           Default  Effect
============================  =============  =======  ============================================================
``ConsoleURL``                string (URL)   —        The Angelo console to sign in to, ``https://`` origin only.
                                                      Wins over ``ANGELO_CONSOLE_URL``.
``GatewayURL``                string (URL)   —        The Angelo gateway for team connections, ``https://`` origin
                                                      only. Wins over ``ANGELO_GATEWAY_URL``.
``RequireTeamSignIn``         boolean        false    No AI connection can be used until the person is signed in
                                                      to the organization's Angelo team. Personal connections (if
                                                      allowed) appear only after that sign-in.
``Organization``              string (UUID)  —        The organization a sign-in must be for. A sign-in to any
                                                      other organization is revoked at once.
``EmailDomains``              string list    —        The sign-in's email must be at one of these domains (for
                                                      example ``northwind.example``); otherwise it is revoked at
                                                      once.
``AllowPersonalConnections``  boolean        true     false: only team connections. People can't add or use
                                                      subscriptions (ChatGPT, Claude) or their own API keys; the
                                                      ones already saved stay on the computer, unused.
``AllowLocalCLIAccess``       boolean        true     false: the local ``angelo`` command-line access for coding
                                                      agents on the computer stays closed and its preference is
                                                      locked (``external_cli.rst``).
``AllowVoice``                boolean        true     false: voice chat is off.
``AllowAutoApproval``         boolean        true     false: the session-only trusted-script consent and the
                                                      person's Always allow scripts (automatic approval of agent
                                                      scripts) can't be given or used, and queued automatic
                                                      scripts are blocked at once. Edits are not affected (see
                                                      ``RequireAskForEdits``). Neither value replaces manual
                                                      review of the exact code or overrides Read Only.
``RequireAskForEdits``        boolean        false    true: every chat uses Ask before changes. Auto bounded
                                                      edits (otherwise a new chat's default) reads as Ask and
                                                      can't be chosen ("Turned off by your organization."), so
                                                      queued edits ask too. It still allows "Trust scripts for
                                                      this chat": add ``AllowAutoApproval`` false to have every
                                                      script reviewed too.
``AllowCollaborators``        boolean        true     false: the agent can't consult another connection's model.
``AdminContact``              string         —        Shown after "Contact your admin for access:" (an email
                                                      address, a URL or a help-desk name, at most 200 characters).
``ExtraCACertificates``       string (path)  —        An absolute path to a PEM file of extra trusted root
                                                      certificates, for networks that inspect TLS. Added to
                                                      Angelo's own roots, never a replacement; Claude Code
                                                      receives it as ``NODE_EXTRA_CA_CERTS``.
``DisableUpdateCheck``        boolean        false    true: Angelo never asks whether a newer version exists,
                                                      not even from Check for Updates…, so it never downloads or
                                                      installs one either; the setting is shown locked. An
                                                      install for all users (the MSI with ``ALLUSERS=1``, an app
                                                      in a folder people can't write) never installs updates
                                                      itself; it only tells.
``UpdateFeedURL``             string (URL)   —        An https address (no user name, query or other port) of
                                                      your own copy of Angelo's signed update file,
                                                      ``stable.json``. The signature is still required.
``DisableCrashReporting``     boolean        false    true: no crash dialog, report or upload; the crash log
                                                      stays in the temporary folder.
============================  =============  =======  ============================================================

**Unusable values are never ignored.** A value of the wrong type or form is
applied in the restrictive direction and reported to the person in AI
Providers ("Your organization's Angelo policy has unusable values (…)"): an
``Allow…`` switch reads as off, ``RequireTeamSignIn`` and the ``Disable…``
switches as on, an unusable ``ConsoleURL``, ``GatewayURL``, ``Organization`` or
``EmailDomains`` turns team sign-in off rather than falling back to Angelo's
defaults, and an unusable ``UpdateFeedURL`` stops the update check rather than
falling back to Angelo's own address. Booleans accept
true/false, 1/0 and yes/no (any case); a missing ``ExtraCACertificates`` file
adds nothing. Unknown keys are ignored.

Where the policy comes from
===========================

Sources, highest precedence first; a key set by an earlier source wins over
the same key in a later one:

1. **macOS**: managed preferences for the domain ``studio.hyperbond.angelo`` (the app's
   bundle identifier), delivered by a configuration profile from your device
   management service. A device-level profile
   (``/Library/Managed Preferences/studio.hyperbond.angelo.plist``) wins over a
   user-level one (``/Library/Managed Preferences/<user>/studio.hyperbond.angelo.plist``).
2. **Windows**: registry values under
   ``HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Hyperbond\Angelo`` (the 64-bit view),
   then the same key under ``HKEY_CURRENT_USER``.
3. **Linux**: ``/etc/angelo/policy.json``, a JSON object of the keys above.
4. ``ANGELO_POLICY_FILE``, a JSON file named by an environment variable, for
   trying a policy out without device management. It can only set keys that no
   managed source sets.

macOS configuration profile
---------------------------

Deliver a *Custom Settings* (Application & Custom Settings) payload for the
preference domain ``studio.hyperbond.angelo``, for example::

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
      <key>PayloadContent</key>
      <array>
        <dict>
          <key>PayloadType</key><string>studio.hyperbond.angelo</string>
          <key>PayloadIdentifier</key><string>example.angelo.policy</string>
          <key>PayloadUUID</key><string>REPLACE-WITH-A-UUID</string>
          <key>PayloadVersion</key><integer>1</integer>
          <key>RequireTeamSignIn</key><true/>
          <key>AllowPersonalConnections</key><false/>
          <key>Organization</key><string>REPLACE-WITH-YOUR-ORGANIZATION-ID</string>
          <key>EmailDomains</key><array><string>northwind.example</string></array>
          <key>AdminContact</key><string>it-help@northwind.example</string>
        </dict>
      </array>
      <key>PayloadDisplayName</key><string>Angelo policy</string>
      <key>PayloadIdentifier</key><string>example.angelo</string>
      <key>PayloadType</key><string>Configuration</string>
      <key>PayloadUUID</key><string>REPLACE-WITH-ANOTHER-UUID</string>
      <key>PayloadVersion</key><integer>1</integer>
    </dict>
    </plist>

Check what a computer received with
``defaults read "/Library/Managed Preferences/studio.hyperbond.angelo"``. The
organization's ID is shown in the console's organization settings and in its
URLs (``/orgs/<id>/…``).

Windows registry and Group Policy
---------------------------------

Booleans are ``REG_DWORD`` (1 or 0), text is ``REG_SZ`` (or ``REG_EXPAND_SZ``,
whose environment variables such as ``%ProgramData%`` are expanded), and
``EmailDomains`` is ``REG_MULTI_SZ`` (or a comma-separated ``REG_SZ``). Deploy them with Group
Policy Preferences (Registry items), Intune, or any tool that writes the
policy key. An example ``.reg`` file::

    Windows Registry Editor Version 5.00

    [HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Hyperbond\Angelo]
    "RequireTeamSignIn"=dword:00000001
    "AllowPersonalConnections"=dword:00000000
    "Organization"="REPLACE-WITH-YOUR-ORGANIZATION-ID"
    "AdminContact"="it-help@northwind.example"

ADMX administrative templates are not shipped yet.

Linux
-----

``/etc/angelo/policy.json``, readable by everyone and writable only by root::

    {"RequireTeamSignIn": true, "AllowPersonalConnections": false,
     "EmailDomains": ["northwind.example"], "AdminContact": "it-help@northwind.example"}

What people see
===============

* **The status bar**, right of the version number, shows the signed-in team
  and person (a warning icon when they have no seat) or **Sign in**; its menu
  has the account, seat, usage this period, the console pages (Profile and
  Usage, Your Devices, Team Library), AI Providers and Sign Out.
* **Preferences > AI Providers** says which settings are managed and lists the
  team's connections. With ``AllowPersonalConnections`` off it shows only the
  team section.
* **No access.** Whenever someone signed in to a team, or on a managed
  computer, can't use AI (no seat, the team has added no provider keys, a
  removed membership, a sign-in for the wrong organization), the message ends
  in **Contact your admin for access**, followed by ``AdminContact`` when set.
  The chat's setup line says the same.

Sign-in, SSO and devices
========================

People sign in from Angelo, which opens their default browser at the console
(``https://console.angelo.studio/oauth/authorize``). The console handles the
sign-in itself: its own sign-in, email code, Google, GitHub, or your
organization's single sign-on when its email domain uses SSO. Angelo never
sees a password. On the allow screen the person picks the organization (one
per sign-in on a computer) and clicks Allow; the browser then returns to
Angelo on ``http://127.0.0.1:<random port>/callback`` on the same computer.

* **Seats.** Without a seat a person can sign in (the team library works) but
  can't use the team's AI connections.
* **Your devices.** Every sign-in is a device in the console (*Your devices*
  for the person, *People > Devices* for admins). Signing a device out, or
  removing the person from the organization, ends that computer's sign-in: at
  once for the console, within 10 minutes for the gateway's access tokens and
  within about a minute for the coding agents' keys. Angelo then says the
  computer was signed out (or that the person is no longer a member) and asks
  them to sign in again.
* **Sign out** in Angelo revokes the device on the console and removes its
  credentials from the computer.

Network
=======

Angelo needs outbound HTTPS (port 443) to the console
(``console.angelo.studio``) and the gateway (``gateway.angelo.studio``), or the
origins your policy pins, and the loopback address 127.0.0.1 on the computer
itself during sign-in (a local firewall must not block loopback connections).
Personal connections, when allowed, also reach their providers directly.

* **TLS inspection.** Angelo verifies TLS with its bundled root certificates.
  A network that re-signs TLS with its own root needs that root in
  ``ExtraCACertificates``. API-key connection checks use the same extra roots
  as generation and team requests, with certificate and hostname verification
  still required. A chain holding a key Angelo distrusts (a certificate
  authority's compromised key) is refused even under an added root. The coding agents (Codex and Claude Code) are separate
  programs: Claude Code receives the file as
  ``NODE_EXTRA_CA_CERTS``; Codex follows the standard ``SSL_CERT_FILE`` and
  ``CODEX_CA_CERTIFICATE`` variables, which you can set machine-wide.
* **Proxies.** Angelo's own requests to the console and gateway don't use an
  HTTP proxy yet; a network that only allows outbound traffic through a proxy
  can't sign in. The update check and crash reports carry no credential and do
  follow the system proxy (``HTTPS_PROXY``/``NO_PROXY``, macOS network
  settings, the Windows proxy setting; no PAC files). The coding agents honour
  the standard ``HTTPS_PROXY`` and ``NO_PROXY`` variables.
* **Updates and crash reports** are described in ``updates_and_reports.rst``:
  what the update check sends (one request a day, no identifier), how to mirror
  the signed file for ``UpdateFeedURL``, and what a crash report contains.
* **Agent usage reports.** While someone is signed in to the organization,
  Angelo reports to that organization's console how many agent tasks they
  finished on team connections and the longest one, per day, provider and
  model, about every 15 minutes and at quit (``ai_providers.rst``, "Agent
  usage reports"). Nothing else is sent: no prompt, file, token count or cost.
  No policy key turns it off; it goes only to the console your policy pins.

Credentials on the computer
===========================

Angelo keeps the sign-in in the operating system's credential store, never in
a file: the macOS login Keychain, Windows Credential Manager or the Linux
Secret Service. Three items per sign-in, all removed by Sign out:

* ``studio.hyperbond.angelo.team-sign-in``: the console's refresh token (60
  days if unused, rotated on every renewal);
* ``studio.hyperbond.angelo.team-access-token``: the current 10-minute access
  token, so Angelo's windows and restarts share it instead of each renewing
  the sign-in;
* ``studio.hyperbond.angelo.team-grant-key``: the key the coding agents use
  with the gateway (12 hours), shared by Angelo's windows.

Provider keys never reach the computer: the gateway adds them.

What needs a Windows run
========================

Checked on Windows Server 2022 against the in-process fake console, as SYSTEM
and in a signed-in desktop session (``app_identity.rst``, "Windows
validation"): the Credential Manager items, sign-in through the loopback
(127.0.0.1 only, no firewall rule added) with the sign-in page opened in the
default browser, the team lock across two processes (released when the holder
is killed), the policy reader (REG_SZ, REG_DWORD, REG_MULTI_SZ, REG_EXPAND_SZ,
HKLM over HKCU, unusable values restrictive) and Sign out. Still to run there:

* A sign-in against a real console, and the status bar's account item in
  Angelo's own window.
* Claude Code and Codex team runtimes launched with the gateway route and the
  grant key (``team_runtime.py``), and ``ExtraCACertificates`` with a TLS
  inspecting proxy.

The organization's own policy
=============================

Owners and admins can also set a **desktop app policy** in the console, for
everyone signed in to the organization: personal connections, local CLI
access, voice and collaborators on or off, and an admin contact. Angelo
receives it with every sign-in renewal and combines it with the machine
policy: for each switch the stricter value wins, and the machine policy's
``AdminContact`` comes first. Only the machine policy decides where people
sign in (``ConsoleURL``, ``GatewayURL``, ``Organization``, ``EmailDomains``,
``RequireTeamSignIn``) and adds TLS roots. Angelo keeps the last policy it
received, so it still applies when the computer is offline; signing out
removes it. Use the machine policy for what must hold whether or not anyone
is signed in. The console's admin contact follows the same rule as
``AdminContact``: one line, at most 200 characters.

**Approval switches: managed policy today.** ``AllowAutoApproval`` and
``RequireAskForEdits`` are enforced from the machine policy (MDM, Group Policy,
``/etc/angelo/policy.json``) today. The console does not send them yet; Angelo
already reads them from a token answer's policy as ``allowAutoApproval`` and
``requireAskForEdits`` and enforces them once the console supports them, the
stricter of the two policies winning, so an organization could then keep Auto
edits while every script is reviewed. How a sent value reads: a missing
``allowAutoApproval`` leaves it on and any value but ``true`` (``null``
included) turns it off; a missing or ``null`` ``requireAskForEdits`` leaves it
off, ``false`` keeps it off, and any other value turns it on.

With ``Organization`` set, Angelo asks the console to pre-select that
organization on the allow screen; a sign-in to another organization is still
revoked at once. When the console has ended someone's sign-in, the next
sign-in fills in that account's email for them.

Not supported yet
=================

* HTTP proxy support for Angelo's own console and gateway requests, and ADMX
  templates for Windows.
