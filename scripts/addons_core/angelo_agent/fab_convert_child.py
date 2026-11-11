# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turn one unpacked Fab download into a clean .blend. Runs only inside the sandboxed converter Blender.

``Angelo --background --factory-startup --disable-autoexec --offline-mode -noaudio --python fab_convert_child.py
-- <job.json> <result.json>``, started by ``fab_convert`` with an empty home, no network and writes limited to its
work folder (macOS). Standalone on purpose: no Angelo add-on runs in this process, and nothing it reads can run
code (Python scripts and drivers stay off; Text blocks, script nodes and drivers that need Python are removed).

The job names the format (a row of ``fab_formats``), the unpacked folder, the model files (or, for a texture set,
the images), the output path and the listing title. The child imports with Blender's own importer, keeps the
asset's objects (``fab_convert_clean``), packs every texture, and writes only the asset (one collection, or one
material for a texture set) with ``bpy.data.libraries.write``. The result file holds counts and a closed error
code; the app trusts neither more than a fixed sentence per code.
"""

import json
import os
from pathlib import Path
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # Plain sibling modules, not the add-on package.
import fab_convert_clean as clean  # noqa: E402
import fab_convert_guard as guard  # noqa: E402
import fab_convert_material as material  # noqa: E402
import fab_formats  # noqa: E402

ERRORS = ('UNREADABLE', 'NO_OBJECTS', 'OUTSIDE_REFERENCE', 'LINKED_LIBRARY', 'IMPORTER_MISSING', 'NO_MAPS',
          'TOO_LARGE', 'FAILED')


class Refusal(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code if code in ERRORS else 'FAILED'


def stage(name):
    print('ANGELO_FAB_STAGE %s %.3f' % (name, time.monotonic()), flush=True)


def inside(folder, relative):
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder) or not path.is_file() or path.is_symlink():
        raise Refusal('OUTSIDE_REFERENCE')
    return path


def _usd_roots(folder, models):
    """USD layers no other chosen layer composes. Every dependency of every layer (sublayers, references, payloads,
    value clips, shader asset paths, UDIM sets) must resolve inside the download, or the file is refused before
    Blender's importer reads any of it (security review 2026-11-11, F1)."""
    from pxr import UsdUtils
    referenced = set()
    for relative in models:
        path = inside(folder, relative)
        _usd_raw_paths(folder, path)   # Unresolved first: resolving a network path would already open it.
        try:
            dependencies = UsdUtils.ComputeAllDependencies(str(path))
        except Exception:  # noqa: BLE001 - pxr raises Tf errors for malformed layers.
            raise Refusal('UNREADABLE') from None
        layers = [layer.realPath or layer.identifier for layer in dependencies[0]]
        assets = [str(asset) for asset in dependencies[1]]
        unresolved = [str(asset) for asset in dependencies[2]]
        if guard.usd_escapes((layers, assets, unresolved), str(folder)):
            print('ANGELO_FAB_REFUSED usd dependency outside the download', flush=True)
            raise Refusal('OUTSIDE_REFERENCE')
        root = str(path)
        referenced.update(guard.resolved(layer) for layer in layers if guard.resolved(layer) != guard.resolved(root))
    return [relative for relative in models if guard.resolved(str(inside(folder, relative))) not in referenced]


LAYERS = ('.usd', '.usda', '.usdc', '.usdz', '.mtlx')
OWN_PACKED_DATA = ('fbx', 'blend')   # Importers whose packed images can only hold the downloaded file's own bytes.


def _usd_raw_paths(folder, root):
    """Read every asset path of ``root`` and of each layer it composes as written, unresolved (sublayers, references,
    payloads, variants, clips, shader inputs, UDIM patterns, MaterialX documents): one naming another computer or a
    device, a URL, or a place outside the download refuses the file before anything resolves or opens it
    (re-review r2: on Windows opening \\\\host\\share sends an NTLM hash)."""
    import posixpath
    from pxr import Sdf, UsdUtils
    # A USDZ package's own asset paths are relative to the package's inside, its layers named "pkg.usdz[inner]".
    package_root = str(root) if str(root).lower().endswith('.usdz') else None
    pending, seen = [(str(root), package_root, '' if package_root else str(root.parent))], set()
    while pending:
        identifier, package, base = pending.pop()
        if identifier in seen:
            continue
        seen.add(identifier)
        if len(seen) > 256:
            raise Refusal('UNREADABLE')
        layer = Sdf.Layer.FindOrOpen(identifier)
        if layer is None:
            raise Refusal('UNREADABLE')
        found = []
        UsdUtils.ModifyAssetPaths(layer, lambda asset: found.append(asset) or asset)   # Visits; returns each unchanged.
        for asset in found:
            asset = str(asset).strip()
            if not asset:
                continue
            if guard.network_or_device(asset) or guard.names_a_root(asset):
                raise Refusal('OUTSIDE_REFERENCE')
            relative = posixpath.normpath(posixpath.join(base, asset.replace('\\', '/')))
            if package is None:
                if not guard.inside_any(guard.resolved(relative), [str(folder)]):
                    raise Refusal('OUTSIDE_REFERENCE')
                if relative.lower().endswith('.usdz'):
                    pending.append((relative, relative, ''))   # Inside a package, paths start from its root.
                elif relative.lower().endswith(LAYERS):
                    pending.append((relative, None, posixpath.dirname(relative)))
            else:
                if relative.startswith('../') or relative == '..' or relative.startswith('/'):
                    raise Refusal('OUTSIDE_REFERENCE')
                if relative.lower().endswith(LAYERS):
                    pending.append(('%s[%s]' % (package, relative), package, posixpath.dirname(relative)))


def _blend(path, folder, scale):
    """Append a .blend's scenes without running anything, keep their visible objects, then drop the scenes."""
    with bpy.data.libraries.load(str(path), link=False) as (source, _target):
        if list(source.libraries):
            raise Refusal('LINKED_LIBRARY')   # Its own links would read other files; never followed.
        names = list(source.scenes)
    before = set(bpy.data.scenes)
    with bpy.data.libraries.load(str(path), link=False) as (_source, target):
        target.scenes = names
    objects = []
    for scene in [s for s in bpy.data.scenes if s not in before]:
        excluded = clean.excluded_collections(scene)
        for obj in scene.objects:
            if obj not in objects and not any(c in excluded for c in obj.users_collection) and not obj.hide_viewport:
                objects.append(obj)
        unit = scene.unit_settings.scale_length if scene.unit_settings.system != 'NONE' else 1.0
        scale.append(unit if unit > 0 else 1.0)
    for library in list(bpy.data.libraries):   # libraries.load leaves its source Library behind.
        bpy.data.libraries.remove(library)
    return objects


def import_model(fmt, folder, relative, scale):
    path = inside(folder, relative)
    before = set(bpy.data.objects)
    if fmt.importer == 'blend':
        return _blend(path, folder, scale)
    try:
        if fmt.importer == 'fbx':
            result = bpy.ops.import_scene.fbx(filepath=str(path), use_image_search=False)
        elif fmt.importer == 'obj':
            result = bpy.ops.wm.obj_import(filepath=str(path))
        elif fmt.importer == 'gltf':
            import gltf_checks   # Plain sibling modules: the importer's own reading of URIs, and Angelo's options.
            from gltf_morph import import_options
            try:
                gltf_checks.check_file(str(path), self_contained=False)
            except gltf_checks.Refused:
                raise Refusal('OUTSIDE_REFERENCE') from None
            result = bpy.ops.import_scene.gltf(filepath=str(path), import_pack_images=True, import_scene_extras=False,
                                               **import_options(str(path)))
        elif fmt.importer == 'usd':
            options = bpy.ops.wm.usd_import.get_rna_type().properties.keys()
            wanted = {'import_textures_mode': 'IMPORT_PACK', 'set_frame_range': False,
                      'create_world_material': False, 'import_cameras': False, 'import_lights': True}
            result = bpy.ops.wm.usd_import(filepath=str(path), **{k: v for k, v in wanted.items() if k in options})
        else:
            raise Refusal('IMPORTER_MISSING')
    except (AttributeError, KeyError):
        raise Refusal('IMPORTER_MISSING') from None
    except RuntimeError:
        raise Refusal('UNREADABLE') from None
    if result != {'FINISHED'}:
        raise Refusal('UNREADABLE')
    return [obj for obj in bpy.data.objects if obj not in before]


def convert_model(job, fmt, folder, timings):
    models = list(job['models'])
    if fmt.importer == 'usd':
        models = _usd_roots(folder, models)
    objects, scale = [], []
    stage('import')
    started = time.monotonic()
    images_before = {image.session_uid for image in bpy.data.images}
    for relative in models:
        objects.extend(o for o in import_model(fmt, folder, relative, scale) if o not in objects)
    # Packed data an importer took from the downloaded file itself, whatever author path it records (re-review r3
    # G4): Blender's FBX importer packs only the file's own embedded "Content" (import_fbx.blen_read_texture_image),
    # never a file from disk; a .blend's packed images travel inside the .blend, and appending reads no other file.
    # The USD and glTF importers are not trusted this way: their packed sources must pass source_allowed.
    embedded = {image.session_uid for image in bpy.data.images if fmt.importer in OWN_PACKED_DATA
                and len(image.packed_files) and image.session_uid not in images_before}
    timings['import'] = round(time.monotonic() - started, 3)
    started = time.monotonic()
    stage('clean')
    kept, skipped = clean.keep_asset_objects(objects)
    if not kept:
        raise Refusal('NO_OBJECTS')
    unit = scale[0] if scale and all(abs(s - scale[0]) < 1e-9 for s in scale) else 1.0
    if unit != 1.0:
        clean.scale_roots(kept, unit)
    collection = bpy.data.collections.new(job['datablock'])
    for obj in kept:
        # Linked, never moved: a collection another object instances must keep its members.
        collection.objects.link(obj)
    report = clean.sanitize(folder, job['images'])
    textured = material.complete_untextured(kept, folder, job['images'], job['datablock'], report)
    clean.pack_images(folder, report, embedded=embedded)
    timings['clean'] = round(time.monotonic() - started, 3)
    return collection, dict(report, objects=len(kept), meshes=sum(o.type == 'MESH' for o in kept),
                            skipped=skipped, unit_scale=unit, models=len(models), material_from_textures=textured)


def convert_surface(job, folder, timings):
    stage('surface')
    started = time.monotonic()
    report = {'missing_textures': 0}
    built = material.surface(folder, job['images'], job['datablock'])
    if built is None:
        raise Refusal('NO_MAPS')
    block, roles = built
    clean.pack_images(folder, report)
    block.use_fake_user = True
    timings['surface'] = round(time.monotonic() - started, 3)
    return block, dict(report, surface=roles)


def main():
    arguments = sys.argv[sys.argv.index('--') + 1:]
    job_path, result_path = Path(arguments[0]), Path(arguments[1])
    result = {'ok': False, 'error': 'FAILED'}
    timings = {}
    try:
        job = json.loads(job_path.read_text(encoding='utf-8'))
        # Before anything reads the download: end at the deadline, or with the app (security review F6).
        guard.watchdog(job['deadline_seconds'], job['parent_pid'])
        fmt = fab_formats.BY_CODE[job['format']]
        folder = Path(job['folder']).resolve()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if fmt.importer == 'surface':
            block, facts = convert_surface(job, folder, timings)
        else:
            block, facts = convert_model(job, fmt, folder, timings)
        stage('write')
        started = time.monotonic()
        output = Path(job['output'])
        block.asset_mark()   # The app appends asset-marked datablocks only (blender_asset_import._append).
        bpy.data.libraries.write(str(output), {block}, path_remap='NONE', fake_user=True, compress=False)
        timings['write'] = round(time.monotonic() - started, 3)
        result = dict(facts, ok=True, error=None, name=block.name, id_type='MATERIAL' if fmt.kind == 'material'
                      else 'COLLECTION', timings=timings)
    except Refusal as refusal:
        result = {'ok': False, 'error': refusal.code, 'timings': timings}
    except Exception:  # noqa: BLE001 - any other failure is one closed code; the log keeps the detail.
        import traceback
        traceback.print_exc()
        result = {'ok': False, 'error': 'FAILED', 'timings': timings}
    result_path.write_text(json.dumps(result), encoding='utf-8')
    stage('done')


if __name__ == '__main__':
    main()
