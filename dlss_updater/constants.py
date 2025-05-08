DLL_TYPE_MAP = {
    "nvngx_dlss.dll": "DLSS DLL",
    "nvngx_dlssg.dll": "DLSS Frame Generation DLL",
    "nvngx_dlssd.dll": "DLSS Ray Reconstruction DLL",
    "libxess.dll": "XeSS DLL",
    "libxess_dx11.dll": "XeSS DX11 DLL",
    "sl.common.dll": "Streamline Shared Library DLL",
    "sl.dlss.dll": "Streamline DLSS Super Resolution DLL",
    "sl.dlss_g.dll": "Streamline DLSS Frame Generation DLL",
    "sl.interposer.dll": "Streamline Graphics API Interception DLL",
    "sl.pcl.dll": "Streamline Parameter/Platform Configuration DLL",
    "sl.reflex.dll": "Streamline Reflex Low-Latency DLL",
}


DLL_GROUPS = {
    "DLSS": [
        "nvngx_dlss.dll",
        "nvngx_dlssg.dll",
        "nvngx_dlssd.dll",
        "sl.common.dll",
        "sl.dlss.dll",
        "sl.dlss_g.dll",
        "sl.interposer.dll",
        "sl.pcl.dll",
        "sl.reflex.dll",
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
