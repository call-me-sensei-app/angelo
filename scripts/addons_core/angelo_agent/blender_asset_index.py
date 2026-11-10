# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded records from Blender's Essentials listing, not a second remote library protocol.

Blender owns listing synchronization and asset transfers. This module reads its completed v1 listing on a worker,
checks paths/hashes/versions, and supplies opaque identities to the existing Angelo Library tools. No bpy here.
Only Essentials is admitted; a model cannot supply a URL, file path or an additional library.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import unicodedata
from urllib.parse import urljoin, urlsplit, unquote

PREFIX = 'blender:'
MAX_FILE = 512 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024
MAX_ITEMS, MAX_PAGES = 10000, 32
TYPES = {'OBJECT': ('objects', 'model'), 'COLLECTION': ('collections', 'model'),
         'MATERIAL': ('materials', 'material'), 'WORLD': ('worlds', 'world'),
         'NODETREE': ('node_groups', 'node_group'), 'BRUSH': ('brushes', 'brush')}


def text(value, limit=1000):
    if not isinstance(value, str):
        return ''
    return ''.join(c for c in value if unicodedata.category(c) not in {'Cc', 'Cf'})[:limit]


def relative(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or '\\' in value or ':' in value:
        raise ValueError('Invalid Essentials relative path')
    parts = value.split('/')
    if any(p in {'', '.', '..'} for p in parts) or text(value, 1024) != value:
        raise ValueError('Invalid Essentials relative path')
    return PurePosixPath(value)


def inside(root, rel):
    root = Path(root)
    path = root.joinpath(*relative(rel).parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Essentials file leaves its library')
    for parent in (path, *path.parents):
        if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
            raise ValueError('Linked Essentials path')
    return path


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'SHA256:[0-9a-fA-F]{64}', value):
        raise ValueError('Essentials file has no supported digest')
    return value[7:].lower()


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', value):
        raise ValueError('Invalid asset Blender version')
    return tuple(int(x) for x in value.split('.')[:2])


def compatible(bounds, running):
    return (isinstance(bounds, dict) and version(bounds['min']) <= tuple(running[:2])
            and ('until' not in bounds or tuple(running[:2]) < version(bounds['until'])))


def identity(origin, file, id_type, name, revision):
    data = json.dumps([origin, file, id_type, name, revision], ensure_ascii=False).encode('utf8')
    return PREFIX + hashlib.sha256(data).hexdigest()


def file_record(raw, root, base_url):
    path = inside(root, raw['path'])
    if path.suffix.lower() != '.blend' or type(raw['size_in_bytes']) is not int \
            or not 1 <= raw['size_in_bytes'] <= MAX_FILE:
        raise ValueError('Unsupported Essentials file')
    sha = digest(raw['hash'])
    url = urljoin(base_url, raw.get('url') or raw['path'])
    base, parts = urlsplit(base_url), urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc != base.netloc or parts.username or parts.password
            or parts.query or parts.fragment or not unquote(parts.path).startswith(base.path)):
        raise ValueError('Essentials download leaves its server')
    relative(unquote(parts.path[len(base.path):]))
    return {'path': str(path), 'relative': raw['path'], 'size': raw['size_in_bytes'], 'sha256': sha, 'url': url}


def record(raw, file, origin):
    name, id_type = raw['name'], raw['id_type']
    if id_type not in TYPES or not isinstance(name, str) or not name or text(name, 256) != name:
        raise ValueError('Unsupported Essentials asset')
    meta = raw.get('meta') or {}
    if not isinstance(meta, dict):
        raise ValueError('Invalid Essentials metadata')
    tags, properties = meta.get('tags') or [], meta.get('properties') or []
    if not isinstance(tags, list) or not isinstance(properties, list) or len(properties) > 128:
        raise ValueError('Invalid Essentials tags or properties')
    row = {'id': identity(origin, file['relative'], id_type, name, file['sha256']), 'name': name,
           'id_type': id_type, 'kind': TYPES[id_type][1], 'origin': origin, 'file': dict(file),
           'description': text(meta.get('description')), 'license': text(meta.get('license'), 160),
           'author': text(meta.get('author'), 160), 'tags': [text(t, 80) for t in tags[:64]]}
    traits = {p.get('name'): p.get('value') for p in properties if isinstance(p, dict)
              and p.get('type') == 'IDP_INT' and type(p.get('value')) is int and isinstance(p.get('name'), str)}
    row['node_type'] = {0: 'ShaderNodeTree', 1: 'CompositorNodeTree', 3: 'GeometryNodeTree'}.get(traits.get('type'), '')
    row['is_modifier'] = row['node_type'] == 'GeometryNodeTree' and bool(
        traits.get('geometry_node_asset_traits_flag', 0) & (1 << 6))  # DNA_node_types.h: GEO_NODE_ASSET_MODIFIER.
    row['search'] = ' '.join([name, row['description'], row['author'], *row['tags']]).casefold()
    return row


def _json(path, expected=None):
    with path.open('rb') as stream:
        data = stream.read(MAX_JSON + 1)
    if len(data) > MAX_JSON or (expected and hashlib.sha256(data).hexdigest() != digest(expected)):
        raise ValueError('Essentials index is oversized or changed')
    return json.loads(data)


def remote_rows(root, base_url, running):
    """Read only Blender's processed page list and its hash-checked completed pages. Never downloads payloads."""
    index = _json(inside(root, '_v1/asset-index.processed.json'))
    pages = index['pages']
    if not isinstance(pages, list) or len(pages) > MAX_PAGES:
        raise ValueError('Too many Essentials index pages')
    rows = []
    for ref in pages:
        page = _json(inside(root, ref['url']), ref['hash'])
        if len(page['assets']) > MAX_ITEMS or len(page['files']) > MAX_ITEMS:
            raise ValueError('Too many Essentials assets')
        files = {f['path']: f for f in page['files']}
        for raw in page['assets']:
            try:
                if len(raw['files']) != 1 or not compatible(raw['bl_versions'], running):
                    continue
                file = file_record(files[raw['files'][0]], root, base_url)
                row = record(raw, file, 'online')
                thumb = raw.get('thumbnail')
                if isinstance(thumb, dict):
                    try:
                        rel = relative(thumb['url']).as_posix()
                        if any(char in rel for char in '%?#') or PurePosixPath(rel).suffix.lower() not in {
                                '.png', '.jpg', '.jpeg', '.webp'}:
                            raise ValueError('Unsupported Essentials thumbnail path')
                        row['thumbnail'] = {'relative': rel, 'path': str(inside(root, rel)),
                                            'hash': digest(thumb['hash'])}
                    except (KeyError, TypeError, ValueError):
                        pass  # An invalid optional preview must not hide a usable asset.
                rows.append(row)
            except (KeyError, TypeError, ValueError):
                continue  # An unsupported asset does not hide compatible assets in the same listing.
            if len(rows) > MAX_ITEMS:
                raise ValueError('Too many Essentials assets')
    return rows


def verified(file, cancel=None):
    """Rehash on a worker before every append, including cached assets. Returns False for a missing/bad file."""
    path = Path(file['path'])
    try:
        if path.is_symlink() or path.stat().st_size != file['size']:
            return False
        sha, size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                if cancel is not None and cancel.is_set():
                    return False
                size += len(chunk)
                if size > file['size']:
                    return False
                sha.update(chunk)
        return sha.hexdigest() == file['sha256']
    except OSError:
        return False


def wait_for_file(file, cancel, queue_done):
    """Watch atomic native publication on a worker, independent of unrelated downloads in its shared queue.

    Hash only a new file stamp. A corrupt existing file must not be rehashed every poll. The final verification
    still applies to every import, and cancellation never changes another consumer's native transfer.
    """
    previous = object()
    while not cancel.is_set():
        try:
            stat = Path(file['path']).stat()
            stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        except OSError:
            stamp = None
        if stamp != previous:
            previous = stamp
            if verified(file, cancel):
                return True
        if queue_done.is_set():
            return verified(file, cancel)
        cancel.wait(0.1)
    return False


def search(rows, query, kind, limit, offset=0):
    words = query.casefold().split()
    found = [r for r in rows if (kind is None or r['kind'] == kind)
             and all(w in r['search'] for w in words)]
    found.sort(key=lambda r: (r['origin'] != 'bundled', r['name'].casefold(), r['id']))
    return found[offset:offset + limit], len(found)


def compact(row):
    return {key: row[key] for key in ('id', 'name', 'kind', 'id_type', 'origin', 'description', 'license', 'author')} | {
        'source': 'blender', 'title': row['name'],
        'preview_available': bool(row.get('thumbnail')),
        'mb': round(sum(f['size'] for f in row.get('files', (row['file'],))) / 1048576, 2),
        'downloaded': Path(row['file']['path']).is_file(),
        'node_tree_type': row.get('node_type', ''), 'can_apply_as_modifier': row.get('is_modifier', False),
        'usage': ('apply_material' if row['kind'] == 'material' else 'replace_world' if row['kind'] == 'world' else
                  'place_model' if row['kind'] == 'model' else 'load_datablock')}
