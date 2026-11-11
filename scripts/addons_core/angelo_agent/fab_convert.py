# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Convert an unpacked Fab download into a clean .blend in a sandboxed background Blender. Worker thread; no bpy.

Third-party files are read only by a child process (``fab_convert_child``), never by the app:

* The child is Angelo itself, started ``--background --factory-startup --disable-autoexec --offline-mode -noaudio``
  with a minimal environment: a fresh home, user configuration and scripts folders inside its work folder (so no
  person's add-on or startup script loads) and no Python path variables. Python scripts never auto-run.
* On macOS it runs under ``sandbox-exec``, denied by default: no network, writes only in its work folder, reads
  only the system's libraries and data, the app and that work folder. On Windows it sits in a kill-on-close Job
  Object; elsewhere it is isolated as a process only; its effects are bounded by the same flags and its work folder.
* It has a deadline (``TIMEOUT`` plus time per unpacked megabyte), runs at lowered priority, is killed when the
  person stops the import, and its result is a small JSON file whose fields are checked and whose error is a closed
  code, shown as a fixed sentence (``MESSAGES``). Its log never reaches the person or a model.

``cached`` keeps the result beside the download (``converted.blend`` and its receipt: download digest, converter
version, size and digest of the .blend), so a repeated add reuses it; the unpacked files are removed either way.
"""

import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from . import fab_archive, fab_formats, shared_replace
from .fab_catalog import FabError

VERSION = 1                       # Bump when the child's output changes: older cached conversions are redone.
TIMEOUT, SECONDS_PER_MIB = 120, 1.0
MAX_TIMEOUT = 900
MAX_RESULT = 64 * 1024
MAX_LOG = 64 * 1024
CHILD = Path(__file__).with_name('fab_convert_child.py')
MESSAGES = {
    'UNREADABLE': "Blender could not read this Fab file. Choose another format or quality of the listing.",
    'NO_OBJECTS': "This Fab file holds no model objects (only cameras, lights or helpers).",
    'OUTSIDE_REFERENCE': "This Fab file refers to files outside its own download, so Angelo did not read it.",
    'LINKED_LIBRARY': "This .blend links other .blend files, which Angelo does not follow. Choose another format.",
    'IMPORTER_MISSING': "This build of Angelo has no importer for this format.",
    'NO_MAPS': "None of this texture set's images is a material map Angelo recognises.",
    'TOO_LARGE': "This Fab file is too large to convert. Choose a lower quality.",
    'FAILED': "Angelo could not convert this Fab file. Choose another format or quality of the listing.",
    'TIMEOUT': "Converting this Fab file took too long. Choose a lower quality.",
    'SANDBOX': "Angelo could not start its file converter.",
}
NETWORK = ("This Fab file points at a file on another computer or a device, so Angelo did not open it. Choose "
           "another format of the listing.")
_binary = None


class ConvertRefused(FabError):
    """A refusal with one of this module's fixed sentences (never child or file text)."""

    def __init__(self, sentence):
        RuntimeError.__init__(self, sentence)
        self.code = 'UNSUPPORTED'


def register():
    """At the add-on's registration, on the main thread: the converter child is this app."""
    import bpy
    set_binary(bpy.app.binary_path)


def set_binary(path):
    """Main thread, at registration: the app's own executable (``bpy.app.binary_path``)."""
    global _binary
    _binary = str(path) if path else None


def _digest(path):
    digest, size = hashlib.sha256(), 0
    with shared_replace.open_file(path) as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _receipt(folder, download_digest):
    record, output = folder / 'converted.json', folder / 'converted.blend'
    try:
        if record.lstat().st_size > MAX_RESULT or not output.is_file() or output.is_symlink():
            return None
        with shared_replace.open_file(record) as handle:
            saved = json.loads(handle.read(MAX_RESULT + 1).decode('utf8'))
        if (not isinstance(saved, dict) or saved.get('version') != VERSION or saved.get('download') != download_digest
                or not isinstance(saved.get('result'), dict)):
            return None
        size, digest = _digest(output)
        if size != saved.get('bytes') or digest != saved.get('sha256'):
            return None
        return output, size, digest, saved['result']
    except (OSError, ValueError, UnicodeError):
        return None


def cached(folder, download, download_digest, fmt, facts, item, check, progress=None):
    """``(path, size, sha256, result)`` of ``asset.blend`` for this download, converting it once."""
    folder = Path(folder)
    hit = _receipt(folder, download_digest)
    if hit is not None:
        return hit[0], hit[1], hit[2], dict(hit[3], reused=True)
    work = folder / ('.convert-' + uuid.uuid4().hex[:12])
    work.mkdir(mode=0o700)
    try:
        source = work / 'files'
        if facts['container'] == 'zip':
            unpacked = fab_archive.extract(download, source, fmt, check)
            members = unpacked['members']
        else:
            source.mkdir(mode=0o700)
            name = 'model' + {'usdc': '.usdc', 'usda': '.usda', 'usdz': '.usdz', 'blend': '.blend', 'glb': '.glb',
                              'gltf': '.gltf'}.get(facts['content'], '.' + facts['content'])
            shutil.copyfile(download, source / name)
            members = [(name, facts['content'])]
        images = sorted(path for path, kind in members if kind in fab_formats.IMAGES)
        if fmt.importer == 'surface':
            models = []
            if not images:
                raise ConvertRefused(MESSAGES['NO_MAPS'])
        else:
            models = fab_formats.chosen_models(fmt, members)
        check()
        prescan(source, members)   # Before the child: no path in them may name another computer or a device.
        size = sum((source / path).stat().st_size for path, _kind in members)
        job = dict(format=fmt.code, folder=str(source), models=models, images=images, output=str(work / 'out.blend'),
                   datablock=item.title[:63], kind=fmt.kind)
        result = run(job, work, check, timeout=min(MAX_TIMEOUT, TIMEOUT + SECONDS_PER_MIB * size / (1024 * 1024)))
        output = work / 'out.blend'
        out_size, out_digest = _digest(output)
        if out_size > fab_formats.MAX_DOWNLOAD * 2:
            raise ConvertRefused(MESSAGES['TOO_LARGE'])
        record = work / 'receipt.json'
        _write_new(record, json.dumps({'version': VERSION, 'download': download_digest, 'bytes': out_size,
                                       'sha256': out_digest, 'result': result}))
        shared_replace.replace(output, folder / 'converted.blend', told=False)
        shared_replace.replace(record, folder / 'converted.json', told=False)
        return folder / 'converted.blend', out_size, out_digest, dict(result, reused=False)
    except fab_formats.Unsupported as refusal:
        raise ConvertRefused(str(refusal)) from None
    except fab_archive.ArchiveRefused as refusal:
        raise ConvertRefused(str(refusal)) from None
    except ConvertRefused:
        _keep_log(work, folder)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


_MTL_PATHS = (b'map_', b'bump', b'disp', b'decal', b'refl', b'norm')


def prescan(folder, members):
    """Refuse a download whose FBX texture paths or OBJ/MTL library and map paths name another computer or a device:
    on Windows merely opening one tries SMB sign-in, sending the person's NTLM hash to that host (re-review r2).
    The child scans USD's raw asset paths itself, and glTF URIs must stay in their folder (``gltf_checks``)."""
    from . import fab_convert_guard as guard, team_library_files
    for relative, kind in members:
        path = Path(folder) / relative
        try:
            if kind == 'fbx':
                team_library_files.fbx_paths_checked(path)
            elif kind in ('obj', 'mtl'):
                statements = (b'mtllib',) if kind == 'obj' else _MTL_PATHS
                with shared_replace.open_file(path) as handle:
                    for line in _statements(handle, joined=kind == 'obj'):
                        line = line.strip()
                        if line[:6].lower().startswith(statements) and any(
                                guard.network_or_device(token.decode('utf-8', 'replace')) for token in line.split()[1:]):
                            raise ConvertRefused(NETWORK)
        except team_library_files.Refused as refusal:
            # The FBX reader's own fixed sentences: another computer or device, or a malformed or unsupported file.
            raise ConvertRefused(NETWORK if 'another computer' in str(refusal) else str(refusal)) from None


_BLANK = bytes(range(0x21))   # Bytes <= 0x20, which Blender's OBJ reader skips after a continuation backslash.


def _statements(handle, joined):
    """Lines as the reader reads them. Blender's OBJ reader (``read_next_obj_line``) turns a backslash followed only
    by bytes <= 0x20 up to a newline into one space and reads on; any other backslash stays, and the file's last line
    without a newline does not continue (re-review r3 G2, r4). Its MTL reader joins nothing."""
    pending = b''
    for raw in handle:
        newline = raw.endswith(b'\n')
        line = raw[:-1] if newline else raw
        if joined and newline:
            trimmed = line.rstrip(_BLANK)
            if trimmed.endswith(b'\\'):
                pending += trimmed[:-1] + b' '
                continue
        yield pending + line
        pending = b''
    if pending:
        yield pending


def _short_name(path):
    """Windows' 8.3 short form of ``path`` (a long folder name shortened to six letters and ``~1``), or None."""
    if os.name != 'nt':
        return None
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.windll.kernel32.GetShortPathNameW(path, buffer, len(buffer))
        return buffer.value if 0 < size < len(buffer) else None
    except (OSError, AttributeError):
        return None


def _redacted(text, homes=None, short=None, fold=None):
    """The person's home folder, however a log spells it, becomes ``~`` (re-review r4, r5): as given, resolved and
    Windows' USERPROFILE and short name; with single, doubled (Python tracebacks) or mixed separators; in any case
    where the file system ignores case (macOS, Windows). Never a longer name that merely starts the same (a word
    character or '-' follows); any other character after it, punctuation included, ends the home folder (r6 R1)."""
    if homes is None:
        home = os.path.expanduser('~')
        homes = [home, os.path.realpath(home), os.environ.get('USERPROFILE', '')]
        short = _short_name(home)
    if fold is None:
        fold = sys.platform in ('win32', 'darwin')
    spellings = [h for h in {*homes, short or ''} if h and len(h.strip('/\\')) > 2]
    for home in sorted(spellings, key=len, reverse=True):
        parts = [part for part in re.split(r'[\\/]+', home) if part]
        leading = r'[\\/]+' if home[:1] in '/\\' else ''
        pattern = leading + r'[\\/]+'.join(re.escape(part) for part in parts) + r'(?![\w-])'
        text = re.sub(pattern, '~', text, flags=re.IGNORECASE if fold else 0)
    return text


def _keep_log(work, folder):
    """The failed child's last ``MAX_LOG`` bytes beside the download (``convert-failed.log``), for support: local and
    private like the cache, never shown to the person or a model, replaced by the next failure."""
    try:
        with shared_replace.open_file(work / 'child.log') as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - MAX_LOG))
            tail = handle.read(MAX_LOG)
        kept = work / 'failed.log'
        _write_new(kept, _redacted(tail.decode('utf8', 'replace')))
        shared_replace.replace(kept, folder / 'convert-failed.log', told=False)
    except OSError:
        pass


def _write_new(path, text):
    """A new file in the private work folder (exclusive creation; nothing reads or replaces it meanwhile)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags, 0o600), 'wb') as handle:
        handle.write(text.encode('utf8'))


def sandbox_profile():
    """The macOS sandbox for the child: denied by default (security review F3). It may run the app's own program,
    read the system's libraries and data, the app and its work folder, and write only its work folder; it has no
    network. Paths WORK and APP are passed with ``-D``, never formatted in. Mach lookups stay open: the system's own
    frameworks start through them, and a lookup reaches no file or network the profile denies."""
    return ('(version 1)(deny default)'
            '(allow process-exec (subpath (param "APP")))(allow process-fork)(allow signal (target self))'
            '(allow sysctl-read)(allow mach-lookup)'
            '(allow ipc-posix-shm-read* ipc-posix-shm-write-data ipc-posix-shm-write-create)'
            '(allow file-read-metadata)'
            '(allow file-read* (literal "/") (subpath "/System") (subpath "/usr/lib") (subpath "/usr/share")'
            ' (subpath "/private/var/db/timezone") (subpath "/dev") (subpath (param "APP")) (subpath (param "WORK")))'
            '(allow file-write* (subpath (param "WORK")) (literal "/dev/null"))'
            '(allow file-ioctl (subpath "/dev"))')


def command(binary, job_path, result_path, work, platform=sys.platform):
    base = [binary, '--background', '--factory-startup', '--disable-autoexec', '--offline-mode', '-noaudio',
            '--python-exit-code', '1', '--python', str(CHILD), '--', str(job_path), str(result_path)]
    if platform != 'darwin' or not Path('/usr/bin/sandbox-exec').exists():
        return base
    app = Path(binary).resolve()
    bundle = next((p for p in app.parents if p.suffix == '.app'), app.parent.parent)
    return ['/usr/bin/sandbox-exec', '-D', 'WORK=%s' % Path(work).resolve(), '-D', 'APP=%s' % bundle,
            '-p', sandbox_profile()] + base


def environment(work):
    home = Path(work) / 'home'
    for sub in ('config', 'scripts', 'datafiles', 'extensions', 'tmp'):
        (home / sub).mkdir(parents=True, exist_ok=True)
    keep = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'WINDIR')}
    return dict(keep, HOME=str(home), USERPROFILE=str(home), TMPDIR=str(home / 'tmp'), TEMP=str(home / 'tmp'),
                TMP=str(home / 'tmp'), BLENDER_USER_CONFIG=str(home / 'config'),
                BLENDER_USER_SCRIPTS=str(home / 'scripts'), BLENDER_USER_DATAFILES=str(home / 'datafiles'),
                BLENDER_USER_EXTENSIONS=str(home / 'extensions'))


def run(job, work, check, *, timeout):
    """Run the child for ``job``; its checked result, or raises ``ConvertRefused``/the stop's own error."""
    if not _binary:
        raise ConvertRefused(MESSAGES['SANDBOX'])
    job_path, result_path = Path(work) / 'job.json', Path(work) / 'result.json'
    # The child keeps the same deadline itself and ends when this process is gone (fab_convert_guard.watchdog).
    _write_new(job_path, json.dumps(dict(job, deadline_seconds=timeout, parent_pid=os.getpid())))
    options = {'stdin': subprocess.DEVNULL, 'cwd': str(work), 'env': environment(work)}
    log = open(Path(work) / 'child.log', 'wb')
    options.update(stdout=log, stderr=subprocess.STDOUT)
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW
    else:
        options['start_new_session'] = True
    try:
        process = subprocess.Popen(command(_binary, job_path, result_path, work), **options)
    except OSError:
        log.close()
        raise ConvertRefused(MESSAGES['SANDBOX']) from None
    bound = None
    try:
        if sys.platform == 'win32':
            bound = _kill_on_close_job(process)
        else:
            try:
                os.setpriority(os.PRIO_PROCESS, process.pid, 10)
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if time.monotonic() > deadline:
                raise ConvertRefused(MESSAGES['TIMEOUT'])
            check()
            time.sleep(0.1)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        log.close()
        _close_job(bound)
    return checked_result(result_path, job)


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


def _kill_on_close_job(process):
    """Windows: a Job Object that ends the child when its last handle closes, so an app that quits or crashes takes
    the converter with it. None when Windows refuses (a parent job forbidding nesting): the deadline then remains."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount',
                                                         'OtherOperationCount', 'ReadTransferCount',
                                                         'WriteTransferCount', 'OtherTransferCount')]

    class Extended(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', Counters), ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                    ('PeakJobMemoryUsed', ctypes.c_size_t)]
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = Extended()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (not kernel32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
                                             ctypes.sizeof(limits))
            or not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle)))):
        kernel32.CloseHandle(job)
        return None
    return (kernel32, job)


def _close_job(job):
    """Close the job's handle: a child still in it ends (kill on close); one that exited is unaffected."""
    if job is not None:
        kernel32, handle = job
        kernel32.CloseHandle(handle)


_COUNTS = ('objects', 'meshes', 'models', 'drivers_removed', 'texts_removed', 'script_nodes_removed',
           'external_removed', 'missing_textures', 'relinked_textures', 'outside_removed', 'trimmed_tiles',
           'single_view_images')


def checked_result(path, job):
    """The child's result with only known fields of known types; a refusal raises its fixed sentence."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_RESULT:
            raise ValueError
        raw = json.loads(shared_replace.read_text(path))
    except (OSError, ValueError, UnicodeError):
        raise ConvertRefused(MESSAGES['FAILED']) from None
    if not isinstance(raw, dict) or raw.get('ok') is not True:
        code = raw.get('error') if isinstance(raw, dict) else None
        raise ConvertRefused(MESSAGES.get(code if code in MESSAGES else 'FAILED'))
    expected = 'MATERIAL' if job['kind'] == 'material' else 'COLLECTION'
    if raw.get('id_type') != expected or not isinstance(raw.get('name'), str) or not 0 < len(raw['name']) <= 63:
        raise ConvertRefused(MESSAGES['FAILED'])
    result = {'name': raw['name'], 'id_type': expected}
    for key in _COUNTS:
        value = raw.get(key, 0)
        result[key] = value if type(value) is int and 0 <= value < 10 ** 7 else 0
    timings = raw.get('timings') if isinstance(raw.get('timings'), dict) else {}
    result['timings'] = {k: float(v) for k, v in timings.items()
                         if k in ('import', 'clean', 'surface', 'write') and type(v) in (int, float) and 0 <= v < 3600}
    unit = raw.get('unit_scale', 1.0)
    result['unit_scale'] = float(unit) if type(unit) in (int, float) and 0 < unit < 1e6 else 1.0
    surface = raw.get('surface')
    if isinstance(surface, dict):
        roles = surface.get('roles')
        result['surface'] = {
            'roles': sorted(r for r in roles if r in fab_surface_roles())[:16] if isinstance(roles, list) else [],
            'normal': surface.get('normal') if surface.get('normal') in ('gl', 'dx') else None}
    result['material_from_textures'] = raw.get('material_from_textures') is True
    return result


def fab_surface_roles():
    from .fab_surface import ROLES
    return ROLES
