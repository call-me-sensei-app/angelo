<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
     SPDX-License-Identifier: MIT -->

# Angelo Link for Godot

Edit a `.blend` or `.glb` asset in Angelo and see every change on that asset's instances in the Godot editor as you
work. Saving in Angelo updates the asset file, and Godot re-imports it.

- **Edit in Angelo** is in the Scene dock's context menu (on a node made from a `.blend` or `.glb`, or its children),
  the FileSystem dock's context menu (on a `.blend` or `.glb`), and the **Angelo** menu in the 3D editor's toolbar.
  Angelo asks you before it opens the asset.
- While the link is live, the changes show in place of the asset's instances, in every open scene that uses it. The
  preview is never saved into your scene; Stop Live Link, closing Angelo or disabling the plugin shows the asset as
  last saved again.
- The plugin talks only to Angelo on this computer (127.0.0.1), after both sides prove they share the key Angelo
  writes to its per-user data folder.

## Requirements

Godot 4.4 or newer (the plugin uses `EditorContextMenuPlugin`), and Angelo running with Godot Live Link turned on (the
chat bar's + menu ▸ Godot live link). An older Godot reports that it needs 4.4 instead of loading the plugin. The
plugin is fully statically typed, so it also loads in projects that raise GDScript warnings to errors.

## Installing and enabling

In Angelo, choose **Add Angelo Link to a Godot Project…** (File ▸ Export, or the + menu ▸ Godot live link) and pick
the folder that holds `project.godot`. Angelo copies the plugin into `addons/angelo_link`, and replaces an older copy
the same way when you update. Then, in Godot, enable **Angelo Link** in Project ▸ Project Settings ▸ Plugins.

## Licence and trademarks

The files in this folder are released under the MIT licence (see the SPDX header in each file).

"Angelo" and the Angelo mark are trademarks of Hyperbond Studio Pte. Ltd. The MIT licence covers these files; it
does not grant permission to use the Angelo name or mark.
