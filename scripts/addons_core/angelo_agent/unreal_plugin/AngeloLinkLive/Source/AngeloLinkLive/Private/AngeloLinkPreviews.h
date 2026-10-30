// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "UObject/WeakObjectPtrTemplates.h"

class AAngeloLinkPreviewActor;
class UMeshComponent;

namespace AngeloLink
{
	struct FPreviewMesh;

	/**
	 * The previews in the editor's level. Each instance of the linked asset (a static or skeletal mesh component that
	 * uses it; instanced static mesh components are left as they are) gets a transient, unsaved, outliner-hidden,
	 * unselectable actor in the persistent level showing Angelo's live mesh at the component's transform, and the
	 * component is hidden for this editor session only (bHiddenEdTemporary, transient). Spawning, updating and
	 * removing previews run with no undo buffer, so no transaction (a gizmo drag in progress) records them. A new
	 * mesh updates the existing previews in place. The previews follow their instances as they move or hide, end with
	 * them, and come back on the newest mesh when another level opens; they are dropped before the editor tears its
	 * world down at exit. Game thread only.
	 */
	class FPreviews
	{
	public:
		static FPreviews& Get();

		void Startup();
		void Shutdown();

		int32 Show(const FString& GlbPath, const FString& AssetPackage, FString& Error);
		int32 Clear();
		int32 Count() const;
		FBox Bounds() const;
		int32 Triangles() const;

		void SetLive(bool bInLive) { bLive = bInLive; }
		bool IsLive() const { return bLive; }

	private:
		struct FShown
		{
			TWeakObjectPtr<AAngeloLinkPreviewActor> Preview;
			TWeakObjectPtr<UMeshComponent> Original;
		};

		int32 Apply();
		void Drop();
		bool Tick(float DeltaTime);
		void OnMapOpened(const FString& Filename, bool bAsTemplate);
		void OnEditorPreExit();

		TArray<FShown> Shown;
		TSharedPtr<FPreviewMesh> Current;   // The newest mesh, re-applied when another level opens.
		FString Package;
		bool bLive = false;
		bool bExiting = false;   // The editor is tearing its world down: no UObject work any more.
		FTSTicker::FDelegateHandle TickHandle;
		FDelegateHandle MapOpenedHandle;
		FDelegateHandle PreExitHandle;
		FDelegateHandle ThrottleHandle;
	};
}
