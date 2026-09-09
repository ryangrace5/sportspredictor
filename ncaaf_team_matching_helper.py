import json
import re
from difflib import get_close_matches
from typing import Dict, Iterable, List, Optional, Tuple

_STOPWORDS = {"university", "univ", "the", "of", "and", "&"}
_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def canonicalize(name: str) -> str:
    s = (name or "").lower().strip()
    s = s.replace("’", "'").replace("ʻ", "'").replace("`", "'")
    s = s.replace("hawai'i", "hawaii")
    s = _PUNCT_RE.sub(" ", s)
    parts = [p for p in s.split() if p and p not in _STOPWORDS]
    return " ".join(parts)


def load_mapping(path: str) -> Dict[str, dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_mapping(path: str, mapping: Dict[str, dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)


def _best_match(
    value: str,
    candidates: Iterable[str],
    cutoff: float = 0.82,
) -> Optional[str]:
    matches = get_close_matches(value, list(candidates), n=1, cutoff=cutoff)
    return matches[0] if matches else None


def _build_canonical_index(mapping: Dict[str, dict]):
    canon_primary = {}
    canon_alias = {}
    for primary, meta in mapping.items():
        canon_primary[canonicalize(primary)] = primary
        for alias in meta.get("aliases", []) or []:
            canon_alias[canonicalize(alias)] = primary
    return canon_primary, canon_alias


# Known schedule-name families are checked before fuzzy matching. This prevents
# old or accidentally truncated mapping entries (for example "Texas Tech Red")
# from winning over the canonical Google Sheet team name.
COMMON_ALIASES: Dict[str, List[str]] = {
    "USC": ["Southern California", "USC Trojans"],
    "LSU": ["Louisiana State", "LSU Tigers"],
    "UMass": ["Massachusetts", "Massachusetts Minutemen"],
    "BYU": ["Brigham Young", "BYU Cougars"],
    "UCLA": ["UCLA Bruins"],
    "Ole Miss": ["Mississippi", "Mississippi Rebels"],
    "Cal": ["California", "California Golden Bears"],
    "UTEP": ["Texas-El Paso", "Texas El Paso", "UTEP Miners"],
    "UTSA": ["Texas San Antonio", "UTSA Roadrunners"],
    "Louisiana": ["Louisiana-Lafayette", "ULL", "Louisiana Ragin' Cajuns"],
    "Louisiana-Monroe": ["ULM", "Louisiana Monroe"],
    "Kansas State": ["Kansas St", "K-State", "Kansas State Wildcats"],
    "Western Kentucky": ["WKU", "Western Kentucky Hilltoppers"],
    "Georgia State": ["Georgia St"],
    "Southeastern Louisiana": ["SE Louisiana", "Southeastern Louisiana Lions"],
    "Southeast Missouri State": [
        "SE Missouri State",
        "Southeast Missouri St",
        "SE Missouri St",
        "SEMO",
    ],
    "Hawaii": ["Hawai'i", "Hawaiʻi", "Hawaii Rainbow Warriors"],
    "Arizona State": ["Arizona St"],
    "Ohio State": ["Ohio St"],
    "Florida State": ["Florida St"],
    "Penn State": ["Penn St"],
    "Texas State": ["Texas State Bobcats"],
    "Texas Tech": ["Texas Tech Red Raiders", "Texas Tech Red", "TTU"],
    "Air Force": ["Air Force Falcons"],
    "Duquesne": ["Duquesne Dukes"],
    "Bucknell": ["Bucknell Bison"],
    "Portland State": ["Portland State Vikings"],
    "Northern Arizona": ["Northern Arizona Lumberjacks"],
    "North Dakota": ["North Dakota Fighting Hawks"],
    "Chattanooga": ["Chattanooga Mocs"],
}

ABBREV_TO_PRIMARY = {
    "USC": "USC",
    "LSU": "LSU",
    "UCLA": "UCLA",
    "BYU": "BYU",
    "UMASS": "UMass",
    "WKU": "Western Kentucky",
    "SEMO": "Southeast Missouri State",
    "ULL": "Louisiana",
    "ULM": "Louisiana-Monroe",
    "UTEP": "UTEP",
    "UTSA": "UTSA",
    "K-STATE": "Kansas State",
    "KSTATE": "Kansas State",
    "OLE MISS": "Ole Miss",
    "PENN ST": "Penn State",
    "OHIO ST": "Ohio State",
    "ARIZONA ST": "Arizona State",
    "GEORGIA ST": "Georgia State",
    "TTU": "Texas Tech",
}


def _normalize_to_primary(team_name: str) -> Optional[str]:
    abbr = team_name.upper().replace(".", "").strip()
    return ABBREV_TO_PRIMARY.get(abbr)


def _seed_common_aliases(mapping: Dict[str, dict]) -> None:
    for primary, aliases in COMMON_ALIASES.items():
        entry = mapping.get(
            primary,
            {
                "aliases": [],
                "abbr": "",
                "stats_key": primary,
                "PF": None,
                "PA": None,
                "PPG": None,
            },
        )
        existing = set(entry.get("aliases", []) or [])
        existing.update(aliases)
        entry["aliases"] = sorted(existing)
        if not entry.get("stats_key"):
            entry["stats_key"] = primary
        for field in ("PF", "PA", "PPG"):
            entry.setdefault(field, None)
        mapping[primary] = entry


def _sheet_key_for(
    primary: str,
    mapping: Dict[str, dict],
    sheet_names: List[str],
    cutoff_sheet: float,
) -> Optional[str]:
    if not sheet_names:
        return None

    entry = mapping.get(primary) or {}
    preferred_names = [primary, entry.get("stats_key")] + list(entry.get("aliases", []) or [])
    preferred_canon = {canonicalize(name) for name in preferred_names if name}

    for sheet_name in sheet_names:
        if canonicalize(sheet_name) in preferred_canon:
            return sheet_name

    sheet_canon = [canonicalize(name) for name in sheet_names]
    match = _best_match(canonicalize(primary), sheet_canon, cutoff_sheet)
    if match:
        return sheet_names[sheet_canon.index(match)]
    return None


def _stats_key_for(
    primary: str,
    mapping: Dict[str, dict],
    sheet_names: List[str],
    cutoff_sheet: float,
) -> str:
    sheet_key = _sheet_key_for(primary, mapping, sheet_names, cutoff_sheet)
    if sheet_key:
        return sheet_key
    if primary in mapping:
        return mapping[primary].get("stats_key") or primary
    return primary


def _known_family_primary(team_name: str) -> Optional[str]:
    cn = canonicalize(team_name)
    for primary, aliases in COMMON_ALIASES.items():
        if cn in {canonicalize(name) for name in [primary] + aliases}:
            return primary
    return None


def resolve_team(
    team_name: str,
    mapping: Dict[str, dict],
    sheet_names: Optional[Iterable[str]] = None,
    cutoff_alias: float = 0.82,
    cutoff_sheet: float = 0.78,
) -> Tuple[str, str]:
    """Return ``(primary_key, stats_key)`` for a schedule team name.

    Matching order is intentionally conservative: known aliases and explicit
    disambiguation first, then exact canonical mapping, then fuzzy mapping, and
    finally fuzzy alignment to Google Sheet names.
    """
    if not team_name:
        return "", ""

    sheet_list = list(sheet_names or [])

    norm_primary = _normalize_to_primary(team_name)
    if norm_primary:
        return norm_primary, _stats_key_for(
            norm_primary, mapping, sheet_list, cutoff_sheet
        )

    low = team_name.lower()
    if "miami" in low:
        miami_is_ohio = any(x in low for x in ["(oh", " ohio", "redhawks"])
        primary = "Miami (OH)" if miami_is_ohio else "Miami (FL)"
        return primary, _stats_key_for(primary, mapping, sheet_list, cutoff_sheet)

    family_primary = _known_family_primary(team_name)
    if family_primary:
        return family_primary, _stats_key_for(
            family_primary, mapping, sheet_list, cutoff_sheet
        )

    canon_primary, canon_alias = _build_canonical_index(mapping)
    cn = canonicalize(team_name)

    if cn in canon_primary:
        primary = canon_primary[cn]
        return primary, _stats_key_for(primary, mapping, sheet_list, cutoff_sheet)

    if cn in canon_alias:
        primary = canon_alias[cn]
        return primary, _stats_key_for(primary, mapping, sheet_list, cutoff_sheet)

    known_canon = list(canon_primary.keys()) + list(canon_alias.keys())
    match = _best_match(cn, known_canon, cutoff_alias)
    if match:
        primary = canon_primary.get(match) or canon_alias.get(match)
        return primary, _stats_key_for(primary, mapping, sheet_list, cutoff_sheet)

    if sheet_list:
        sheet_canon = [canonicalize(name) for name in sheet_list]
        sheet_match = _best_match(cn, sheet_canon, cutoff_sheet)
        if sheet_match:
            stats_key = sheet_list[sheet_canon.index(sheet_match)]
            return team_name, stats_key

    return team_name, team_name


def extend_mapping_with_schedule(
    schedule_team_names: Iterable[str],
    mapping: Dict[str, dict],
    sheet_names: Optional[Iterable[str]] = None,
    default_notes: str = "Fill PF/PA/PPG from your Google Sheet or API.",
) -> Dict[str, dict]:
    _seed_common_aliases(mapping)
    sheet_list = list(sheet_names or [])

    for raw in schedule_team_names:
        if not raw:
            continue
        primary, stats_key_guess = resolve_team(raw, mapping, sheet_list)
        if primary not in mapping:
            mapping[primary] = {
                "aliases": [] if raw == primary else [raw],
                "abbr": "",
                "stats_key": stats_key_guess or primary,
                "notes": default_notes,
                "PF": None,
                "PA": None,
                "PPG": None,
            }
        else:
            aliases = set(mapping[primary].get("aliases", []) or [])
            if raw != primary:
                aliases.add(raw)
            mapping[primary]["aliases"] = sorted(aliases)
            if not mapping[primary].get("stats_key"):
                mapping[primary]["stats_key"] = stats_key_guess or primary

    return mapping
