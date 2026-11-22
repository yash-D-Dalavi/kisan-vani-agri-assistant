"""
handlers.py - framework-agnostic HTTP layer.

All endpoint logic lives here as plain functions `handler(Req) -> (status, payload, content_type)`.  app.py (FastAPI) and
lite_server.py (zero-dependency stdlib server) both mount the same ROUTES table, so behaviour is identical and testable
without any web framework.

Roles (Module 14): set AGRI_TOKENS="admin:tok1,reviewer:tok2,analyst:tok3" to require an X-Admin-Token header on the
admin endpoints (admin >= reviewer >= analyst). When AGRI_TOKENS is unset the demo is open.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import audio_service as A
import config
import demo_presets
import glossary as G
from assistant_pipeline import AgriAssistant
from knowledge_base import KnowledgeBase
from seed_data import GLOSSARY_TERMS
from whatsapp_channel import WhatsAppGateway, MediaStore, verify_challenge, verify_signature

VERSION = "1.0.0"
MAX_UPLOAD = 5 * 1024 * 1024
UI_FILE = config.BASE_DIR / "ui_demo.html"
Resp = Tuple[int, Any, str]


@dataclass
class Req:
    method: str = "GET"
    path: str = "/"
    query: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    path_params: Dict[str, str] = field(default_factory=dict)

    def header(self, name: str, default: str = "") -> str:
        low = name.lower()
        for k, v in self.headers.items():
            if k.lower() == low:
                return v
        return default

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            v = json.loads(self.body.decode("utf-8"))
            return v if isinstance(v, dict) else {}
        except (ValueError, UnicodeDecodeError):
            raise BadRequest("invalid JSON body")

    def form(self) -> Tuple[Dict[str, str], Dict[str, Tuple[str, str, bytes]]]:
        """Parse multipart/form-data with the stdlib email parser -> (fields, files{name: (filename, content_type, bytes)})."""
        ctype = self.header("content-type")
        if "multipart/form-data" not in ctype:
            raise BadRequest("expected multipart/form-data")
        msg = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + self.body)
        fields: Dict[str, str] = {}
        files: Dict[str, Tuple[str, str, bytes]] = {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            payload = part.get_payload(decode=True) or b""
            fn = part.get_filename()
            if fn is not None:
                files[name] = (fn, part.get_content_type(), payload)
            else:
                fields[name] = payload.decode("utf-8", "replace")
        return fields, files


class BadRequest(Exception):
    pass


# ----------------------------------------------------------------------------------------------------------------------------
# services (one shared set; tests can swap it)
# ----------------------------------------------------------------------------------------------------------------------------
class Services:
    def __init__(self, assistant: Optional[AgriAssistant] = None):
        self.assistant = assistant or AgriAssistant(generator=os.getenv("AGRI_LLM", "extractive"), translator=os.getenv("AGRI_MT", "none"))
        self.gateway = WhatsAppGateway(self.assistant, MediaStore())
        self.last_eval: Optional[dict] = None
        self._preset_cache: Dict[Tuple[str, str], bytes] = {}
        self._lock = threading.Lock()
        self.started = time.time()

    def preset_audio(self, pid: str, fmt: str) -> Optional[bytes]:
        p = next((x for x in demo_presets.PRESETS if x["id"] == pid), None)
        if not p:
            return None
        with self._lock:
            if (pid, fmt) not in self._preset_cache:
                self._preset_cache[(pid, fmt)] = A.make_demo_voice_note(p["transcript"], p["lang"], p["conf"], p["noise"], fmt)
            return self._preset_cache[(pid, fmt)]


_SERVICES: Optional[Services] = None
_SLOCK = threading.Lock()


def get_services() -> Services:
    global _SERVICES
    with _SLOCK:
        if _SERVICES is None:
            _SERVICES = Services()
        return _SERVICES


def set_services(s: Optional[Services]) -> None:
    global _SERVICES
    _SERVICES = s


# ----------------------------------------------------------------------------------------------------------------------------
# role-based access
# ----------------------------------------------------------------------------------------------------------------------------
_RANK = {"analyst": 1, "reviewer": 2, "admin": 3}


def _tokens() -> Dict[str, str]:
    out = {}
    for item in os.getenv("AGRI_TOKENS", "").split(","):
        if ":" in item:
            role, tok = item.split(":", 1)
            out[tok.strip()] = role.strip()
    return out


def authorize(req: Req, role: Optional[str]) -> Optional[Resp]:
    toks = _tokens()
    if not role or not toks:
        return None
    have = toks.get(req.header("x-admin-token"))
    if not have:
        return 401, {"error": "missing or invalid X-Admin-Token"}, "application/json"
    if _RANK[have] < _RANK[role]:
        return 403, {"error": f"role '{have}' cannot access a '{role}' endpoint"}, "application/json"
    return None


# ----------------------------------------------------------------------------------------------------------------------------
# handlers
# ----------------------------------------------------------------------------------------------------------------------------
def _json(payload: Any, status: int = 200) -> Resp:
    return status, payload, "application/json"


def ui(req: Req) -> Resp:
    if UI_FILE.exists():
        return 200, UI_FILE.read_bytes(), "text/html; charset=utf-8"
    return 404, {"error": "ui_demo.html not found"}, "application/json"


def health(req: Req) -> Resp:
    s = get_services()
    a = s.assistant
    return _json({"status": "ok", "version": VERSION, "uptime_s": round(time.time() - s.started, 1), "kb": a.kb.stats(),
                  "backends": {"asr": config.ASR_BACKEND, "tts": config.TTS_BACKEND, "embedder": a.kb.embedder.name, "reranker": a.kb.reranker.name,
                               "generator": a.generator, "translator": a.translator,
                               "live_tools": "live" if config.LIVE_MODE else "demo-feed"},
                  "capabilities": {"ffmpeg": bool(shutil.which("ffmpeg")), "languages": config.SUPPORTED_LANGUAGES},
                  "targets": config.TARGETS, "sessions": a.sessions.count()})


def query_text(req: Req) -> Resp:
    b = req.json()
    text = str(b.get("text", "")).strip()
    if not text:
        raise BadRequest("'text' is required")
    if len(text) > 1000:
        raise BadRequest("'text' too long (max 1000 chars)")
    s = get_services()
    ac = b.get("asr_conf")
    resp = s.assistant.handle_text(text, user_id=b.get("user_id"), lang_hint=b.get("lang"), location=b.get("location"),
                                   asr_conf=float(ac) if ac is not None else None, channel=b.get("channel", "web"),
                                   want_tts=bool(b.get("tts", True)))
    return _json(resp)


def query_voice(req: Req) -> Resp:
    s = get_services()
    ctype = req.header("content-type")
    fields: Dict[str, str] = {}
    if "multipart/form-data" in ctype:
        fields, files = req.form()
        f = files.get("audio") or files.get("file")
        if not f:
            raise BadRequest("multipart field 'audio' (file) is required")
        filename, mime, data = f
    elif ctype.startswith("audio/") or ctype == "application/octet-stream":
        filename, mime, data = req.query.get("filename", ""), ctype, req.body
        fields = dict(req.query)
    else:
        raise BadRequest("send multipart/form-data with an 'audio' file, or a raw audio/* body")
    if len(data) > MAX_UPLOAD:
        return _json({"error": "audio too large (max 5 MB)"}, 413)
    hc = fields.get("asr_hint_conf")
    resp = s.assistant.handle_voice(data, mime=mime, filename=filename, user_id=fields.get("user_id"), lang_hint=fields.get("lang") or None,
                                    location=fields.get("location") or None, asr_hint=fields.get("asr_hint") or None,
                                    asr_hint_conf=float(hc) if hc else None, channel=fields.get("channel", "web"), want_tts=fields.get("tts", "1") != "0")
    return _json(resp)


def admin_documents(req: Req) -> Resp:
    kb = get_services().assistant.kb
    return _json({"stats": kb.stats(), "documents": kb.list_documents(with_text=req.query.get("with_text") == "1"),
                  "glossary": GLOSSARY_TERMS})


def admin_add_document(req: Req) -> Resp:
    b = req.json()
    kb = get_services().assistant.kb
    res = kb.add_document(b, approve=False)         # new content is ALWAYS unapproved until a reviewer approves it
    return _json({"result": res.to_dict(), "stats": kb.stats()}, 201 if res.status == "added" else 422)


def admin_approve(req: Req) -> Resp:
    b = req.json()
    doc_id = b.get("doc_id")
    if not doc_id or "approved" not in b:
        raise BadRequest("'doc_id' and 'approved' are required")
    kb = get_services().assistant.kb
    if not kb.set_approval(doc_id, bool(b["approved"])):
        return _json({"error": f"unknown doc_id {doc_id}"}, 404)
    return _json({"doc_id": doc_id, "approved": bool(b["approved"]), "stats": kb.stats()})


def admin_search(req: Req) -> Resp:
    """Retrieval debugger: run hybrid search + rerank for any query and show scores."""
    b = req.json()
    q = str(b.get("query", "")).strip()
    if not q:
        raise BadRequest("'query' is required")
    kb = get_services().assistant.kb
    ents = G.extract_entities(q)
    lang, _ = G.detect_language(q, b.get("lang"))
    ev = kb.search(q, lang=lang, crop=ents.get("crop"), region=config.DEFAULT_STATE)
    return _json({"query": q, "language": lang, "crop_filter": ents.get("crop"), "concepts": ents["concepts"],
                  "results": [{**e.to_dict(), "title": e.chunk.title, "text": e.chunk.text, "source": e.chunk.source} for e in ev]})


def metrics(req: Req) -> Resp:
    obs = get_services().assistant.observer
    if req.query.get("format") == "prom":
        return 200, obs.prometheus().encode(), "text/plain; version=0.0.4"
    return _json(obs.snapshot())


def feedback(req: Req) -> Resp:
    b = req.json()
    ok = get_services().assistant.observer.add_feedback(str(b.get("request_id", "")), str(b.get("rating", "")), str(b.get("comment", "")))
    return _json({"ok": ok}, 200 if ok else 422)


def admin_review(req: Req) -> Resp:
    return _json({"queue": get_services().assistant.observer.review_queue(int(req.query.get("limit", 50)))})


def admin_review_promote(req: Req) -> Resp:
    b = req.json()
    item = get_services().assistant.observer.promote_to_eval(str(b.get("request_id", "")), str(b.get("expected_action", "refuse")), str(b.get("note", "")))
    return _json({"added": item}, 200 if item else 404)


def demo_presets_list(req: Req) -> Resp:
    return _json({"voice": [{k: v for k, v in p.items() if k != "noise"} for p in demo_presets.PRESETS], "text": demo_presets.TEXT_EXAMPLES,
                  "ogg_available": bool(shutil.which("ffmpeg"))})


def demo_voice(req: Req) -> Resp:
    fmt = req.query.get("fmt", "ogg")
    if fmt not in ("ogg", "wav"):
        raise BadRequest("fmt must be ogg or wav")
    data = get_services().preset_audio(req.query.get("id", ""), fmt)
    if data is None:
        return _json({"error": "unknown preset id"}, 404)
    return 200, data, "audio/ogg" if data[:4] == b"OggS" else "audio/wav"


def eval_run(req: Req) -> Resp:
    import evaluator
    s = get_services()
    report = evaluator.run_evaluation(s.assistant, include_voice=req.query.get("voice", "1") != "0", write_reports=True)
    s.last_eval = report
    return _json(report)


def eval_last(req: Req) -> Resp:
    s = get_services()
    if s.last_eval is None:
        p = config.REPORT_DIR / "eval_report.json"
        if p.exists():
            return _json(json.loads(p.read_text(encoding="utf-8")))
        return _json({"error": "no evaluation has been run yet - POST /eval/run"}, 404)
    return _json(s.last_eval)


_AUDIO_NAME = re.compile(r"^[A-Za-z0-9_\-]+\.(wav|mp3|ogg)$")


def audio_file(req: Req) -> Resp:
    name = req.path_params.get("name", "")
    if not _AUDIO_NAME.match(name):
        return _json({"error": "invalid file name"}, 400)
    p = config.AUDIO_DIR / name
    if not p.exists():
        return _json({"error": "not found (audio is deleted after one hour - data minimisation)"}, 404)
    return 200, p.read_bytes(), {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg"}[name.rsplit(".", 1)[1]]


def wa_verify(req: Req) -> Resp:
    code, text = verify_challenge(req.query)
    return code, text.encode(), "text/plain"


def wa_webhook(req: Req) -> Resp:
    gw = get_services().gateway
    if not verify_signature(gw.app_secret, req.body, req.header("x-hub-signature-256")):
        return _json({"error": "bad signature"}, 403)
    return _json(gw.process_webhook(req.json()))     # always 200 quickly so Meta does not retry


def wa_simulate(req: Req) -> Resp:
    gw = get_services().gateway
    ctype = req.header("content-type")
    if "multipart/form-data" in ctype:
        fields, files = req.form()
        f = files.get("audio")
        audio = f[2] if f else None
        out = gw.simulate(fields.get("from", "919999900001"), text=fields.get("text"), audio=audio, mime=f[1] if f else "audio/ogg")
    else:
        b = req.json()
        audio = None
        if b.get("preset"):
            audio = get_services().preset_audio(b["preset"], "ogg")
            if audio is None:
                return _json({"error": "unknown preset"}, 404)
        out = gw.simulate(b.get("from", "919999900001"), text=b.get("text"), audio=audio)
    return _json(out)


def wa_outbox(req: Req) -> Resp:
    gw = get_services().gateway
    to = req.query.get("to")
    box = [m for m in gw.outbox if not to or m["to"] == to]
    return _json({"outbox": box[-50:], "counters": gw.counters, "statuses": gw.status_counts, "live": gw.live,
                  "require_consent": __import__("whatsapp_channel").REQUIRE_CONSENT})


def wa_reset(req: Req) -> Resp:
    b = req.json()
    get_services().gateway.reset(b.get("from"))
    return _json({"ok": True})


# ----------------------------------------------------------------------------------------------------------------------------
# route table (single source of truth for FastAPI and the stdlib server)
# ----------------------------------------------------------------------------------------------------------------------------
ROUTES: List[Dict[str, Any]] = [
    {"method": "GET", "path": "/", "fn": ui, "role": None, "summary": "Web workbench (alias of /ui)"},
    {"method": "GET", "path": "/ui", "fn": ui, "role": None, "summary": "Farmer + evaluator/admin web workbench"},
    {"method": "GET", "path": "/health", "fn": health, "role": None, "summary": "Health, versions, backends, targets"},
    {"method": "POST", "path": "/query/text", "fn": query_text, "role": None, "summary": "Text query (JSON: text, lang?, location?, user_id?, asr_conf?, tts?)"},
    {"method": "POST", "path": "/query/voice", "fn": query_voice, "role": None, "summary": "Voice-note query (multipart 'audio' or raw audio/* body)"},
    {"method": "GET", "path": "/admin/documents", "fn": admin_documents, "role": "analyst", "summary": "List documents + metadata + glossary"},
    {"method": "POST", "path": "/admin/documents", "fn": admin_add_document, "role": "admin", "summary": "Ingest a document (dedupe + versioning, unapproved)"},
    {"method": "POST", "path": "/admin/approve", "fn": admin_approve, "role": "admin", "summary": "Approve / un-approve a document"},
    {"method": "POST", "path": "/admin/search", "fn": admin_search, "role": "analyst", "summary": "Retrieval debugger (hybrid + rerank scores)"},
    {"method": "GET", "path": "/admin/review", "fn": admin_review, "role": "reviewer", "summary": "Low-confidence / refused conversations for expert review"},
    {"method": "POST", "path": "/admin/review/promote", "fn": admin_review_promote, "role": "reviewer", "summary": "Add a reviewed conversation to the eval set"},
    {"method": "GET", "path": "/metrics", "fn": metrics, "role": "analyst", "summary": "Observability metrics (?format=prom for Prometheus)"},
    {"method": "POST", "path": "/feedback", "fn": feedback, "role": None, "summary": "Thumbs up/down on a reply"},
    {"method": "GET", "path": "/demo/presets", "fn": demo_presets_list, "role": None, "summary": "Hindi/Marathi demo voice-note presets"},
    {"method": "GET", "path": "/demo/voice", "fn": demo_voice, "role": None, "summary": "Demo voice note audio (?id=&fmt=ogg|wav)"},
    {"method": "POST", "path": "/eval/run", "fn": eval_run, "role": "analyst", "summary": "Run the evaluation harness and return the report"},
    {"method": "GET", "path": "/eval/last", "fn": eval_last, "role": "analyst", "summary": "Last evaluation report"},
    {"method": "GET", "path": "/audio/{name}", "fn": audio_file, "role": None, "summary": "Generated reply audio (auto-deleted after 1 h)"},
    {"method": "GET", "path": "/webhook/whatsapp", "fn": wa_verify, "role": None, "summary": "WhatsApp webhook verification"},
    {"method": "POST", "path": "/webhook/whatsapp", "fn": wa_webhook, "role": None, "summary": "WhatsApp webhook receiver (signature-checked)"},
    {"method": "POST", "path": "/simulate/whatsapp", "fn": wa_simulate, "role": None, "summary": "Simulate an incoming WhatsApp text / voice note"},
    {"method": "GET", "path": "/simulate/whatsapp/outbox", "fn": wa_outbox, "role": None, "summary": "Simulated WhatsApp outbox"},
    {"method": "POST", "path": "/simulate/whatsapp/reset", "fn": wa_reset, "role": None, "summary": "Reset simulated chat / session (consent demo)"},
]


def match_route(method: str, path: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, str], bool]:
    """Return (route, path_params, path_exists_with_other_method)."""
    path_hit = False
    for r in ROUTES:
        pat = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", r["path"]) + "$"
        m = re.match(pat, path)
        if m:
            path_hit = True
            if r["method"] == method:
                return r, m.groupdict(), True
    return None, {}, path_hit


def dispatch(req: Req) -> Resp:
    """Run a request through routing, RBAC and error handling. Never raises."""
    route, params, path_hit = match_route(req.method, req.path)
    if route is None:
        return (405 if path_hit else 404), {"error": "method not allowed" if path_hit else "not found"}, "application/json"
    req.path_params = params
    denied = authorize(req, route["role"])
    if denied:
        return denied
    try:
        return route["fn"](req)
    except BadRequest as e:
        return 400, {"error": str(e)}, "application/json"
    except (ValueError, TypeError) as e:
        return 400, {"error": f"bad request: {e}"}, "application/json"
    except Exception as e:  # noqa: BLE001 - the API must never leak a traceback to a farmer
        return 500, {"error": "internal error", "detail": type(e).__name__}, "application/json"
