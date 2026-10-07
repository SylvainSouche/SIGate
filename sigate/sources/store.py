"""
sources.store - the source-config registry: data model, plus load/merge/save
logic for combining bundled seed data with a user's own additions.

Called a "source config" throughout, deliberately not "profile" - QGIS
has its own unrelated first-class profile concept, and reusing the term
here would be confusing.

Storage split: bundled seed data (sources/seed.py) is plain Python,
shipped with the plugin. Anything user-growable (a user's own added or
edited source configs) is stored as JSON under a data directory supplied
by the caller - this module takes a plain file path, not a QGIS-specific
call, so it can be used and tested independently of a QGIS environment;
the calling application supplies the actual storage location.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

USER_OVERRIDES_FILENAME = "sigate_sources.json"


@dataclass(frozen=True)
class AuthConfig:
    """How to authenticate against one gateway instance. `kind` is one of:
    "none", "apikey_header" (sent as an HTTP header), or "token" (a
    fetched/refreshed token)."""

    kind: str = "none"
    header_name: Optional[str] = None
    value: Optional[str] = None


@dataclass(frozen=True)
class GatewayConfig:
    """One gateway instance for a source. A source can declare several of
    these, one per gateway type it exposes, and more than one instance of
    the same gateway type if a provider exposes it multiple ways (e.g. a
    public endpoint and a separate authenticated one)."""

    gateway_type: str  # matches a gateway module name: "atom" | "wfs" | "ftp" | "stac" | "wmts_wms" | "csw"
    base_url: str
    auth: AuthConfig = field(default_factory=AuthConfig)
    # Gateway-specific details that don't fit the general model cleanly,
    # kept as a plain string dict rather than growing GatewayConfig's own
    # fields for every gateway type's own quirks.
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceConfig:
    """One provider's full configuration - a named entry in the source
    registry, potentially exposing several gateway types at once."""

    key: str  # stable identifier, used for namespacing (e.g. in a providerKey)
    display_name: str
    country: str
    gateways: List[GatewayConfig] = field(default_factory=list)
    # The publishing organisation, the middle level of the connection
    # manager's country > organisation > entry pickers. Empty means
    # "work it out from the display name" (sources.organisations).
    organisation: str = ""

    def gateway(self, gateway_type: str) -> Optional[GatewayConfig]:
        """The first configured gateway of the given type, or None. If a
        source declares more than one instance of the same gateway type,
        use gateways_of_type instead to see all of them."""
        for gw in self.gateways:
            if gw.gateway_type == gateway_type:
                return gw
        return None

    def gateways_of_type(self, gateway_type: str) -> List[GatewayConfig]:
        """All configured gateways of the given type, in declaration order."""
        return [gw for gw in self.gateways if gw.gateway_type == gateway_type]


def _source_to_dict(source: SourceConfig) -> dict:
    return asdict(source)


def _source_from_dict(data: dict) -> SourceConfig:
    gateways = [
        GatewayConfig(
            gateway_type=gw["gateway_type"],
            base_url=gw["base_url"],
            auth=AuthConfig(**gw.get("auth", {})),
            extra=dict(gw.get("extra", {})),
        )
        for gw in data.get("gateways", [])
    ]
    return SourceConfig(
        key=data["key"],
        display_name=data["display_name"],
        country=data["country"],
        gateways=gateways,
        organisation=data.get("organisation", ""),
    )


def load_user_overrides(data_dir: Path) -> List[SourceConfig]:
    """Reads the user's own added or edited source configs from
    <data_dir>/sigate_sources.json. Returns an empty list if the file
    doesn't exist yet - not an error condition."""
    path = Path(data_dir) / USER_OVERRIDES_FILENAME
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [_source_from_dict(entry) for entry in raw]


def save_user_overrides(data_dir: Path, sources: List[SourceConfig]) -> None:
    """Writes the full list of user overrides back to disk, replacing
    whatever was there. Callers wanting to add or edit one entry should
    read the current list first, modify it, then call this with the full
    updated list - see add_or_replace_source for the common case."""
    path = Path(data_dir)
    path.mkdir(parents=True, exist_ok=True)
    out_path = path / USER_OVERRIDES_FILENAME
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            [_source_to_dict(s) for s in sources], f, indent=2, ensure_ascii=False
        )


def add_or_replace_source(data_dir: Path, source: SourceConfig) -> List[SourceConfig]:
    """Adds a new source to the user's overrides, or replaces an existing
    one with the same key. Returns the updated full list of user
    overrides, already saved to disk."""
    existing = load_user_overrides(data_dir)
    updated = [s for s in existing if s.key != source.key]
    updated.append(source)
    save_user_overrides(data_dir, updated)
    return updated


def merged_sources(
    seed_sources: List[SourceConfig], data_dir: Path
) -> List[SourceConfig]:
    """Combines bundled seed sources with the user's own overrides. A user
    override with the same key as a seed source replaces it entirely,
    rather than being merged field by field - simpler and more
    predictable than a deep merge."""
    overrides = load_user_overrides(data_dir)
    override_keys = {s.key for s in overrides}
    combined = [s for s in seed_sources if s.key not in override_keys]
    combined.extend(overrides)
    return combined
