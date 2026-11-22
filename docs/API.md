# API reference

Base URL `http://127.0.0.1:8000`. Interactive docs at `/docs` (FastAPI). Roles apply only when `AGRI_TOKENS` is set; send `X-Admin-Token`.

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/health` | none | status, KB counts, backends, targets |
| GET | `/ui` | none | web workbench |
| POST | `/query/text` | none | JSON `{text, lang?, location?, user_id?, tts?}` |
| POST | `/query/voice` | none | multipart `audio` (+ `lang`, `tts`, `asr_hint`, `asr_hint_conf`) or raw `audio/*` body |
| GET / POST | `/admin/documents` | analyst / admin | list; ingest (stays unapproved) |
| POST | `/admin/approve` | admin | `{doc_id, approved}` |
| POST | `/admin/search` | analyst | retrieval debugger `{query}` |
| GET | `/admin/review` | reviewer | refused / low-confidence / thumbs-down queue |
| POST | `/admin/review/promote` | reviewer | add to eval set |
| GET | `/metrics` | analyst | JSON; `?format=prom` for Prometheus |
| POST | `/feedback` | none | `{request_id, rating: up|down, comment?}` |
| POST | `/eval/run` | analyst | `?voice=0|1` run the harness |
| GET | `/eval/last` | analyst | last report |
| GET | `/demo/presets`, `/demo/voice?id=&fmt=ogg|wav` | none | demo voice notes |
| GET | `/audio/{name}` | none | generated reply audio |
| GET / POST | `/webhook/whatsapp` | none | Meta verification / incoming messages (HMAC checked) |
| POST | `/simulate/whatsapp`, GET `/simulate/whatsapp/outbox`, POST `/simulate/whatsapp/reset` | none | local WhatsApp simulator |

**Query response fields:** `request_id`, `language`, `input` (transcript, ASR), `understanding` (intent, entities), `decision` (`action`, `reason_codes`, `confidence`, `signals`, `handoff`), `answer` (`text`, `spoken_text`, `citations`, `disclaimers`, `grounding`), `tool` (with `as_of`, `source`, `mode`, `stale`), `tts`, `latency`.
Errors return `{"error": "..."}` with 400 (bad input), 401/403 (auth), 404, 405, 422.
