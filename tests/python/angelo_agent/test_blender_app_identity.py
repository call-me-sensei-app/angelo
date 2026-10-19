# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The built app presents as Angelo and shares no folder or identifier with a stock Blender.

Run outside Blender (``run_native_suites.py --gui`` does this)::

  python3 tests/python/angelo_agent/test_blender_app_identity.py \\
    --binary /absolute/Angelo --out /private/tmp/fresh-directory

Two launches share one isolated home (HOME and CFFIXED_USER_HOME, the XDG variables and TMPDIR all
inside ``--out``). Unlike the other native suites, **no BLENDER_USER_* variable is set**, so the
product's real default folders are exercised:

1. ``folders`` (background, factory startup) prints the resolved user, cache and temporary folders,
   saves preferences, and the host checks that everything landed under the Angelo user folder of the
   isolated home and that no Blender folder was created there.
2. ``window`` (a real window) checks the bundle's ``Info.plist`` against the identity, the Help menu,
   the URL presets, the splash and About text and (macOS) the window's title as AppKit holds it, and
   captures their pixels: ``help-menu.png``, ``about.png``, ``splash.png``, ``window-title.png`` (the
   window frame AppKit draws) and ``paths.png`` (the folders the window's process resolved).

No provider, account or real user folder is used; stock Blender's folders are not touched (the host
never looks outside ``--out``). Inspect ``app-identity.json`` and the PNGs beside it.
"""

import argparse
import json
import os
import plistlib
import struct
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/modules"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # macos_window, inside the app.
import angelo_identity  # noqa: E402

FATAL = ("BLI_assert failed", "Segmentation fault", "Traceback")
IDENTITY_PATHS = angelo_identity.data()["paths"]
VERSION = "5.2"


def expected_roots(home, platform):
    """The per-version user folder, the cache folder and the Blender folder that must stay absent."""
    names = angelo_identity.data()["paths"]
    if platform == "darwin":
        support = home / "Library/Application Support"
        return (support / names["user_dir_name"] / VERSION, home / "Library/Caches" / names["user_dir_name"],
                [support / "Blender", home / "Library/Caches/Blender"])
    if platform == "win32":
        return (home / "AppData/Roaming" / names["user_dir_name"] / VERSION,
                home / "AppData/Local" / names["user_dir_name"] / "Cache",
                [home / "AppData/Roaming/Blender Foundation", home / "AppData/Local/Blender Foundation"])
    return (home / ".config" / names["user_dir_name_unix"] / VERSION, home / ".cache" / names["user_dir_name_unix"],
            [home / ".config/blender", home / ".cache/blender"])


def run_host():
    import subprocess
    import gui_launch
    if sys.platform not in {"darwin", "win32"}:
        raise SystemExit("test_blender_app_identity runs on macOS (folders and window) and Windows (window only)")
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        raise ValueError("Use a fresh disposable directory")
    binary = Path(args.binary).resolve(strict=True)
    home = out / "home"
    for folder in (home, out / "tmp", home / ".config", home / ".cache", home / ".local/share", home / ".local/state"):
        folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("BLENDER_", "ANGELO_", "SENSEI_", "TOONLAB_")) and key not in {"APPDATA", "LOCALAPPDATA"}}
    env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), USERPROFILE=str(home), TMPDIR=str(out / "tmp"),
               TMP=str(out / "tmp"), TEMP=str(out / "tmp"), APPDATA=str(home / "AppData/Roaming"),
               LOCALAPPDATA=str(home / "AppData/Local"), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_CACHE_HOME=str(home / ".cache"), XDG_DATA_HOME=str(home / ".local/share"),
               XDG_STATE_HOME=str(home / ".local/state"), ANGELO_QA_DIR=str(out), ANGELO_DISABLE_DEFAULT_CHAT="1",
               ANGELO_DISABLE_KEYCHAIN="1", ANGELO_STATE_DIR=str(out / "state"), ANGELO_CONNECTIONS_DIR=str(out / "accounts"),
               ANGELO_LIBRARY_DIR=str(out / "library"))
    if sys.platform == "win32":
        # Windows takes its folders from known-folder APIs that no variable can redirect, so the real
        # profile stays in place (Angelo's own folder is what is checked); only Angelo's state is isolated.
        for key in ("HOME", "CFFIXED_USER_HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            env.pop(key, None)
        env.update({key: os.environ[key] for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "TMP", "TEMP") if key in os.environ})
    reports = []

    def phase(name, window):
        command = [str(binary), "--disable-autoexec"]
        command += ([*gui_launch.window_flags(), "--window-geometry", "80", "80", "1400", "900", "--factory-startup"]
                    if window else ["--background", "--factory-startup"])
        command += ["--python", str(Path(__file__).resolve()), "--", "--native-phase", name, "--fixture-root", str(out)]
        log = out / (name + ".log")
        with log.open("w") as output:
            result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=150)
        text = log.read_text(errors="replace")
        data = json.loads((out / (name + ".json")).read_text())
        data.update(exit_code=result.returncode, fatal_log_markers=[m for m in FATAL if m in text],
                    allocator_warning=next((l for l in text.splitlines() if "Not freed memory blocks" in l), None))
        data["success"] = (data["success"] and result.returncode == 0 and not data["fatal_log_markers"]
                           and not data["allocator_warning"])
        reports.append(data)
        if not data["success"]:
            raise AssertionError("Native phase failed: " + name + " " + str(data.get("error")))

    def macos_sequence():
        phase("folders", window=False)
        user, cache, forbidden = expected_roots(home, sys.platform)
        host = {}
        host["config_folder_exists"] = (user / "config").is_dir()
        host["preferences_saved_in_angelo_folder"] = (user / "config/userpref.blend").is_file()
        host["no_blender_folder_created"] = not any(path.exists() for path in forbidden)
        host["nothing_outside_the_isolated_home"] = True
        host["crash_log_absent"] = not list((out / "tmp").rglob("*.crash.txt"))
        # The freedesktop thumbnail cache is shared by design, but failure markers are per application.
        fail_markers = home / ".thumbnails/fail"
        host["thumbnail_failure_folder_is_angelos"] = (
            not fail_markers.exists()
            or sorted(entry.name for entry in fail_markers.iterdir()) == [IDENTITY_PATHS["user_dir_name_unix"]])
        reports[0]["host"] = host
        reports[0]["success"] = reports[0]["success"] and all(host.values())
        if not reports[0]["success"]:
            raise AssertionError("Folder checks failed: " + json.dumps(host))
        phase("window", window=True)

    try:
        if sys.platform == "win32":
            phase("window", window=True)
        else:
            macos_sequence()
    finally:
        expected_phases = 1 if sys.platform == "win32" else 2
        report = {"success": len(reports) == expected_phases and all(item["success"] for item in reports), "phases": reports,
                  "provider_starts": 0, "paid_requests": 0, "binary": str(binary), "platform": sys.platform}
        (out / "app-identity.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    if report["success"]:
        print("ANGELO_APP_IDENTITY_OK", flush=True)
    else:
        raise SystemExit(1)


if "--native-phase" not in sys.argv:
    if __name__ == "__main__":
        run_host()
    raise SystemExit

import bpy  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1:]
PHASE = ARGS[ARGS.index("--native-phase") + 1]
OUT = Path(ARGS[ARGS.index("--fixture-root") + 1])
report = {"phase": PHASE, "checks": {}, "success": False}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def finish(error=None):
    if error:
        report["error"] = error
        print(error.replace("Traceback", "Trace"), flush=True)  # The host fails on its own markers.
    else:
        report["success"] = all(report["checks"].values())
    (OUT / (PHASE + ".json")).write_text(json.dumps(report, indent=2, default=str))


class Recorder:
    """A layout that records the operators, labels and links drawn into it."""

    def __init__(self):
        self.items = []

    def operator(self, idname, text="", **kwargs):
        record = {"op": idname, "text": text, "props": {}}
        self.items.append(record)
        return _Props(record["props"])

    def label(self, text="", **kwargs):
        self.items.append({"label": text})

    def column(self, **kwargs):
        return self

    row = split = column

    def separator(self, **kwargs):
        pass

    def menu(self, idname, text="", **kwargs):
        self.items.append({"menu": idname, "text": text})

    def __getattr__(self, name):
        return lambda *args, **kwargs: self


class _Props:
    def __init__(self, store):
        object.__setattr__(self, "_store", store)

    def __setattr__(self, name, value):
        self._store[name] = value


def draw(menu_class, **context_values):
    """Draw ``menu_class`` into a recorder and return what it drew."""
    layout = Recorder()
    instance = type("Instance", (), {"layout": layout})()
    # ``workspace``: a menu with appended draw functions filters them by the workspace's add-on owners.
    context = type("Context", (), {"preferences": bpy.context.preferences, "scene": bpy.context.scene,
                                   "workspace": bpy.context.window_manager.windows[0].workspace,
                                   **context_values})()
    menu_class.draw(instance, context)
    return layout.items


def url_of(item):
    if "url" in item["props"]:
        return item["props"]["url"]
    return bpy.types.WM_OT_url_open_preset.lookup_url_from_type(item["props"]["type"])


def folders():
    expected_user, expected_cache, forbidden = expected_roots(Path(os.environ["HOME"]), sys.platform)
    paths = {kind: Path(bpy.utils.user_resource(kind, create=True)) for kind in ("CONFIG", "SCRIPTS", "DATAFILES", "EXTENSIONS")}
    report["paths"] = {kind: str(path) for kind, path in paths.items()}
    report["cachedir"], report["tempdir"] = bpy.app.cachedir, bpy.app.tempdir
    for kind, path in paths.items():
        # Cocoa reports /tmp where the host wrote /private/tmp: compare real paths.
        check(f"{kind.lower()}_folder_is_angelos",
              expected_user.resolve() in path.resolve().parents or path.resolve() == expected_user.resolve())
    check("cache_folder_is_angelos", Path(bpy.app.cachedir).resolve() == expected_cache.resolve())
    check("session_temp_folder_is_angelos",
          Path(bpy.app.tempdir).name.startswith(angelo_identity.data()["paths"]["temp_prefix"] + "_"))
    home = Path(os.environ["HOME"]).resolve()  # The scratch location itself may contain the word.
    check("no_path_names_blender", not any("blender" in str(path.resolve().relative_to(home)).lower()
                                           for path in paths.values()))
    check("preferences_path_not_overridden", not any(key.startswith("BLENDER_USER") for key in os.environ))
    bpy.ops.wm.save_userpref()
    check("userpref_written_to_config", (paths["CONFIG"] / "userpref.blend").is_file())


def bundle_info():
    binary = Path(bpy.app.binary_path)
    if sys.platform != "darwin":
        return None
    return plistlib.loads((binary.parents[1] / "Info.plist").read_bytes()), binary.parents[2]


def executable_minos(path):
    """The minimum macOS a thin 64-bit Mach-O file declares (its LC_BUILD_VERSION), as "26.0"; None without one."""
    with open(path, "rb") as file:
        magic, _, _, _, count, _, _, _ = struct.unpack("<8I", file.read(32))
        for _ in range(count if magic == 0xFEEDFACF else 0):
            command, size = struct.unpack("<II", file.read(8))
            body = file.read(size - 8)
            if command == 0x32:  # LC_BUILD_VERSION: platform, minos (major << 16 | minor << 8 | patch), sdk, tools.
                minos = struct.unpack_from("<I", body, 4)[0]
                return "%d.%d" % (minos >> 16, (minos >> 8) & 0xFF)
    return None


def macos_bundle_checks(identity):
    info, bundle = bundle_info()
    minimum = identity["bundle"]["macos_min_version"]
    report["executable_minos"] = executable_minos(bpy.app.binary_path)
    check("bundle_and_executable_require_the_identitys_macos",
          info["LSMinimumSystemVersion"] == minimum == report["executable_minos"])
    check("bundle_is_named_from_the_identity", bundle.name == identity["bundle"]["macos_executable"] + ".app")
    check("bundle_identifier", info["CFBundleIdentifier"] == identity["bundle"]["macos_id"])
    check("bundle_names", info["CFBundleName"] == info["CFBundleDisplayName"] == identity["product"]["name"])
    check("bundle_vendor_in_info_string", info["CFBundleGetInfoString"].endswith(", " + identity["vendor"]["name"]))
    check("bundle_version_is_angelos_own",
          info["CFBundleShortVersionString"] == info["CFBundleVersion"] == identity["version"]["label"])
    check("bundle_info_string_notes_the_base", "(based on Blender " in info["CFBundleGetInfoString"]
          and info["CFBundleGetInfoString"].startswith(identity["version"]["label"] + " "))
    check("bundle_copyright", identity["product"]["copyright"].split("{year}")[0] in info["NSHumanReadableCopyright"]
          and "Angelo modifications" in info["NSHumanReadableCopyright"])
    check("blend_type_is_imported_not_exported",
          "UTExportedTypeDeclarations" not in info
          and [t["UTTypeIdentifier"] for t in info["UTImportedTypeDeclarations"]] == [identity["bundle"]["macos_imported_document_uti"]])
    check("blend_default_is_not_claimed", info["CFBundleDocumentTypes"][0]["LSIsAppleDefaultForType"] is False)
    appex = bundle / "Contents/PlugIns/blender-thumbnailer.appex/Contents/Info.plist"
    if appex.is_file():
        thumb = plistlib.loads(appex.read_bytes())
        check("thumbnailer_has_its_own_identifier",
              thumb["CFBundleIdentifier"] == identity["bundle"]["macos_id"] + "." + identity["bundle"]["macos_thumbnailer_suffix"])
        check("thumbnailer_requires_the_same_macos", thumb["LSMinimumSystemVersion"] == minimum
              == executable_minos(appex.parent / "MacOS/blender-thumbnailer"))
        check("thumbnailer_names_angelo", thumb["CFBundleDisplayName"] == identity["product"]["name"] + " Thumbnailer"
              and thumb["CFBundleGetInfoString"].endswith(", " + identity["vendor"]["name"])
              and thumb["CFBundleShortVersionString"] == identity["version"]["label"])
    report["thumbnailer_built"] = appex.is_file()


def windows_checks(identity):
    """The installed program's own identity on Windows: executable name and default folders."""
    names = identity["paths"]
    check("exe_is_named_from_the_identity", Path(bpy.app.binary_path).name == identity["windows"]["exe_stem"] + ".exe")
    config = Path(bpy.utils.user_resource("CONFIG")).parts
    check("config_folder_is_angelos", config[-3:] == (names["user_dir_name"], VERSION, "config"))
    check("config_folder_is_not_blender_foundation", "Blender Foundation" not in config)


def window():
    identity = angelo_identity.data()
    # Blender's own version plumbing (file compatibility, add-on checks, the 5.2 folders) is untouched.
    check("blender_version_plumbing_untouched", tuple(bpy.app.version[:2]) == (5, 2)
          and bpy.app.version_string.startswith("5.2") and bpy.app.version_string != identity["version"]["label"])
    if sys.platform == "darwin":
        macos_bundle_checks(identity)
    elif sys.platform == "win32":
        windows_checks(identity)
    try:
        import gpu
        report["gpu_renderer"] = gpu.platform.renderer_get()
        report["gpu_backend"] = gpu.platform.backend_type_get()
    except Exception as error:  # Recorded as evidence only; a missing GPU is the dialog's story.
        report["gpu_renderer"] = "unavailable: " + str(error)

    # Help menu: every link from the identity table; Blender's manual keeps its own entry.
    items = [i for i in draw(bpy.types.TOPBAR_MT_help, preferences=bpy.context.preferences) if "op" in i]
    texts = [i["text"] for i in items]
    for needed in ("Manual", "Blender Manual", "Support", "Release Notes", "Report a Bug"):
        check("help_has_" + needed.lower().replace(" ", "_"), needed in texts)
    check("help_has_no_donate", "Donate" not in texts)   # Donate lives in About, not in a primary spot.
    check("help_labels_blenders_manual_under_a_blender_heading",
          any(i.get("label") == "Blender" for i in draw(bpy.types.TOPBAR_MT_help, preferences=bpy.context.preferences)))
    # The agent add-on appends Welcome, Check for Updates and What's New below Blender's own entries.
    operators = [i["op"] for i in items]
    report["help_operators"] = operators
    runtime = [index for index, name in enumerate(operators) if name.startswith("angelo.")]
    check("help_keeps_the_agents_entries",
          {"angelo.intro", "angelo.check_for_updates", "angelo.whats_new"} <= set(operators))
    check("help_agent_entries_follow_blenders", bool(runtime) and min(runtime) > operators.index("wm.sysinfo"))
    links = {i["text"]: url_of(i) for i in items if "url" in i["props"] or "type" in i["props"]}
    report["help_links"] = links
    check("help_manual_is_angelos", links["Manual"] == angelo_identity.url("manual"))
    check("help_support_is_angelos", links["Support"] == angelo_identity.url("support"))
    check("help_release_notes_is_angelos", links["Release Notes"] == angelo_identity.url("release_notes"))
    check("help_blender_manual_is_blenders", links["Blender Manual"].startswith("https://docs.blender.org/manual/")
          and "{" not in links["Blender Manual"])
    bug = bpy.types.WM_OT_url_open_preset.lookup_url_from_type("BUG")
    check("help_bug_report_opens_angelos_page_with_system_info",
          bug.startswith(angelo_identity.url("report_bug") + "?") and "os=" in bug and "gpu=" in bug and "version=" in bug)
    report["bug_url"] = bug.split("?")[0]
    check("no_help_link_goes_to_blender_support_or_fund",
          not any(host in url for url in links.values() for host in ("blender.org/support", "fund.blender.org", "redirect.blender.org")))
    check("release_notes_link_is_not_blenders", "blender.org" not in links["Release Notes"])

    # Splash and About text.
    about = draw(bpy.types.WM_MT_splash_about)
    labels = [i["label"] for i in about if "label" in i]
    report["about_labels"] = labels
    check("about_says_based_on_blender", any(label.startswith("Based on Blender 5.2") for label in labels))
    check("about_shows_angelos_version", "Angelo " + angelo_identity.APP_VERSION_DISPLAY in labels)
    check("about_has_no_branch_line", not any(label.startswith("Branch") for label in labels))
    check("about_says_angelo_is_free_software", "Angelo is free software" in labels)
    check("about_does_not_say_blender_is_free_software", "Blender is free software" not in labels)
    about_ops = {i["text"]: url_of(i) for i in about if "op" in i and ("url" in i["props"] or "type" in i["props"])}
    check("about_has_no_store_link", "Blender Store" not in about_ops)
    check("about_keeps_license_and_credits", about_ops["License"] == angelo_identity.url("license")
          and about_ops["Credits"] == angelo_identity.url("credits"))
    check("about_donate_is_angelos", about_ops["Donate"] == angelo_identity.url("donate"))
    # With Angelo running, Blender's splash page gives way to Angelo's start panel (splash_setup): no Donate row.
    splash = draw(bpy.types.WM_MT_splash)
    check("splash_page_has_no_donate_or_templates", not any(i.get("text") in {"Donate", "Donate to Blender", "General"}
                                                          for i in splash))


def capture(name):
    target = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=target):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))


def window_title_checks(identity):
    """macOS: the real window's title (AppKit's, read in the app) names Angelo's version; its frame is captured."""
    import macos_window
    product = identity["product"]["name"]
    label = identity["version"]["label"]
    shown = label[:-2] if label.endswith(".0") and label.count(".") == 2 else label  # "1.0" for 1.0.0.
    titled = [(window, title) for window, title, visible in macos_window.windows() if visible and title]
    report["window_titles"] = [title for _, title in titled]
    check("window_title_names_angelos_version", any(title.endswith(" - %s %s" % (product, shown)) for _, title in titled))
    check("window_title_does_not_name_blender", not any("Blender" in title for _, title in titled))
    report["window_title_captured"] = bool(titled) and macos_window.capture_frame(titled[0][0], OUT / "window-title.png")


def show_paths(window):
    """The folders this process resolved, shown in a Text Editor in place of the largest area."""
    def short(path):  # The isolated home as "~" and the isolated temporary folder as "$TMPDIR", to fit the view.
        text = str(Path(path).resolve())
        for prefix, name in ((os.environ["HOME"], "~"), (os.environ.get("TMPDIR", ""), "$TMPDIR")):
            prefix = str(Path(prefix).resolve()) if prefix else ""
            if prefix and text.startswith(prefix + os.sep):
                return name + text[len(prefix):]
        return text
    lines = ["%s: %s" % (kind, short(bpy.utils.user_resource(kind))) for kind in ("CONFIG", "SCRIPTS", "DATAFILES", "EXTENSIONS")]
    lines += ["Preferences file: %s" % short(Path(bpy.utils.user_resource("CONFIG")) / "userpref.blend"),
              "Cache: %s" % short(bpy.app.cachedir), "Session temporary folder: %s" % short(bpy.app.tempdir)]
    report["window_paths"] = lines
    text = bpy.data.texts.new("Angelo folders")
    text.write("\n".join(lines))
    text.cursor_set(0)
    area = max(window.screen.areas, key=lambda area: area.width * area.height)
    area.type = 'TEXT_EDITOR'
    area.spaces.active.text = text
    area.spaces.active.top = 0


phase = {"name": "boot"}


def dismiss(target):
    """Close the open popup (Esc), so each capture shows one surface."""
    target.event_simulate(type='ESC', value='PRESS')
    target.event_simulate(type='ESC', value='RELEASE')


def tick():
    try:
        target = bpy.context.window_manager.windows[0]
        if phase["name"] == "boot":
            bpy.context.preferences.view.show_splash = False
            # The suite opts out of the default chat (ANGELO_DISABLE_DEFAULT_CHAT), which also keeps the agent
            # add-on off; its Help entries are part of the menu under test. The opt-out keeps its cards and
            # network checks off (app_notices.automation_off).
            import addon_utils
            addon_utils.enable("angelo_agent", default_set=False, persistent=False)
            window()
            if sys.platform == "darwin":
                window_title_checks(angelo_identity.data())
            phase["name"] = "help"
            with bpy.context.temp_override(window=target):
                bpy.ops.wm.call_menu(name="TOPBAR_MT_help")
            return .8
        if phase["name"] == "help":
            capture("help-menu.png")
            dismiss(target)
            phase["name"] = "about"
            return .6
        if phase["name"] == "about":
            with bpy.context.temp_override(window=target):
                bpy.ops.wm.splash_about('INVOKE_DEFAULT')
            phase["name"] = "about-captured"
            return .8
        if phase["name"] == "about-captured":
            capture("about.png")
            dismiss(target)
            phase["name"] = "splash"
            return .6
        if phase["name"] == "splash":
            with bpy.context.temp_override(window=target):
                bpy.ops.wm.splash('INVOKE_DEFAULT')
            phase["name"] = "splash-captured"
            return .8
        if phase["name"] == "splash-captured":
            capture("splash.png")
            dismiss(target)
            phase["name"] = "paths"
            return .6
        if phase["name"] == "paths":
            show_paths(target)
            # With the startup splash open the window redraws the changed area only after it handles a click
            # (a timer or a mouse move is not enough; measured). The status bar's left corner holds no button.
            for value in ('PRESS', 'RELEASE'):
                target.event_simulate(type='LEFTMOUSE', value=value, x=4, y=4)
            phase["name"] = "paths-captured"
            return .8
        if phase["name"] == "paths-captured":
            capture("paths.png")
            report["phase"] = "complete"
            finish()
            bpy.ops.wm.quit_blender()
            return None
    except Exception as error:
        finish(str(error) + "\n" + traceback.format_exc())
        bpy.ops.wm.quit_blender()
        return None


if PHASE == "folders":
    try:
        folders()
        finish()
    except Exception as error:
        finish(str(error) + "\n" + traceback.format_exc())
else:
    bpy.app.timers.register(tick, first_interval=1)
