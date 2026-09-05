from dlss_updater.platform_utils import FEATURES

# Base DLL type map (cross-platform)
DLL_TYPE_MAP = {
    "nvngx_dlss.dll": "DLSS DLL",
    "nvngx_dlssg.dll": "DLSS Frame Generation DLL",
    "nvngx_dlssd.dll": "DLSS Ray Reconstruction DLL",
    "nvngx_dlssnr.dll": "DLSS Neural Rendering DLL",
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
    "sl.directsr.dll": "Streamline DirectSR DLL",
    "sl.dlss_d.dll": "Streamline DLSS Ray Reconstruction DLL",
    "sl.nis.dll": "Streamline NIS Upscaling DLL",
    "amd_fidelityfx_vk.dll": "AMD FidelityFX Super Resolution (FSR) Vulkan DLL",
    "amd_fidelityfx_dx12.dll": "AMD FidelityFX Super Resolution (FSR) DirectX 12 DLL",
    "amd_fidelityfx_upscaler_dx12.dll": "AMD FidelityFX Super Resolution 4 (FSR4) Upscaler DLL",
    "amd_fidelityfx_framegeneration_dx12.dll": "AMD FidelityFX Super Resolution 4 (FSR4) Frame Generation DLL",
    "amd_fidelityfx_loader_dx12.dll": "AMD FidelityFX Super Resolution 4 (FSR4) Loader DLL",
    "amd_fidelityfx_denoiser_dx12.dll": "AMD FSR Ray Regeneration (Denoiser) DLL",
    "amd_fidelityfx_radiancecache_dx12.dll": "AMD FSR Radiance Caching DLL (Preview)",
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
        # DLSS 5 Neural Rendering (released 2026-09-03, RTX 50 series only).
        # Runs alongside SR/RR/Frame Generation rather than replacing any of
        # them, so it is an ordinary member of this group. Too large for the
        # DLL repo's dlls/ directory — see resolve_download_url.
        "nvngx_dlssnr.dll",
    ],
    "Streamline": [
        "sl.common.dll",
        "sl.dlss.dll",
        "sl.dlss_g.dll",
        "sl.interposer.dll",
        "sl.pcl.dll",
        "sl.reflex.dll",
        "sl.directsr.dll",
        "sl.dlss_d.dll",
        "sl.nis.dll",
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
        # FSR Ray Regeneration. Replace-only: shipped by Call of Duty: Black Ops 7
        # and Crimson Desert, and never added to a game that lacks it.
        "amd_fidelityfx_denoiser_dx12.dll",
        # Preview — NOT updated unless the user opts in. See PREVIEW_DLLS below.
        # Listed here so that if a game does ship one it still groups under FSR
        # in the UI rather than showing up as an unknown DLL.
        "amd_fidelityfx_radiancecache_dx12.dll",
    ],
}


# DLLs that belong to a technology group but are PRE-RELEASE and must never be
# touched on the strength of that group's preference alone.
#
# AMD ships FSR Radiance Caching as "(Preview)" in its own documentation and the
# DLL reports version 0.9.0 — below 1.0 by AMD's own numbering. At the time of
# writing no shipping game, driver path or benchmark uses it; it exists so engine
# teams can integrate ahead of release. Replacing it is therefore not advisable,
# and the app requires a separate, explicitly-acknowledged opt-in
# (`UpdatePreferencesConfig.update_fsr_radiance_cache`, default False) on top of
# the FSR technology toggle. See utils.is_dll_update_enabled.
PREVIEW_DLLS = frozenset({
    "amd_fidelityfx_radiancecache_dx12.dll",
})

# Maps a preview DLL to the preference token that unlocks it.
PREVIEW_DLL_PREFERENCE = {
    "amd_fidelityfx_radiancecache_dx12.dll": "FSR_RadianceCache",
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