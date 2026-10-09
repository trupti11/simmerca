"""Controlled vocabularies for object intelligence (textile profile).

Design rules (enforced by validate_vocab() at import, and by tests/test_vocab.py):
  1. Each axis means ONE thing:
       fabric  = the material/yarn a saree is known by (drives the SKU code)
       weave   = how the cloth is structurally woven
       surface = what is applied after weaving (print, dye, paint, embroidery)
       border, motif, color = as named
     Regional crafts (Banarasi, Paithani...) are NOT fabrics; see OPEN QUESTIONS below.
  2. Synonyms live next to the term they belong to, so a synonym can never point at a term
     that does not exist (the bug this module had: "kanchipuram" -> "Kanjivaram", a deleted profile).
  3. Every phrase is stored sanitized (casefolded, no punctuation, single spaces) and maps to
     exactly one term. Matching is whole-word: "red" never matches inside "bordered".
  4. SKU codes are 2 capital letters, unique, and NEVER reused once hang tags are printed.
     Retired codes stay in RETIRED_CODES forever.

OPEN QUESTIONS for the owner (do not guess in code):
  - Banarasi: removed as a fabric (it is a weaving tradition; Banarasi sarees come in katan,
    georgette, organza...). Should it become a `craft` attribute?
  - "koiri" motif: confirm meaning (kept as its own term until confirmed).
  - Generic "tussar" text maps to the parent profile "Tussar Silk" (TS); only an explicit
    "tussar by tussar" maps to TT. Confirm this split.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

GENERIC_PREFIX = "XX"
RETIRED_CODES = frozenset({"KJ", "BN"})  # used by v0.1 (Kanjivaram, Banarasi); never reassign


def sanitize(text: object) -> str:
    """'Tussar-by-TUSSAR (pure)!' -> 'tussar by tussar pure'. Keeps Indic letters and marks."""
    s = unicodedata.normalize("NFKC", str(text)).casefold()
    s = re.sub(r"[_/\\\-–—]+", " ", s)
    s = re.sub(r"[^\w\sऀ-෿]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class FabricProfile:
    name: str          # canonical display name (Title Case, spaces, no CamelCase)
    code: str          # 2-letter SKU prefix
    family: str        # silk | cotton | blend | linen | synthetic-blend
    synonyms: tuple[str, ...] = ()


# Order = display order in admin dropdowns.
FABRIC_PROFILE_LIST: tuple[FabricProfile, ...] = (
    FabricProfile("Kanchi Silk", "KS", "silk", ("kanjivaram", "kanjeevaram", "kanchivaram", "kanchipuram",
                                                "kanchipuram silk", "kanchi", "kanjivaram silk")),
    FabricProfile("Mysore Silk", "MY", "silk", ("mysore", "mysore crepe", "ksic silk")),
    FabricProfile("Bangalore Silk", "BA", "silk", ("bangalore", "bengaluru silk")),
    FabricProfile("Mulberry Silk", "MS", "silk", ("mulberry", "pure mulberry silk")),
    FabricProfile("Katan Silk", "KN", "silk", ("katan", "kataan", "katan silk")),
    FabricProfile("Tussar Silk", "TS", "silk", ("tussar", "tussah", "tasar", "tassar", "kosa", "kosa silk")),
    FabricProfile("Tussar by Tussar", "TT", "silk", ("tussar x tussar", "tasar by tasar", "pure tussar by tussar")),
    FabricProfile("Ghicha by Ghicha", "GG", "silk", ("ghicha", "gicha", "gichha", "ghichha", "gachi",
                                                     "gicha by gicha", "ghicha x ghicha", "ghicha silk")),
    FabricProfile("Chanderi", "CH", "blend", ("chanderi silk", "chanderi cotton")),
    FabricProfile("Maheshwari", "MH", "blend", ("maheshwari silk", "maheshwari cotton")),
    FabricProfile("Handloom Cotton", "HC", "cotton", ("cotton handloom", "handloom cotton saree", "pure cotton")),
    FabricProfile("Mul Cotton", "MC", "cotton", ("mulmul", "mul mul", "mul", "malmal")),
    FabricProfile("Linen", "LN", "linen", ("pure linen", "linen saree")),
    FabricProfile("Khadi", "KD", "cotton", ("khaddar", "khadi cotton")),
    FabricProfile("Chiffon", "CF", "synthetic-blend", ()),
    FabricProfile("Georgette", "GT", "synthetic-blend", ("georgette silk",)),
    FabricProfile("Organza", "OR", "silk", ("organza silk", "kora organza")),
    FabricProfile("Crepe Silk", "CS", "silk", ("crepe",)),
    FabricProfile("Satin Silk", "SS", "silk", ("satin",)),
    FabricProfile("Silk Cotton", "SC", "blend", ("sico", "silk cotton blend", "cotton silk")),
)

# Backward-compatible derived views (used across the codebase).
FABRIC_PROFILES: dict[str, str] = {p.name: p.code for p in FABRIC_PROFILE_LIST}

# ---- other axes: term -> synonyms ------------------------------------------------------

WEAVE_TERMS: dict[str, tuple[str, ...]] = {
    "plain": ("plain weave",),
    "jacquard": (),
    "brocade": ("zari brocade",),
    "jamdani": ("jamdani weave",),
    "ikat": ("ikkat", "pochampally", "pochampally ikat", "patola", "sambalpuri ikat"),
    "kadhua": ("kadwa", "kadhwa"),
    "phekua": ("fekuwa", "phekwa"),
    "tanchoi": ("tanchoi weave",),
    "paithani": ("paithani weave",),
    "baluchari": ("baluchori",),
    "unknown": (),
}

SURFACE_TERMS: dict[str, tuple[str, ...]] = {
    "none": (),
    "block print": ("hand block print", "block printed"),
    "ajrakh": ("ajrakh print", "ajrak", "ajrakh block print"),
    "kalamkari": ("kalamkari print",),
    "screen print": ("screen printed",),
    "bandhani": ("bandhej", "bandini"),
    "leheriya": ("lehariya", "leheria"),
    "shibori": (),
    "hand painted": ("hand painting", "painted"),
    "kantha": ("kantha stitch", "kantha embroidery"),
    "embroidered": ("embroidery",),
    "unknown": (),
}

BORDER_TERMS: dict[str, tuple[str, ...]] = {
    "none": ("borderless", "no border"),
    "thin": ("narrow",),
    "medium": (),
    "broad": ("wide", "big border"),
    "contrast": ("contrast border",),
    "ganga jamuna": ("gangajamuna", "ganga jamuna border"),
    "temple": ("temple border", "gopuram"),
    "korvai": ("korvai border",),
    "zari": ("zari border",),
    "munia": ("muniya",),
    "triple munia": ("triple muniya",),
    "narali": ("narli",),
    "unknown": (),
}

MOTIF_TERMS: dict[str, tuple[str, ...]] = {
    "none": ("plain body",),
    "buta": ("booti large",),
    "buti": ("booti", "bootie"),
    "paisley": ("kalka",),
    "mango": ("kairi", "ambi", "mango motif"),
    "floral": ("flowers", "phool"),
    "temple": ("temple motif",),
    "checks": ("checked", "kattam", "chex"),
    "stripes": ("striped", "veldhari"),
    "peacock": ("mayil", "mor"),
    "swan": ("annam", "hamsa"),
    "elephant": ("haathi", "hathi", "yanai"),
    "rudraksha": (),
    "geometric": (),
    "jaal": ("jaali", "jal"),
    "chaand": ("chand", "moon"),
    "koiri": (),
    "pattachitra": ("pottachitra", "patachitra", "patta chitra"),
    "unknown": (),
}

COLOR_TERMS: dict[str, tuple[str, ...]] = {
    "red": ("crimson", "scarlet", "vermilion", "lal", "sindoori"),
    "maroon": ("kumkum",),
    "wine": ("burgundy",),
    "pink": ("gulabi", "onion", "onion pink", "baby pink"),
    "magenta": ("fuchsia", "rani", "rani pink"),
    "orange": ("saffron", "kesari"),
    "mustard": ("ochre", "haldi mustard"),
    "yellow": ("peela", "haldi"),
    "gold": ("golden", "sona"),
    "green": ("olive", "emerald", "hara", "mehendi", "parrot green", "mint"),
    "bottle green": (),
    "teal": ("turquoise", "peacock blue", "peacock green"),
    "blue": ("royal blue", "sky blue", "neela"),
    "navy": ("indigo", "navy blue"),
    "purple": ("plum", "jamuni"),
    "lavender": ("lilac",),
    "violet": (),
    "black": ("kala",),
    "white": ("safed",),
    "off white": ("ivory", "offwhite"),
    "cream": (),
    "beige": ("tan", "sand"),
    "brown": ("chocolate", "coffee"),
    "grey": ("gray", "charcoal"),
    "silver": (),
    "peach": ("coral",),
    "rust": ("copper", "brick"),
    "multicolor": ("multicolour", "multi color", "multi"),
}

# Umbrella terms lose to any specific term found in the same text:
# "Ajrakh block print" -> ajrakh (a kind of block print), "tussar by tussar" -> the specific profile.
GENERIC_TERMS: dict[str, frozenset[str]] = {
    "fabric": frozenset({"Tussar Silk", "Mulberry Silk"}),
    "weave": frozenset({"plain", "jacquard", "brocade", "unknown"}),
    "surface": frozenset({"block print", "screen print", "hand painted", "embroidered", "none", "unknown"}),
    "border": frozenset({"thin", "medium", "broad", "contrast", "zari", "none", "unknown"}),
    "motif": frozenset({"floral", "geometric", "none", "unknown"}),
    "color": frozenset({"multicolor"}),
}

WEAVES = frozenset(WEAVE_TERMS)
SURFACES = frozenset(SURFACE_TERMS)
BORDERS = frozenset(BORDER_TERMS)
MOTIFS = frozenset(MOTIF_TERMS)
COLORS = frozenset(COLOR_TERMS)

# ---- matching ------------------------------------------------------------------------------


def _index(axis: str, terms: dict[str, tuple[str, ...]]) -> list[tuple[str, str, bool]]:
    """(sanitized phrase, canonical term, is_generic) for every term and synonym."""
    pairs = {sanitize(t): t for t in terms}
    for term, syns in terms.items():
        for s in syns:
            pairs[sanitize(s)] = term
    generic = GENERIC_TERMS.get(axis, frozenset())
    return [(phrase, term, term in generic) for phrase, term in sorted(pairs.items())]


_FABRIC_INDEX = _index("fabric", {p.name: p.synonyms for p in FABRIC_PROFILE_LIST})
_INDEXES = {
    "weave": _index("weave", WEAVE_TERMS),
    "surface": _index("surface", SURFACE_TERMS),
    "border": _index("border", BORDER_TERMS),
    "motif": _index("motif", MOTIF_TERMS),
    "color": _index("color", COLOR_TERMS),
}


def _match(index: list[tuple[str, str, bool]], value: object) -> str | None:
    """Whole-word match. Winner: specific over generic, then earliest in the text, then longest phrase.
    "teal and gold" -> teal; "Ajrakh block print" -> ajrakh; "tussar by tussar" -> Tussar by Tussar."""
    if value is None:
        return None
    text = sanitize(value)
    if not text:
        return None
    padded = f" {text} "
    best = None
    for phrase, term, is_generic in index:
        pos = padded.find(f" {phrase} ")
        if pos >= 0:
            rank = (is_generic, pos, -len(phrase))
            if best is None or rank < best[0]:
                best = (rank, term)
    return best[1] if best else None


def normalize_fabric(value: object) -> str | None:
    """Free text -> canonical fabric profile name, or None."""
    return _match(_FABRIC_INDEX, value)


def normalize_color(value: object) -> str | None:
    return _match(_INDEXES["color"], value)


def normalize_term(axis: str, value: object) -> str | None:
    """axis in {weave, surface, border, motif, color}."""
    return _match(_INDEXES[axis], value)


def sku_prefix(fabric: str | None) -> str:
    return FABRIC_PROFILES.get(fabric or "", GENERIC_PREFIX)


# ---- validation (runs at import; a bad edit fails fast, not in production) ------------------


def validate_vocab() -> list[str]:
    problems: list[str] = []
    names = [p.name for p in FABRIC_PROFILE_LIST]
    codes = [p.code for p in FABRIC_PROFILE_LIST]
    for p in FABRIC_PROFILE_LIST:
        if not re.fullmatch(r"[A-Z]{2}", p.code) or p.code == GENERIC_PREFIX:
            problems.append(f"{p.name}: code {p.code!r} must be 2 capital letters and not {GENERIC_PREFIX}")
        if p.code in RETIRED_CODES:
            problems.append(f"{p.name}: code {p.code} is retired and can never be reused")
        if re.search(r"[a-z][A-Z]", p.name) or p.name != p.name.strip() or "  " in p.name:
            problems.append(f"{p.name!r}: use spaced Title Case, not CamelCase")
    problems += [f"duplicate fabric name {n!r}" for n in set(names) if names.count(n) > 1]
    problems += [f"duplicate SKU code {c!r}" for c in set(codes) if codes.count(c) > 1]

    def phrases(axis: str, terms: dict[str, tuple[str, ...]]) -> None:
        owner: dict[str, str] = {}
        for term, syns in terms.items():
            for phrase in (term, *syns):
                key = sanitize(phrase)
                if not key:
                    problems.append(f"{axis}: empty phrase under {term!r}")
                elif key in owner and owner[key] != term:
                    problems.append(f"{axis}: {phrase!r} maps to both {owner[key]!r} and {term!r}")
                owner.setdefault(key, term)
            if axis != "fabric" and term != sanitize(term):
                problems.append(f"{axis}: term {term!r} must be stored sanitized ({sanitize(term)!r})")

    known = {"fabric": set(names), "weave": set(WEAVE_TERMS), "surface": set(SURFACE_TERMS),
             "border": set(BORDER_TERMS), "motif": set(MOTIF_TERMS), "color": set(COLOR_TERMS)}
    for axis, generic in GENERIC_TERMS.items():
        problems += [f"{axis}: generic term {g!r} is not a defined term" for g in generic - known[axis]]
    phrases("fabric", {p.name: p.synonyms for p in FABRIC_PROFILE_LIST})
    for axis, terms in (("weave", WEAVE_TERMS), ("surface", SURFACE_TERMS), ("border", BORDER_TERMS),
                        ("motif", MOTIF_TERMS), ("color", COLOR_TERMS)):
        phrases(axis, terms)
    return problems


_problems = validate_vocab()
if _problems:
    raise ValueError("vocab.py is inconsistent:\n  " + "\n  ".join(_problems))
