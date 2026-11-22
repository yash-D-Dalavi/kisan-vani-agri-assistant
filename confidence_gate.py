"""
confidence_gate.py - Module 10 (Confidence Gate and Fallback) + the guardrail half of Module 12.

Decision = answer | ask_repeat | ask_clarify | refuse, always with REASON CODES and the numeric signals behind them.

Signals combined (blueprint 10): ASR confidence, reranker score, evidence coverage, grounding check, intent scope.
Hard rules (never overridden by a high score):
  * prompt-injection / unsafe / off-topic requests are refused politely
  * speculative requests (future prices, guaranteed profit) are refused
  * pesticide / fertiliser DOSAGE: answered only from an APPROVED dose-bearing source that covers the named crop and
    chemical; "double the dose", chemical mixing and chemicals absent from approved sources are refused;
    every chemical answer carries a safety note
  * every refusal hands over to the Kisan Call Centre (1800-180-1551) and the local KVK, or asks the farmer to repeat
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Sequence

import config
import glossary as G
from knowledge_base import Evidence, coverage_of, query_weight_total

T = config.T

# ---- reason codes -------------------------------------------------------------------------------------------------------
OK = "OK_GROUNDED"
ASR_UNCERTAIN = "ASR_UNCERTAIN_CONFIRM"
LOW_ASR = "LOW_ASR_CONFIDENCE"
AUDIO_FAIL = "AUDIO_DECODE_FAILED"
NO_SPEECH = "NO_SPEECH_DETECTED"
INJECTION = "PROMPT_INJECTION"
UNSAFE = "UNSAFE_REQUEST"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
SPECULATIVE = "SPECULATIVE_REQUEST"
OVERDOSE = "UNSAFE_DOSE_REQUEST"
MIXING = "CHEMICAL_MIXING"
UNAPPROVED_CHEM = "CHEMICAL_NOT_IN_APPROVED_SOURCES"
NO_DOSE = "NO_APPROVED_DOSE_SOURCE"
NO_EVIDENCE = "NO_EVIDENCE"
LOW_RETRIEVAL = "LOW_RETRIEVAL_SCORE"
LOW_COVERAGE = "LOW_EVIDENCE_COVERAGE"
MISSING_ENTITY = "MISSING_KEY_ENTITY"
LOW_OVERALL = "LOW_OVERALL_CONFIDENCE"
UNGROUNDED = "UNGROUNDED_ANSWER"
UNCLEAR = "UNCLEAR_QUERY"
MISSING_CROP = "MISSING_CROP"
MISSING_LOCATION = "MISSING_LOCATION"
TOOL_DOWN = "TOOL_UNAVAILABLE"
TOOL_NO_DATA = "TOOL_NO_DATA"
CHEM_NOTE = "CHEMICAL_SAFETY_NOTE_ADDED"
STALE = "DATA_MAY_BE_STALE"

_MIX_RE = re.compile(r"\bmix(ing|ed)?\b|मिक्स|मिला(ऊं|उं|कर|ना|एं|यें)|मिसळ|मिश्रण|combine", re.I)


@dataclass
class GateContext:
    text: str
    lang: str
    intent: str
    intent_conf: float
    entities: dict
    concepts: List[str]
    is_voice: bool = False
    asr_conf: Optional[float] = None
    injection: bool = False
    evidence: List[Evidence] = field(default_factory=list)
    tool_ok: Optional[bool] = None          # None => this query is not a tool query
    tool_error: str = ""
    tool_stale: bool = False
    grounding: Optional[float] = None
    needs_crop: bool = False
    needs_location: bool = False


@dataclass
class GateResult:
    action: str                              # answer | ask_repeat | ask_clarify | refuse
    reason_codes: List[str]
    confidence: float
    signals: Dict[str, float] = field(default_factory=dict)
    message_key: str = ""                    # key into MESSAGES when the action is not "answer"
    disclaimers: List[str] = field(default_factory=list)   # keys into DISCLAIMERS
    handoff: Dict[str, object] = field(default_factory=dict)
    echo_transcript: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ---- user-facing messages (hi / mr / en) -----------------------------------------------------------------------------------
K = config.KCC_NUMBER
MESSAGES: Dict[str, Dict[str, str]] = {
    "ASK_REPEAT": {
        "hi": "माफ़ कीजिए, आपकी आवाज़ साफ़ सुनाई नहीं दी। कृपया अपना सवाल धीरे और साफ़ आवाज़ में दोबारा भेजें।",
        "mr": "क्षमा करा, तुमचा आवाज स्पष्ट ऐकू आला नाही. कृपया तुमचा प्रश्न हळू आणि स्पष्ट आवाजात पुन्हा पाठवा.",
        "en": "Sorry, I could not hear you clearly. Please send your question again, slowly and clearly."},
    "CLARIFY_CROP": {
        "hi": "कृपया बताइए, यह किस फसल के बारे में है? जैसे कपास, सोयाबीन, प्याज या टमाटर।",
        "mr": "कृपया सांगा, हे कोणत्या पिकाबद्दल आहे? उदा. कापूस, सोयाबीन, कांदा किंवा टोमॅटो.",
        "en": "Please tell me which crop this is about, for example cotton, soybean, onion or tomato."},
    "CLARIFY_LOCATION": {
        "hi": "कृपया अपना जिला या शहर बताइए, ताकि मैं सही जानकारी दे सकूं।",
        "mr": "कृपया तुमचा जिल्हा किंवा शहर सांगा, म्हणजे मी अचूक माहिती देऊ शकेन.",
        "en": "Please tell me your district or town so that I can give the right information."},
    "CLARIFY_GENERIC": {
        "hi": "कृपया अपना सवाल थोड़ा विस्तार से बताइए, जैसे फसल का नाम और समस्या क्या है।",
        "mr": "कृपया तुमचा प्रश्न थोडा सविस्तर सांगा, उदा. पिकाचे नाव आणि समस्या काय आहे.",
        "en": "Please describe your question in a little more detail, such as the crop name and the problem."},
    "REFUSE_LOW_EVIDENCE": {
        "hi": f"मुझे इस सवाल का भरोसेमंद जवाब नहीं मिला, इसलिए मैं अंदाज़ा नहीं लगाऊंगा। कृपया किसान कॉल सेंटर {K} पर कॉल करें या अपने नजदीकी कृषि विज्ञान केंद्र (KVK) से संपर्क करें।",
        "mr": f"मला या प्रश्नाचे विश्वासार्ह उत्तर मिळाले नाही, म्हणून मी अंदाज लावणार नाही. कृपया किसान कॉल सेंटर {K} वर कॉल करा किंवा जवळच्या कृषी विज्ञान केंद्राशी (KVK) संपर्क साधा.",
        "en": f"I could not find a reliable answer to this, so I will not guess. Please call the Kisan Call Centre on {K} or contact your nearest Krishi Vigyan Kendra (KVK)."},
    "REFUSE_NO_DOSE": {
        "hi": f"कीटनाशक या खाद की सही मात्रा फसल, रोग और लेबल पर निर्भर करती है, और मेरे पास इसका मान्य स्रोत नहीं है। गलत मात्रा से फसल और सेहत को नुकसान हो सकता है। कृपया किसान कॉल सेंटर {K} या अपने नजदीकी KVK से सलाह लें।",
        "mr": f"कीटकनाशक किंवा खताचे अचूक प्रमाण पीक, रोग आणि लेबलवर अवलंबून असते, आणि माझ्याकडे त्याचा मान्यताप्राप्त स्रोत नाही. चुकीच्या प्रमाणामुळे पिकाचे आणि आरोग्याचे नुकसान होऊ शकते. कृपया किसान कॉल सेंटर {K} किंवा जवळच्या KVK चा सल्ला घ्या.",
        "en": f"The right pesticide or fertiliser dose depends on the crop, the problem and the product label, and I do not have an approved source for it. A wrong dose can harm the crop and your health. Please ask the Kisan Call Centre ({K}) or your nearest KVK."},
    "REFUSE_OVERDOSE": {
        "hi": f"दवा की मात्रा बढ़ाना सुरक्षित नहीं है। इससे फसल जल सकती है, मित्र कीट मर सकते हैं और सेहत को नुकसान हो सकता है। हमेशा लेबल पर लिखी मात्रा ही रखें। सलाह के लिए किसान कॉल सेंटर {K} या KVK से संपर्क करें।",
        "mr": f"औषधाचे प्रमाण वाढवणे सुरक्षित नाही. त्यामुळे पीक जळू शकते, मित्र कीटक मरू शकतात आणि आरोग्याला हानी होऊ शकते. नेहमी लेबलवर दिलेलेच प्रमाण वापरा. सल्ल्यासाठी किसान कॉल सेंटर {K} किंवा KVK शी संपर्क साधा.",
        "en": f"Increasing the dose is not safe. It can burn the crop, kill beneficial insects and harm your health. Always use only the dose written on the label. For advice call the Kisan Call Centre ({K}) or your KVK."},
    "REFUSE_UNAPPROVED_CHEM": {
        "hi": f"इस रसायन के बारे में मेरे पास मान्य स्रोत नहीं है, इसलिए मैं इसकी मात्रा या उपयोग नहीं बता सकता। कृपया किसान कॉल सेंटर {K} या नजदीकी KVK से पूछें।",
        "mr": f"या रसायनाबद्दल माझ्याकडे मान्यताप्राप्त स्रोत नाही, म्हणून मी त्याचे प्रमाण किंवा वापर सांगू शकत नाही. कृपया किसान कॉल सेंटर {K} किंवा जवळच्या KVK ला विचारा.",
        "en": f"I do not have an approved source for this chemical, so I cannot tell its dose or use. Please ask the Kisan Call Centre ({K}) or your nearest KVK."},
    "REFUSE_MIXING": {
        "hi": f"अलग-अलग दवाइयों को मिलाना खतरनाक हो सकता है। विशेषज्ञ की सलाह के बिना न मिलाएं। किसान कॉल सेंटर {K} या KVK से पूछें।",
        "mr": f"वेगवेगळी औषधे मिसळणे धोकादायक असू शकते. तज्ज्ञांच्या सल्ल्याशिवाय मिसळू नका. किसान कॉल सेंटर {K} किंवा KVK ला विचारा.",
        "en": f"Mixing different pesticides can be dangerous. Do not mix them without expert advice. Ask the Kisan Call Centre ({K}) or your KVK."},
    "REFUSE_SCOPE": {
        "hi": "मैं केवल खेती, फसल, मंडी भाव, मौसम और सरकारी योजनाओं के सवालों में मदद कर सकता हूं। इस विषय पर मैं जवाब नहीं दे सकता।",
        "mr": "मी फक्त शेती, पीक, मंडी भाव, हवामान आणि सरकारी योजनांच्या प्रश्नांमध्ये मदत करू शकतो. या विषयावर मी उत्तर देऊ शकत नाही.",
        "en": "I can only help with farming, crops, mandi prices, weather and government schemes. I cannot answer on this topic."},
    "REFUSE_UNSAFE": {
        "hi": "मैं इस अनुरोध में मदद नहीं कर सकता। मैं केवल खेती से जुड़े सुरक्षित सवालों के जवाब दे सकता हूं।",
        "mr": "मी या विनंतीमध्ये मदत करू शकत नाही. मी फक्त शेतीशी संबंधित सुरक्षित प्रश्नांना उत्तर देऊ शकतो.",
        "en": "I cannot help with this request. I can only answer safe, farming-related questions."},
    "REFUSE_SPECULATIVE": {
        "hi": f"भविष्य के भाव या मुनाफे की गारंटी कोई नहीं दे सकता, इसलिए मैं अंदाज़ा नहीं लगाऊंगा। चाहें तो मैं आज का मंडी भाव बता सकता हूं। सलाह के लिए किसान कॉल सेंटर {K} पर संपर्क करें।",
        "mr": f"भविष्यातील भाव किंवा नफ्याची हमी कोणीही देऊ शकत नाही, म्हणून मी अंदाज लावणार नाही. हवे असल्यास मी आजचा मंडी भाव सांगू शकतो. सल्ल्यासाठी किसान कॉल सेंटर {K} शी संपर्क साधा.",
        "en": f"Nobody can guarantee future prices or profit, so I will not guess. I can tell you today's mandi price if you like. For advice contact the Kisan Call Centre ({K})."},
    "REFUSE_TOOL_DOWN": {
        "hi": f"अभी लाइव जानकारी नहीं मिल पा रही है। कृपया थोड़ी देर बाद कोशिश करें या किसान कॉल सेंटर {K} पर संपर्क करें।",
        "mr": f"सध्या थेट माहिती मिळत नाही. कृपया थोड्या वेळाने प्रयत्न करा किंवा किसान कॉल सेंटर {K} शी संपर्क साधा.",
        "en": f"Live data is not available right now. Please try again later or contact the Kisan Call Centre ({K})."},
    "REFUSE_NO_DATA": {
        "hi": f"इस फसल या मंडी के लिए मेरे पास आज का भाव उपलब्ध नहीं है। कृपया स्थानीय मंडी या किसान कॉल सेंटर {K} से पूछें।",
        "mr": f"या पिकासाठी किंवा मंडीसाठी माझ्याकडे आजचा भाव उपलब्ध नाही. कृपया स्थानिक मंडी किंवा किसान कॉल सेंटर {K} ला विचारा.",
        "en": f"I do not have today's price for this crop or market. Please ask the local mandi or the Kisan Call Centre ({K})."},
}

DISCLAIMERS: Dict[str, Dict[str, str]] = {
    # short spoken version of the chemical safety note (the full CHEMICAL text is shown in the written reply)
    "CHEMICAL_SHORT": {"hi": "सुरक्षा: लेबल पढ़ें, दस्ताने और मास्क पहनें, KVK से पुष्टि करें।",
                       "mr": "सुरक्षितता: लेबल वाचा, हातमोजे आणि मास्क वापरा, KVK कडून खात्री करा.",
                       "en": "Safety: read the label, wear gloves and a mask, confirm with your KVK."},
    "GENERAL": {"hi": "यह जानकारी केवल मार्गदर्शन के लिए है, प्रमाणित कृषि विशेषज्ञ की सलाह का विकल्प नहीं।",
                "mr": "ही माहिती फक्त मार्गदर्शनासाठी आहे, प्रमाणित कृषी तज्ज्ञांच्या सल्ल्याला पर्याय नाही.",
                "en": "This information is for guidance only and is not a substitute for a certified agronomist."},
    "CHEMICAL": {"hi": "सुरक्षा: दवा लगाते समय लेबल पढ़ें, दस्ताने और मास्क पहनें, बच्चों और पशुओं को दूर रखें और उपयोग से पहले KVK से पुष्टि करें।",
                 "mr": "सुरक्षितता: औषध वापरताना लेबल वाचा, हातमोजे आणि मास्क वापरा, मुले आणि जनावरे दूर ठेवा आणि वापरापूर्वी KVK कडून खात्री करा.",
                 "en": "Safety: read the label, wear gloves and a mask, keep children and animals away, and confirm with your KVK before use."},
}

_REFUSAL_KEYS = {INJECTION: "REFUSE_UNSAFE", UNSAFE: "REFUSE_UNSAFE", OUT_OF_SCOPE: "REFUSE_SCOPE", SPECULATIVE: "REFUSE_SPECULATIVE",
                 OVERDOSE: "REFUSE_OVERDOSE", MIXING: "REFUSE_MIXING", UNAPPROVED_CHEM: "REFUSE_UNAPPROVED_CHEM", NO_DOSE: "REFUSE_NO_DOSE"}


def message_for(key: str, lang: str) -> str:
    return MESSAGES[key].get(lang) or MESSAGES[key]["en"]


def disclaimer_for(key: str, lang: str) -> str:
    return DISCLAIMERS[key].get(lang) or DISCLAIMERS[key]["en"]


def _handoff(kvk: bool = True) -> dict:
    return {"kisan_call_centre": config.KCC_NUMBER, "kvk": kvk, "text": f"Kisan Call Centre {config.KCC_NUMBER} / local KVK"}


# ---- key entities & evidence coverage ---------------------------------------------------------------------------------------
def key_entities(concepts: Sequence[str]) -> List[str]:
    out = []
    for c in concepts:
        cat = G.concept_category(c)
        if cat in ("crop", "pest", "chemical", "chemical_other") or c.startswith("scheme_") or c == "helpline":
            out.append(c)
    return out


def evidence_coverage(concepts: Sequence[str], evidence: Sequence[Evidence], k: int = 3) -> Dict[str, float]:
    """Coverage of the query's weighted concepts by the union of the top-k evidence chunks (same document family),
    capped when a named crop / pest / chemical / scheme is absent from the evidence."""
    union: set = set()
    for e in evidence[:k]:
        union |= set(e.chunk.concepts)
    cov = coverage_of(concepts, frozenset(union))
    missing = [c for c in key_entities(concepts) if c not in union and c != "helpline"]
    if missing:
        cov = min(cov, 0.4)
    return {"coverage": cov, "missing_entities": missing}


def dose_query(concepts: Sequence[str]) -> bool:
    """A dosage question = a dose word or "how much?" together with a pesticide / named chemical / fertiliser."""
    cs = set(concepts)
    cats = {G.concept_category(c) for c in cs}
    asks_amount = bool(cs & {"dose", "qty_q"})
    return asks_amount and bool(cats & {"chemical", "chemical_other"} or "fertilizer" in cs)


# ---- the gate ---------------------------------------------------------------------------------------------------------------
class ConfidenceGate:
    def __init__(self, thresholds=T):
        self.t = thresholds

    # phase 1: cheap guardrails before any retrieval
    def pre_check(self, ctx: GateContext) -> Optional[GateResult]:
        cs = set(ctx.concepts)
        sig = {"asr": ctx.asr_conf if ctx.asr_conf is not None else 1.0}

        def refuse(code: str, action: str = "refuse", key: Optional[str] = None) -> GateResult:
            return GateResult(action, [code], 0.0, sig, key or _REFUSAL_KEYS.get(code, "REFUSE_LOW_EVIDENCE"), [], _handoff())

        if ctx.injection:
            return refuse(INJECTION)
        if "unsafe" in cs:
            return refuse(UNSAFE)
        if ctx.is_voice and ctx.asr_conf is not None and ctx.asr_conf < self.t.asr_min:
            return GateResult("ask_repeat", [LOW_ASR], round(ctx.asr_conf, 3), sig, "ASK_REPEAT", [], {})
        if not ctx.text.strip():
            return GateResult("ask_repeat", [NO_SPEECH], 0.0, sig, "ASK_REPEAT", [], {})
        if "off_topic" in cs and not (cs & {"crop_cotton", "crop_soybean", "crop_wheat", "crop_tomato", "crop_onion", "crop_rice",
                                              "crop_maize", "crop_sugarcane"}):
            return refuse(OUT_OF_SCOPE)
        if "speculative" in cs:
            return refuse(SPECULATIVE)
        # ---- pesticide / dosage safety rules
        if "over_dose" in cs:
            return refuse(OVERDOSE)
        chem_named = [c for c in cs if G.concept_category(c) in ("chemical", "chemical_other")]
        if _MIX_RE.search(ctx.text) and (chem_named or "chem_pesticide" in cs):
            return refuse(MIXING)
        if "chem_other" in cs:
            return refuse(UNAPPROVED_CHEM)
        if ctx.intent == "unknown" and not ctx.concepts:
            return GateResult("ask_clarify", [UNCLEAR], 0.0, sig, "CLARIFY_GENERIC", [], {})
        if ctx.needs_crop:
            return GateResult("ask_clarify", [MISSING_CROP], 0.0, sig, "CLARIFY_CROP", [], {})
        if ctx.needs_location:
            return GateResult("ask_clarify", [MISSING_LOCATION], 0.0, sig, "CLARIFY_LOCATION", [], {})
        return None

    # phase 2: after retrieval / tool call / answer generation
    def decide(self, ctx: GateContext) -> GateResult:
        t = self.t
        asr = ctx.asr_conf if (ctx.is_voice and ctx.asr_conf is not None) else 1.0
        reasons: List[str] = []
        disclaimers = ["GENERAL"]

        # ---------------- live-tool queries
        if ctx.tool_ok is not None:
            sig = {"asr": round(asr, 3), "retrieval": 1.0 if ctx.tool_ok else 0.0, "coverage": 1.0 if ctx.tool_ok else 0.0,
                   "intent": round(ctx.intent_conf, 3), "grounding": 1.0}
            if not ctx.tool_ok:
                code = TOOL_NO_DATA if ctx.tool_error in ("NO_DATA", "UNKNOWN_COMMODITY", "UNKNOWN_LOCATION") else TOOL_DOWN
                return GateResult("refuse", [code], 0.0, sig, "REFUSE_NO_DATA" if code == TOOL_NO_DATA else "REFUSE_TOOL_DOWN",
                                  [], _handoff())
            overall = self._overall(sig)
            if overall < t.overall_min:
                return GateResult("refuse", [LOW_OVERALL], overall, sig, "REFUSE_LOW_EVIDENCE", [], _handoff())
            reasons.append(OK)
            if ctx.tool_stale:
                reasons.append(STALE)
            if ctx.is_voice and asr < 0.75:
                reasons.append(ASR_UNCERTAIN)
            return GateResult("answer", reasons, overall, sig, "", disclaimers, {}, echo_transcript=(ctx.is_voice and asr < 0.75))

        # ---------------- knowledge-base queries
        ev = ctx.evidence
        # grounding is unknown until the answer is generated: assume 1.0 for the early look, real value on the second call
        sig = {"asr": round(asr, 3), "retrieval": round(ev[0].score if ev else 0.0, 3), "coverage": 0.0,
               "intent": round(ctx.intent_conf, 3), "grounding": round(ctx.grounding if ctx.grounding is not None else 1.0, 3)}
        if not ev:
            return GateResult("refuse", [NO_EVIDENCE], 0.0, sig, "REFUSE_LOW_EVIDENCE", [], _handoff())
        cov = evidence_coverage(ctx.concepts, ev)
        sig["coverage"] = round(cov["coverage"], 3)

        # dosage rule: a dose question needs an APPROVED dose-bearing chunk that covers crop and chemical
        if dose_query(ctx.concepts):
            chem_named = [c for c in ctx.concepts if G.concept_category(c) in ("chemical", "chemical_other")]
            crop = ctx.entities.get("crop")
            ok_chunks = [e for e in ev[:3] if e.chunk.has_dose
                         and (not crop or e.chunk.crop in (crop, "all"))
                         and all(c in e.chunk.concepts for c in chem_named)]
            if not ok_chunks:
                return GateResult("refuse", [NO_DOSE], 0.0, sig, "REFUSE_NO_DOSE", [], _handoff())
        if ev[0].score < t.retrieval_min:
            return GateResult("refuse", [LOW_RETRIEVAL], self._overall(sig), sig, "REFUSE_LOW_EVIDENCE", [], _handoff())
        if cov["coverage"] < t.coverage_min:
            code = MISSING_ENTITY if cov["missing_entities"] else LOW_COVERAGE
            return GateResult("refuse", [code], self._overall(sig), sig, "REFUSE_LOW_EVIDENCE", [], _handoff())
        overall = self._overall(sig)
        if overall < t.overall_min:
            return GateResult("refuse", [LOW_OVERALL], overall, sig, "REFUSE_LOW_EVIDENCE", [], _handoff())
        if ctx.grounding is not None and ctx.grounding < t.grounding_min:
            return GateResult("refuse", [UNGROUNDED], overall, sig, "REFUSE_LOW_EVIDENCE", [], _handoff())
        reasons.append(OK)
        if any(e.chunk.safety_level == "dose" or e.chunk.has_dose for e in ev[:3]) or "chem_pesticide" in ctx.concepts \
                or any(G.concept_category(c) in ("chemical", "chemical_other") for c in ctx.concepts):
            disclaimers.append("CHEMICAL")
            reasons.append(CHEM_NOTE)
        if ctx.is_voice and asr < 0.75:
            reasons.append(ASR_UNCERTAIN)
        return GateResult("answer", reasons, overall, sig, "", disclaimers, {}, echo_transcript=(ctx.is_voice and asr < 0.75))

    def _overall(self, s: Dict[str, float]) -> float:
        t = self.t
        v = (t.w_asr * s["asr"] + t.w_retrieval * s["retrieval"] + t.w_coverage * s["coverage"]
             + t.w_intent * s["intent"] + t.w_grounding * s["grounding"])
        return round(max(0.0, min(1.0, v)), 3)
