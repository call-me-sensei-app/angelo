<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
     SPDX-License-Identifier: MIT -->

# Angelo Link Live for Unreal Engine

The live layer of Angelo Link. While you edit a mesh in Angelo, your changes show in the open Unreal Editor in place
of that mesh's instances in the level, before you save. Nothing is saved into your level or asset: saving in Angelo
updates the asset itself, as the Angelo Link plugin always does.

**It ships as source only.** You build it for your engine version. Angelo doesn't install it or ship binaries yet.
It was tested with **Unreal Engine 5.8 on macOS**. Windows, Linux and other engine versions haven't been built or
tested yet.

## What you need

* The **Angelo Link** plugin in the same project, enabled. It's the link to Angelo; this plugin only draws the
  live edits. Without Angelo Link Live, Angelo Link still updates the asset each time you save in Angelo.
* A C++ toolchain for your engine version: Xcode on macOS, Visual Studio on Windows.
* This folder. Angelo includes it at `angelo_agent/unreal_plugin/AngeloLinkLive` in its add-ons folder (on macOS,
  inside `Angelo.app/Contents/Resources/<version>/scripts/addons_core`).

## Build and enable it

1. Close the Unreal Editor.
2. Build the plugin with your engine's `RunUAT`, into a folder outside your project:

   macOS:

   ```
   "<Unreal Engine>/Engine/Build/BatchFiles/RunUAT.sh" BuildPlugin \
       -Plugin="<this folder>/AngeloLinkLive.uplugin" -Package="<build folder>/AngeloLinkLive" -TargetPlatforms=Mac
   ```

   Windows (not tested yet): `RunUAT.bat` with `-TargetPlatforms=Win64`.

3. Copy `<build folder>/AngeloLinkLive` into your project as `Plugins/AngeloLinkLive`, beside `Plugins/AngeloLink`.
4. Open the project, and in **Edit > Plugins** enable **Angelo Link Live**. Restart the editor when it asks.
5. Choose **Edit in Angelo** on a mesh. Once Angelo opens it, the Output Log says "Your changes show here live".

If the editor says the module is missing or was built for another engine version, build the plugin again with that
engine. Until then, turn Angelo Link Live off in Edit > Plugins: Angelo Link keeps working without it.

## What shows, and what doesn't yet

* Every static and skeletal mesh component that uses the asset shows Angelo's mesh at its own transform, with each
  material's base colour. Instanced static meshes (foliage) keep showing the asset.
* Not yet: textures and vertex colours, skinning (a skinned mesh shows its bind pose), Play In Editor.

Licence: MIT (`LICENSE.md`). Design and validation: `doc/angelo/unreal_link.rst` in Angelo's source.
