DLL_TYPE_MAP = {
    "nvngx_dlss.dll": "DLSS DLL",
    "nvngx_dlssg.dll": "DLSS Frame Generation DLL",
    "nvngx_dlssd.dll": "DLSS Ray Reconstruction DLL",
    "libxess.dll": "XeSS DLL",
    "libxess_dx11.dll": "XeSS DX11 DLL",
}

DLL_GROUPS = {
    "DLSS": [
        "nvngx_dlss.dll",
        "nvngx_dlssg.dll",
        "nvngx_dlssd.dll",
    ],
    "XeSS": [
        "libxess.dll",
        "libxess_dx11.dll",
    ],
    "DirectStorage": [
        "dstorage.dll",
        "dstoragecore.dll",
    ],
}