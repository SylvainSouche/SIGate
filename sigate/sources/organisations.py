"""
sources.organisations - the organisation level of the connection
manager's country > organisation > entry pickers, derived from a source's
display name when it does not say so itself.

Pure Python (no Qt). Display names in the registry read like
"Regione Piemonte - base cartography (Italy)": the organisation is the
prefix, the rest names the entry, and the trailing "(Italy)" repeats the
country that is already picked above. A source can state its organisation
explicitly (SourceConfig.organisation); this module only fills the gap for
connections added by hand.
"""

import re
from typing import Optional

from .store import SourceConfig

_TRAILING_PARENTHETICAL = re.compile(r"\s*\([^()]*\)\s*$")
_ORG_SEPARATORS = re.compile(r"\s+[-–—]\s+")
_LEADING_SEPARATORS = " -–—:,"


def _without_trailing_parenthetical(name: str) -> str:
    stripped = _TRAILING_PARENTHETICAL.sub("", name).strip()
    return stripped or name.strip()


def derive_organisation(display_name: str) -> str:
    """The organisation part of a display name: drop a trailing
    "(country)", then keep what precedes the first " - "."""
    name = _without_trailing_parenthetical(display_name or "")
    first = _ORG_SEPARATORS.split(name, maxsplit=1)[0].strip()
    return first or (display_name or "").strip()


def organisation_of(source: SourceConfig) -> str:
    """The source's organisation: stated, else derived from its name."""
    return (source.organisation or "").strip() or derive_organisation(
        source.display_name
    )


def organisation_key(name: str) -> str:
    """Case-insensitive grouping key, so "IGN" and "ign" are one."""
    return (name or "").strip().casefold()


def entry_name(source: SourceConfig) -> str:
    """The source's display name without its organisation prefix and
    without the trailing "(country)": "Regione Piemonte - base
    cartography (Italy)" -> "base cartography". Empty when nothing is
    left ("IGN (France)")."""
    organisation = organisation_of(source)
    name = _without_trailing_parenthetical(source.display_name or "")
    if name.casefold().startswith(organisation.casefold()):
        name = name[len(organisation) :].lstrip(_LEADING_SEPARATORS)
    return name.strip()


def short_label(source: SourceConfig, role: Optional[str], shared: bool) -> str:
    """The entry shown once the organisation is already picked. A source
    with several entries of this kind (`shared`) is told apart by role;
    a lone entry shows the source's own entry name, or the organisation
    when the name added nothing."""
    name = entry_name(source)
    if shared:
        if name and role:
            return f"{name} — {role}"
        return role or name or organisation_of(source)
    return name or role or organisation_of(source)
