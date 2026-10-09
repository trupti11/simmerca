"""Multilingual WhatsApp message parser (spec 02, criteria 3-4).

Rules first (fast, deterministic, free). Anything the rules cannot classify goes to an optional
LLM fallback whose output is schema-validated. Every write still needs the weaver's confirmation.

Keyword lists need review by native speakers of each pilot language; the eval set is the safety net.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Protocol

from ..vocab import FABRIC_PROFILES, GENERIC_PREFIX

INTENTS = {"add", "sold", "set", "query", "confirm", "cancel", "help", "new_product", "unknown"}
WRITE_INTENTS = {"add", "sold", "set"}
PREFIXES = set(FABRIC_PROFILES.values()) | {GENERIC_PREFIX}

# SKU: known 2-letter prefix, optional dash/space, 1-5 digits (any script). Not preceded by a letter.
SKU_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(PREFIXES)) + r")\s*[-_ ]?\s*(\d{1,5})(?!\d)", re.IGNORECASE)
SIGNED_RE = re.compile(r"(?<![\w])([+\-])\s*(\d{1,4})(?!\d)")
NUM_RE = re.compile(r"(?<![\w])(\d{1,4})(?!\d)")

CONFIRM_WORDS = {
    "yes", "y", "ok", "okay", "confirm", "haan", "han", "ha", "haa", "ji", "jee",
    "हाँ", "हां", "हा", "जी", "होय", "হ্যাঁ", "হা", "হয়", "হয", "હા", "ஆம்", "ஆமாம்", "சரி",
    "అవును", "సరే", "ಹೌದು", "ಸರಿ", "ହଁ", "👍", "✅", "✔", "✔️",
}
CANCEL_WORDS = {
    "no", "n", "cancel", "nahi", "nahin", "na", "stop",
    "नहीं", "नही", "ना", "नाही", "না", "নহয়", "নহয", "ના", "இல்லை", "వద్దు", "కాదు", "ಇಲ್ಲ", "ନା", "❌", "✖",
}
HELP_WORDS = {"help", "menu", "hi", "hello", "namaste", "?", "मदद", "सहायता", "नमस्ते", "मेनू"}

# keyword -> intent. Latin keywords match on word boundaries; others as substrings.
KEYWORDS: dict[str, list[str]] = {
    "sold": [
        "sold", "sale", "sell", "becha", "bechi", "bika", "biki", "bik gaya", "vikla", "vikli",
        "बिका", "बिकी", "बिक", "बेचा", "बेची", "बेचे", "विकले", "विकली", "विकला", "विक्री",
        "বিক্রি", "বিক্রী", "বিক্ৰী", "বিক্ৰি", "বেচা", "વેચાયું", "વેચાયા", "વેચાઈ", "વેચ્યું", "વેચી",
        "விற்பனை", "விற்றது", "விற்றேன்", "விற்று", "అమ్మాను", "అమ్మింది", "అమ్మకం", "అమ్మేశా",
        "ಮಾರಾಟ", "ಮಾರಿದೆ", "ಮಾರಿದೆನು", "ବିକ୍ରି", "ବିକିଲା", "ବିକ୍ରୟ",
    ],
    "add": [
        "add", "added", "plus", "received", "receive", "new stock", "jodo", "jod", "jama", "aaya", "aaye", "aayi",
        "जोड़", "जोड", "जोड़ें", "जोडें", "जमा", "आया", "आए", "आये", "आई", "नया", "जोडा", "वाढवा", "आले",
        "যোগ", "এসেছে", "আসছে", "এল", "আহিছে", "ઉમેરો", "ઉમેર્યા", "ઉમેરી", "આવ્યા", "આવી",
        "சேர்", "சேர்க்க", "சேர்த்து", "வந்தது", "வந்தன", "జోడించు", "జోడించండి", "చేర్చు", "వచ్చాయి", "వచ్చింది",
        "ಸೇರಿಸಿ", "ಸೇರಿಸು", "ಬಂದಿದೆ", "ಬಂದಿವೆ", "ଯୋଗ", "ମିଶାନ୍ତୁ", "ଆସିଲା", "আহিল",
    ],
    "set": [
        "count", "total", "left", "remaining", "stock is", "set", "baki", "bache", "bacha",
        "बचे", "बची", "बाकी", "शेष", "कुल", "शिल्लक", "বাকি", "বাকী", "অবশিষ্ট", "બાકી", "બચ્યા",
        "மீதம்", "மீதி", "మిగిలాయి", "మిగిలింది", "ಉಳಿದಿದೆ", "ಉಳಿದಿವೆ", "ବାକି", "ବଳିଲା",
    ],
    "query": [
        "how many", "kitne", "kitna", "kitni", "check", "stock?", "status",
        "कितने", "कितना", "कितनी", "किती", "কত", "কিমান", "કેટલા", "કેટલી", "எத்தனை", "ఎన్ని", "ಎಷ್ಟು", "କେତେ",
    ],
}
KEYWORD_ORDER = ("sold", "add", "set", "query")

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
}


@dataclass
class Parsed:
    intent: str
    sku: str | None = None
    qty: int | None = None
    confidence: float = 1.0
    source: str = "rules"

    def to_dict(self) -> dict:
        return asdict(self)


class LlmFallback(Protocol):
    def complete_json(self, system: str, user: str) -> str: ...


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").strip()
    return re.sub(r"\s+", " ", text)


def _bare(text: str) -> str:
    return re.sub(r"[.!,।]+$", "", text.strip().lower()).strip()


def normalize_sku(prefix: str, digits: str) -> str:
    return f"{prefix.upper()}-{int(digits):04d}"  # int() accepts Devanagari, Bengali, Tamil, ... digits


def _has_keyword(text_low: str, word: str) -> bool:
    if word.isascii():
        return re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", text_low) is not None
    return word in text_low


def _number_word(text_low: str) -> int | None:
    # split on whitespace/punctuation, not \w: Indic vowel signs (e.g. the ो in दो) are not \w characters
    for token in re.split(r"[\s,.!?;:।|]+", text_low):
        if token in NUMBER_WORDS:
            return NUMBER_WORDS[token]
    return None


def parse_rules(text: str, has_media: bool = False) -> Parsed:
    raw = _clean(text)
    low = raw.lower()
    bare = _bare(raw)

    if has_media:
        return Parsed("new_product")
    if not raw:
        return Parsed("help")
    if bare in CONFIRM_WORDS:
        return Parsed("confirm")
    if bare in CANCEL_WORDS:
        return Parsed("cancel")

    m = SKU_RE.search(raw)
    if not m:
        if bare in HELP_WORDS:
            return Parsed("help")
        return Parsed("unknown", confidence=0.0)

    sku = normalize_sku(m.group(1), m.group(2))
    rest = (raw[: m.start()] + " " + raw[m.end():]).strip()
    rest_low = rest.lower()

    signed = SIGNED_RE.search(rest)
    if signed:
        qty = int(signed.group(2))
        return Parsed("add" if signed.group(1) == "+" else "sold", sku, qty)

    rest = re.sub(r"(?i)(?<![a-z])x\s*(\d)", r" \1", rest)  # "x1" / "x 2" means a quantity
    num = NUM_RE.search(rest)
    qty = int(num.group(1)) if num else _number_word(rest_low)

    # "how many left?" has a set-word but no number: it is a question
    if qty is None and any(_has_keyword(rest_low, w) for w in KEYWORDS["query"]):
        return Parsed("query", sku)

    for intent in KEYWORD_ORDER:
        if any(_has_keyword(rest_low, w) for w in KEYWORDS[intent]):
            if intent == "query":
                return Parsed("query", sku)
            return Parsed(intent, sku, qty)

    if qty is None:
        # just a code, or a code with a question mark
        return Parsed("query", sku)
    # a code and a number but no verb: ambiguous, ask
    return Parsed("unknown", sku, qty, confidence=0.3)


LLM_SYSTEM = """You classify WhatsApp messages from handloom weavers managing inventory.
Messages may be in English, Hindi, Marathi, Bengali, Gujarati, Tamil, Telugu, Odia, Assamese or Kannada,
in native script or romanized. Product codes look like KJ-0114 (two letters, dash, digits).
Return ONLY a JSON object: {"intent": one of ["add","sold","set","query","help","unknown"],
"sku": "XX-0000" or null, "qty": integer or null, "confidence": number 0..1}.
"add" = new pieces arrived; "sold" = pieces sold; "set" = the total count now; "query" = asking stock.
If unsure, use "unknown" with low confidence. Never invent a product code."""


def parse(text: str, has_media: bool = False, llm: LlmFallback | None = None) -> Parsed:
    """Rules first; LLM only for messages the rules could not classify."""
    result = parse_rules(text, has_media)
    if result.intent != "unknown" or llm is None or not _clean(text):
        return result
    try:
        candidate = validate_llm_output(llm.complete_json(LLM_SYSTEM, _clean(text)[:500]))
    except Exception:  # noqa: BLE001 - a broken model call must never break the webhook
        return result
    if candidate.intent == "unknown" or candidate.confidence < 0.7:
        return result
    if result.sku and candidate.sku and candidate.sku != result.sku:
        return result  # model disagrees with a code the weaver actually typed: ask instead
    return candidate


def validate_llm_output(raw: str) -> Parsed:
    """Strict schema check. Anything off-schema becomes unknown."""
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return Parsed("unknown", confidence=0.0, source="llm")
    intent = data.get("intent")
    if intent not in INTENTS - {"confirm", "cancel", "new_product"}:
        return Parsed("unknown", confidence=0.0, source="llm")
    sku = None
    if data.get("sku"):
        m = SKU_RE.fullmatch(str(data["sku"]).strip())
        if not m:
            return Parsed("unknown", confidence=0.0, source="llm")
        sku = normalize_sku(m.group(1), m.group(2))
    qty = data.get("qty")
    if qty is not None and (not isinstance(qty, int) or isinstance(qty, bool) or not 0 <= qty <= 999):
        return Parsed("unknown", confidence=0.0, source="llm")
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    if intent in WRITE_INTENTS and (sku is None or qty is None):
        return Parsed("unknown", sku, qty, confidence=min(conf, 0.3), source="llm")
    return Parsed(intent, sku, qty, confidence=conf, source="llm")
