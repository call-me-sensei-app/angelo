# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which of the person's add-ons are known to work on the Blender version an Angelo update is built on.

Contract: doc/angelo/updates_and_reports.rst, "Your add-ons and the update". The update card names the person's
enabled add-ons that are not yet known to work on the new version, and says what add-on safe start does if one stops
Angelo as it starts (app_identity.rst, "A start that did not finish"). "Known" rests only on real signals, never on a
list of add-on names:

* **Angelo's own load results**: the add-on loads in this Angelo now, and the update keeps the same Blender series
  (``MAJOR.MINOR``); or Angelo recorded it loading, at this add-on version, on the new version's series (the person
  ran that version before: ``angelo_addon_loads``). An add-on that failed as it loaded in this session is named with
  its cause.
* **The add-on's declared range**: an extension's ``blender_version_min``/``blender_version_max`` (and platforms),
  which Blender itself enforces (an extension outside its range does not load), and a legacy add-on's
  ``bl_info["blender"]`` minimum. Only a range the author closed (``blender_version_max``) past the new version counts
  as known: an open range ("4.2 and later") was written before that version existed.
* **The extension platform's data**: the repository's listing asked for the new Blender version (Blender's own query:
  ``platform``, ``blender_version``, ``python_version``; nothing about the person), which names the newest version of
  each extension compatible with it. A newer version than the installed one is an update to get first.

``judge`` gives each add-on one verdict; ``Summary`` orders them for the card. No bpy here:
tests/python/angelo_agent/test_native_addon_compat.py.
"""

from dataclasses import dataclass, field

WORKS, FAILING, UNSUPPORTED, UPDATE, UNKNOWN = "works", "failing", "unsupported", "update", "unknown"
ATTENTION_ORDER = (FAILING, UNSUPPORTED, UPDATE, UNKNOWN)   # The card names these, most pressing first.
MAX_NAMED = 3   # Names in the card's summary line; the rest are counted ("and 2 more").


def version_tuple(text):
    """A Blender-style version (``"4.2"``, ``"5.3.1"``) as three ints, as Blender's extension code parses it
    (missing parts are 0), or None for anything else. A tuple of ints is accepted as it is."""
    if isinstance(text, (tuple, list)):
        parts = list(text)
    elif isinstance(text, str) and text.strip():
        parts = text.strip().split(".")
    else:
        return None
    try:
        numbers = [int(part) for part in parts]
    except (TypeError, ValueError):
        return None
    if not numbers or len(numbers) > 3 or any(number < 0 for number in numbers):
        return None
    return tuple((numbers + [0, 0])[:3])


def version_text(version):
    """``(5, 3, 0)`` as ``"5.3.0"``; a two-part series as ``"5.3"``."""
    return ".".join(str(part) for part in version)


def series(version):
    return tuple(version[:2]) if version else None


@dataclass(frozen=True)
class Addon:
    """What Angelo knows about one enabled add-on of the person's (``addon_compat_live.collect``)."""

    module: str
    name: str
    version: tuple = ()           # The add-on's own version, () when it states none.
    extension: bool = False       # An extension (``blender_manifest.toml``), else a legacy add-on (``bl_info``).
    declared_min: tuple = None    # Blender versions it declares; max is exclusive, as Blender reads it.
    declared_max: tuple = None
    platforms: tuple = ()         # An extension's platforms; () means every platform.
    package: str = ""             # An extension's id on its repository ("" for a legacy add-on).
    remote: bool = False          # Installed from a remote repository whose listing Angelo may ask for.
    loaded: bool = False          # Loaded in this Angelo now.
    cause: str = ""               # Why it failed as it loaded in this session ("" when it did not fail).


@dataclass(frozen=True)
class Verdict:
    addon: Addon
    kind: str          # WORKS, FAILING, UNSUPPORTED, UPDATE or UNKNOWN.
    why: str = ""      # "same_series", "loaded_there", "declared", "too_new", "too_old", "platform", "newer",
    #                    "needs_update", "open_range", "no_range", "not_loaded", "base_unknown", or the failure cause.
    detail: str = ""   # The declared bound or the platform's version that ``why`` names.


def outside_range(addon, target, platform):
    """``(why, bound)`` when the add-on's own declaration excludes ``target`` or this platform, else None. Blender
    applies the same rules to an extension and does not load it outside them; a legacy add-on's minimum is a
    warning in Blender, but an add-on that says it needs a newer Blender uses what the older one lacks."""
    if addon.declared_min is not None and target < addon.declared_min:
        return "too_old", version_text(addon.declared_min)
    if addon.extension and addon.declared_max is not None and target >= addon.declared_max:
        return "too_new", version_text(addon.declared_max)
    if addon.extension and addon.platforms and platform and platform not in addon.platforms:
        return "platform", platform
    return None


def listed_version(listing, addon, target, platform):
    """The newest version of the add-on's package that the platform's ``listing`` says is compatible with
    ``target`` (an entry outside its own range is skipped, as Blender's client skips it), or None."""
    if not listing or not addon.package:
        return None
    best = None
    for entry in listing.get(addon.package, ()):
        candidate = Addon(addon.module, addon.name, entry.get("version", ()), True, entry.get("min"),
                          entry.get("max"), tuple(entry.get("platforms", ())))
        if outside_range(candidate, target, platform) is None and candidate.version and (
                best is None or candidate.version > best):
            best = candidate.version
    return best


def judge(addon, target, current, *, listing=None, loaded_there=(), platform=""):
    """The ``Verdict`` for ``addon`` on an update built on Blender ``target`` (None: not known), from this Angelo
    on Blender ``current``. ``listing``: ``{package: [entry, ...]}`` from the repository's listing for ``target``
    (``parse_listing``), or None when Angelo has none. ``loaded_there``: ``{(module, version)}`` Angelo recorded
    loading on ``target``'s series."""
    if addon.cause:
        return Verdict(addon, FAILING, addon.cause)
    if target is None:
        return Verdict(addon, UNKNOWN, "base_unknown")
    excluded = outside_range(addon, target, platform)
    newer = listed_version(listing, addon, target, platform)
    if newer is not None and addon.version and newer > addon.version:
        return Verdict(addon, UPDATE, "needs_update" if excluded else "newer", version_text(newer))
    if excluded is not None:
        return Verdict(addon, UNSUPPORTED, *excluded)
    if series(target) == series(current) and addon.loaded:
        return Verdict(addon, WORKS, "same_series")
    if (addon.module, version_text(addon.version) if addon.version else "") in loaded_there:
        return Verdict(addon, WORKS, "loaded_there")
    if addon.extension and addon.declared_max is not None:   # A range its author closed, past ``target``.
        return Verdict(addon, WORKS, "declared", version_text(addon.declared_max))
    if series(target) == series(current):
        return Verdict(addon, UNKNOWN, "not_loaded")   # Enabled but not loaded now (for a reason nobody recorded).
    return Verdict(addon, UNKNOWN, "open_range" if addon.declared_min is not None else "no_range",
                   version_text(addon.declared_min) if addon.declared_min is not None else "")


@dataclass(frozen=True)
class Summary:
    """The verdicts for one update: ``attention`` (not known to work, most pressing first, then by name) and how
    many are known to work."""

    target: tuple                  # Blender version of the update, or None.
    verdicts: tuple = field(default_factory=tuple)

    @property
    def attention(self):
        order = {kind: index for index, kind in enumerate(ATTENTION_ORDER)}
        return tuple(sorted((v for v in self.verdicts if v.kind != WORKS),
                            key=lambda v: (order[v.kind], v.addon.name.casefold())))

    @property
    def known(self):
        return sum(1 for v in self.verdicts if v.kind == WORKS)

    def named(self, limit=MAX_NAMED):
        """``(names, more)``: the first add-ons the card names and how many more there are."""
        attention = self.attention
        return [v.addon.name for v in attention[:limit]], max(0, len(attention) - limit)


def summarize(addons, target, current, *, listing=None, loaded_there=(), platform=""):
    return Summary(target, tuple(judge(addon, target, current, listing=listing, loaded_there=loaded_there,
                                       platform=platform) for addon in addons))


def parse_listing(document):
    """``{package: [{"version", "min", "max", "platforms"}, ...]}`` from a repository listing (Blender's
    ``index.json`` format, version ``v1``), skipping any entry that is not well formed. A remote server lists only
    versions compatible with the Blender it was asked for; a static listing may list several per package, which
    ``listed_version`` filters itself. None for a document that is not a listing."""
    if not isinstance(document, dict) or document.get("version") != "v1" or not isinstance(document.get("data"), list):
        return None
    listing = {}
    for item in document["data"]:
        if not isinstance(item, dict) or item.get("type", "add-on") != "add-on":
            continue
        package, version = item.get("id"), version_tuple(item.get("version"))
        minimum = version_tuple(item.get("blender_version_min"))
        maximum = version_tuple(item.get("blender_version_max")) if item.get("blender_version_max") else None
        platforms = item.get("platforms") or []
        if (not isinstance(package, str) or version is None or minimum is None or not isinstance(platforms, list)
                or (item.get("blender_version_max") and maximum is None)):
            continue
        listing.setdefault(package, []).append({"version": version, "min": minimum, "max": maximum,
                                                "platforms": [p for p in platforms if isinstance(p, str)]})
    return listing


def listing_query(remote_url, target, python_version, platform):
    """The repository URL with Blender's own listing query for ``target`` (``bl_extension_utils.
    url_append_query_for_blender``): only the platform and the versions, nothing about the person."""
    from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
    parts = urlsplit(remote_url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in {"platform", "blender_version", "python_version"}]
    query += [("platform", platform), ("blender_version", version_text(target)),
              ("python_version", version_text(python_version))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
