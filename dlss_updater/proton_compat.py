"""
Proton compatibility-tool detection for Steam games (Linux).

Reads Steam's ``config/config.vdf`` ``CompatToolMapping`` section to determine
which Proton build each game runs under, and classifies each build's support
for the community upscaler-upgrade launch options (``PROTON_DLSS_UPGRADE``,
``PROTON_FSR4_UPGRADE``, ``PROTON_XESS_UPGRADE``, indicator overlays).

Capability sources (verified July 2026):
- GE-Proton README: documents DLSS/FSR4(+RDNA3)/XeSS upgrade + indicator vars
- Proton-CachyOS README: same set (plus extras we don't expose yet)
- Proton-EM docs/FSR4.md: documents the FSR4 upgrade path only
- Valve upstream Proton (proton_experimental / proton_9 / hotfix): none of the
  upgrade downloaders ship; only the DXVK-NVAPI DRS preset variables apply.

The DXVK-NVAPI DRS variables (SR/RR preset selection, FG override, debug
overlay) work on any Proton new enough to bundle dxvk-nvapi and are therefore
not part of the capability gating here.
"""

from pathlib import Path

import msgspec

from dlss_updater.logger import setup_logger

logger = setup_logger()

# =============================================================================
# Capability keys (referenced by linux_dlss_utils capability gating)
# =============================================================================

CAP_DLSS_UPGRADE = "dlss_upgrade"
CAP_DLSS_INDICATOR = "dlss_indicator"
CAP_FSR4_UPGRADE = "fsr4_upgrade"
CAP_FSR4_INDICATOR = "fsr4_indicator"
CAP_XESS_UPGRADE = "xess_upgrade"

ALL_UPGRADE_CAPS: frozenset[str] = frozenset({
    CAP_DLSS_UPGRADE,
    CAP_DLSS_INDICATOR,
    CAP_FSR4_UPGRADE,
    CAP_FSR4_INDICATOR,
    CAP_XESS_UPGRADE,
})

# Proton-EM only documents the FSR4 upgrade path (docs/FSR4.md)
_EM_CAPS: frozenset[str] = frozenset({CAP_FSR4_UPGRADE})

_NO_CAPS: frozenset[str] = frozenset()


class ProtonToolInfo(msgspec.Struct, frozen=True):
    """Classification of a Steam compatibility tool."""
    raw_name: str          # Tool name as stored in config.vdf ("" = Valve default)
    display_name: str      # Human-readable label for the UI
    family: str            # "valve" | "ge" | "cachyos" | "em" | "runtime" | "unknown"
    is_proton: bool        # False for native runtimes (Steam Linux Runtime etc.)
    capabilities: frozenset[str]


def classify_compat_tool(name: str | None) -> ProtonToolInfo:
    """
    Classify a compat tool name from CompatToolMapping into its family and
    supported PROTON_* upgrade capabilities.

    Args:
        name: Tool name from config.vdf (e.g. "GE-Proton10-4", "proton-cachyos",
              "proton_experimental"). None/"" means Steam's default (Valve Proton
              or native, unknown here).

    Returns:
        ProtonToolInfo with conservative capabilities (unknown -> none).
    """
    if not name:
        return ProtonToolInfo(
            raw_name="",
            display_name="Steam default (Valve Proton)",
            family="valve",
            is_proton=True,
            capabilities=_NO_CAPS,
        )

    n = name.lower()

    if n.startswith(("ge-proton", "proton-ge", "geproton")):
        return ProtonToolInfo(name, name, "ge", True, ALL_UPGRADE_CAPS)

    if "cachyos" in n:
        return ProtonToolInfo(name, name, "cachyos", True, ALL_UPGRADE_CAPS)

    if n.startswith(("proton-em", "proton_em", "em-proton")):
        return ProtonToolInfo(name, name, "em", True, _EM_CAPS)

    # Valve official tools: proton_experimental, proton_hotfix, proton_9,
    # proton_10, proton_63 (legacy), "proton-stable" beta naming.
    if n.startswith(("proton_", "proton-stable", "proton experimental")):
        return ProtonToolInfo(name, name, "valve", True, _NO_CAPS)

    # Native Linux runtimes — not Proton at all, no Windows DLLs involved.
    if "steamlinuxruntime" in n or n.startswith(("slr", "sniper", "soldier", "scout")):
        return ProtonToolInfo(name, name, "runtime", False, _NO_CAPS)

    # Unknown third-party fork — probably Proton-based, but we can't vouch for
    # the upgrade downloaders. Conservative: no upgrade capabilities.
    return ProtonToolInfo(name, name, "unknown", True, _NO_CAPS)


# =============================================================================
# Steam config.vdf parsing
# =============================================================================

def _walk_ci(data: dict, *keys: str) -> dict | None:
    """Walk nested dicts by case-insensitive keys; None if any level missing."""
    node = data
    for key in keys:
        if not isinstance(node, dict):
            return None
        match = None
        for k, v in node.items():
            if k.lower() == key.lower():
                match = v
                break
        if match is None:
            return None
        node = match
    return node if isinstance(node, dict) else None


def extract_compat_tool_mapping(vdf_data: dict) -> dict[str, str]:
    """
    Extract {app_id: tool_name} from parsed config.vdf data.

    Structure: InstallConfigStore/Software/Valve/Steam/CompatToolMapping/<appid>
    where each entry holds {"name": "...", "config": "", "priority": "..."}.
    App id "0" is Steam's global default compat tool, if the user set one.
    Key casing varies between installs, so the walk is case-insensitive.
    """
    mapping_node = _walk_ci(
        vdf_data, "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping"
    )
    if not mapping_node:
        return {}

    mapping: dict[str, str] = {}
    for app_id, entry in mapping_node.items():
        if isinstance(entry, dict):
            tool_name = entry.get("name") or entry.get("Name") or ""
            if tool_name:
                mapping[str(app_id)] = tool_name
    return mapping


def resolve_tool_for_app(mapping: dict[str, str], app_id: int | str | None) -> str | None:
    """
    Resolve the compat tool for an app: per-app entry wins, then the global
    default ("0"), then None (Steam's own default Proton / native).
    """
    if app_id is not None:
        tool = mapping.get(str(app_id))
        if tool:
            return tool
    return mapping.get("0") or None


def get_steam_root() -> Path | None:
    """Find the Steam root that contains config/config.vdf (Linux paths)."""
    from dlss_updater.linux_paths import STEAM_PATHS

    for root in STEAM_PATHS:
        try:
            if (root / "config" / "config.vdf").is_file():
                return root
        except OSError:
            continue
    return None


async def get_compat_tool_mapping(steam_root: Path | None = None) -> dict[str, str]:
    """
    Read the CompatToolMapping from Steam's config.vdf.

    Args:
        steam_root: Steam root override (auto-detected when None).

    Returns:
        {app_id_str: tool_name}; empty when Steam/config.vdf is not found.
    """
    from dlss_updater.vdf_parser import VDFParser

    if steam_root is None:
        steam_root = get_steam_root()
    if steam_root is None:
        logger.debug("No Steam root with config.vdf found; compat mapping empty")
        return {}

    data = await VDFParser.parse_file(steam_root / "config" / "config.vdf")
    mapping = extract_compat_tool_mapping(data)
    logger.debug(f"CompatToolMapping entries: {len(mapping)}")
    return mapping
