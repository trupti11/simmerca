"""Reply templates. English is complete; Hindi provided; other pilot languages fall back to English
until a native speaker supplies translations (add a dict below, no code change needed)."""

from __future__ import annotations

TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "not_registered": "This number is not registered with Aalora. Please contact the Aalora team.",
        "help": "Send:\nADD KS-0114 2 (new pieces)\nSOLD KS-0114 1 (sold)\nKS-0114 3 left (correct count)\nKS-0114 ? (check stock)\nor a photo to add a new piece.",
        "unknown": "Sorry, I did not understand. Example: ADD KS-0114 2. Send HELP for options.",
        "ask_qty": "How many for {sku}? Example: ADD {sku} 2",
        "not_found": "{sku} was not found.{suggest}",
        "suggest": " Your codes: {codes}",
        "not_yours": "{sku} is not one of your pieces.",
        "confirm_add": "Add {qty} to {sku} ({label})? Reply YES or NO.",
        "confirm_sold": "Mark {qty} of {sku} ({label}) as sold? Reply YES or NO.",
        "confirm_set": "Set {sku} ({label}) to {qty} pieces? Reply YES or NO.",
        "done": "Done. {sku} now has {on_hand}.",
        "done_mto": "Done. Production order {po} recorded for {sku}.",
        "nothing_pending": "Nothing to confirm. Send HELP for options.",
        "expired": "That request expired. Please send it again.",
        "cancelled": "Cancelled. Nothing was changed.",
        "insufficient": "Only {on_hand} of {sku} in stock. Nothing was changed.",
        "stock": "{sku} ({label}): {on_hand} in stock.",
        "stock_mto": "{sku} ({label}) is made to order, about {days} days.",
        "draft_created": "Thank you. New piece {sku} created. Please write {sku} on its tag. We will check the details.",
    },
    "hi": {
        "not_registered": "यह नंबर Aalora के साथ पंजीकृत नहीं है। कृपया Aalora टीम से संपर्क करें।",
        "help": "भेजें:\nADD KS-0114 2 (नए पीस)\nSOLD KS-0114 1 (बिके)\nKS-0114 3 बचे (सही गिनती)\nKS-0114 ? (स्टॉक देखें)\nया नए पीस की फोटो।",
        "unknown": "माफ़ कीजिए, समझ नहीं आया। उदाहरण: ADD KS-0114 2। विकल्पों के लिए HELP भेजें।",
        "ask_qty": "{sku} के कितने? उदाहरण: ADD {sku} 2",
        "not_found": "{sku} नहीं मिला।{suggest}",
        "suggest": " आपके कोड: {codes}",
        "not_yours": "{sku} आपका पीस नहीं है।",
        "confirm_add": "{sku} ({label}) में {qty} जोड़ें? हाँ या नहीं लिखें।",
        "confirm_sold": "{sku} ({label}) के {qty} बिके? हाँ या नहीं लिखें।",
        "confirm_set": "{sku} ({label}) की गिनती {qty} करें? हाँ या नहीं लिखें।",
        "done": "हो गया। {sku} में अब {on_hand} हैं।",
        "done_mto": "हो गया। {sku} के लिए ऑर्डर {po} दर्ज हुआ।",
        "nothing_pending": "पुष्टि के लिए कुछ नहीं है। HELP भेजें।",
        "expired": "समय समाप्त हो गया। कृपया फिर से भेजें।",
        "cancelled": "रद्द किया। कुछ नहीं बदला।",
        "insufficient": "{sku} के केवल {on_hand} स्टॉक में हैं। कुछ नहीं बदला।",
        "stock": "{sku} ({label}): स्टॉक में {on_hand}।",
        "stock_mto": "{sku} ({label}) ऑर्डर पर बनता है, लगभग {days} दिन।",
        "draft_created": "धन्यवाद। नया पीस {sku} बना। कृपया टैग पर {sku} लिखें।",
    },
}


def reply(lang: str, key: str, **kwargs) -> str:
    table = TEMPLATES.get(lang) or TEMPLATES["en"]
    template = table.get(key) or TEMPLATES["en"][key]
    return template.format(**kwargs)


def twiml(message: str | None) -> str:
    if not message:
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    from xml.sax.saxutils import escape

    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(message)}</Message></Response>'
