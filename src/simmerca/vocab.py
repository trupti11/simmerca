"""Controlled vocabularies shared by products, intelligence and channels."""

from __future__ import annotations

# The 15 Aalora fabric profiles, with their short-code prefix.
FABRIC_PROFILES: dict[str, str] = {
    "Kanjivaram": "KJ",
    "Banarasi": "BN",
    "Tussar": "TS",
    "Chanderi": "CH",
    "Maheshwari": "MH",
    "Handloom Cotton": "HC",
    "Mul Cotton": "MC",
    "Linen": "LN",
    "Khadi": "KD",
    "Chiffon": "CF",
    "Georgette": "GT",
    "Organza": "OR",
    "Crepe Silk": "CS",
    "Satin Silk": "SS",
    "Silk Cotton": "SC",
}
GENERIC_PREFIX = "XX"

FABRIC_SYNONYMS: dict[str, str] = {
    "kanjivaram": "Kanjivaram",
    "kanjeevaram": "Kanjivaram",
    "kanchipuram": "Kanjivaram",
    "kanchipuram silk": "Kanjivaram",
    "kanchi": "Kanjivaram",
    "banarasi": "Banarasi",
    "benarasi": "Banarasi",
    "banaras": "Banarasi",
    "varanasi silk": "Banarasi",
    "tussar": "Tussar",
    "tussah": "Tussar",
    "tasar": "Tussar",
    "kosa": "Tussar",
    "chanderi": "Chanderi",
    "maheshwari": "Maheshwari",
    "handloom cotton": "Handloom Cotton",
    "cotton handloom": "Handloom Cotton",
    "mul cotton": "Mul Cotton",
    "mulmul": "Mul Cotton",
    "mul mul": "Mul Cotton",
    "linen": "Linen",
    "khadi": "Khadi",
    "khaddar": "Khadi",
    "chiffon": "Chiffon",
    "georgette": "Georgette",
    "organza": "Organza",
    "crepe silk": "Crepe Silk",
    "crepe": "Crepe Silk",
    "satin silk": "Satin Silk",
    "satin": "Satin Silk",
    "silk cotton": "Silk Cotton",
    "sico": "Silk Cotton",
    "silk-cotton": "Silk Cotton",
}

WEAVES = {"plain", "jacquard", "brocade", "jamdani", "ikat", "kadhua", "phekua", "tanchoi", "kalamkari print", "block print", "embroidered", "unknown"}
BORDERS = {"none", "thin", "medium", "broad", "contrast", "temple", "korvai", "zari", "unknown"}
MOTIFS = {"none", "buta", "buti", "paisley", "floral", "temple", "checks", "stripes", "peacock", "elephant", "geometric", "jaal", "mango", "unknown"}
COLORS = {
    "red", "maroon", "pink", "magenta", "orange", "mustard", "yellow", "gold", "green", "bottle green",
    "teal", "blue", "navy", "purple", "lavender", "violet", "black", "white", "off-white", "cream",
    "beige", "brown", "grey", "silver", "peach", "wine", "rust",
}
COLOR_SYNONYMS = {
    "crimson": "red", "scarlet": "red", "vermilion": "red", "burgundy": "wine", "plum": "purple",
    "ivory": "off-white", "offwhite": "off-white", "off white": "off-white", "turquoise": "teal",
    "royal blue": "blue", "indigo": "navy", "olive": "green", "emerald": "green", "fuchsia": "magenta",
    "rani pink": "magenta", "saffron": "orange", "ochre": "mustard", "golden": "gold", "gray": "grey",
    "coral": "peach", "copper": "rust",
}


def normalize_fabric(value: str | None) -> str | None:
    if not value:
        return None
    v = str(value).strip()
    if v in FABRIC_PROFILES:
        return v
    low = v.lower().replace("_", " ")
    if low in FABRIC_SYNONYMS:
        return FABRIC_SYNONYMS[low]
    for syn, canon in sorted(FABRIC_SYNONYMS.items(), key=lambda kv: -len(kv[0])):
        if syn in low:
            return canon
    return None


def normalize_color(value: str | None) -> str | None:
    if not value:
        return None
    low = str(value).strip().lower()
    if low in COLORS:
        return low
    if low in COLOR_SYNONYMS:
        return COLOR_SYNONYMS[low]
    for syn, canon in COLOR_SYNONYMS.items():
        if syn in low:
            return canon
    for c in sorted(COLORS, key=len, reverse=True):
        if c in low:
            return c
    return None


def sku_prefix(fabric: str | None) -> str:
    return FABRIC_PROFILES.get(fabric or "", GENERIC_PREFIX)
