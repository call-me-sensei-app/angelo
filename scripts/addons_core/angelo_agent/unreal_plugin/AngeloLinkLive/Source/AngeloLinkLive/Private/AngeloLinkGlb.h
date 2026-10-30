// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

#pragma once

#include "CoreMinimal.h"
#include "DynamicMesh/DynamicMesh3.h"

namespace AngeloLink
{
	/** A .glb read for a preview: every mesh node baked into one mesh in the asset's own space, as Interchange's
	 * default static mesh import bakes it, and each material slot's base colour (linear). */
	struct FPreviewMesh
	{
		UE::Geometry::FDynamicMesh3 Mesh;
		TArray<FLinearColor> SlotColors;
	};

	/** Read GlbPath into Out (glTF's axes and metres converted to Unreal's as Interchange does). False with Error. */
	bool ReadGlb(const FString& GlbPath, FPreviewMesh& Out, FString& Error);
}
