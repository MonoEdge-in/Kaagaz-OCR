"""The WhatsApp chat's fixed words in English, Hindi and Marathi (#603, PRD 7 section 15: "Every
fixed message exists in Marathi, Hindi and English"), the language a message is in, and ID-number
masking.

* ``language``: Devanagari letters are Hindi or Marathi, by a few common words of each (Marathi
  when neither, or as many of each: Kaagaz's documents are mostly Maharashtra's); anything else
  is English. Hindi or Marathi typed in English letters is answered in English.
* ``say``: a fixed message in a language, its ``{}`` filled in.
* ``mask``: Aadhaar, PAN, bank account and card numbers (Meta's Business Messaging Policy forbids
  sending them) as ``••••`` and their last 4 characters: a PAN (5 letters, 4 digits, a letter), and
  any run of 9 or more digits (of any script) in groups split by up to 3 spaces, tabs, new lines,
  hyphens or dashes of any kind, so a phone number goes too. W7: what draws nothing (zero-width
  spaces, the soft hyphen, marks of direction) goes first, so a number with it between its digits is
  masked as the whole it reads as. An amount (``₹18,50,000``: commas) or a date is
  left as it is. True when anything was masked, so the reply can say how to see it in full. Only
  replies sent on WhatsApp are masked (Meta's policy, PRD 7 section 15): nothing stored changes,
  and the browser shows every number in full.
* WhatsApp's limits, ``cut``, ``lang_of`` and ``masked`` (W4's, kept in kaagaz/channel/chat.py
  until W5's uploads shared them).
* ``kaagaz_links_only`` (W7): a reply without any link but Kaagaz's own. WhatsApp makes a link of an
  address with its scheme, of ``www.`` and of a domain with a path; whoever wrote one (a file's or
  folder's name, a translation a document's words talked into it), it never goes from Kaagaz's bot.
"""

import re
import unicodedata
from typing import Literal

Language = Literal["en", "hi", "mr"]
LANGUAGES: tuple[Language, ...] = ("en", "hi", "mr")
MAX_BODY = 1024  # WhatsApp's most for a message with reply buttons
MAX_ROWS = 10  # WhatsApp's most for a list
BUTTON_CHARS, ROW_CHARS, DESCRIPTION_CHARS = 20, 24, 72  # WhatsApp's limits
_VIRAMA = chr(0x094D)  # joins a Devanagari consonant to the next: a cut never falls after it


def cut(text: str, most: int, *, lines: bool = False) -> str:
    """``text`` on one line (``lines``: its lines kept), at most ``most`` characters, cut with "…"
    at a whole letter: never between a Devanagari letter and its vowel sign, or inside a joined
    consonant."""
    text = text.strip() if lines else " ".join(text.split())
    if len(text) <= most:
        return text
    end = most - 1
    while end > 0 and (unicodedata.category(text[end]).startswith("M") or text[end - 1] == _VIRAMA):
        end -= 1
    return text[:end].rstrip() + "…"


def lang_of(value: str) -> Language:
    """``value`` as a language this chat speaks; English for anything else."""
    return next((each for each in LANGUAGES if each == value), "en")


_DEVANAGARI = re.compile("[ऀ-ॿ]")
_WORD = re.compile(r"[ऀ-ॿ]+")
_HINDI = frozenset(
    "है हैं क्या का की के मुझे मेरा मेरी मेरे कितना कितनी कहाँ कहां दिखाओ बताओ बताइए और में नहीं था थी"  # noqa: SIM905
    " कौन कौनसा चाहिए मदद".split()  # W5: मदद, Help typed in Hindi (Marathi's is मदत)
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


# Each fixed message: English, Hindi, Marathi. Button titles (``*_button``) are at most 20
# characters, list row titles (``*_row``) 24 (WhatsApp's limits; tests/test_whatsapp_chat.py checks
# every one).
TEXTS: dict[str, tuple[str, str, str]] = {
    "menu": (
        "Hello {}. What would you like to do?\nType More for language, help or to disconnect.",
        "नमस्ते {}। आप क्या करना चाहेंगे?\nभाषा, मदद या डिस्कनेक्ट के लिए More लिखें।",
        "नमस्कार {}. तुम्हाला काय करायचे आहे?\nभाषा, मदत किंवा डिस्कनेक्टसाठी More लिहा.",
    ),
    "ask_button": ("Ask a question", "सवाल पूछें", "प्रश्न विचारा"),
    "upload_button": ("Upload a file", "फ़ाइल अपलोड करें", "फाइल अपलोड करा"),
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
    "model_not_allowed": (
        "That way of answering isn't available now. Choose another in Kaagaz, in Answer settings.",
        "जवाब देने का यह तरीका अभी उपलब्ध नहीं है। Kaagaz में Answer settings में दूसरा चुनें।",
        "उत्तर देण्याची ही पद्धत आता उपलब्ध नाही. Kaagaz मध्ये Answer settings मध्ये दुसरी निवडा.",
    ),
    "change_password": (
        "Please sign in to Kaagaz and set a new password first.",
        "कृपया पहले Kaagaz में साइन इन करके नया पासवर्ड सेट करें।",
        "कृपया आधी Kaagaz मध्ये साइन इन करून नवीन पासवर्ड सेट करा.",
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
        "I answer questions about your Kaagaz files, send links that open them in Kaagaz, and save the"
        " files you send me. Files are never sent on WhatsApp. Type Menu at any time. For help, ask your"
        " organisation's Kaagaz admin, or open Kaagaz: {}",
        "मैं आपकी Kaagaz फ़ाइलों के बारे में सवालों के जवाब देता हूँ, उन्हें Kaagaz में खोलने के लिंक भेजता हूँ, और आपकी"
        " भेजी फ़ाइलें सेव करता हूँ। फ़ाइलें WhatsApp पर कभी नहीं भेजी जातीं। कभी भी Menu लिखें। मदद के लिए अपनी संस्था के"
        " Kaagaz एडमिन से पूछें, या Kaagaz खोलें: {}",
        "मी तुमच्या Kaagaz फाइल्सबद्दलच्या प्रश्नांची उत्तरे देतो, त्या Kaagaz मध्ये उघडण्याच्या लिंक पाठवतो, आणि तुम्ही"
        " पाठवलेल्या फाइल्स सेव्ह करतो. फाइल्स WhatsApp वर कधीच पाठवल्या जात नाहीत. कधीही Menu लिहा. मदतीसाठी तुमच्या"
        " संस्थेच्या Kaagaz ॲडमिनला विचारा, किंवा Kaagaz उघडा: {}",
    ),
    "looking": ("Looking through your files…", "आपकी फ़ाइलें देख रहा हूँ…", "तुमच्या फाइल्स पाहत आहे…"),
    # --- uploads (W5) ---
    "send_now": ("Send the file or photos now.", "अब फ़ाइल या फ़ोटो भेजें।", "आता फाइल किंवा फोटो पाठवा."),
    "tip": (
        "Tip: send scans as Document, not Photo, so WhatsApp keeps them sharp.",
        "सुझाव: स्कैन को Photo नहीं, Document के रूप में भेजें, ताकि WhatsApp उन्हें साफ़ रखे।",
        "टीप: स्कॅन Photo म्हणून नाही, Document म्हणून पाठवा, म्हणजे WhatsApp ते स्पष्ट ठेवेल.",
    ),
    "uploads_off": (
        "Saving files from WhatsApp is switched off for your organisation.",
        "आपकी संस्था के लिए WhatsApp से फ़ाइलें सेव करना बंद है।",
        "तुमच्या संस्थेसाठी WhatsApp वरून फाइल्स सेव्ह करणे बंद आहे.",
    ),
    "too_many_files": (
        "You have sent many files in a short time. Please wait a while and send the rest later.",
        "आपने कम समय में बहुत फ़ाइलें भेजी हैं। कृपया थोड़ी देर रुककर बाकी बाद में भेजें।",
        "तुम्ही थोड्या वेळात खूप फाइल्स पाठवल्या आहेत. कृपया थोडे थांबून बाकीच्या नंतर पाठवा.",
    ),
    "no_video": (
        "Kaagaz saves documents and photos, not videos.",
        "Kaagaz दस्तावेज़ और फ़ोटो सेव करता है, वीडियो नहीं।",
        "Kaagaz दस्तऐवज आणि फोटो सेव्ह करते, व्हिडिओ नाही.",
    ),
    "save_one": ('Save "{}" to Kaagaz?', '"{}" को Kaagaz में सेव करें?', '"{}" Kaagaz मध्ये सेव्ह करायची?'),
    "save_many": (
        "You sent {} files. Save them to Kaagaz?",
        "आपने {} फ़ाइलें भेजीं। इन्हें Kaagaz में सेव करें?",
        "तुम्ही {} फाइल्स पाठवल्या. त्या Kaagaz मध्ये सेव्ह करायच्या?",
    ),
    "photos": (
        "You sent {} photos. How should I save them?",
        "आपने {} फ़ोटो भेजीं। इन्हें कैसे सेव करूँ?",
        "तुम्ही {} फोटो पाठवले. ते कसे सेव्ह करू?",
    ),
    "choose_button": ("Choose folder", "फ़ोल्डर चुनें", "फोल्डर निवडा"),
    "auto_button": ("Let Kaagaz decide", "Kaagaz तय करे", "Kaagaz ला ठरवू द्या"),
    "cancel_button": ("Cancel", "रद्द करें", "रद्द करा"),
    "one_button": ("One document", "एक दस्तावेज़", "एक दस्तऐवज"),
    "separate_button": ("Separate files", "अलग-अलग फ़ाइलें", "वेगवेगळ्या फाइल्स"),
    "save_here_button": ("Save here", "यहाँ सेव करें", "इथे सेव्ह करा"),
    "open_folder_button": ("Open folder", "फ़ोल्डर खोलें", "फोल्डर उघडा"),
    "back_button": ("Back", "वापस", "मागे"),
    "cancelled": ("Nothing was saved.", "कुछ भी सेव नहीं हुआ।", "काहीही सेव्ह झाले नाही."),
    "nothing_waiting": (
        "There is nothing waiting to be saved. Send the file again.",
        "सेव करने के लिए कुछ नहीं है। फ़ाइल फिर से भेजें।",
        "सेव्ह करण्यासाठी काहीही नाही. फाइल पुन्हा पाठवा.",
    ),
    "pick_folder": (
        'Choose a folder for "{}":',
        '"{}" के लिए फ़ोल्डर चुनें:',
        '"{}" साठी फोल्डर निवडा:',
    ),
    "pick_folder_many": (
        "Choose a folder for the {} files:",
        "{} फ़ाइलों के लिए फ़ोल्डर चुनें:",
        "{} फाइल्ससाठी फोल्डर निवडा:",
    ),
    "folders_in": ("Folders in {}:", "{} के फ़ोल्डर:", "{} मधील फोल्डर:"),
    "found_folders": ("Folders named like that:", "इस नाम से मिलते फ़ोल्डर:", "या नावाशी जुळणारे फोल्डर:"),
    "no_folder_named": (
        "No folder you can see has that name. Try other words, or choose one:",
        "आपको दिखने वाले किसी फ़ोल्डर का यह नाम नहीं है। दूसरे शब्द लिखें, या एक चुनें:",
        "तुम्हाला दिसणाऱ्या कोणत्याही फोल्डरचे हे नाव नाही. दुसरे शब्द लिहा, किंवा एक निवडा:",
    ),
    "type_folder": ("Type the folder's name.", "फ़ोल्डर का नाम लिखें।", "फोल्डरचे नाव लिहा."),
    "more_folders_row": ("More folders", "और फ़ोल्डर", "आणखी फोल्डर"),
    "type_name_row": ("Type a folder name", "फ़ोल्डर का नाम लिखें", "फोल्डरचे नाव लिहा"),
    "recent": ("Recent", "हाल का", "अलीकडील"),
    "inside": ("{} folders inside", "अंदर {} फ़ोल्डर", "आत {} फोल्डर"),
    "inside_one": ("1 folder inside", "अंदर 1 फ़ोल्डर", "आत 1 फोल्डर"),
    "folder_gone": (
        "This folder isn't available any more. Please choose again.",
        "यह फ़ोल्डर अब उपलब्ध नहीं है। कृपया फिर से चुनें।",
        "हे फोल्डर आता उपलब्ध नाही. कृपया पुन्हा निवडा.",
    ),
    "list_changed": (
        "That list has changed, choose again.",
        "वह सूची बदल गई है, फिर से चुनें।",
        "ती यादी बदलली आहे, पुन्हा निवडा.",
    ),
    "saved": ('Saved "{}" in {}.', '"{}" को {} में सेव किया।', '"{}" {} मध्ये सेव्ह केली.'),
    "saved_unfiled": (
        'Saved "{}". Kaagaz will file it once it has read it.',
        '"{}" सेव हो गई। पढ़ने के बाद Kaagaz इसे फ़ाइल कर देगा।',
        '"{}" सेव्ह झाली. वाचल्यानंतर Kaagaz ती फाइल करेल.',
    ),
    "saved_many": ("Saved {} files in {}.", "{} फ़ाइलें {} में सेव कीं।", "{} फाइल्स {} मध्ये सेव्ह केल्या."),
    "saved_many_unfiled": (
        "Saved {} files. Kaagaz will file them once it has read them.",
        "{} फ़ाइलें सेव हो गईं। पढ़ने के बाद Kaagaz इन्हें फ़ाइल कर देगा।",
        "{} फाइल्स सेव्ह झाल्या. वाचल्यानंतर Kaagaz त्या फाइल करेल.",
    ),
    "fetch_failed": (
        "WhatsApp didn't send the rest of the files just now. Tap Save the rest to try again.",
        "WhatsApp ने अभी बाकी फ़ाइलें नहीं भेजीं। फिर से कोशिश करने के लिए बाकी सेव करें दबाएँ।",
        "WhatsApp ने आत्ता बाकीच्या फाइल्स पाठवल्या नाहीत. पुन्हा प्रयत्न करण्यासाठी बाकीच्या सेव्ह करा दाबा.",
    ),
    "save_rest_button": ("Save the rest", "बाकी सेव करें", "बाकीच्या सेव्ह करा"),
    "ready_many": (
        "Ready. {} files are filed in {}. You can ask me about them now.",
        "तैयार। {} फ़ाइलें {} में फ़ाइल हैं। अब आप इनके बारे में मुझसे पूछ सकते हैं।",
        "तयार. {} फाइल्स {} मध्ये फाइल केल्या आहेत. आता तुम्ही मला त्यांच्याबद्दल विचारू शकता.",
    ),
    "ready_many_kaagaz": (
        "Ready. {} files are in Kaagaz. You can ask me about them now.",
        "तैयार। {} फ़ाइलें Kaagaz में हैं। अब आप इनके बारे में मुझसे पूछ सकते हैं।",
        "तयार. {} फाइल्स Kaagaz मध्ये आहेत. आता तुम्ही मला त्यांच्याबद्दल विचारू शकता.",
    ),
    "saved_kaagaz": ('Saved "{}" in Kaagaz.', '"{}" को Kaagaz में सेव किया।', '"{}" Kaagaz मध्ये सेव्ह केली.'),
    "already": ('Already in Kaagaz: "{}".', 'Kaagaz में पहले से है: "{}"।', 'Kaagaz मध्ये आधीच आहे: "{}".'),
    "already_trash": (
        'Already in Kaagaz, in Trash: "{}". Restore it from Trash in Kaagaz.',
        'Kaagaz में पहले से है, Trash में: "{}"। इसे Kaagaz के Trash से वापस लाएँ।',
        'Kaagaz मध्ये आधीच आहे, Trash मध्ये: "{}". ती Kaagaz च्या Trash मधून परत आणा.',
    ),
    "hidden_copy": (
        "This file is already in Kaagaz, in a place you can't see. Ask your admin if you need it.",
        "यह फ़ाइल पहले से Kaagaz में है, ऐसी जगह जो आपको नहीं दिखती। ज़रूरत हो तो अपने एडमिन से पूछें।",
        "ही फाइल आधीच Kaagaz मध्ये आहे, अशा ठिकाणी जे तुम्हाला दिसत नाही. गरज असल्यास तुमच्या ॲडमिनला विचारा.",
    ),
    "locked_copy": (
        "A copy already exists and is locked.",
        "इसकी एक प्रति पहले से है और लॉक है।",
        "याची एक प्रत आधीच आहे आणि ती लॉक आहे.",
    ),
    "too_large": (
        '"{}" can\'t be saved: it is too large.',
        '"{}" सेव नहीं हो सकती: यह बहुत बड़ी है।',
        '"{}" सेव्ह होऊ शकत नाही: ती खूप मोठी आहे.',
    ),
    "wrong_type": (
        "\"{}\" can't be saved: Kaagaz doesn't take this kind of file.",
        '"{}" सेव नहीं हो सकती: Kaagaz इस तरह की फ़ाइल नहीं लेता।',
        '"{}" सेव्ह होऊ शकत नाही: Kaagaz या प्रकारची फाइल घेत नाही.',
    ),
    "empty_file": (
        '"{}" can\'t be saved: it is empty.',
        '"{}" सेव नहीं हो सकती: यह खाली है।',
        '"{}" सेव्ह होऊ शकत नाही: ती रिकामी आहे.',
    ),
    "storage_full": (
        "\"{}\" can't be saved: your organisation's Kaagaz storage is full. Ask your admin.",
        '"{}" सेव नहीं हो सकती: आपकी संस्था का Kaagaz स्टोरेज भर गया है। अपने एडमिन से पूछें।',
        '"{}" सेव्ह होऊ शकत नाही: तुमच्या संस्थेचे Kaagaz स्टोरेज भरले आहे. तुमच्या ॲडमिनला विचारा.',
    ),
    "media_gone": (
        'WhatsApp no longer has "{}". Please send it again.',
        'WhatsApp के पास अब "{}" नहीं है। कृपया इसे फिर से भेजें।',
        'WhatsApp कडे आता "{}" नाही. कृपया ती पुन्हा पाठवा.',
    ),
    "not_saved": (
        '"{}" couldn\'t be saved. Try uploading it in Kaagaz.',
        '"{}" सेव नहीं हो सकी। इसे Kaagaz में अपलोड करके देखें।',
        '"{}" सेव्ह होऊ शकली नाही. ती Kaagaz मध्ये अपलोड करून पाहा.',
    ),
    "not_joined": (
        "These photos can't be joined into one document, so each was saved on its own.",
        "ये फ़ोटो एक दस्तावेज़ में नहीं जुड़ सकतीं, इसलिए हर एक अलग से सेव हुई।",
        "हे फोटो एका दस्तऐवजात जोडता येत नाहीत, म्हणून प्रत्येक वेगळा सेव्ह केला.",
    ),
    "ready": (
        'Ready. "{}" is filed in {}. You can ask me about it now.',
        'तैयार। "{}" {} में फ़ाइल है। अब आप इसके बारे में मुझसे पूछ सकते हैं।',
        'तयार. "{}" {} मध्ये फाइल केली आहे. आता तुम्ही मला तिच्याबद्दल विचारू शकता.',
    ),
    "ready_unfiled": (
        'Ready. "{}" is in Kaagaz. You can ask me about it now.',
        'तैयार। "{}" Kaagaz में है। अब आप इसके बारे में मुझसे पूछ सकते हैं।',
        'तयार. "{}" Kaagaz मध्ये आहे. आता तुम्ही मला तिच्याबद्दल विचारू शकता.',
    ),
    "rejected": (
        '"{}" was not kept: it failed the virus check.',
        '"{}" नहीं रखी गई: यह वायरस जाँच में फ़ेल हुई।',
        '"{}" ठेवली नाही: ती व्हायरस तपासणीत नापास झाली.',
    ),
    "not_read": (
        'Kaagaz couldn\'t read "{}". Open it in Kaagaz to see why.',
        'Kaagaz "{}" नहीं पढ़ सका। कारण जानने के लिए इसे Kaagaz में खोलें।',
        'Kaagaz "{}" वाचू शकले नाही. कारण पाहण्यासाठी ती Kaagaz मध्ये उघडा.',
    ),
}
# The language list's rows: each in its own language, whatever the chat's.
LANGUAGE_NAMES: dict[Language, str] = {"en": "English", "hi": "हिंदी", "mr": "मराठी"}


def say(key: str, lang: Language, *values: object) -> str:
    return TEXTS[key][LANGUAGES.index(lang)].format(*values)


_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z0-9])")
# 9 digits or more: groups of digits split by up to 3 spaces (any, a no-break space, a tab or a new
# line too), hyphens or dashes (W7: Unicode's hyphens and dashes U+2010 to U+2015, the minus, the
# small and full-width hyphen-minus), never next to another digit.
_NUMBER = re.compile(r"(?<!\d)\d+(?:[\s\-\u2010-\u2015\u2212\ufe58\ufe63\uff0d]{1,3}\d+)*(?!\d)")
# W7: the joiner and non-joiner shape a Devanagari word after one of its letters or signs (not a digit).
_SHAPED = re.compile("[\u0900-\u0965\u0970-\u097f]")
MASK = "••••"
MASKED_DIGITS = 9  # the shortest bank account number; Aadhaar is 12, a card 13 to 19


def _digits(found: re.Match[str]) -> str:
    run = found.group(0)
    digits = [ch for ch in run if ch.isdigit()]
    return MASK + "".join(digits[-4:]) if len(digits) >= MASKED_DIGITS else run


def _visible(text: str) -> str:
    """W7: ``text`` without what draws nothing (Unicode's format characters), but the joiner and
    non-joiner that shape a Devanagari word. O(len(text))."""
    if text.isascii():
        return text  # none of them is ASCII
    return "".join(
        ch
        for i, ch in enumerate(text)
        if unicodedata.category(ch) != "Cf" or (ch in "\u200c\u200d" and i > 0 and _SHAPED.match(text[i - 1]))
    )


def mask(text: str) -> tuple[str, bool]:
    """``text`` with its ID and account numbers masked, and whether any was. O(len(text))."""
    visible = _visible(text)
    masked = _PAN.sub(lambda found: MASK + found.group(0)[-4:].upper(), visible)
    masked = _NUMBER.sub(_digits, masked)
    return masked, masked != visible


# W7: an address with a scheme, or starting www., or a domain with a path (WhatsApp links each).
_LINK = re.compile(
    r"\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}/\S*", re.IGNORECASE
)


def kaagaz_links_only(text: str, home: str) -> str:
    """``text`` without its links but Kaagaz's own (``home``, APP_URL: it, or it and a path).
    O(len(text))."""

    def kept(found: re.Match[str]) -> str:
        link = found.group(0)
        return link if home and (link == home or link.startswith(home + "/")) else ""

    return _LINK.sub(kept, text)


def masked(text: str) -> str:
    """``text`` with its ID and account numbers masked (``mask``'s text)."""
    return mask(text)[0]
