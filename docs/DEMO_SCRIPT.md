# 5-minute demo and viva script

**Setup (before the professor arrives):** `python app.py` → open `http://127.0.0.1:8000/ui`. Have the README Mermaid diagram open in another tab. Run `python evaluator.py` once so the numbers are fresh.

| Time | Do this | Say this |
|---|---|---|
| 0:00 | Show the README architecture diagram | "A farmer sends a voice note. We check audio quality, transcribe, detect Hindi/Marathi, then a two-phase confidence gate decides: answer from approved documents, call a live tool, or refuse and hand off to Kisan Call Centre." |
| 0:30 | **Ask** tab → click **MR कापसाची पाने पिवळी** (Marathi voice note) | "This is a Marathi voice note. We see the transcript with ASR confidence, intent and crop, the answer in Marathi, the **source citation** (KVK note, date, version) and the five confidence bars. The dark tick is each minimum." Click **Read aloud**. |
| 1:15 | Click **HI कपास में गुलाबी सुंडी**; then type a Hindi question in the box | "Same pipeline in Hindi, and text works too. Retrieval is cross-lingual, so Hindi and Marathi documents share concept ids." |
| 1:45 | Click **HI लासलगांव प्याज भाव** and **HI पुणे मौसम** | "These are **live tools**: price min/modal/max and a 3-day forecast. Notice **data as of 21-11-2026**, the source, and the DEMO FEED badge. With `AGRI_LIVE=1` it calls Agmarknet and Open-Meteo; if they fail we say so, we never invent numbers." |
| 2:30 | Click **HI टमाटर मैंकोज़ेब मात्रा** | "Dose answers need an approved chunk covering both crop and chemical, and always carry a safety note." |
| 2:50 | Click **HI कपास मोनोक्रोटोफॉस मात्रा (REFUSE)**, then **HI शोर वाली आवाज़**, then **MR ऊस रोग** | "Three safe failures: chemical not in approved sources, bad audio so we ask to repeat, and sugarcane isn't in our knowledge base so we refuse instead of answering from soybean. Each shows reason codes and the KCC 1800-180-1551 / KVK hand-off." |
| 3:30 | **Documents (admin)** tab → Load → untick/tick a document; add a document (stays unapproved) | "Nothing reaches farmers until an expert approves it. Versions supersede older ones, duplicates are rejected, and old documents get a review-due flag." Ask the related question to show the effect. |
| 4:15 | **Evaluation** tab → Run | "74 questions, 5 categories: Recall@5 100%, faithfulness 100%, safe-refusal 100%, plus 17 voice notes end to end. Latency is 4.1 s p95 projected with real models. I'm upfront that the set is self-authored and the WER fixture is illustrative." |
| 4:45 | **WhatsApp simulator** (optional if time) | "Webhook signature check, de-duplication, consent and 'delete my data' are implemented; live Meta calls are untested." |
| 5:00 | Show `python -m unittest discover -s tests` output | "94 automated tests." |

## Likely viva questions

1. **Why not just use an LLM?** Hallucinated doses are dangerous. We use extractive answers by default, verify every claim against the cited text, and allow an LLM only through a plug-in that must pass the same verification.
2. **How do you handle Hindi vs Marathi?** Surface words map to language-independent concept ids, so one retrieval index serves both; answers come back in the farmer's language.
3. **How is "confidence" computed?** Weighted sum of five signals (ASR 0.20, retrieval 0.35, coverage 0.30, intent 0.05, grounding 0.10) against per-signal minimums and overall 0.55. Safety rules are hard rules on top.
4. **What if the mandi API is down?** Retry with exponential backoff, then a stale-cache answer labelled stale, otherwise an honest "data unavailable".
5. **What is simulated?** ASR (reads a tag in the demo audio, or the browser's speech hint), TTS audio when gTTS is absent, and the demo mandi/weather feed. Hooks for Whisper, Bhashini, gTTS and live APIs exist; Whisper/Bhashini/live WhatsApp are untested here.
6. **How do you know it works?** Evaluation harness plus unit tests; limitation: self-written test set.
7. **Privacy?** Consent, phone masking, hashed user ids, delete command, audio auto-deleted after 1 h.
8. **How would it scale?** Swap the in-process index for pgvector/Qdrant, run Whisper on GPU, add a queue for voice notes; the handlers are framework-agnostic.
