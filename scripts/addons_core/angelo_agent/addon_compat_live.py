# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's side of the update card's add-on check (``addon_compat``; doc/angelo/updates_and_reports.rst, "Your
add-ons and the update").

``summary_for(offer)`` is what the update card asks for: the person's enabled add-ons (not those that ship with
Angelo), judged for the Blender version the offer's release is built on (the signed feed's ``blender``; a release
of this line without it keeps this line's Blender series). The facts are read on the main thread, cheaply and only
again when the enabled add-ons change: each add-on's ``bl_info`` (Blender's cached add-on scan), an extension's
manifest, whether it loaded, this session's failures (``angelo_safe_start.failed_now``) and Angelo's load record from
the new version's own folder (``angelo_addon_loads``).

For an update to another Blender series, the listing of each remote repository the person's extensions came from is
asked for that version, once per session, on a worker thread: Blender's own query (platform, Blender and Python
versions), only with Blender's online access allowed and never for a repository that needs an access token, at most
16 MB. Until it answers, or when it cannot, the verdicts stand without it.
"""

import sys
import threading

import bpy

from . import addon_compat as compat, app_version, update_feed

LISTING_LIMIT = 16 * 1024 * 1024
LISTING_TIMEOUT = 20
_listings = {}      # (remote URL, target): parsed listing, or None (asked and failed); missing: not asked yet.
_asking = set()     # (remote URL, target) being asked now.
_lock = threading.Lock()
_cache = {"key": None, "summary": None}


def _target(offer):
    """The Blender version ``offer``'s release is built on, or None when the feed does not say and it is another
    line (a new line is where Angelo moves to a newer Blender)."""
    named = compat.version_tuple(offer.release.blender) if offer.release.blender else None
    if named is not None:
        return named
    if update_feed.line_of(offer.release.version) == update_feed.line_of(app_version.current()):
        return tuple(bpy.app.version)
    return None


def _person_addons():
    import angelo_safe_start
    shipped = angelo_safe_start.shipped_here(bpy)
    return [entry.module for entry in bpy.context.preferences.addons if not shipped(entry.module)]


def _repos():
    """``{repository module: repository}`` of the enabled extension repositories."""
    return {repo.module: repo for repo in bpy.context.preferences.extensions.repos if repo.enabled}


def _manifest(repo, package):
    import tomllib
    from pathlib import Path
    try:
        with open(Path(repo.directory) / package / "blender_manifest.toml", "rb") as handle:
            return tomllib.load(handle)
    except (OSError, ValueError):
        return {}


def _facts(module, repos, failed):
    import addon_utils
    addon_utils.modules(refresh=False)
    fake = addon_utils.addons_fake_modules.get(module)
    if fake is None:   # Enabled but not installed: there is nothing of it to load (Blender says so itself).
        return None
    info = addon_utils.module_bl_info(fake)
    loaded = bool(addon_utils.check(module)[1])
    name = info.get("name") or module
    version = compat.version_tuple(info.get("version") or ()) or ()
    if module.startswith("bl_ext."):
        _, repo_module, package = (module.split(".", 2) + ["", ""])[:3]
        repo = repos.get(repo_module)
        if repo is None:
            return None
        manifest = _manifest(repo, package)
        maximum = manifest.get("blender_version_max")
        platforms = manifest.get("platforms") or []
        return compat.Addon(
            module, manifest.get("name") or name, compat.version_tuple(manifest.get("version", "")) or version,
            extension=True, declared_min=compat.version_tuple(manifest.get("blender_version_min", "")),
            declared_max=compat.version_tuple(maximum) if maximum else None,
            platforms=tuple(p for p in platforms if isinstance(p, str)), package=package,
            remote=bool(repo.use_remote_url and repo.remote_url and not repo.use_access_token),
            loaded=loaded, cause=failed.get(module, ""))
    return compat.Addon(module, name, version, declared_min=compat.version_tuple(info.get("blender") or ()),
                        loaded=loaded, cause=failed.get(module, ""))


def _loaded_there(target):
    import angelo_addon_loads as loads
    path = loads.record_for_series(bpy.utils.user_resource('CONFIG'), compat.series(target))
    if path is None:
        return set()
    found_series, addons = loads.read(path)
    return addons if found_series == compat.series(target) else set()


def _may_ask():
    """Blender's own rule for its repositories: online access allowed (and not forced off)."""
    return bool(getattr(bpy.app, "online_access", False))


def _ask(url, target):
    """Fetch one repository's listing for ``target`` on a worker thread (once per session)."""
    key = (url, target)
    with _lock:
        if key in _listings or key in _asking:
            return
        _asking.add(key)

    def work():
        from . import app_http
        listing = None
        try:
            query = compat.listing_query(url, target, sys.version_info[:3], update_feed.platform_key())
            response = app_http.request("GET", query, headers={"Accept": "application/json"},
                                        timeout=LISTING_TIMEOUT, limit=LISTING_LIMIT)
            if response.status == 200:
                import json
                listing = compat.parse_listing(json.loads(response.body))
        except Exception as error:   # Offline, refused, not a listing: the verdicts stand without it.
            print("Angelo: add-on check could not read %s's listing: %s" % (url, type(error).__name__))
        with _lock:
            _listings[key] = listing
            _asking.discard(key)

    threading.Thread(target=work, name="angelo-addon-listing", daemon=True).start()
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=1.0)


def _poll():
    """Main thread: redraw the card once every listing asked for has answered."""
    with _lock:
        waiting = bool(_asking)
    try:
        from . import card_host
        card_host.refresh("update")
    except Exception:
        pass
    return 1.0 if waiting else None


def summary_for(offer):
    """The ``addon_compat.Summary`` for the version ``offer`` installs (cached until what it reads changes)."""
    import angelo_safe_start
    target, current = _target(offer), tuple(bpy.app.version)
    modules = tuple(_person_addons())
    failed = angelo_safe_start.failed_now()
    with _lock:
        answered = tuple(sorted(k for k in _listings if k[1] == target))
    key = (offer.release.version, target, modules, tuple(sorted(failed.items())), answered)
    if _cache["key"] == key:
        return _cache["summary"]
    repos = _repos()
    addons = [facts for facts in (_facts(module, repos, failed) for module in modules) if facts is not None]
    there = _loaded_there(target) if target is not None and compat.series(target) != compat.series(current) else set()
    platform = update_feed.platform_key()
    verdicts = []
    for addon in addons:
        listing = None
        if addon.remote and target is not None and compat.series(target) != compat.series(current) and _may_ask():
            url = repos[addon.module.split(".", 2)[1]].remote_url
            _ask(url, target)
            with _lock:
                listing = _listings.get((url, target))
        verdicts.append(compat.judge(addon, target, current, listing=listing, loaded_there=there, platform=platform))
    summary = compat.Summary(target, tuple(verdicts))
    _cache.update(key=key, summary=summary)
    return summary
