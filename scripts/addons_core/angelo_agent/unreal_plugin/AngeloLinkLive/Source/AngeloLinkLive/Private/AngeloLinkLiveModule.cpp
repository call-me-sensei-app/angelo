// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

#include "AngeloLinkPreviews.h"
#include "Modules/ModuleManager.h"

class FAngeloLinkLiveModule : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		if (GIsEditor && !IsRunningCommandlet())
		{
			AngeloLink::FPreviews::Get().Startup();
			bStarted = true;
		}
	}

	virtual void ShutdownModule() override
	{
		if (bStarted)
		{
			AngeloLink::FPreviews::Get().Shutdown();
			bStarted = false;
		}
	}

private:
	bool bStarted = false;
};

IMPLEMENT_MODULE(FAngeloLinkLiveModule, AngeloLinkLive)
