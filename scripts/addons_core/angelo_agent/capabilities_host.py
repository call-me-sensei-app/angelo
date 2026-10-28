# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_agent_capabilities``: the discovery answers (tool families, action kinds, queries, checks, recipes) with
this build's runtime facts. Split out of ``blender_tools``; ``BlenderExecutor`` answers the tool here."""

import copy

import bpy


def _runtime_facts():
    """This build's feature availability, without the catalog runtime_capabilities repeats."""
    from .action_kernels import runtime_capabilities
    runtime = runtime_capabilities()
    return {key: runtime[key] for key in ('runtime_features', 'unavailable_in_this_build')}


def agent_capabilities(family, names=None, controller=None):
    from .action_catalog import action_capabilities, ACTION_SCHEMAS, CAPABILITIES
    from .plan_schema import lookup, deferred_index
    from .scene_spec import SCENE_TOOLS
    from .validation_spec import validation_capabilities
    from .action_recipes import recipe_capabilities
    from .routing import TOOLSET_VERSION
    result = {'toolset_version':TOOLSET_VERSION,'blender_version':bpy.app.version_string,
              'scene_changed':False,'permissions':'Discovery does not grant permission; the configured host approval policy still applies.'}
    from .team_library_access import hidden_tools
    from . import parallel_access
    hidden = hidden_tools() | parallel_access.discovery_hidden(controller)   # A worker chat's: only what it may call.
    if names:
        return dict(result,**lookup(family,names,hidden),**({'runtime':_runtime_facts()} if family=='ACTIONS' else {}))
    if family=='TOOLS':
        return dict(result,**deferred_index(hidden))
    if family=='ACTIONS':
        from .action_catalog import action_index
        return dict(result,**action_index(),runtime=_runtime_facts())
    if family=='QUERIES':
        return dict(result,queries=copy.deepcopy(SCENE_TOOLS))
    if family=='CHECKS':
        return dict(result,**validation_capabilities())
    if family=='RECIPES':
        return dict(result,**recipe_capabilities())
    return dict(result,families=['ACTIONS','QUERIES','CHECKS','RECIPES','TOOLS'],action_kinds=list(ACTION_SCHEMAS),
                limits=copy.deepcopy(CAPABILITIES),runtime=_runtime_facts(),
                workflow=['Observe exact document handles/revisions','Apply a tested recipe in one call, or expand it to review or extend',
                          'Submit a bounded program with expected checks','Inspect staged preview or verified commit'],
                cancellation='Cooperative between staged operations; a running native kernel or privileged script remains atomic.',
                fallback='Unsupported actions require separately reviewed Blender Python. Never infer an action from arbitrary RNA metadata.',
                project_memory='angelo_memory_read/update preserve bounded project facts, not permissions or hidden reasoning.',
                api_lookup='angelo_blender_api exposes installed RNA/BMesh documentation without execution.',
                visual_observation='angelo_view returns read-only SOLID, WIREFRAME, MATERIAL, LIT (scene lights and world), ATTRIBUTE (colour attributes) or WEIGHT (vertex weights) images of targets or the scene, a strip over frames, or a RENDERED look of the scene camera by the scene\'s own engine; with image it shows a finished render or one of its passes, an Image\'s pixels (IMAGE:<name>), a UV layout (UV) or what the person sees (EDITOR); reuse a view_id for identical before/after cameras; a free camera views from any location, beside an '
                                   'attached image with reference.',
                metrics='angelo_agent_metrics reports recorded local timing; it is not an end-to-end speed guarantee.')
