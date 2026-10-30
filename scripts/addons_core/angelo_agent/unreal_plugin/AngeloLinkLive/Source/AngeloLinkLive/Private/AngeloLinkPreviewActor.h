// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "AngeloLinkPreviewActor.generated.h"

class UDynamicMeshComponent;

/**
 * One instance's live preview: draws Angelo's mesh and nothing else. Spawned transient and outliner-hidden by
 * FPreviews; never selectable (a click, Delete or Duplicate can't reach it, so nothing of it enters a transaction or
 * the level) and editor-only (never in a cooked game or Play In Editor).
 */
UCLASS(NotPlaceable, NotBlueprintable, Transient)
class AAngeloLinkPreviewActor : public AActor
{
	GENERATED_BODY()

public:
	AAngeloLinkPreviewActor();

	UDynamicMeshComponent* GetMeshComponent() const { return MeshComponent; }

#if WITH_EDITOR
	virtual bool IsSelectable() const override { return false; }
	virtual bool IsUserManaged() const override { return false; }
#endif

private:
	UPROPERTY()
	TObjectPtr<UDynamicMeshComponent> MeshComponent;
};
