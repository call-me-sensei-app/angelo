# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one table of Fab downloads Angelo can add: how each is recognised, unpacked and imported.

Everything format-specific about a Fab file is a row of ``FORMATS``: the listing's format labels it answers to
(``labels``), the content kinds of its model file (``members``), whether it may arrive as a zip (``archive``) or
one bare file (``bare``), its side files (``sidecars``, or for glTF the files its URIs name), the importer that reads it (``importer``) and what it
becomes (``kind``: a model or a material). Nothing reads a file name to decide what a file is: ``sniff`` reads
its first bytes, and a name only says which listing row a person picked.

Fab's own measurements (2026-11-11, 427 model listings, ``doc/angelo/fab_formats.rst``) order the table: a
converted GLB first (it is the only format imported without the converter), then FBX, .blend, OBJ, glTF, USD.

Standard library only; no bpy. The converter child imports this module as a plain sibling file.
"""

from dataclasses import dataclass
import struct
import zlib

MiB = 1024 * 1024


@dataclass(frozen=True)
class Format:
    code: str               # Angelo's own name for it; stored in cache receipts.
    label: str              # Shown to people ("FBX", ".blend").
    kind: str               # 'model' or 'material'.
    labels: frozenset       # Fab's row badge / format code texts that name this format (lower case).
    members: frozenset      # Content kinds (``sniff``) of the file the importer opens.
    importer: str           # 'gltf', 'fbx', 'obj', 'usd', 'blend' or 'surface'.
    archive: bool           # May come as a zip of the model and its side files.
    bare: bool              # May come as the model file alone.
    sidecars: frozenset     # Content kinds kept beside the model (images are always kept for models).
    direct: bool = False    # Imported in the app without the converter (self-contained GLB only).
    named_sidecars: bool = False   # Side files are the ones the model's own URIs name (glTF buffers have no magic).


IMAGES = frozenset({'png', 'jpeg', 'webp', 'exr', 'tiff', 'tga', 'bmp', 'hdr'})

FORMATS = (
    Format('glb', 'GLB', 'model', frozenset({'glb'}), frozenset({'glb'}), 'gltf', False, True, frozenset(), True),
    Format('fbx', 'FBX', 'model', frozenset({'fbx'}), frozenset({'fbx'}), 'fbx', True, True, frozenset()),
    Format('blend', '.blend', 'model', frozenset({'blend', 'blender'}), frozenset({'blend'}), 'blend', True, True,
           frozenset()),
    Format('obj', 'OBJ', 'model', frozenset({'obj'}), frozenset({'obj'}), 'obj', True, True, frozenset({'mtl'})),
    Format('gltf', 'glTF', 'model', frozenset({'gltf'}), frozenset({'gltf', 'glb'}), 'gltf', True, True,
           frozenset(), named_sidecars=True),
    Format('usd', 'USD', 'model', frozenset({'usd', 'usdz', 'usdc', 'usda'}),
           frozenset({'usdc', 'usda', 'usdz'}), 'usd', True, True, frozenset({'usdc', 'usda'})),
    Format('surface', 'Texture set', 'material', frozenset({'texture-set', 'texture set', 'texture_set'}),
           IMAGES, 'surface', True, False, frozenset()),
)
BY_CODE = {row.code: row for row in FORMATS}
RANK = {row.code: index for index, row in enumerate(FORMATS)}

MAX_DOWNLOAD = 256 * MiB        # One Fab file, as the converted-GLB slice already allowed.
MAX_MODELS = 32                 # Model files imported from one archive (a pack); more is refused, never cut.


class Unsupported(ValueError):
    """A fixed sentence for people; never file or listing text."""


def for_label(label):
    """The format a Fab row badge or format code names, or None (engine formats like Unreal are not imported)."""
    if type(label) is not str:
        return None
    label = label.strip().lower()
    return next((row for row in FORMATS if label in row.labels), None)


def kind_of_content(head, size=None):
    """``sniff`` without raising: '' for anything not recognised."""
    try:
        return sniff(head, size)
    except Unsupported:
        return ''


def sniff(head, size=None):
    """The content kind of a file from its first bytes (64 KiB is plenty); raises ``Unsupported`` with a reason for
    a known kind Angelo never reads (ASCII FBX), and returns '' for an unknown one."""
    if head.startswith(b'glTF'):
        return 'glb'
    if head.startswith(b'Kaydara FBX Binary  \x00\x1a\x00'):
        return 'fbx'
    if head.startswith(b'; FBX') or head.lstrip(b'\xef\xbb\xbf \t\r\n').startswith(b'; FBX'):
        raise Unsupported('This FBX file is in the text (ASCII) form, which Blender cannot read. '
                          'Choose another format of this listing.')
    if head.startswith(b'BLENDER') or _compressed_blend(head):
        return 'blend'
    if head.startswith(b'PXR-USDC'):
        return 'usdc'
    if head.startswith(b'#usda '):
        return 'usda'
    if head.startswith(b'PK\x03\x04') or head.startswith(b'PK\x05\x06'):
        return 'usdz' if _first_zip_member(head).lower().endswith((b'.usdc', b'.usda', b'.usd')) else 'zip'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    if head.startswith(b'RIFF') and head[8:12] == b'WEBP':
        return 'webp'
    if head.startswith(b'v/1\x01'):
        return 'exr'
    if head.startswith((b'II*\x00', b'MM\x00*')):
        return 'tiff'
    if head.startswith((b'#?RADIANCE', b'#?RGBE')):
        return 'hdr'
    if head.startswith(b'BM') and len(head) >= 26 and struct.unpack_from('<I', head, 2)[0] == size:
        return 'bmp'
    if _looks_like_tga(head, size):
        return 'tga'
    stripped = head.lstrip(b'\xef\xbb\xbf \t\r\n')
    if stripped[:1] == b'{' and b'"asset"' in head:
        return 'gltf'
    if _looks_like_mtl(head):
        return 'mtl'
    if _looks_like_obj(head):
        return 'obj'
    return ''


def _compressed_blend(head):
    """Blender saves compressed .blend files as Zstandard (current) or gzip (before 3.0). A gzip stream is a .blend
    only when it inflates to Blender's header; the standard library has no Zstandard reader, so a Zstandard stream
    is a candidate the converter's own reader confirms or refuses."""
    if head.startswith(b'\x28\xb5\x2f\xfd'):
        return True
    if not head.startswith(b'\x1f\x8b'):
        return False
    try:
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(head, 16).startswith(b'BLENDER')
    except zlib.error:
        return False


def _first_zip_member(head):
    if len(head) < 30 or not head.startswith(b'PK\x03\x04'):
        return b''
    length = struct.unpack_from('<H', head, 26)[0]
    return head[30:30 + length]


def _looks_like_tga(head, size):
    """TGA has no magic: accept only a header whose every field is a legal uncompressed/RLE image description."""
    if len(head) < 18 or size is None:
        return False
    id_length, colour_map, image_type = head[0], head[1], head[2]
    width, height = struct.unpack_from('<HH', head, 12)
    depth, descriptor = head[16], head[17]
    return (colour_map in (0, 1) and image_type in (1, 2, 3, 9, 10, 11) and 0 < width <= 16384
            and 0 < height <= 16384 and depth in (8, 15, 16, 24, 32) and descriptor & 0xC0 == 0
            and size >= 18 + id_length + (width * height if image_type in (1, 2, 3) else 0))


_OBJ = (b'#', b'v ', b'vt ', b'vn ', b'vp ', b'f ', b'l ', b'p ', b'o ', b'g ', b's ', b'mg ', b'mtllib ',
        b'usemtl ', b'cstype ', b'deg ', b'curv ', b'surf ', b'parm ', b'end')
_MTL = (b'#', b'newmtl ', b'ka ', b'kd ', b'ks ', b'ke ', b'ns ', b'ni ', b'd ', b'tr ', b'tf ', b'illum ',
        b'map_', b'bump ', b'disp ', b'decal ', b'refl ', b'norm ', b'pr ', b'pm ', b'ps ', b'pc ', b'pcr ',
        b'aniso', b'sharpness ')


def _lines(head):
    lines = [line.strip() for line in head.split(b'\n')]
    if len(head) >= 65536:
        lines = lines[:-1]   # The last line may be cut.
    return [line for line in lines if line][:200]


def _looks_like_obj(head):
    lines = _lines(head)
    return (bool(lines) and all(line.startswith(_OBJ) or line in (b'end',) for line in lines[:60])
            and any(line.startswith(b'v ') for line in lines))


def _looks_like_mtl(head):
    lines = _lines(head)
    lowered = [line.lower() for line in lines[:60]]
    return (bool(lines) and all(line.startswith(_MTL) for line in lowered)
            and any(line.startswith(b'newmtl ') for line in lowered))


def keep_member(fmt, kind):
    """Whether an archive member of content ``kind`` is extracted for format ``fmt``: its model files, side files and
    (for a model) its textures. Everything else (read-me files, other formats, nested archives) stays packed."""
    return kind in fmt.members or kind in fmt.sidecars or (kind in IMAGES and kind != '')


def chosen_models(fmt, members):
    """The model files to import from ``members`` (``[(relative path, kind)]``): every file of the format's model
    kinds, with Level-of-Detail siblings reduced to their most detailed level (``_LOD0``), as engines name them.

    A pack keeps all its models (up to ``MAX_MODELS``). Raises ``Unsupported`` when there is none or too many.
    """
    models = sorted(path for path, kind in members if kind in fmt.members and fmt.importer != 'surface')
    # USD layers that another kept layer composes are dropped by the converter, which reads the references.
    models = _most_detailed(models)
    if not models:
        raise Unsupported('This download has no %s model Angelo can read.' % fmt.label)
    if len(models) > MAX_MODELS:
        raise Unsupported('This download holds %d %s models; Angelo adds up to %d at once. Choose a smaller part '
                          'of the listing.' % (len(models), fmt.label, MAX_MODELS))
    return models


def _lod_level(path):
    """``(sibling key, level)`` for a file named like ``Rock_LOD2.fbx``, else None."""
    folder, _, name = path.rpartition('/')
    stem = name.rsplit('.', 1)[0].lower()
    for separator in ('_lod', '-lod', '.lod', ' lod', 'lod'):
        at = stem.rfind(separator)
        digits = stem[at + len(separator):] if at > 0 else ''
        if digits.isdigit() and len(digits) <= 2:
            return folder + '/' + stem[:at], int(digits)
    return None


def _most_detailed(models):
    best = {}
    for path in models:
        level = _lod_level(path)
        key = level[0] if level else path
        rank = level[1] if level else -1
        if key not in best or rank < best[key][0]:
            best[key] = (rank, path)
    return sorted(path for _rank, path in best.values())
