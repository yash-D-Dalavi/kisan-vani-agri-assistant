"""
glossary.py - Hindi / Marathi / English agricultural glossary and text analysis helpers.

The glossary is organised as *concepts*.  Every concept has surface forms in en/hi/mr.
Text (queries AND documents) is mapped to concept ids such as ``crop_cotton`` or ``sym_yellow``.
Because both sides use the same concept ids this gives cheap but effective cross-lingual
matching (a Marathi query can match a Hindi or English passage) and guarantees that crop,
pest, chemical and scheme names are never "mistranslated": they are matched, not generated.

Surface-form syntax
  "yellow"      ASCII  -> token equals or starts with it (yellow, yellowing)
  "पील"          Devanagari stem -> a token that starts with it (पीला, पीली, पीलापन)
  "=पान"         exact token only (needed for short words such as पान/leaf vs पानी/water)
  "pink bollworm" / "पीएम किसान"  multi-word -> substring of the normalised text
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Set, Tuple

# --------------------------------------------------------------------------
# Tokenisation (Python's \w splits Devanagari at vowel signs, so use ranges)
# --------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[ऀ-ॣ०-ॿ]+|[A-Za-z]+|\d+(?:[.,]\d+)?")
_DEV_RE = re.compile(r"[ऀ-ॿ]")

STOPWORDS = {
    # Hindi
    "है", "हैं", "का", "की", "के", "को", "से", "में", "पर", "और", "या", "यह", "वह", "क्या", "कैसे", "कब",
    "मेरे", "मेरी", "मेरा", "मैं", "हो", "रहा", "रही", "रहे", "कर", "करें", "करना", "लिए", "तो", "भी", "ही",
    "बताओ", "बताइए", "बताएं", "कृपया", "जी", "एक", "इस", "उस", "था", "थी", "गया", "गई", "सकता", "सकते",
    # Marathi
    "आहे", "आहेत", "काय", "कसे", "कधी", "माझ्या", "माझा", "माझी", "माझे", "मला", "आणि", "किंवा", "हे", "ते",
    "साठी", "मध्ये", "वर", "चा", "ची", "चे", "ला", "ना", "करा", "करावे", "सांगा", "कृपया", "एक", "या", "त्या",
    "होते", "झाले", "झाला", "शकतो", "शकते",
    # English / romanised
    "the", "a", "an", "is", "are", "of", "in", "on", "for", "to", "and", "or", "what", "how", "my", "me", "i",
    "please", "tell", "kya", "hai", "ka", "ki", "ke", "mera", "meri", "kay", "aahe", "majha", "mazi",
}

_DEV_SUFFIXES = sorted(
    ["ों", "ें", "ियां", "ियों", "में", "से", "पर", "का", "की", "के", "को", "ाच्या", "ाचा", "ाची", "ाचे", "ात", "ला", "ना",
     "चा", "ची", "चे", "मध्ये", "त"],
    key=len, reverse=True,
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("‍", "").replace("‌", "")  # ZWJ / ZWNJ
    text = text.replace("ऱ्", "र्").replace("़", "")           # nukta: ज़ -> ज
    return text.lower().strip()


def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(normalize(text))


def stem_token(tok: str) -> str:
    """Very light suffix stripper for Devanagari tokens (keeps >= 3 chars)."""
    if not _DEV_RE.search(tok):
        return tok
    for suf in _DEV_SUFFIXES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            return tok[: -len(suf)]
    return tok


def content_tokens(text: str) -> List[str]:
    return [stem_token(t) for t in tokenize(text) if t not in STOPWORDS and len(t) > 1]


# --------------------------------------------------------------------------
# Concept table:  id -> (category, {"en": [...], "hi": [...], "mr": [...]})
# --------------------------------------------------------------------------
C: Dict[str, Tuple[str, Dict[str, List[str]]]] = {
    # ---- crops
    "crop_cotton": ("crop", {"en": ["cotton"], "hi": ["कपास", "रुई"], "mr": ["कापूस", "कापस", "कपाशी", "कपाशीव"]}),
    "crop_soybean": ("crop", {"en": ["soybean", "soyabean", "soya"], "hi": ["सोयाबीन"], "mr": ["सोयाबीन", "सोयाबिन"]}),
    "crop_wheat": ("crop", {"en": ["wheat"], "hi": ["गेहूं", "गेहू", "गेंहू", "गेहूँ"], "mr": ["=गहू", "गव्हा", "गव्हाच", "=गव्हाची"]}),
    "crop_tomato": ("crop", {"en": ["tomato"], "hi": ["टमाटर"], "mr": ["टोमॅटो", "टोमेटो", "टमाटे"]}),
    "crop_onion": ("crop", {"en": ["onion"], "hi": ["प्याज"], "mr": ["कांद", "कांदा"]}),
    "crop_rice": ("crop", {"en": ["rice", "paddy"], "hi": ["धान", "चावल"], "mr": ["भात", "धान"]}),
    "crop_maize": ("crop", {"en": ["maize", "corn"], "hi": ["मक्का"], "mr": ["=मका", "मक्या"]}),
    "crop_sugarcane": ("crop", {"en": ["sugarcane"], "hi": ["गन्ना", "गन्ने"], "mr": ["=ऊस", "ऊसा", "ऊसाच", "ऊसाव", "उसा", "उसाच"]}),
    # crops that are NOT in the demo knowledge base: recognised on purpose so that questions about them are refused
    # instead of being answered from another crop's document
    "crop_gram": ("crop", {"en": ["chickpea", "gram"], "hi": ["चना", "चने"], "mr": ["हरभरा", "हरभऱ्या"]}),
    "crop_tur": ("crop", {"en": ["pigeon pea", "tur dal", "arhar"], "hi": ["अरहर", "=तूर"], "mr": ["=तूर", "तुरी"]}),
    "crop_groundnut": ("crop", {"en": ["groundnut", "peanut"], "hi": ["मूंगफली"], "mr": ["भुईमूग"]}),
    "crop_potato": ("crop", {"en": ["potato"], "hi": ["आलू"], "mr": ["बटाट"]}),
    "crop_chilli": ("crop", {"en": ["chilli", "chili"], "hi": ["मिर्च"], "mr": ["मिरची", "मिरच्य"]}),
    "crop_banana": ("crop", {"en": ["banana"], "hi": ["=केला", "=केले"], "mr": ["=केळी", "केळीच"]}),
    "crop_grape": ("crop", {"en": ["grape", "grapes"], "hi": ["अंगूर"], "mr": ["द्राक्ष"]}),
    "crop_pomegranate": ("crop", {"en": ["pomegranate"], "hi": ["अनार"], "mr": ["डाळिंब"]}),
    "crop_brinjal": ("crop", {"en": ["brinjal", "eggplant"], "hi": ["बैंगन"], "mr": ["वांगी", "वांग्य"]}),
    "crop_jowar": ("crop", {"en": ["sorghum", "jowar"], "hi": ["ज्वार"], "mr": ["ज्वारी", "ज्वारीच"]}),
    "crop_bajra": ("crop", {"en": ["bajra", "pearl millet"], "hi": ["बाजरा"], "mr": ["बाजरी"]}),
    "crop_generic": ("generic", {"en": ["crop", "crops", "farm", "field"], "hi": ["फसल", "खेत"], "mr": ["पीक", "शेत", "पिक"]}),
    # ---- symptoms
    "sym_yellow": ("symptom", {"en": ["yellow"], "hi": ["पील"], "mr": ["पिवळ"]}),
    "sym_leaf": ("symptom", {"en": ["leaf", "leaves"], "hi": ["पत्त"], "mr": ["=पान", "=पाने", "पानांच", "पानांव"]}),
    "sym_spots": ("symptom", {"en": ["spot", "spots", "blotch"], "hi": ["धब्बे", "धब्बा"], "mr": ["डाग"]}),
    "sym_wilt": ("symptom", {"en": ["wilt", "wilting"], "hi": ["मुरझा"], "mr": ["सुकत", "कोमेज"]}),
    # ---- pests / diseases
    "pest_sucking": ("pest", {"en": ["aphid", "aphids", "jassid", "jassids", "whitefly", "sucking"],
                              "hi": ["माहू", "तेला", "सफेद मक्खी", "रस चूसक"], "mr": ["मावा", "तुडतुडे", "पांढरी माशी", "रस शोषक"]}),
    "pest_pbw": ("pest", {"en": ["pink bollworm", "bollworm", "pbw"], "hi": ["गुलाबी सुंडी", "सुंडी", "बॉलवर्म"],
                          "mr": ["गुलाबी बोंडअळी", "बोंडअळ", "बोंडअळी"]}),
    "dis_blight": ("pest", {"en": ["late blight", "blight"], "hi": ["झुलसा", "ब्लाइट", "पछेती"], "mr": ["करपा", "ब्लाइट", "लेट ब्लाइट"]}),
    "pest_generic": ("generic", {"en": ["pest", "pests", "insect", "insects", "disease", "diseases"],
                                 "hi": ["कीट", "रोग", "बीमारी"], "mr": ["कीड", "किडी", "किड", "रोग"]}),
    # ---- chemicals
    "chem_pesticide": ("chemical", {"en": ["pesticide", "insecticide", "fungicide", "spray", "chemical"],
                                    "hi": ["कीटनाशक", "=दवा", "=दवाई", "छिड़क", "स्प्रे", "फफूंदनाशक"],
                                    "mr": ["कीटकनाशक", "=औषध", "फवारणी", "फवारा", "बुरशीनाशक", "स्प्रे"]}),
    "chem_mancozeb": ("chemical", {"en": ["mancozeb"], "hi": ["मैंको", "मैन्को", "मैंकोज़ेब"], "mr": ["मॅन्को", "मँको", "मैंको"]}),
    "chem_mgso4": ("chemical", {"en": ["magnesium sulphate", "magnesium"], "hi": ["मैग्नीशियम"], "mr": ["मॅग्नेशियम", "मैग्नीशियम"]}),
    "chem_other": ("chemical_other", {"en": ["monocrotophos", "endosulfan", "chlorpyrifos", "imidacloprid", "cypermethrin", "paraquat"],
                                      "hi": ["मोनोक्रोटोफॉस", "एंडोसल्फान", "क्लोरपायरीफॉस", "इमिडाक्लोप्रिड"],
                                      "mr": ["मोनोक्रोटोफॉस", "एंडोसल्फान", "क्लोरपायरीफॉस", "इमिडाक्लोप्रिड"]}),
    "dose": ("dose", {"en": ["dose", "dosage", "quantity", "per litre", "per liter", "gram", "grams", "ml"],
                      "hi": ["मात्रा", "=ग्राम", "मिली", "लीटर", "डालना"],
                      "mr": ["मात्रा", "प्रमाण", "ग्रॅम", "मिली", "लिटर", "टाकावे", "टाकू"]}),
    # "how much?" question word: generic on its own, becomes a dosage question only together with a chemical / fertiliser
    "qty_q": ("generic", {"en": ["how much"], "hi": ["कितना", "कितनी", "कितने"], "mr": ["किती"]}),
    "over_dose": ("dose", {"en": ["double dose", "more dose", "extra dose", "overdose", "double the dose"],
                           "hi": ["दुगनी", "दोगुनी", "ज्यादा मात्रा", "अधिक मात्रा", "दुगुनी"],
                           "mr": ["दुप्पट", "जास्त प्रमाण", "जास्त मात्रा", "दुपटीने"]}),
    # ---- inputs / practices
    "fertilizer": ("practice", {"en": ["fertilizer", "fertiliser", "urea", "dap", "nutrient", "nitrogen"],
                                "hi": ["खाद", "उर्वरक", "यूरिया", "डीएपी", "नाइट्रोजन"],
                                "mr": ["=खत", "=खते", "=खतांचे", "=खताची", "युरिया", "नत्र"]}),
    "prac_sowing": ("practice", {"en": ["sowing", "sow", "planting"], "hi": ["बुवाई", "बुआई", "बोआई", "बोना", "बोएं"],
                                 "mr": ["पेरणी", "पेरावे", "पेरू", "लागवड"]}),
    "prac_irrigation": ("practice", {"en": ["irrigation", "irrigate", "watering"], "hi": ["सिंचाई", "पानी"], "mr": ["सिंचन", "पाणी", "पाणि"]}),
    "prac_seed_treat": ("practice", {"en": ["seed treatment"], "hi": ["बीज उपचार", "बीजोपचार"], "mr": ["बीजप्रक्रिया", "बीज प्रक्रिया"]}),
    "prac_storage": ("practice", {"en": ["storage", "store", "storing", "curing"], "hi": ["भंडारण", "स्टोरेज"], "mr": ["साठवण"]}),
    "soil_test": ("practice", {"en": ["soil test", "soil testing"], "hi": ["मिट्टी की जांच", "मिट्टी जांच", "मृदा परीक्षण"], "mr": ["माती परीक्षण"]}),
    "drainage": ("practice", {"en": ["drain", "waterlogging", "waterlogged"], "hi": ["जलभराव", "जल निकास"], "mr": ["पाणी साचल", "निचरा"]}),
    # ---- market & weather
    "price": ("market", {"en": ["price", "prices", "rate", "rates", "bhav", "bhaav", "msp"], "hi": ["भाव", "कीमत", "दाम", "=रेट", "मूल्य"],
                         "mr": ["भाव", "=दर", "किंमत", "बाजारभाव"]}),
    "mandi": ("market", {"en": ["mandi", "market", "apmc", "agmarknet", "enam", "e-nam"], "hi": ["मंडी", "बाजार"], "mr": ["मंडी", "बाजार"]}),
    "weather": ("weather", {"en": ["weather", "forecast", "temperature", "humidity", "climate"], "hi": ["मौसम", "तापमान", "पूर्वानुमान"],
                            "mr": ["हवामान", "तापमान", "अंदाज"]}),
    "rain": ("weather", {"en": ["rain", "rainfall", "rainy", "shower"], "hi": ["बारिश", "बरसात", "वर्षा"], "mr": ["=पाऊस", "पावस"]}),
    # ---- schemes
    "scheme": ("scheme", {"en": ["scheme", "yojana", "subsidy", "benefit", "eligibility", "instalment", "installment"],
                          "hi": ["योजना", "सब्सिडी", "लाभ", "पात्रता", "किस्त"], "mr": ["योजना", "अनुदान", "लाभ", "पात्रता", "हप्ता", "हप्त"]}),
    "scheme_pmkisan": ("scheme", {"en": ["pm-kisan", "pm kisan", "pmkisan", "kisan samman"], "hi": ["पीएम किसान", "पीएम-किसान", "किसान सम्मान"],
                                  "mr": ["पीएम किसान", "पीएम-किसान", "किसान सन्मान", "किसान सम्मान"]}),
    "scheme_pmfby": ("scheme", {"en": ["crop insurance", "pmfby", "fasal bima", "insurance", "premium"], "hi": ["फसल बीमा", "बीमा", "प्रीमियम"],
                                "mr": ["पीक विमा", "विमा", "प्रीमियम"]}),
    "helpline": ("helpline", {"en": ["kisan call centre", "kisan call center", "kcc", "helpline", "toll free", "kvk"],
                              "hi": ["किसान कॉल सेंटर", "हेल्पलाइन", "कृषि विज्ञान केंद्र", "केवीके", "टोल फ्री"],
                              "mr": ["किसान कॉल सेंटर", "हेल्पलाईन", "हेल्पलाइन", "कृषि विज्ञान केंद्र", "केवीके", "टोल फ्री"]}),
    "season_kharif": ("season", {"en": ["kharif"], "hi": ["खरीफ"], "mr": ["खरीप", "खरिप"]}),
    "season_rabi": ("season", {"en": ["rabi"], "hi": ["रबी", "रबि"], "mr": ["रब्बी", "रबी"]}),
    # ---- scope guards
    "off_topic": ("off_topic", {"en": ["cricket", "ipl", "movie", "film", "election", "bitcoin", "crypto", "share market", "stock market",
                                       "lottery", "cinema", "song", "fever", "headache", "politics", "gold", "petrol", "loan"],
                                "hi": ["क्रिकेट", "फिल्म", "चुनाव", "शेयर बाजार", "लॉटरी", "गाना", "सिनेमा", "बुखार", "सिरदर्द", "सोने का", "सोना", "पेट्रोल", "कर्ज"],
                                "mr": ["क्रिकेट", "चित्रपट", "निवडणूक", "शेअर बाजार", "लॉटरी", "गाणे", "ताप", "डोकेदुखी", "सोन्याचा", "सोने", "पेट्रोल", "कर्ज"]}),
    "speculative": ("speculative", {"en": ["guarantee", "guaranteed", "assured profit", "sure profit", "will rise", "will increase", "next month price",
                                           "predict", "next year", "double my income"],
                                    "hi": ["गारंटी", "पक्का मुनाफा", "निश्चित लाभ", "बढ़ेंगे", "बढ़ेगा", "अगले साल", "अगले महीने", "भविष्य"],
                                    "mr": ["हमी", "निश्चित नफा", "वाढतील", "वाढेल", "पुढील वर्षी", "पुढच्या महिन्यात", "पुढील महिन्यात", "भविष्यात"]}),
    "unsafe": ("unsafe", {"en": ["poison", "kill someone", "burn crop", "destroy neighbour", "destroy neighbor", "harm neighbour"],
                          "hi": ["जहर", "ज़हर", "फसल जलाने", "पड़ोसी की फसल", "पड़ोसी के खेत"],
                          "mr": ["विष", "जहर", "शेजाऱ्याच", "शेजाऱ्याच्या", "पीक जाळ"]}),
}

INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|above|prior) (instructions|rules)",
    r"(reveal|show|print).{0,20}(system prompt|instructions)",
    r"you are now", r"jailbreak", r"developer mode", r"act as (an? )?(unrestricted|dan)",
    r"निर्देश(ों)? (को )?(अनदेखा|भूल)", r"पिछले निर्देश", r"सिस्टम प्रॉम्प्ट", r"सूचना (विसरा|दुर्लक्ष)", r"मागील सूचना",
]

# Implied crop when a pest concept is mentioned without the crop
IMPLIED_CROP = {"pest_pbw": "crop_cotton", "dis_blight": "crop_tomato"}

# Locations: canonical -> surface forms
LOCATIONS: Dict[str, List[str]] = {
    "Pune": ["pune", "पुणे", "पुण्य"],
    "Latur": ["latur", "लातूर"],
    "Nashik": ["nashik", "nasik", "नाशिक", "नासिक"],
    "Nagpur": ["nagpur", "नागपुर", "नागपूर"],
    "Akola": ["akola", "अकोला"],
    "Jalgaon": ["jalgaon", "जळगाव", "जलगांव", "जलगाव"],
    "Solapur": ["solapur", "सोलापुर", "सोलापूर"],
    "Kolhapur": ["kolhapur", "कोल्हापुर", "कोल्हापूर"],
    "Lasalgaon": ["lasalgaon", "लासलगांव", "लासलगाव"],
    "Amravati": ["amravati", "अमरावती"],
    "Delhi": ["delhi", "दिल्ली"],
    "Aurangabad": ["aurangabad", "औरंगाबाद", "संभाजीनगर"],
}

# Display names for crops / commodities in each language
CROP_NAMES = {
    "cotton": {"en": "Cotton", "hi": "कपास", "mr": "कापूस"},
    "soybean": {"en": "Soybean", "hi": "सोयाबीन", "mr": "सोयाबीन"},
    "wheat": {"en": "Wheat", "hi": "गेहूं", "mr": "गहू"},
    "tomato": {"en": "Tomato", "hi": "टमाटर", "mr": "टोमॅटो"},
    "onion": {"en": "Onion", "hi": "प्याज", "mr": "कांदा"},
    "rice": {"en": "Rice", "hi": "धान", "mr": "भात"},
    "maize": {"en": "Maize", "hi": "मक्का", "mr": "मका"},
    "sugarcane": {"en": "Sugarcane", "hi": "गन्ना", "mr": "ऊस"},
}
LOCATION_NAMES = {
    "Pune": {"hi": "पुणे", "mr": "पुणे"}, "Latur": {"hi": "लातूर", "mr": "लातूर"}, "Nashik": {"hi": "नाशिक", "mr": "नाशिक"},
    "Nagpur": {"hi": "नागपुर", "mr": "नागपूर"}, "Akola": {"hi": "अकोला", "mr": "अकोला"}, "Jalgaon": {"hi": "जलगांव", "mr": "जळगाव"},
    "Solapur": {"hi": "सोलापुर", "mr": "सोलापूर"}, "Kolhapur": {"hi": "कोल्हापुर", "mr": "कोल्हापूर"},
    "Lasalgaon": {"hi": "लासलगांव", "mr": "लासलगाव"}, "Amravati": {"hi": "अमरावती", "mr": "अमरावती"},
    "Delhi": {"hi": "दिल्ली", "mr": "दिल्ली"}, "Aurangabad": {"hi": "औरंगाबाद", "mr": "औरंगाबाद"},
}

# Language identification markers
_MR_MARKERS = {"आहे", "आहेत", "काय", "माझ्या", "माझा", "माझी", "माझे", "कसे", "किती", "मध्ये", "साठी", "मला", "तुम्ही", "नाही", "करावे",
               "सांगा", "द्यावे", "शेतात", "होतो", "झाला", "झाली", "आणि", "पिवळी", "पिवळे", "पाने", "कधी", "काय", "सध्या",
               "कशी", "करावी", "चालेल", "पडेल", "तर", "पाऊस", "पुढचे", "केले", "करू", "आली", "आला", "होत", "आहेस"}
_MR_SUFFIX = ("मध्ये", "साठी", "ावर", "ाचे", "ाची", "ाचा", "ावी", "ेल", "णूक", "ांचा", "ाचं")
_HI_MARKERS = {"है", "हैं", "क्या", "मेरे", "मेरी", "मेरा", "कैसे", "कितना", "कितनी", "में", "करें", "लिए", "मुझे", "नहीं", "बताओ",
               "बताइए", "और", "रहा", "रही", "रहे", "सकता", "कब", "क्यों"}
_ROMAN_HI = {"kya", "hai", "ka", "ki", "ke", "mera", "meri", "kitna", "kaise", "bhav", "bhaav", "karu", "karein", "nahi", "mujhe"}
_ROMAN_MR = {"kay", "aahe", "kiti", "majha", "majhi", "kase", "mala", "kasa"}


def detect_language(text: str, hint: str | None = None) -> Tuple[str, float]:
    """Return (lang, confidence). Handles Devanagari Hindi/Marathi, Roman-script Hinglish and English."""
    toks = tokenize(text)
    if not toks:
        return (hint or "hi"), 0.0
    dev = sum(1 for t in toks if _DEV_RE.search(t))
    if dev == 0:
        r_hi = sum(1 for t in toks if t in _ROMAN_HI)
        r_mr = sum(1 for t in toks if t in _ROMAN_MR)
        if r_mr > r_hi:
            return "mr", 0.7
        if r_hi > 0:
            return "hi", 0.7
        return "en", 0.9
    mr = (sum(2 for t in toks if t in _MR_MARKERS) + sum(2 for t in toks if "ळ" in t) + sum(1 for t in toks if t.endswith("च्या") or t.endswith("ाचा"))
          + sum(1 for t in toks if len(t) > 4 and t.endswith(_MR_SUFFIX)))
    hi = sum(2 for t in toks if t in _HI_MARKERS)
    if mr == hi:
        return (hint if hint in ("hi", "mr") else "hi"), 0.5
    lang = "mr" if mr > hi else "hi"
    conf = min(1.0, 0.55 + 0.1 * abs(mr - hi))
    return lang, conf


# --------------------------------------------------------------------------
# Concept extraction
# --------------------------------------------------------------------------
def _match(surface: str, text: str, tokens: List[str]) -> bool:
    surface = normalize(surface)
    if surface.startswith("="):
        return surface[1:] in tokens
    if " " in surface or "-" in surface:
        return surface in text
    if _DEV_RE.search(surface):
        return any(t.startswith(surface) for t in tokens)
    return any(t == surface or (len(surface) >= 4 and t.startswith(surface)) for t in tokens)


def extract_concepts(text: str) -> List[str]:
    norm = normalize(text)
    toks = tokenize(text)
    found: List[str] = []
    for cid, (_cat, forms) in C.items():
        if any(_match(s, norm, toks) for lang_forms in forms.values() for s in lang_forms):
            found.append(cid)
    return found


def concept_category(cid: str) -> str:
    return C[cid][0]


def extract_entities(text: str) -> Dict[str, object]:
    concepts = extract_concepts(text)
    norm = normalize(text)
    toks = tokenize(text)
    crops = [c.replace("crop_", "") for c in concepts if c.startswith("crop_") and c != "crop_generic"]
    # implied crop (e.g. pink bollworm -> cotton)
    implied = None
    if not crops:
        for cid, crop in IMPLIED_CROP.items():
            if cid in concepts:
                implied = crop.replace("crop_", "")
    location = None
    for canon, forms in LOCATIONS.items():
        if any(_match(f, norm, toks) for f in forms):
            location = canon
            break
    season = next((c.replace("season_", "") for c in concepts if c.startswith("season_")), None)
    chemicals = [c for c in concepts if concept_category(c) in ("chemical", "chemical_other")]
    return {
        "crop": crops[0] if crops else implied,
        "crops_all": crops,
        "crop_implied": bool(implied and not crops),
        "location": location,
        "season": season,
        "chemicals": chemicals,
        "concepts": concepts,
    }


def detect_injection(text: str) -> bool:
    low = normalize(text)
    return any(re.search(p, low) for p in INJECTION_PATTERNS)


def glossary_terms_preserved(source: str, target: str) -> Tuple[int, int]:
    """Count how many glossary concepts present in `source` are also present in `target` (translation consistency)."""
    src = set(c for c in extract_concepts(source) if concept_category(c) in ("crop", "pest", "chemical", "scheme"))
    tgt = set(extract_concepts(target))
    return len(src & tgt), len(src)


def localized(term_map: Dict[str, Dict[str, str]], key: str, lang: str) -> str:
    entry = term_map.get(key, {})
    return entry.get(lang) or entry.get("en") or key
