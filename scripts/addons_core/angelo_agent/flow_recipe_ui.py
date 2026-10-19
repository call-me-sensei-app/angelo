# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Node Editor sidebar and operators for recipes and Subflow nodes.

A flow becomes a recipe when it has an output step; its source nodes marked
"Recipe input" become the inputs of every Subflow node that uses it. Saving
puts a copy in the recipe library so other projects can add it; adding a
saved recipe copies it into the open file first, so the .blend stays
self-contained. Nothing here generates or costs anything.
"""

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel

from . import flow_host, flow_nodes, flow_recipe_library, flow_steps, flow_tree
from .flow_spec import SOURCE_TYPES, FlowError
from .ui_text import n_, rpt_


def _flow_tree(context):
    space = context.space_data
    tree = getattr(space, "edit_tree", None)
    return tree if tree is not None and tree.bl_idname == flow_nodes.TREE else None


class ANGELO_OT_flow_open_recipe(Operator):
    """Show this recipe in the Node Editor; edits change every Subflow that uses it"""
    bl_idname = "angelo.flow_open_recipe"
    bl_label = "Open Recipe"
    bl_options = {'INTERNAL'}
    recipe: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        tree = bpy.data.node_groups.get(self.recipe)
        space = context.space_data
        if tree is None or tree.bl_idname != flow_nodes.TREE or getattr(space, "type", "") != 'NODE_EDITOR':
            self.report({'ERROR'}, rpt_("That recipe isn't in this file"))
            return {'CANCELLED'}
        space.node_tree = tree
        return {'FINISHED'}


class ANGELO_OT_flow_save_recipe(Operator):
    """Save this recipe to the recipe library so other projects can use it"""
    bl_idname = "angelo.flow_save_recipe"
    bl_label = "Save to Recipe Library"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        tree = _flow_tree(context)
        try:
            if tree is None:
                raise FlowError(rpt_("Open a Generation Flow first"))
            flow_recipe_library.save(tree.name, flow_tree.tree_flow(tree))
        except (FlowError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt_("Saved recipe “{name}”", name=tree.name))
        return {'FINISHED'}


def _recipe_items(self, context):
    names = {tree.name for tree in flow_steps.recipe_trees()} | set(flow_steps.saved_interfaces())
    current = _flow_tree(context)
    items = [(name, name, n_("In this file") if bpy.data.node_groups.get(name) else n_("From the recipe library"))
             for name in sorted(names) if not current or name != current.name]
    _recipe_items.cache = items or [("", n_("No recipes yet"), n_("Give a flow an output step to make it a recipe"))]
    return _recipe_items.cache  # Blender requires enum strings to stay referenced.


class ANGELO_OT_flow_add_recipe(Operator):
    """Add a Subflow node that runs a recipe as one step"""
    bl_idname = "angelo.flow_add_recipe"
    bl_label = "Add Recipe"
    bl_options = {'UNDO'}
    bl_property = "recipe"
    recipe: EnumProperty(name="Recipe", items=_recipe_items)

    @classmethod
    def poll(cls, context):
        return _flow_tree(context) is not None

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        tree = _flow_tree(context)
        try:
            if not self.recipe:
                raise FlowError(rpt_("There are no recipes yet; give a flow an output step to make it a recipe"))
            recipe = bpy.data.node_groups.get(self.recipe) or flow_steps.import_recipe(self.recipe)
            if recipe == tree or not recipe.angelo_recipe_output:
                raise FlowError(rpt_("'{recipe}' can't be used as a recipe here", recipe=self.recipe))
            node = tree.nodes.new(flow_nodes.ANGELO_FlowSubflowNode.bl_idname)
            node.recipe = recipe
            node.location = context.space_data.cursor_location
        except (FlowError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        for other in tree.nodes:
            other.select = other == node
        tree.nodes.active = node
        return {'FINISHED'}


class ANGELO_PT_flow_recipe(Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Flow"
    bl_label = "Recipe"

    @classmethod
    def poll(cls, context):
        return _flow_tree(context) is not None

    def draw(self, context):
        layout = self.layout
        tree = _flow_tree(context)
        layout.prop_search(tree, "angelo_recipe_output", tree, "nodes", text="Output")
        if not tree.angelo_recipe_output:
            layout.label(text="Set an output to use as a recipe")
        else:
            entry = flow_host._cache.get(tree.name) or {}
            if entry.get("recipe_problem"):
                layout.label(text=entry["recipe_problem"], icon='ERROR')
            column = layout.column(heading="Inputs", align=True)
            for node in tree.nodes:
                if flow_nodes.TYPE_BY_IDNAME.get(node.bl_idname) in SOURCE_TYPES:
                    column.prop(node, "exposed", text=node.label or node.name, translate=False)
            layout.operator("angelo.flow_save_recipe", icon='FILE_TICK')
        layout.separator()
        layout.operator("angelo.flow_add_recipe", text="Add recipe as Subflow…", icon='NODETREE')


CLASSES = (ANGELO_OT_flow_open_recipe, ANGELO_OT_flow_save_recipe, ANGELO_OT_flow_add_recipe, ANGELO_PT_flow_recipe)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
