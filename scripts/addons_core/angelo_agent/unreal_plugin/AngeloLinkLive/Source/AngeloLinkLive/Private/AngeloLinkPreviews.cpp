// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

#include "AngeloLinkPreviews.h"

#include "AngeloLinkGlb.h"
#include "AngeloLinkPreviewActor.h"
#include "Components/DynamicMeshComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Editor.h"
#include "Editor/EditorEngine.h"
#include "EngineUtils.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/StaticMesh.h"
#include "Materials/Material.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/ITransaction.h"

namespace AngeloLink
{
	namespace
	{
		const TCHAR* BaseMaterial = TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial");
		const FName ColorParameter(TEXT("Color"));

		UWorld* EditorWorld()
		{
			return GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
		}

		/** The package of the mesh asset Component draws as one mesh, or an empty name. An instanced static mesh
		 * component (foliage, packed level actors) draws many instances at their own transforms: not previewed. */
		FName MeshPackage(const UMeshComponent* Component)
		{
			const UObject* Mesh = nullptr;
			if (Cast<UInstancedStaticMeshComponent>(Component))
			{
				return NAME_None;
			}
			if (const UStaticMeshComponent* Static = Cast<UStaticMeshComponent>(Component))
			{
				Mesh = Static->GetStaticMesh();
			}
			else if (const USkeletalMeshComponent* Skeletal = Cast<USkeletalMeshComponent>(Component))
			{
				Mesh = Skeletal->GetSkeletalMeshAsset();
			}
			return Mesh ? Mesh->GetOutermost()->GetFName() : NAME_None;
		}

		/** Whether the person can see Component now (its actor not hidden for the session, the component visible). */
		bool IsSeen(const UMeshComponent* Component)
		{
			const AActor* Owner = Component->GetOwner();
			return Component->GetVisibleFlag() && Owner && !Owner->IsTemporarilyHiddenInEditor();
		}

		void SetMaterials(UDynamicMeshComponent* Drawn, const FPreviewMesh& Mesh, UMaterialInterface* Base)
		{
			for (int32 Slot = 0; Slot < Mesh.SlotColors.Num(); ++Slot)
			{
				UMaterialInstanceDynamic* Material = Cast<UMaterialInstanceDynamic>(Drawn->GetMaterial(Slot));
				if (!Material || Material->Parent != Base)
				{
					Material = UMaterialInstanceDynamic::Create(Base, Drawn);
					Material->SetFlags(RF_Transient);
					Drawn->SetMaterial(Slot, Material);
				}
				Material->SetVectorParameterValue(ColorParameter, Mesh.SlotColors[Slot]);
			}
		}
	}

	FPreviews& FPreviews::Get()
	{
		static FPreviews Previews;
		return Previews;
	}

	void FPreviews::Startup()
	{
		bExiting = false;
		MapOpenedHandle = FEditorDelegates::OnMapOpened.AddRaw(this, &FPreviews::OnMapOpened);
		PreExitHandle = FEditorDelegates::OnEditorPreExit.AddRaw(this, &FPreviews::OnEditorPreExit);
		if (GEditor)
		{
			GEditor->ShouldDisableCPUThrottlingDelegates.Add(
				UEditorEngine::FShouldDisableCPUThrottling::CreateLambda([this]() { return bLive; }));
			ThrottleHandle = GEditor->ShouldDisableCPUThrottlingDelegates.Last().GetHandle();
		}
	}

	void FPreviews::Shutdown()
	{
		if (IsEngineExitRequested())
		{
			bExiting = true;   // The world is gone or going (UEditorEngine::PreExit): forget, never touch.
		}
		Clear();
		bLive = false;
		FEditorDelegates::OnMapOpened.Remove(MapOpenedHandle);
		FEditorDelegates::OnEditorPreExit.Remove(PreExitHandle);
		if (GEditor && ThrottleHandle.IsValid())
		{
			GEditor->ShouldDisableCPUThrottlingDelegates.RemoveAll(
				[this](const UEditorEngine::FShouldDisableCPUThrottling& Delegate)
				{ return Delegate.GetHandle() == ThrottleHandle; });
		}
		ThrottleHandle.Reset();
	}

	void FPreviews::OnEditorPreExit()
	{
		Clear();   // While the editor world still exists.
		bLive = false;
		bExiting = true;
	}

	int32 FPreviews::Show(const FString& GlbPath, const FString& AssetPackage, FString& Error)
	{
		TSharedPtr<FPreviewMesh> Read = MakeShared<FPreviewMesh>();
		if (!ReadGlb(GlbPath, *Read, Error))
		{
			return -1;   // The earlier preview stays: a bad frame never blanks what the person sees.
		}
		if (AssetPackage != Package)
		{
			Drop();   // Another asset: none of the previews are its instances.
		}
		Current = Read;
		Package = AssetPackage;
		return Apply();
	}

	int32 FPreviews::Clear()
	{
		const int32 Removed = Count();
		Drop();
		Current.Reset();
		Package.Reset();
		return Removed;
	}

	int32 FPreviews::Count() const
	{
		int32 Live = 0;
		for (const FShown& Item : Shown)
		{
			Live += Item.Preview.IsValid() ? 1 : 0;
		}
		return Live;
	}

	FBox FPreviews::Bounds() const
	{
		FBox Box(ForceInit);
		for (const FShown& Item : Shown)
		{
			if (const AActor* Preview = Item.Preview.Get())
			{
				Box += Preview->GetComponentsBoundingBox(/*bNonColliding=*/true);
			}
		}
		return Box;
	}

	int32 FPreviews::Triangles() const
	{
		return Current.IsValid() ? Current->Mesh.TriangleCount() : 0;
	}

	int32 FPreviews::Apply()
	{
		UWorld* World = EditorWorld();
		if (bExiting || !World || !World->PersistentLevel || !Current.IsValid() || Package.IsEmpty())
		{
			Drop();
			return 0;
		}
		TGuardValue<ITransaction*> NoUndo(GUndo, nullptr);   // Never recorded in a transaction in progress.
		const FName Wanted(*Package);
		UMaterialInterface* Base = LoadObject<UMaterialInterface>(nullptr, BaseMaterial);
		if (!Base)
		{
			Base = UMaterial::GetDefaultMaterial(MD_Surface);
		}
		TMap<UMeshComponent*, TWeakObjectPtr<AAngeloLinkPreviewActor>> Existing;
		for (const FShown& Item : Shown)
		{
			if (Item.Original.IsValid() && Item.Preview.IsValid())
			{
				Existing.Add(Item.Original.Get(), Item.Preview);
			}
			else if (AAngeloLinkPreviewActor* Orphan = Item.Preview.Get())
			{
				Orphan->Destroy();
			}
		}
		TArray<FShown> Kept;
		for (TActorIterator<AActor> It(World); It; ++It)
		{
			AActor* Actor = *It;
			if (!IsValid(Actor) || Actor->HasAnyFlags(RF_Transient))
			{
				continue;   // Previews, ours or another tool's.
			}
			TArray<UMeshComponent*> Components;
			Actor->GetComponents(Components);
			for (UMeshComponent* Component : Components)
			{
				if (!Component->IsRegistered() || MeshPackage(Component) != Wanted)
				{
					continue;
				}
				TWeakObjectPtr<AAngeloLinkPreviewActor> Reused;
				Existing.RemoveAndCopyValue(Component, Reused);
				AAngeloLinkPreviewActor* Preview = Reused.Get();
				if (!Preview)
				{
					FActorSpawnParameters Params;
					Params.ObjectFlags = RF_Transient;
					Params.bTemporaryEditorActor = true;
					Params.bHideFromSceneOutliner = true;
					Params.OverrideLevel = World->PersistentLevel;   // Lives as long as the world, not a sublevel.
					Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
					Preview = World->SpawnActor<AAngeloLinkPreviewActor>(
						AAngeloLinkPreviewActor::StaticClass(), Component->GetComponentTransform(), Params);
				}
				if (!Preview)
				{
					continue;
				}
				UDynamicMeshComponent* Drawn = Preview->GetMeshComponent();
				Drawn->SetMesh(UE::Geometry::FDynamicMesh3(Current->Mesh));
				SetMaterials(Drawn, *Current, Base);
				Preview->SetActorTransform(Component->GetComponentTransform());
				Preview->SetIsTemporarilyHiddenInEditor(!IsSeen(Component));
				Component->SetIsTemporarilyHiddenInEditor(true);
				Kept.Add({Preview, Component});
			}
		}
		for (const TPair<UMeshComponent*, TWeakObjectPtr<AAngeloLinkPreviewActor>>& Gone : Existing)
		{
			if (AAngeloLinkPreviewActor* Preview = Gone.Value.Get())
			{
				Preview->Destroy();   // Its instance no longer uses the asset (Tick would have caught most).
			}
		}
		Shown = MoveTemp(Kept);
		if (Shown.Num() > 0 && !TickHandle.IsValid())
		{
			TickHandle = FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateRaw(this, &FPreviews::Tick));
		}
		return Shown.Num();
	}

	void FPreviews::Drop()
	{
		if (!bExiting)
		{
			TGuardValue<ITransaction*> NoUndo(GUndo, nullptr);
			for (const FShown& Item : Shown)
			{
				if (UMeshComponent* Original = Item.Original.Get())
				{
					Original->SetIsTemporarilyHiddenInEditor(false);
				}
				if (AAngeloLinkPreviewActor* Preview = Item.Preview.Get())
				{
					Preview->Destroy();
				}
			}
		}
		Shown.Reset();
		if (TickHandle.IsValid())
		{
			FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
			TickHandle.Reset();
		}
	}

	bool FPreviews::Tick(float DeltaTime)
	{
		if (bExiting)
		{
			TickHandle.Reset();
			return false;
		}
		TGuardValue<ITransaction*> NoUndo(GUndo, nullptr);
		const FName Wanted(*Package);
		for (int32 Index = Shown.Num() - 1; Index >= 0; --Index)
		{
			FShown& Item = Shown[Index];
			UMeshComponent* Original = Item.Original.Get();
			AAngeloLinkPreviewActor* Preview = Item.Preview.Get();
			const bool bStillInstance = IsValid(Original) && Original->IsRegistered() && MeshPackage(Original) == Wanted;
			if (!bStillInstance || !IsValid(Preview))   // Deleted, unloaded, given another mesh, or the preview went.
			{
				if (IsValid(Original))
				{
					Original->SetIsTemporarilyHiddenInEditor(false);
				}
				if (IsValid(Preview))
				{
					Preview->Destroy();
				}
				Shown.RemoveAtSwap(Index);
				continue;
			}
			const FTransform At = Original->GetComponentTransform();
			if (!Preview->GetActorTransform().Equals(At))   // The person moved the instance.
			{
				Preview->SetActorTransform(At);
			}
			const bool bHide = !IsSeen(Original);   // The person hid the instance (H, the eye, Visible off).
			if (Preview->IsTemporarilyHiddenInEditor() != bHide)
			{
				Preview->SetIsTemporarilyHiddenInEditor(bHide);
			}
		}
		if (Shown.Num() == 0)
		{
			TickHandle.Reset();
			return false;   // Removes this ticker.
		}
		return true;
	}

	void FPreviews::OnMapOpened(const FString& Filename, bool bAsTemplate)
	{
		Shown.Reset();   // The old level's previews and instances went with it.
		if (Current.IsValid())
		{
			Apply();
		}
	}
}
