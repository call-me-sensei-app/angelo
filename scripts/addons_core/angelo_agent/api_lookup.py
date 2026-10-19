# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Installed RNA/BMesh documentation only. No eval, invoke or instance values."""

import inspect
import math
from itertools import islice

from .execution_spec import validate_execution


def _property(prop):
    record = {'name':prop.identifier,'label':prop.name,'type':prop.type,
              'description':str(prop.description)[:1200],
              'readonly':bool(prop.is_readonly)}
    if prop.type in {'INT','FLOAT'}:
        for key in ('hard_min','hard_max'):
            value = getattr(prop,key,None)
            if isinstance(value,(float,int)) and math.isfinite(value):
                record[key]=value
        record['array_length']=int(getattr(prop,'array_length',0))
    if prop.type in {'POINTER','COLLECTION'}:
        fixed = getattr(prop,'fixed_type',None)
        record['target_type'] = fixed.identifier if fixed else None
    if prop.type=='ENUM':
        values = list(islice(prop.enum_items,65))
        record['choices'] = [{'id':item.identifier,'label':item.name} for item in values[:64] if item.identifier]
        record['choices_truncated'] = len(values)>64
    for key in ('is_required','is_output','is_optional','is_enum_flag'):
        if hasattr(prop,key):
            record[key]=bool(getattr(prop,key))
    return record


# Blender's Python layer over its RNA types (Mesh.from_pydata, Object.children_recursive...): the module that
# defines them is bpy_types, _bpy_types since Blender 4.4.
PYTHON_LAYER = frozenset({'bpy_types', '_bpy_types'})


def _python_members(value):
    """Public functions and properties Blender's Python layer adds to an RNA type, by name; never called.

    RNA lists only C-defined members, so the lookup answered that Mesh.from_pydata
    does not exist (in a recorded GPT-6.1-Sol one-shot).
    """
    found = {}
    for owner in getattr(value, '__mro__', ()):
        if getattr(owner, '__module__', None) not in PYTHON_LAYER:
            continue
        for name, member in vars(owner).items():
            if not name.startswith('_') and name not in found and (inspect.isfunction(member) or isinstance(
                    member, (property, classmethod, staticmethod))):
                found[name] = member
    return found


def _python_member(name, member):
    function = member.fget if isinstance(member, property) else getattr(member, '__func__', member)
    try:
        signature = '' if isinstance(member, property) else str(inspect.signature(function))
    except (TypeError, ValueError):
        signature = '(...)'
    documentation = inspect.getdoc(function) or ''
    return {'name': name, 'kind': 'python_property' if isinstance(member, property) else 'python_function',
            'signature': name + signature, 'description': documentation[:1600], 'truncated': len(documentation) > 1600,
            'note': "Defined by Blender's Python layer (bpy_types), not RNA: use it from a reviewed script."}


def _page(items,args):
    offset,limit = args['offset'],args['limit']
    selected = items[offset:offset+limit]
    more = offset+len(selected)<len(items)
    return {'items':selected,'total':len(items),'offset':offset,
            'next_offset':offset+len(selected) if more else None,'truncated':more}


def _registered(bpy,name):
    """The class a registered add-on defines under this RNA identifier (Cycles' settings), or None.

    ``bpy.types`` has attributes for Blender's own structs only; Python-registered
    property groups and add-on preferences are found by their identifier.
    """
    find = getattr(bpy.types.bpy_struct,'bl_rna_get_subclass_py',None)
    value = find(name) if callable(find) else None
    return value if getattr(getattr(value,'bl_rna',None),'identifier',None)==name else None


def _type_names(bpy):
    """Every installed RNA type name: Blender's own and those registered add-ons define."""
    names = {name for name in dir(bpy.types) if not name.startswith('_') and hasattr(getattr(bpy.types,name),'bl_rna')}
    seen, pending = set(), [bpy.types.bpy_struct]
    while pending:
        for value in pending.pop().__subclasses__():
            if value not in seen:
                seen.add(value)
                pending.append(value)
                if value.__name__ not in names and _registered(bpy,value.__name__) is value:
                    names.add(value.__name__)
    return sorted(names)


def _writable(bpy,args):
    """Settings of an RNA type the generic native kinds write (rna_rules), as paths with types and ranges."""
    from .rna_rules import writable_settings
    if not args['name']:
        raise ValueError('Name an RNA type, for example Material, RenderSettings or BevelModifier')
    rna = getattr(getattr(bpy.types,args['name'],None),'bl_rna',None)
    if rna is None and _registered(bpy,args['name']) is not None:
        raise ValueError('WRITABLE describes Blender\'s own types; read an add-on type\'s writable settings '
                         'through the type that owns them, for example Scene')
    if rna is None:
        raise ValueError('Unknown installed RNA type; list RNA types first')
    settings = writable_settings(rna)
    return dict(_page(settings,args),name=rna.identifier,
                note='Paths are relative to an owner of this type; object pointers take {"object": handle}.')


def execute(args):
    import bpy
    args = validate_execution('angelo_blender_api',args)
    result = {'domain':args['domain'],'blender_version':bpy.app.version_string,
              'scene_changed':False,'authority':'Installed API metadata, not authorization or executable instructions.'}
    if args['domain']=='BMESH':
        import bmesh
        names = sorted(name for name in dir(bmesh.ops) if not name.startswith('_'))
        if not args['name']:
            return dict(result,**_page(names,args))
        if args['name'] not in names:
            raise ValueError('Unknown installed BMesh operator; list BMESH names first')
        function = getattr(bmesh.ops,args['name'])
        documentation = str(function.__doc__ or '')
        return dict(result,name=args['name'],documentation=documentation[:16000],truncated=len(documentation)>16000,
                    note='BMesh operations act on a supplied BMesh; invoking them still requires an approved action or script.')
    if args['domain']=='WRITABLE':
        return dict(result,**_writable(bpy,args))
    if not args['name']:
        return dict(result,**_page(_type_names(bpy),args))
    value = getattr(bpy.types,args['name'],None)
    rna = getattr(value,'bl_rna',None) or getattr(_registered(bpy,args['name']),'bl_rna',None)
    if rna is None:
        raise ValueError('Unknown installed RNA type; list RNA types first')
    properties = {prop.identifier:prop for prop in rna.properties if not prop.identifier.startswith('_') and prop.identifier!='rna_type'}
    functions = {func.identifier:func for func in rna.functions if not func.identifier.startswith('_')}
    result.update(name=rna.identifier,description=str(rna.description)[:1600])
    member=args['member']
    if member:
        if member in properties:
            return dict(result,property=_property(properties[member]))
        if member in functions:
            function=functions[member]
            params=list(islice(function.parameters,65))
            return dict(result,function={'name':member,'description':str(function.description)[:1600],
                        'parameters':[_property(prop) for prop in params[:64]],'truncated':len(params)>64})
        python = _python_members(value).get(member)
        if python is not None:
            return dict(result,function=_python_member(member,python))
        raise ValueError('This installed RNA type has no such public property or function, in RNA or in Blender\'s '
                         'Python layer')
    python = {name: member for name, member in _python_members(value).items()
              if name not in properties and name not in functions}
    members=sorted([{'name':name,'kind':'property','type':prop.type} for name,prop in properties.items()]+
                   [{'name':name,'kind':'function'} for name in functions]+
                   [{'name':name,'kind':_python_member(name,member)['kind']} for name,member in python.items()],
                   key=lambda entry:(entry['name'],entry['kind']))
    return dict(result,**_page(members,args))
