"""
config.py - central configuration, thresholds and targets.

Everything tunable lives here so the evaluator and the confidence gate use the
same numbers.  Targets mirror Section 2 ("Success Criteria") of the blueprint.
All values can be overridden with environment variables (AGRI_*).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
REPORT_DIR = BASE_DIR / "reports"
AUDIO_DIR = BASE_DIR / "data" / "audio"
for _d in (DATA_DIR, LOG_DIR, REPORT_DIR, AUDIO_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SUPPORTED_LANGUAGES = {"hi": "Hindi", "mr": "Marathi", "en": "English"}
DEFAULT_LANGUAGE = "hi"
DEFAULT_STATE = "Maharashtra"

# Human fallbacks (Kisan Call Centre number is the national toll-free line)
KCC_NUMBER = "1800-180-1551"

# Feature switches ---------------------------------------------------------
ASR_BACKEND = os.getenv("AGRI_ASR_BACKEND", "simulated")      # simulated | whisper | <registered name>
TTS_BACKEND = os.getenv("AGRI_TTS_BACKEND", "auto")           # auto | gtts | edge | simulated
EMBEDDER = os.getenv("AGRI_EMBEDDER", "hashing")              # hashing | bge-m3
RERANKER = os.getenv("AGRI_RERANKER", "lexical")              # lexical | cross-encoder
LIVE_MODE = os.getenv("AGRI_LIVE", "0") == "1"                # 1 => try real Agmarknet / weather APIs first
DATA_GOV_API_KEY = os.getenv("DATA_GOV_API_KEY", "")          # data.gov.in key for Agmarknet
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "agri-demo-verify")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")


@dataclass(frozen=True)
class Thresholds:
    # --- confidence gate ---
    asr_min: float = 0.60              # below -> ask the farmer to repeat
    retrieval_min: float = 0.35        # top reranker score (0..1)
    coverage_min: float = 0.60         # share of query concepts found in evidence
    grounding_min: float = 0.90        # share of answer claims supported
    overall_min: float = 0.55          # weighted score
    intent_min: float = 0.40
    # weights of the overall score (sum = 1)
    w_asr: float = 0.20
    w_retrieval: float = 0.35
    w_coverage: float = 0.30
    w_intent: float = 0.05
    w_grounding: float = 0.10
    # --- retrieval ---
    top_k: int = 5
    candidate_k: int = 25
    rrf_k: int = 60
    chunk_chars: int = 420
    chunk_overlap_sentences: int = 1
    # --- answer ---
    max_answer_sentences: int = 3
    max_spoken_seconds: int = 30
    speech_chars_per_second: float = 13.0  # rough speaking rate for duration estimate
    # --- live tools ---
    mandi_ttl_s: int = 30 * 60
    weather_ttl_s: int = 60 * 60
    live_retries: int = 3
    live_backoff_s: float = 0.5
    rate_capacity: int = 5
    rate_refill_per_s: float = 1.0


T = Thresholds()

# Targets from the blueprint (Section 2)
TARGETS = {
    "recall_at_5": 0.85,
    "faithfulness": 0.90,
    "refusal_accuracy": 0.90,
    "latency_voice_s": 15.0,
    "spoken_reply_s": 30.0,
}

# Production latency budget (seconds) used for the *projected* end-to-end figure.
# These are planning estimates for hosted models, not measurements.
PROD_LATENCY_BUDGET_S = {
    "audio_download_and_normalize": 0.6,
    "asr": 2.5,
    "intent_and_entities": 0.3,
    "retrieval_and_rerank": 0.8,
    "llm_generation": 3.0,
    "tts": 2.5,
    "upload_and_delivery": 1.0,
}

# Prompt used when a real LLM is plugged in (Module 9: answer only from evidence)
GROUNDED_PROMPT = (
    "You are a careful agricultural assistant for Indian farmers.\n"
    "Answer ONLY from the EVIDENCE below. Use short, simple, spoken-style sentences "
    "in {language}. Name the source. If the evidence does not contain the answer, say you "
    "are not sure and refer the farmer to the Kisan Call Centre ({kcc}) or the local KVK. "
    "Never invent doses, prices or scheme rules.\n\nEVIDENCE:\n{evidence}\n\nQUESTION: {question}\n"
)
