DLL_TYPE_MAP = {
    "nvngx_dlss.dll": "DLSS DLL",
    "nvngx_dlssg.dll": "DLSS Frame Generation DLL",
    "nvngx_dlssd.dll": "DLSS Ray Reconstruction DLL",
    "libxess.dll": "XeSS DLL",
    "libxess_dx11.dll": "XeSS DX11 DLL",
    "dstorage.dll": "DirectStorage DLL",
    "dstoragecore.dll": "DirectStorage Core DLL",
    "sl.common.dll": "Streamline Shared Library DLL",
    "sl.dlss.dll": "Streamline DLSS Super Resolution DLL",
    "sl.dlss_g.dll": "Streamline DLSS Frame Generation DLL",
    "sl.interposer.dll": "Streamline Graphics API Interception DLL",
    "sl.pcl.dll": "Streamline Parameter/Platform Configuration DLL",
    "sl.reflex.dll": "Streamline Reflex Low-Latency DLL",
    "amd_fidelityfx_vk.dll": "AMD FidelityFX Super Resolution (FSR) Vulkan DLL",
    "amd_fidelityfx_dx12.dll": "AMD FidelityFX Super Resolution (FSR) DirectX 12 DLL",
}


DLL_GROUPS = {
    "DLSS": [
        "nvngx_dlss.dll",
        "nvngx_dlssg.dll",
        "nvngx_dlssd.dll",
    ],
    "Streamline": [
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
    "FSR": [
        "amd_fidelityfx_vk.dll",
        "amd_fidelityfx_dx12.dll",
    ],
}