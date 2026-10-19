/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup blendthumb
 *
 * Thumbnail from Blend file extraction for MS-Windows (DLL).
 */

#include <new>
#include <objbase.h>
#include <shlobj.h> /* For #SHChangeNotify */
#include <shlwapi.h>
#include <thumbcache.h> /* For IThumbnailProvider */

extern HRESULT CBlendThumb_CreateInstance(REFIID riid, void **ppv);

/* The handler's CLSID and display name come from the application identity
 * (`scripts/modules/angelo_identity.json`, via `angelo_identity.cmake`). The CLSID must
 * differ from stock Blender's: registering a shared CLSID would redirect Blender's
 * thumbnails to this DLL and unregistering it would remove Blender's handler. */
#ifndef ANGELO_WIN_THUMBNAIL_CLSID_STR
#  error "ANGELO_WIN_THUMBNAIL_CLSID_STR comes from build_files/cmake/angelo_identity.cmake"
#endif
#define SZ_CLSID_BLENDTHUMBHANDLER L"" ANGELO_WIN_THUMBNAIL_CLSID_STR
#define SZ_BLENDTHUMBHANDLER L"" ANGELO_WIN_THUMBNAIL_HANDLER_NAME
/* The shell's thumbnail-handler slot of the `.blend` extension (one handler per extension). */
#define SZ_KEY_BLEND_SHELLEX \
  L"Software\\Classes\\.blend\\ShellEx\\{e357fccd-a995-4576-b01f-234630154e96}"

namespace {

constexpr int guid_hex_digit(const char c)
{
  return (c >= '0' && c <= '9') ? c - '0' : (c >= 'a' && c <= 'f') ? c - 'a' + 10 : c - 'A' + 10;
}

/** The value of \a count hexadecimal digits starting at \a text[offset]. */
constexpr unsigned long guid_hex(const char *text, const int offset, const int count)
{
  unsigned long value = 0;
  for (int i = 0; i < count; i++) {
    value = value * 16 + guid_hex_digit(text[offset + i]);
  }
  return value;
}

/** Parse `{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}` (the layout #angelo_identity.cmake validates). */
constexpr CLSID guid_parse(const char *text)
{
  return {guid_hex(text, 1, 8),
          static_cast<unsigned short>(guid_hex(text, 10, 4)),
          static_cast<unsigned short>(guid_hex(text, 15, 4)),
          {static_cast<unsigned char>(guid_hex(text, 20, 2)),
           static_cast<unsigned char>(guid_hex(text, 22, 2)),
           static_cast<unsigned char>(guid_hex(text, 25, 2)),
           static_cast<unsigned char>(guid_hex(text, 27, 2)),
           static_cast<unsigned char>(guid_hex(text, 29, 2)),
           static_cast<unsigned char>(guid_hex(text, 31, 2)),
           static_cast<unsigned char>(guid_hex(text, 33, 2)),
           static_cast<unsigned char>(guid_hex(text, 35, 2))}};
}

}  // namespace

const CLSID CLSID_BlendThumbHandler = guid_parse(ANGELO_WIN_THUMBNAIL_CLSID_STR);

typedef HRESULT (*PFNCREATEINSTANCE)(REFIID riid, void **ppvObject);
struct CLASS_OBJECT_INIT {
  const CLSID *pClsid;
  PFNCREATEINSTANCE pfnCreate;
};

/* Add classes supported by this module here. */
const CLASS_OBJECT_INIT c_rgClassObjectInit[] = {
    {&CLSID_BlendThumbHandler, CBlendThumb_CreateInstance}};

long g_cRefModule = 0;

/** Handle the DLL's module */
HINSTANCE g_hInst = nullptr;

/** Standard DLL functions. */
STDAPI_(BOOL) DllMain(HINSTANCE hInstance, DWORD dwReason, void *)
{
  if (dwReason == DLL_PROCESS_ATTACH) {
    g_hInst = hInstance;
    DisableThreadLibraryCalls(hInstance);
  }
  return TRUE;
}

STDAPI DllCanUnloadNow()
{
  /* Only allow the DLL to be unloaded after all outstanding references have been released. */
  return (g_cRefModule == 0) ? S_OK : S_FALSE;
}

void DllAddRef()
{
  InterlockedIncrement(&g_cRefModule);
}

void DllRelease()
{
  InterlockedDecrement(&g_cRefModule);
}

class CClassFactory final : public IClassFactory {
 public:
  static HRESULT CreateInstance(REFCLSID clsid,
                                const CLASS_OBJECT_INIT *pClassObjectInits,
                                size_t cClassObjectInits,
                                REFIID riid,
                                void **ppv)
  {
    *ppv = nullptr;
    HRESULT hr = CLASS_E_CLASSNOTAVAILABLE;
    for (size_t i = 0; i < cClassObjectInits; i++) {
      if (clsid == *pClassObjectInits[i].pClsid) {
        IClassFactory *pClassFactory = new (std::nothrow)
            CClassFactory(pClassObjectInits[i].pfnCreate);
        hr = pClassFactory ? S_OK : E_OUTOFMEMORY;
        if (SUCCEEDED(hr)) {
          hr = pClassFactory->QueryInterface(riid, ppv);
          pClassFactory->Release();
        }
        /* Match found. */
        break;
      }
    }
    return hr;
  }

  CClassFactory(PFNCREATEINSTANCE pfnCreate) : _cRef(1), _pfnCreate(pfnCreate)
  {
    DllAddRef();
  }

  /** #IUnknown */
  IFACEMETHODIMP QueryInterface(REFIID riid, void **ppv)
  {
    static const QITAB qit[] = {QITABENT(CClassFactory, IClassFactory), {0}};
    return QISearch(this, qit, riid, ppv);
  }

  IFACEMETHODIMP_(ULONG) AddRef()
  {
    return InterlockedIncrement(&_cRef);
  }

  IFACEMETHODIMP_(ULONG) Release()
  {
    long cRef = InterlockedDecrement(&_cRef);
    if (cRef == 0) {
      delete this;
    }
    return cRef;
  }

  /** #IClassFactory */
  IFACEMETHODIMP CreateInstance(IUnknown *punkOuter, REFIID riid, void **ppv)
  {
    return punkOuter ? CLASS_E_NOAGGREGATION : _pfnCreate(riid, ppv);
  }

  IFACEMETHODIMP LockServer(BOOL fLock)
  {
    if (fLock) {
      DllAddRef();
    }
    else {
      DllRelease();
    }
    return S_OK;
  }

 private:
  ~CClassFactory()
  {
    DllRelease();
  }

  long _cRef;
  PFNCREATEINSTANCE _pfnCreate;
};

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, void **ppv)
{
  return CClassFactory::CreateInstance(
      clsid, c_rgClassObjectInit, ARRAYSIZE(c_rgClassObjectInit), riid, ppv);
}

/**
 * A struct to hold the information required for a registry entry.
 */
struct REGISTRY_ENTRY {
  HKEY hkeyRoot;
  PCWSTR pszKeyName;
  PCWSTR pszValueName;
  DWORD dwValueType;
  /** These two fields could/should have been a union, but C++ */
  PCWSTR pszData;
  /** Only lets you initialize the first field in a union. */
  DWORD dwData;
};

/**
 * Creates a registry key (if needed) and sets the default value of the key.
 */
HRESULT CreateRegKeyAndSetValue(const REGISTRY_ENTRY *pRegistryEntry)
{
  HKEY hKey;
  HRESULT hr = HRESULT_FROM_WIN32(RegCreateKeyExW(pRegistryEntry->hkeyRoot,
                                                  pRegistryEntry->pszKeyName,
                                                  0,
                                                  nullptr,
                                                  REG_OPTION_NON_VOLATILE,
                                                  KEY_SET_VALUE,
                                                  nullptr,
                                                  &hKey,
                                                  nullptr));
  if (SUCCEEDED(hr)) {
    /* All this just to support #REG_DWORD. */
    DWORD size;
    DWORD data;
    BYTE *lpData = (LPBYTE)pRegistryEntry->pszData;
    switch (pRegistryEntry->dwValueType) {
      case REG_SZ:
        size = ((DWORD)wcslen(pRegistryEntry->pszData) + 1) * sizeof(WCHAR);
        break;
      case REG_DWORD:
        size = sizeof(DWORD);
        data = pRegistryEntry->dwData;
        lpData = (BYTE *)&data;
        break;
      default:
        return E_INVALIDARG;
    }

    hr = HRESULT_FROM_WIN32(RegSetValueExW(
        hKey, pRegistryEntry->pszValueName, 0, pRegistryEntry->dwValueType, lpData, size));
    RegCloseKey(hKey);
  }
  return hr;
}

/**
 * Registers this COM server.
 */
STDAPI DllRegisterServer()
{
  HRESULT hr;

  WCHAR szModuleName[MAX_PATH];

  if (!GetModuleFileNameW(g_hInst, szModuleName, ARRAYSIZE(szModuleName))) {
    hr = HRESULT_FROM_WIN32(GetLastError());
  }
  else {
    const REGISTRY_ENTRY rgRegistryEntries[] = {
        /* `RootKey KeyName ValueName ValueType Data` */
        {HKEY_CURRENT_USER,
         L"Software\\Classes\\CLSID\\" SZ_CLSID_BLENDTHUMBHANDLER,
         nullptr,
         REG_SZ,
         SZ_BLENDTHUMBHANDLER},
        {HKEY_CURRENT_USER,
         L"Software\\Classes\\CLSID\\" SZ_CLSID_BLENDTHUMBHANDLER L"\\InProcServer32",
         nullptr,
         REG_SZ,
         szModuleName},
        {HKEY_CURRENT_USER,
         L"Software\\Classes\\CLSID\\" SZ_CLSID_BLENDTHUMBHANDLER L"\\InProcServer32",
         L"ThreadingModel",
         REG_SZ,
         L"Apartment"},
        {HKEY_CURRENT_USER,
         L"Software\\Classes\\.blend\\",
         L"Treatment",
         REG_DWORD,
         0,
         0}, /* This doesn't appear to do anything. */
        {HKEY_CURRENT_USER,
         SZ_KEY_BLEND_SHELLEX,
         nullptr,
         REG_SZ,
         SZ_CLSID_BLENDTHUMBHANDLER},
    };

    hr = S_OK;
    for (int i = 0; i < ARRAYSIZE(rgRegistryEntries) && SUCCEEDED(hr); i++) {
      hr = CreateRegKeyAndSetValue(&rgRegistryEntries[i]);
    }
  }
  if (SUCCEEDED(hr)) {
    /* This tells the shell to invalidate the thumbnail cache.
     * This is important because any `.blend` files viewed before registering this handler
     * would otherwise show cached blank thumbnails. */
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, nullptr, nullptr);
  }
  return hr;
}

/**
 * Whether the `.blend` thumbnail handler key names this DLL's CLSID. The key is shared with any
 * other application that registers a `.blend` thumbnail handler (a stock Blender install does),
 * so it is only removed when it is ours.
 */
static bool blend_shell_handler_is_ours()
{
  WCHAR value[64] = {0};
  DWORD size = sizeof(value) - sizeof(WCHAR);
  const LSTATUS status = RegGetValueW(
      HKEY_CURRENT_USER, SZ_KEY_BLEND_SHELLEX, nullptr, RRF_RT_REG_SZ, nullptr, value, &size);
  return status == ERROR_SUCCESS && _wcsicmp(value, SZ_CLSID_BLENDTHUMBHANDLER) == 0;
}

/**
 * Unregisters this COM server
 */
STDAPI DllUnregisterServer()
{
  HRESULT hr = S_OK;

  /* The handler's own CLSID is always ours to remove. The `.blend` association key is
   * removed only when it still points at this DLL: after another application took it over,
   * unregistering must leave that application's thumbnails working. */
  PCWSTR rgpszKeys[2] = {L"Software\\Classes\\CLSID\\" SZ_CLSID_BLENDTHUMBHANDLER, nullptr};
  int key_count = 1;
  if (blend_shell_handler_is_ours()) {
    rgpszKeys[key_count++] = SZ_KEY_BLEND_SHELLEX;
  }

  /* Delete the registry entries. */
  for (int i = 0; i < key_count && SUCCEEDED(hr); i++) {
    hr = HRESULT_FROM_WIN32(RegDeleteTreeW(HKEY_CURRENT_USER, rgpszKeys[i]));
    if (hr == HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)) {
      /* If the registry entry has already been deleted, say S_OK. */
      hr = S_OK;
    }
  }
  return hr;
}
