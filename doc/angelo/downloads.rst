.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Files from the web
==================

Agent scripts have no network while they run (2.0.0, ``script_confinement.rst``), so a file the person asks for from
a link (a texture, a model, an HDRI, an image or a sound) comes into the project through one typed tool,
``angelo_download``, after the person approves it on a card. The download only saves the file: it is never opened,
run or imported by it. The agent imports the saved file afterwards with the usual tools.

The agent
---------

``angelo_download`` (deferred: a plan names it and its schema and guidance are read on first use; never offered to
voice threads) takes:

* ``url``: an ``https`` link of at most 2,048 bytes, with a host and no user name or password. Any other scheme is
  refused before a card.
* ``kind``: ``texture``, ``model``, ``hdri``, ``image``, ``audio`` or ``other``: what the file must be.
* ``name`` (optional): the file name to use, at most 100 characters; the extension always comes from the checked type.

Its result: ``path`` (relative to the project's folder; the full path for an unsaved document), ``file`` (the full
path, which an agent script may read), ``folder`` (``project`` or ``unsaved``), ``bytes``, ``sha256``, ``format``,
``host`` (the host the file came from, after redirects), ``address_checked_by`` (``angelo``, or ``proxy`` when the
proxy resolved a name: "Through a proxy") and ``scene_changed: false``. A refusal has ``error_code``
(``refusal_codes``), ``reason`` (one word, below) and ``scene_changed: false``.

The card
--------

The tool is not a bounded edit, so it shows a card in Ask and in Auto (Auto's line: Approve for me never runs this
kind of step on its own); a read-only chat refuses it. The card names the host in its title and shows the link, the
kind, the size limit and the folder, and says the file's type is checked from its contents and that it isn't opened,
run or imported. Its chip says "File download", not "Scene changes": a family whose tools change no scene names
its own chip (``CHIP`` in its approvals module, read by ``approval_families.approval_kind``). Outside apps (MCP)
reach the tool at the Edit level only with Angelo's window open, where the same card asks the person; a headless
session never offers it.

Where the file goes
-------------------

``assets/downloads`` beside the saved file, or, while the document is unsaved, in its own folder
``<Blender's temporary folder>/angelo-downloads/<id>`` (``download_folders``; the card says which). Blender keeps an
unsaved session's autosave in the same temporary folder. The id is a random one kept as the
``_angelo_downloads_id`` custom property of every local scene, so a reload of the unsaved session (Recover Last
Session, Recover Auto Save) finds the same folder. It is never the chat's own folder: that sits under the agent state
folder, which the script confinement keeps private, so a later agent script could not read the file there. The
confinement lists ``angelo-downloads`` (``download_folders.unsaved_base``) as written only by Angelo: a script reads a
download there but cannot change it. The temporary folder is shared on Linux, so ``angelo-downloads`` and each
document's folder are made owner-only, and one that is a link, another user's or writable by others is refused
(``folder_unavailable``). Saving the document later moves nothing: earlier downloads stay where they are, so their
paths in the chat stay valid, and later ones go beside the saved file.

The ``assets/downloads`` folder is made when missing and must be exactly that folder: a part of it that
is a link or junction, or a folder that resolves anywhere else, is refused (``outside_folder``) before any transfer.
The file is written under a hidden temporary name and then placed under a safe name:

* the ``name`` argument, else the link's last path part, else the server's ``Content-Disposition`` file name, only as
  hints, cleaned to one plain name: no folder, no hidden or reserved device name (``CON``, ``NUL``, ``COM1``...), no
  control, direction or separator characters, no program extension, at most 100 characters; else ``download``;
* the extension is the checked type's (``.png``, ``.glb``, ``.exr``...), never the server's or the link's;
* it never replaces a file: a taken name becomes ``name (2)``, ``name (3)``...;
* it is created without execute permission and is never made executable; its resolved path must sit in the folder.

A refused, failed or stopped transfer leaves nothing behind.

What a download may reach
-------------------------

Public addresses only. Each host is resolved once; every answer must be public, and the connection is made to that
address with the host name kept for the certificate check (SNI) and the Host header, so a name that answers with
another address a moment later (DNS rebinding) changes nothing. A link that names an address is checked as written.
Refused classes (``reason`` ``address_<class>``): ``loopback``, ``private``, ``link_local`` (169.254.0.0/16, where
clouds serve instance metadata, and fe80::/10), ``shared`` (100.64.0.0/10, carrier-grade NAT), ``multicast``,
``reserved``, ``unspecified``, ``unique_local`` (fc00::/7), and an IPv4 address carried in IPv6 (mapped, 6to4, NAT64)
of any of these.

The transfer uses exactly the proxy the update check (``app_http``) would: urllib's own choice for an ``https``
request (``getproxies``, ``proxy_bypass``, Basic credentials only with both a user name and a password, port 443 when
the proxy names none). The name is resolved and checked here, then the proxy is asked for a ``CONNECT`` to that
address, never to the name, and TLS inside the tunnel verifies the name's certificate. A proxy that is not an HTTP
``CONNECT`` proxy (SOCKS) fails, as it does for the update check; the transfer never goes direct around it.
Certificates are verified against Angelo's trust roots (``team_http.tls_context``: the system's or bundled roots plus
a managed policy's extra roots), the same roots as ``app_http``. Redirects are followed only to ``https``, at most
five, each hop resolved, checked and pinned the same way (``redirect_not_https``, ``too_many_redirects``). A
redirect keeps to port 443 or the port the person's link named (``redirect_port``): another port on a checked public
address is another service.

Through a proxy
---------------

Some office networks let only their proxy resolve outside names: this computer's own lookup fails. Then, and only
then, the download asks the proxy for ``CONNECT <name>:<port>`` and the proxy finds the address, which Angelo cannot
check (``download_fetch._route``, ``download_names``). The rule, for each name a download reaches (every redirect
hop):

* Only when this computer's lookup *fails* and a proxy is set for the name (``proxy_bypass``). A lookup that answers
  a refused address is refused, proxy or not: there is no fallback around a refusal.
* Only for a name that cannot be internal: never a single label, an address in any spelling or a numeric last label;
  never a special-use or private-use suffix (``localhost``, ``local``, ``test``, ``invalid``, ``example``, ``onion``,
  ``alt``, ``arpa`` including ``home.arpa``, ``internal``, ``lan``, ``corp``, ``home``, ``intranet``, ``private``,
  ``localdomain``); never under one of this machine's DNS search domains (``/etc/resolv.conf``, macOS ``scutil --dns``,
  Windows' TCP/IP and DNS client policy registry values, and the host name's own domain), read at most once a minute.
  When they cannot be read, no name goes to the proxy.
* A name is looked up once per download and keeps its route for every hop, so a later answer (a rebinding attempt)
  never swaps an address in.
* The card says, before anything runs, when this may happen: "If this computer can't look up <host>, your network's
  proxy finds its address instead, and Angelo can't check where it leads." It cannot say whether it will: that
  needs a lookup, and nothing touches the network before the person approves. The result's ``address_checked_by``
  is ``proxy`` when it did, else ``angelo``.

Otherwise a name this computer cannot look up reads as ``offline``.

Limits and checks
-----------------

* Size: at most 512 MB. A declared length over it is refused before any byte; a longer body is cut off
  (``too_large``).
* Time: 600 seconds for the whole transfer; 30 seconds of silence from the server ends it (``too_slow``,
  ``timeout``). Stop ends it too (``cancelled``). Both end whatever the transfer is waiting on (a connect, the TLS
  handshake, a header line, a read): a watch thread shuts the socket at the limit or at Stop
  (``download_fetch._Watch``), so a server that sends a byte now and then cannot hold it longer.
* Type: read from the file's bytes, never from the server's header or the link: ``image`` and ``texture`` take PNG,
  JPEG, GIF, WebP, TIFF, BMP, KTX2, DDS, OpenEXR and Radiance HDR; ``hdri`` OpenEXR and Radiance HDR; ``model`` GLB,
  glTF, FBX, OBJ, STL, PLY, USD (``.usdc``, ``.usda``, ``.usdz``), Alembic, Collada and ``.blend``; ``audio`` WAV, MP3,
  Ogg, FLAC, AIFF and M4A; ``other`` any data. For every kind a program or script (Windows, Linux and macOS
  executables, a ``#!`` script, a shortcut, an installer) and an HTML page (a sign-in or error page instead of the
  file) are refused (``wrong_type``). An answer other than 200 is ``http_<status>``; an unreachable host ``offline``,
  a certificate that does not verify ``tls``.

Only the person's approval runs it
----------------------------------

``download_tool.execute``, ``download_tool.precondition`` and ``download_fetch.download`` are grant entries
(``grant_guard.entry``, ``grant_operators.rst``): agent code that calls them directly is refused, whatever thread it is
on. The transfer runs on its own worker thread, started by the approved call, outside the script confinement.

Module ownership
----------------

==========================  ===================================================================================
Module                      Responsibility
==========================  ===================================================================================
``download_spec.py``        The schema, argument checks, limits and the guidance sent with the schema. No bpy.
``download_checks.py``      Address classes, the type read from bytes, safe names. No bpy, no network.
``download_fetch.py``       The pinned transfer, redirects, caps, the folder and placing the file. Worker thread.
``download_folders.py``     Which folder: beside the saved file, or the unsaved document's own. Main thread.
``download_names.py``       Which names the proxy may resolve; this machine's DNS search domains. No bpy.
``download_tool.py``        The host route: the folder, the worker, the pending result. Main thread.
``download_approvals.py``   The card's text (translated).
==========================  ===================================================================================

Tests: ``test_native_download_checks``, ``test_native_download_folders``, ``test_native_download_names``,
``test_native_download_fetch`` (a stand-in resolver and connections; no
network) and ``test_native_download_tool``; in the real window ``test_blender_download_card`` (the card in Ask and
Auto, a real Approve, the refusals).
