# Deployment notes

**Local:** see README §7. **Docker:** `docker build -t kisan-vani . && docker run -p 8000:8000 -e AGRI_TOKENS="admin:CHANGE_ME" kisan-vani`.

**Production checklist (not done in this prototype)**
- Put the app behind HTTPS (nginx / cloud load balancer); set `AGRI_TOKENS` and `WHATSAPP_APP_SECRET`.
- Replace `data/kb_state.json` with Postgres + pgvector; keep approval and version columns.
- Run Whisper on a GPU worker and queue voice notes (Redis / SQS); keep WhatsApp replies under 30 s.
- Set `AGRI_LIVE=1` with a data.gov.in API key; monitor `/metrics?format=prom` (fallback rate, p95, `stale` share).
- Register the webhook with Meta (verify token, app secret) and test the live send path, which is untested here.
- Content review: have KVK/ICAR experts replace the demo documents before farmers use it.
- Retention: logs hold masked data only; audio is deleted after 1 hour.
