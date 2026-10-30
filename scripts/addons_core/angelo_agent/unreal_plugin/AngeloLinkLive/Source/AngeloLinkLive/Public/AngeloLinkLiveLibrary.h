// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "AngeloLinkLiveLibrary.generated.h"

/**
 * The live layer of Angelo Link, called by the Angelo Link plugin's Python (unreal.AngeloLinkLiveLibrary). Angelo's
 * link client stays in Python; this shows the live .glb Angelo sends in place of the linked mesh asset's instances in
 * the editor's level without changing the level or the asset, and keeps a background editor ticking while a session
 * is live. Game thread only.
 */
UCLASS()
class ANGELOLINKLIVE_API UAngeloLinkLiveLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** The live layer's version (Python's check that the module is loaded and new enough). */
	UFUNCTION(BlueprintPure, Category = "Angelo Link")
	static FString LiveVersion();

	/**
	 * Show the .glb at GlbPath in place of every instance of the mesh asset whose package is AssetPackage
	 * (/Game/Props/SM_Crate) in the editor's level: one transient preview actor per mesh component that uses it, the
	 * component hidden for this editor session only. Replaces any earlier preview. Returns how many instances show it,
	 * or -1 with Error set when the file can't be read.
	 */
	UFUNCTION(BlueprintCallable, Category = "Angelo Link")
	static int32 ShowPreview(const FString& GlbPath, const FString& AssetPackage, FString& Error);

	/** Remove every preview and show the instances as they are again. Returns how many previews were removed. */
	UFUNCTION(BlueprintCallable, Category = "Angelo Link")
	static int32 ClearPreview();

	/** How many instances show a preview now. */
	UFUNCTION(BlueprintPure, Category = "Angelo Link")
	static int32 PreviewCount();

	/** World-space bounds of every preview (an invalid box when none): what the person sees, for checks. */
	UFUNCTION(BlueprintPure, Category = "Angelo Link")
	static FBox PreviewBounds();

	/** Triangles in the newest preview mesh (0 when none). */
	UFUNCTION(BlueprintPure, Category = "Angelo Link")
	static int32 PreviewTriangles();

	/**
	 * While a link session is live, keep the editor ticking at full rate when it is in the background (Angelo in front)
	 * instead of Unreal's 3 Hz throttle; only then.
	 */
	UFUNCTION(BlueprintCallable, Category = "Angelo Link")
	static void SetSessionLive(bool bLive);

	UFUNCTION(BlueprintPure, Category = "Angelo Link")
	static bool IsSessionLive();
};
