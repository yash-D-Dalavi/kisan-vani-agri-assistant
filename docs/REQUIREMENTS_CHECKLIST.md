# IA Activity-2 requirements checklist

Tick each row after verifying. "Where" shows the file or UI element to show the examiner.

| # | Requirement | Where | Verified by |
|---|---|---|---|
| 1 | Voice-first input (WhatsApp-style voice note and web mic) | `audio_service.py`, `whatsapp_channel.py`, UI Ask/Record | `tests/test_audio.py`, `tests/test_whatsapp.py` |
| 2 | Hindi and Marathi support (and English) | `glossary.py`, `seed_data.py`, presets | `tests/test_glossary.py` |
| 3 | Audio normalisation + quality checks (noise, silence, clipping) | `audio_service.normalize_audio` | `tests/test_audio.py` |
| 4 | ASR with confidence and pluggable backends | `audio_service.transcribe`, registry | `test_registered_backend_and_crash_isolation` |
| 5 | Cross-lingual hybrid retrieval (dense + BM25 + rerank) | `knowledge_base.py` | `tests/test_knowledge_base.py` |
| 6 | Curated corpus with approval, versioning, dedupe, metadata | `knowledge_base.add_document`, UI Documents tab | `test_ingest_validation_duplicates_and_approval` |
| 7 | Grounded answers with citations | `grounding.py`, result card "Sources" | `tests/test_gate_and_pipeline.py` |
| 8 | Claim verification / hallucination control | `grounding.verify_claims` | evaluator: hallucination-detection 100% |
| 9 | Confidence gate with refusal, ask-repeat, clarify | `confidence_gate.py` | `tests/test_gate_and_pipeline.py` |
| 10 | Kisan Call Centre (1800-180-1551) / KVK fallback | `confidence_gate.MESSAGES` | evaluator: refusal hand-off rate 100% |
| 11 | Chemical/dosage safety rules and note | `confidence_gate.dose_query` | evaluator: chemical_dosage category |
| 12 | Live mandi tool with cache, rate limit, retry | `live_tools.MandiTool` | `tests/test_live_tools.py` |
| 13 | Live weather tool + advisories | `live_tools.WeatherTool` | `tests/test_live_tools.py` |
| 14 | "Data as of" timestamp in every tool answer | `live_tools.render_*` | `test_answers_contain_data_as_of_in_each_language` |
| 15 | TTS with ≤ 30 s spoken reply, OGG/Opus | `audio_service.synthesize`, `grounding.compose_spoken` | `test_tts_placeholder_duration_and_compression` |
| 16 | FastAPI endpoints /health, /query/text, /query/voice, /admin/documents, /admin/approve, /metrics, /ui | `app.py`, `handlers.py` | `tests/test_handlers.py`, `tests/test_app_routes.py` |
| 17 | Single-file UI: voice presets, citations, confidence badge, mandi/weather cards, admin approval, evaluation panel | `ui_demo.html` | browser smoke test; Demo script |
| 18 | Admin approval workflow and review queue | `/admin/*`, UI | `test_admin_approval_changes_retrieval` |
| 19 | Evaluation: Recall@5, MRR, faithfulness, safe refusal, WER/CER, chrF/BLEU, latency | `evaluator.py`, `eval_metrics.py` | `tests/test_evaluator.py` |
| 20 | Observability: traces, masking, p95, cost, feedback | `observability.py` | `test_metrics_feedback_review_promote` |
| 21 | Privacy: consent, deletion, retention | `sessions.py`, `whatsapp_channel.py` | `test_consent_flow_and_data_deletion` |
| 22 | Prompt-injection and off-topic guard | `glossary.detect_injection`, gate phase 1 | evaluator: adversarial category |
| 23 | Mermaid architecture diagram | README §1 | visual |
| 24 | Model/tech trade-off table | README §2 | visual |
| 25 | Risks and mitigations | README §8 | visual |
| 26 | Future scope | README §9 | visual |
| 27 | Run commands for Windows and Ubuntu/WSL | README §7 | run once on your laptop |
| 28 | 5-minute demo and viva script | `docs/DEMO_SCRIPT.md` | rehearse |
| 29 | Honest limitations stated | README note, §6 | read aloud |

**Before submission:** run `python -m unittest discover -s tests` (all OK), `python evaluator.py` (all PASS), open `/ui` and click through each tab, and read `docs/DATA_SOURCE_REGISTER.md` so you can explain the corpus is demo text.
