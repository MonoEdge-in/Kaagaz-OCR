"""The WhatsApp chat's fixed words in English, Hindi and Marathi (#603, PRD 7 section 15: "Every
fixed message exists in Marathi, Hindi and English"), the language a message is in, and ID-number
masking.

* ``language``: Devanagari letters are Hindi or Marathi, by a few common words of each (Marathi
  when neither, or as many of each: Kaagaz's documents are mostly Maharashtra's); anything else
  is English. Hindi or Marathi typed in English letters is answered in English.
* ``say``: a fixed message in a language, its ``{}`` filled in.
* ``mask``: Aadhaar, PAN, bank account and card numbers (Meta's Business Messaging Policy forbids
  sending them) as ``••••`` and their last 4 characters: a PAN (5 letters, 4 digits, a letter), and
  any run of 9 to 19 digits (of any script) in groups split by single spaces or hyphens, so a
  phone number goes too. An amount (``₹18,50,000``: commas) or a date is left as it is. True when
  anything was masked, so the reply can say how to see it in full.
"""

import re
from typing import Literal

Language = Literal["en", "hi", "mr"]
LANGUAGES: tuple[Language, ...] = ("en", "hi", "mr")

_DEVANAGARI = re.compile("[ऀ-ॿ]")
_WORD = re.compile(r"[ऀ-ॿ]+")
_HINDI = frozenset(
    "है हैं क्या का की के मुझे मेरा मेरी मेरे कितना कितनी कहाँ कहां दिखाओ बताओ बताइए और में नहीं था थी"  # noqa: SIM905
    " कौन कौनसा चाहिए".split()
)
_MARATHI = frozenset(
    "आहे आहेत काय कोणता कोणती कोणते कुठे मला माझा माझी माझे माझ्या तुमचा च्या चा ची चे आणि नाही"  # noqa: SIM905
    " पाहिजे द्या दाखवा सांगा होते झाले".split()
)


def language(text: str) -> Language | None:
    """The language ``text`` is written in; None when it has no letter to tell by (an empty text,
    a number). O(len(text))."""
    if _DEVANAGARI.search(text):
        words = _WORD.findall(text)
        hindi = sum(word in _HINDI for word in words)
        marathi = sum(word in _MARATHI or word.endswith("च्या") for word in words)
        return "hi" if hindi > marathi else "mr"
    return "en" if any(ch.isalpha() for ch in text) else None


# Each fixed message: English, Hindi, Marathi. Button titles are at most 20 characters, list row
# titles 24 (WhatsApp's limits; tests/test_whatsapp_chat.py checks every one).
TEXTS: dict[str, tuple[str, str, str]] = {
    "menu": (
        "Hello {}. What would you like to do?",
        "नमस्ते {}। आप क्या करना चाहेंगे?",
        "नमस्कार {}. तुम्हाला काय करायचे आहे?",
    ),
    "ask_button": ("Ask a question", "सवाल पूछें", "प्रश्न विचारा"),
    "get_button": ("Get a file", "फ़ाइल पाएँ", "फाइल मिळवा"),
    "more_button": ("More", "और", "आणखी"),
    "menu_button": ("Menu", "मेन्यू", "मेनू"),
    "another_button": ("Ask another", "और सवाल पूछें", "आणखी प्रश्न"),
    "not_this_button": ("Not this one", "यह नहीं", "ही नाही"),
    "see_files": ("See files", "फ़ाइलें देखें", "फाइल्स पाहा"),
    "choose": ("Choose", "चुनें", "निवडा"),
    "ask": ("Type your question.", "अपना सवाल लिखें।", "तुमचा प्रश्न लिहा."),
    "which_file": ("Which file? Type a name.", "कौन-सी फ़ाइल? उसका नाम लिखें।", "कोणती फाइल? तिचे नाव लिहा."),
    "not_here": (
        "Not here? Add a year, name or amount.",
        "यहाँ नहीं है? साल, नाम या रकम जोड़कर लिखें।",
        "इथे नाही? वर्ष, नाव किंवा रक्कम जोडून लिहा.",
    ),
    "found": ("I found {} files. Pick one:", "मुझे {} फ़ाइलें मिलीं। एक चुनें:", "मला {} फाइल्स सापडल्या. एक निवडा:"),
    "found_more": (
        "I found more than {} files. Here are the best {}:",
        "मुझे {} से ज़्यादा फ़ाइलें मिलीं। सबसे सही {} ये हैं:",
        "मला {} पेक्षा जास्त फाइल्स सापडल्या. सर्वात जुळणाऱ्या {} या:",
    ),
    "files_section": ("Files", "फ़ाइलें", "फाइल्स"),
    "not_found": (
        "I couldn't find this in the files you can see. Try other words, or ask your admin if you think you"
        " should have access.",
        "जो फ़ाइलें आप देख सकते हैं, उनमें यह नहीं मिला। दूसरे शब्दों में लिखें, या अगर आपको लगता है कि आपके पास"
        " पहुँच होनी चाहिए तो अपने एडमिन से पूछें।",
        "तुम्हाला दिसणाऱ्या फाइल्समध्ये हे सापडले नाही. दुसरे शब्द वापरून पाहा, किंवा तुम्हाला प्रवेश असायला हवा असे"
        " वाटत असल्यास तुमच्या ॲडमिनला विचारा.",
    ),
    "only_files": (
        "I can only help with your Kaagaz files.",
        "मैं केवल आपकी Kaagaz फ़ाइलों में मदद कर सकता हूँ।",
        "मी फक्त तुमच्या Kaagaz फाइल्सबद्दल मदत करू शकतो.",
    ),
    "shared": ("Shared with you", "आपके साथ साझा की गई", "तुमच्यासोबत शेअर केलेली"),
    "pages": ("{} pages", "{} पेज", "{} पाने"),
    "added": ("added {}", "{} को जोड़ी गई", "{} रोजी जोडली"),
    "summary": ("Summary: {}", "सारांश: {}", "सारांश: {}"),
    "open_in_kaagaz": ("Open in Kaagaz: {}", "Kaagaz में खोलें: {}", "Kaagaz मध्ये उघडा: {}"),
    "source": ("Source: {}, page {}", "स्रोत: {}, पेज {}", "स्रोत: {}, पान {}"),
    "open": ("Open: {}", "खोलें: {}", "उघडा: {}"),
    "full_answer": ("Full answer in Kaagaz: {}", "पूरा जवाब Kaagaz में: {}", "पूर्ण उत्तर Kaagaz मध्ये: {}"),
    "masked": (
        "ID and account numbers show only their last 4 characters here. Open in Kaagaz to see them in full.",
        "पहचान और खाता नंबरों के यहाँ केवल आख़िरी 4 अक्षर दिखते हैं। पूरे नंबर Kaagaz में खोलकर देखें।",
        "ओळख आणि खाते क्रमांकांचे इथे फक्त शेवटचे 4 अक्षरे दिसतात. पूर्ण क्रमांक Kaagaz मध्ये उघडून पाहा.",
    ),
    "choice_gone": (
        "That choice isn't available any more. Please ask again.",
        "यह विकल्प अब उपलब्ध नहीं है। कृपया फिर से पूछें।",
        "हा पर्याय आता उपलब्ध नाही. कृपया पुन्हा विचारा.",
    ),
    "type_it": ("Type what you mean.", "आपका मतलब लिखें।", "तुम्हाला काय म्हणायचे आहे ते लिहा."),
    "something_else": ("Something else", "कुछ और", "दुसरे काही"),
    "all_of_these": ("All of these", "ये सभी", "हे सर्व"),
    "too_many": (
        "You have asked many questions in a short time. Please wait a minute and ask again.",
        "आपने कम समय में बहुत सवाल पूछे हैं। एक मिनट रुककर फिर पूछें।",
        "तुम्ही थोड्या वेळात खूप प्रश्न विचारले आहेत. एक मिनिट थांबून पुन्हा विचारा.",
    ),
    "ask_off": (
        "Asking questions is switched off for your organisation.",
        "आपकी संस्था के लिए सवाल पूछना बंद है।",
        "तुमच्या संस्थेसाठी प्रश्न विचारणे बंद आहे.",
    ),
    "too_long": (
        "Please keep your question under 1,000 characters.",
        "कृपया अपना सवाल 1,000 अक्षरों से छोटा रखें।",
        "कृपया तुमचा प्रश्न 1,000 अक्षरांपेक्षा लहान ठेवा.",
    ),
    "typed_only": (
        "I can read typed messages only. Type your question, or Menu.",
        "मैं केवल लिखे हुए संदेश पढ़ सकता हूँ। अपना सवाल लिखें, या Menu लिखें।",
        "मी फक्त लिहिलेले संदेश वाचू शकतो. तुमचा प्रश्न लिहा, किंवा Menu लिहा.",
    ),
    "more": ("More options", "और विकल्प", "आणखी पर्याय"),
    "language_section": ("Language", "भाषा", "भाषा"),
    "other_section": ("Other", "अन्य", "इतर"),
    "help_row": ("Help", "मदद", "मदत"),
    "disconnect_row": ("Disconnect", "डिस्कनेक्ट करें", "डिस्कनेक्ट करा"),
    "help": (
        "I answer questions about your Kaagaz files and send links that open them in Kaagaz. Files are"
        " never sent on WhatsApp. Type Menu at any time. For help, ask your organisation's Kaagaz admin, or"
        " open Kaagaz: {}",
        "मैं आपकी Kaagaz फ़ाइलों के बारे में सवालों के जवाब देता हूँ और उन्हें Kaagaz में खोलने के लिंक भेजता हूँ। फ़ाइलें"
        " WhatsApp पर कभी नहीं भेजी जातीं। कभी भी Menu लिखें। मदद के लिए अपनी संस्था के Kaagaz एडमिन से पूछें, या"
        " Kaagaz खोलें: {}",
        "मी तुमच्या Kaagaz फाइल्सबद्दलच्या प्रश्नांची उत्तरे देतो आणि त्या Kaagaz मध्ये उघडण्याच्या लिंक पाठवतो. फाइल्स"
        " WhatsApp वर कधीच पाठवल्या जात नाहीत. कधीही Menu लिहा. मदतीसाठी तुमच्या संस्थेच्या Kaagaz ॲडमिनला विचारा,"
        " किंवा Kaagaz उघडा: {}",
    ),
}
# The language list's rows: each in its own language, whatever the chat's.
LANGUAGE_NAMES: dict[Language, str] = {"en": "English", "hi": "हिंदी", "mr": "मराठी"}


def say(key: str, lang: Language, *values: object) -> str:
    return TEXTS[key][LANGUAGES.index(lang)].format(*values)


_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z0-9])")
# 9 to 19 digits: groups of digits split by one space or hyphen, never next to another digit.
_NUMBER = re.compile(r"(?<![\d])\d+(?:[ -]\d+)*(?![\d])")
MASK = "••••"


def _digits(found: re.Match[str]) -> str:
    run = found.group(0)
    digits = [ch for ch in run if ch.isdigit()]
    return MASK + "".join(digits[-4:]) if 9 <= len(digits) <= 19 else run


def mask(text: str) -> tuple[str, bool]:
    """``text`` with its ID and account numbers masked, and whether any was. O(len(text))."""
    masked = _PAN.sub(lambda found: MASK + found.group(0)[-4:].upper(), text)
    masked = _NUMBER.sub(_digits, masked)
    return masked, masked != text
