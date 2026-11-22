import json
import os
import threading
import unittest
import urllib.error
import urllib.request
import uuid

import helpers
from helpers import make_assistant
import audio_service as A
import handlers as H
import lite_server
import whatsapp_channel as W


def call(method, path, body=None, headers=None, query=None):
    raw = json.dumps(body).encode() if isinstance(body, (dict, list)) else (body or b"")
    return H.dispatch(H.Req(method, path, query or {}, headers or {}, raw))


def multipart(fields, files):
    b = uuid.uuid4().hex
    out = b""
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, (fn, ct, data) in files.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: {ct}\r\n\r\n'.encode() + data + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return out, f"multipart/form-data; boundary={b}"


class HandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        W.REQUIRE_CONSENT = False
        H.set_services(H.Services(make_assistant()))

    @classmethod
    def tearDownClass(cls):
        W.REQUIRE_CONSENT = True
        H.set_services(None)

    def test_required_endpoints_exist(self):
        paths = {(r["method"], r["path"]) for r in H.ROUTES}
        for need in [("GET", "/health"), ("POST", "/query/text"), ("POST", "/query/voice"), ("GET", "/admin/documents"), ("POST", "/admin/approve"),
                     ("GET", "/metrics"), ("GET", "/ui")]:
            self.assertIn(need, paths)

    def test_health_and_ui(self):
        st, p, _ = call("GET", "/health")
        self.assertEqual((st, p["status"]), (200, "ok"))
        self.assertEqual(p["kb"]["retrievable"], 25)
        st, html, ct = call("GET", "/ui")
        self.assertEqual(st, 200)
        self.assertTrue(ct.startswith("text/html"))
        self.assertIn(b"</html>", html)

    def test_query_text_and_validation(self):
        st, p, _ = call("POST", "/query/text", {"text": "गहू पेरणी कधी करावी?", "tts": False})
        self.assertEqual((st, p["decision"]["action"]), (200, "answer"))
        self.assertEqual(call("POST", "/query/text", {})[0], 400)
        self.assertEqual(call("POST", "/query/text", b"{not json")[0], 400)
        self.assertEqual(call("POST", "/query/text", {"text": "x" * 2000})[0], 400)
        self.assertEqual(call("GET", "/query/text")[0], 405)
        self.assertEqual(call("GET", "/nope")[0], 404)

    def test_query_voice_multipart_and_raw(self):
        raw = A.make_demo_voice_note("आज लासलगांव में प्याज का भाव क्या है?", "hi", 0.91, 0.02, "ogg")
        body, ct = multipart({"tts": "0", "lang": "hi"}, {"audio": ("note.ogg", "audio/ogg", raw)})
        st, p, _ = call("POST", "/query/voice", body, {"Content-Type": ct})
        self.assertEqual((st, p["decision"]["action"], p["tool"]["tool"]), (200, "answer", "mandi_price"))
        st, p, _ = call("POST", "/query/voice", raw, {"Content-Type": "audio/ogg"})
        self.assertEqual((st, p["input"]["asr"]["backend"]), (200, "simulated"))
        self.assertEqual(call("POST", "/query/voice", b"x", {"Content-Type": "text/plain"})[0], 400)

    def test_browser_asr_hint_for_real_speech(self):
        raw = A._wav_bytes(A._speechlike(2.0, 0.02, 3))
        body, ct = multipart({"asr_hint": "गहू पेरणी कधी करावी", "asr_hint_conf": "0.82", "tts": "0"}, {"audio": ("rec.wav", "audio/wav", raw)})
        st, p, _ = call("POST", "/query/voice", body, {"Content-Type": ct})
        self.assertEqual((p["input"]["asr"]["backend"], p["decision"]["action"]), ("browser-webspeech", "answer"))

    def test_admin_approval_changes_retrieval(self):
        doc = dict(doc_id="adm1", topic="maize_sowing", title="मक्का बुवाई", text="मक्का की बुवाई जून में करें। बीज उपचार जरूर करें।", language="hi",
                   crop="maize", region="Maharashtra", season="kharif", source="Test", publisher="Test", doc_date="2026-08-01", license="test")
        st, p, _ = call("POST", "/admin/documents", doc)
        self.assertEqual((st, p["result"]["status"]), (201, "added"))
        q = {"text": "मक्का की बुवाई कब करें?", "tts": False}
        self.assertEqual(call("POST", "/query/text", q)[1]["decision"]["action"], "refuse")       # not approved yet
        self.assertEqual(call("POST", "/admin/approve", {"doc_id": "adm1", "approved": True})[0], 200)
        r = call("POST", "/query/text", q)[1]
        self.assertEqual((r["decision"]["action"], r["answer"]["citations"][0]["doc_id"]), ("answer", "adm1"))
        call("POST", "/admin/approve", {"doc_id": "adm1", "approved": False})
        self.assertEqual(call("POST", "/query/text", q)[1]["decision"]["action"], "refuse")
        self.assertEqual(call("POST", "/admin/approve", {"doc_id": "ghost", "approved": True})[0], 404)
        self.assertEqual(call("POST", "/admin/approve", {"doc_id": "adm1"})[0], 400)
        st, p, _ = call("GET", "/admin/documents")
        self.assertTrue(any(d["doc_id"] == "adm1" and not d["retrievable"] for d in p["documents"]))

    def test_metrics_feedback_review_promote(self):
        st, p, _ = call("POST", "/query/text", {"text": "IPL में कौन जीतेगा?", "tts": False})
        rid = p["request_id"]
        self.assertEqual(call("POST", "/feedback", {"request_id": rid, "rating": "down", "comment": "call 9876543210"})[0], 200)
        self.assertEqual(call("POST", "/feedback", {"request_id": rid, "rating": "meh"})[0], 422)
        q = call("GET", "/admin/review")[1]["queue"]
        self.assertTrue(any(x["request_id"] == rid for x in q))
        self.assertEqual(call("POST", "/admin/review/promote", {"request_id": rid, "expected_action": "refuse"})[0], 200)
        m = call("GET", "/metrics")[1]
        self.assertGreater(m["requests"], 0)
        self.assertIn(b"agri_requests_total", call("GET", "/metrics", query={"format": "prom"})[1])
        import config
        (config.DATA_DIR / "eval_additions.jsonl").unlink(missing_ok=True)

    def test_presets_and_demo_audio(self):
        st, p, _ = call("GET", "/demo/presets")
        langs = {v["lang"] for v in p["voice"]}
        self.assertEqual(langs, {"hi", "mr"})
        st, data, ct = call("GET", "/demo/voice", query={"id": "mr-cotton-yellow", "fmt": "wav"})
        self.assertEqual((st, ct), (200, "audio/wav"))
        self.assertEqual(call("GET", "/demo/voice", query={"id": "zzz"})[0], 404)
        self.assertEqual(call("GET", "/demo/voice", query={"id": "hi-pbw", "fmt": "mp3"})[0], 400)

    def test_audio_endpoint_blocks_path_traversal(self):
        self.assertEqual(call("GET", "/audio/..%2f..%2fconfig.py")[0], 400)
        self.assertEqual(call("GET", "/audio/secret.txt")[0], 400)
        self.assertEqual(call("GET", "/audio/tts_missing.wav")[0], 404)

    def test_whatsapp_endpoints(self):
        st, text, _ = call("GET", "/webhook/whatsapp", query={"hub.mode": "subscribe", "hub.verify_token": "agri-demo-verify", "hub.challenge": "7"})
        self.assertEqual((st, text), (200, b"7"))
        self.assertEqual(call("GET", "/webhook/whatsapp", query={"hub.mode": "subscribe", "hub.verify_token": "x"})[0], 403)
        st, p, _ = call("POST", "/simulate/whatsapp", {"from": "919111111111", "text": "गहू पेरणी कधी करावी?"})
        self.assertEqual(st, 200)
        self.assertEqual(call("POST", "/webhook/whatsapp", p["payload"])[1]["duplicates"], 1)
        self.assertTrue(call("GET", "/simulate/whatsapp/outbox", query={"to": "919111111111"})[1]["outbox"])
        st, p, _ = call("POST", "/simulate/whatsapp", {"from": "919111111112", "preset": "mr-cotton-yellow"})
        self.assertEqual([m["type"] for m in p["replies"]], ["text", "audio"])

    def test_rbac(self):
        os.environ["AGRI_TOKENS"] = "admin:A1,reviewer:R1,analyst:N1"
        try:
            self.assertEqual(call("POST", "/admin/approve", {"doc_id": "x", "approved": True})[0], 401)
            self.assertEqual(call("POST", "/admin/approve", {"doc_id": "x", "approved": True}, {"X-Admin-Token": "R1"})[0], 403)
            self.assertEqual(call("POST", "/admin/approve", {"doc_id": "x", "approved": True}, {"X-Admin-Token": "A1"})[0], 404)   # authorised, unknown doc
            self.assertEqual(call("GET", "/metrics", headers={"X-Admin-Token": "N1"})[0], 200)
            self.assertEqual(call("GET", "/admin/review", headers={"X-Admin-Token": "N1"})[0], 403)
            self.assertEqual(call("POST", "/query/text", {"text": "गहू पेरणी कधी करावी?", "tts": False})[0], 200)   # farmers need no token
        finally:
            del os.environ["AGRI_TOKENS"]

    def test_eval_endpoint(self):
        st, rep, _ = call("POST", "/eval/run", query={"voice": "0"})
        self.assertEqual(st, 200)
        self.assertTrue(all(t["pass"] for t in rep["targets"]), rep["targets"])
        self.assertEqual(call("GET", "/eval/last")[0], 200)


class LiveHttpTests(unittest.TestCase):
    """Real sockets: the stdlib server must behave exactly like the handlers."""

    @classmethod
    def setUpClass(cls):
        W.REQUIRE_CONSENT = False
        H.set_services(H.Services(make_assistant()))
        cls.srv = lite_server.make_server("127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        W.REQUIRE_CONSENT = True
        H.set_services(None)

    def _req(self, method, path, data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_end_to_end_over_http(self):
        st, body, h = self._req("GET", "/health")
        self.assertEqual(json.loads(body)["status"], "ok")
        self.assertEqual(h["Access-Control-Allow-Origin"], "*")
        st, body, _ = self._req("GET", "/ui")
        self.assertIn(b"<html", body.lower())
        st, body, _ = self._req("POST", "/query/text", json.dumps({"text": "पीक विमा योजनेचा प्रीमियम किती आहे?"}).encode(), {"Content-Type": "application/json"})
        r = json.loads(body)
        self.assertEqual(r["decision"]["action"], "answer")
        st, audio, h = self._req("GET", r["tts"]["audio_url"])                     # generated reply audio is downloadable
        self.assertEqual((st, h["Content-Type"]), (200, "audio/wav"))
        raw = A.make_demo_voice_note("पुणे में अगले तीन दिन बारिश होगी क्या?", "hi", 0.9, 0.02, "ogg")
        body, ct = multipart({"tts": "0"}, {"audio": ("n.ogg", "audio/ogg", raw)})
        st, out, _ = self._req("POST", "/query/voice", body, {"Content-Type": ct})
        self.assertEqual(json.loads(out)["tool"]["tool"], "weather")
        self.assertEqual(self._req("GET", "/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
