# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Crafted-file attacks on the descriptor-only .blend metadata scanner.

Runs inside Blender (the scanner is `_bpy._angelo_read.metadata_scan`). Run it on the change
build and on the previous build and diff the JSON: every row must be identical, or the change must refuse where
the previous build answered (never answer differently, never answer where the previous build refused).

  Angelo -b --factory-startup --python metadata_scan_attack.py -- BASE.blend WORKDIR OUT.json [GROUP...]

BASE.blend is an uncompressed Blender 5.2 file (DNA source). Every case runs with this build's
product limits (`angelo_agent.blend_file_listing.SCAN_LIMITS`) and with the change's limits
(block_count 32,000,000) so reader differences are separated from the limit change.
"""
import json
import os
import resource
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blend_craft as craft  # noqa: E402
from _bpy import _angelo_read as api  # noqa: E402

ARGS = sys.argv[sys.argv.index('--') + 1:]
BASE, WORK, OUT = ARGS[:3]
GROUPS = set(ARGS[3:])
os.makedirs(WORK, exist_ok=True)
base = craft.Base(BASE)

try:
    from angelo_agent.blend_file_listing import SCAN_LIMITS as PRODUCT
except ImportError:
    PRODUCT = None
CHANGE = {"decoded_bytes": 6 * 1024 ** 3, "input_bytes": 6 * 1024 ** 3,
          "block_count": 32_000_000, "block_bytes": 2 * 1024 ** 3,
          "dna_bytes": 8 * 1024 ** 2, "dna_entries": 100_000,
          "decoder_bytes": 64 * 1024 ** 2, "zstd_window_bytes": 32 * 1024 ** 2,
          "result_names": 4096}
SUMMARY = {"list": "", "offset": 0, "limit": 12, "summary_names": 12, "libraries_limit": 32}
results = {}


def scan_fd(fd, request=None, limits=None, seconds=5.0, during=None):
    token = api.metadata_cancel_token(seconds)
    worker = None
    if during:
        worker = threading.Thread(target=during, args=(token,))
        worker.start()
    start = time.perf_counter()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    out = api.metadata_scan(fd, dict(request or SUMMARY), dict(limits or CHANGE), token)
    elapsed = time.perf_counter() - start
    if worker:
        worker.join()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    objects = out['types'].get('objects') or {}
    return {'status': out['status'], 'message': out['message'],
            'objects': [objects.get('total'), objects.get('names')],
            'types': sorted((k, v['total']) for k, v in out['types'].items() if v['total']),
            'libraries': out['libraries'], 'version': list(out.get('version') or ()),
            'decoded': out['decoded_bytes'], 'input': out['input_bytes'],
            'blocks': out['blocks'], 'passes': out['passes'],
            'seconds': round(elapsed, 3), 'max_rss_growth_mb': (rss_after - rss) // (1024 * 1024)}


def scan_path(path, request=None, limits=None, **kw):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    try:
        return scan_fd(fd, request, limits, **kw)
    finally:
        os.close(fd)


def case(group, name, path, request=None, both=True, **kw):
    if GROUPS and group not in GROUPS:
        return
    row = {'change_limits': scan_path(path, request, CHANGE, **kw)}
    if both and PRODUCT is not None:
        row['product_limits'] = scan_path(path, request, PRODUCT, **kw)
    results[f'{group}/{name}'] = row
    print(group, name, {k: (v['status'], v['message'], v['blocks'], v['seconds'])
                        for k, v in row.items()}, flush=True)


def p(name):
    return os.path.join(WORK, name)


def objects_file(names, block=craft.large, data_between=0):
    out = craft.file_start(base, block)
    for i, name in enumerate(names):
        if data_between:
            out += block('DATA', b'\0' * data_between)
        out += craft.object_block(base, name, block)
    out += craft.endb(block)
    return bytes(out)


# --- Baseline and codecs ---------------------------------------------------------------------
case('base', 'real_file', BASE)
plain = objects_file([f'Obj{i:04d}' for i in range(300)], data_between=100)
case('base', 'plain', craft.write(p('plain.blend'), plain))
case('base', 'zstd', craft.write(p('plain_zstd.blend'), craft.zstd_compress(plain)))
case('base', 'gzip', craft.write(p('plain_gz.blend'), craft.gzip_compress(plain)))
case('base', 'deep_page', p('plain.blend'),
     request={"list": "objects", "offset": 150, "limit": 20, "summary_names": 12,
              "libraries_limit": 32})

# --- Window borders: headers and names straddling every offset of the 64 KiB window ----------
for block, label in ((craft.large, 'large'), (craft.small, 'small')):
    hsize = craft.LARGE.size if block is craft.large else craft.SMALL.size
    for shift in list(range(0, 40)) + [hsize // 2, 300, 330]:
        out = craft.file_start(base, block)
        names = []
        for border in range(1, 7):
            target = border * craft.WINDOW - shift
            pad = target - len(out) - hsize
            if pad < 0:
                continue
            out += block('DATA', b'\x01' * pad)
            names.append(f'Border{border}_{shift}')
            out += craft.object_block(base, names[-1], block)
        out += craft.endb(block)
        case('straddle', f'{label}_{shift}', craft.write(p(f'straddle_{label}_{shift}.blend'), bytes(out)),
             request={"list": "objects", "offset": 1, "limit": 64, "summary_names": 12,
                      "libraries_limit": 32})

# --- Lengths: overflow, negative, past EOF, exactly at EOF, zero ----------------------------
for label, length in (('int64_max', 2 ** 63 - 1), ('neg1', -1), ('int64_min', -2 ** 63),
                      ('2GiB', 2 * 1024 ** 3), ('2GiB_plus1', 2 * 1024 ** 3 + 1),
                      ('uint32_wrap', 2 ** 32 - 1), ('past_eof', 10 ** 6), ('one_past', None),
                      ('to_eof', 'eof')):
    out = craft.file_start(base)
    out += craft.object_block(base, 'Before')
    if length is None or length == 'eof':
        tail = craft.endb()
        rest = len(tail) if length == 'eof' else len(tail) + 1
        out += craft.LARGE.pack(b'DATA', 0, 0, rest, 1) + tail
    else:
        out += craft.LARGE.pack(b'DATA', 0, 0, length, 1) + b'\0' * 64 + craft.endb()
    case('length', label, craft.write(p(f'len_{label}.blend'), bytes(out)))
for label, (sdna, nr) in {'sdna_neg': (-1 & 0xFFFFFFFF, 1), 'nr_neg': (0, -1), 'nr_zero_id': (None, 0)}.items():
    out = craft.file_start(base)
    payload = craft.object_payload(base, 'X')
    out += craft.LARGE.pack(b'OB\0\0', base.ob[0] if sdna is None else sdna, 0, len(payload), nr) + payload
    out += craft.endb()
    case('length', label, craft.write(p(f'hdr_{label}.blend'), bytes(out)))
out = craft.file_start(base)
out += craft.large('OB', b'', sdna=base.ob[0]) + craft.endb()
case('length', 'id_len_zero', craft.write(p('id_len_zero.blend'), bytes(out)))
out = craft.file_start(base)
for i in range(20000):
    out += craft.large('DATA', b'')
out += craft.object_block(base, 'AfterZeros') + craft.endb()
case('length', 'zero_len_data_20000', craft.write(p('zero_len.blend'), bytes(out)))

# --- Truncation at every byte of the last window and of a straddling header -----------------
full = objects_file([f'T{i:03d}' for i in range(40)], data_between=3000)
cut_points = sorted({len(full) - k for k in range(1, 70)} | {craft.WINDOW + k for k in range(-40, 40)})
for cut in cut_points:
    case('truncate', f'at_{cut}', craft.write(p(f'trunc_{cut}.blend'), full[:cut]), both=False)
case('truncate', 'endb_code_only', craft.write(p('endb4.blend'), bytes(craft.file_start(base)) + b'ENDB'))
case('truncate', 'trailing_garbage', craft.write(p('trail.blend'), full + os.urandom(200000)))

# --- Byte order and pointer size ------------------------------------------------------------
be = bytearray(objects_file(['A']))
be[16:17] = b'2'
be[12:13] = b'V'
case('format', 'big_endian_marker', craft.write(p('big_endian.blend'), bytes(be)))
small_file = objects_file([f'S{i}' for i in range(50)], block=craft.small, data_between=1300)
case('format', 'small_bhead8', craft.write(p('small_bhead8.blend'), small_file))
b4 = bytearray(small_file)
b4[7:8] = b'_'
case('format', 'pointer4_header_with_8_byte_blocks', craft.write(p('ptr4.blend'), bytes(b4)))

# --- Block counts near and above the limits; deadline with expensive blocks ------------------
for blocks in (1_999_000, 2_100_000, 31_000_000, 32_500_000):
    path = craft.sparse_zero_blocks(p(f'zero_{blocks}.blend'), base, blocks, objects=['Last'])
    case('count', f'plain_{blocks}', path)
with open(p('zero_31000000.blend'), 'rb') as f:
    import zstandard
    zpath = p('zero_31000000_zstd.blend')
    with open(zpath, 'wb') as z:
        zstandard.ZstdCompressor(level=1).copy_stream(f, z)
case('count', 'zstd_31000000', zpath)
deep = {"list": "objects", "offset": 0, "limit": 4000, "summary_names": 12, "libraries_limit": 32}
names = craft.file_start(base)
one = craft.object_block(base, 'N' * 60)
names += one * 1_000_000 + craft.endb()
case('count', 'objects_1000000_capacity_4000', craft.write(p('objects_1m.blend'), bytes(names)),
     request=deep)

# --- Codec attacks ---------------------------------------------------------------------------
import zstandard  # noqa: E402
zplain = craft.zstd_compress(plain)
case('codec', 'zstd_truncated', craft.write(p('z_trunc.blend'), zplain[:-7]))
case('codec', 'zstd_garbage_after', craft.write(p('z_garbage.blend'), zplain + os.urandom(64)))
flipped = bytearray(zplain)
flipped[len(flipped) // 2] ^= 0xFF
case('codec', 'zstd_corrupt_middle', craft.write(p('z_corrupt.blend'), bytes(flipped)))
with open(p('z_bomb.blend'), 'wb') as f:
    c = zstandard.ZstdCompressor(level=1).compressobj()
    f.write(c.compress(bytes(craft.file_start(base))))
    zero = b'\0' * (64 * 1024 * 1024)
    for _ in range(7):  # 7 x 1 GiB DATA blocks of zeros: the decoded limit (6 GiB) must stop it
        f.write(c.compress(craft.large('DATA', length=1024 ** 3)))
        for _ in range(16):
            f.write(c.compress(zero))
    f.write(c.compress(craft.endb()))
    f.write(c.flush())
case('codec', 'zstd_bomb_7GiB', p('z_bomb.blend'))
big_window = zstandard.ZstdCompressor(compression_params=zstandard.ZstdCompressionParameters(
    window_log=30, enable_ldm=True)).compress(plain + b'\0' * (64 * 1024 * 1024))
case('codec', 'zstd_window_1GiB', craft.write(p('z_window.blend'), big_window))
skippable = b'\x50\x2a\x4d\x18' + (512 * 1024 * 1024).to_bytes(4, 'little')
case('codec', 'zstd_skippable_claims_512MiB', craft.write(p('z_skip.blend'), skippable + zplain))
case('codec', 'gzip_truncated', craft.write(p('g_trunc.blend'), craft.gzip_compress(plain)[:-9]))

# --- Not a regular file ----------------------------------------------------------------------
if not GROUPS or 'special' in GROUPS:
    fifo = p('fifo.blend')
    if os.path.exists(fifo):
        os.unlink(fifo)
    os.mkfifo(fifo)
    fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    results['special/fifo'] = {'change_limits': scan_fd(fd)}
    os.close(fd)
    a, b = socket.socketpair()
    a.sendall(open(p('plain.blend'), 'rb').read()[:4096])
    results['special/socket'] = {'change_limits': scan_fd(a.fileno())}
    a.close()
    b.close()
    fd = os.open('/dev/zero', os.O_RDONLY)
    results['special/dev_zero'] = {'change_limits': scan_fd(fd)}
    os.close(fd)
    r, w = os.pipe()
    results['special/pipe'] = {'change_limits': scan_fd(r)}
    os.close(r)
    os.close(w)
    for key in ('special/fifo', 'special/socket', 'special/dev_zero', 'special/pipe'):
        print(key, results[key]['change_limits']['status'], results[key]['change_limits']['message'],
              flush=True)

# --- The descriptor changing while it is scanned (same inode) ---------------------------------
if not GROUPS or 'toctou' in GROUPS:
    big = p('zero_31000000.blend')
    if not os.path.exists(big):
        craft.sparse_zero_blocks(big, base, 31_000_000, objects=['Last'])
    size = os.path.getsize(big)

    def mutate(kind):
        def run(token):
            time.sleep(0.05)
            if kind == 'shrink':
                os.truncate(work, size // 2)
            elif kind == 'grow':
                with open(work, 'ab') as f:
                    f.write(craft.object_block(base, 'Grown') * 1000)
            elif kind == 'overwrite':
                with open(work, 'r+b') as f:
                    f.seek(size // 2)
                    f.write(craft.large('DATA', length=2 ** 40))
            elif kind == 'replace':
                os.replace(p('plain.blend.copy'), work)
            elif kind == 'cancel':
                api.metadata_cancel(token)
        return run

    for kind in ('shrink', 'grow', 'overwrite', 'replace', 'cancel'):
        work = p(f'toctou_{kind}.blend')
        os.system(f'cp -c "{big}" "{work}" 2>/dev/null || cp "{big}" "{work}"')
        with open(p('plain.blend'), 'rb') as src, open(p('plain.blend.copy'), 'wb') as dst:
            dst.write(src.read())
        fd = os.open(work, os.O_RDONLY | os.O_NONBLOCK)
        try:
            row = scan_fd(fd, during=mutate(kind))
        finally:
            os.close(fd)
        results[f'toctou/{kind}'] = {'change_limits': row}
        print('toctou', kind, row['status'], row['message'], row['blocks'], row['seconds'], flush=True)
        os.unlink(work)

with open(OUT, 'w') as f:
    json.dump(results, f, indent=1)
print('wrote', OUT, len(results), 'cases')
