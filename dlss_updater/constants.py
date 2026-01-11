from dlss_updater.platform_utils import FEATURES

# Base DLL type map (cross-platform)
DLL_TYPE_MAP = {
    "nvngx_dlss.dll": "DLSS DLL",
    "nvngx_dlssg.dll": "DLSS Frame Generation DLL",
    "nvngx_dlssd.dll": "DLSS Ray Reconstruction DLL",
    "libxess.dll": "XeSS DLL",
    "libxess_dx11.dll": "XeSS DX11 DLL",
    "libxess_fg.dll": "XeSS Frame Generation DLL",
    "libxell.dll": "XeLL DLL",
    "sl.common.dll": "Streamline Shared Library DLL",
    "sl.dlss.dll": "Streamline DLSS Super Resolution DLL",
    "sl.dlss_g.dll": "Streamline DLSS Frame Generation DLL",
    "sl.interposer.dll": "Streamline Graphics API Interception DLL",
    "sl.pcl.dll": "Streamline Parameter/Platform Configuration DLL",
    "sl.reflex.dll": "Streamline Reflex Low-Latency DLL",
    "amd_fidelityfx_vk.dll": "AMD FidelityFX Super Resolution (FSR) Vulkan DLL",
    "amd_fidelityfx_dx12.dll": "AMD FidelityFX Super Resolution (FSR) DirectX 12 DLL",
    "amd_fidelityfx_upscaler_dx12.dll": "AMD FidelityFX Super Resolution 4 (FSR4) Upscaler DLL",
    "amd_fidelityfx_framegeneration_dx12.dll": "AMD FidelityFX Super Resolution 4 (FSR4) Frame Generation DLL",
    "amd_fidelityfx_loader_dx12.dll": "AMD FidelityFX Super Resolution 4 (FSR4) Loader DLL",
}

# DirectStorage DLLs are Windows-only
if FEATURES.directstorage:
    DLL_TYPE_MAP["dstorage.dll"] = "DirectStorage DLL"
    DLL_TYPE_MAP["dstoragecore.dll"] = "DirectStorage Core DLL"


# Base DLL groups (cross-platform)
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
        "libxess_fg.dll",
        "libxell.dll",
    ],
    "FSR": [
        "amd_fidelityfx_vk.dll",
        "amd_fidelityfx_dx12.dll",
        "amd_fidelityfx_upscaler_dx12.dll",
        "amd_fidelityfx_framegeneration_dx12.dll",
        "amd_fidelityfx_loader_dx12.dll",
    ],
}

# DirectStorage is Windows-only
if FEATURES.directstorage:
    DLL_GROUPS["DirectStorage"] = [
        "dstorage.dll",
        "dstoragecore.dll",
    ]


# FSR4 DLL rename mapping - source DLL name -> target DLL name
FSR4_DLL_RENAME_MAP = {
    "amd_fidelityfx_loader_dx12.dll": "amd_fidelityfx_dx12.dll"
}