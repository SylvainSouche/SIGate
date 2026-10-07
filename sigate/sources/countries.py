"""
sources.countries - one canonical, English country name per country, so
that a connection typed as "NO", "no", "Norge" or "norway" is grouped with
the bundled "Norway" in the connection manager's country filter.

Pure Python (no Qt). Recognises ISO 3166 alpha-2 and alpha-3 codes, the
English name and the common local names, all case-insensitively and
ignoring accents. Anything not recognised is kept as typed, so a country
missing from the table still works - it just only groups with identical
spellings.
"""

import unicodedata
from typing import Dict, Optional

# (alpha-2, alpha-3, English name, local / alternative names...)
_COUNTRIES = [
    ("AD", "AND", "Andorra"),
    ("AL", "ALB", "Albania", "Shqipëria"),
    ("AT", "AUT", "Austria", "Österreich"),
    ("AU", "AUS", "Australia"),
    ("BA", "BIH", "Bosnia and Herzegovina", "Bosnia-Herzegovina"),
    ("BE", "BEL", "Belgium", "Belgique", "België", "Belgien"),
    ("BG", "BGR", "Bulgaria"),
    ("BR", "BRA", "Brazil", "Brasil"),
    ("BY", "BLR", "Belarus"),
    ("CA", "CAN", "Canada"),
    ("CH", "CHE", "Switzerland", "Schweiz", "Suisse", "Svizzera", "Svizra"),
    ("CL", "CHL", "Chile"),
    ("CN", "CHN", "China"),
    ("CY", "CYP", "Cyprus"),
    ("CZ", "CZE", "Czechia", "Czech Republic", "Česko", "Česká republika"),
    ("DE", "DEU", "Germany", "Deutschland", "Allemagne"),
    ("DK", "DNK", "Denmark", "Danmark"),
    ("EE", "EST", "Estonia", "Eesti"),
    ("ES", "ESP", "Spain", "España", "Espagne"),
    ("FI", "FIN", "Finland", "Suomi"),
    ("FR", "FRA", "France"),
    ("GB", "GBR", "United Kingdom", "UK", "Great Britain", "Royaume-Uni"),
    ("GR", "GRC", "Greece", "Ελλάδα"),
    ("HR", "HRV", "Croatia", "Hrvatska"),
    ("HU", "HUN", "Hungary", "Magyarország"),
    ("IE", "IRL", "Ireland", "Éire"),
    ("IN", "IND", "India"),
    ("IS", "ISL", "Iceland", "Ísland"),
    ("IT", "ITA", "Italy", "Italia", "Italie"),
    ("JP", "JPN", "Japan"),
    ("LI", "LIE", "Liechtenstein"),
    ("LT", "LTU", "Lithuania", "Lietuva"),
    ("LU", "LUX", "Luxembourg", "Luxemburg"),
    ("LV", "LVA", "Latvia", "Latvija"),
    ("MC", "MCO", "Monaco"),
    ("MD", "MDA", "Moldova"),
    ("ME", "MNE", "Montenegro", "Crna Gora"),
    ("MK", "MKD", "North Macedonia", "Macedonia"),
    ("MT", "MLT", "Malta"),
    ("MX", "MEX", "Mexico", "México"),
    ("NL", "NLD", "Netherlands", "Nederland", "The Netherlands", "Holland"),
    ("NO", "NOR", "Norway", "Norge", "Noreg", "Norvège"),
    ("NZ", "NZL", "New Zealand"),
    ("PL", "POL", "Poland", "Polska"),
    ("PT", "PRT", "Portugal"),
    ("RO", "ROU", "Romania", "România"),
    ("RS", "SRB", "Serbia", "Srbija"),
    ("RU", "RUS", "Russia"),
    ("SE", "SWE", "Sweden", "Sverige"),
    ("SI", "SVN", "Slovenia", "Slovenija"),
    ("SK", "SVK", "Slovakia", "Slovensko"),
    ("SM", "SMR", "San Marino"),
    ("UA", "UKR", "Ukraine"),
    ("US", "USA", "United States", "United States of America", "États-Unis"),
    ("VA", "VAT", "Vatican City", "Holy See"),
    ("XK", "XKX", "Kosovo"),
    ("ZA", "ZAF", "South Africa"),
]


def _fold(text: str) -> str:
    """Case- and accent-insensitive comparison key."""
    decomposed = unicodedata.normalize("NFKD", text.strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _build_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for alpha2, alpha3, english, *others in _COUNTRIES:
        for name in (alpha2, alpha3, english, *others):
            index[_fold(name)] = english
    return index


_INDEX = _build_index()


def canonical_country(text: Optional[str]) -> str:
    """The English country name for a code or a name; the trimmed input
    unchanged when it isn't recognised; "" for nothing."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    return _INDEX.get(_fold(cleaned), cleaned)
