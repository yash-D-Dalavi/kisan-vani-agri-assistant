"""
assistant_pipeline.py - orchestrates the whole blueprint flow (section 3.1):

  voice/text -> [audio normalise -> ASR] -> language id -> intent + entities -> pre-gate guardrails
      -> route: knowledge base (hybrid retrieval + rerank)  |  live tool (mandi / weather)
      -> grounded answer -> claim verification -> confidence gate -> translation/glossary -> safety notes -> TTS
      -> structured trace + metrics

Every step is timed (measured_ms) and the response carries a projected production latency (hosted ASR/LLM/TTS budget)
so the <15 s voice-in/voice-out target can be argued, not just asserted.
"""
from __future__ import annotations

import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import audio_service as A
import config
import glossary as G
import grounding as GR
import live_tools as LT
import translation as TR
from confidence_gate import (ConfidenceGate, GateContext, GateResult, disclaimer_for, message_for, dose_query,
                             AUDIO_FAIL, NO_SPEECH, MISSING_CROP, NO_EVIDENCE, LOW_RETRIEVAL, LOW_COVERAGE, LOW_OVERALL, MISSING_ENTITY,
                             STALE, CHEM_NOTE)
from knowledge_base import KnowledgeBase, split_sentences
from observability import Observer, estimate_cost_inr, hash_user, mask_pii
from sessions import SessionStore

T = config.T
KB_INTENTS = {"pest_disease", "crop_advice", "scheme", "helpline"}


# ----------------------------------------------------------------------------------------------------------------------
# Module 3: language / intent / entities
# ----------------------------------------------------------------------------------------------------------------------
def classify_intent(concepts: List[str]) -> Tuple[str, float]:
    cats = Counter(G.concept_category(c) for c in concepts)
    cs = set(concepts)
    specific = {k: v for k, v in cats.items() if k not in ("generic", "off_topic", "speculative", "unsafe")}
    if not specific and not cs & {"crop_generic", "pest_generic"}:
        return "unknown", 0.2
    if cats["scheme"]:
        return "scheme", min(0.95, 0.6 + 0.15 * cats["scheme"])
    if cs <= {"helpline"} or (cats["helpline"] and len(specific) == 1):
        return "helpline", 0.85
    if cs & {"price"} or ("mandi" in cs):
        return "mandi_price", min(0.95, 0.55 + 0.15 * (cats["market"] + cats["crop"]))
    if (cats["weather"]) and not (cats["pest"] or cats["chemical"] or cats["practice"] or cats["dose"]):
        return "weather", min(0.95, 0.6 + 0.15 * cats["weather"])
    if cats["pest"] or cats["chemical"] or cats["chemical_other"] or cats["dose"] or cats["symptom"] or "pest_generic" in cs:
        return "pest_disease", min(0.95, 0.5 + 0.12 * (cats["pest"] + cats["chemical"] + cats["symptom"] + cats["crop"]))
    if cats["practice"] or cats["crop"] or cats["season"] or cs & {"crop_generic"}:
        return "crop_advice", min(0.9, 0.45 + 0.12 * (cats["practice"] + cats["crop"]))
    return "unknown", 0.3


def is_code_mixed(text: str) -> bool:
    toks = G.tokenize(text)
    dev = any(G._DEV_RE.search(t) for t in toks)
    latin_concepts = [t for t in toks if re.fullmatch(r"[a-z]+", t) and t not in G.STOPWORDS and G.extract_concepts(t)]
    return dev and bool(latin_concepts)


def state_of(location: Optional[str]) -> str:
    return "Delhi" if location == "Delhi" else config.DEFAULT_STATE


# ----------------------------------------------------------------------------------------------------------------------
# tracing
# ----------------------------------------------------------------------------------------------------------------------
class Trace:
    def __init__(self):
        self.t0 = time.perf_counter()
        self.steps: List[dict] = []

    def step(self, name: str, fn, **info):
        t = time.perf_counter()
        out = fn()
        self.steps.append({"name": name, "ms": round((time.perf_counter() - t) * 1000, 2), **info})
        return out

    def note(self, name: str, ms: float = 0.0, **info):
        self.steps.append({"name": name, "ms": round(ms, 2), **info})

    def total_ms(self) -> float:
        return round((time.perf_counter() - self.t0) * 1000, 2)


def projected_latency(input_type: str, route: str, answered: bool, want_tts: bool, channel: str) -> Dict[str, object]:
    b = config.PROD_LATENCY_BUDGET_S
    keys = []
    if input_type == "voice":
        keys += ["audio_download_and_normalize", "asr"]
    keys += ["intent_and_entities", "retrieval_and_rerank"]
    if route == "kb" and answered:
        keys.append("llm_generation")
    if want_tts:
        keys.append("tts")
    if channel == "whatsapp":
        keys.append("upload_and_delivery")
    total = round(sum(b[k] for k in keys), 1)
    return {"total_s": total, "breakdown": {k: b[k] for k in keys}}


# ----------------------------------------------------------------------------------------------------------------------
# the assistant
# ----------------------------------------------------------------------------------------------------------------------
class AgriAssistant:
    def __init__(self, kb: Optional[KnowledgeBase] = None, mandi: Optional[LT.MandiTool] = None, weather: Optional[LT.WeatherTool] = None,
                 gate: Optional[ConfidenceGate] = None, sessions: Optional[SessionStore] = None, observer: Optional[Observer] = None,
                 generator: str = "extractive", translator: str = "none"):
        self.kb = kb or KnowledgeBase()
        self.mandi = mandi or LT.MandiTool()
        self.weather = weather or LT.WeatherTool()
        self.gate = gate or ConfidenceGate()
        self.sessions = sessions or SessionStore()
        self.observer = observer or Observer()
        self.generator = generator
        self.translator = translator
        self._n = 0

    # ------------------------------------------------------------------ public entry points
    def handle_text(self, text: str, *, user_id: Optional[str] = None, lang_hint: Optional[str] = None, location: Optional[str] = None,
                    asr_conf: Optional[float] = None, channel: str = "web", want_tts: bool = True) -> dict:
        """Text query. `asr_conf` lets tests/evaluation simulate a voice query with a given ASR confidence."""
        tr = Trace()
        asr = A.ASRResult(text, lang_hint, asr_conf, "simulated-input") if asr_conf is not None else None
        return self._run(text, tr, channel=channel, user_id=user_id, lang_hint=lang_hint, location=location, asr=asr, audio=None,
                         input_type="voice" if asr_conf is not None else "text", want_tts=want_tts)

    def handle_voice(self, audio_bytes: bytes, *, mime: str = "", filename: str = "", user_id: Optional[str] = None,
                     lang_hint: Optional[str] = None, location: Optional[str] = None, asr_hint: Optional[str] = None,
                     asr_hint_conf: Optional[float] = None, channel: str = "web", want_tts: bool = True) -> dict:
        tr = Trace()
        try:
            info = tr.step("audio_normalize", lambda: A.normalize_audio(audio_bytes, mime, filename), input_bytes=len(audio_bytes))
        except A.AudioDecodeError as e:
            return self._audio_failure(str(e), tr, channel, user_id, lang_hint, want_tts)
        tr.steps[-1].update(method=info.method, duration_s=info.duration_s, quality=info.quality, warnings=info.warnings)
        asr = tr.step("asr", lambda: A.transcribe(audio_bytes, info, lang_hint, asr_hint=asr_hint, asr_hint_conf=asr_hint_conf))
        tr.steps[-1].update(backend=asr.backend, confidence=asr.confidence)
        return self._run(asr.text, tr, channel=channel, user_id=user_id, lang_hint=lang_hint or asr.language, location=location,
                         asr=asr, audio=info, input_type="voice", want_tts=want_tts)

    # ------------------------------------------------------------------ core
    def _run(self, text: str, tr: Trace, *, channel: str, user_id: Optional[str], lang_hint: Optional[str], location: Optional[str],
             asr: Optional[A.ASRResult], audio: Optional[A.AudioInfo], input_type: str, want_tts: bool) -> dict:
        self._n += 1
        if self._n % 50 == 0:
            A.purge_old_audio(3600)
        sess = self.sessions.get(user_id)
        text = (text or "").strip()

        # --- language id + understanding
        lang, lang_conf = tr.step("language_id", lambda: G.detect_language(text, lang_hint or sess.get("lang")))
        reply_lang = lang if lang in config.SUPPORTED_LANGUAGES else (lang_hint or sess.get("lang") or config.DEFAULT_LANGUAGE)
        if lang_conf < 0.5 and (sess.get("lang") in ("hi", "mr")):
            reply_lang = sess["lang"]
        und = tr.step("understanding", lambda: self._understand(text))
        concepts, ents, (intent, intent_conf) = und["concepts"], und["entities"], und["intent"]
        loc = ents.get("location") or location or sess.get("location")
        tr.steps[-1].update(intent=intent, concepts=len(concepts))

        is_voice = input_type == "voice"
        asr_conf = asr.confidence if asr else None
        specific = [c for c in concepts if G.concept_category(c) in ("crop", "pest", "chemical", "chemical_other", "scheme", "practice", "helpline")]
        needs_crop = (intent == "mandi_price" and not ents.get("crops_all")) or (intent in ("pest_disease", "crop_advice") and not specific)
        needs_loc = intent == "weather" and not loc
        ctx = GateContext(text=text, lang=reply_lang, intent=intent, intent_conf=intent_conf, entities=ents, concepts=concepts,
                          is_voice=is_voice, asr_conf=asr_conf, injection=G.detect_injection(text), needs_crop=needs_crop, needs_location=needs_loc)

        tool_res: Optional[LT.ToolResult] = None
        evidence = []
        gen = None
        grounding_info = None
        route = "none"

        pre = tr.step("pre_gate_guardrails", lambda: self.gate.pre_check(ctx))
        decision: Optional[GateResult] = pre
        answer_lang = reply_lang
        translation_note = None
        claims_text: List[str] = []
        spoken = ""

        if decision is None and intent in ("mandi_price", "weather"):
            route = "tool"
            if intent == "mandi_price":
                commodity = ents["crops_all"][0]
                market = loc if loc in {"Lasalgaon", "Pune", "Nashik", "Solapur", "Latur", "Akola", "Nagpur", "Jalgaon", "Kolhapur"} else None
                tool_res = tr.step("tool_mandi", lambda: self.mandi.lookup(commodity, market, state_of(loc)), commodity=commodity)
            else:
                tool_res = tr.step("tool_weather", lambda: self.weather.forecast(loc), location=loc)
            ctx.tool_ok, ctx.tool_error, ctx.tool_stale = tool_res.ok, tool_res.error, tool_res.stale
            decision = tr.step("confidence_gate", lambda: self.gate.decide(ctx))
            if decision.action == "answer":
                spoken = (LT.render_mandi(tool_res, reply_lang, commodity) if intent == "mandi_price" else LT.render_weather(tool_res, reply_lang))
                claims_text = [spoken]
        elif decision is None:
            route = "kb"
            crop = ents.get("crop")
            evidence = tr.step("retrieval_hybrid_rerank", lambda: self.kb.search(text, lang=reply_lang, crop=crop, region=state_of(loc), concepts=concepts))
            tr.steps[-1].update(candidates=len(evidence), top_score=round(evidence[0].score, 3) if evidence else 0.0)
            ctx.evidence = evidence
            pre_dec = self.gate.decide(ctx)           # early look (grounding not known yet): cheap refusals skip generation
            if pre_dec.action != "answer":
                decision = pre_dec
                tr.note("confidence_gate", reasons=pre_dec.reason_codes)
            else:
                gen = tr.step("answer_generation", lambda: GR.generate_answer(text, concepts, evidence, reply_lang, self.generator), generator=self.generator)
                ev_texts = {e.chunk.chunk_id: e.chunk.text for e in gen.sources}
                grounding_info = tr.step("grounding_verification", lambda: GR.verify_claims(gen.sentences, ev_texts))
                if grounding_info["score"] < T.grounding_min and self.generator != "extractive":   # LLM drifted -> safe fallback
                    gen = GR.extractive_generate(concepts, evidence, reply_lang)
                    ev_texts = {e.chunk.chunk_id: e.chunk.text for e in gen.sources}
                    grounding_info = GR.verify_claims(gen.sentences, ev_texts)
                    tr.note("generator_fallback_to_extractive")
                ctx.grounding = grounding_info["score"]
                decision = tr.step("confidence_gate", lambda: self.gate.decide(ctx))

        # --- refusal -> clarification when the farmer simply did not say which crop
        if (decision and decision.action == "refuse" and route == "kb" and not ents.get("crop")
                and set(decision.reason_codes) & {NO_EVIDENCE, LOW_RETRIEVAL, LOW_COVERAGE, LOW_OVERALL, MISSING_ENTITY}
                and (set(concepts) & {"crop_generic", "pest_generic", "sym_yellow", "sym_leaf", "sym_spots", "sym_wilt"})
                and not ents.get("chemicals")):
            decision = GateResult("ask_clarify", [MISSING_CROP], decision.confidence, decision.signals, "CLARIFY_CROP", [], {})

        # --- assemble the reply text
        citations: List[dict] = []
        disclaimers: List[str] = []
        if decision.action == "answer":
            if route == "kb" and gen:
                top_lang = gen.sources[0].chunk.language
                body = " ".join(gen.sentences)
                if top_lang != reply_lang:
                    _, res, translation_note = TR.ensure_language(body, top_lang, reply_lang, self.translator)
                    if res and res.translated:
                        gen.sentences, answer_lang = split_sentences(res.text), reply_lang
                    else:
                        answer_lang = top_lang
                else:
                    answer_lang = reply_lang
                notes = []
                if "CHEMICAL" in decision.disclaimers:
                    notes.append(disclaimer_for("CHEMICAL_SHORT", answer_lang))
                if translation_note:
                    notes.append(translation_note)
                echo = asr.text if decision.echo_transcript and asr else None
                scores = gen.scores if answer_lang == top_lang else None
                comp = tr.step("compose_answer", lambda: GR.compose_spoken(gen.sentences, gen.sources, answer_lang, echo, notes, scores=scores))
                spoken, claims_text = comp["spoken_text"], comp["claims"]
                citations = [{"n": i, **e.chunk.cite(), "score": round(e.score, 3)} for i, e in enumerate(gen.sources, 1)]
            else:
                answer_lang = reply_lang
                if decision.echo_transcript and asr:
                    spoken = GR.REPEAT_LINE.get(reply_lang, GR.REPEAT_LINE["en"]).format(t=asr.text) + " " + spoken
                citations = [{"n": 1, "type": "live-tool", "tool": tool_res.tool, "source": tool_res.source, "mode": tool_res.mode,
                              "as_of": tool_res.as_of, "as_of_display": LT.fmt_date(tool_res.as_of), "from_cache": tool_res.from_cache}]
            disclaimers = [disclaimer_for(k, answer_lang) for k in decision.disclaimers]
        else:
            spoken = message_for(decision.message_key, reply_lang)
            answer_lang = reply_lang
            disclaimers = []

        # --- TTS
        tts = None
        if want_tts and spoken:
            tts = tr.step("tts", lambda: A.synthesize(spoken, answer_lang if answer_lang in config.SUPPORTED_LANGUAGES else "hi")).to_dict()
            tts["audio_url"] = f"/audio/{tts['filename']}" if tts.get("filename") else ""
        speech_s = A.estimate_speech_seconds(spoken)

        # --- session memory
        self.sessions.bump(user_id)
        if user_id:
            upd = {}
            if lang in ("hi", "mr") and lang_conf >= 0.6:
                upd["lang"] = lang
            if ents.get("location"):
                upd["location"] = ents["location"]
            if upd:
                self.sessions.update(user_id, **upd)

        proj = projected_latency(input_type, route, decision.action == "answer", want_tts, channel)
        measured = tr.total_ms()
        resp = {
            "request_id": uuid.uuid4().hex, "channel": channel, "timestamp": datetime.now(LT.IST).isoformat(timespec="seconds"),
            "user_hash": hash_user(user_id),
            "input": {"type": input_type, "transcript": text, "asr": asr.to_dict() if asr else None, "audio": audio.to_dict() if audio else None},
            "language": {"detected": lang, "confidence": round(lang_conf, 2), "reply": answer_lang, "code_mixed": und["code_mixed"]},
            "understanding": {"intent": intent, "intent_confidence": round(intent_conf, 2), "entities": {
                "crop": ents.get("crop"), "crop_implied": ents.get("crop_implied"), "location": ents.get("location") or loc,
                "season": ents.get("season"), "chemicals": ents.get("chemicals"), "concepts": concepts},
                "normalized_query": und["normalized"], "route": route},
            "decision": {"action": decision.action, "reason_codes": decision.reason_codes, "confidence": decision.confidence,
                         "signals": decision.signals, "handoff": decision.handoff, "echo_transcript": decision.echo_transcript,
                         "thresholds": {"asr_min": T.asr_min, "retrieval_min": T.retrieval_min, "coverage_min": T.coverage_min,
                                        "grounding_min": T.grounding_min, "overall_min": T.overall_min}},
            "answer": {"text": spoken, "spoken_text": spoken, "claims": claims_text, "language": answer_lang, "citations": citations,
                       "disclaimers": disclaimers, "translation_note": translation_note,
                       "grounding": {"score": grounding_info["score"], "claims": [c.to_dict() for c in grounding_info["claims"]]} if grounding_info else None},
            "tool": tool_res.to_dict() if tool_res else None,
            "retrieval": {"top": [e.to_dict() for e in evidence], "embedder": self.kb.embedder.name, "reranker": self.kb.reranker.name} if route == "kb" else None,
            "tts": tts,
            "latency": {"measured_ms": measured, "steps": tr.steps, "projected_prod_s": proj["total_s"], "projected_breakdown": proj["breakdown"],
                        "within_voice_target": proj["total_s"] < config.TARGETS["latency_voice_s"],
                        "spoken_reply_s_est": speech_s, "within_spoken_target": speech_s <= config.TARGETS["spoken_reply_s"]},
        }
        resp["cost_inr"] = estimate_cost_inr(audio.duration_s if audio else 0.0, len(spoken), channel)
        self.observer.record(resp)
        return resp

    # ------------------------------------------------------------------ helpers
    def _understand(self, text: str) -> dict:
        concepts = G.extract_concepts(text)
        ents = G.extract_entities(text)
        return {"concepts": concepts, "entities": ents, "intent": classify_intent(concepts),
                "normalized": " ".join(G.content_tokens(text)), "code_mixed": is_code_mixed(text)}

    def _audio_failure(self, err: str, tr: Trace, channel: str, user_id: Optional[str], lang_hint: Optional[str], want_tts: bool) -> dict:
        lang = lang_hint if lang_hint in config.SUPPORTED_LANGUAGES else config.DEFAULT_LANGUAGE
        msg = message_for("ASK_REPEAT", lang)
        tts = None
        if want_tts:
            tts = A.synthesize(msg, lang).to_dict()
            tts["audio_url"] = f"/audio/{tts['filename']}" if tts.get("filename") else ""
        resp = {
            "request_id": uuid.uuid4().hex, "channel": channel, "timestamp": datetime.now(LT.IST).isoformat(timespec="seconds"),
            "user_hash": hash_user(user_id),
            "input": {"type": "voice", "transcript": "", "asr": None, "audio": {"error": err}},
            "language": {"detected": lang, "confidence": 0.0, "reply": lang, "code_mixed": False},
            "understanding": {"intent": "unknown", "intent_confidence": 0.0, "entities": {}, "normalized_query": "", "route": "none"},
            "decision": {"action": "ask_repeat", "reason_codes": [AUDIO_FAIL], "confidence": 0.0, "signals": {}, "handoff": {}, "echo_transcript": False,
                         "thresholds": {}},
            "answer": {"text": msg, "spoken_text": msg, "claims": [], "language": lang, "citations": [], "disclaimers": [], "translation_note": None, "grounding": None},
            "tool": None, "retrieval": None, "tts": tts,
            "latency": {"measured_ms": tr.total_ms(), "steps": tr.steps, "projected_prod_s": 1.0, "projected_breakdown": {}, "within_voice_target": True,
                        "spoken_reply_s_est": A.estimate_speech_seconds(msg), "within_spoken_target": True},
            "cost_inr": 0.0,
        }
        self.observer.record(resp)
        return resp
