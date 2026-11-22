"""
whatsapp_channel.py - Module 1 (Channel and Audio Interface) for the WhatsApp Business Cloud API.

  verify_challenge()   GET webhook verification (hub.mode / hub.verify_token / hub.challenge)
  verify_signature()   X-Hub-Signature-256 HMAC check of the POST body
  parse_webhook()      Cloud-API payload -> IncomingMessage list + delivery statuses
  DedupCache           duplicate-message protection (Meta retries webhooks)
  WhatsAppGateway      consent -> commands -> (download voice note) -> pipeline -> text + voice-note reply, with retries

Two modes (same code path):
  * SIMULATION (default, no WHATSAPP_TOKEN): media comes from an in-memory store and replies go to an `outbox` list that the
    web workbench shows as a phone screen.
  * LIVE (WHATSAPP_TOKEN + WHATSAPP_PHONE_NUMBER_ID set): downloads media from the Graph API and posts replies.
    The live HTTP calls follow the public Cloud API shape but could not be exercised against Meta from the offline sandbox -
    test them with your own sandbox number first.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import audio_service as A
import config
from assistant_pipeline import AgriAssistant

GRAPH = "https://graph.facebook.com/v20.0"
REQUIRE_CONSENT = os.getenv("AGRI_WA_CONSENT", "1") == "1"


# ---------------------------------------------------------------------------------------------------------------------------
def verify_challenge(params: Dict[str, str], verify_token: str = config.WHATSAPP_VERIFY_TOKEN) -> Tuple[int, str]:
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == verify_token:
        return 200, params.get("hub.challenge", "")
    return 403, "verification failed"


def verify_signature(app_secret: str, body: bytes, header: str) -> bool:
    if not app_secret:          # signature checking is only enforced when an app secret is configured
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


@dataclass
class IncomingMessage:
    message_id: str
    from_id: str
    type: str                  # text | audio | other
    text: str = ""
    media_id: str = ""
    mime: str = ""
    timestamp: str = ""


def parse_webhook(payload: dict) -> Tuple[List[IncomingMessage], List[dict]]:
    msgs: List[IncomingMessage] = []
    statuses: List[dict] = []
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            v = ch.get("value", {})
            for m in v.get("messages", []):
                t = m.get("type", "other")
                if t == "text":
                    msgs.append(IncomingMessage(m["id"], m["from"], "text", m.get("text", {}).get("body", ""), timestamp=m.get("timestamp", "")))
                elif t == "audio":
                    a = m.get("audio", {})
                    msgs.append(IncomingMessage(m["id"], m["from"], "audio", media_id=a.get("id", ""), mime=a.get("mime_type", ""),
                                                timestamp=m.get("timestamp", "")))
                else:
                    msgs.append(IncomingMessage(m.get("id", ""), m.get("from", ""), "other", timestamp=m.get("timestamp", "")))
            statuses += v.get("statuses", [])
    return msgs, statuses


class DedupCache:
    def __init__(self, ttl_s: int = 24 * 3600):
        self._d: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.ttl = ttl_s

    def seen(self, message_id: str) -> bool:
        now = time.time()
        with self._lock:
            for k in [k for k, ts in self._d.items() if now - ts > self.ttl]:
                del self._d[k]
            if message_id in self._d:
                return True
            self._d[message_id] = now
            return False


class MediaStore:
    """Simulation-mode media store: media_id -> (bytes, mime)."""

    def __init__(self):
        self._d: Dict[str, Tuple[bytes, str]] = {}

    def put(self, data: bytes, mime: str) -> str:
        mid = "sim-media-" + uuid.uuid4().hex[:10]
        self._d[mid] = (data, mime)
        return mid

    def get(self, mid: str) -> Optional[Tuple[bytes, str]]:
        return self._d.get(mid)


# ---------------------------------------------------------------------------------------------------------------------------
CONSENT_TEXT = ("नमस्ते! मैं किसान सहायक हूं। आपका सवाल और आवाज़ केवल जवाब देने और गुणवत्ता सुधारने के लिए उपयोग होगी; फोन नंबर सुरक्षित (हैश) रखा जाता है। "
                "जारी रखने के लिए \"हाँ\" लिखें। डेटा हटाने के लिए \"डेटा हटाओ\" लिखें।\n\n"
                "नमस्कार! मी किसान सहाय्यक आहे. तुमचा प्रश्न आणि आवाज फक्त उत्तर देण्यासाठी आणि गुणवत्ता सुधारण्यासाठी वापरला जाईल. "
                "सुरू ठेवण्यासाठी \"होय\" लिहा. डेटा हटवण्यासाठी \"डेटा हटवा\" लिहा.")
_YES = re.compile(r"^\s*(yes|y|ok|haan|han|हाँ|हां|हा|होय|ho)\s*[.!]*\s*$", re.I)
_DELETE = re.compile(r"(delete my data|डेटा हटाओ|डेटा हटा|मेरा डेटा हटा|माझा डेटा हटवा|डेटा हटवा)", re.I)
_LANG = re.compile(r"(?:language|भाषा)\s*[:\-]?\s*(hindi|marathi|हिंदी|हिन्दी|मराठी)", re.I)
MSG = {
    "consented": {"hi": "धन्यवाद! अब अपना सवाल लिखें या आवाज़ में भेजें।", "mr": "धन्यवाद! आता तुमचा प्रश्न लिहा किंवा आवाजात पाठवा."},
    "deleted": {"hi": "आपका डेटा हटा दिया गया है।", "mr": "तुमचा डेटा हटवला आहे."},
    "lang_set": {"hi": "ठीक है, अब मैं हिंदी में जवाब दूंगा।", "mr": "ठीक आहे, आता मी मराठीत उत्तर देईन."},
    "unsupported": {"hi": "कृपया टेक्स्ट या वॉइस नोट भेजें।", "mr": "कृपया मजकूर किंवा व्हॉइस नोट पाठवा."},
    "download_failed": {"hi": "आपकी आवाज़ डाउनलोड नहीं हो सकी। कृपया दोबारा भेजें।", "mr": "तुमचा आवाज डाउनलोड होऊ शकला नाही. कृपया पुन्हा पाठवा."},
}


def format_text_reply(resp: dict) -> str:
    """Text copy of the reply for WhatsApp: answer + sources (with dates) + safety note."""
    a, lines = resp["answer"], [resp["answer"]["text"]]
    cites = [c for c in a.get("citations", []) if c.get("doc_id")]
    if cites:
        lines.append("📚 " + "; ".join(f"[{c['n']}] {c['publisher']} ({c['doc_date']}, v{c['version']})" for c in cites[:3]))
    for c in a.get("citations", []):
        if c.get("type") == "live-tool":
            lines.append(f"🕒 Data as of {c['as_of_display']} ({c['mode']})")
    lines += [f"⚠️ {d}" for d in a.get("disclaimers", [])[-1:]]
    return "\n".join(lines)


class WhatsAppGateway:
    def __init__(self, assistant: AgriAssistant, media: Optional[MediaStore] = None, app_secret: str = os.getenv("WHATSAPP_APP_SECRET", ""),
                 post: Optional[Callable[[str, dict], dict]] = None, sleep: Callable[[float], None] = time.sleep):
        self.assistant = assistant
        self.media = media or MediaStore()
        self.dedup = DedupCache()
        self.app_secret = app_secret
        self.live = bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID)
        self.outbox: List[dict] = []
        self.status_counts: Dict[str, int] = {}
        self.counters = {"received": 0, "duplicates": 0, "send_retries": 0, "send_failures": 0}
        self._post = post or self._graph_post
        self._sleep = sleep

    # ------------------------------------------------------------------ webhook
    def process_webhook(self, payload: dict) -> dict:
        msgs, statuses = parse_webhook(payload)
        for s in statuses:
            st = s.get("status", "unknown")
            self.status_counts[st] = self.status_counts.get(st, 0) + 1
        processed, dups = 0, 0
        for m in msgs:
            self.counters["received"] += 1
            if m.message_id and self.dedup.seen(m.message_id):
                dups += 1
                self.counters["duplicates"] += 1
                continue
            self._handle(m)
            processed += 1
        return {"processed": processed, "duplicates": dups, "statuses": len(statuses)}

    # ------------------------------------------------------------------ one message
    def _handle(self, m: IncomingMessage) -> None:
        sess = self.assistant.sessions.get(m.from_id)
        lang = sess.get("lang") or "hi"
        if m.type == "text":
            t = m.text.strip()
            if _DELETE.search(t):
                self.assistant.sessions.delete(m.from_id)
                self._send_text(m.from_id, MSG["deleted"].get(lang, MSG["deleted"]["hi"]))
                return
            if REQUIRE_CONSENT and not sess.get("consent"):
                if _YES.match(t):
                    self.assistant.sessions.update(m.from_id, consent=True)
                    self._send_text(m.from_id, MSG["consented"].get(lang, MSG["consented"]["hi"]))
                else:
                    self._send_text(m.from_id, CONSENT_TEXT)
                return
            lm = _LANG.search(t)
            if lm:
                new = "mr" if lm.group(1).lower() in ("marathi", "मराठी") else "hi"
                self.assistant.sessions.update(m.from_id, lang=new)
                self._send_text(m.from_id, MSG["lang_set"][new])
                return
        elif m.type == "audio":
            if REQUIRE_CONSENT and not sess.get("consent"):
                self._send_text(m.from_id, CONSENT_TEXT)
                return
        else:
            self._send_text(m.from_id, MSG["unsupported"].get(lang, MSG["unsupported"]["hi"]))
            return

        if m.type == "text":
            resp = self.assistant.handle_text(m.text, user_id=m.from_id, channel="whatsapp", lang_hint=sess.get("lang"))
        else:
            media = self._download(m.media_id)
            if not media:
                self._send_text(m.from_id, MSG["download_failed"].get(lang, MSG["download_failed"]["hi"]))
                return
            data, mime = media
            resp = self.assistant.handle_voice(data, mime=m.mime or mime, user_id=m.from_id, channel="whatsapp", lang_hint=sess.get("lang"))
        self._send_text(m.from_id, format_text_reply(resp), resp=resp)
        tts = resp.get("tts")
        if tts and tts.get("filename"):
            self._send_voice(m.from_id, tts)

    # ------------------------------------------------------------------ media / sending
    def _download(self, media_id: str) -> Optional[Tuple[bytes, str]]:
        if not self.live:
            return self.media.get(media_id)
        try:  # LIVE: two-step download (media metadata -> binary URL), both with the bearer token
            hdr = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}
            meta = json.loads(urllib.request.urlopen(urllib.request.Request(f"{GRAPH}/{media_id}", headers=hdr), timeout=10).read())
            raw = urllib.request.urlopen(urllib.request.Request(meta["url"], headers=hdr), timeout=20).read()
            return raw, meta.get("mime_type", "audio/ogg")
        except Exception:  # noqa: BLE001
            return None

    def _graph_post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(f"{GRAPH}/{config.WHATSAPP_PHONE_NUMBER_ID}/{path}", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def _send(self, path: str, body: dict, retries: int = 3) -> bool:
        """POST with exponential backoff (webhook reliability / WhatsApp rate-limit handling)."""
        for i in range(retries):
            try:
                self._post(path, body)
                return True
            except Exception:  # noqa: BLE001
                self.counters["send_retries"] += 1
                if i < retries - 1:
                    self._sleep(0.5 * (2 ** i))
        self.counters["send_failures"] += 1
        return False

    def _send_text(self, to: str, body: str, resp: Optional[dict] = None) -> None:
        rec = {"id": uuid.uuid4().hex[:10], "type": "text", "to": to, "body": body, "ts": time.time(),
               "request_id": resp["request_id"] if resp else None}
        if self.live:
            rec["delivered"] = self._send("messages", {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}})
        self.outbox.append(rec)

    def _send_voice(self, to: str, tts: dict) -> None:
        path = config.AUDIO_DIR / tts["filename"]
        ogg = path.with_suffix(".ogg")
        rec = {"id": uuid.uuid4().hex[:10], "type": "audio", "to": to, "ts": time.time(), "url": f"/audio/{(ogg if ogg.exists() else path).name}",
               "mime": "audio/ogg; codecs=opus" if ogg.exists() else "audio/wav", "duration_s": tts.get("duration_s"),
               "bytes": ogg.stat().st_size if ogg.exists() else tts.get("bytes"), "synthetic_placeholder": tts.get("synthetic_placeholder")}
        if self.live and ogg.exists():
            # LIVE: upload the OGG/Opus voice note, then send it by media id
            try:
                rec["delivered"] = self._upload_and_send_audio(to, ogg)
            except Exception:  # noqa: BLE001
                rec["delivered"] = False
        self.outbox.append(rec)

    def _upload_and_send_audio(self, to: str, ogg: Path) -> bool:  # pragma: no cover - needs live credentials
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"messaging_product\"\r\n\r\nwhatsapp\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{ogg.name}\"\r\nContent-Type: audio/ogg\r\n\r\n").encode() \
               + ogg.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{GRAPH}/{config.WHATSAPP_PHONE_NUMBER_ID}/media", data=body,
                                     headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
                                              "Content-Type": f"multipart/form-data; boundary={boundary}"})
        mid = json.loads(urllib.request.urlopen(req, timeout=20).read())["id"]
        return self._send("messages", {"messaging_product": "whatsapp", "to": to, "type": "audio", "audio": {"id": mid}})

    # ------------------------------------------------------------------ simulation helpers
    def simulate(self, from_id: str, text: Optional[str] = None, audio: Optional[bytes] = None, mime: str = "audio/ogg; codecs=opus") -> dict:
        """Build a genuine Cloud-API style webhook payload and feed it through process_webhook()."""
        mid = "wamid." + uuid.uuid4().hex[:16]
        if audio is not None:
            media_id = self.media.put(audio, mime)
            msg = {"from": from_id, "id": mid, "timestamp": str(int(time.time())), "type": "audio", "audio": {"id": media_id, "mime_type": mime, "voice": True}}
        else:
            msg = {"from": from_id, "id": mid, "timestamp": str(int(time.time())), "type": "text", "text": {"body": text or ""}}
        payload = {"object": "whatsapp_business_account", "entry": [{"id": "SIM", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"display_phone_number": "0000000000", "phone_number_id": "SIM"},
            "contacts": [{"profile": {"name": "Farmer"}, "wa_id": from_id}], "messages": [msg]}}]}]}
        n0 = len(self.outbox)
        out = self.process_webhook(payload)
        return {**out, "message_id": mid, "payload": payload, "replies": self.outbox[n0:]}

    def reset(self, from_id: Optional[str] = None) -> None:
        self.outbox.clear()
        if from_id:
            self.assistant.sessions.delete(from_id)
