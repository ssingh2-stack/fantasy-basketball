"""Name and team normalization so players match across Basketball-Reference, ESPN and Yahoo."""
import re
import unicodedata

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Hand-maintained aliases for players whose names differ between sources.
ALIASES = {
    "nicolas claxton": "nic claxton",
    "cameron thomas": "cam thomas",
    "herbert jones": "herb jones",
    "kenyon martin": "kj martin",
    "moe wagner": "moritz wagner",
    "carlton carrington": "bub carrington",
    "alex sarr": "alexandre sarr",
    "ron holland": "ronald holland",
}


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("*", "")
    s = re.sub(r"[.'`]", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    parts = [p for p in s.split() if p not in SUFFIXES]
    s = " ".join(parts)
    return ALIASES.get(s, s)


# Canonical abbreviations are the common ones (BKN, CHA, PHX, GSW, NYK, SAS, NOP, UTA, WAS).
TEAM_ALIASES = {
    "BRK": "BKN", "BKN": "BKN", "CHO": "CHA", "CHA": "CHA", "PHO": "PHX", "PHX": "PHX",
    "GS": "GSW", "GSW": "GSW", "NY": "NYK", "NYK": "NYK", "SA": "SAS", "SAS": "SAS",
    "NO": "NOP", "NOP": "NOP", "UTAH": "UTA", "UTA": "UTA", "WSH": "WAS", "WAS": "WAS",
}


def norm_team(abbr: str) -> str:
    a = (abbr or "").strip().upper()
    return TEAM_ALIASES.get(a, a)
