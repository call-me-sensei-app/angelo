// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

#include "AngeloLinkLiveLibrary.h"

#include "AngeloLinkPreviews.h"

FString UAngeloLinkLiveLibrary::LiveVersion()
{
	return TEXT("0.1.0");
}

int32 UAngeloLinkLiveLibrary::ShowPreview(const FString& GlbPath, const FString& AssetPackage, FString& Error)
{
	Error.Reset();
	return AngeloLink::FPreviews::Get().Show(GlbPath, AssetPackage, Error);
}

int32 UAngeloLinkLiveLibrary::ClearPreview()
{
	return AngeloLink::FPreviews::Get().Clear();
}

int32 UAngeloLinkLiveLibrary::PreviewCount()
{
	return AngeloLink::FPreviews::Get().Count();
}

FBox UAngeloLinkLiveLibrary::PreviewBounds()
{
	return AngeloLink::FPreviews::Get().Bounds();
}

int32 UAngeloLinkLiveLibrary::PreviewTriangles()
{
	return AngeloLink::FPreviews::Get().Triangles();
}

void UAngeloLinkLiveLibrary::SetSessionLive(bool bLive)
{
	AngeloLink::FPreviews::Get().SetLive(bLive);
}

bool UAngeloLinkLiveLibrary::IsSessionLive()
{
	return AngeloLink::FPreviews::Get().IsLive();
}
