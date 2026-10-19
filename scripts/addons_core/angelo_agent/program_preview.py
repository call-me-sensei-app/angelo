# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private pixels of an isolated staged scene, never a mutation authorization."""

import os


def capture(window, scene, references, *, resolution=512):
    """Render bounded native solid geometry without switching the artist window.

    The caller owns the isolated scene and must keep it alive during this call.
    These PNGs are ordinary chat references, not admitted sculpt target cameras.
    """
    import bpy
    import gpu
    import numpy as np
    from mathutils import Matrix, Vector

    if scene == window.scene:
        raise RuntimeError('Program previews must use an isolated staging scene')
    if resolution not in {384,512,768}:
        raise ValueError('Choose a bounded native preview resolution')
    if references is None:
        raise RuntimeError('Bind a conversation reference store before rendering a preview')
    area = next((area for area in window.screen.areas if area.type == 'VIEW_3D'), None)
    if area is None:
        raise RuntimeError('A 3D Viewport is required to show a native program preview')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    layer = scene.view_layers[0]
    layer.update()
    if len(scene.objects) > 128:
        raise RuntimeError('Staged previews are limited to 128 objects')
    points = []
    for obj in scene.objects:
        if obj.type not in {'MESH','CURVE','SURFACE','FONT','META','CURVES'} or obj.hide_get(view_layer=layer):
            continue
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return {'views':[], 'preview_note':'No drawable geometry; inspect the staged property changes and checks.'}
    points = np.asarray(points, dtype=np.float64)
    if not np.isfinite(points).all():
        raise RuntimeError('Cannot render invalid staged geometry bounds')
    center = Vector(((points.min(axis=0)+points.max(axis=0))*.5).tolist())
    direction = Vector((1,-1,.7)).normalized()
    rotation = direction.to_track_quat('Z','Y').to_matrix().to_4x4()
    projected = (points-np.asarray(center)) @ np.asarray(rotation.to_3x3())
    radius = max(float(np.abs(projected[:,:2]).max())*1.15,.001)
    distance = max(radius*4,float(projected[:,2].max())+radius)
    depth = max(radius*12,distance-float(projected[:,2].min())+radius)
    camera = Matrix.Translation(center+direction*distance) @ rotation
    projection = Matrix(((1/radius,0,0,0),(0,1/radius,0,0),(0,0,-2/depth,-1),(0,0,0,1)))
    space = area.spaces.active
    settings = {'type':'SOLID','light':'STUDIO','color_type':'MATERIAL','show_shadows':True,
                'show_cavity':True,'background_type':'VIEWPORT','background_color':(.025,.025,.03),
                'show_xray':False,'show_backface_culling':False,'show_object_outline':False,
                'show_specular_highlight':False,'use_dof':False}
    old = {key:getattr(space.shading,key) for key in settings}
    old['background_color'] = tuple(old['background_color'])
    overlays = space.overlay.show_overlays
    view_regions = tuple(space.region_quadviews) or (space.region_3d,)
    clipping = [(view_region,view_region.use_clip_planes) for view_region in view_regions]
    front = [obj for obj in scene.objects if obj.show_in_front]
    offscreen = image = reference = None
    try:
        for key,value in settings.items():
            setattr(space.shading,key,value)
        space.overlay.show_overlays = False
        for view_region,_ in clipping:
            view_region.use_clip_planes = False
        for obj in front:
            obj.show_in_front = False
        layer.update()
        offscreen = gpu.types.GPUOffScreen(resolution,resolution)
        offscreen.draw_view3d(scene,layer,space,region,camera.inverted(),projection,do_color_management=True)
        with offscreen.bind():
            pixels = offscreen.texture_color.read()
            pixels.dimensions = resolution*resolution*4
            rgba = np.frombuffer(bytes(pixels),dtype=np.uint8).astype(np.float32)/255
        reference = references.new_observation('Staged program preview · not applied')
        image = bpy.data.images.new('Angelo private program preview',width=resolution,height=resolution,alpha=True)
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set(rgba)
        image.filepath_raw = reference['path']
        image.file_format = 'PNG'
        image.save()
        os.chmod(reference['path'],0o600)
        reference['bytes'] = os.path.getsize(reference['path'])
        return {'views':[reference], 'preview_note':'Native solid-shaded staged geometry, not final material rendering. No live scene change; not an image-space targeting camera.'}
    except Exception:
        if reference is not None:
            references.discard_observation(reference)
        raise
    finally:
        if image is not None:
            bpy.data.images.remove(image)
        if offscreen is not None:
            offscreen.free()
        for key,value in old.items():
            setattr(space.shading,key,value)
        space.overlay.show_overlays = overlays
        for view_region,enabled in clipping:
            view_region.use_clip_planes = enabled
        for obj in front:
            obj.show_in_front = True
