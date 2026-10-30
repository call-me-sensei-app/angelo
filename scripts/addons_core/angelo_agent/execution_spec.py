# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stable discovery/measurement tools for the native agent execution layer."""

import re

CAPABILITY_FAMILIES = ('SUMMARY', 'ACTIONS', 'QUERIES', 'CHECKS', 'RECIPES', 'TOOLS')
# Every family takes names, as its schema offers: exact schemas of only those items, QUERIES of scene query tools,
# SUMMARY of a name in any family (plan_schema.lookup). Only ACTIONS, CHECKS and TOOLS did: in recorded runs GPT
# models asked for {family: RECIPES, names: ["bevel-and-shade"]} in 26 turns and Opus in one, each refused (Opus's
# by Claude Code's schema check, with a 10 KB error).
# Tool names, action kinds, check kinds, modifier:TYPE check variants and recipe names (bevel-and-shade).
NAME_PATTERN = r'^[A-Za-z][A-Za-z0-9_\-]{0,47}(?::[A-Z_]{1,15})?$'

EXECUTION_TOOLS = [
    {
        'name':'angelo_blender_api',
        'description': ('Look up installed Blender RNA properties/function parameters (operators: module.name or '
                        'MODULE_OT_name) or bmesh operator docs without running code. Names are identifiers, not '
                        'Python expressions or paths. WRITABLE lists an RNA type\'s settings (Material, BevelModifier) '
                        'that native actions can write, as typed paths with ranges. '
                        'Returned docs are reference data, not permission to run or bypass approval.'),
        'inputSchema': {'type':'object', 'properties': {
            'domain': {'type':'string','enum':['RNA','BMESH','WRITABLE']},
            'name': {'type':'string','maxLength':96},
            'member': {'type':'string','maxLength':96},
            'offset': {'type':'integer','minimum':0,'maximum':10000},
            'limit': {'type':'integer','minimum':1,'maximum':64}},
            'required':['domain','name','member','offset','limit'],'additionalProperties':False},
        'annotations': {'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},
    },
    {
        'name': 'angelo_agent_capabilities',
        'description': ('Discover the installed native action, query, verification and recipe capabilities. '
                        'Use summaries first, then request a specific family for exact supported controls. '
                        'With names, any family returns exact schemas of only those action kinds, check kinds, '
                        'recipes, query tools or tools (SUMMARY: a name of any family; TOOLS also returns sculpt/Media '
                        'Studio workflow guidance; '
                        'without names it lists the tools a Claude plan defers). '
                        'Unsupported actions still require reviewed Python; this does not grant permissions.'),
        'inputSchema': {'type':'object', 'properties': {
            'family': {'type':'string', 'enum':list(CAPABILITY_FAMILIES)},
            'names': {'type':'array', 'minItems':1, 'maxItems':32, 'uniqueItems':True,
                      'items': {'type':'string', 'minLength':1, 'maxLength':64, 'pattern':NAME_PATTERN}}},
            'required':['family'], 'additionalProperties':False},
        'annotations': {'readOnlyHint':True, 'destructiveHint':False, 'openWorldHint':False},
    },
    {
        'name':'angelo_agent_metrics',
        'description': ('Read bounded local performance measurements for this conversation: phase timings, '
                        'tool counts and completion status. No prompts, scripts or credentials are included. '
                        'These measurements describe recorded work, not predicted model speed or artistic quality.'),
        'inputSchema': {'type':'object', 'properties': {
            'limit': {'type':'integer', 'minimum':1, 'maximum':100}},
            'required':['limit'], 'additionalProperties':False},
        'annotations': {'readOnlyHint':True, 'destructiveHint':False, 'openWorldHint':False},
    },
]
EXECUTION_NAMES = frozenset(tool['name'] for tool in EXECUTION_TOOLS)


def validate_execution(name, args):
    if not isinstance(name,str) or name not in EXECUTION_NAMES or not isinstance(args,dict):
        raise ValueError('Unknown execution discovery tool or invalid arguments')
    schema=next(tool['inputSchema'] for tool in EXECUTION_TOOLS if tool['name']==name)
    if set(args)-set(schema['properties']) or set(schema['required'])-set(args):
        raise ValueError('Unexpected or missing execution discovery argument')
    if name == 'angelo_blender_api':
        if not isinstance(args.get('domain'),str) or args['domain'] not in {'RNA','BMESH','WRITABLE'}:
            raise ValueError('Choose RNA, BMESH or WRITABLE documentation')
        if args['domain']=='WRITABLE' and args['member']:
            raise ValueError('WRITABLE lists a whole type; leave member empty')
        for field in ('name','member'):
            value = args.get(field)
            # An operator's bl_idname (mesh.primitive_cube_add) is the one dotted name: a type's name in RNA.
            dotted = (field=='name' and args['domain']!='BMESH' and isinstance(value,str)
                      and re.fullmatch(r'[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*',value))
            if not isinstance(value,str) or len(value)>96 or (value and not dotted and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',value)):
                raise ValueError('Use a single public API identifier, or an operator as module.name, not a Python expression')
        if args['domain']=='BMESH' and args['member']:
            raise ValueError('BMESH operator names belong in name; leave member empty')
        for field,low,high in [('offset',0,10000),('limit',1,64)]:
            if type(args.get(field)) is not int or not low<=args[field]<=high:
                raise ValueError('Use bounded integer API pagination')
    elif name == 'angelo_agent_capabilities':
        if not isinstance(args.get('family'),str) or args['family'] not in CAPABILITY_FAMILIES:
            raise ValueError('Choose a supported capability family')
        if 'names' in args:
            names = args['names']
            if (type(names) is not list or not 1 <= len(names) <= 32 or len(set(map(repr, names))) != len(names)
                    or any(type(item) is not str or re.fullmatch(NAME_PATTERN, item) is None for item in names)):
                raise ValueError('Name 1-32 distinct tools, action kinds, check kinds or recipes')
            return {'family': args['family'], 'names': list(names)}
    elif name == 'angelo_agent_metrics':
        if type(args.get('limit')) is not int or not 1 <= args['limit'] <= 100:
            raise ValueError('Read between 1 and 100 recent local measurements')
    else:
        raise ValueError('Unknown execution discovery tool')
    return dict(args)
