// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

#include "AngeloLinkPreviewActor.h"

#include "Components/DynamicMeshComponent.h"

AAngeloLinkPreviewActor::AAngeloLinkPreviewActor()
{
	PrimaryActorTick.bCanEverTick = false;
	bIsEditorOnlyActor = true;
	MeshComponent = CreateDefaultSubobject<UDynamicMeshComponent>(TEXT("Mesh"));
	MeshComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	MeshComponent->SetGenerateOverlapEvents(false);
	MeshComponent->bSelectable = false;
	MeshComponent->SetCastShadow(true);
	SetRootComponent(MeshComponent);
}
